"""The parent fault/recover RPCs through the real Registry and local owner.

Synthetic Execution principal and a no-op trusted checker; no network or
account. Evidence class: UNIT/FAKE.
"""
from contextlib import contextmanager
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
    CANCEL,
    CANCEL_V2,
    FAULT,
    FAULT_OPERATIONS,
    OPERATIONS,
    RECOVER,
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

PARENT_OPS = OPERATIONS | V2_OPERATIONS | FAULT_OPERATIONS | {SOURCE}


@contextmanager
def fence():
    yield


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
        journal.enable_parent_faults(guard)
        authority = GatewayParentAuthority(journal, guard, now_ns=lambda: 100,
            trading_day=lambda: '20300101', fault_recheck=lambda *_: True)
        control = GatewayDirectControl(journal, guard)
        dispatcher = GatewayDirectDispatcher(journal, 'CTP', guard, lambda *_: None,
                                             lambda _: ('key',), lambda _: True)
        parts.update(journal=journal, authority=authority, control=control, guard=guard)
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
        cap = ServiceCapability('instance', 'service.direct_write', PARENT_OPS | {'direct.reserve'},
                                frozenset({REGISTER, CANCEL, REGISTER_V2, CANCEL_V2, FAULT, RECOVER,
                                           'direct.reserve'}))
        grants = {
            identity.principal_ref: (PrincipalGrant(identity, (ResourceGrant(
                'instance', 'service.direct_write', 'scope', template, PARENT_OPS),)),),
            strategy.principal_ref: (PrincipalGrant(strategy, (ResourceGrant(
                'instance', 'service.direct_write', 'scope', template, frozenset({'direct.reserve'})),)),),
        }
        registry = ServiceRegistry(':memory:', service_id='gateway-service', capabilities=(cap,),
                                   grants=grants, policy_version='p', requirements_version='r')
        instrument = MarketInstrumentKey('ctp', 'SHFE', 'simnow', 'future', 'rb', 'rb')
        grant = GatewayParentGrant(identity, 'account', 'strategy', 'domain', 'execution', 'rev',
                                   frozenset({instrument}), PARENT_OPS)
        parent_service = GatewayParentService(GatewayParentSettings((grant,)), owner=owner,
            journal=parts['journal'], authority=parts['authority'], instance_id='instance', scope_ref='scope')
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
        from bt_api_execution.contracts import ExecutionScope
        from bt_api_py.gateway_execution_subject_scopes import (
            GatewayExecutionSubjectBinding,
            GatewayExecutionSubjectScopes,
        )
        subject = GatewayExecutionSubjectBinding(GatewayExecutionSubjectScopes(
            frozenset({'account'}), {template: ExecutionScope('ctp', 'simnow', 'synthetic-account', 'strategy')}),
            parts['journal'])
        component = build_component(prepared, scope=WireScope('domain'), resource_provider=lambda *_:
            ComponentResources.borrowed({'binding': binding, 'parent_service': parent_service,
                                         'execution_subject_binding': subject}, ready=lambda: True))
        assert set(component.handlers) >= FAULT_OPERATIONS
        registry.register_handler('instance', 'direct.reserve', lambda *_: {})
        for op in PARENT_OPS:
            registry.register_handler('instance', op, component.handlers[op])

        def register(actor, operations, key):
            return _register(registry, principal=actor.principal_ref, identity=actor, scope=scope, key=key,
                             instance_id='instance', port='service.direct_write', scope_ref='scope',
                             operations=tuple(operations))

        assert register(strategy, {'direct.reserve'}, 'strategy')['ok']
        registration = register(identity, PARENT_OPS, 'execution')
        assert registration['ok'], registration
        issued = registration['result']
        parts['registration'] = issued

        def request(operation, arguments):
            return registry.handle(identity.principal_ref, _next_request(operation, {
                'registration_id': issued['registration_id'], 'service_epoch': issued['service_epoch'],
                'instance_id': 'instance', 'arguments': arguments}, identity=identity, scope=scope))

        # Register the parent on the owner thread, as the Execution delegate would.
        wire = dict(_spec())
        wire.pop('risk_selected')
        wire.pop('risk_operation_id')

        def declare_parent():
            return parts['authority'].register_parent(wire, principal='execution-principal',
                actor_ref='execution', fence=fence, recheck=lambda: True)

        value = owner.submit_service_call(declare_parent)
        assert value.result(timeout_seconds=2)['state'] == 'ACTIVE'
        yield {'owner': owner, 'registry': registry, 'journal': parts['journal'],
               'parent_service': parent_service, 'identity': identity, 'scope': scope,
               'request': request, 'db': tmp_path / 'journal'}
    finally:
        if registry is not None:
            registry.close()
        assert owner.close(2)


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


def _fault(env, **changes):
    arguments = dict(_key(), cause_id='fault-1', expected_parent_version=1,
                     expected_plan_version='1', expected_plan_digest='0' * 64,
                     expected_source=_expected(env))
    arguments.update(changes)
    return env['request'](FAULT, arguments)


def _recover(env, **changes):
    arguments = dict(_key(), cause_id='fault-1', recovery_id='recover-1',
                     expected_cause_version=1, expected_parent_version=1,
                     expected_plan_version='1', expected_plan_digest='0' * 64,
                     expected_source=_expected(env))
    arguments.update(changes)
    return env['request'](RECOVER, arguments)


def test_fault_and_recovery_are_served_on_the_source_bound_wire(service):
    asserted = _fault(service)
    assert asserted['ok'], asserted
    reply = asserted['result']
    assert reply['status'] == 'OBSERVED' and reply['schema_version'] == 2 and reply['operation'] == FAULT
    cause = reply['result']
    assert cause['cause_id'] == 'fault-1' and cause['active'] == 1 and cause['version'] == 1
    assert cause['origin'].startswith('execution:')
    assert reply['source']['journal_revision'] > 0
    assert _fault(service)['result'] == reply
    released = _recover(service)['result']
    assert released['operation'] == RECOVER and released['schema_version'] == 2
    assert released['result']['cause']['active'] == 0
    assert released['result']['unresolved'] == []
    assert _recover(service)['result'] == released


@pytest.mark.parametrize('changes', [
    {'cause_id': ''}, {'expected_plan_digest': 'x'}, {'expected_plan_digest': '0' * 63},
    {'expected_parent_version': 0}, {'expected_parent_version': True},
    {'expected_plan_version': 1}, {'expected_source': {}}])
def test_invalid_fault_requests_are_rejected_without_writing(service, changes):
    reply = _fault(service, **changes)
    assert reply == {'ok': True, 'code': 'OK',
                     'result': {'status': 'REQUEST_REJECTED', 'code': 'INVALID_REQUEST'}}, reply
    with service['journal']._transaction() as db:
        assert db.execute('SELECT count(*) FROM direct_execution_parent_faults').fetchone()[0] == 0


@pytest.mark.parametrize('changes', [{'recovery_id': ''}, {'expected_cause_version': 0},
                                     {'expected_cause_version': '1'}, {'cause_id': 'missing'}])
def test_invalid_recovery_requests_are_rejected_or_left_unknown(service, changes):
    assert _fault(service)['ok']
    reply = _recover(service, **changes)
    if changes.get('cause_id') == 'missing':
        # A validly shaped request reaches the owner; the finite owner code is
        # not published on the wire (fail-closed UNKNOWN), nothing is written.
        assert reply['result'] == {'status': 'UNKNOWN'}
    else:
        assert reply == {'ok': True, 'code': 'OK',
                         'result': {'status': 'REQUEST_REJECTED', 'code': 'INVALID_REQUEST'}}
    with service['journal']._transaction() as db:
        assert db.execute('SELECT count(*) FROM direct_execution_parent_fault_recoveries').fetchone()[0] == 0


def test_a_strategy_grant_cannot_hold_the_fault_operations():
    identity = WireIdentity(WireRole.STRATEGY_CLIENT, 'strategy-principal', 'strategy')
    instrument = MarketInstrumentKey('ctp', 'SHFE', 'simnow', 'future', 'rb', 'rb')
    with pytest.raises(ValueError, match='INVALID_PARENT_GRANT'):
        GatewayParentGrant(identity, 'account', 'strategy', 'domain', 'strategy', 'rev',
                           frozenset({instrument}), FAULT_OPERATIONS)
