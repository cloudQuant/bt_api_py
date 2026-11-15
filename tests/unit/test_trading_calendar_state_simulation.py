"""Synthetic assertions only: never authenticate a venue or authorize orders."""

from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from bt_api_py.trading_calendar_state_simulation import (
    SIMULATION_ONLY,
    SimulatedObservation,
    SimulatedRecovery,
    SimulationContext,
    SimulationScope,
    compose_calendar_state,
)

SCOPE = SimulationScope("CTP___FUTURE", "SHFE", "FUTURE", "rb", "rb2701")
CONTEXT = SimulationContext(SCOPE, "generation2", date(2026, 11, 12), "sim_source")
STAMP = datetime(2026, 11, 12, 1, tzinfo=UTC)
STATES = {
    "CONTINUOUS": "CONTINUOUS",
    "AUCTION": "AUCTION",
    "NO_TRADING": "CLOSED",
    "CLOSED": "CLOSED",
    "CIRCUIT_BREAKER_HALT": "CLOSED",
}


def observation(state="CONTINUOUS", **changes):
    return replace(
        SimulatedObservation(CONTEXT, state, "VALID", True, True, True, STAMP, "sim_source"),
        **changes,
    )


def compose(calendar="CONTINUOUS", obs=None, **changes):
    args = {
        "mode": SIMULATION_ONLY,
        "context": CONTEXT,
        "calendar_phase": calendar,
        "observation": obs,
    }
    args.update(changes)
    return compose_calendar_state(**args)


def assert_no_authority(result):
    assert result.mode == "SIMULATION_ONLY"
    assert result.source_authentication == "NOT_AUTHENTICATED"
    assert result.account_authorization == result.trade_authorization == "NONE"
    assert result.dispatch == "NEVER"
    assert (result.original_target, result.expiry) == ("NOT_EVALUATED", "NOT_EVALUATED")


@pytest.mark.parametrize("calendar", ["CONTINUOUS", "AUCTION", "CLOSED"])
@pytest.mark.parametrize("realtime", list(STATES))
def test_all_known_state_combinations_follow_d67_08_b(calendar, realtime):
    result = compose(calendar, observation(realtime))
    phase = STATES[realtime]
    assert result.phase == phase and result.calendar_phase == calendar
    assert result.observation_state == realtime and result.observation_status == "DECLARED_VALID"
    assert result.conflict == (phase != calendar)
    assert result.reason == (
        "CALENDAR_OBSERVATION_CONFLICT" if result.conflict else "OBSERVATION_MATCHES"
    )
    assert result.next_step == (
        "CHECK_EXISTING_ELIGIBILITY" if phase == "CONTINUOUS" else "NO_ELIGIBILITY_ENTRY"
    )
    assert_no_authority(result)


@pytest.mark.parametrize("realtime", list(STATES))
def test_unknown_calendar_never_expanded_by_observation(realtime):
    result = compose("UNKNOWN", observation(realtime))
    assert (result.phase, result.reason, result.observation_status) == (
        "UNKNOWN",
        "CALENDAR_UNKNOWN",
        "NOT_APPLIED",
    )
    assert result.observation_state == realtime
    assert result.next_step == "NO_ELIGIBILITY_ENTRY"
    assert_no_authority(result)


@pytest.mark.parametrize("calendar", ["CONTINUOUS", "AUCTION", "CLOSED"])
def test_absence_of_observation_uses_calendar_without_authentication(calendar):
    result = compose(calendar)
    assert (result.phase, result.reason, result.observation_status) == (
        calendar,
        "NO_OBSERVATION",
        "NO_OBSERVATION",
    )
    assert_no_authority(result)


@pytest.mark.parametrize(
    "field,value",
    [
        ("route", "different"),
        ("venue", "DCE"),
        ("kind", "OPTION"),
        ("product", "cu"),
        ("instrument", "rb2705"),
    ],
)
def test_mismatched_scope_cannot_be_treated_as_no_observation(field, value):
    other = replace(CONTEXT, scope=replace(SCOPE, **{field: value}))
    result = compose("CLOSED", observation(context=other))
    assert (result.phase, result.reason) == ("UNKNOWN", "OBSERVATION_UNPROVEN")
    assert result.observation_status == "UNKNOWN"


@pytest.mark.parametrize(
    "changes",
    [
        {"context": replace(CONTEXT, generation="generation1")},
        {"context": replace(CONTEXT, trading_day=date(2026, 11, 11))},
        {"context": replace(CONTEXT, scope=None)},
        {"source_asserted": False},
        {"applicability_asserted": False},
        {"ordering_asserted": False},
        {"validity": "UNKNOWN"},
        {"validity": "EXPIRED"},
        {"state": "BEFORE_TRADING"},
        {"state": "INVALID_NATIVE_ENUM"},
        {"observed_at_utc": datetime(2026, 11, 12)},
        {"source_asserted": 1},
        {"source_id": ""},
        {"source_id": "other_source"},
        {"context": replace(CONTEXT, expected_source_id="other_source")},
    ],
)
def test_unproved_invalid_or_old_observation_fails_closed(changes):
    result = compose("CLOSED", observation(**changes))
    assert result.phase == "UNKNOWN" and result.next_step == "NO_ELIGIBILITY_ENTRY"
    assert_no_authority(result)


def test_d67_16_recovery_requires_explicit_applicability_invalidation_and_recovery():
    old = observation(
        "NO_TRADING", context=replace(CONTEXT, generation="generation1"), validity="INVALIDATED"
    )
    recovery = SimulatedRecovery(CONTEXT, "generation1", True, True, True, True, True)
    result = compose("CONTINUOUS", old, recovery=recovery)
    assert (result.phase, result.reason, result.observation_status, result.next_step) == (
        "CONTINUOUS",
        "INVALIDATED_HALT_RECOVERED",
        "NO_VALID_OBSERVATION",
        "CHECK_EXISTING_ELIGIBILITY",
    )
    assert_no_authority(result)
    for field in (
        "source_disconnected",
        "prior_applicability_asserted",
        "session_restored",
        "reports_restored",
        "no_new_observation",
    ):
        assert (
            compose("CONTINUOUS", old, recovery=replace(recovery, **{field: False})).phase
            == "UNKNOWN"
        )
    assert compose("CONTINUOUS", old).phase == "UNKNOWN"
    assert (
        compose("CONTINUOUS", old, recovery=replace(recovery, invalidated_generation="wrong")).phase
        == "UNKNOWN"
    )
    assert (
        compose(
            "CONTINUOUS",
            old,
            recovery=replace(recovery, context=replace(CONTEXT, generation="other")),
        ).phase
        == "UNKNOWN"
    )
    assert compose("AUCTION", old, recovery=recovery).phase == "UNKNOWN"
    assert (
        compose("CONTINUOUS", replace(old, state="CONTINUOUS"), recovery=recovery).phase
        == "UNKNOWN"
    )
    assert (
        compose("CONTINUOUS", replace(old, source_asserted=False), recovery=recovery).phase
        == "UNKNOWN"
    )
    assert (
        compose("CONTINUOUS", replace(old, validity="UNKNOWN"), recovery=recovery).phase
        == "UNKNOWN"
    )
    assert (
        compose("CONTINUOUS", old, recovery=replace(recovery, no_new_observation=1)).phase
        == "UNKNOWN"
    )


def test_d67_16_same_generation_does_not_establish_new_recovery_cycle():
    old = observation("NO_TRADING", context=CONTEXT, validity="INVALIDATED")
    recovery = SimulatedRecovery(CONTEXT, CONTEXT.generation, True, True, True, True, True)
    result = compose("CONTINUOUS", old, recovery=recovery)
    assert (result.phase, result.reason, result.next_step) == (
        "UNKNOWN",
        "INVALIDATED_OBSERVATION",
        "NO_ELIGIBILITY_ENTRY",
    )
    assert_no_authority(result)


@pytest.mark.parametrize("state", ["NO_TRADING", "CLOSED", "CIRCUIT_BREAKER_HALT"])
def test_new_valid_halt_precedes_stale_halt_recovery(state):
    result = compose("CONTINUOUS", observation(state))
    assert result.phase == "CLOSED" and result.conflict
    assert result.next_step == "NO_ELIGIBILITY_ENTRY"


def test_no_elapsed_time_auto_expiry():
    old_stamp = datetime(1990, 1, 1, tzinfo=UTC)
    result = compose("CLOSED", observation(observed_at_utc=old_stamp))
    assert result.phase == "CONTINUOUS" and result.conflict
    assert result.observation_status == "DECLARED_VALID"
    assert_no_authority(result)


def test_explicit_mode_and_invalid_calendar_or_recovery_fail_closed():
    with pytest.raises(TypeError):
        compose_calendar_state(context=CONTEXT, calendar_phase="CONTINUOUS", observation=None)
    assert compose(mode="PRODUCTION").phase == "UNKNOWN"
    assert compose(calendar="BEFORE_TRADING").phase == "UNKNOWN"
    assert compose(context=replace(CONTEXT, scope=replace(SCOPE, instrument=""))).phase == "UNKNOWN"
    recovery = SimulatedRecovery(CONTEXT, "generation1", True, True, True, True, True)
    assert compose(recovery=recovery).phase == "UNKNOWN"
    assert compose(obs=observation(), recovery=recovery).phase == "UNKNOWN"
