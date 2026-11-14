"""Account-level netting service policy and paired Registry admissions only."""
from dataclasses import replace
import os
import threading

import pytest
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py.gateway_parent_service import GatewayParentGrant
from bt_api_py.service_registry import (
    GatewayNettingServicePolicy, PrincipalGrant, ResourceGrant, ServiceCapability,
    ServiceDispatchAdmissionError, ServiceRegistry,
)
from tests.unit.test_service_registry import _next_request, _register


SERVICE = WireIdentity(WireRole.SERVICE, 'execution-principal', 'execution')
INSTRUMENT = MarketInstrumentKey('ctp', 'SHFE', 'simnow', 'future', 'rb', 'rb')
OTHER = MarketInstrumentKey('ctp', 'SHFE', 'simnow', 'future', 'cu', 'cu')
OPS = frozenset({'direct.parent.query', 'direct.parent.cancel'})
V2_OPS = frozenset({'direct.parent.operation_v2', 'direct.parent.cancel_v2'})


def _make_env(*, v2=False):
    now = [100.0]
    operations = V2_OPS if v2 else OPS
    query = 'direct.parent.operation_v2' if v2 else 'direct.parent.query'
    cancel = 'direct.parent.cancel_v2' if v2 else 'direct.parent.cancel'
    scopes = [WireScope('domain', account_ref='account', strategy_ref=s, run_id='run-' + s)
              for s in ('strategy-a', 'strategy-b')]
    strategies = [WireIdentity(WireRole.STRATEGY_CLIENT, 'principal-' + s.strategy_ref, s.strategy_ref)
                  for s in scopes]
    service_resources = tuple(ResourceGrant('gateway', 'service.direct_write', 'scope',
        replace(scope, run_id=None), operations) for scope in scopes)
    grants = {SERVICE.principal_ref: (PrincipalGrant(SERVICE, service_resources),)}
    for strategy, scope in zip(strategies, scopes):
        grants[strategy.principal_ref] = (PrincipalGrant(strategy, (ResourceGrant(
            'gateway', 'service.direct_write', 'scope', replace(scope, run_id=None),
            frozenset({'direct.reserve'})),)),)
    cap = ServiceCapability('gateway', 'service.direct_write', operations | {'direct.reserve'},
                            frozenset({cancel, 'direct.reserve'}))
    registry = ServiceRegistry(':memory:', service_id='gateway-service', capabilities=(cap,),
        grants=grants, policy_version='p', requirements_version='r', lease_seconds=10,
        clock=lambda: now[0])
    policy = GatewayNettingServicePolicy('gateway', SERVICE, 'domain', 'account', frozenset({INSTRUMENT}))
    registry.configure_gateway_parents(instance_id='gateway', scope_ref='scope', grants=tuple(
        GatewayParentGrant(SERVICE, 'account', scope.strategy_ref, 'domain', 'execution', 'rev',
                           frozenset({INSTRUMENT}), operations) for scope in scopes))
    captured = []
    registry.register_handler('gateway', 'direct.reserve', lambda *_: {'status': 'ok'})
    registry.register_handler('gateway', cancel, lambda *_: {'status': 'ok'})
    registry.register_handler('gateway', query,
                              lambda context, arguments: captured.append(context.dispatch_admission) or {'status': 'ok'})
    service_regs, strategy_regs, args = [], [], []
    for i, (strategy, scope) in enumerate(zip(strategies, scopes)):
        shared = dict(scope=scope, instance_id='gateway', port='service.direct_write', scope_ref='scope')
        result = _register(registry, identity=strategy, principal=strategy.principal_ref,
                           key='strat-' + str(i), operations=('direct.reserve',), **shared)
        assert result['ok'], result
        strategy_regs.append(result['result'])
        result = _register(registry, identity=SERVICE, principal=SERVICE.principal_ref,
                           key='svc-' + str(i), operations=tuple(sorted(operations)), **shared)
        assert result['ok'], result
        service_regs.append(result['result'])
        argument = {'account_scope': 'account', 'strategy_scope': scope.strategy_ref,
                    'parent_ref': 'parent-' + str(i), 'registration_operation_id': 'reg-' + str(i)}
        if v2:
            argument.update(operation_id='op-' + str(i), expected_source={
                'account_scope': 'account', 'journal_id': 'journal',
                'gateway_generation': 'generation', 'minimum_revision': 1})
        args.append(argument)
        reply = registry.handle(SERVICE.principal_ref, _next_request(query, {
            'registration_id': result['result']['registration_id'], 'service_epoch': result['result']['service_epoch'],
            'instance_id': 'gateway', 'arguments': argument}, identity=SERVICE, scope=scope))
        assert reply['ok'], reply
    try:
        yield registry, policy, captured, args, scopes, strategies, service_regs, strategy_regs, now
    finally:
        registry.close()


@pytest.fixture
def env():
    yield from _make_env()


@pytest.fixture
def v2_env():
    yield from _make_env(v2=True)


def bind(env):
    registry, _, admissions, args, *_ = env
    return registry.bind_gateway_parent_pair(admissions[0], args[0], admissions[1], args[1], instrument=INSTRUMENT)


@pytest.fixture
def second_env():
    second = _make_env()
    try:
        yield next(second)
    finally:
        second.close()


def test_v2_admissions_bind_same_source_and_current_service_grants(v2_env):
    registry, policy, admissions, args, scopes, _, service_regs, _, _ = v2_env
    registry.configure_gateway_netting_policies(policies=(policy,))
    pair = bind(v2_env)
    with pair.scope() as check:
        check()
    changed = {**args[1], 'expected_source': {**args[1]['expected_source'], 'journal_id': 'other'}}
    reply = registry.handle(SERVICE.principal_ref, _next_request('direct.parent.operation_v2', {
        'registration_id': service_regs[1]['registration_id'],
        'service_epoch': service_regs[1]['service_epoch'], 'instance_id': 'gateway',
        'arguments': changed}, identity=SERVICE, scope=scopes[1]))
    assert reply['ok'], reply
    with pytest.raises(ServiceDispatchAdmissionError, match='SCOPE_DENIED'):
        registry.bind_gateway_parent_pair(admissions[0], args[0], admissions[2], changed,
                                          instrument=INSTRUMENT)


def test_default_denied_and_account_service_policy_has_no_strategy_opt_in(env):
    registry, policy, *_ = env
    with pytest.raises(ServiceDispatchAdmissionError, match='SCOPE_DENIED'):
        bind(env)
    registry.configure_gateway_netting_policies(policies=(policy,))
    pair = bind(env)
    with pair.scope() as check:
        check()
    assert registry.revoke_gateway_netting_policy(policy)
    with pytest.raises(ServiceDispatchAdmissionError, match='SCOPE_DENIED'):
        with pair.scope():
            pass
    # Reinstalling even the same object cannot revive an issued pair.
    registry.configure_gateway_netting_policies(policies=(policy,))
    with pytest.raises(ServiceDispatchAdmissionError, match='SCOPE_DENIED'):
        with pair.scope():
            pass


def test_exact_query_binding_and_instrument(env):
    registry, policy, admissions, args, *_ = env
    registry.configure_gateway_netting_policies(policies=(policy,))
    with pytest.raises(ServiceDispatchAdmissionError, match='SCOPE_DENIED'):
        registry.bind_gateway_parent_pair(admissions[0], {**args[0], 'parent_ref': 'other'},
                                          admissions[1], args[1], instrument=INSTRUMENT)
    with pytest.raises(ServiceDispatchAdmissionError, match='SCOPE_DENIED'):
        registry.bind_gateway_parent_pair(admissions[0], args[0], admissions[1], args[1], instrument=OTHER)
    with pytest.raises(ServiceDispatchAdmissionError, match='SCOPE_DENIED'):
        registry.bind_gateway_parent_pair(admissions[0], args[0], admissions[0], args[0], instrument=INSTRUMENT)


def test_two_real_registries_cannot_share_pair(env, second_env):
    registry, policy, admissions, args, *_ = env
    _, _, other_admissions, other_args, *_ = second_env
    registry.configure_gateway_netting_policies(policies=(policy,))
    with pytest.raises(ServiceDispatchAdmissionError, match='SCOPE_DENIED'):
        registry.bind_gateway_parent_pair(admissions[0], args[0], other_admissions[1], other_args[1],
                                          instrument=INSTRUMENT)


def test_two_parent_queries_in_same_strategy_are_not_a_cross_strategy_pair(env):
    registry, policy, admissions, args, scopes, _, service_regs, _, _ = env
    registry.configure_gateway_netting_policies(policies=(policy,))
    second_query = {**args[0], 'parent_ref': 'other-parent'}
    result = registry.handle(SERVICE.principal_ref, _next_request('direct.parent.query', {
        'registration_id': service_regs[0]['registration_id'],
        'service_epoch': service_regs[0]['service_epoch'], 'instance_id': 'gateway',
        'arguments': second_query}, identity=SERVICE, scope=scopes[0]))
    assert result['ok'], result
    with pytest.raises(ServiceDispatchAdmissionError, match='SCOPE_DENIED'):
        registry.bind_gateway_parent_pair(admissions[0], args[0], admissions[2], second_query,
                                          instrument=INSTRUMENT)


@pytest.mark.parametrize('variant', ['service', 'instance', 'account', 'domain', 'no_cancel'])
def test_real_second_registration_cannot_widen_scope_or_service_authority(variant):
    service2 = (WireIdentity(WireRole.SERVICE, 'other-service', 'other-execution')
                if variant == 'service' else SERVICE)
    services = (SERVICE, service2)
    instances = ('gateway', 'other-gateway' if variant == 'instance' else 'gateway')
    scopes = (WireScope('domain', account_ref='account', strategy_ref='a', run_id='run-a'),
              WireScope('other-domain' if variant == 'domain' else 'domain',
                        account_ref='other-account' if variant == 'account' else 'account',
                        strategy_ref='b', run_id='run-b'))
    strategies = tuple(WireIdentity(WireRole.STRATEGY_CLIENT, 'principal-' + scope.strategy_ref,
                                    scope.strategy_ref) for scope in scopes)
    service_ops = (OPS, frozenset({'direct.parent.query'}) if variant == 'no_cancel' else OPS)
    capability_ops = {}
    resources = {}
    parent_grants = {}
    for instance, scope, identity, strategy, operations in zip(instances, scopes, services, strategies, service_ops):
        capability_ops.setdefault(instance, set()).update(operations | {'direct.reserve'})
        resources.setdefault(identity, []).append(ResourceGrant(instance, 'service.direct_write', 'scope',
            replace(scope, run_id=None), operations))
        resources.setdefault(strategy, []).append(ResourceGrant(instance, 'service.direct_write', 'scope',
            replace(scope, run_id=None), frozenset({'direct.reserve'})))
        parent_grants.setdefault(instance, []).append(GatewayParentGrant(identity, scope.account_ref,
            scope.strategy_ref, scope.execution_domain_ref, 'execution', 'rev', frozenset({INSTRUMENT}), operations))
    principal_grants = {identity.principal_ref: (PrincipalGrant(identity, tuple(items)),)
                        for identity, items in resources.items()}
    capabilities = tuple(ServiceCapability(instance, 'service.direct_write', frozenset(operations),
        frozenset(operations) & {'direct.reserve', 'direct.parent.cancel'})
        for instance, operations in capability_ops.items())
    registry = ServiceRegistry(':memory:', service_id='gateway-service', capabilities=capabilities,
        grants=principal_grants, policy_version='p', requirements_version='r', clock=lambda: 100.)
    captured, args = [], []
    try:
        for instance, parent in parent_grants.items():
            registry.configure_gateway_parents(instance_id=instance, scope_ref='scope', grants=tuple(parent))
        for instance in capability_ops:
            registry.register_handler(instance, 'direct.reserve', lambda *_: {'status': 'ok'})
            registry.register_handler(instance, 'direct.parent.query',
                lambda context, arguments: captured.append(context.dispatch_admission) or {'status': 'ok'})
            if 'direct.parent.cancel' in capability_ops[instance]:
                registry.register_handler(instance, 'direct.parent.cancel', lambda *_: {'status': 'ok'})
        registry.configure_gateway_netting_policies(policies=(GatewayNettingServicePolicy(
            'gateway', SERVICE, 'domain', 'account', frozenset({INSTRUMENT})),))
        for i, (scope, identity, strategy, instance, operations) in enumerate(
                zip(scopes, services, strategies, instances, service_ops)):
            shared = dict(scope=scope, instance_id=instance, port='service.direct_write', scope_ref='scope')
            result = _register(registry, identity=strategy, principal=strategy.principal_ref,
                key='strategy-' + str(i), operations=('direct.reserve',), **shared)
            assert result['ok'], result
            result = _register(registry, identity=identity, principal=identity.principal_ref,
                key='service-' + str(i), operations=tuple(operations), **shared)
            assert result['ok'], result
            argument = {'account_scope': scope.account_ref, 'strategy_scope': scope.strategy_ref,
                        'parent_ref': 'parent-' + str(i), 'registration_operation_id': 'reg-' + str(i)}
            args.append(argument)
            reply = registry.handle(identity.principal_ref, _next_request('direct.parent.query', {
                'registration_id': result['result']['registration_id'],
                'service_epoch': result['result']['service_epoch'], 'instance_id': instance,
                'arguments': argument}, identity=identity, scope=scope))
            assert reply['ok'], reply
        if variant == 'no_cancel':
            pair = registry.bind_gateway_parent_pair(captured[0], args[0], captured[1], args[1], instrument=INSTRUMENT)
            with pytest.raises(ServiceDispatchAdmissionError):
                with pair.scope():
                    pass
        else:
            with pytest.raises(ServiceDispatchAdmissionError, match='SCOPE_DENIED'):
                registry.bind_gateway_parent_pair(captured[0], args[0], captured[1], args[1], instrument=INSTRUMENT)
    finally:
        registry.close()


@pytest.mark.parametrize('which', [0, 1])
def test_strategy_session_revocation_closes_pair(env, which):
    registry, policy, _, _, scopes, strategies, _, strategy_regs, _ = env
    registry.configure_gateway_netting_policies(policies=(policy,))
    pair = bind(env)
    reg = strategy_regs[which]
    result = registry.handle(strategies[which].principal_ref, _next_request('service.unregister', {
        'registration_id': reg['registration_id'], 'service_epoch': reg['service_epoch'],
    }, identity=strategies[which], scope=scopes[which]))
    assert result['ok'], result
    with pytest.raises(ServiceDispatchAdmissionError):
        with pair.scope():
            pass


@pytest.mark.parametrize('which', [0, 1])
def test_service_session_revocation_closes_pair(env, which):
    registry, policy, _, _, scopes, _, service_regs, _, _ = env
    registry.configure_gateway_netting_policies(policies=(policy,))
    pair = bind(env)
    reg = service_regs[which]
    result = registry.handle(SERVICE.principal_ref, _next_request('service.unregister', {
        'registration_id': reg['registration_id'], 'service_epoch': reg['service_epoch'],
    }, identity=SERVICE, scope=scopes[which]))
    assert result['ok'], result
    with pytest.raises(ServiceDispatchAdmissionError):
        with pair.scope():
            pass


@pytest.mark.parametrize('role', ['service', 'strategy-a', 'strategy-b'])
def test_principal_acl_revocation_closes_pair(env, role):
    registry, policy, _, _, _, strategies, *_ = env
    registry.configure_gateway_netting_policies(policies=(policy,))
    pair = bind(env)
    principal = SERVICE.principal_ref if role == 'service' else next(
        identity.principal_ref for identity in strategies if identity.actor_ref == role)
    assert registry.revoke_principal(principal)
    with pytest.raises(ServiceDispatchAdmissionError):
        with pair.scope():
            pass


def test_both_existing_service_grants_must_allow_instrument(env):
    registry, policy, admissions, args, *_ = env
    broader = replace(policy, instruments=frozenset({INSTRUMENT, OTHER}))
    registry.configure_gateway_netting_policies(policies=(broader,))
    pair = registry.bind_gateway_parent_pair(admissions[0], args[0], admissions[1], args[1], instrument=OTHER)
    with pytest.raises(ServiceDispatchAdmissionError, match='SCOPE_DENIED'):
        with pair.scope():
            pass


def test_unready_registry_and_revoked_admission_fail_inside_scope(env):
    registry, policy, admissions, *_ = env
    registry.configure_gateway_netting_policies(policies=(policy,))
    pair = bind(env)
    registry.set_ready(False)
    with pytest.raises(ServiceDispatchAdmissionError):
        with pair.scope():
            pass
    registry.set_ready(True)
    with pytest.raises(ServiceDispatchAdmissionError, match='ADMISSION_CLOSED'):
        with pair.scope() as check:
            check()
            admissions[0].close()


def test_policy_revocation_serializes_after_snapshot(env):
    registry, policy, *_ = env
    registry.configure_gateway_netting_policies(policies=(policy,))
    pair = bind(env)
    started, completed = threading.Event(), threading.Event()
    result = []
    def revoke():
        started.set()
        result.append(registry.revoke_gateway_netting_policy(policy))
        completed.set()
    with pair.scope() as check:
        thread = threading.Thread(target=revoke)
        thread.start()
        assert started.wait(1)
        assert not completed.wait(0.05)
        check()
    thread.join(1)
    assert completed.is_set() and result == [True]
    with pytest.raises(ServiceDispatchAdmissionError, match='SCOPE_DENIED'):
        with pair.scope():
            pass


def test_same_thread_policy_revocation_is_seen_by_final_recheck(env):
    registry, policy, *_ = env
    registry.configure_gateway_netting_policies(policies=(policy,))
    pair = bind(env)
    with pytest.raises(ServiceDispatchAdmissionError, match='SCOPE_DENIED'):
        with pair.scope():
            assert registry.revoke_gateway_netting_policy(policy)


def test_expiry_inside_scope_fails_final_check(env):
    registry, policy, *_, now = env
    registry.configure_gateway_netting_policies(policies=(policy,))
    pair = bind(env)
    with pytest.raises(ServiceDispatchAdmissionError, match='NOT_REGISTERED'):
        with pair.scope():
            now[0] = 110


@pytest.mark.parametrize('field,value', [('account_ref', 'other'), ('execution_domain_ref', 'other')])
def test_policy_for_different_account_or_domain_does_not_enable_pair(env, field, value):
    registry, policy, *_ = env
    registry.configure_gateway_netting_policies(policies=(replace(policy, **{field: value}),))
    with pytest.raises(ServiceDispatchAdmissionError, match='SCOPE_DENIED'):
        bind(env)


def test_nested_snapshot_fails_closed_and_releases_admissions(env):
    registry, policy, *_ = env
    registry.configure_gateway_netting_policies(policies=(policy,))
    pair = bind(env)
    with pair.scope():
        with pytest.raises(ServiceDispatchAdmissionError):
            with pair.scope():
                pass
    with pair.scope() as check:
        check()


def test_lease_expiry_closes_pair(env):
    registry, policy, *_, now = env
    registry.configure_gateway_netting_policies(policies=(policy,))
    pair = bind(env)
    now[0] = 110
    with pytest.raises(ServiceDispatchAdmissionError):
        with pair.scope():
            pass


def test_thread_binding_and_closed_admission(env):
    registry, policy, admissions, *_ = env
    registry.configure_gateway_netting_policies(policies=(policy,))
    pair = bind(env)
    failures = []
    def use_on_other_thread():
        try:
            with pair.scope():
                pass
        except ServiceDispatchAdmissionError as error:
            failures.append(str(error))
    thread = threading.Thread(target=use_on_other_thread)
    thread.start()
    thread.join()
    assert failures == ['WRONG_OWNER_THREAD']
    admissions[1].close()
    with pytest.raises(ServiceDispatchAdmissionError, match='ADMISSION_CLOSED'):
        with pair.scope():
            pass


@pytest.mark.skipif(not hasattr(os, 'fork'), reason='requires fork')
def test_fork_cannot_use_pair(env):
    registry, policy, *_ = env
    registry.configure_gateway_netting_policies(policies=(policy,))
    pair = bind(env)
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(read_fd)
        try:
            with pair.scope():
                result = 'unexpected'
        except ServiceDispatchAdmissionError as error:
            result = str(error)
        os.write(write_fd, result.encode())
        os._exit(0)
    os.close(write_fd)
    try:
        assert os.read(read_fd, 100) == b'WRONG_PROCESS'
        _, status = os.waitpid(pid, 0)
        assert os.waitstatus_to_exitcode(status) == 0
    finally:
        os.close(read_fd)
