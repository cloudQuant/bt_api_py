"""Bound parent v2 through actual Registry, component and local Gateway owner."""

import json
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
from bt_api_gateway.session_guard import LocalAccountSessionGuard

from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDirectOwnerResources
from bt_api_py.gateway_parent_service import (
    CANCEL,
    CANCEL_V2,
    OPERATION,
    OPERATION_V2,
    OPERATIONS,
    REGISTER,
    REGISTER_V2,
    SOURCE,
    V2_OPERATIONS,
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
from tests.unit.test_iter63_gateway_parent_client import _spec
from tests.unit.test_service_registry import _next_request, _register


@pytest.fixture
def service(tmp_path):
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
        dispatcher = GatewayDirectDispatcher(journal, 'CTP', guard, lambda *_: None, lambda _: ('key',), lambda _: True)
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
        parent_ops = OPERATIONS | V2_OPERATIONS | {SOURCE}
        cap = ServiceCapability('instance', 'service.direct_write', parent_ops | {'direct.reserve'},
                                frozenset({REGISTER, CANCEL, REGISTER_V2, CANCEL_V2, 'direct.reserve'}))
        grants = {
            identity.principal_ref: (PrincipalGrant(identity, (ResourceGrant(
                'instance', 'service.direct_write', 'scope', template, parent_ops),)),),
            strategy.principal_ref: (PrincipalGrant(strategy, (ResourceGrant(
                'instance', 'service.direct_write', 'scope', template, frozenset({'direct.reserve'})),)),),
        }
        registry = ServiceRegistry(':memory:', service_id='gateway-service', capabilities=(cap,),
                                   grants=grants, policy_version='p', requirements_version='r')
        instrument = MarketInstrumentKey('ctp', 'SHFE', 'simnow', 'future', 'rb', 'rb')
        grant = GatewayParentGrant(identity, 'account', 'strategy', 'domain', 'execution', 'rev',
                                   frozenset({instrument}), parent_ops)
        parent_service = GatewayParentService(GatewayParentSettings((grant,)), owner=owner, journal=parts['journal'],
                                              authority=parts['authority'], instance_id='instance', scope_ref='scope')
        parent_service.configure(registry)

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
            ComponentResources.borrowed({'binding': binding, 'parent_service': parent_service}, ready=lambda: True))
        assert set(component.handlers) >= V2_OPERATIONS
        assert component.capabilities[0].write_operations & V2_OPERATIONS == {REGISTER_V2, CANCEL_V2}
        registry.register_handler('instance', 'direct.reserve', lambda *_: {})
        for op in parent_ops:
            registry.register_handler('instance', op, component.handlers[op])

        def register(actor, operations, key):
            return _register(registry, principal=actor.principal_ref, identity=actor, scope=scope, key=key,
                             instance_id='instance', port='service.direct_write', scope_ref='scope',
                             operations=tuple(operations))

        assert register(strategy, {'direct.reserve'}, 'strategy')['ok']
        registration = register(identity, parent_ops, 'execution')
        assert registration['ok'], registration
        issued = registration['result']

        def request(operation, arguments):
            return registry.handle(identity.principal_ref, _next_request(operation, {
                'registration_id': issued['registration_id'], 'service_epoch': issued['service_epoch'],
                'instance_id': 'instance', 'arguments': arguments}, identity=identity, scope=scope))

        yield {'owner': owner, 'registry': registry, 'journal': parts['journal'], 'grant': grant,
               'parent_service': parent_service,
               'identity': identity, 'scope': scope, 'request': request, 'db': tmp_path / 'journal'}
    finally:
        if registry is not None:
            registry.close()
        assert owner.close(2)


def _spec_wire():
    value = _spec()
    value.pop('risk_selected')
    value.pop('risk_operation_id')
    return value


def _key():
    return {'account_scope': 'account', 'strategy_scope': 'strategy', 'parent_ref': 'parent',
            'registration_operation_id': 'register-1'}


def _expected(env):
    reply = env['request'](SOURCE, {'account_scope': 'account', 'strategy_scope': 'strategy',
                                    'execution_domain_ref': 'domain'})
    assert reply['ok'] and reply['result']['status'] == 'OBSERVED'
    source = reply['result']['result']
    return {'account_scope': source['account_scope'], 'journal_id': source['journal_id'],
            'gateway_generation': source['gateway_generation'], 'minimum_revision': source['journal_revision']}


def test_v2_explicit_operations_preserve_original_constants_and_require_grant():
    from bt_api_py import gateway_direct_service

    assert not V2_OPERATIONS & OPERATIONS
    assert not V2_OPERATIONS & gateway_direct_service.PARENT_OPERATIONS
    assert V2_OPERATIONS == gateway_direct_service.PARENT_V2_OPERATIONS
    strategy = WireIdentity(WireRole.STRATEGY_CLIENT, 'principal', 'strategy')
    instrument = MarketInstrumentKey('ctp', 'SHFE', 'simnow', 'future', 'rb', 'rb')
    with pytest.raises(ValueError, match='INVALID_PARENT_GRANT'):
        GatewayParentGrant(strategy, 'account', 'strategy', 'domain', 'strategy', 'rev',
                           frozenset({instrument}), V2_OPERATIONS)


def test_v2_component_and_runtime_require_explicit_selection():
    from bt_api_py.gateway_direct_service import SUPPORTED_OPERATIONS
    from bt_api_py.service_components import component_specs, validate_components
    from bt_api_py.service_config import parse_service_config
    from bt_api_py.service_runtime import ServiceClientRuntime
    from tests.integration.test_iter63_gateway_direct_service import _service_config

    raw = _service_config('tcp://127.0.0.1:12345').to_public_dict()
    settings = raw['components']['gateway']['settings']
    settings['options'] = {'role': 'trading_owner'}
    disabled = validate_components(component_specs(parse_service_config(raw).components))[0]
    assert not V2_OPERATIONS & disabled.operations_by_port['service.direct_write']
    settings['options'] = {'role': 'trading_owner', 'dispatch_enabled': True, 'control_enabled': True,
                           'strategy_ledger_enabled': True, 'account_extensions_enabled': True,
                           'parent_authority_enabled': True}
    enabled = validate_components(component_specs(parse_service_config(raw).components))[0]
    assert enabled.operations_by_port['service.direct_write'] >= V2_OPERATIONS
    assert V2_OPERATIONS & enabled.write_operations_by_port['service.direct_write'] == {REGISTER_V2, CANCEL_V2}

    binding = {'instance_id': 'instance', 'port': 'service.direct_write', 'mount': 'upstream.direct_write',
               'scope_ref': 'scope', 'service_id': 'gateway', 'transport_ref': 'transport'}
    capabilities = [{'instance_id': 'instance', 'port': 'service.direct_write',
                     'operations': sorted(SUPPORTED_OPERATIONS | V2_OPERATIONS)}]

    class CapturedError(Exception):
        pass

    for requested, expected in ((None, SUPPORTED_OPERATIONS), (V2_OPERATIONS, V2_OPERATIONS)):
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


def test_v2_is_not_authorized_by_an_old_parent_grant(service):
    expected = _expected(service)
    key = ('instance', 'scope', service['identity'].principal_ref, 'account', 'strategy')
    with service['registry']._lock:
        service['registry']._gateway_parent_grants[key] = replace(service['grant'], operations=OPERATIONS)
    result = service['request'](REGISTER_V2, {'spec': _spec_wire(), 'expected_source': expected})
    assert not result['ok'] and result['code'] == 'SCOPE_DENIED'
    with sqlite3.connect(service['db']) as db:
        assert db.execute('SELECT count(*) FROM direct_execution_parents').fetchone()[0] == 0


def test_v2_real_registry_register_query_cancel_and_replay(service):
    expected = _expected(service)
    registered = service['request'](REGISTER_V2, {'spec': _spec_wire(), 'expected_source': expected})
    assert registered['ok'] and registered['result']['status'] == 'OBSERVED'
    outer = registered['result']
    assert outer['schema_version'] == 2 and outer['operation'] == REGISTER_V2
    assert outer['source'] == {'account_scope': 'account', 'journal_id': expected['journal_id'],
                               'gateway_generation': service['journal'].generation,
                               'journal_revision': expected['minimum_revision'] + 1}
    assert outer['result']['state'] == 'ACTIVE'
    assert service['request'](REGISTER_V2, {'spec': _spec_wire(), 'expected_source': expected})['result'] == outer
    query = service['request'](OPERATION_V2, dict(_key(), operation_id='register-1', expected_source=expected))
    assert query['ok'] and query['result']['result']['result'] == outer['result']
    assert query['result']['source'] == outer['source']
    cancel_args = dict(_key(), cancel_operation_id='cancel-1', expected_version=1, expected_source=expected)
    cancel = service['request'](CANCEL_V2, cancel_args)
    assert cancel['ok'] and cancel['result']['result']['state'] == 'CANCEL_REQUESTED'
    assert cancel['result']['result']['targets'] == []
    assert cancel['result']['source']['journal_revision'] == expected['minimum_revision'] + 2
    assert service['request'](CANCEL_V2, cancel_args)['result'] == cancel['result']


@pytest.mark.parametrize('source', [None, {}, {'account_scope': 'account', 'journal_id': 'x', 'minimum_revision': 0},
    {'account_scope': 'other', 'journal_id': 'x', 'gateway_generation': 'g', 'minimum_revision': 0},
    {'account_scope': 'account', 'journal_id': 'x', 'gateway_generation': 'g', 'minimum_revision': True},
    {'account_scope': 'account', 'journal_id': 'x', 'gateway_generation': 'g', 'minimum_revision': -1},
    {'account_scope': 'account', 'journal_id': 'x', 'gateway_generation': 'g', 'minimum_revision': 0, 'extra': 1},
    {'account_scope': 'account', 'journal_id': 'x', 'gateway_generation': True, 'minimum_revision': 0},
    {'account_scope': 'account', 'journal_id': 'x', 'gateway_generation': '', 'minimum_revision': 0}])
def test_v2_malformed_expected_source_rejected_before_parent_write(service, source):
    result = service['request'](REGISTER_V2, {'spec': _spec_wire(), 'expected_source': source})
    assert result['ok'] and result['result'] == {'status': 'REQUEST_REJECTED', 'code': 'INVALID_REQUEST'}
    with sqlite3.connect(service['db']) as db:
        assert db.execute('SELECT count(*) FROM direct_execution_parents').fetchone()[0] == 0


def test_v2_rejects_other_real_journal_and_future_revision(service, tmp_path):
    directory = tmp_path / 'other-guards'
    directory.mkdir()
    other_guard = LocalAccountSessionGuard(directory, 'account', 'other')
    try:
        other = GatewayDirectCommandJournal.create(tmp_path / 'other-journal', 'account', generation=other_guard.acquire())
        expected = _expected(service)
        for source in (dict(expected, journal_id=other.journal_id),
                       dict(expected, minimum_revision=expected['minimum_revision'] + 1)):
            result = service['request'](REGISTER_V2, {'spec': _spec_wire(), 'expected_source': source})
            assert result['ok'] and result['result'] == {'status': 'UNKNOWN'}
        with sqlite3.connect(service['db']) as db:
            assert db.execute('SELECT count(*) FROM direct_execution_parents').fetchone()[0] == 0
            assert db.execute('SELECT count(*) FROM direct_execution_parent_operations').fetchone()[0] == 0
    finally:
        other_guard.release()


def test_v2_wrong_source_cannot_query_or_cancel_existing_parent(service):
    expected = _expected(service)
    assert service['request'](REGISTER_V2, {'spec': _spec_wire(), 'expected_source': expected})['result']['status'] == 'OBSERVED'
    wrong = dict(expected, journal_id='different')
    assert service['request'](OPERATION_V2, dict(_key(), operation_id='register-1', expected_source=wrong))['result'] == {'status': 'UNKNOWN'}
    assert service['request'](CANCEL_V2, dict(_key(), cancel_operation_id='cancel-1', expected_version=1,
                                             expected_source=wrong))['result'] == {'status': 'UNKNOWN'}
    with sqlite3.connect(service['db']) as db:
        assert db.execute('SELECT state FROM direct_execution_parents').fetchone()[0] == 'ACTIVE'
        assert db.execute('SELECT count(*) FROM direct_execution_parent_operations').fetchone()[0] == 1


def test_v2_wrong_generation_cannot_register_query_or_cancel(service):
    expected = _expected(service)
    assert service['request'](REGISTER_V2, {'spec': _spec_wire(), 'expected_source': expected})['result']['status'] == 'OBSERVED'
    wrong = dict(expected, gateway_generation='old-generation')
    other = dict(_spec_wire(), parent_ref='other-parent', registration_operation_id='other-register')
    for operation, arguments in (
        (REGISTER_V2, {'spec': other, 'expected_source': wrong}),
        (OPERATION_V2, dict(_key(), operation_id='register-1', expected_source=wrong)),
        (CANCEL_V2, dict(_key(), cancel_operation_id='cancel-1', expected_version=1, expected_source=wrong)),
    ):
        assert service['request'](operation, arguments)['result'] == {'status': 'UNKNOWN'}
    with sqlite3.connect(service['db']) as db:
        assert db.execute('SELECT state, version FROM direct_execution_parents').fetchall() == [('ACTIVE', 1)]
        assert db.execute('SELECT count(*) FROM direct_execution_parent_operations').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM direct_execution_parent_cancel_targets').fetchone()[0] == 0


def test_v1_parent_wire_remains_unchanged(service):
    registration = service['request'](REGISTER, {'spec': _spec_wire()})['result']
    assert set(registration) == {'status', 'schema_version', 'operation', 'result'}
    assert registration['schema_version'] == 1 and registration['result']['state'] == 'ACTIVE'
    operation = service['request'](OPERATION, dict(_key(), operation_id='register-1'))['result']
    assert operation['schema_version'] == 1 and operation['result']['result'] == registration['result']
    cancel = service['request'](CANCEL, dict(_key(), cancel_operation_id='cancel-1', expected_version=1))['result']
    assert cancel['schema_version'] == 1 and cancel['result']['state'] == 'CANCEL_REQUESTED'


def test_v2_grant_revoked_while_queued_never_writes(service):
    expected = _expected(service)
    entered, release, output = threading.Event(), threading.Event(), []

    def block():
        entered.set()
        if not release.wait(2):
            raise RuntimeError('synthetic timeout')

    blocker = service['owner'].submit_service_call(block)
    assert entered.wait(2)
    worker = threading.Thread(target=lambda: output.append(service['request'](
        REGISTER_V2, {'spec': _spec_wire(), 'expected_source': expected})))
    worker.start()
    try:
        deadline = time.monotonic() + 0.8
        while service['owner'].status().service_queue_depth != 1 and time.monotonic() < deadline:
            time.sleep(0.002)
        assert service['owner'].status().service_queue_depth == 1
        key = ('instance', 'scope', service['identity'].principal_ref, 'account', 'strategy')
        with service['registry']._lock:
            service['registry']._gateway_parent_grants.pop(key)
    finally:
        release.set()
        blocker.result(timeout_seconds=2)
        worker.join(2)
    assert not worker.is_alive()
    assert output and ((output[0]['ok'] and output[0]['result'] == {'status': 'UNKNOWN'})
                       or (not output[0]['ok'] and output[0]['code'] in {'SCOPE_DENIED', 'DUPLICATE_ACTIVE_RUN'}))
    with sqlite3.connect(service['db']) as db:
        assert db.execute('SELECT count(*) FROM direct_execution_parents').fetchone()[0] == 0


def test_v2_other_strategy_rejected_for_register_and_existing_parent(service):
    expected = _expected(service)
    assert service['request'](REGISTER_V2, {'spec': _spec_wire(), 'expected_source': expected})['result']['status'] == 'OBSERVED'
    other_spec = dict(_spec_wire(), strategy_scope='other-strategy', parent_ref='other-parent')
    other_key = dict(_key(), strategy_scope='other-strategy')
    for operation, arguments in (
        (REGISTER_V2, {'spec': other_spec, 'expected_source': expected}),
        (OPERATION_V2, dict(other_key, operation_id='register-1', expected_source=expected)),
        (CANCEL_V2, dict(other_key, cancel_operation_id='cancel-1', expected_version=1, expected_source=expected)),
    ):
        response = service['request'](operation, arguments)
        assert response['ok'] and response['result'] == {'status': 'REQUEST_REJECTED', 'code': 'INVALID_REQUEST'}
    with sqlite3.connect(service['db']) as db:
        assert db.execute('SELECT strategy, state, version FROM direct_execution_parents').fetchall() == [('strategy', 'ACTIVE', 1)]
        assert db.execute('SELECT count(*) FROM direct_execution_parent_operations').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM direct_execution_parent_cancel_targets').fetchone()[0] == 0


def test_v2_narrowed_instrument_grant_blocks_existing_parent_and_new_register(service):
    expected = _expected(service)
    assert service['request'](REGISTER_V2, {'spec': _spec_wire(), 'expected_source': expected})['result']['status'] == 'OBSERVED'
    other = MarketInstrumentKey('ctp', 'SHFE', 'simnow', 'future', 'cu', 'cu')
    narrowed = replace(service['grant'], instruments=frozenset({other}))
    key = ('instance', 'scope', service['identity'].principal_ref, 'account', 'strategy')
    service['parent_service'].settings = GatewayParentSettings((narrowed,))
    with service['registry']._lock:
        service['registry']._gateway_parent_grants[key] = narrowed
    denied_spec = dict(_spec_wire(), parent_ref='other-parent', registration_operation_id='register-2')
    response = service['request'](REGISTER_V2, {'spec': denied_spec, 'expected_source': expected})
    assert response['ok'] and response['result'] == {'status': 'REQUEST_REJECTED', 'code': 'INVALID_REQUEST'}
    for operation, arguments in (
        (OPERATION_V2, dict(_key(), operation_id='register-1', expected_source=expected)),
        (CANCEL_V2, dict(_key(), cancel_operation_id='cancel-1', expected_version=1, expected_source=expected)),
    ):
        response = service['request'](operation, arguments)
        assert response['ok'] and response['result'] == {'status': 'UNKNOWN'}
    with sqlite3.connect(service['db']) as db:
        assert db.execute('SELECT state, version FROM direct_execution_parents').fetchall() == [('ACTIVE', 1)]
        assert db.execute('SELECT count(*) FROM direct_execution_parent_operations').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM direct_execution_parent_cancel_targets').fetchone()[0] == 0


def _ready_args(**extra):
    return dict(account_scope='account', strategy_scope='strategy', execution_domain_ref='domain',
                readiness_version=2, **extra)


def _not_ready(reply):
    assert not reply['ok'] or reply['result'].get('status') != 'OBSERVED'


def test_source_readiness_has_explicit_version_and_never_records_write(service, monkeypatch):
    def forbidden_write(**_kwargs):
        raise AssertionError('readiness must not record a write attempt')

    monkeypatch.setattr(service['registry'], '_record_migration_write_attempt', forbidden_write)
    original = service['request'](SOURCE, {'account_scope': 'account', 'strategy_scope': 'strategy',
                                           'execution_domain_ref': 'domain'})
    assert original['ok'] and set(original['result']) == {'status', 'schema_version', 'operation', 'result'}
    assert original['result']['schema_version'] == 1
    observed = service['request'](SOURCE, _ready_args())
    assert observed['ok'] and observed['result'] == {
        'status': 'OBSERVED', 'schema_version': 2, 'operation': SOURCE, 'result': original['result']['result'],
        'checked_operations': [SOURCE, REGISTER_V2, OPERATION_V2, CANCEL_V2],
    }
    with sqlite3.connect(service['db']) as db:
        assert db.execute('SELECT count(*) FROM direct_execution_parents').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM direct_execution_parent_operations').fetchone()[0] == 0


@pytest.mark.parametrize('version', [True, 1, 3, '2', None])
def test_source_readiness_rejects_other_versions(service, version):
    result = service['request'](SOURCE, dict(_ready_args(), readiness_version=version))
    assert result['ok'] and result['result'] == {'status': 'REQUEST_REJECTED', 'code': 'INVALID_REQUEST'}


@pytest.mark.parametrize('operation', [SOURCE, REGISTER_V2, OPERATION_V2, CANCEL_V2])
@pytest.mark.parametrize('missing', ['capability', 'registration', 'resource', 'parent', 'handler'])
def test_source_readiness_requires_each_live_authorization(service, operation, missing):
    registry = service['registry']
    principal = service['identity'].principal_ref
    with registry._lock:
        if missing == 'capability':
            key = ('instance', 'service.direct_write')
            cap = registry._capabilities[key]
            capabilities = dict(registry._capabilities)
            capabilities[key] = replace(cap, operations=cap.operations - {operation},
                                        write_operations=cap.write_operations - {operation})
            registry._capabilities = capabilities
        elif missing == 'registration':
            row = registry._db.execute('SELECT registration_id,operations_json,write_operations_json FROM sessions WHERE principal=?',
                                       (principal,)).fetchone()
            ops = set(json.loads(row['operations_json'])) - {operation}
            writes = set(json.loads(row['write_operations_json'])) - {operation}
            registry._db.execute('UPDATE sessions SET operations_json=?,write_operations_json=? WHERE registration_id=?',
                                 (json.dumps(sorted(ops)), json.dumps(sorted(writes)), row['registration_id']))
        elif missing == 'resource':
            grants = dict(registry._grants)
            current = grants[principal][0]
            resource = current.resources[0]
            grants[principal] = (replace(current, resources=(replace(resource, operations=resource.operations - {operation}),)),)
            registry._grants = grants
        elif missing == 'parent':
            key = ('instance', 'scope', principal, 'account', 'strategy')
            registry._gateway_parent_grants[key] = replace(service['grant'], operations=service['grant'].operations - {operation})
        else:
            registry._handlers.pop(('instance', operation))
    _not_ready(service['request'](SOURCE, _ready_args()))
    with sqlite3.connect(service['db']) as db:
        assert db.execute('SELECT count(*) FROM direct_execution_parents').fetchone()[0] == 0


@pytest.mark.parametrize('operation', [SOURCE, OPERATION_V2])
def test_source_readiness_rejects_read_operation_misclassified_as_write(service, operation):
    registry = service['registry']
    with registry._lock:
        key = ('instance', 'service.direct_write')
        cap = registry._capabilities[key]
        capabilities = dict(registry._capabilities)
        capabilities[key] = replace(cap, write_operations=cap.write_operations | {operation})
        registry._capabilities = capabilities
    _not_ready(service['request'](SOURCE, _ready_args()))


@pytest.mark.parametrize('operation', [REGISTER_V2, CANCEL_V2])
@pytest.mark.parametrize('missing', ['capability', 'registration'])
def test_source_readiness_requires_correct_write_classification(service, operation, missing):
    registry = service['registry']
    with registry._lock:
        if missing == 'capability':
            key = ('instance', 'service.direct_write')
            cap = registry._capabilities[key]
            capabilities = dict(registry._capabilities)
            capabilities[key] = replace(cap, write_operations=cap.write_operations - {operation})
            registry._capabilities = capabilities
        else:
            row = registry._db.execute('SELECT registration_id,write_operations_json FROM sessions WHERE principal=?',
                                       (service['identity'].principal_ref,)).fetchone()
            writes = set(json.loads(row['write_operations_json'])) - {operation}
            registry._db.execute('UPDATE sessions SET write_operations_json=? WHERE registration_id=?',
                                 (json.dumps(sorted(writes)), row['registration_id']))
    _not_ready(service['request'](SOURCE, _ready_args()))


@pytest.mark.parametrize('principal', ['execution-principal', 'strategy-principal'])
def test_source_readiness_requires_live_execution_and_strategy_leases(service, principal):
    registry = service['registry']
    with registry._lock:
        registry._db.execute('UPDATE sessions SET expires_at=0 WHERE principal=?', (principal,))
    _not_ready(service['request'](SOURCE, _ready_args()))


def test_source_readiness_rechecks_grant_after_source_read(service, monkeypatch):
    registry, journal = service['registry'], service['journal']
    original = journal._assert_anchor
    calls = []

    def revoke_during_source(db):
        revision = original(db)
        calls.append(True)
        key = ('instance', 'scope', service['identity'].principal_ref, 'account', 'strategy')
        registry._gateway_parent_grants[key] = replace(service['grant'], operations=service['grant'].operations - {CANCEL_V2})
        return revision

    monkeypatch.setattr(journal, '_assert_anchor', revoke_during_source)
    _not_ready(service['request'](SOURCE, _ready_args()))
    assert calls == [True]


def test_source_readiness_rechecks_lease_after_source_read(service, monkeypatch):
    registry, journal = service['registry'], service['journal']
    original = journal._assert_anchor
    clock = registry._clock
    calls = []

    def expire_during_source(db):
        revision = original(db)
        calls.append(True)
        registry._clock = lambda: clock() + 3600
        return revision

    monkeypatch.setattr(journal, '_assert_anchor', expire_during_source)
    try:
        _not_ready(service['request'](SOURCE, _ready_args()))
        assert calls == [True]
    finally:
        registry._clock = clock


def test_source_readiness_rechecks_guard_after_source_read(service, monkeypatch):
    journal = service['journal']
    original = journal._assert_anchor
    guard = service['parent_service'].authority._guard
    calls = []

    def release_during_source(db):
        revision = original(db)
        calls.append(True)
        guard.release()
        return revision

    monkeypatch.setattr(journal, '_assert_anchor', release_during_source)
    _not_ready(service['request'](SOURCE, _ready_args()))
    assert calls == [True]


def test_source_readiness_serializes_grant_revocation(service, monkeypatch):
    registry, journal = service['registry'], service['journal']
    original = journal._assert_anchor
    entered, resume, revoked = threading.Event(), threading.Event(), threading.Event()
    output = []

    def block_in_snapshot(db):
        revision = original(db)
        entered.set()
        if not resume.wait(2):
            raise RuntimeError('synthetic timeout')
        return revision

    def revoke():
        with registry._lock:
            key = ('instance', 'scope', service['identity'].principal_ref, 'account', 'strategy')
            registry._gateway_parent_grants[key] = replace(service['grant'], operations=service['grant'].operations - {CANCEL_V2})
            revoked.set()

    monkeypatch.setattr(journal, '_assert_anchor', block_in_snapshot)
    reader = threading.Thread(target=lambda: output.append(service['request'](SOURCE, _ready_args())))
    reader.start()
    changer = threading.Thread(target=revoke)
    try:
        assert entered.wait(2)
        changer.start()
        assert not revoked.wait(0.05)
    finally:
        resume.set()
        reader.join(2)
        if changer.ident is not None:
            changer.join(2)
    assert not reader.is_alive() and not changer.is_alive() and revoked.is_set()
    # Registry may also reject the completed observation when it resumes
    # after revocation; either outcome respects the serialized snapshot.
    assert output and (not output[0]['ok'] or output[0]['result']['status'] == 'OBSERVED')
    _not_ready(service['request'](SOURCE, _ready_args()))
