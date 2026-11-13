"""Read-only coverage status for the experimental local calendar store.

Store hashes establish local consistency, not source authentication, runtime
binding, or real venue coverage. This entry only reports simulation declarations;
its separate countdown is never a verified coverage health or trading decision.
The bound snapshot is a caller-supplied copy, not proof of a live process binding.
No clock, notification, store mutation, or additional coverage is supplied here.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from bt_api_py.trading_calendar_binding import _covered_source
from bt_api_py.trading_calendar_coverage_health import (
    CoverageHealth,
    CoverageObservation,
    EvidenceClaim,
    evaluate_coverage_health,
)

VENUES = ("SHFE", "INE", "CZCE", "DCE", "CFFEX", "GFEX")
SIMULATION_ONLY = "SIMULATION_ONLY"


@dataclass(frozen=True)
class DeclaredCoverageStatus:
    """Real health remains UNKNOWN; simulated arithmetic is separately labelled.

    Simulation status is solely arithmetic against the declared interval; it
    does not establish completeness. Before the declared start, it is
    OUTSIDE_COVERAGE and no countdown is shown. After the end, the countdown
    remains a warning.
    """

    health: CoverageHealth
    coverage_start: date | None
    declaration: str
    simulation_status: str
    simulation_days_remaining: int | None
    completeness: str = "NOT_PROVEN"


@dataclass(frozen=True)
class CoverageStatusReport:
    mode: str
    current: tuple[DeclaredCoverageStatus, ...]
    bound: tuple[DeclaredCoverageStatus, ...] | None
    current_origin: str = "LOCAL_CURRENT_UNAUTHENTICATED"
    bound_origin: str = "NOT_SUPPLIED"


def _statuses(
    source: Any,
    kind: str,
    *,
    clock_instant: datetime | None,
    clock_claim: EvidenceClaim | None,
    clock_error_bound: timedelta | None,
    threshold_days: int,
) -> tuple[DeclaredCoverageStatus, ...]:
    try:
        # The store's snapshot is a detached but mutable object. Validate and
        # consume the same private copy, including manifest and version digests.
        snapshot = deepcopy(source)
        coverage, _ = _covered_source(snapshot)
        rows = {row.venue: row for row in coverage}
        version_id = snapshot.version_id
        valid = True
    except Exception:
        rows, version_id, valid = {}, None, False

    observations = []
    for venue in VENUES:
        row = rows.get(venue)
        observations.append(
            CoverageObservation(
                venue,
                kind,
                version_id,
                row.end_day if row is not None else None,
                EvidenceClaim("UNVERIFIED", "local:snapshot") if valid else None,
                EvidenceClaim("UNVERIFIED", "local:declaration") if row is not None else None,
            )
        )
    health = evaluate_coverage_health(
        observations,
        clock_instant=clock_instant,
        clock_claim=clock_claim,
        clock_error_bound=clock_error_bound,
        threshold_days=threshold_days,
    )
    result = []
    for item in health:
        row = rows.get(item.venue)
        days = None
        status = "UNKNOWN"
        if row is not None and item.shanghai_date is not None:
            if item.shanghai_date < row.start_day:
                status = "OUTSIDE_COVERAGE"
            else:
                days = (row.end_day - item.shanghai_date).days
                status = "WARNING" if days <= threshold_days else "OK"
        result.append(
            DeclaredCoverageStatus(
                item,
                row.start_day if row is not None else None,
                "DECLARED" if row is not None else "UNAVAILABLE",
                status,
                days,
            )
        )
    return tuple(result)


def read_coverage_status(
    store: Any,
    *,
    mode: str,
    clock_instant: datetime | None,
    clock_claim: EvidenceClaim | None,
    clock_error_bound: timedelta | None = None,
    threshold_days: int = 30,
    bound_snapshot: Any = None,
) -> CoverageStatusReport:
    """Read CURRENT once and optionally compare a separately supplied bound copy.

    The store must provide the SDK's ``read_current`` contract. A failed read,
    absent or inconsistent artifact, or missing declaration produces UNKNOWN.
    Caller-provided clock assertions are not authenticated here. Missing or
    ambiguous clock data suppresses even the separately labelled simulation.
    """
    if type(mode) is not str or mode != SIMULATION_ONLY:
        raise ValueError("explicit SIMULATION_ONLY mode is required")
    # Validate arguments even if there is no readable snapshot.
    evaluate_coverage_health(
        [],
        clock_instant=clock_instant,
        clock_claim=clock_claim,
        clock_error_bound=clock_error_bound,
        threshold_days=threshold_days,
    )
    try:
        current = store.read_current()
    except Exception:
        current = None
    kwargs = {
        "clock_instant": clock_instant,
        "clock_claim": clock_claim,
        "clock_error_bound": clock_error_bound,
        "threshold_days": threshold_days,
    }
    return CoverageStatusReport(
        SIMULATION_ONLY,
        _statuses(current, "published", **kwargs),
        _statuses(bound_snapshot, "bound", **kwargs) if bound_snapshot is not None else None,
        bound_origin=(
            "CALLER_DECLARED_UNAUTHENTICATED" if bound_snapshot is not None else "NOT_SUPPLIED"
        ),
    )
