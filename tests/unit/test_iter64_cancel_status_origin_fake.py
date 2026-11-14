"""Gateway origin checks with fake owner and fake admission, no real registry."""
from contextlib import contextmanager
import hashlib
import json
from types import SimpleNamespace

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_py.gateway_direct_service import GatewayControlGrant, GatewayDirectServiceBinding
from bt_api_py.service_registry import ServiceCallContext, ServiceDispatchAdmission

OPERATION = 'direct.control.cancel_scope_status'


def origin(principal, actor):
    return 'risk:' + hashlib.sha256(json.dumps([principal, actor], separators=(',', ':')).encode()).hexdigest()


@pytest.fixture
def call_status(monkeypatch):
    admission = object.__new__(ServiceDispatchAdmission)
    monkeypatch.setattr(ServiceDispatchAdmission, 'matches_operation', lambda self, operation: True)
    monkeypatch.setattr(ServiceDispatchAdmission, 'matches_context', lambda self, context: True)
    monkeypatch.setattr(ServiceDispatchAdmission, 'matches', lambda self, arguments: True)
    monkeypatch.setattr(ServiceDispatchAdmission, 'close', lambda self: None)
    @contextmanager
    def scope(self):
        yield lambda: None
    monkeypatch.setattr(ServiceDispatchAdmission, 'scope', scope)
    row = {'scope_kind': 'account', 'scope_value': '', 'origin': origin('risk-a', 'actor-a')}
    target = {'reservation_id': 'r-1', 'strategy': 's', 'request_key': 'k', 'state': 'QUEUED', 'cancel': None}
    counts = {'QUEUED': 1, 'SENT': 0, 'NOT_SENT': 0, 'UNKNOWN': 0, 'TERMINAL': 0}
    control = SimpleNamespace(cancel_scope_status=lambda operation_id, **kw: (row, (target,), counts, 1))
    class Owner:
        def submit_service_call(self, callback):
            return SimpleNamespace(result=lambda timeout_seconds: callback())
    binding = object.__new__(GatewayDirectServiceBinding)
    binding._control = control
    binding._journal = SimpleNamespace(account_scope='account')
    binding._owner = Owner()
    binding._control_grants = {
        'risk-a': GatewayControlGrant('actor-a', True, frozenset(), frozenset({OPERATION}), kind='risk'),
        'risk-b': GatewayControlGrant('actor-b', True, frozenset(), frozenset({OPERATION}), kind='risk'),
    }
    def call(principal, actor):
        # Model a valid current grant for the tested identity, including a
        # later registration with a changed actor or principal.
        binding._control_grants[principal] = GatewayControlGrant(
            actor, True, frozenset(), frozenset({OPERATION}), kind='risk')
        identity = WireIdentity(WireRole.APPLICATION_CLIENT, principal, actor)
        context = ServiceCallContext(identity, WireScope('domain', account_ref='account'),
                                     'registration', principal, dispatch_admission=admission)
        args = {'operation_id': 'op', 'after': '', 'limit': 10}
        return binding._handle_control(OPERATION, context=context, arguments=args)
    return call


def test_authenticated_creator_can_read_bound_page(call_status):
    reply = call_status('risk-a', 'actor-a')
    assert reply['status'] == 'OBSERVED'
    assert reply['origin'] == origin('risk-a', 'actor-a')
    assert (reply['after'], reply['limit'], reply['remaining_at_cursor']) == ('', 10, 1)
    assert len(reply['targets']) == 1


def test_different_authenticated_actor_same_scope_receives_no_target(call_status):
    assert call_status('risk-b', 'actor-b') == {'status': 'REQUEST_REJECTED', 'code': 'SCOPE_DENIED'}


@pytest.mark.parametrize('principal,actor', [
    ('risk-a', 'actor-b'),  # same principal, a different currently granted actor
    ('risk-b', 'actor-a'),  # same actor, a different currently granted principal
])
def test_either_identity_change_rejects_the_stored_origin(call_status, principal, actor):
    assert call_status(principal, actor) == {'status': 'REQUEST_REJECTED', 'code': 'SCOPE_DENIED'}


def test_old_control_tuple_after_one_cancel_is_unknown_without_automatic_retry(monkeypatch):
    """Fake owner wraps the real handler's post-dispatch unpacking error."""
    from bt_api_py.gateway_direct_owner import GatewayOwnerServiceCallError

    admission = object.__new__(ServiceDispatchAdmission)
    admission._binding = SimpleNamespace(service_epoch='epoch')
    monkeypatch.setattr(ServiceDispatchAdmission, 'matches_operation', lambda self, operation: True)
    monkeypatch.setattr(ServiceDispatchAdmission, 'matches_context', lambda self, context: True)
    monkeypatch.setattr(ServiceDispatchAdmission, 'matches', lambda self, arguments: True)
    monkeypatch.setattr(ServiceDispatchAdmission, 'close', lambda self: None)
    @contextmanager
    def scope(self):
        yield lambda: None
    monkeypatch.setattr(ServiceDispatchAdmission, 'scope', scope)

    calls = []
    row = {'scope_kind': 'account', 'scope_value': '', 'origin': origin('risk-a', 'actor-a'),
           'cause_id': 'cancel-scope:op', 'mode': 'CANCEL_ONLY', 'version': 1}
    target = {'reservation_id': 'r-1', 'state': 'PENDING'}
    class LegacyControl:
        def cancel_scope(self, operation_id, **kwargs):
            calls.append('create')
            return row
        def reconcile_targets(self, cause_id, **kwargs):
            return None
        def targets(self, cause_id, **kwargs):
            return [target]
        def claim_target(self, cause_id, reservation_id, **kwargs):
            return target, object()
        def dispatch_claimed_cancel(self, *args, **kwargs):
            calls.append('dispatch')
        def cancel_scope_status(self, operation_id, **kwargs):
            calls.append('read_legacy_tuple')
            return row, (target,), {'QUEUED': 0}  # older three-value contract

    class FakeOwner:
        supports_cancel = True
        _cancel_dispatcher = object()
        def submit_service_call(self, callback):
            def result(timeout_seconds):
                try:
                    return callback()
                except BaseException:
                    # Mirrors the real owner's SERVICE_CALL_FAILED boundary.
                    raise GatewayOwnerServiceCallError('SERVICE_CALL_FAILED') from None
            return SimpleNamespace(result=result)

    binding = object.__new__(GatewayDirectServiceBinding)
    binding._control = LegacyControl()
    binding._journal = SimpleNamespace(account_scope='account', _schema_version=17)
    binding._owner, binding._cancel_journal = FakeOwner(), object()
    operation = 'direct.control.cancel_scope'
    binding._control_grants = {'risk-a': GatewayControlGrant(
        'actor-a', True, frozenset(), frozenset({operation}), kind='risk')}
    context = ServiceCallContext(WireIdentity(WireRole.APPLICATION_CLIENT, 'risk-a', 'actor-a'),
        WireScope('domain', account_ref='account'), 'registration', 'risk-a', dispatch_admission=admission)
    reply = binding.handle(operation, context=context, arguments={'operation_id': 'op', 'limit': 1})
    assert reply == {'status': 'UNKNOWN', 'cause_id': ''}
    assert calls == ['create', 'dispatch', 'read_legacy_tuple']
