"""Authenticated loopback of Runtime control with synthetic Gateway resources."""
import queue
import socket
import threading
import time
import uuid
import sys
import json
import http.client
from bt_api_run_contract import (ApiConfig, GatewayConfig, IdentityState, MonitorEventsConfig,
    OperatorConfig, OperatorRole, RestartConfig, StopConfig, UnitConfig, UnitKind, ValidatedRuntimeConfig)
from bt_api_runtime.control_auth import MappingAuthenticator, OperatorIdentity
from bt_api_runtime.events import EventStore
from bt_api_runtime.supervision import IdentityRead, SystemClock, UnitIdentity
from bt_api_py.runtime_service_host import RuntimeServiceHost
from backtrader.live_state import initialize_identity
from dataclasses import replace

import zmq

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher
from bt_api_gateway.session_guard import LocalAccountSessionGuard
from bt_api_run_contract import AccessMode
from bt_api_runtime.gateway_control import GatewayDelegationProof
from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDirectOwnerResources
from bt_api_py.gateway_direct_service import (GatewayControlGrant, GatewayDirectGrant,
    GatewayDirectServiceBinding, GatewayRuntimeControlProof)
from bt_api_py.runtime_gateway_control import (RuntimeGatewayControlBridge, RuntimeGatewayControlRoute,
                                               TrustedRuntimeControlBinding)
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import ClientCurveKeys

def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def make_unit(tmp_path, *, name, access=AccessMode.DIRECT):
    directory = tmp_path / ('strategy_' + name)
    directory.mkdir(parents=True, exist_ok=True)
    entry = directory / 'run.py'
    entry.write_text('# synthetic strategy\n', encoding='utf-8')
    gateway = GatewayConfig(str(tmp_path / ('gateway_' + name))) if access is AccessMode.GATEWAY else None
    return UnitConfig(name, UnitKind.STRATEGY, str(directory), str(entry), sys.executable, (), access,
                      1.0, 10.0, StopConfig(5.0, 1.0, 1.0 if gateway else None),
                      RestartConfig(), gateway=gateway)


def make_config(tmp_path, unit):
    return ValidatedRuntimeConfig('bt_api_runtime/v1', str(tmp_path / 'runtime_home'),
        ApiConfig('http_json', '127.0.0.1', free_port(), 'subject_token'),
        (OperatorConfig('alice', OperatorRole.OPERATOR, ('*',)),), MonitorEventsConfig(False), (unit,))


def control_payload(operation, *, unit=None, parameters=None, command_id=None, reason=''):
    return {'schema': 'bt_api_run_contract.control/v1', 'operation': operation, 'unit': unit,
            'parameters': parameters or {}, 'command_id': command_id, 'reason': reason,
            'confirm': None, 'all_units': False}


def request(port, payload, *, token):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    try:
        connection.request('POST', '/v1/control', body=json.dumps(payload), headers={
            'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token,
            'Host': f'127.0.0.1:{port}'})
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


OPS = frozenset({'direct.control.apply', 'direct.control.status', 'direct.control.resume'})


def _client(endpoint, client, route):
    return parse_client_config({'client_config_schema_version': 1, 'process_role': 'application_client',
        'registration': {'application_ref': client.actor_ref},
        'clients': {'gateway': {'config_schema_version': 1, 'settings': {'bindings': [{
            'mount': 'application.direct_write', 'service_id': route.service_id, 'instance_id': route.instance_id,
            'scope_ref': route.scope_ref, 'account_ref': route.scope.account_ref, 'transport_ref': 'gateway'}],
            'options': {'request_timeout_ms': 1000}}}},
        'communication': {'config_schema_version': 1, 'profiles': {'gateway': {
            'backend': 'zmq', 'config_schema_version': 1, 'endpoints': {'rpc_connect': endpoint},
            'security_profile_ref': 'client-key'}}}})


def test_http_gateway_composition(tmp_path):
    unit = make_unit(tmp_path, name='alpha', access=AccessMode.GATEWAY)
    strategy = initialize_identity(unit.strategy_dir).strategy_uuid
    server_revoked = False
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        endpoint = f'tcp://127.0.0.1:{probe.getsockname()[1]}'
    server_keys, client_keys = zmq.curve_keypair(), zmq.curve_keypair()
    service = WireIdentity(WireRole.SERVICE, 'gateway-service', 'service')
    client = WireIdentity(WireRole.APPLICATION_CLIENT, 'runtime-principal', 'runtime-app')
    scope = WireScope('domain', account_ref='account', strategy_ref=strategy)
    route = RuntimeGatewayControlRoute('service', 'gateway', 'scope', scope)
    config = _client(endpoint, client, route)
    expiry = time.time() + 120
    proof = GatewayDelegationProof('delegation', 'alice', expiry)
    server_proof = GatewayRuntimeControlProof('delegation', 'alice', expiry, revoked=server_revoked)
    binding = TrustedRuntimeControlBinding(route, client, service, config.fingerprint(), proof)
    keys = {'client-key': ClientCurveKeys(server_keys[0], client_keys[0], client_keys[1])}
    service_config = parse_service_config({'service_config_schema_version': 1, 'process_role': 'service',
        'service_id': 'service', 'scope_ref': 'scope', 'transport_ref': 'server',
        'registration_policy_ref': 'policy', 'service_requirements_ref': 'requirements',
        'components': {'gateway': {'config_schema_version': 2, 'settings': {'instance_id': 'gateway',
            'bind_ports': ['service.direct_write'], 'options': {'role': 'trading_owner',
                'dispatch_enabled': True, 'control_enabled': True}}}},
        'communication': {'config_schema_version': 1, 'profiles': {'server': {'backend': 'zmq',
            'config_schema_version': 1, 'endpoints': {'rpc_bind': endpoint}, 'security_profile_ref': 'key'}}}})
    grant = ResourceGrant('gateway', 'service.direct_write', 'scope', scope, OPS)
    authority = TrustedServiceAuthority(identity=service, scope=WireScope('domain'), scope_ref='scope',
        scope_version='v1', security_ref='key', security_version='v1', registration_policy_ref='policy',
        registration_policy_version='v1', service_requirements_ref='requirements', service_requirements_version='v1',
        curve_public_key=server_keys[0], curve_secret_key=server_keys[1],
        principals={client_keys[0]: client.principal_ref}, grants={client.principal_ref: (PrincipalGrant(client, (grant,)),)})
    ready, stop = queue.Queue(), threading.Event()
    (tmp_path / 'guard').mkdir()

    def serve():
        owner = host = None
        try:
            state = []
            def factory():
                guard = LocalAccountSessionGuard(tmp_path / 'guard', 'account', 'owner')
                journal = GatewayDirectCommandJournal.create(tmp_path / 'journal', 'account', generation=guard.acquire())
                dispatcher = GatewayDirectDispatcher(journal, 'TEST', guard, lambda *_: None, lambda _: (), lambda _: True)
                state.append((journal, guard))
                return GatewayDirectOwnerResources(dispatcher, journal, guard.release)
            owner = GatewayDirectOwner(factory, queue_size=4, max_queue_wait_seconds=10, busy_timeout_seconds=10)
            assert owner.start(10)
            journal, guard = state[0]
            control = owner.submit_service_call(lambda: journal.enable_controls(guard)).result(timeout_seconds=5)
            grants = {client.principal_ref: GatewayControlGrant(client.actor_ref, False, frozenset({strategy}),
                OPS, kind='runtime', runtime_proof=server_proof)}
            service_binding = GatewayDirectServiceBinding(journal, {strategy: GatewayDirectGrant('s', frozenset({'X'}))},
                owner=owner, control=control, control_grants=grants)
            host = ServiceHost(service_config, authority, tmp_path / 'registry',
                lambda *_: ComponentResources.borrowed({'binding': service_binding}, ready=lambda: True))
            host.start()
            ready.put(('ready', grants))
            while not stop.is_set():
                host.serve_once(10)
        except BaseException as exc:
            ready.put(('error', repr(exc)))
        finally:
            if host is not None and host.state == 'started':
                host.close()
            if owner is not None:
                owner.close(10)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    bridge = host = store = None
    try:
        status, _ = ready.get(timeout=20)
        assert status == 'ready'
        bridge = RuntimeGatewayControlBridge({strategy: route}, client_configs={strategy: config},
            client_identity=client, expected_gateway=service, security_profiles=keys, proofs={strategy: binding})
        runtime_config = make_config(tmp_path, unit)
        runtime_config = replace(runtime_config, api=replace(runtime_config.api, port=free_port()))
        store = EventStore(runtime_config.home, SystemClock())
        auth = MappingAuthenticator({'test-token': OperatorIdentity('alice', OperatorRole.OPERATOR, ('*',))})
        host = RuntimeServiceHost(runtime_config, authenticator=auth, gateway_bridge=bridge,
            monitor_bridge=None, event_store=store, unit_strategies={'alpha': strategy})
        host.start()
        assert host._supervisor.read_identity('alpha').identity.strategy_uuid == strategy
        port = host.bound_address[1]
        def call(operation, *, unit_name='alpha', parameters=None):
            return request(port, control_payload(operation, unit=unit_name, parameters=parameters,
                command_id=str(uuid.uuid4()), reason='synthetic test'), token='test-token')[1]
        paused = call('runtime.unit.pause', parameters={'freeze': True})
        assert paused['status'] == 'APPLIED', paused
        cause = paused['detail']['cause_id']
        version = paused['detail']['version']
        projection = call('runtime.unit.status')
        assert projection['status'] == 'OK', projection
        resumed = call('runtime.unit.resume', parameters={'cause': cause, 'expected_version': version})
        assert resumed['status'] == 'APPLIED', resumed
        assert call('runtime.unit.pause', unit_name='wrong')['status'] == 'REJECTED'
        host._supervisor.read_identity = lambda name: IdentityRead(IdentityState.PRESENT,
            UnitIdentity('other', 'test-host', unit.strategy_dir), 'identity_present')
        refused = call('runtime.unit.pause')
        assert refused['status'] == 'REJECTED' and refused['code'] == 'gateway_control_port_unavailable', refused
    finally:
        if host is not None:
            host.close()
        elif bridge is not None:
            bridge.close()
        if store is not None:
            assert store.available
            store.close()
        stop.set()
        thread.join(15)
        assert not thread.is_alive()



def test_http_event_reaches_monitor_ingress(tmp_path):
    from tests.integration import test_runtime_monitor_ingress as fixture
    path, doc, keys = fixture._deployment(tmp_path)
    process = fixture.monitor_fixture.start(path)
    host = bridge = store = None
    try:
        unit = make_unit(tmp_path, name='alpha')
        runtime_config = make_config(tmp_path, unit)
        runtime_config = replace(runtime_config, monitor_events=MonitorEventsConfig(enabled=True))
        store = EventStore(runtime_config.home, SystemClock())
        bridge = fixture._bridge(doc, keys, store)
        auth = MappingAuthenticator({'test-token': OperatorIdentity('alice', OperatorRole.OPERATOR, ('*',))})
        host = RuntimeServiceHost(runtime_config, authenticator=auth, gateway_bridge=None,
            monitor_bridge=bridge, event_store=store, unit_strategies={})
        host.start()
        result = request(host.bound_address[1], control_payload('runtime.unit.pause', unit='alpha',
            command_id=str(uuid.uuid4()), reason='test'), token='test-token')[1]
        assert result['status'] == 'REJECTED', result
        deadline = time.monotonic() + 5
        rows = []
        while not rows and time.monotonic() < deadline:
            rows = fixture._rows(tmp_path)
            if not rows:
                time.sleep(0.02)
        assert rows
        host.close()
        assert store.available
    finally:
        if host is not None and host.state != 'closed':
            host.close()
        elif host is None and bridge is not None:
            bridge.close()
        if store is not None:
            store.close()
        fixture.monitor_fixture.stop(process)
