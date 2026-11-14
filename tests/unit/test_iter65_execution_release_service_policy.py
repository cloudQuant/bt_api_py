"""Real Registry, synthetic service registration; this never claims a release."""
from dataclasses import replace
from contextlib import contextmanager
import sqlite3
import threading
import time

import pytest

from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
import bt_api_py.service_registry as registry_module
from bt_api_py.execution_action_grant import ExecutionActionGrant
from bt_api_py.service_registry import (PrincipalGrant, ResourceGrant, ServiceCapability,
                                        ServiceDispatchAdmissionError, ServiceRegistry)
from tests.unit.test_service_registry import _next_request, _register


PORT = 'service.execution_delegate'
OP = 'execution.child.release_policy_check'
SERVICE = WireIdentity(WireRole.SERVICE, 'execution-principal', 'execution')
SCOPE = WireScope('domain', account_ref='account', strategy_ref='strategy')
KEY = MarketInstrumentKey('ctp', 'SHFE', 'simnow', 'future', 'rb', 'rb')


def _policy():
    return ExecutionActionGrant('execution', SERVICE, 'account', 'strategy', 'domain',
        'revision', frozenset({KEY}), frozenset({'plan'}), frozenset({'release_child'}))


def _arguments():
    return {'schema_version': 1, 'operation_id': 'op', 'parent_ref': 'parent', 'child_id': 'child',
            'account_ref': 'account', 'strategy_ref': 'strategy', 'execution_domain_ref': 'domain',
            'instrument': KEY.to_dict(), 'plan_version': 'plan', 'grant_revision': 'revision'}


def _registry(*, configured=True, handler=registry_module._execution_release_local_policy_handler,
              lease=10):
    capability = ServiceCapability('execution', PORT, frozenset({OP}))
    resource = ResourceGrant('execution', PORT, 'scope', SCOPE, frozenset({OP}))
    registry = ServiceRegistry(':memory:', service_id='execution-service',
        capabilities=(capability,), grants={SERVICE.principal_ref: (PrincipalGrant(SERVICE, (resource,)),)},
        policy_version='policy', requirements_version='req', lease_seconds=lease)
    if configured:
        registry.configure_execution_actions(grants=(_policy(),))
    registry.register_handler('execution', OP, handler)
    return registry


def _register_service(registry):
    return _register(registry, principal=SERVICE.principal_ref, identity=SERVICE, scope=SCOPE,
        key='service-key', instance_id='execution', port=PORT, scope_ref='scope', operations=(OP,))


def _call(registry, registration, *, arguments=None, identity=SERVICE, scope=SCOPE, principal=None):
    return registry.handle(principal or SERVICE.principal_ref, _next_request(OP, {
        'registration_id': registration['registration_id'], 'service_epoch': registration['service_epoch'],
        'instance_id': 'execution', 'arguments': _arguments() if arguments is None else arguments},
        identity=identity, scope=scope))


def test_registered_service_checks_local_policy_without_strategy_session():
    registry = _registry()
    try:
        registration = _register_service(registry)
        assert registration['ok'], registration
        # No strategy identity or strategy registration has ever existed.
        result = _call(registry, registration['result'])
        assert result['ok'], result
        assert result['result'] == {'schema_version': 1, 'status': 'LOCAL_POLICY_ONLY'}
    finally:
        registry.close()


def test_default_deny_and_wrong_identity_or_scope():
    registry = _registry(configured=False)
    try:
        assert not _register_service(registry)['ok']
        registry.configure_execution_actions(grants=(_policy(),))
        registration = _register_service(registry)
        assert registration['ok'], registration
        session = registration['result']
        assert not _call(registry, session, principal='other')['ok']
        assert not _call(registry, session, identity=replace(SERVICE, actor_ref='other'))['ok']
        assert not _call(registry, session, scope=replace(SCOPE, account_ref='other'))['ok']
        assert not _call(registry, session, scope=replace(SCOPE, strategy_ref='other'))['ok']
        assert not _call(registry, session, scope=replace(SCOPE, execution_domain_ref='other'))['ok']
    finally:
        registry.close()


@pytest.mark.parametrize('change', [
    {'schema_version': 2}, {'account_ref': 'other'}, {'strategy_ref': 'other'},
    {'execution_domain_ref': 'other'}, {'grant_revision': 'other'}, {'plan_version': 'other'},
    {'instrument': MarketInstrumentKey('ctp', 'SHFE', 'simnow', 'future', 'hc', 'hc').to_dict()},
    {'parent_ref': ''}, {'unexpected': True},
])
def test_wire_facts_must_match_exact_policy(change):
    registry = _registry()
    try:
        session = _register_service(registry)['result']
        result = _call(registry, session, arguments={**_arguments(), **change})
        assert not result['ok'], result
    finally:
        registry.close()


def test_untrusted_handler_never_runs_and_cannot_return_success():
    calls = []
    registry = _registry(handler=lambda *_: calls.append(True) or {'status': 'CLAIMED'})
    try:
        session = _register_service(registry)['result']
        result = _call(registry, session)
        assert not result['ok'] and result['code'] == 'CAPABILITY_UNAVAILABLE'
        assert not calls
    finally:
        registry.close()


def test_policy_revocation_and_expired_service_lease_fail_closed():
    registry = _registry()
    try:
        session = _register_service(registry)['result']
        assert _call(registry, session)['ok']
        grant = registry._execution_release_policy('execution', SERVICE, SCOPE)
        assert registry.revoke_execution_action(grant)
        assert not _call(registry, session)['ok']
    finally:
        registry.close()
    registry = _registry(lease=.05)
    try:
        session = _register_service(registry)['result']
        time.sleep(.07)
        assert not _call(registry, session)['ok']
    finally:
        registry.close()


def test_release_scope_cross_thread_exit_does_not_unwind_owner(monkeypatch):
    original = registry_module.ServiceDispatchAdmission.release_action_scope
    errors = []
    def checking_scope(admission, context, arguments, action):
        manager = original(admission, context, arguments, action)
        class Wrapper:
            def __enter__(self):
                check = manager.__enter__()
                def foreign():
                    try:
                        manager.__exit__(None, None, None)
                    except ServiceDispatchAdmissionError as error:
                        errors.append(error.code)
                worker = threading.Thread(target=foreign)
                worker.start()
                worker.join(2)
                assert not worker.is_alive()
                check()
                return check
            def __exit__(self, *args):
                return manager.__exit__(*args)
        return Wrapper()
    monkeypatch.setattr(registry_module.ServiceDispatchAdmission, 'release_action_scope', checking_scope)
    registry = _registry()
    try:
        session = _register_service(registry)['result']
        assert _call(registry, session)['ok']
        assert errors == ['WRONG_OWNER_THREAD']
        assert _call(registry, session)['ok']
    finally:
        registry.close()


def test_revoke_and_replace_after_scope_cannot_publish_old_policy_result(monkeypatch):
    original = registry_module.ServiceDispatchAdmission.release_action_scope
    def replacing_scope(admission, context, arguments, action):
        manager = original(admission, context, arguments, action)
        class Wrapper:
            def __enter__(self):
                return manager.__enter__()
            def __exit__(self, *args):
                result = manager.__exit__(*args)
                registry = admission._registry
                old = action._grant
                other = MarketInstrumentKey('ctp', 'SHFE', 'simnow', 'future', 'hc', 'hc')
                assert registry.revoke_execution_action(old)
                registry.configure_execution_actions(grants=(replace(old, instruments=frozenset({other})),))
                return result
        return Wrapper()
    monkeypatch.setattr(registry_module.ServiceDispatchAdmission, 'release_action_scope', replacing_scope)
    registry = _registry()
    try:
        session = _register_service(registry)['result']
        result = _call(registry, session)
        assert not result['ok'] and result['code'] == 'HANDLER_FAILED'
    finally:
        registry.close()


def test_original_context_and_wire_arguments_cannot_be_replaced(monkeypatch):
    original = registry_module.ServiceDispatchAdmission.release_action_scope
    checked = []
    def checking_scope(admission, context, arguments, action):
        for other_context, other_arguments in ((replace(context), arguments), (context, dict(arguments))):
            with pytest.raises(ServiceDispatchAdmissionError, match='SCOPE_DENIED'):
                with original(admission, other_context, other_arguments, action):
                    pass
        with pytest.raises(ServiceDispatchAdmissionError, match='OPERATION_MISMATCH'):
            with admission.scope():
                pass
        checked.append(True)
        return original(admission, context, arguments, action)
    monkeypatch.setattr(registry_module.ServiceDispatchAdmission, 'release_action_scope', checking_scope)
    registry = _registry()
    try:
        session = _register_service(registry)['result']
        assert _call(registry, session)['ok']
        assert checked == [True]
    finally:
        registry.close()


def test_expiry_within_scope_cannot_return_policy_result(monkeypatch):
    original = registry_module.ServiceDispatchAdmission.release_action_scope
    def slow_scope(admission, context, arguments, action):
        manager = original(admission, context, arguments, action)
        class Wrapper:
            def __enter__(self):
                check = manager.__enter__()
                time.sleep(.08)
                return check
            def __exit__(self, *args):
                return manager.__exit__(*args)
        return Wrapper()
    monkeypatch.setattr(registry_module.ServiceDispatchAdmission, 'release_action_scope', slow_scope)
    registry = _registry(lease=.05)
    try:
        session = _register_service(registry)['result']
        assert not _call(registry, session)['ok']
    finally:
        registry.close()


def _renew(registry, session):
    return registry.handle(SERVICE.principal_ref, _next_request('service.renew', {
        'registration_id': session['registration_id'], 'service_epoch': session['service_epoch'],
    }, identity=SERVICE, scope=SCOPE))


def test_wall_clock_rollback_after_real_ttl_does_not_restore_service_lease(monkeypatch):
    registry = _registry(lease=.05)
    try:
        session = _register_service(registry)['result']
        issued = time.time()
        time.sleep(.08)
        rolled_back = lambda: issued - 30
        monkeypatch.setattr(registry_module, '_INTAKE_REALTIME', rolled_back)
        registry._clock = rolled_back
        assert not _call(registry, session)['ok']
        assert not _renew(registry, session)['ok']
        assert not _register_service(registry)['ok']
    finally:
        registry.close()


def test_repeat_registration_does_not_extend_and_missing_deadline_fails_closed():
    registry = _registry(lease=1)
    try:
        session = _register_service(registry)['result']
        old = registry._execution_release_monotonic_leases[session['registration_id']]
        same = _register_service(registry)
        assert same['ok'] and same['result']['registration_id'] == session['registration_id']
        assert registry._execution_release_monotonic_leases[session['registration_id']] is old
        registry._execution_release_monotonic_leases.pop(session['registration_id'])
        assert not _call(registry, session)['ok']
        assert not _renew(registry, session)['ok']
        assert not _register_service(registry)['ok']
    finally:
        registry.close()


def test_timely_renew_extends_only_after_commit_and_unregister_discards_deadline():
    registry = _registry(lease=.4)
    try:
        session = _register_service(registry)['result']
        old = registry._execution_release_monotonic_leases[session['registration_id']]
        time.sleep(.22)
        assert _renew(registry, session)['ok']
        new = registry._execution_release_monotonic_leases[session['registration_id']]
        assert new[3] > old[3]
        time.sleep(.22)
        assert _call(registry, session)['ok']
        result = registry.handle(SERVICE.principal_ref, _next_request('service.unregister', {
            'registration_id': session['registration_id'], 'service_epoch': session['service_epoch'],
        }, identity=SERVICE, scope=SCOPE))
        assert result['ok']
        assert session['registration_id'] not in registry._execution_release_monotonic_leases
        assert not _call(registry, session)['ok']
    finally:
        registry.close()


def test_committed_registration_with_lost_completion_cannot_acquire_a_deadline(monkeypatch):
    registry = _registry()
    original = registry._transaction
    @contextmanager
    def commit_then_raise(*args, **kwargs):
        with original(*args, **kwargs):
            yield
        raise sqlite3.OperationalError('synthetic lost commit acknowledgement')
    try:
        monkeypatch.setattr(registry, '_transaction', commit_then_raise)
        assert not _register_service(registry)['ok']
        monkeypatch.setattr(registry, '_transaction', original)
        assert not _register_service(registry)['ok']
        assert not registry._execution_release_monotonic_leases
    finally:
        registry.close()


def test_committed_renewal_with_lost_completion_cannot_extend_deadline(monkeypatch):
    registry = _registry()
    original = registry._transaction
    @contextmanager
    def commit_then_raise(*args, **kwargs):
        with original(*args, **kwargs):
            yield
        raise sqlite3.OperationalError('synthetic lost commit acknowledgement')
    try:
        session = _register_service(registry)['result']
        old = registry._execution_release_monotonic_leases[session['registration_id']]
        time.sleep(.01)
        monkeypatch.setattr(registry, '_transaction', commit_then_raise)
        assert not _renew(registry, session)['ok']
        monkeypatch.setattr(registry, '_transaction', original)
        assert registry._execution_release_monotonic_leases[session['registration_id']] is old
        assert not _call(registry, session)['ok']
        assert not _renew(registry, session)['ok']
    finally:
        registry.close()


@pytest.mark.parametrize('phase', ['registration', 'renewal'])
def test_slow_commit_cannot_create_or_revive_an_expired_lease(monkeypatch, phase):
    registry = _registry(lease=.06)
    original = registry._transaction
    session = None
    if phase == 'renewal':
        session = _register_service(registry)['result']
    @contextmanager
    def commit_then_delay(*args, **kwargs):
        with original(*args, **kwargs):
            yield
        time.sleep(.09)
    try:
        monkeypatch.setattr(registry, '_transaction', commit_then_delay)
        result = _register_service(registry) if session is None else _renew(registry, session)
        assert not result['ok']
        monkeypatch.setattr(registry, '_transaction', original)
        if session is None:
            assert not registry._execution_release_monotonic_leases
        else:
            assert not _call(registry, session)['ok']
            assert not _renew(registry, session)['ok']
    finally:
        registry.close()


def test_unsupported_sleep_clock_keeps_new_port_unavailable(monkeypatch):
    registry = _registry()
    try:
        monkeypatch.setattr(registry_module, '_RELEASE_SLEEP_CLOCK_ID', None)
        assert not _register_service(registry)['ok']
    finally:
        registry.close()


def test_sleep_clock_error_keeps_new_port_unavailable(monkeypatch):
    registry = _registry()
    try:
        monkeypatch.setattr(registry_module, '_RELEASE_SLEEP_CLOCK_ID', -1)
        assert not _register_service(registry)['ok']
    finally:
        registry.close()


def test_renew_and_unregister_are_serialized_and_cannot_revive_session():
    registry = _registry(lease=5)
    start = threading.Event()
    responses, errors = [], []
    try:
        session = _register_service(registry)['result']
        def renew():
            try:
                assert start.wait(2)
                responses.append(('renew', _renew(registry, session)))
            except BaseException as error:
                errors.append(error)
        def unregister():
            try:
                assert start.wait(2)
                reply = registry.handle(SERVICE.principal_ref, _next_request('service.unregister', {
                    'registration_id': session['registration_id'], 'service_epoch': session['service_epoch'],
                }, identity=SERVICE, scope=SCOPE))
                responses.append(('unregister', reply))
            except BaseException as error:
                errors.append(error)
        workers = [threading.Thread(target=renew), threading.Thread(target=unregister)]
        for worker in workers:
            worker.start()
        start.set()
        for worker in workers:
            worker.join(3)
        assert not errors and all(not worker.is_alive() for worker in workers)
        assert dict(responses)['unregister']['ok']
        assert session['registration_id'] not in registry._execution_release_monotonic_leases
        assert not _call(registry, session)['ok']
        assert not _renew(registry, session)['ok']
    finally:
        start.set()
        registry.close()


def test_release_deadline_cannot_be_inherited_by_another_process_identity(monkeypatch):
    registry = _registry()
    try:
        session = _register_service(registry)['result']
        monkeypatch.setattr(registry, '_execution_action_pid', registry._execution_action_pid + 1)
        assert not _call(registry, session)['ok']
        assert not _renew(registry, session)['ok']
    finally:
        registry.close()


def test_capability_rejects_write_or_other_port_and_nonservice_scope():
    with pytest.raises(ValueError, match='invalid execution release capability'):
        ServiceRegistry(':memory:', service_id='execution-service',
            capabilities=(ServiceCapability('execution', PORT, frozenset({OP}), frozenset({OP})),),
            grants={}, policy_version='p', requirements_version='r')
    with pytest.raises(ValueError, match='requires its own port'):
        ServiceRegistry(':memory:', service_id='execution-service',
            capabilities=(ServiceCapability('execution', 'service.account_read', frozenset({OP})),),
            grants={}, policy_version='p', requirements_version='r')
    strategy = WireIdentity(WireRole.STRATEGY_CLIENT, 's', 'strategy')
    resource = ResourceGrant('execution', PORT, 'scope', SCOPE, frozenset({OP}))
    with pytest.raises(ValueError, match='exact service scope'):
        ServiceRegistry(':memory:', service_id='execution-service',
            capabilities=(ServiceCapability('execution', PORT, frozenset({OP})),),
            grants={'s': (PrincipalGrant(strategy, (resource,)),)}, policy_version='p', requirements_version='r')
