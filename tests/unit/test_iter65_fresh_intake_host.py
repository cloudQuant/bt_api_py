"""Explicit fresh-only Host assembly; no sockets, venue or account access."""
import hashlib
import importlib
import os
import threading
import time
import uuid
from dataclasses import replace

import pytest
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py.execution_parent_grant import PORT, QUERY, SUBMIT, ExecutionParentGrant
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import ServiceConfigError, parse_client_config, parse_service_config
from bt_api_py.service_deployment import ClientBindingRequest, _record_writer, validate_deployment
from bt_api_py.service_host import ServiceHost, ServiceHostError, TrustedServiceClients
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    ServiceCapability,
    ServiceRegistry,
)
from bt_api_py.service_runtime import PORT_OPERATIONS, ServiceClientRuntime, _RequestNotSentAdapter
from tests.unit.test_service_host import _authority, _RegistryTransport, _request, _service_config

KEY = MarketInstrumentKey('ctp', 'SHFE', 'simnow', 'future', 'rb', 'rb')
IDENTITY = WireIdentity(WireRole.STRATEGY_CLIENT, 'curve-principal', 'strategy-a')
SCOPE = WireScope('live:test', account_ref='account-a', strategy_ref='strategy-a', run_id='run-a')
TEMPLATE = WireScope('live:test', account_ref='account-a', strategy_ref='strategy-a')
OPS = frozenset({SUBMIT, QUERY})


def component(instance='exec-a'):
    return {'config_schema_version': 2, 'settings': {'instance_id': instance,
        'bind_ports': [PORT], 'options': {'role': 'fresh_intake'}}}


class PollingServer:
    instances = []

    def __init__(self, _endpoint, _identity, _principals, handler, **_kwargs):
        self.endpoint = _endpoint
        self.handler = handler
        self.queue = []
        self.responses = []
        self.closed = False
        self.instances.append(self)

    def serve_once(self, _timeout_ms):
        if not self.queue:
            return False
        principal, request = self.queue.pop(0)
        self.responses.append(self.handler(principal, request))
        return True

    def close(self):
        self.closed = True


def build_host(tmp_path, monkeypatch, *, adapter_instance='exec-a', owned=False, grant_account='account-a',
               synthetic_auth=True, other_strategy=False, second_run=False, second_principal=False,
               two_strategies=False):
    from bt_api_execution.contracts import ExecutionScope
    from bt_api_execution.service_fresh_intake import FreshIntakeService
    from bt_api_execution.submission_intake import FreshSubmissionIntake

    monkeypatch.setattr(importlib.import_module('bt_api_transport_zmq.service_v2'),
                        'ZmqServiceServer', PollingServer)
    if synthetic_auth:
        # Fabricated transport internals only test fences: no CURVE socket,
        # key exchange, or actual authentication is involved.
        from bt_api_transport_zmq.service_v2 import ZmqServiceClient
        monkeypatch.setattr(ZmqServiceClient, 'request',
                            lambda client, request, *, timeout: client._synthetic_transport.request(request, timeout=timeout))
        monkeypatch.setattr(ZmqServiceClient, 'close', lambda client: client._synthetic_transport.close())
        def synthetic_proof(runtime, binding):
            if binding not in runtime.bindings or not binding.active or not runtime.healthy:
                return None
            transport = runtime._transports[(binding.service_id, binding.transport_ref)]
            key = (binding.service_id, binding.transport_ref, binding.registration_id, binding.service_epoch)
            peer_value = runtime._peers[binding.service_id]
            digest = hashlib.sha256(transport._transport._server_key).hexdigest()
            proof = runtime._authenticated_registration_peers.get(key)
            if proof is None or proof[0] is not peer_value or proof[1] != digest:
                proof = (peer_value, digest)
                runtime._authenticated_registration_peers[key] = proof
            return proof
        monkeypatch.setattr(ServiceClientRuntime, 'authenticated_peer_for_binding', synthetic_proof)
    from bt_api_py.gateway_parent_service import GatewayParentGrant
    clients = {'gateway': {'config_schema_version': 1, 'settings': {'bindings': [{
        'mount': 'upstream.direct_write', 'account_ref': 'account-a', 'service_id': 'gateway-service',
        'instance_id': 'gateway-a', 'scope_ref': 'gateway-scope', 'transport_ref': 'upstream-connect',
        'required': True}]}}}
    if two_strategies:
        other_strategy = True
        clients['gateway']['settings']['bindings'].append({
            **clients['gateway']['settings']['bindings'][0], 'scope_ref': 'gateway-b-scope'})
    other_identity = WireIdentity(WireRole.STRATEGY_CLIENT, 'other-principal', 'strategy-b')
    other_scope = replace(SCOPE, strategy_ref='strategy-b', run_id='run-b')
    other_template = replace(TEMPLATE, strategy_ref='strategy-b')
    config = _service_config({'execution': component()}, service_id='execution-service',
                             listener_endpoint='tcp://127.0.0.1:18971', clients=clients,
                             client_endpoint='tcp://127.0.0.1:18970')
    resource = ResourceGrant('exec-a', PORT, 'scope-a', TEMPLATE, OPS)
    authority_grants = {'curve-principal': (PrincipalGrant(IDENTITY, (resource,)),)}
    second_identity = WireIdentity(WireRole.STRATEGY_CLIENT, 'second-principal', 'strategy-a')
    if second_principal:
        authority_grants['second-principal'] = (PrincipalGrant(second_identity, (resource,)),)
    if two_strategies:
        authority_grants['other-principal'] = (PrincipalGrant(other_identity,
            (ResourceGrant('exec-a', PORT, 'scope-b', other_template, OPS),)),)
    initial_authority = _authority(config)
    principals = dict(initial_authority.principals)
    if second_principal:
        principals[b'b' * 40] = 'second-principal'
    if two_strategies:
        principals[b'c' * 40] = 'other-principal'
    authority = replace(initial_authority, grants=authority_grants, principals=principals)
    grant = ExecutionParentGrant(adapter_instance, 'scope-a', IDENTITY, grant_account, 'strategy-a',
                                 SCOPE.execution_domain_ref, 'grant-v1', frozenset({KEY}), OPS)
    parent_ops = frozenset({'direct.parent.register', 'direct.parent.query'})
    remote_grant = ResourceGrant('gateway-a', 'service.direct_write', 'gateway-scope', TEMPLATE, parent_ops)
    strategy_identity = IDENTITY
    strategy_grant = ResourceGrant('gateway-a', 'service.direct_write', 'strategy-scope',
                                   TEMPLATE, frozenset({'direct.reserve'}))
    other_gateway_scope_ref = 'gateway-b-scope' if two_strategies else 'gateway-scope'
    remote_resources = (remote_grant,) + ((ResourceGrant('gateway-a', 'service.direct_write', other_gateway_scope_ref,
        other_template, parent_ops),) if other_strategy else ())
    trusted = {authority.identity.principal_ref: (PrincipalGrant(authority.identity, remote_resources),),
               'curve-principal': (PrincipalGrant(strategy_identity, (strategy_grant,)),)}
    if other_strategy:
        trusted['other-principal'] = (PrincipalGrant(other_identity, (ResourceGrant('gateway-a',
            'service.direct_write', 'other-scope', other_template, frozenset({'direct.reserve'})),)),)
    gateway = ServiceRegistry(':memory:', service_id='gateway-service', capabilities=(
        ServiceCapability('gateway-a', 'service.direct_write', parent_ops | {'direct.reserve'},
                          frozenset({'direct.parent.register', 'direct.reserve'})),),
        grants=trusted,
        policy_version='gateway-policy', requirements_version='gateway-requirements')
    parent_grants = (
        GatewayParentGrant(authority.identity, 'account-a', 'strategy-a', SCOPE.execution_domain_ref,
                           'execution', 'gateway-grant-v1', frozenset({KEY}), parent_ops),)
    if other_strategy and not two_strategies:
        parent_grants += (GatewayParentGrant(authority.identity, 'account-a', 'strategy-b',
            SCOPE.execution_domain_ref, 'execution', 'gateway-grant-b', frozenset({KEY}), parent_ops),)
    gateway.configure_gateway_parents(instance_id='gateway-a', scope_ref='gateway-scope', grants=parent_grants)
    if two_strategies:
        gateway.configure_gateway_parents(instance_id='gateway-a', scope_ref='gateway-b-scope', grants=(
            GatewayParentGrant(authority.identity, 'account-a', 'strategy-b', SCOPE.execution_domain_ref,
                               'execution', 'gateway-grant-b', frozenset({KEY}), parent_ops),))
    for operation in parent_ops:
        gateway.register_handler('gateway-a', operation, lambda _context, _arguments: {'status': 'synthetic'})
    gateway.register_handler('gateway-a', 'direct.reserve', lambda _context, _arguments: {'status': 'synthetic'})
    strategy_registration = gateway.handle('curve-principal', _request('service.register', {
        'registration_key': 'strategy-key', 'client_session_id': 'strategy-session',
        'instance_id': 'gateway-a', 'port': 'service.direct_write', 'scope_ref': 'strategy-scope',
        'operations': ['direct.reserve']}, schema_id='sdk.service.control', identity=strategy_identity, scope=SCOPE))
    assert strategy_registration['ok'], strategy_registration
    if other_strategy:
        other_registration = gateway.handle('other-principal', _request('service.register', {
            'registration_key': 'other-key', 'client_session_id': 'other-session',
            'instance_id': 'gateway-a', 'port': 'service.direct_write', 'scope_ref': 'other-scope',
            'operations': ['direct.reserve']}, schema_id='sdk.service.control',
            identity=other_identity, scope=other_scope))
        assert other_registration['ok'], other_registration
    gateway._synthetic_strategy_registration = strategy_registration['result']
    peer = WireIdentity(WireRole.SERVICE, 'gateway-peer', 'gateway-service')
    def upstream_transport(_profile, _peer):
        local = _RegistryTransport(gateway, authority.identity.principal_ref, peer, [])
        if not synthetic_auth:
            return local
        from bt_api_transport_zmq.service_v2 import ZmqServiceClient
        client = object.__new__(ZmqServiceClient)
        client._synthetic_transport = local
        client._server_key = b'a' * 40
        client._last_reply_receipt = None
        client._owner_pid = os.getpid()
        client._owner_thread = threading.get_ident()
        return _RequestNotSentAdapter(client, TimeoutError)
    clients = TrustedServiceClients(scope_resolver=lambda ref: other_scope if ref == 'gateway-b-scope' else SCOPE,
        peer_resolver=lambda _id: peer,
        transport_factory=upstream_transport,
        requested_operations={'upstream.direct_write': parent_ops})
    target = ExecutionScope('FAKE', 'test', 'local-' + uuid.uuid4().hex, 'local-strategy')
    targets = {SCOPE: target}
    if second_run:
        targets[replace(SCOPE, run_id='new-run')] = target
    if two_strategies:
        targets[other_scope] = replace(target, strategy_id='local-strategy-b')
    intake = FreshSubmissionIntake(root=str(tmp_path), targets=targets)
    grants = (grant,)
    if second_principal:
        grants += (replace(grant, identity=second_identity),)
    if two_strategies:
        grants += (replace(grant, scope_ref='scope-b', identity=other_identity, strategy_ref='strategy-b'),)
    adapter = FreshIntakeService(adapter_instance, intake, grants)
    if owned:
        resource_values = ComponentResources.owned({'service': adapter}, ready=lambda: True, close=intake.close)
    else:
        resource_values = ComponentResources.borrowed({'service': adapter}, ready=lambda: True)
    gateway_config = _service_config({'gateway': {'config_schema_version': 2, 'settings': {
        'instance_id': 'gateway-a', 'bind_ports': ['service.direct_write'],
        'options': {'role': 'trading_owner'}}}}, service_id='gateway-service',
        listener_endpoint='tcp://127.0.0.1:18970')
    host = ServiceHost(config, authority, tmp_path / 'registry.sqlite3', lambda *_: resource_values,
                       trusted_clients=clients, service_directory={'gateway-service': gateway_config,
                                                                   'execution-service': config})
    return host, intake, grant, gateway


def call(host, operation, payload, *, scope=SCOPE, identity=IDENTITY, principal='curve-principal'):
    server = host._server
    server.queue.append((principal, _request(operation, payload,
        schema_id='sdk.service.control' if operation.startswith('service.') else 'sdk.service.call',
        identity=identity, scope=scope)))
    assert host.serve_once() is True
    return server.responses[-1]


def parent_payload():
    return {'request_id': 'submit-a', 'order_type': 'limit', 'spec': {
        'parent_ref': 'parent-a', 'registration_operation_id': 'gateway-a', 'risk_operation_id': 'risk-a',
        'account_scope': SCOPE.account_ref, 'strategy_scope': SCOPE.strategy_ref,
        'execution_domain_ref': SCOPE.execution_domain_ref, 'instrument': KEY.to_dict(),
        'quantity': '5', 'quantity_unit': 'lots', 'side': 'buy', 'offset': 'open', 'limit_price': '5',
        'creation_trading_day': '20261109', 'allowed_trading_days': ['20261109'],
        'expires_at_ns': time.time_ns() + 60_000_000_000, 'grant_revision': 'gateway-grant',
        'plan_digest': 'a' * 64, 'plan_version': 'v1', 'target_position_ref': None,
        'risk_selected': False, 'time_in_force': 'DAY'}}


def test_host_installs_exact_grants_and_dispatches_local_only(tmp_path, monkeypatch):
    host, intake, grant, gateway = build_host(tmp_path, monkeypatch)
    try:
        host.start()
        registry = host._registry
        assert registry.execution_parent_grant(instance_id='exec-a', scope_ref='scope-a',
            identity=IDENTITY, scope=SCOPE, operation=SUBMIT) is grant
        assert PORT_OPERATIONS[PORT] == OPS
        registered = call(host, 'service.register', {'registration_key': 'test-registration',
            'client_session_id': 'test-client', 'instance_id': 'exec-a', 'port': PORT,
            'scope_ref': 'scope-a', 'operations': [SUBMIT, QUERY]})
        assert registered['ok'], registered
        binding = registered['result']
        def invoke(operation, args):
            return call(host, operation, {'registration_id': binding['registration_id'],
                'service_epoch': binding['service_epoch'], 'instance_id': 'exec-a', 'arguments': args})
        submitted = invoke(SUBMIT, parent_payload())
        assert submitted['ok'] and submitted['result']['status'] == 'RECEIVED', submitted
        assert submitted['result']['local_only'] is True
        result = invoke(QUERY, {'request_id': 'submit-a'})
        assert result['ok'] and result['result']['found'] is True
        assert host.revoke_execution_parent_grant(grant)
        denied = invoke(QUERY, {'request_id': 'submit-a'})
        assert not denied['ok']
    finally:
        host.close()
        intake.close()
        gateway.close()


def test_real_sdk_runtime_registers_both_mounts_and_reads_local_receipt(tmp_path, monkeypatch):
    host, intake, _, gateway = build_host(tmp_path, monkeypatch)
    runtime = None
    try:
        host.start()
        old = gateway._synthetic_strategy_registration
        released = gateway.handle('curve-principal', _request('service.unregister', {
            'registration_id': old['registration_id'], 'service_epoch': old['service_epoch']},
            schema_id='sdk.service.control', identity=IDENTITY, scope=SCOPE))
        assert released['ok'], released
        raw = _strategy_config(parent_owner='exec-a')
        raw['clients']['gateway']['settings']['bindings'][0]['scope_ref'] = 'strategy-scope'
        raw['clients']['execution']['settings']['bindings'][0]['scope_ref'] = 'scope-a'
        config = parse_client_config(raw)
        gateway_peer = WireIdentity(WireRole.SERVICE, 'gateway-peer', 'gateway-service')
        host_peer = host._authority.identity
        runtime = ServiceClientRuntime(config, identity=IDENTITY, scope_resolver=lambda _ref: TEMPLATE,
            peer_resolver=lambda service_id: gateway_peer if service_id == 'gateway-service' else host_peer,
            transport_factory=lambda _profile, peer: _RegistryTransport(
                gateway if peer == gateway_peer else host._registry, 'curve-principal', peer, []),
            run_id='run-a', requested_operations={'broker.direct_write': frozenset({'direct.reserve'}),
                                                  'broker.execution_parent': OPS})
        summary = runtime.start()
        assert {binding.mount for binding in summary.bindings} == {'broker.direct_write', 'broker.execution_parent'}
        result = runtime.call('broker.execution_parent', SUBMIT, parent_payload(), account_ref='account-a')
        assert result['status'] == 'RECEIVED' and result['local_only'] is True
        queried = runtime.call('broker.execution_parent', QUERY, {'request_id': 'submit-a'}, account_ref='account-a')
        assert queried['found'] is True and queried['request_sha256'] == result['request_sha256']
    finally:
        if runtime is not None:
            runtime.close()
        host.close()
        intake.close()
        gateway.close()


def test_host_trusted_grant_rotation_is_owner_thread_and_key_scoped(tmp_path, monkeypatch):
    host, intake, grant, gateway = build_host(tmp_path, monkeypatch)
    fresh = replace(grant, revision='grant-v2')
    try:
        with pytest.raises(ServiceHostError, match='HOST_NOT_STARTED'):
            host.revoke_execution_parent_grant(grant)
        host.start()
        errors = []
        def foreign():
            try:
                host.revoke_execution_parent_grant(grant)
            except ServiceHostError as exc:
                errors.append(exc.code)
        thread = threading.Thread(target=foreign)
        thread.start()
        thread.join()
        assert errors == ['OWNER_THREAD_REQUIRED']
        with pytest.raises(ServiceHostError, match='EXECUTION_PARENT_GRANT_KEY_INVALID'):
            host.replace_execution_parent_grant(grant, replace(grant, scope_ref='unknown'))
        assert host.replace_execution_parent_grant(grant, fresh)
        assert not host.replace_execution_parent_grant(grant, fresh)
        registered = call(host, 'service.register', {'registration_key': 'rotated',
            'client_session_id': 'rotated-client', 'instance_id': 'exec-a', 'port': PORT,
            'scope_ref': 'scope-a', 'operations': [SUBMIT, QUERY]})
        assert registered['ok'], registered
        binding = registered['result']
        def invoke(operation, args):
            return call(host, operation, {'registration_id': binding['registration_id'],
                'service_epoch': binding['service_epoch'], 'instance_id': 'exec-a', 'arguments': args})
        submitted = invoke(SUBMIT, parent_payload())
        assert submitted['ok'] and submitted['result']['status'] == 'RECEIVED'
        assert invoke(QUERY, {'request_id': 'submit-a'})['ok']
        assert host.revoke_execution_parent_grant(fresh)
        assert not invoke(QUERY, {'request_id': 'submit-a'})['ok']
        query_only = replace(grant, revision='query-only', operations=frozenset({QUERY}))
        host.install_execution_parent_grant(query_only)
        assert invoke(QUERY, {'request_id': 'submit-a'})['ok']
        assert not invoke(SUBMIT, parent_payload())['ok']
        assert host.revoke_execution_parent_grant(query_only)
        host.install_execution_parent_grant(grant)
        assert invoke(QUERY, {'request_id': 'submit-a'})['ok']
        with pytest.raises(ServiceHostError, match='EXECUTION_PARENT_GRANT_ALREADY_INSTALLED'):
            host.install_execution_parent_grant(grant)
    finally:
        host.close()
        intake.close()
        gateway.close()
    with pytest.raises(ServiceHostError, match='HOST_NOT_STARTED'):
        host.revoke_execution_parent_grant(grant)


def test_revoking_one_grant_keeps_other_authorized_principal_operational(tmp_path, monkeypatch):
    host, intake, grant, gateway = build_host(tmp_path, monkeypatch, second_principal=True)
    second = WireIdentity(WireRole.STRATEGY_CLIENT, 'second-principal', 'strategy-a')
    try:
        host.start()
        assert host.revoke_execution_parent_grant(grant)
        assert host.healthy
        registered = call(host, 'service.register', {'registration_key': 'second-principal',
            'client_session_id': 'second-client', 'instance_id': 'exec-a', 'port': PORT,
            'scope_ref': 'scope-a', 'operations': [SUBMIT, QUERY]}, identity=second, principal='second-principal')
        assert registered['ok'], registered
        binding = registered['result']
        submitted = call(host, SUBMIT, {'registration_id': binding['registration_id'],
            'service_epoch': binding['service_epoch'], 'instance_id': 'exec-a', 'arguments': parent_payload()},
            identity=second, principal='second-principal')
        assert submitted['ok'] and submitted['result']['status'] == 'RECEIVED', submitted
    finally:
        host.close()
        intake.close()
        gateway.close()


def test_revoking_one_strategy_does_not_block_other_strategy(tmp_path, monkeypatch):
    host, intake, grant, gateway = build_host(tmp_path, monkeypatch, two_strategies=True)
    other = WireIdentity(WireRole.STRATEGY_CLIENT, 'other-principal', 'strategy-b')
    other_scope = replace(SCOPE, strategy_ref='strategy-b', run_id='run-b')
    try:
        host.start()
        assert host.revoke_execution_parent_grant(grant)
        assert host.healthy
        registered = call(host, 'service.register', {'registration_key': 'other-strategy',
            'client_session_id': 'other-client', 'instance_id': 'exec-a', 'port': PORT,
            'scope_ref': 'scope-b', 'operations': [SUBMIT, QUERY]}, identity=other,
            principal='other-principal', scope=other_scope)
        assert registered['ok'], registered
        binding = registered['result']
        arguments = parent_payload()
        arguments['spec']['strategy_scope'] = 'strategy-b'
        submitted = call(host, SUBMIT, {'registration_id': binding['registration_id'],
            'service_epoch': binding['service_epoch'], 'instance_id': 'exec-a', 'arguments': arguments},
            identity=other, principal='other-principal', scope=other_scope)
        assert submitted['ok'] and submitted['result']['status'] == 'RECEIVED', submitted
    finally:
        host.close()
        intake.close()
        gateway.close()


def test_host_blocks_reentrant_changes_during_submit(tmp_path, monkeypatch):
    from bt_api_execution.service_fresh_intake import FreshIntakeService
    host, intake, grant, gateway = build_host(tmp_path, monkeypatch)
    original = FreshIntakeService.handle
    rotated = replace(grant, revision='grant-v2')
    pending = [True]
    errors = []
    def replace_after_issue(adapter, operation, context, arguments):
        if pending[0]:
            pending[0] = False
            for action in (lambda: host.replace_execution_parent_grant(grant, rotated),
                           host.recover_upstreams, host.maintain, host.next_maintenance_delay, host.close):
                try:
                    action()
                except ServiceHostError as error:
                    errors.append(error.code)
        return original(adapter, operation, context, arguments)
    monkeypatch.setattr(FreshIntakeService, 'handle', replace_after_issue)
    try:
        host.start()
        registered = call(host, 'service.register', {'registration_key': 'rotation-race',
            'client_session_id': 'rotation-client', 'instance_id': 'exec-a', 'port': PORT,
            'scope_ref': 'scope-a', 'operations': [SUBMIT, QUERY]})
        assert registered['ok'], registered
        binding = registered['result']
        request = {'registration_id': binding['registration_id'], 'service_epoch': binding['service_epoch'],
                   'instance_id': 'exec-a', 'arguments': parent_payload()}
        submitted = call(host, SUBMIT, request)
        assert errors == ['EXECUTION_SUBMISSION_ACTIVE'] * 5
        assert submitted['ok'] and submitted['result']['status'] == 'RECEIVED'
        assert host.replace_execution_parent_grant(grant, rotated)
        next_payload = parent_payload()
        next_payload['request_id'] = 'submit-b'
        next_payload['spec'].update(parent_ref='parent-b', registration_operation_id='gateway-b',
                                    risk_operation_id='risk-b')
        request['arguments'] = next_payload
        submitted = call(host, SUBMIT, request)
        assert submitted['ok'] and submitted['result']['status'] == 'RECEIVED'
    finally:
        host.close()
        intake.close()
        gateway.close()


def _strategy_config(parent_owner='execution-a', *, gateway_owner='gateway-a',
                     include_gateway=True, extra_parent=None):
    gateway = {'mount': 'broker.direct_write', 'account_ref': 'account-a', 'service_id': 'gateway-service',
               'instance_id': gateway_owner, 'scope_ref': 'gateway-scope', 'transport_ref': 'gateway-connect'}
    parent = {'mount': 'broker.execution_parent', 'account_ref': 'account-a', 'service_id': 'execution-service',
              'instance_id': parent_owner, 'scope_ref': 'parent-scope', 'transport_ref': 'execution-connect'}
    clients = {'execution': {'config_schema_version': 1, 'settings': {'bindings': [parent]}}}
    if extra_parent:
        clients['execution']['settings']['bindings'].append({**parent, 'instance_id': extra_parent, 'scope_ref': 'second'})
    if include_gateway:
        clients['gateway'] = {'config_schema_version': 1, 'settings': {'bindings': [gateway]}}
    def profile(port):
        return {'backend': 'zmq', 'config_schema_version': 1,
            'endpoints': {'rpc_connect': f'tcp://127.0.0.1:{port}'}, 'security_profile_ref': 'test-security'}
    return {'client_config_schema_version': 1, 'process_role': 'strategy_client',
            'registration': {'strategy_ref': 'strategy-a'}, 'clients': clients,
            'communication': {'config_schema_version': 1, 'profiles': {
                'gateway-connect': profile(18970), 'execution-connect': profile(18971)}}}


def test_strategy_requires_gateway_and_distinct_parent_writer_slot():
    config = parse_client_config(_strategy_config())
    assert config.clients['gateway'] and config.clients['execution']
    with pytest.raises(ServiceConfigError, match='EXECUTION_REQUIRES_GATEWAY'):
        parse_client_config(_strategy_config(include_gateway=False))
    with pytest.raises(ServiceConfigError, match='WRITER_CONFLICT'):
        parse_client_config(_strategy_config(extra_parent='execution-b'))


def test_host_rejects_service_for_another_instance_before_listener(tmp_path, monkeypatch):
    host, intake, _, gateway = build_host(tmp_path, monkeypatch, adapter_instance='exec-b')
    before = len(PollingServer.instances)
    try:
        with pytest.raises(ServiceHostError, match='FRESH_INTAKE_SERVICE_INVALID'):
            host.start()
        assert len(PollingServer.instances) == before
    finally:
        host.close()
        intake.close()
        gateway.close()


def test_host_closes_owned_resource_when_listener_start_fails(tmp_path, monkeypatch):
    host, intake, _, gateway = build_host(tmp_path, monkeypatch, owned=True)
    def broken_listener(*_args, **_kwargs):
        raise OSError('synthetic listener failure')
    monkeypatch.setattr(importlib.import_module('bt_api_transport_zmq.service_v2'),
                        'ZmqServiceServer', broken_listener)
    with pytest.raises(ServiceHostError):
        host.start()
    assert intake._closed
    assert list(tmp_path.glob('.execution-intake-*.owner'))
    host.close()
    gateway.close()


def test_host_cannot_start_when_gateway_is_unavailable(tmp_path, monkeypatch):
    host, intake, _, gateway = build_host(tmp_path, monkeypatch)
    called = []
    host._resource_provider = lambda *_: called.append(True)
    gateway.set_ready(False)
    before = len(PollingServer.instances)
    try:
        with pytest.raises(ServiceHostError, match='UPSTREAM_START_FAILED'):
            host.start()
        assert not called and len(PollingServer.instances) == before
    finally:
        host.close()
        intake.close()
        gateway.close()


def test_host_rejects_local_transport_without_authenticated_gateway(tmp_path, monkeypatch):
    host, intake, _, gateway = build_host(tmp_path, monkeypatch, synthetic_auth=False)
    before = len(PollingServer.instances)
    try:
        with pytest.raises(ServiceHostError, match='EXECUTION_GATEWAY_UNAVAILABLE'):
            host.start()
        assert len(PollingServer.instances) == before
    finally:
        host.close()
        intake.close()
        gateway.close()


def test_recover_changed_strategy_revokes_host_readiness(tmp_path, monkeypatch):
    host, intake, grant, gateway = build_host(tmp_path, monkeypatch, other_strategy=True)
    try:
        host.start()
        registered = call(host, 'service.register', {'registration_key': 'strategy-a-in',
            'client_session_id': 'strategy-a-client', 'instance_id': 'exec-a', 'port': PORT,
            'scope_ref': 'scope-a', 'operations': [SUBMIT, QUERY]})
        assert registered['ok'], registered
        binding = registered['result']
        other_scope = replace(SCOPE, strategy_ref='strategy-b', run_id='run-b')
        host._upstream._scope_resolver = lambda _ref: other_scope
        with pytest.raises(ServiceHostError, match='UPSTREAM_RECOVERY_FAILED'):
            host.recover_upstreams()
        assert not host.healthy
        denied = call(host, SUBMIT, {'registration_id': binding['registration_id'],
            'service_epoch': binding['service_epoch'], 'instance_id': 'exec-a', 'arguments': parent_payload()})
        assert not denied['ok']
        # A revoked policy is no longer part of the coverage requirement.
        assert host.revoke_execution_parent_grant(grant)
        host.maintain()
        assert host.healthy
    finally:
        host.close()
        intake.close()
        gateway.close()


def test_new_run_query_preserves_authorized_historical_lookup(tmp_path, monkeypatch):
    host, intake, _, gateway = build_host(tmp_path, monkeypatch, second_run=True)
    try:
        host.start()
        registered = call(host, 'service.register', {'registration_key': 'first-run',
            'client_session_id': 'first-client', 'instance_id': 'exec-a', 'port': PORT,
            'scope_ref': 'scope-a', 'operations': [SUBMIT, QUERY]})
        assert registered['ok'], registered
        binding = registered['result']
        def invoke(scope, registration, operation, args):
            return call(host, operation, {'registration_id': registration['registration_id'],
                'service_epoch': registration['service_epoch'], 'instance_id': 'exec-a', 'arguments': args},
                scope=scope)
        assert invoke(SCOPE, binding, SUBMIT, parent_payload())['ok']
        assert call(host, 'service.unregister', {'registration_id': binding['registration_id'],
            'service_epoch': binding['service_epoch']})['ok']
        new_scope = replace(SCOPE, run_id='new-run')
        new_registration = call(host, 'service.register', {'registration_key': 'new-run',
            'client_session_id': 'new-client', 'instance_id': 'exec-a', 'port': PORT,
            'scope_ref': 'scope-a', 'operations': [QUERY]}, scope=new_scope)
        assert new_registration['ok'], new_registration
        new_binding = new_registration['result']
        query = invoke(new_scope, new_binding, QUERY, {'request_id': 'submit-a'})
        assert query['ok'] and query['result']['found']
    finally:
        host.close()
        intake.close()
        gateway.close()


def test_new_run_submit_requires_matching_gateway_run(tmp_path, monkeypatch):
    host, intake, _, gateway = build_host(tmp_path, monkeypatch, second_run=True)
    try:
        host.start()
        new_scope = replace(SCOPE, run_id='new-run')
        registered = call(host, 'service.register', {'registration_key': 'new-run',
            'client_session_id': 'new-client', 'instance_id': 'exec-a', 'port': PORT,
            'scope_ref': 'scope-a', 'operations': [SUBMIT, QUERY]}, scope=new_scope)
        assert registered['ok'], registered
        binding = registered['result']
        def invoke(operation, args):
            return call(host, operation, {'registration_id': binding['registration_id'],
                'service_epoch': binding['service_epoch'], 'instance_id': 'exec-a', 'arguments': args},
                scope=new_scope)
        assert not invoke(SUBMIT, parent_payload())['ok']
        queried = invoke(QUERY, {'request_id': 'submit-a'})
        assert queried['ok'] and not queried['result']['found']
    finally:
        host.close()
        intake.close()
        gateway.close()


def test_upstream_binding_change_before_local_commit_rejects_receipt(tmp_path, monkeypatch):
    from bt_api_execution.submission_intake import FreshSubmissionIntake
    host, intake, _, gateway = build_host(tmp_path, monkeypatch)
    try:
        host.start()
        registered = call(host, 'service.register', {'registration_key': 'change-upstream',
            'client_session_id': 'change-upstream-client', 'instance_id': 'exec-a', 'port': PORT,
            'scope_ref': 'scope-a', 'operations': [SUBMIT, QUERY]})
        assert registered['ok'], registered
        binding = registered['result']
        original = FreshSubmissionIntake._verify_files
        changed = [False]
        def replace_gateway_binding(instance):
            result = original(instance)
            if instance is intake and not changed[0]:
                changed[0] = True
                live = host._upstream._bindings[0]
                host._upstream._bindings[0] = replace(live, lease_deadline=time.monotonic() - 1)
            return result
        monkeypatch.setattr(FreshSubmissionIntake, '_verify_files', replace_gateway_binding)
        result = call(host, SUBMIT, {'registration_id': binding['registration_id'],
            'service_epoch': binding['service_epoch'], 'instance_id': 'exec-a', 'arguments': parent_payload()})
        assert changed[0]
        assert not result['ok']
        assert intake._db.execute('SELECT COUNT(*) FROM received').fetchone()[0] == 0
    finally:
        host.close()
        intake.close()
        gateway.close()


def test_upstream_untrusted_monotonic_clock_rejects_submit(tmp_path, monkeypatch):
    host, intake, _, gateway = build_host(tmp_path, monkeypatch)
    try:
        host.start()
        registered = call(host, 'service.register', {'registration_key': 'untrusted-clock',
            'client_session_id': 'untrusted-clock-client', 'instance_id': 'exec-a', 'port': PORT,
            'scope_ref': 'scope-a', 'operations': [SUBMIT, QUERY]})
        assert registered['ok'], registered
        binding = registered['result']
        host._upstream._monotonic_clock = lambda: time.monotonic()
        result = call(host, SUBMIT, {'registration_id': binding['registration_id'],
            'service_epoch': binding['service_epoch'], 'instance_id': 'exec-a', 'arguments': parent_payload()})
        assert not result['ok']
        assert intake._db.execute('SELECT COUNT(*) FROM received').fetchone()[0] == 0
    finally:
        host.close()
        intake.close()
        gateway.close()


def test_upstream_loss_after_local_commit_returns_unknown(tmp_path, monkeypatch):
    from bt_api_execution.service_fresh_intake import FreshIntakeService
    host, intake, _, gateway = build_host(tmp_path, monkeypatch)
    try:
        host.start()
        registered = call(host, 'service.register', {'registration_key': 'late-upstream-loss',
            'client_session_id': 'late-upstream-client', 'instance_id': 'exec-a', 'port': PORT,
            'scope_ref': 'scope-a', 'operations': [SUBMIT, QUERY]})
        assert registered['ok'], registered
        binding = registered['result']
        original = FreshIntakeService.handle
        def lose_after_commit(service, operation, context, arguments):
            result = original(service, operation, context, arguments)
            live = host._upstream._bindings[0]
            host._upstream._bindings[0] = replace(live, lease_deadline=time.monotonic() - 1)
            return result
        monkeypatch.setattr(FreshIntakeService, 'handle', lose_after_commit)
        response = call(host, SUBMIT, {'registration_id': binding['registration_id'],
            'service_epoch': binding['service_epoch'], 'instance_id': 'exec-a', 'arguments': parent_payload()})
        assert not response['ok'] and response['code'] == 'HANDLER_FAILED'
        assert intake._db.execute('SELECT COUNT(*) FROM received').fetchone()[0] == 1
    finally:
        host.close()
        intake.close()
        gateway.close()


def test_upstream_lease_expiration_before_local_commit_rejects_receipt(tmp_path, monkeypatch):
    from bt_api_execution.submission_intake import FreshSubmissionIntake
    host, intake, _, gateway = build_host(tmp_path, monkeypatch)
    try:
        host.start()
        registered = call(host, 'service.register', {'registration_key': 'expire-upstream',
            'client_session_id': 'expire-upstream-client', 'instance_id': 'exec-a', 'port': PORT,
            'scope_ref': 'scope-a', 'operations': [SUBMIT, QUERY]})
        assert registered['ok'], registered
        binding = registered['result']
        live = replace(host._upstream._bindings[0], lease_deadline=time.monotonic() + 0.5)
        host._upstream._bindings[0] = live
        host._upstream_registration_summary = replace(host._upstream_registration_summary, bindings=(live,))
        original = FreshSubmissionIntake._verify_files
        waited = [False]
        def expire_in_transaction(instance):
            result = original(instance)
            if instance is intake and not waited[0]:
                waited[0] = True
                time.sleep(0.65)
            return result
        monkeypatch.setattr(FreshSubmissionIntake, '_verify_files', expire_in_transaction)
        result = call(host, SUBMIT, {'registration_id': binding['registration_id'],
            'service_epoch': binding['service_epoch'], 'instance_id': 'exec-a', 'arguments': parent_payload()})
        assert waited[0]
        assert not result['ok']
        assert intake._db.execute('SELECT COUNT(*) FROM received').fetchone()[0] == 0
    finally:
        host.close()
        intake.close()
        gateway.close()


def test_parent_submit_is_blocked_when_upstream_is_unhealthy(tmp_path, monkeypatch):
    host, intake, _, gateway = build_host(tmp_path, monkeypatch)
    try:
        host.start()
        registered = call(host, 'service.register', {'registration_key': 'test-registration',
            'client_session_id': 'test-client', 'instance_id': 'exec-a', 'port': PORT,
            'scope_ref': 'scope-a', 'operations': [SUBMIT, QUERY]})
        assert registered['ok'], registered
        binding = registered['result']
        # A synthetic maintenance failure tests host gating, not disconnect detection.
        def fail_maintenance():
            raise RuntimeError('synthetic upstream failure')
        monkeypatch.setattr(host._upstream, 'maintain', fail_maintenance)
        submitted = call(host, SUBMIT, {'registration_id': binding['registration_id'],
            'service_epoch': binding['service_epoch'], 'instance_id': 'exec-a',
            'arguments': parent_payload()})
        assert not submitted['ok']
    finally:
        host.close()
        intake.close()
        gateway.close()


def test_offline_gateway_retains_authorized_local_query(tmp_path, monkeypatch):
    host, intake, _, gateway = build_host(tmp_path, monkeypatch)
    try:
        host.start()
        registered = call(host, 'service.register', {'registration_key': 'offline-query',
            'client_session_id': 'offline-query-client', 'instance_id': 'exec-a', 'port': PORT,
            'scope_ref': 'scope-a', 'operations': [SUBMIT, QUERY]})
        assert registered['ok'], registered
        binding = registered['result']
        def invoke(operation, args):
            return call(host, operation, {'registration_id': binding['registration_id'],
                'service_epoch': binding['service_epoch'], 'instance_id': 'exec-a', 'arguments': args})
        submitted = invoke(SUBMIT, parent_payload())
        assert submitted['ok'], submitted
        def fail_maintenance():
            raise RuntimeError('synthetic upstream failure')
        monkeypatch.setattr(host._upstream, 'maintain', fail_maintenance)
        query = invoke(QUERY, {'request_id': 'submit-a'})
        if not query['ok'] and query['code'] == 'POLICY_REQUIREMENT_UNMET':
            pytest.xfail('offline local query requires a restricted read readiness contract')
        assert query['ok'] and query['result']['found'], query
    finally:
        host.close()
        intake.close()
        gateway.close()


def test_host_rejects_execution_account_without_matching_gateway(tmp_path, monkeypatch):
    host, intake, _, gateway = build_host(tmp_path, monkeypatch, grant_account='account-b')
    before = len(PollingServer.instances)
    try:
        with pytest.raises(ServiceHostError, match='EXECUTION_GATEWAY_UNAVAILABLE'):
            host.start()
        assert len(PollingServer.instances) == before
    finally:
        host.close()
        intake.close()
        gateway.close()


def test_explicit_role_and_port_reject_other_options():
    raw = {'service_config_schema_version': 1, 'process_role': 'service', 'service_id': 'execution-service',
           'scope_ref': 'root-scope-v1', 'transport_ref': 'listener-v1',
           'registration_policy_ref': 'registration-policy-v1', 'service_requirements_ref': 'service-requirements-v1',
           'components': {'execution': component()}, 'communication': {'config_schema_version': 1,
               'profiles': {'listener-v1': {'backend': 'zmq', 'config_schema_version': 1,
                   'endpoints': {'rpc_bind': 'tcp://127.0.0.1:18971'}, 'security_profile_ref': 'listener-security-v1'}}}}
    raw['components']['execution']['settings']['options']['unexpected'] = True
    with pytest.raises(ServiceConfigError):
        parse_service_config(raw)
    raw['components']['execution']['settings']['options'].pop('unexpected')
    with pytest.raises(ServiceConfigError, match='EXECUTION_REQUIRES_GATEWAY'):
        parse_service_config(raw)


def test_deployment_connects_gateway_and_parent_to_distinct_owners():
    gateway = _service_config({'gateway': {'config_schema_version': 2, 'settings': {
        'instance_id': 'gateway-a', 'bind_ports': ['service.direct_write'],
        'options': {'role': 'trading_owner'}}}}, service_id='gateway-service',
        listener_endpoint='tcp://127.0.0.1:18970')
    clients = {'gateway': {'config_schema_version': 1, 'settings': {'bindings': [{
        'mount': 'upstream.direct_write', 'account_ref': 'account-a', 'service_id': 'gateway-service',
        'instance_id': 'gateway-a', 'scope_ref': 'gateway-scope', 'transport_ref': 'upstream-connect',
        'required': True}]}}}
    execution = _service_config({'execution': component('execution-a')}, service_id='execution-service',
                                listener_endpoint='tcp://127.0.0.1:18971', clients=clients,
                                client_endpoint='tcp://127.0.0.1:18970')
    client = parse_client_config(_strategy_config())
    plan = validate_deployment({'gateway-service': gateway, 'execution-service': execution}, (client,))
    assert {entry.mount for entry in plan.client_binding_requests} == {
        'broker.direct_write', 'broker.execution_parent', 'upstream.direct_write'}
    assert not plan.services_activated


def test_deployment_shared_gateway_and_parent_allow_two_strategies():
    gateway = _service_config({'gateway': {'config_schema_version': 2, 'settings': {
        'instance_id': 'gateway-a', 'bind_ports': ['service.direct_write'],
        'options': {'role': 'trading_owner'}}}}, service_id='gateway-service',
        listener_endpoint='tcp://127.0.0.1:18970')
    clients = {'gateway': {'config_schema_version': 1, 'settings': {'bindings': [{
        'mount': 'upstream.direct_write', 'account_ref': 'account-a', 'service_id': 'gateway-service',
        'instance_id': 'gateway-a', 'scope_ref': 'gateway-scope', 'transport_ref': 'upstream-connect',
        'required': True}]}}}
    execution = _service_config({'execution': component('execution-a')}, service_id='execution-service',
                                listener_endpoint='tcp://127.0.0.1:18971', clients=clients,
                                client_endpoint='tcp://127.0.0.1:18970')
    first = _strategy_config()
    second = _strategy_config()
    second['registration']['strategy_ref'] = 'strategy-b'
    configs = (parse_client_config(first), parse_client_config(second))
    directory = {'gateway-service': gateway, 'execution-service': execution}
    plan = validate_deployment(directory, configs)
    assert len(plan.client_binding_requests) == 5
    # The same account and endpoints cannot be redirected to another parent
    # owner, even while Gateway remains shared.
    other = _service_config({'execution': component('execution-b')}, service_id='other-execution',
                            listener_endpoint='tcp://127.0.0.1:18972', clients=clients,
                            client_endpoint='tcp://127.0.0.1:18970')
    second['clients']['execution']['settings']['bindings'][0].update(
        service_id='other-execution', instance_id='execution-b')
    second['communication']['profiles']['execution-connect']['endpoints']['rpc_connect'] = 'tcp://127.0.0.1:18972'
    with pytest.raises(ServiceConfigError, match='WRITER_CONFLICT'):
        validate_deployment({**directory, 'other-execution': other},
                            (configs[0], parse_client_config(second)))


def test_parent_writer_slot_allows_shared_owner_and_rejects_second_owner():
    def binding(client, service, instance):
        return ClientBindingRequest(None, client, 'execution', 0, 'broker.execution_parent',
            service, instance, 'scope-a', 'transport', 'account-a', None, True,
            'tcp://127.0.0.1:18971', 'tcp://127.0.0.1:18971')
    venue, parent = {}, {}
    _record_writer(venue, parent, binding(0, 'exec-service', 'exec-a'), 0)
    _record_writer(venue, parent, binding(1, 'exec-service', 'exec-a'), 1)
    with pytest.raises(ServiceConfigError, match='WRITER_CONFLICT'):
        _record_writer(venue, parent, binding(2, 'other-service', 'exec-b'), 2)


def test_gateway_writer_slot_rejects_different_gateway_and_legacy_owner():
    def binding(client, mount, service, instance):
        return ClientBindingRequest(None, client, 'gateway', 0, mount,
            service, instance, 'scope-a', 'transport', 'account-a', None, True,
            'tcp://127.0.0.1:18970', 'tcp://127.0.0.1:18970')
    venue, parent = {}, {}
    _record_writer(venue, parent, binding(0, 'broker.direct_write', 'gateway-service', 'gateway-a'), 0)
    _record_writer(venue, parent, binding(1, 'broker.direct_write', 'gateway-service', 'gateway-a'), 1)
    with pytest.raises(ServiceConfigError, match='WRITER_CONFLICT'):
        _record_writer(venue, parent, binding(2, 'broker.direct_write', 'other-gateway', 'gateway-b'), 2)
    with pytest.raises(ServiceConfigError, match='WRITER_CONFLICT'):
        _record_writer(venue, parent, binding(2, 'broker.orders', 'legacy-execution', 'legacy-a'), 2)
