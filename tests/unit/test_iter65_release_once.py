"""Execution release first-claim tests with local SQLite and synthetic RPC.

The injected continuity seam and RPC are only local test evidence. They do not
establish Gateway, provider, or account authorization.
"""

import threading
from dataclasses import asdict, replace

import pytest

pytest.importorskip('bt_api_execution')

import test_iter65_delegate_origin_fence as _origin_fixture
from bt_api_base.gateway_direct_protocol import canonical_gateway_payload
from bt_api_execution.contracts import canonical_json
from bt_api_execution.gateway_delegate_host import ExecutionGatewayDelegateHost
from bt_api_execution.parent_authority import ParentAuthority
from bt_api_execution.store import SqliteExecutionStore
from test_iter65_delegate_origin_fence import _seed_release

from bt_api_py.gateway_delegate_service_client import (
    GatewayDelegateActorObservation,
    GatewayDelegateActorResult,
    GatewayDelegateIntent,
    GatewayDelegateServiceClient,
)
from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime
from tests.unit.test_iter63_gateway_delegate_service_client import Host
from tests.unit.test_iter64_gateway_delegate_client_outcomes import _Site


@pytest.fixture(name='make_host')
def _make_host_fixture(tmp_path, monkeypatch):
    yield from _origin_fixture.make_host.__wrapped__(tmp_path, monkeypatch)


def _client(host, intent, observation, monkeypatch, calls):
    runtime = ThreadedServiceClientRuntime(lambda: None)
    client = GatewayDelegateServiceClient(runtime, route=intent.route, host=host,
                                           execution_one_shot=True)
    actor = GatewayDelegateActorObservation(observation, None)
    client._actor_observations[observation.request_key] = actor

    def actor_call(operation, _intent, _arguments, _timeout):
        calls.append(operation)
        return None, GatewayDelegateActorResult('UNKNOWN', code='SYNTHETIC_RPC')

    monkeypatch.setattr(client, '_actor_call', actor_call)
    return client, actor


def _setup(make_host, monkeypatch, mode='valid', *, saved_intent=False, saved_marker=False):
    # Exercise the local claim with a synthetic capability only. Real
    # Execution release is disabled until the route can be bound safely.
    monkeypatch.setattr(GatewayDelegateServiceClient, 'execution_write_available', property(lambda _self: True))
    store, authority, host, intent, observation, _ = make_host(mode)
    plan, marker = _seed_release(store, authority, host, intent, observation,
                                 saved_intent=saved_intent, saved_marker=saved_marker)
    calls = []
    client, actor = _client(host, intent, observation, monkeypatch, calls)
    return store, authority, host, intent, observation, plan, marker, calls, client, actor


def test_first_execution_release_claim_sends_once_then_queries(make_host, monkeypatch):
    store, authority, host, intent, observation, plan, marker, calls, client, actor = _setup(make_host, monkeypatch)
    first = client.release_actor(actor, operation_id=plan.operation_id)
    assert first.outcome == 'UNKNOWN'
    assert host.load_delegate_release_intent(plan.operation_id) == plan
    assert host.load_delegate_release_marker(plan.operation_id) == marker
    assert client.release_actor(actor, operation_id=plan.operation_id).outcome == 'UNKNOWN'
    assert calls == ['direct.delegate_actor_release', 'direct.delegate_actor_release_status']


@pytest.mark.parametrize(('saved_intent', 'saved_marker'), [(True, False), (True, True), (False, True)])
def test_legacy_partial_or_complete_claim_never_sends(make_host, monkeypatch, saved_intent, saved_marker):
    *_, plan, marker, calls, client, actor = _setup(
        make_host, monkeypatch, 'missing', saved_intent=saved_intent, saved_marker=saved_marker)
    assert client.release_actor(actor, operation_id=plan.operation_id).outcome == 'UNKNOWN'
    assert 'direct.delegate_actor_release' not in calls
    assert calls == (['direct.delegate_actor_release_status'] if saved_intent else [])


def test_missing_origin_cannot_claim_and_never_sends(make_host, monkeypatch):
    store, authority, host, intent, observation, plan, marker, calls, client, actor = _setup(
        make_host, monkeypatch, 'missing')
    result = client.release_actor(actor, operation_id=plan.operation_id)
    assert (result.outcome, result.code) == ('UNKNOWN', 'RELEASE_CLAIM_UNKNOWN')
    assert host.load_delegate_release_intent(plan.operation_id) is None
    assert host.load_delegate_release_marker(plan.operation_id) is None and calls == []


def test_unbound_execution_does_not_consume_first_claim(make_host, monkeypatch):
    store, authority, host, intent, observation, plan, marker, calls, client, actor = _setup(make_host, monkeypatch)
    monkeypatch.setattr(GatewayDelegateServiceClient, 'execution_write_available', property(lambda _self: False))
    result = client.release_actor(actor, operation_id=plan.operation_id)
    assert (result.outcome, result.code) == ('UNKNOWN', 'EXECUTION_BOUND_ROUTE_UNAVAILABLE')
    assert host.load_delegate_release_intent(plan.operation_id) is None
    assert host.load_delegate_release_marker(plan.operation_id) is None and calls == []


def test_existing_release_can_query_while_execution_is_unbound(make_host, monkeypatch):
    *_, plan, marker, calls, client, actor = _setup(
        make_host, monkeypatch, 'missing', saved_intent=True, saved_marker=True)
    monkeypatch.setattr(GatewayDelegateServiceClient, 'execution_write_available', property(lambda _self: False))
    assert client.release_actor(actor, operation_id=plan.operation_id).outcome == 'UNKNOWN'
    assert calls == ['direct.delegate_actor_release_status']


def test_execution_replay_does_not_persist_unbound_release_observation(make_host, monkeypatch):
    store, authority, host, intent, observation, plan, marker, calls, client, actor = _setup(
        make_host, monkeypatch, 'missing', saved_intent=True, saved_marker=True)
    before = host.load_delegate_observation('c')

    def reply(operation, *_args):
        calls.append(operation)
        return {'status': 'OBSERVED', 'state': 'RELEASED'}, None

    monkeypatch.setattr(client, '_actor_call', reply)
    monkeypatch.setattr(host, 'save_delegate_observation', lambda *_args: pytest.fail('persisted unbound status'))
    result = client.release_actor(actor, operation_id=plan.operation_id)
    assert (result.outcome, result.code) == ('UNKNOWN', 'RELEASE_STATUS_UNVERIFIED')
    assert calls == ['direct.delegate_actor_release_status']
    assert host.load_delegate_observation('c') == before


@pytest.mark.parametrize(('state', 'version', 'native_digest'), [
    ('RESERVED', 1, None), ('DISPATCHING', 2, 'a' * 64), ('SENT', 3, 'a' * 64),
    ('NOT_SENT', 3, 'a' * 64), ('UNKNOWN', 3, 'a' * 64),
    ('RELEASED', 2, None), ('EXPIRED', 2, None),
])
def test_public_execution_actor_status_discards_all_unbound_facts(make_host, monkeypatch, state, version, native_digest):
    store, authority, host, intent, observation, plan, marker, calls, client, actor = _setup(
        make_host, monkeypatch, 'missing', saved_intent=True, saved_marker=True)
    before = host.load_delegate_observation(plan.request_key)

    def reply(operation, _intent, arguments, _timeout):
        calls.append((operation, arguments['command']['request_key']))
        return dict(asdict(replace(observation, state=state, version=version)), status='OBSERVED',
                    schema_version=1, native_identity_digest=native_digest), None

    monkeypatch.setattr(client, '_actor_call', reply)
    monkeypatch.setattr(host, 'save_delegate_observation', lambda *_args: pytest.fail('persisted unbound fact'))
    result = client.actor_status(plan.request_key)
    assert (result.outcome, result.code, result.observation) == ('UNKNOWN', 'ACTOR_STATUS_UNVERIFIED', None)
    assert calls == [('direct.delegate_actor_status', plan.request_key)]
    assert host.load_delegate_observation(plan.request_key) == before
    assert plan.request_key not in client._actor_observations


@pytest.mark.parametrize('reply_kind', ['exception', 'transport_error', 'malformed'])
def test_public_execution_actor_status_errors_stay_unknown(make_host, monkeypatch, reply_kind):
    store, authority, host, intent, observation, plan, marker, calls, client, actor = _setup(
        make_host, monkeypatch, 'missing', saved_intent=True, saved_marker=True)

    def reply(operation, *_args):
        calls.append(operation)
        if reply_kind == 'exception':
            raise RuntimeError('synthetic connection failure')
        if reply_kind == 'transport_error':
            return None, GatewayDelegateActorResult('TRANSPORT_NOT_STARTED')
        return {'unexpected': 'reply'}, None

    monkeypatch.setattr(client, '_actor_call', reply)
    result = client.actor_status(plan.request_key)
    assert (result.outcome, result.code) == ('UNKNOWN', 'ACTOR_STATUS_UNVERIFIED')
    assert calls == ['direct.delegate_actor_status']
    assert host.load_delegate_observation(plan.request_key) == observation
    assert plan.request_key not in client._actor_observations


@pytest.mark.parametrize('method', ['actor_release_status', 'release_actor'])
@pytest.mark.parametrize('record', ['plan', 'intent'])
@pytest.mark.parametrize('failure', ['missing', 'malformed', 'exception'])
def test_execution_release_missing_or_unreadable_history_is_unknown(make_host, monkeypatch, method, record, failure):
    store, authority, host, intent, observation, plan, marker, calls, client, actor = _setup(
        make_host, monkeypatch, 'missing', saved_intent=True, saved_marker=True)

    def load(_key):
        if failure == 'exception':
            raise ValueError('synthetic corrupt record')
        return None if failure == 'missing' else {'malformed': True}

    monkeypatch.setattr(host, 'load_delegate_release_intent' if record == 'plan' else 'load_delegate_intent', load)
    result = (client.actor_release_status(plan.operation_id) if method == 'actor_release_status'
              else client.release_actor(actor, operation_id=plan.operation_id))
    assert result.outcome == 'UNKNOWN'
    assert calls == [] and host.load_delegate_observation(plan.request_key) == observation


@pytest.mark.parametrize('state', ['RELEASED', 'EXPIRED'])
def test_public_execution_release_status_discards_complete_terminal_reply(make_host, monkeypatch, state):
    store, authority, host, intent, observation, plan, marker, calls, client, actor = _setup(
        make_host, monkeypatch, 'missing', saved_intent=True, saved_marker=True)
    before = host.load_delegate_observation(plan.request_key)

    def reply(operation, _intent, arguments, _timeout):
        calls.append((operation, arguments['operation_id']))
        return dict(asdict(replace(observation, state=state, version=2)), status='OBSERVED', schema_version=1,
                    native_identity_digest=None, release_operation_id=plan.operation_id,
                    release_expected_version=plan.expected_version), None

    monkeypatch.setattr(client, '_actor_call', reply)
    monkeypatch.setattr(host, 'save_delegate_observation', lambda *_args: pytest.fail('persisted unbound status'))
    result = client.actor_release_status(plan.operation_id)
    assert (result.outcome, result.code, result.state) == ('UNKNOWN', 'RELEASE_STATUS_UNVERIFIED', None)
    assert calls == [('direct.delegate_actor_release_status', plan.operation_id)]
    assert host.load_delegate_observation(plan.request_key) == before


@pytest.mark.parametrize('reply_kind', ['exception', 'transport_error', 'malformed'])
def test_public_execution_release_status_errors_remain_unknown(make_host, monkeypatch, reply_kind):
    store, authority, host, intent, observation, plan, marker, calls, client, actor = _setup(
        make_host, monkeypatch, 'missing', saved_intent=True, saved_marker=True)
    before = host.load_delegate_observation(plan.request_key)

    def reply(operation, *_args):
        calls.append(operation)
        if reply_kind == 'exception':
            raise RuntimeError('synthetic connection failure')
        if reply_kind == 'transport_error':
            return None, GatewayDelegateActorResult('TRANSPORT_NOT_STARTED')
        return {'unexpected': 'reply'}, None

    monkeypatch.setattr(client, '_actor_call', reply)
    monkeypatch.setattr(host, 'save_delegate_observation', lambda *_args: pytest.fail('persisted unbound status'))
    result = client.actor_release_status(plan.operation_id)
    assert (result.outcome, result.code) == ('UNKNOWN', 'RELEASE_STATUS_UNVERIFIED')
    assert calls == ['direct.delegate_actor_release_status']
    assert host.load_delegate_observation(plan.request_key) == before


def test_public_execution_release_status_rejects_conflicting_plan(make_host, monkeypatch):
    store, authority, host, intent, observation, plan, marker, calls, client, actor = _setup(
        make_host, monkeypatch, 'missing', saved_intent=True, saved_marker=True)
    conflict = replace(plan, command_digest='0' * 64)
    monkeypatch.setattr(host, 'load_delegate_release_intent', lambda _operation: conflict)
    result = client.actor_release_status(plan.operation_id)
    assert (result.outcome, result.code) == ('UNKNOWN', 'RELEASE_PLAN_UNAVAILABLE')
    assert calls == [] and host.load_delegate_observation(plan.request_key) == observation


def test_public_execution_release_status_invalid_intent_remains_unknown(make_host, monkeypatch):
    store, authority, host, intent, observation, plan, marker, calls, client, actor = _setup(
        make_host, monkeypatch, 'missing', saved_intent=True, saved_marker=True)

    def invalid(_intent):
        raise ValueError('synthetic invalid intent')

    monkeypatch.setattr(client, '_validate', invalid)
    result = client.actor_release_status(plan.operation_id)
    assert (result.outcome, result.code) == ('UNKNOWN', 'RELEASE_PLAN_UNAVAILABLE')
    assert calls == [] and host.load_delegate_observation(plan.request_key) == observation


def test_public_execution_release_status_two_clients_cannot_confirm_unbound_reply(make_host, monkeypatch):
    store, authority, host, intent, observation, plan, marker, calls, _, _ = _setup(
        make_host, monkeypatch, 'missing', saved_intent=True, saved_marker=True)
    second = SqliteExecutionStore(store.path)
    second_authority = ParentAuthority(second, authority.scope, authority.lease,
        admission=lambda *_: True, _session_parent_route=('a', 's', 'd'))
    hosts = (host, ExecutionGatewayDelegateHost(second_authority))
    barrier, errors, outcomes = threading.Barrier(2), [], []

    def run(current):
        try:
            client, _ = _client(current, intent, observation, monkeypatch, calls)

            def reply(operation, _intent, arguments, _timeout):
                calls.append((operation, arguments['operation_id']))
                barrier.wait(timeout=5)
                return dict(asdict(replace(observation, state='RELEASED', version=2)), status='OBSERVED',
                            schema_version=1, native_identity_digest=None, release_operation_id=plan.operation_id,
                            release_expected_version=plan.expected_version), None

            monkeypatch.setattr(client, '_actor_call', reply)
            outcomes.append(client.actor_release_status(plan.operation_id).outcome)
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=run, args=(current,)) for current in hosts]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=8)
        assert not any(thread.is_alive() for thread in threads)
        assert errors == [] and outcomes == ['UNKNOWN', 'UNKNOWN']
        assert calls == [('direct.delegate_actor_release_status', plan.operation_id)] * 2
        assert host.load_delegate_observation(plan.request_key) == observation
    finally:
        second.close()


def test_execution_first_send_reply_does_not_confirm_unbound_status(make_host, monkeypatch):
    store, authority, host, intent, observation, plan, marker, calls, client, actor = _setup(make_host, monkeypatch)

    def synthetic_claim(value, saved_marker):
        assert value == plan and saved_marker == marker
        with store._transaction() as db:
            db.execute('UPDATE execution_parent_child_cancels SET intent_json=?, dispatch_marker_json=? '
                       'WHERE scope_key=? AND operation_id=?',
                       (canonical_json(asdict(plan)), canonical_json(marker), authority.parent_scope_key, plan.operation_id))
        return True

    def reply(operation, *_args):
        calls.append(operation)
        if operation == 'direct.delegate_actor_release':
            return {}, None
        return dict(asdict(replace(observation, state='RELEASED', version=2)), status='OBSERVED', schema_version=1,
                    native_identity_digest=None, release_operation_id=plan.operation_id,
                    release_expected_version=plan.expected_version), None

    monkeypatch.setattr(host, 'claim_first_delegate_release', synthetic_claim)
    monkeypatch.setattr(host, 'save_delegate_observation', lambda *_args: pytest.fail('persisted unbound status'))
    monkeypatch.setattr(client, '_actor_call', reply)
    result = client.release_actor(actor, operation_id=plan.operation_id)
    assert (result.outcome, result.code) == ('UNKNOWN', 'RELEASE_STATUS_UNVERIFIED')
    assert calls == ['direct.delegate_actor_release', 'direct.delegate_actor_release_status']
    assert host.load_delegate_observation(plan.request_key) == observation


def test_lost_bound_capability_after_claim_does_not_send(make_host, monkeypatch):
    store, authority, host, intent, observation, plan, marker, calls, client, actor = _setup(make_host, monkeypatch)
    claim = host.claim_first_delegate_release

    def claim_then_disable(value, saved_marker):
        result = claim(value, saved_marker)
        monkeypatch.setattr(GatewayDelegateServiceClient, 'execution_write_available', property(lambda _self: False))
        return result

    monkeypatch.setattr(host, 'claim_first_delegate_release', claim_then_disable)
    assert client.release_actor(actor, operation_id=plan.operation_id).outcome == 'UNKNOWN'
    assert host.load_delegate_release_intent(plan.operation_id) == plan
    assert host.load_delegate_release_marker(plan.operation_id) == marker and calls == []


def test_prior_same_operation_different_content_is_unknown(make_host, monkeypatch):
    store, authority, host, intent, observation, plan, marker, calls, client, actor = _setup(
        make_host, monkeypatch, saved_intent=True, saved_marker=True)
    conflict = replace(plan, reservation_id='other-reservation')
    with store._transaction() as db:
        db.execute('UPDATE execution_parent_child_cancels SET intent_json=? WHERE scope_key=? AND operation_id=?',
                   (canonical_json(asdict(conflict)), authority.parent_scope_key, plan.operation_id))
    assert client.release_actor(actor, operation_id=plan.operation_id).outcome == 'UNKNOWN'
    assert calls == []


def test_read_then_competing_legacy_claim_does_not_send(make_host, monkeypatch):
    store, authority, host, intent, observation, plan, marker, calls, client, actor = _setup(
        make_host, monkeypatch, 'missing')
    original = host.load_delegate_release_intent
    first = [True]

    def load_after_competitor(operation):
        result = original(operation)
        if first[0]:
            first[0] = False
            assert result is None
            with store._transaction() as db:
                db.execute('UPDATE execution_parent_child_cancels SET intent_json=?, dispatch_marker_json=? '
                           'WHERE scope_key=? AND operation_id=?',
                           (canonical_json(asdict(plan)), canonical_json(marker), authority.parent_scope_key, operation))
        return result

    monkeypatch.setattr(host, 'load_delegate_release_intent', load_after_competitor)
    assert client.release_actor(actor, operation_id=plan.operation_id).outcome == 'UNKNOWN'
    assert calls == ['direct.delegate_actor_release_status']


def test_committed_claim_with_lost_reply_stays_unknown_on_new_client(make_host, monkeypatch):
    store, authority, host, intent, observation, plan, marker, calls, client, actor = _setup(make_host, monkeypatch)
    claim = host.claim_first_delegate_release

    def lost_after_commit(value, saved_marker):
        assert claim(value, saved_marker) is True
        raise RuntimeError('synthetic lost reply after local commit')

    monkeypatch.setattr(host, 'claim_first_delegate_release', lost_after_commit)
    assert client.release_actor(actor, operation_id=plan.operation_id).outcome == 'UNKNOWN'
    assert calls == [] and host.load_delegate_release_marker(plan.operation_id) == marker
    monkeypatch.setattr(host, 'claim_first_delegate_release', claim)
    restarted, restarted_actor = _client(host, intent, observation, monkeypatch, calls)
    assert restarted.release_actor(restarted_actor, operation_id=plan.operation_id).outcome == 'UNKNOWN'
    assert calls == ['direct.delegate_actor_release_status']


@pytest.mark.parametrize('claim_result', [None, 1, 'true'])
def test_only_exact_true_claim_can_send(make_host, monkeypatch, claim_result):
    *_, host, intent, observation, plan, marker, calls, client, actor = _setup(make_host, monkeypatch)
    monkeypatch.setattr(host, 'claim_first_delegate_release', lambda *_: claim_result)
    assert client.release_actor(actor, operation_id=plan.operation_id).outcome == 'UNKNOWN'
    assert calls == []


def test_two_clients_on_independent_connections_cannot_both_send(make_host, monkeypatch):
    store, authority, host, intent, observation, plan, marker, calls, _, _ = _setup(make_host, monkeypatch)
    second = SqliteExecutionStore(store.path)
    second_authority = ParentAuthority(second, authority.scope, authority.lease,
        admission=lambda *_: True, _session_parent_route=('a', 's', 'd'))
    hosts = (host, ExecutionGatewayDelegateHost(second_authority))
    barrier, errors, outcomes = threading.Barrier(2), [], []
    original = ExecutionGatewayDelegateHost.load_delegate_release_intent
    thread_state = threading.local()

    def concurrent_first_load(current, operation):
        value = original(current, operation)
        if not getattr(thread_state, 'seen', False):
            thread_state.seen = True
            assert value is None
            barrier.wait(timeout=5)
        return value

    monkeypatch.setattr(ExecutionGatewayDelegateHost, 'load_delegate_release_intent', concurrent_first_load)

    def run(current):
        try:
            client, actor = _client(current, intent, observation, monkeypatch, calls)
            outcomes.append(client.release_actor(actor, operation_id=plan.operation_id).outcome)
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=run, args=(current,)) for current in hosts]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=8)
        assert not any(thread.is_alive() for thread in threads)
        assert errors == [] and outcomes == ['UNKNOWN', 'UNKNOWN']
        assert calls.count('direct.delegate_actor_release') == 1
        assert host.load_delegate_release_marker(plan.operation_id) == marker
    finally:
        second.close()


class _RiskReleaseHost(Host):
    def __init__(self):
        super().__init__()
        self.release_intents, self.release_markers = {}, {}

    def load_delegate_release_intent(self, operation_id):
        return self.release_intents.get(operation_id)

    def save_delegate_release_intent(self, plan):
        self.release_intents[plan.operation_id] = plan
        return plan

    def load_delegate_release_marker(self, operation_id):
        return self.release_markers.get(operation_id)

    def save_delegate_release_marker(self, operation_id, marker):
        self.release_markers[operation_id] = marker
        return marker


def test_risk_actor_status_still_persists_observation(monkeypatch):
    from bt_api_py.gateway_delegate_service_client import GatewayDelegateObservation

    host = _RiskReleaseHost()
    site = _Site(monkeypatch, host=host)
    intent = GatewayDelegateIntent(site.client._route, site.cmd,
                                  canonical_gateway_payload(site.request.to_dict()), 10.0)
    host.save_delegate_intent(intent)
    observation = GatewayDelegateObservation('account', 'strategy', 'risk-principal', site.cmd.request_key,
        'reservation', site.cmd.payload_digest, 'RELEASED', 2, 'owner', 'owner')
    monkeypatch.setattr(site.client, '_actor_call', lambda *_args:
                        (dict(asdict(observation), status='OBSERVED', schema_version=1,
                              native_identity_digest=None), None))
    result = site.client.actor_status(site.cmd.request_key)
    assert result.outcome == 'OBSERVED' and result.observation.command == observation
    assert host.load_delegate_observation(site.cmd.request_key) == observation


def test_risk_missing_release_plan_keeps_legacy_outcome(monkeypatch):
    host = _RiskReleaseHost()
    site = _Site(monkeypatch, host=host)
    result = site.client.actor_release_status('missing')
    assert (result.outcome, result.code) == ('TRANSPORT_NOT_STARTED', 'RELEASE_PLAN_UNAVAILABLE')


def test_risk_release_keeps_legacy_host_contract(monkeypatch):
    from bt_api_py.gateway_delegate_service_client import GatewayDelegateObservation
    host = _RiskReleaseHost()
    site = _Site(monkeypatch, host=host)
    intent = GatewayDelegateIntent(site.client._route, site.cmd,
                                  canonical_gateway_payload(site.request.to_dict()), 10.0)
    host.save_delegate_intent(intent)
    command = GatewayDelegateObservation('account', 'strategy', 'risk-principal', site.cmd.request_key,
        'reservation', site.cmd.payload_digest, 'RESERVED', 1, 'owner', 'owner')
    actor = GatewayDelegateActorObservation(command, None)
    site.client._actor_observations[command.request_key] = actor
    calls = []
    monkeypatch.setattr(site.client, '_actor_call', lambda operation, *_args:
                        (calls.append(operation), GatewayDelegateActorResult('UNKNOWN')))
    assert site.client.release_actor(actor, operation_id='risk-release').outcome == 'UNKNOWN'
    assert calls == ['direct.delegate_actor_release']
    assert host.load_delegate_release_intent('risk-release') is not None
    assert host.load_delegate_release_marker('risk-release') is not None


def test_risk_release_can_still_confirm_and_replay_original_id(monkeypatch):
    from bt_api_py.gateway_delegate_service_client import GatewayDelegateObservation

    host = _RiskReleaseHost()
    site = _Site(monkeypatch, host=host)
    intent = GatewayDelegateIntent(site.client._route, site.cmd,
                                  canonical_gateway_payload(site.request.to_dict()), 10.0)
    host.save_delegate_intent(intent)
    command = GatewayDelegateObservation('account', 'strategy', 'risk-principal', site.cmd.request_key,
        'reservation', site.cmd.payload_digest, 'RESERVED', 1, 'owner', 'owner')
    actor = GatewayDelegateActorObservation(command, None)
    site.client._actor_observations[command.request_key] = actor
    calls = []

    def actor_call(operation, *_args):
        calls.append(operation)
        if operation == 'direct.delegate_actor_release':
            return {}, None
        assert operation == 'direct.delegate_actor_release_status'
        return dict(asdict(replace(command, state='RELEASED', version=2)), status='OBSERVED', schema_version=1,
                    native_identity_digest=None, release_operation_id='risk-release', release_expected_version=1), None

    monkeypatch.setattr(site.client, '_actor_call', actor_call)
    assert site.client.release_actor(actor, operation_id='risk-release').state == 'RELEASED'
    assert site.client.release_actor(actor, operation_id='risk-release').state == 'RELEASED'
    assert site.client.actor_release_status('risk-release').state == 'RELEASED'
    assert calls == ['direct.delegate_actor_release', 'direct.delegate_actor_release_status',
                     'direct.delegate_actor_release_status', 'direct.delegate_actor_release_status']
    assert host.load_delegate_observation(command.request_key).state == 'RELEASED'


def test_execution_release_rejects_untrusted_host_even_if_it_claims_true(monkeypatch):
    from bt_api_py.gateway_delegate_service_client import GatewayDelegateObservation
    host = _RiskReleaseHost()
    host.claim_first_delegate_reserve = lambda _intent: True
    host.check_delegate_dispatch_retry = lambda *_args: True
    host.claim_first_delegate_release = lambda *_args: True
    site = _Site(monkeypatch, host=host)
    site.cmd = replace(site.cmd, actor_kind='execution', parent_ref='parent', child_id='child')
    monkeypatch.setattr(GatewayDelegateServiceClient, 'execution_write_available', property(lambda _self: True))
    client = GatewayDelegateServiceClient(site.runtime, route=site.client._route, host=host,
                                           execution_one_shot=True)
    intent = GatewayDelegateIntent(client._route, site.cmd, canonical_gateway_payload(site.request.to_dict()), 10.0)
    host.save_delegate_intent(intent)
    command = GatewayDelegateObservation('account', 'strategy', 'risk-principal', site.cmd.request_key,
        'reservation', site.cmd.payload_digest, 'RESERVED', 1, 'owner', 'owner')
    actor = GatewayDelegateActorObservation(command, None)
    client._actor_observations[command.request_key] = actor
    calls = []
    monkeypatch.setattr(client, '_actor_call', lambda operation, *_args: (calls.append(operation), None))
    result = client.release_actor(actor, operation_id='execution-release')
    assert (result.outcome, result.code) == ('UNKNOWN', 'RELEASE_CLAIM_UNAVAILABLE')
    assert calls == []
