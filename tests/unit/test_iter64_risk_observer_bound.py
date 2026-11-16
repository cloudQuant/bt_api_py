"""Authenticated observer local contracts; synthetic responses and no service I/O."""
from dataclasses import replace
from types import SimpleNamespace
import threading
import os

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_py.gateway_account_observer_client import GatewayAccountObserverClient, GatewayAccountObserverRoute
from bt_api_py.service_runtime_thread import BoundCallObservation, BoundCallResult, BoundCallRoute, _BoundBindingToken
from tests.unit.test_iter64_risk_service_gateway import account_event

IDENTITY = WireIdentity(WireRole.SERVICE, 'risk', 'risk-actor')
PEER = WireIdentity(WireRole.SERVICE, 'gateway', 'gateway-actor')
SCOPE = WireScope('domain', account_ref='account-a')
ROUTE = GatewayAccountObserverRoute('upstream.account', 'service', 'instance', 'scope', SCOPE, IDENTITY)
OPS = frozenset({'account.events.read_after', 'account.events.ack', 'account.events.progress'})
EPOCH = 'b' * 64


@pytest.fixture
def client():
    c = object.__new__(GatewayAccountObserverClient)
    c._pid, c._thread, c._lock = os.getpid(), threading.current_thread(), threading.Lock()
    c._route, c._bound_route = ROUTE, BoundCallRoute(IDENTITY, SCOPE, 'scope', 'service', 'instance', PEER)
    c._expected_fingerprint, c._consumer_ref, c._read_binding = 'a' * 64, 'consumer', None
    c._registration = lambda _: ('registration', 'service-epoch')
    calls = []
    def call(_mount, operation, arguments, **kwargs):
        calls.append((operation, arguments, kwargs))
        observation = BoundCallObservation(IDENTITY, 'run', SCOPE, 'registration', 'service-epoch',
            ROUTE.mount, ROUTE.service_id, ROUTE.instance_id, ROUTE.scope_ref, OPS, PEER,
            'a' * 64, 1000.0, True, True, operation)
        progress = {'schema_version': 1, 'account_scope': 'account-a', 'consumer_ref': 'consumer',
                    'epoch': EPOCH, 'offered_sequence': 3, 'acknowledged_sequence': 0}
        if operation.endswith('read_after'):
            events = []
            for seq in (1, 2, 3):
                item = account_event(seq)
                wire = item.event.to_mapping()
                wire['strategy_scope'] = item.strategy_scope
                events.append(wire)
            raw = {'status': 'OK', 'page': dict(progress, events=events, has_more=False)}
        else:
            progress['acknowledged_sequence'] = arguments.get('sequence', 0)
            raw = {'status': 'OK', 'progress': progress}
        token = _BoundBindingToken(object(), object(), object(), object(), (), (), observation)
        return BoundCallResult(raw, observation, observation, token)
    c._runtime = SimpleNamespace(call_bound=call)
    return c, calls


def test_partial_ack_is_bound_to_actual_read_and_upper_limit(client):
    c, calls = client
    read = c.read_after()
    assert read.outcome == 'OBSERVED' and read._read_token is not None
    assert c.acknowledge(4, expected_epoch=EPOCH, read_token=read._read_token).code == 'READ_BINDING_REQUIRED'
    assert c.acknowledge(1, expected_epoch=EPOCH, read_token=object()).code == 'READ_BINDING_REQUIRED'
    ack = c.acknowledge(2, expected_epoch=EPOCH, read_token=read._read_token)
    assert ack.outcome == 'OBSERVED'
    assert calls[-1][2]['expected_binding'].operation == 'account.events.read_after'
    assert type(calls[-1][2]['_expected_token']) is _BoundBindingToken
    assert c.acknowledge(2, expected_epoch=EPOCH, read_token=read._read_token).code == 'READ_BINDING_REQUIRED'


@pytest.mark.parametrize('field,value', [('peer', IDENTITY), ('fingerprint', 'c'*64),
    ('registration_id', 'new'), ('service_epoch', 'new'), ('operation', 'wrong')])
def test_changed_observation_never_issues_read_token(client, field, value):
    c, _ = client
    original = c._runtime.call_bound
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        bad = replace(result.before, **{field: value})
        return replace(result, before=bad, after=bad)
    c._runtime.call_bound = changed
    assert c.read_after().outcome == 'UNKNOWN'
    assert c.acknowledge(1, expected_epoch=EPOCH, read_token=object()).code == 'READ_BINDING_REQUIRED'


def test_changed_registration_after_call_and_runtime_swap_fail(client):
    c, calls = client
    original = c._runtime.call_bound
    def swap(*args, **kwargs):
        result = original(*args, **kwargs)
        c._registration = lambda _: ('other', 'service-epoch')
        return result
    c._runtime.call_bound = swap
    assert c.read_after().outcome == 'UNKNOWN'
    c._registration = lambda _: ('registration', 'service-epoch')
    c._runtime.call_bound = original
    read = c.read_after()
    c._runtime = SimpleNamespace(call_bound=lambda *a, **k: pytest.fail('stale runtime used'))
    assert c.acknowledge(1, expected_epoch=EPOCH, read_token=read._read_token).code == 'READ_BINDING_REQUIRED'


def test_timeout_or_missing_owner_token_never_acknowledges(client):
    c, calls = client
    original = c._runtime.call_bound
    c._runtime.call_bound = lambda *a, **k: (_ for _ in ()).throw(TimeoutError())
    assert c.read_after().outcome != 'OBSERVED'
    assert c.acknowledge(1, expected_epoch=EPOCH, read_token=object()).code == 'READ_BINDING_REQUIRED'
    c._runtime.call_bound = lambda *a, **k: replace(original(*a, **k), _binding_token=None)
    assert c.read_after().outcome == 'UNKNOWN'
    assert len(calls) == 1


def test_gap_limits_ack_to_contiguous_prefix(client):
    c, calls = client
    original = c._runtime.call_bound
    def with_gap(*args, **kwargs):
        result = original(*args, **kwargs)
        page = result.result['page']
        page['events'] = [page['events'][0], page['events'][2]]
        return result
    c._runtime.call_bound = with_gap
    read = c.read_after()
    assert read.outcome == 'OBSERVED'
    assert c.acknowledge(2, expected_epoch=EPOCH, read_token=read._read_token).code == 'READ_BINDING_REQUIRED'
    c._runtime.call_bound = original
    assert c.acknowledge(1, expected_epoch=EPOCH, read_token=read._read_token).outcome == 'OBSERVED'


def test_ack_timeout_consumes_token_and_cannot_retry(client):
    c, calls = client
    read = c.read_after()
    def timeout(*args, **kwargs):
        raise TimeoutError()
    c._runtime.call_bound = timeout
    assert c.acknowledge(1, expected_epoch=EPOCH, read_token=read._read_token).outcome == 'UNKNOWN'
    c._runtime.call_bound = lambda *a, **k: pytest.fail('ambiguous ACK retried')
    assert c.acknowledge(1, expected_epoch=EPOCH, read_token=read._read_token).code == 'READ_BINDING_REQUIRED'


def test_bad_epoch_and_empty_read_cannot_ack(client):
    c, calls = client
    original = c._runtime.call_bound
    read = c.read_after()
    assert c.acknowledge(1, expected_epoch='c'*64, read_token=read._read_token).code == 'READ_BINDING_REQUIRED'
    def empty(*args, **kwargs):
        result = original(*args, **kwargs)
        result.result['page']['events'] = []
        return result
    c._runtime.call_bound = empty
    empty_page = c.read_after()
    assert empty_page.outcome == 'OBSERVED' and empty_page._read_token is None
    assert c.acknowledge(1, expected_epoch=EPOCH, read_token=read._read_token).code == 'READ_BINDING_REQUIRED'


def test_real_owner_rejects_swapped_binding_and_runtime_before_ack(monkeypatch):
    from bt_api_py import service_runtime_thread as thread
    from tests.unit.test_iter64_cancel_ack_binding_pure import raw as raw_fixture
    owner = raw_fixture.__wrapped__(monkeypatch)
    owner._bindings[0] = replace(owner._bindings[0], operations=frozenset({'account.events.read_after', 'account.events.ack'}))
    route = thread.BoundCallRoute(owner.identity, owner._bindings[0].scope, 'scope', 'service', 'instance', PEER)
    def call(operation, expected=None, token=None):
        return thread._BoundCall('upstream.account', operation, {}, {'account_ref': 'account',
            'portfolio_ref': None, 'scope_ref': 'scope', 'max_reply_message_bytes': None},
            route, expected, token)
    read = call('account.events.read_after')
    proof = thread._finish_bound_call(owner, read, thread._execute_bound_call(owner, read))
    owner.sent.clear()
    owner._bindings[0] = replace(owner._bindings[0])
    with pytest.raises(thread.ThreadedRuntimeError, match='BOUND_ROUTE_UNAVAILABLE'):
        thread._execute_bound_call(owner, call('account.events.ack', proof.before, proof._binding_token))
    assert owner.sent == []
    other = raw_fixture.__wrapped__(monkeypatch)
    other._bindings[0] = replace(other._bindings[0], operations=frozenset({'account.events.read_after', 'account.events.ack'}))
    with pytest.raises(thread.ThreadedRuntimeError, match='BOUND_ROUTE_UNAVAILABLE'):
        thread._execute_bound_call(other, call('account.events.ack', proof.before, proof._binding_token))
    assert other.sent == []


def test_bound_read_failure_does_not_apply_or_ack(client):
    from tests.unit.test_iter64_risk_service_gateway import Engine, pump
    c, calls = client
    original = c._runtime.call_bound
    def bad_peer(mount, operation, args, **kwargs):
        result = original(mount, operation, args, **kwargs)
        if operation.endswith('read_after'):
            wrong = replace(result.before, peer=IDENTITY)
            return replace(result, before=wrong, after=wrong)
        return result
    c._runtime.call_bound = bad_peer
    engine = Engine()
    value = pump(engine, c)
    assert value.poll() == 0
    assert engine.applied == []
    assert all(operation != 'account.events.ack' for operation, _, _ in calls)
    assert value.verified_epoch is None
