"""SIMULATION_ONLY state composition; never a live status or order gate.

All identities, phases, source/ordering assertions and recovery evidence are
caller claims, not authenticated facts. The result cannot authorize an account,
trade, order dispatch, or a change of an order's target or expiry. No clocks are
read here: absence of new messages is never used to expire an observation.
This provisional model neither maps real provider/native states nor establishes
source validity, actual recovery, or the unresolved Gateway contract alignment.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

SIMULATION_ONLY = "SIMULATION_ONLY"
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z", re.ASCII)
_CALENDAR = frozenset({"CONTINUOUS", "AUCTION", "CLOSED", "UNKNOWN"})
_OBSERVATIONS = {
    "CONTINUOUS": "CONTINUOUS",
    "AUCTION": "AUCTION",
    "NO_TRADING": "CLOSED",
    "CLOSED": "CLOSED",
    "CIRCUIT_BREAKER_HALT": "CLOSED",
}
_HALT = frozenset({"NO_TRADING", "CLOSED", "CIRCUIT_BREAKER_HALT"})


@dataclass(frozen=True)
class SimulationScope:
    route: str
    venue: str
    kind: str
    product: str
    instrument: str


@dataclass(frozen=True)
class SimulationContext:
    scope: SimulationScope
    generation: str
    trading_day: date
    expected_source_id: str


@dataclass(frozen=True)
class SimulatedObservation:
    context: SimulationContext
    state: str
    validity: str
    source_asserted: bool
    applicability_asserted: bool
    ordering_asserted: bool
    observed_at_utc: datetime
    source_id: str


@dataclass(frozen=True)
class SimulatedRecovery:
    context: SimulationContext
    invalidated_generation: str
    source_disconnected: bool
    prior_applicability_asserted: bool
    session_restored: bool
    reports_restored: bool
    no_new_observation: bool


@dataclass(frozen=True)
class SimulationResult:
    phase: str
    reason: str
    calendar_phase: str
    observation_state: str | None
    observation_status: str
    conflict: bool = False
    next_step: str = "NO_ELIGIBILITY_ENTRY"
    mode: str = SIMULATION_ONLY
    source_authentication: str = "NOT_AUTHENTICATED"
    account_authorization: str = "NONE"
    trade_authorization: str = "NONE"
    dispatch: str = "NEVER"
    original_target: str = "NOT_EVALUATED"
    expiry: str = "NOT_EVALUATED"


def _identifier(value: object) -> bool:
    return type(value) is str and _ID.fullmatch(value) is not None


def _context_valid(context: object) -> bool:
    if type(context) is not SimulationContext or type(context.scope) is not SimulationScope:
        return False
    scope = context.scope
    return (
        all(
            _identifier(value)
            for value in (scope.route, scope.venue, scope.product, scope.instrument)
        )
        and type(scope.kind) is str
        and scope.kind in {"FUTURE", "OPTION"}
        and _identifier(context.generation)
        and _identifier(context.expected_source_id)
        and type(context.trading_day) is date
    )


def _observation_valid_shape(observation: object) -> bool:
    if type(observation) is not SimulatedObservation or not _context_valid(observation.context):
        return False
    stamp = observation.observed_at_utc
    return (
        type(observation.state) is str
        and observation.state in _OBSERVATIONS
        and type(observation.validity) is str
        and observation.validity in {"VALID", "INVALIDATED", "UNKNOWN"}
        and type(observation.source_asserted) is bool
        and type(observation.applicability_asserted) is bool
        and type(observation.ordering_asserted) is bool
        and _identifier(observation.source_id)
        and type(stamp) is datetime
        and stamp.tzinfo is not None
        and stamp.utcoffset() == timedelta(0)
    )


def _next_step(phase: str) -> str:
    return "CHECK_EXISTING_ELIGIBILITY" if phase == "CONTINUOUS" else "NO_ELIGIBILITY_ENTRY"


def _recovered(
    context: SimulationContext, observation: SimulatedObservation, recovery: object
) -> bool:
    return (
        type(recovery) is SimulatedRecovery
        and _context_valid(recovery.context)
        and recovery.context == context
        and _identifier(recovery.invalidated_generation)
        and recovery.invalidated_generation == observation.context.generation
        and observation.context.generation != context.generation
        and recovery.source_disconnected is True
        and recovery.prior_applicability_asserted is True
        and recovery.session_restored is True
        and recovery.reports_restored is True
        and recovery.no_new_observation is True
    )


def compose_calendar_state(
    *,
    mode: str,
    context: SimulationContext,
    calendar_phase: str,
    observation: SimulatedObservation | None,
    recovery: SimulatedRecovery | None = None,
) -> SimulationResult:
    """Apply the declared simulation table without authorizing any real action.

    An observation must match the exact declared route and contract scope. Missing
    or nonmatching identity, source, order, generation, day or validity is UNKNOWN.
    Only the specified invalidated halt recovery case can fall back to calendar.
    That simulation requires different old and current session generations;
    it does not establish that either generation represents a real session.
    The caller supplies all assertions; none are verified or independently trusted.
    """
    try:
        if mode != SIMULATION_ONLY or type(mode) is not str or not _context_valid(context):
            return SimulationResult(
                "UNKNOWN", "INVALID_SIMULATION_CONTEXT", "UNKNOWN", None, "UNKNOWN"
            )
        if type(calendar_phase) is not str or calendar_phase not in _CALENDAR:
            return SimulationResult("UNKNOWN", "INVALID_CALENDAR_PHASE", "UNKNOWN", None, "UNKNOWN")
        if calendar_phase == "UNKNOWN":
            observed = (
                observation.state
                if type(observation) is SimulatedObservation
                and type(observation.state) is str
                and observation.state in _OBSERVATIONS
                else None
            )
            return SimulationResult(
                "UNKNOWN", "CALENDAR_UNKNOWN", calendar_phase, observed, "NOT_APPLIED"
            )
        if observation is None:
            if recovery is not None:
                return SimulationResult(
                    "UNKNOWN", "UNEXPECTED_RECOVERY", calendar_phase, None, "UNKNOWN"
                )
            return SimulationResult(
                calendar_phase,
                "NO_OBSERVATION",
                calendar_phase,
                None,
                "NO_OBSERVATION",
                next_step=_next_step(calendar_phase),
            )
        if not _observation_valid_shape(observation):
            return SimulationResult(
                "UNKNOWN", "OBSERVATION_INVALID", calendar_phase, None, "UNKNOWN"
            )
        state = observation.state
        if (
            observation.context.scope != context.scope
            or observation.context.trading_day != context.trading_day
            or observation.context.expected_source_id != context.expected_source_id
            or observation.source_id != context.expected_source_id
            or not observation.source_asserted
            or not observation.applicability_asserted
            or not observation.ordering_asserted
        ):
            return SimulationResult(
                "UNKNOWN", "OBSERVATION_UNPROVEN", calendar_phase, state, "UNKNOWN"
            )
        if observation.validity == "INVALIDATED":
            if (
                state in _HALT
                and calendar_phase == "CONTINUOUS"
                and _recovered(context, observation, recovery)
            ):
                return SimulationResult(
                    calendar_phase,
                    "INVALIDATED_HALT_RECOVERED",
                    calendar_phase,
                    state,
                    "NO_VALID_OBSERVATION",
                    next_step="CHECK_EXISTING_ELIGIBILITY",
                )
            return SimulationResult(
                "UNKNOWN", "INVALIDATED_OBSERVATION", calendar_phase, state, "UNKNOWN"
            )
        if observation.validity != "VALID" or observation.context.generation != context.generation:
            return SimulationResult(
                "UNKNOWN", "OBSERVATION_UNPROVEN", calendar_phase, state, "UNKNOWN"
            )
        if recovery is not None:
            return SimulationResult(
                "UNKNOWN", "UNEXPECTED_RECOVERY", calendar_phase, state, "UNKNOWN"
            )
        phase = _OBSERVATIONS[state]
        conflict = phase != calendar_phase
        return SimulationResult(
            phase,
            "CALENDAR_OBSERVATION_CONFLICT" if conflict else "OBSERVATION_MATCHES",
            calendar_phase,
            state,
            "DECLARED_VALID",
            conflict=conflict,
            next_step=_next_step(phase),
        )
    except Exception:
        # No exception text or assertion can confer authority.
        return SimulationResult("UNKNOWN", "SIMULATION_INPUT_ERROR", "UNKNOWN", None, "UNKNOWN")
