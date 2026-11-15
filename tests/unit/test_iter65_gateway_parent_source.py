"""Authenticated synthetic Gateway source observation; no account or network I/O."""

import sqlite3
import threading
import time
from dataclasses import replace

import pytest
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_control import GatewayDirectControl
from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher
from bt_api_gateway.direct_parent_authority import GatewayParentAuthority

from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDirectOwnerResources
from bt_api_py.gateway_parent_service import (
    SOURCE,
    GatewayParentGrant,
    GatewayParentService,
    GatewayParentSettings,
)
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    ServiceCapability,
    ServiceRegistry,
)
from tests.unit.test_iter63_gateway_delegate_service_client import _synthetic_owner_factory
from tests.unit.test_service_registry import _next_request, _register


@pytest.fixture
def source_service(tmp_path):
    directory = tmp_path / 'guards'
    directory.mkdir()
    parts, startup_errors = {}, []

    def build(guard, generation):
        journal = GatewayDirectCommandJournal.create(tmp_path / 'journal', 'account', generation=generation)
        journal.enable_controls(guard)
        journal.enable_strategy_ledger(guard)
        journal.enable_account_extensions(guard)
        journal.enable_parent_authority(guard)
        authority = GatewayParentAuthority(journal, guard, now_ns=lambda: 100, trading_day=lambda: '20300101')
        control = GatewayDirectControl(journal, guard)
        dispatcher = GatewayDirectDispatcher(journal, 'CTP', guard, lambda *_: None,
                                             lambda _: ('key',), lambda _: True)
        parts.update(journal=journal, authority=authority, control=control)
        return GatewayDirectOwnerResources(dispatcher, journal, guard.release)

    owner = GatewayDirectOwner(_synthetic_owner_factory(directory, build, startup_errors),
                               queue_size=4, max_queue_wait_seconds=2, busy_timeout_seconds=2)
    registry = None
    try:
        assert owner.start(2), startup_errors
        identity = WireIdentity(WireRole.SERVICE, 'execution-principal', 'execution')
        strategy = WireIdentity(WireRole.STRATEGY_CLIENT, 'strategy-principal', 'strategy')
        template = WireScope('domain', account_ref='account', strategy_ref='strategy')
        scope = replace(template, run_id='run')
        cap = ServiceCapability('instance', 'service.direct_write', frozenset({SOURCE, 'direct.reserve'}),
                                frozenset({'direct.reserve'}))
        grants = {
            identity.principal_ref: (PrincipalGrant(identity, (ResourceGrant(
                'instance', 'service.direct_write', 'scope', template, frozenset({SOURCE})),)),),
            strategy.principal_ref: (PrincipalGrant(strategy, (ResourceGrant(
                'instance', 'service.direct_write', 'scope', template, frozenset({'direct.reserve'})),)),),
        }
        registry = ServiceRegistry(':memory:', service_id='gateway-service', capabilities=(cap,),
                                   grants=grants, policy_version='p', requirements_version='r')
        instrument = MarketInstrumentKey('ctp', 'SHFE', 'simnow', 'future', 'rb', 'rb')
        grant = GatewayParentGrant(identity, 'account', 'strategy', 'domain', 'execution', 'rev',
                                   frozenset({instrument}), frozenset({SOURCE}))
        service = GatewayParentService(GatewayParentSettings((grant,)), owner=owner, journal=parts['journal'],
                                       authority=parts['authority'], instance_id='instance', scope_ref='scope')
        service.configure(registry)
        registry.register_handler('instance', 'direct.reserve', lambda *_: {})
        # Use the production component builder and parent-handler selection,
        # without opening any service socket or invoking a provider.
        from bt_api_py.gateway_direct_service import (
            GatewayControlGrant,
            GatewayDirectGrant,
            GatewayDirectServiceBinding,
        )
        from bt_api_py.service_components import (
            ComponentResources,
            build_component,
            component_specs,
            validate_components,
        )
        from bt_api_py.service_config import parse_service_config
        from tests.integration.test_iter63_gateway_direct_service import _service_config

        raw = _service_config('tcp://127.0.0.1:12345').to_public_dict()
        raw['components']['gateway']['settings']['instance_id'] = 'instance'
        raw['components']['gateway']['settings']['options'] = {
            'role': 'trading_owner', 'dispatch_enabled': True, 'control_enabled': True,
            'strategy_ledger_enabled': True, 'account_extensions_enabled': True,
            'parent_authority_enabled': True,
        }
        prepared = validate_components(component_specs(parse_service_config(raw).components))[0]
        binding = GatewayDirectServiceBinding(parts['journal'], {
            strategy.principal_ref: GatewayDirectGrant(strategy.actor_ref, frozenset({'rb'}))},
            owner=owner, control=parts['control'], control_grants={
                'operator': GatewayControlGrant('operator', True, frozenset(), frozenset({'direct.control.status'}))})
        component = build_component(prepared, scope=WireScope('domain'), resource_provider=lambda *_:
            ComponentResources.borrowed({'binding': binding, 'parent_service': service}, ready=lambda: True))
        assert SOURCE in component.handlers
        assert SOURCE in component.capabilities[0].operations
        assert SOURCE not in component.capabilities[0].write_operations
        registry.register_handler('instance', SOURCE, component.handlers[SOURCE])

        def register(actor, operations, key):
            return _register(registry, principal=actor.principal_ref, identity=actor, scope=scope, key=key,
                             instance_id='instance', port='service.direct_write', scope_ref='scope',
                             operations=tuple(operations))

        assert register(strategy, {'direct.reserve'}, 'strategy')['ok']
        registration = register(identity, {SOURCE}, 'execution')
        assert registration['ok'], registration
        issued = registration['result']

        def request(arguments):
            return registry.handle(identity.principal_ref, _next_request(SOURCE, {
                'registration_id': issued['registration_id'], 'service_epoch': issued['service_epoch'],
                'instance_id': 'instance', 'arguments': arguments}, identity=identity, scope=scope))

        yield {'owner': owner, 'registry': registry, 'journal': parts['journal'], 'grant': grant,
               'identity': identity, 'scope': scope, 'request': request, 'db': tmp_path / 'journal',
               'strategy': strategy, 'register': register}
    finally:
        if registry is not None:
            registry.close()
        assert owner.close(2)


def _arguments():
    return {'account_scope': 'account', 'strategy_scope': 'strategy', 'execution_domain_ref': 'domain'}


def test_source_is_opt_in_and_for_execution_service_only():
    from bt_api_py import gateway_direct_service, gateway_parent_service
    from bt_api_py.service_runtime import PORT_OPERATIONS

    assert SOURCE not in gateway_parent_service.OPERATIONS
    assert SOURCE not in gateway_direct_service.PARENT_OPERATIONS
    assert SOURCE in gateway_direct_service.PARENT_SOURCE_OPERATIONS
    assert SOURCE in gateway_direct_service.TRADING_OPERATIONS
    assert SOURCE in PORT_OPERATIONS['service.direct_write']
    strategy = WireIdentity(WireRole.STRATEGY_CLIENT, 'principal', 'strategy')
    instrument = MarketInstrumentKey('ctp', 'SHFE', 'simnow', 'future', 'rb', 'rb')
    with pytest.raises(ValueError, match='INVALID_PARENT_GRANT'):
        GatewayParentGrant(strategy, 'account', 'strategy', 'domain', 'strategy', 'rev',
                           frozenset({instrument}), frozenset({SOURCE}))


def test_component_and_client_require_explicit_source_selection():
    from bt_api_py.gateway_direct_service import SUPPORTED_OPERATIONS
    from bt_api_py.service_components import component_specs, validate_components
    from bt_api_py.service_config import parse_service_config
    from bt_api_py.service_runtime import ServiceClientRuntime
    from tests.integration.test_iter63_gateway_direct_service import _service_config

    raw = _service_config('tcp://127.0.0.1:12345').to_public_dict()
    settings = raw['components']['gateway']['settings']
    settings['options'] = {'role': 'trading_owner'}
    disabled = validate_components(component_specs(parse_service_config(raw).components))[0]
    assert SOURCE not in disabled.operations_by_port['service.direct_write']
    settings['options'] = {'role': 'trading_owner', 'dispatch_enabled': True, 'control_enabled': True,
                           'strategy_ledger_enabled': True, 'account_extensions_enabled': True,
                           'parent_authority_enabled': True}
    enabled = validate_components(component_specs(parse_service_config(raw).components))[0]
    assert SOURCE in enabled.operations_by_port['service.direct_write']
    assert SOURCE not in enabled.write_operations_by_port['service.direct_write']

    binding = {'instance_id': 'instance', 'port': 'service.direct_write', 'mount': 'upstream.direct_write',
               'scope_ref': 'scope', 'service_id': 'gateway', 'transport_ref': 'transport'}
    capabilities = [{'instance_id': 'instance', 'port': 'service.direct_write',
                     'operations': sorted(SUPPORTED_OPERATIONS | {SOURCE})}]

    class CapturedError(Exception):
        pass

    for requested, expected in ((None, SUPPORTED_OPERATIONS), (frozenset({SOURCE}), frozenset({SOURCE}))):
        runtime = ServiceClientRuntime.__new__(ServiceClientRuntime)
        runtime.client_session_id = 'session'
        runtime._requested_operations = {} if requested is None else {'upstream.direct_write': requested}
        sent = []

        def capture(_service, _transport, _scope, _operation, payload, *, sent=sent, **_kwargs):
            sent.append(payload)
            raise CapturedError

        runtime._rpc = capture
        with pytest.raises(CapturedError):
            runtime._register_port(binding, WireScope('domain'), 'service.direct_write', capabilities,
                                   timeout=1, required=True, heartbeat_interval_seconds=1)
        assert frozenset(sent[0]['operations']) == expected


def test_existing_ctp_profile_can_install_an_explicit_source_grant():
    import zmq

    from bt_api_py.ctp_gateway_local_profile import _delegation
    from bt_api_py.gateway_direct_service import GatewayDirectGrant

    public, _ = zmq.curve_keypair()
    instrument = MarketInstrumentKey('ctp', 'SHFE', 'simnow', 'future', 'rb', 'rb')
    scope = {'execution_domain_ref': 'domain', 'account_ref': 'account', 'account_refs': [],
             'strategy_ref': 'strategy', 'run_id': None, 'manual_book_ref': None,
             'portfolio_ref': None, 'group_id': None, 'leg_id': None}
    delegate = {
        'identity': {'role': 'service', 'principal_ref': 'execution-principal',
                     'actor_ref': 'execution', 'delegate_ref': None},
        'curve_public_key': public.decode('ascii'), 'kind': 'execution', 'revision': 'rev',
        'strategies': {'strategy': {'instruments': [instrument.to_dict()], 'operations': ['direct.reserve'],
                                    'parent_operations': [SOURCE]}},
        'resources': [{'instance_id': 'instance', 'port': 'service.direct_write', 'scope_ref': 'scope',
                       'scope': scope, 'operations': [SOURCE]}],
    }
    principals, grants = {}, {}
    _, parents, _ = _delegation({'delegates': [delegate]}, principals, grants,
                                 {'strategy-principal': GatewayDirectGrant('strategy', frozenset({'rb'}))},
                                 account_scope='account', execution_domain_ref='domain', instruments=(instrument,))
    assert parents.grants[0].operations == {SOURCE}
    assert grants['execution-principal'][0].resources[0].operations == {SOURCE}


def test_real_registry_reads_source_without_a_parent_and_without_mutation(source_service):
    env = source_service
    with sqlite3.connect(env['db']) as db:
        anchor = db.execute('SELECT journal_id, revision FROM direct_journal_meta').fetchone()
    result = env['request'](_arguments())
    assert result['ok'] and result['result'] == {
        'status': 'OBSERVED', 'schema_version': 1, 'operation': SOURCE,
        'result': {'schema_version': 1, 'account_scope': 'account', 'strategy_scope': 'strategy',
                   'execution_domain_ref': 'domain', 'journal_id': anchor[0],
                   'gateway_generation': env['journal'].generation, 'journal_revision': anchor[1]},
    }
    assert env['request'](_arguments())['result'] == result['result']
    with sqlite3.connect(env['db']) as db:
        assert db.execute('SELECT journal_id, revision FROM direct_journal_meta').fetchone() == anchor
        assert db.execute('SELECT COUNT(*) FROM direct_execution_parents').fetchone()[0] == 0


@pytest.mark.parametrize('change', [{'account_scope': 'other'}, {'strategy_scope': 'other'},
                                     {'execution_domain_ref': 'other'}, {'parent_ref': 'unrelated'}])
def test_source_does_not_disclose_for_wrong_request_scope(source_service, change):
    result = source_service['request'](dict(_arguments(), **change))
    assert result['ok'] and result['result'] == {'status': 'REQUEST_REJECTED', 'code': 'INVALID_REQUEST'}


def test_source_registry_rejects_missing_or_changed_grant(source_service):
    env = source_service
    key = ('instance', 'scope', env['identity'].principal_ref, 'account', 'strategy')
    with env['registry']._lock:
        env['registry']._gateway_parent_grants.pop(key)
    result = env['request'](_arguments())
    assert not result['ok'] and result['code'] == 'SCOPE_DENIED'


def test_ungranted_principal_cannot_register_for_source(source_service):
    env = source_service
    outsider = WireIdentity(WireRole.SERVICE, 'outsider', 'execution')
    result = env['register'](outsider, {SOURCE}, 'outsider')
    assert not result['ok']


def test_source_queued_grant_revocation_cannot_disclose(source_service):
    env = source_service
    entered, release = threading.Event(), threading.Event()
    output = []

    def block():
        entered.set()
        if not release.wait(2):
            raise RuntimeError('synthetic timeout')

    blocker = env['owner'].submit_service_call(block)
    assert entered.wait(2)
    worker = threading.Thread(target=lambda: output.append(env['request'](_arguments())))
    worker.start()
    try:
        deadline = time.monotonic() + 0.8
        while env['owner'].status().service_queue_depth != 1 and time.monotonic() < deadline:
            time.sleep(0.002)
        assert env['owner'].status().service_queue_depth == 1
        key = ('instance', 'scope', env['identity'].principal_ref, 'account', 'strategy')
        with env['registry']._lock:
            env['registry']._gateway_parent_grants.pop(key)
    finally:
        release.set()
        blocker.result(timeout_seconds=2)
        worker.join(2)
    assert not worker.is_alive()
    assert output and ((output[0]['ok'] and output[0]['result'] == {'status': 'UNKNOWN'})
                       or (not output[0]['ok'] and output[0]['code'] in {'SCOPE_DENIED', 'DUPLICATE_ACTIVE_RUN'}))


def test_source_queued_strategy_run_expiry_cannot_disclose(source_service):
    env = source_service
    entered, release, output = threading.Event(), threading.Event(), []

    def block():
        entered.set()
        if not release.wait(2):
            raise RuntimeError('synthetic timeout')

    blocker = env['owner'].submit_service_call(block)
    assert entered.wait(2)
    worker = threading.Thread(target=lambda: output.append(env['request'](_arguments())))
    worker.start()
    try:
        deadline = time.monotonic() + 0.8
        while env['owner'].status().service_queue_depth != 1 and time.monotonic() < deadline:
            time.sleep(0.002)
        assert env['owner'].status().service_queue_depth == 1
        with env['registry']._lock:
            env['registry']._db.execute('UPDATE sessions SET expires_at=0 WHERE principal=?',
                                        (env['strategy'].principal_ref,))
    finally:
        release.set()
        blocker.result(timeout_seconds=2)
        worker.join(2)
    assert not worker.is_alive()
    assert output and ((output[0]['ok'] and output[0]['result'] == {'status': 'UNKNOWN'})
                       or (not output[0]['ok'] and output[0]['code'] in {'SCOPE_DENIED', 'DUPLICATE_ACTIVE_RUN'}))
