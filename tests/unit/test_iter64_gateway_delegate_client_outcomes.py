"""Delegate clients name a request that provably never left, and a Registry refusal (G64-08).

Synthetic runtime and host doubles; no Gateway process, account or network.
"""
from __future__ import annotations

from dataclasses import replace

import pytest
from bt_api_base.gateway_delegation_protocol import GatewayDelegatedCommand
from bt_api_base.gateway_direct_protocol import gateway_payload_digest
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py.gateway_account_observer_client import (
    GatewayAccountObserverClient,
    GatewayAccountObserverRoute,
)
from bt_api_py.gateway_delegate_actor_stream_client import GatewayDelegateActorStreamClient
from bt_api_py.gateway_delegate_actor_stream_service import ACTOR_STREAM_OPERATIONS
from bt_api_py.gateway_delegate_service_client import (
    GatewayDelegateRoute,
    GatewayDelegateServiceClient,
)
from bt_api_py.service_runtime import ServiceCallOutcomeUnknownError, ServiceRuntimeError
from bt_api_py.service_runtime_thread import (
    BindingSnapshot,
    RuntimeSnapshot,
    ThreadedRuntimeError,
    ThreadedServiceClientRuntime,
)
from tests.unit.test_iter63_gateway_api import order
from tests.unit.test_iter63_gateway_delegate_service_client import Host

NOT_STARTED = ('NOT_READY', 'QUEUE_FULL', 'TIMEOUT_BEFORE_START', 'CLOSED_BEFORE_START', 'WRONG_PROCESS',
               'OWNER_THREAD_REENTRY', 'INVALID_ARGUMENTS')
IDENTITY = WireIdentity(WireRole.SERVICE, 'risk-principal', 'risk')
SCOPE = WireScope('domain', account_ref='account', strategy_ref='strategy', run_id='run')
ROUTE = GatewayDelegateRoute('upstream.direct_write', 'gateway', 'instance', 'scope', SCOPE, IDENTITY)
OPERATIONS = frozenset({'direct.delegated_reserve', 'direct.delegated_dispatch', 'direct.delegate_actor_status',
                        'direct.delegate_actor_cancel'})


def _registry_refusal(code):
    error = ServiceRuntimeError(code)
    error.service_reply_code = code
    return error


def _local(code):
    # The same code raised locally: not the Gateway's answer, so nothing is proved by it.
    return ServiceRuntimeError(code)


class _Site:
    def __init__(self, monkeypatch, host=None):
        self.request = order()
        self.cmd = GatewayDelegatedCommand('account', 'strategy', 'domain', 'risk-principal', 'risk', 'risk', 'run',
            self.request.idempotency_key, gateway_payload_digest(self.request.to_dict()),
            MarketInstrumentKey('ctp', 'SHFE', 'simnow', 'future', 'rb', 'rb'), 'grant')
        self.runtime = ThreadedServiceClientRuntime(lambda: None)
        binding = BindingSnapshot('registration', 'epoch', True, ROUTE.mount, ROUTE.service_id, ROUTE.instance_id,
                                  ROUTE.scope_ref, SCOPE, OPERATIONS, True)
        snapshot = RuntimeSnapshot('running', True, True, None, (binding,), 0, 0, IDENTITY, SCOPE.run_id)
        monkeypatch.setattr(self.runtime, 'status', lambda: snapshot)
        self.calls, self.answers = [], []
        monkeypatch.setattr(self.runtime, 'call', self._call)
        self.host = Host() if host is None else host
        self.client = GatewayDelegateServiceClient(self.runtime, route=ROUTE, host=self.host)

    def _call(self, _mount, operation, arguments, **_kwargs):
        self.calls.append(operation)
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer(operation, arguments) if callable(answer) else answer

    def reserved(self):
        cmd = self.cmd
        return {'status': 'OBSERVED', 'schema_version': 1, 'account_scope': 'account', 'strategy_scope': 'strategy',
                'actor_principal': 'risk-principal', 'request_key': cmd.request_key, 'reservation_id': 'reservation',
                'digest': cmd.payload_digest, 'state': 'RESERVED', 'version': 1, 'owner_generation': 'owner',
                'reservation_generation': 'owner'}

    def queued(self):
        return {'status': 'QUEUED', 'request_key': self.cmd.request_key, 'reservation_id': 'reservation'}


@pytest.mark.parametrize('code', NOT_STARTED)
def test_reserve_that_never_started_is_named_not_started(monkeypatch, code):
    site = _Site(monkeypatch)
    site.answers.append(ThreadedRuntimeError(code))
    result = site.client.reserve(site.cmd, site.request)
    assert (result.outcome, result.code) == ('TRANSPORT_NOT_STARTED', code)


@pytest.mark.parametrize('error', [ThreadedRuntimeError('TIMEOUT_AFTER_START'),
                                   ThreadedRuntimeError('CALL_OUTCOME_UNKNOWN'), TimeoutError('lost'),
                                   ServiceCallOutcomeUnknownError('message', 'direct.delegated_reserve'),
                                   _local('STRATEGY_STOPPED')])
def test_reserve_whose_outcome_is_not_proved_stays_unknown(monkeypatch, error):
    site = _Site(monkeypatch)
    site.answers.append(error)
    result = site.client.reserve(site.cmd, site.request)
    assert (result.outcome, result.code) == ('UNKNOWN', 'RPC_OUTCOME_UNKNOWN')


@pytest.mark.parametrize('code', ['STRATEGY_STOPPED', 'DUPLICATE_ACTIVE_RUN', 'SCOPE_DENIED', 'NOT_REGISTERED'])
def test_registry_refusal_of_reserve_and_dispatch_is_a_denial(monkeypatch, code):
    site = _Site(monkeypatch)
    site.answers.append(_registry_refusal(code))
    result = site.client.reserve(site.cmd, site.request)
    assert (result.outcome, result.code) == ('REQUEST_DENIED', code)
    site.answers[:] = [site.reserved(), _registry_refusal(code)]
    assert site.client.reserve(site.cmd, site.request).outcome == 'OBSERVED'
    result = site.client.dispatch(site.cmd.request_key)
    assert (result.outcome, result.code) == ('REQUEST_DENIED', code)


@pytest.mark.parametrize('code', NOT_STARTED)
def test_dispatch_that_never_left_may_be_retried_with_the_same_identity(monkeypatch, code):
    site = _Site(monkeypatch)
    site.answers[:] = [site.reserved(), ThreadedRuntimeError(code), site.queued()]
    assert site.client.reserve(site.cmd, site.request).outcome == 'OBSERVED'
    first = site.client.dispatch(site.cmd.request_key)
    assert (first.outcome, first.code) == ('TRANSPORT_NOT_STARTED', code)
    marker = site.host.marker[site.cmd.request_key]
    second = site.client.dispatch(site.cmd.request_key)
    assert second.outcome == 'QUEUED'
    assert site.host.marker[site.cmd.request_key] == marker
    assert site.calls == ['direct.delegated_reserve', 'direct.delegated_dispatch', 'direct.delegated_dispatch']


def test_dispatch_retry_after_a_route_outage_is_allowed(monkeypatch):
    site = _Site(monkeypatch)
    site.answers[:] = [site.reserved(), site.queued()]
    assert site.client.reserve(site.cmd, site.request).outcome == 'OBSERVED'
    healthy = site.runtime.status
    monkeypatch.setattr(site.runtime, 'status', lambda: replace(healthy(), healthy=False))
    first = site.client.dispatch(site.cmd.request_key)
    assert (first.outcome, first.code) == ('TRANSPORT_NOT_STARTED', 'ROUTE_UNAVAILABLE')
    monkeypatch.setattr(site.runtime, 'status', healthy)
    assert site.client.dispatch(site.cmd.request_key).outcome == 'QUEUED'


def test_a_dispatch_that_may_have_left_is_never_sent_again(monkeypatch):
    site = _Site(monkeypatch)
    site.answers[:] = [site.reserved(), ThreadedRuntimeError('NOT_READY'), TimeoutError('lost')]
    assert site.client.reserve(site.cmd, site.request).outcome == 'OBSERVED'
    assert site.client.dispatch(site.cmd.request_key).outcome == 'TRANSPORT_NOT_STARTED'
    assert site.client.dispatch(site.cmd.request_key).outcome == 'UNKNOWN'
    again = site.client.dispatch(site.cmd.request_key)
    assert (again.outcome, again.code) == ('UNKNOWN', 'DISPATCH_ALREADY_ATTEMPTED')
    assert site.calls.count('direct.delegated_dispatch') == 2


def test_not_sent_proof_is_not_inherited_by_another_client(monkeypatch):
    # The proof that the attempt never left lives with the client that made it; a new
    # client (a restarted host) finds only the durable marker, which proves nothing.
    site = _Site(monkeypatch)
    site.answers[:] = [site.reserved(), ThreadedRuntimeError('QUEUE_FULL')]
    assert site.client.reserve(site.cmd, site.request).outcome == 'OBSERVED'
    assert site.client.dispatch(site.cmd.request_key).outcome == 'TRANSPORT_NOT_STARTED'
    restarted = _Site(monkeypatch, host=site.host)
    restarted.cmd = site.cmd
    result = restarted.client.dispatch(site.cmd.request_key)
    assert (result.outcome, result.code) == ('UNKNOWN', 'DISPATCH_ALREADY_ATTEMPTED')
    assert restarted.calls == []


def test_actor_reads_map_registry_refusals_but_an_actor_write_does_not(monkeypatch):
    site = _Site(monkeypatch)
    site.answers[:] = [site.reserved()]
    assert site.client.reserve(site.cmd, site.request).outcome == 'OBSERVED'
    site.answers[:] = [ThreadedRuntimeError('NOT_READY')]
    result = site.client.actor_status(site.cmd.request_key)
    assert (result.outcome, result.code) == ('TRANSPORT_NOT_STARTED', 'NOT_READY')
    site.answers[:] = [_registry_refusal('DUPLICATE_ACTIVE_RUN')]
    result = site.client.actor_status(site.cmd.request_key)
    assert (result.outcome, result.code) == ('REQUEST_DENIED', 'DUPLICATE_ACTIVE_RUN')
    # The Registry rechecks a delegate's cancel after the Gateway ran it: its refusal does not
    # prove that no cancel was stored, so it stays unknown. A cancel that never started is named.
    intent = site.host.load_delegate_intent(site.cmd.request_key)
    site.answers[:] = [_registry_refusal('DUPLICATE_ACTIVE_RUN'), ThreadedRuntimeError('QUEUE_FULL')]
    _, refused = site.client._actor_call('direct.delegate_actor_cancel', intent, {}, 5.0)
    assert (refused.outcome, refused.code) == ('UNKNOWN', 'RPC_OUTCOME_UNKNOWN')
    _, not_started = site.client._actor_call('direct.delegate_actor_cancel', intent, {}, 5.0)
    assert (not_started.outcome, not_started.code) == ('TRANSPORT_NOT_STARTED', 'QUEUE_FULL')


def _stream_runtime(monkeypatch, route, operations, identity):
    runtime = ThreadedServiceClientRuntime(lambda: None)
    binding = BindingSnapshot('registration', 'epoch', True, route.mount, route.service_id, route.instance_id,
                              route.scope_ref, route.scope, operations, True)
    snapshot = RuntimeSnapshot('running', True, True, None, (binding,), 0, 0, identity, route.scope.run_id)
    monkeypatch.setattr(runtime, 'status', lambda: snapshot)
    return runtime


@pytest.mark.parametrize('kind', ['actor_stream', 'account_observer'])
def test_stream_clients_name_a_read_that_never_started(monkeypatch, kind):
    if kind == 'actor_stream':
        runtime = _stream_runtime(monkeypatch, ROUTE, ACTOR_STREAM_OPERATIONS, IDENTITY)
        client = GatewayDelegateActorStreamClient(runtime, route=ROUTE, grant_revision='v1')
    else:
        identity = WireIdentity(WireRole.STRATEGY_CLIENT, 'principal', 'strategy')
        route = GatewayAccountObserverRoute('store.account', 'gateway', 'instance', 'scope', SCOPE, identity)
        runtime = _stream_runtime(monkeypatch, route, frozenset(
            {'account.events.read_after', 'account.events.progress', 'account.events.ack'}), identity)
        client = GatewayAccountObserverClient(runtime, route=route)
    answers = [ThreadedRuntimeError('NOT_READY'), _registry_refusal('SCOPE_DENIED'),
               ThreadedRuntimeError('TIMEOUT_AFTER_START')]

    def call(*_args, **_kwargs):
        raise answers.pop(0)
    monkeypatch.setattr(runtime, 'call', call)
    result = client.read_after()
    assert (result.outcome, result.code) == ('TRANSPORT_NOT_STARTED', 'NOT_READY')
    # A tracked read moves the offered cursor before the Registry's last check: a refusal
    # after it proves nothing about that effect, and a started call stays unknown.
    assert client.read_after().outcome == 'UNKNOWN'
    assert client.read_after().outcome == 'UNKNOWN'


def test_not_started_codes_are_the_strategy_clients():
    from bt_api_py import gateway_direct_client
    from bt_api_py.gateway_delegate_service_client import RUNTIME_NOT_STARTED_CODES

    assert RUNTIME_NOT_STARTED_CODES == gateway_direct_client._NOT_STARTED_ERRORS == frozenset(NOT_STARTED)
