"""Unbound actor status is not an Execution fact; runtime and host are synthetic."""
from copy import deepcopy
from dataclasses import asdict, replace

import pytest

from bt_api_py.gateway_delegate_service_client import GatewayDelegateActorReleaseIntent
from tests.unit.test_iter64_gateway_delegate_client_outcomes import ROUTE
from tests.unit.test_iter65_execution_delegate_cancel_guard import setup_site


def reply_for(observation):
    return {'status': 'OBSERVED', 'schema_version': 1, **asdict(observation.command),
            'native_identity_digest': observation.native_identity_digest}


@pytest.mark.parametrize('one_shot', [False, True])
@pytest.mark.parametrize('answer', ['valid', 'bad_identity', 'malformed', 'unknown', 'exception'])
def test_execution_status_never_exposes_or_persists_an_unbound_reply(monkeypatch, one_shot, answer):
    site, _, observation, _, _ = setup_site(monkeypatch, one_shot=one_shot)
    site.host.observation[site.cmd.request_key] = observation.command
    reply = reply_for(observation)
    if answer == 'bad_identity':
        reply['account_scope'] = 'different-account'
    elif answer == 'malformed':
        reply = {'status': 'OBSERVED', 'unexpected': True}
    elif answer == 'unknown':
        reply = {'status': 'UNKNOWN'}
    elif answer == 'exception':
        reply = TimeoutError('synthetic timeout')
    site.answers[:] = [reply]
    before = deepcopy(site.host.observation)
    result = site.client.actor_status(site.cmd.request_key)
    assert result.outcome == 'UNKNOWN' and result.observation is None and result.cancel is None
    assert site.host.observation == before
    assert site.cmd.request_key not in site.client._actor_observations
    assert site.calls == ['direct.delegate_actor_status']


@pytest.mark.parametrize('one_shot', [False, True])
@pytest.mark.parametrize('bad', ['payload', 'route', 'missing', 'command'])
def test_bad_execution_intent_does_not_create_a_fact(monkeypatch, one_shot, bad):
    site, intent, observation, _, _ = setup_site(monkeypatch, one_shot=one_shot)
    site.host.observation[site.cmd.request_key] = observation.command
    if bad == 'payload':
        site.host.intent[site.cmd.request_key] = replace(intent, canonical_payload='{}')
    elif bad == 'route':
        site.host.intent[site.cmd.request_key] = replace(intent, route=replace(ROUTE, service_id='other'))
    elif bad == 'missing':
        site.host.intent.clear()
    else:
        site.host.intent[site.cmd.request_key] = replace(intent, command=None)
    before = deepcopy(site.host.observation)
    result = site.client.actor_status(site.cmd.request_key)
    assert result.outcome == 'UNKNOWN' and result.observation is None and result.cancel is None
    assert site.host.observation == before and site.calls == []
    assert site.cmd.request_key not in site.client._actor_observations


def test_risk_actor_status_retains_observed_result_and_persistence(monkeypatch):
    site, _, observation, _, _ = setup_site(monkeypatch, kind='risk')
    site.answers[:] = [reply_for(observation)]
    result = site.client.actor_status(site.cmd.request_key)
    assert result.outcome == 'OBSERVED' and result.observation == observation
    assert site.host.observation[site.cmd.request_key] == observation.command
    assert site.client._actor_observations[site.cmd.request_key] == observation
    assert site.calls == ['direct.delegate_actor_status']


def swapped(intent):
    cmd = intent.command
    if cmd.actor_kind == 'risk':
        cmd = replace(cmd, actor_kind='execution', parent_ref='parent', child_id='child')
    else:
        cmd = replace(cmd, actor_kind='risk', parent_ref=None, child_id=None)
    return replace(intent, command=cmd)


@pytest.mark.parametrize('kind', ['risk', 'execution'])
@pytest.mark.parametrize('operation', ['cancel', 'release'])
def test_cached_intent_kind_swap_cannot_authorize_action(monkeypatch, kind, operation):
    site, intent, observation, _, _ = setup_site(monkeypatch, kind=kind)
    if operation == 'release':
        observation = replace(observation, command=replace(observation.command, state='RESERVED', version=1),
                              native_identity_digest=None)
        site.client._actor_observations[site.cmd.request_key] = observation
    if kind == 'risk':
        site.answers[:] = [reply_for(observation)]
        observation = site.client.actor_status(site.cmd.request_key).observation
        assert observation is not None
        site.calls.clear()
    # Execution observation models a legacy in-memory cache issued before the
    # existing WIP guard. It is explicitly bound to its original intent.
    site.host.intent[site.cmd.request_key] = swapped(intent)
    before = deepcopy((site.host.plans, site.host.cancel_markers, site.host.cancels, site.host.observation))
    if operation == 'cancel':
        result = site.client.cancel_actor(observation, operation_id='cancel')
    else:
        result = site.client.release_actor(observation, operation_id='release')
    assert result.outcome == 'UNKNOWN' and result.code == 'ACTOR_IDENTITY_CHANGED'
    assert before == (site.host.plans, site.host.cancel_markers, site.host.cancels, site.host.observation)
    assert site.calls == []


@pytest.mark.parametrize('kind', ['risk', 'execution'])
def test_status_rpc_with_intent_swap_cannot_persist_or_expose_fact(monkeypatch, kind):
    site, intent, observation, _, _ = setup_site(monkeypatch, kind=kind)
    site.host.observation[site.cmd.request_key] = observation.command
    before = deepcopy(site.host.observation)
    def swap_during_call(_operation, _arguments):
        site.host.intent[site.cmd.request_key] = swapped(intent)
        return reply_for(observation)
    site.answers[:] = [swap_during_call]
    result = site.client.actor_status(site.cmd.request_key)
    assert result.outcome == 'UNKNOWN' and result.observation is None
    assert site.host.observation == before
    assert site.cmd.request_key not in site.client._actor_observations
    assert site.cmd.request_key not in site.client._actor_observation_intents


@pytest.mark.parametrize('operation', ['cancel', 'release'])
def test_missing_observation_binding_is_unknown(monkeypatch, operation):
    site, _, observation, _, _ = setup_site(monkeypatch, kind='risk')
    if operation == 'release':
        observation = replace(observation, command=replace(observation.command, state='RESERVED', version=1),
                              native_identity_digest=None)
        site.client._actor_observations[site.cmd.request_key] = observation
    site.client._actor_observation_intents.clear()
    result = (site.client.cancel_actor(observation, operation_id='cancel') if operation == 'cancel'
              else site.client.release_actor(observation, operation_id='release'))
    assert result.outcome == 'UNKNOWN' and result.code == 'ACTOR_IDENTITY_CHANGED'
    assert site.calls == [] and not site.host.plans and not site.host.cancels


@pytest.mark.parametrize('operation', ['cancel', 'release'])
def test_same_kind_changed_grant_is_not_same_intent(monkeypatch, operation):
    site, intent, observation, _, _ = setup_site(monkeypatch, kind='risk')
    if operation == 'release':
        observation = replace(observation, command=replace(observation.command, state='RESERVED', version=1),
                              native_identity_digest=None)
        site.client._actor_observations[site.cmd.request_key] = observation
    site.answers[:] = [reply_for(observation)]
    observation = site.client.actor_status(site.cmd.request_key).observation
    site.calls.clear()
    site.host.intent[site.cmd.request_key] = replace(intent, command=replace(intent.command, grant_revision='other'))
    result = (site.client.cancel_actor(observation, operation_id='cancel') if operation == 'cancel'
              else site.client.release_actor(observation, operation_id='release'))
    assert result.outcome == 'UNKNOWN' and result.code == 'ACTOR_IDENTITY_CHANGED'
    assert site.calls == [] and not site.host.plans and not site.host.cancels


def test_risk_release_after_observed_status_keeps_normal_behavior(monkeypatch):
    site, _, observation, _, _ = setup_site(monkeypatch, kind='risk')
    observation = replace(observation, command=replace(observation.command, state='RESERVED', version=1),
                          native_identity_digest=None)
    snapshot = site.runtime.status()
    binding = snapshot.bindings[0]
    operations = binding.operations | {'direct.delegate_actor_release', 'direct.delegate_actor_release_status'}
    monkeypatch.setattr(site.runtime, 'status', lambda: replace(snapshot, bindings=(replace(binding, operations=operations),)))
    plans, markers = {}, {}
    site.host.load_delegate_release_intent = lambda key: plans.get(key)
    site.host.save_delegate_release_intent = lambda plan: plans.setdefault(plan.operation_id, plan)
    site.host.load_delegate_release_marker = lambda key: markers.get(key)
    site.host.save_delegate_release_marker = lambda key, marker: markers.setdefault(key, marker)
    released = replace(observation.command, state='RELEASED', version=2)
    site.answers[:] = [reply_for(observation), {'status': 'OK'},
                       {'status': 'OBSERVED', 'schema_version': 1, **asdict(released),
                        'native_identity_digest': None, 'release_operation_id': 'release',
                        'release_expected_version': 1}]
    observation = site.client.actor_status(site.cmd.request_key).observation
    result = site.client.release_actor(observation, operation_id='release')
    assert (result.outcome, result.state) == ('OBSERVED', 'RELEASED')
    assert site.host.observation[site.cmd.request_key] == released
    assert site.calls == ['direct.delegate_actor_status', 'direct.delegate_actor_release',
                          'direct.delegate_actor_release_status']
    reply = {'status': 'OBSERVED', 'schema_version': 1, **asdict(released),
             'native_identity_digest': None, 'release_operation_id': 'release', 'release_expected_version': 1}
    site.answers[:] = [reply, reply]
    assert site.client.release_actor(observation, operation_id='release').outcome == 'OBSERVED'
    assert site.client.actor_release_status('release').outcome == 'OBSERVED'
    assert site.calls.count('direct.delegate_actor_release') == 1


def test_execution_cached_release_stays_closed_even_with_matching_binding(monkeypatch):
    site, _, observation, _, _ = setup_site(monkeypatch)
    observation = replace(observation, command=replace(observation.command, state='RESERVED', version=1),
                          native_identity_digest=None)
    site.client._actor_observations[site.cmd.request_key] = observation
    before = deepcopy(site.host.observation)
    assert site.client.release_actor(observation, operation_id='release').outcome == 'UNKNOWN'
    assert site.calls == [] and site.host.observation == before


@pytest.mark.parametrize('one_shot', [False, True])
def test_execution_cached_release_bad_payload_is_unknown(monkeypatch, one_shot):
    site, intent, observation, _, _ = setup_site(monkeypatch, one_shot=one_shot)
    observation = replace(observation, command=replace(observation.command, state='RESERVED', version=1),
                          native_identity_digest=None)
    site.client._actor_observations[site.cmd.request_key] = observation
    bad_intent = replace(intent, canonical_payload='{}')
    site.host.intent[site.cmd.request_key] = bad_intent
    site.client._actor_observation_intents[site.cmd.request_key] = bad_intent
    before = deepcopy((site.host.intent, site.host.observation, site.host.plans,
                       site.host.cancel_markers, site.host.cancels))
    assert site.client.release_actor(observation, operation_id='release').outcome == 'UNKNOWN'
    assert site.calls == []
    assert before == (site.host.intent, site.host.observation, site.host.plans,
                      site.host.cancel_markers, site.host.cancels)


@pytest.mark.parametrize('swap', [False, True])
def test_historical_execution_release_is_never_accepted(monkeypatch, swap):
    site, intent, observation, _, _ = setup_site(monkeypatch)
    plan = GatewayDelegateActorReleaseIntent(site.cmd.request_key, 'release', site.cmd.digest,
        'reservation', site.cmd.payload_digest, site.cmd.actor_principal, 1)
    site.host.load_delegate_release_intent = lambda _key: plan
    if swap:
        site.host.intent[site.cmd.request_key] = swapped(intent)
    snapshot = site.runtime.status()
    binding = snapshot.bindings[0]
    monkeypatch.setattr(site.runtime, 'status', lambda: replace(snapshot, bindings=(
        replace(binding, operations=binding.operations | {'direct.delegate_actor_release_status'}),)))
    site.answers[:] = [{'status': 'UNKNOWN'}]
    before = deepcopy(site.host.observation)
    result = site.client.actor_release_status('release')
    assert result.outcome == 'UNKNOWN' and site.host.observation == before
    if swap:
        assert site.calls == []


@pytest.mark.parametrize('field', ['request_key', 'command_digest', 'payload_digest', 'actor_principal',
                                   'reservation_id', 'expected_version'])
def test_historical_risk_release_plan_must_match_intent(monkeypatch, field):
    site, intent, _, _, _ = setup_site(monkeypatch, kind='risk')
    plan = GatewayDelegateActorReleaseIntent(site.cmd.request_key, 'release', site.cmd.digest,
        'reservation', site.cmd.payload_digest, site.cmd.actor_principal, 1)
    bad = replace(plan, **{field: 2 if field == 'expected_version' else '' if field == 'reservation_id' else 'other'})
    site.host.load_delegate_release_intent = lambda _key: bad
    before = deepcopy(site.host.observation)
    assert site.client.actor_release_status('release').outcome == 'UNKNOWN'
    assert site.client._release_query(intent, bad, 5).outcome == 'UNKNOWN'
    assert site.calls == [] and site.host.observation == before
