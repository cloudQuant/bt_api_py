"""Unbound Execution cancellation never sends or accepts a synthetic reply."""
from copy import deepcopy
from dataclasses import asdict, replace

import pytest

from bt_api_base.gateway_direct_protocol import canonical_gateway_payload
from bt_api_py.gateway_delegated_cancel import GatewayDelegatedCancelObservation
from bt_api_py.gateway_delegate_service_client import (
    GatewayDelegateActorCancelIntent, GatewayDelegateActorObservation,
    GatewayDelegateIntent, GatewayDelegateObservation, GatewayDelegateServiceClient,
)
from tests.unit.test_iter64_gateway_delegate_client_outcomes import ROUTE, _Site
from tests.unit.test_iter65_execution_delegate_reserve_once import AtomicHost


class CancelHost(AtomicHost):
    def __init__(self):
        super().__init__()
        self.plans, self.cancel_markers, self.cancels = {}, {}, {}

    def load_delegate_cancel_intent(self, key):
        return self.plans.get(key)

    def save_delegate_cancel_intent(self, plan):
        self.plans[plan.operation_id] = plan
        return plan

    def load_delegate_cancel_marker(self, key):
        return self.cancel_markers.get(key)

    def save_delegate_cancel_marker(self, key, marker):
        self.cancel_markers[key] = marker
        return marker

    def load_delegate_cancel_observation(self, key):
        return self.cancels.get(key)

    def save_delegate_cancel_observation(self, key, observation):
        self.cancels[key] = observation
        return observation


def setup_site(monkeypatch, *, kind='execution', one_shot=False):
    site = _Site(monkeypatch, host=CancelHost())
    if kind == 'execution':
        site.cmd = replace(site.cmd, actor_kind='execution', parent_ref='parent', child_id='child')
    site.client = GatewayDelegateServiceClient(site.runtime, route=ROUTE, host=site.host,
                                               execution_one_shot=one_shot)
    snapshot = site.runtime.status()
    binding = snapshot.bindings[0]
    ops = binding.operations | {'direct.delegate_actor_cancel', 'direct.delegate_actor_cancel_status'}
    monkeypatch.setattr(site.runtime, 'status', lambda: replace(snapshot, bindings=(replace(binding, operations=ops),)))
    cmd = site.cmd
    intent = GatewayDelegateIntent(ROUTE, cmd, canonical_gateway_payload(site.request.to_dict()), 10.0)
    site.host.save_delegate_intent(intent)
    row = GatewayDelegateObservation('account', 'strategy', cmd.actor_principal, cmd.request_key,
                                     'reservation', cmd.payload_digest, 'SENT', 3, 'owner', 'owner')
    observation = GatewayDelegateActorObservation(row, 'a' * 64)
    site.client._actor_observations[cmd.request_key] = observation
    # Simulate an already bound historical observation; the production status
    # path itself never issues one for Execution while source is unverified.
    site.client._actor_observation_intents[cmd.request_key] = intent
    plan = GatewayDelegateActorCancelIntent(cmd.request_key, 'cancel', cmd.digest, row.reservation_id,
                                            row.digest, row.actor_principal, observation.native_identity_digest)
    record = GatewayDelegatedCancelObservation('account', 'strategy', cmd.actor_principal, cmd.actor_principal,
        cmd.request_key, 'cancel', 'attempt', row.reservation_id, row.digest, 'SENT', 3, 'owner', 'owner')
    return site, intent, observation, plan, record


def persistent(host):
    return deepcopy((host.intent, host.observation, host.marker, host.plans, host.cancel_markers, host.cancels))


@pytest.mark.parametrize('one_shot', [False, True])
def test_execution_historical_plan_cannot_accept_unbound_observation(monkeypatch, one_shot):
    site, intent, _, plan, record = setup_site(monkeypatch, one_shot=one_shot)
    site.host.plans['cancel'] = plan
    site.host.cancels['cancel'] = replace(record, state='REQUESTED', version=1)
    site.answers[:] = [{'status': 'OK', 'record': asdict(record)}]
    before = persistent(site.host)
    assert site.client.actor_cancel_status('cancel').outcome == 'UNKNOWN'
    assert site.client._cancel_actor_result('direct.delegate_actor_cancel_status', intent, plan, 5).outcome == 'UNKNOWN'
    assert persistent(site.host) == before and site.calls == []


@pytest.mark.parametrize('one_shot', [False, True])
@pytest.mark.parametrize('has_plan', [False, True])
def test_execution_cached_observation_cannot_send_or_change_storage(monkeypatch, one_shot, has_plan):
    site, _, observation, plan, record = setup_site(monkeypatch, one_shot=one_shot)
    if has_plan:
        site.host.plans['cancel'] = plan
    site.answers[:] = [{'status': 'OK', 'record': asdict(record), 'cancel_queue_state': 'QUEUED'}]
    before = persistent(site.host)
    assert site.client.cancel_actor(observation, operation_id='cancel').outcome == 'UNKNOWN'
    assert persistent(site.host) == before and site.calls == []


@pytest.mark.parametrize('one_shot', [False, True])
@pytest.mark.parametrize('bad', ['missing_plan', 'wrong_plan', 'wrong_operation', 'missing_intent', 'wrong_intent', 'wrong_command', 'bad_payload'])
def test_missing_or_bad_identity_never_proves_unsent(monkeypatch, one_shot, bad):
    site, intent, _, plan, _ = setup_site(monkeypatch, one_shot=one_shot)
    site.host.plans['cancel'] = plan
    if bad == 'missing_plan':
        site.host.plans.clear()
    elif bad == 'wrong_plan':
        site.host.plans['cancel'] = 'invalid-plan'
    elif bad == 'wrong_operation':
        site.host.plans['cancel'] = replace(plan, operation_id='other')
    elif bad == 'missing_intent':
        site.host.intent.clear()
    elif bad == 'wrong_intent':
        site.host.intent[plan.request_key] = 'invalid-intent'
    elif bad == 'wrong_command':
        site.host.intent[plan.request_key] = replace(intent, command=None)
    else:
        site.host.intent[plan.request_key] = replace(intent, canonical_payload='{}')
    before = persistent(site.host)
    assert site.client.actor_cancel_status('cancel').outcome == 'UNKNOWN'
    assert persistent(site.host) == before and site.calls == []


def test_risk_cancel_and_historical_status_keep_original_behavior(monkeypatch):
    site, _, observation, _, record = setup_site(monkeypatch, kind='risk')
    status = {'status': 'OBSERVED', 'schema_version': 1, **asdict(observation.command),
              'native_identity_digest': observation.native_identity_digest}
    site.answers[:] = [status, {'status': 'OK', 'record': asdict(record), 'cancel_queue_state': 'QUEUED'},
                       {'status': 'OK', 'record': asdict(record)}]
    observation = site.client.actor_status(site.cmd.request_key).observation
    assert site.client.cancel_actor(observation, operation_id='cancel').outcome == 'OBSERVED'
    assert site.client.actor_cancel_status('cancel').outcome == 'OBSERVED'
    assert site.host.cancels['cancel'] == record
    assert site.calls == ['direct.delegate_actor_status', 'direct.delegate_actor_cancel', 'direct.delegate_actor_cancel_status']


@pytest.mark.parametrize('one_shot', [False, True])
@pytest.mark.parametrize('bad', ['missing', 'wrong_type', 'bad_payload'])
def test_cached_observation_with_unavailable_intent_is_unknown(monkeypatch, one_shot, bad):
    site, intent, observation, _, _ = setup_site(monkeypatch, one_shot=one_shot)
    if bad == 'missing':
        site.host.intent.clear()
    elif bad == 'wrong_type':
        site.host.intent[site.cmd.request_key] = 'invalid-intent'
    else:
        site.host.intent[site.cmd.request_key] = replace(intent, canonical_payload='{}')
    before = persistent(site.host)
    assert site.client.cancel_actor(observation, operation_id='cancel').outcome == 'UNKNOWN'
    assert persistent(site.host) == before and site.calls == []


@pytest.mark.parametrize('field', ['request_key', 'command_digest', 'payload_digest', 'actor_principal',
                                   'reservation_id', 'native_identity_digest'])
def test_historical_risk_plan_must_match_original_intent(monkeypatch, field):
    site, intent, _, plan, record = setup_site(monkeypatch, kind='risk')
    value = '' if field in {'reservation_id', 'native_identity_digest'} else 'different'
    bad = replace(plan, **{field: value})
    site.host.plans['cancel'] = bad
    site.answers[:] = [{'status': 'OK', 'record': asdict(record)}]
    before = persistent(site.host)
    assert site.client.actor_cancel_status('cancel').outcome == 'UNKNOWN'
    assert site.client._cancel_actor_result('direct.delegate_actor_cancel_status', intent, bad, 5).outcome == 'UNKNOWN'
    assert persistent(site.host) == before and site.calls == []


def test_historical_execution_plan_cannot_be_rebound_to_risk(monkeypatch):
    site, intent, _, plan, record = setup_site(monkeypatch)
    site.host.plans['cancel'] = plan
    site.host.intent[site.cmd.request_key] = replace(intent,
        command=replace(intent.command, actor_kind='risk', parent_ref=None, child_id=None))
    site.answers[:] = [{'status': 'OK', 'record': asdict(record)}]
    before = persistent(site.host)
    assert site.client.actor_cancel_status('cancel').outcome == 'UNKNOWN'
    assert site.client._cancel_actor_result('direct.delegate_actor_cancel_status',
        site.host.intent[site.cmd.request_key], plan, 5).outcome == 'UNKNOWN'
    assert persistent(site.host) == before and site.calls == []
