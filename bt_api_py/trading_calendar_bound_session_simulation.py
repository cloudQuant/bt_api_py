"""Evaluate an explicit owner-day instant against an existing simulation binding.

The binding supplies the snapshot, version and per-window declarations together.
These declarations are unverified and do not establish venue state or authority.
"""

from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from bt_api_py.trading_calendar_binding import (
    BindingStatus,
    BindingUnavailableError,
    SimulationCalendarBinding,
)


@dataclass(frozen=True)
class BoundSessionEvaluation:
    phase: str
    reason: str
    binding: BindingStatus | None
    owner_day: date | None = None
    window: Any = None
    window_provenance: dict | None = None
    simulation_provenance: dict | None = None
    mode: str = "SIMULATION_ONLY"
    source_authentication: str = "NOT_PROVEN"
    trade_authorization: str = "NONE"


def evaluate_bound_session(binding, *, venue, kind, product, contract, owner_day, at_utc):
    """Return a half-open original window and its declared simulation origin.

    An unmatched instant, missing per-window provenance, or invalid scope is
    UNKNOWN. The caller provides the owner day; this function never derives it.
    """

    def unknown(reason, status=None):
        return BoundSessionEvaluation("UNKNOWN", reason, status)

    if type(binding) is not SimulationCalendarBinding:
        return unknown("BINDING_UNAVAILABLE")
    try:
        valid = (
            all(
                type(value) is str and value and value == value.strip()
                for value in (venue, kind, product, contract)
            )
            and kind in {"future", "option"}
            and type(owner_day) is date
            and type(at_utc) is datetime
            and at_utc.tzinfo is not None
            and at_utc.utcoffset() == timedelta(0)
        )
    except Exception:
        valid = False
    if not valid:
        return unknown("INVALID_SCOPE_OR_INSTANT")
    try:
        captured = binding.query(
            venue=venue, kind=kind, product=product, contract=contract, trading_day=owner_day
        )
    except BindingUnavailableError:
        return unknown("BINDING_UNAVAILABLE")
    try:
        result, status = captured.result, captured.binding
        if (
            result.status != "COMPLETE"
            or result.calendar_ref != status.calendar_ref
            or result.sources_verified is not False
            or (result.venue, result.kind, result.product, result.contract, result.trading_day)
            != (venue, kind, product, contract, owner_day)
            or not result.source_ids
        ):
            return unknown("OWNER_DAY_UNAVAILABLE", status)
        matches = [w for w in result.windows if w.start_utc <= at_utc < w.end_utc]
        if len(matches) != 1:
            return unknown("INSTANT_NOT_PROVEN_BY_OWNER_WINDOW", status)
        window = matches[0]
        attribution = captured.simulation_provenance
        if attribution is None:
            attribution = captured.declared_mock_provenance
        if type(attribution) is not dict or type(attribution.get("windows")) is not list:
            return unknown("WINDOW_PROVENANCE_UNAVAILABLE", status)
        sources = [
            row for row in attribution["windows"] if row.get("window_id") == window.window_id
        ]
        if (
            len(sources) != 1
            or sources[0].get("source_type") not in {"AKSHARE", "MOCK"}
            or sources[0].get("scope") not in {"DAY", "NIGHT"}
            or window.trading_day != owner_day
            or window.phase not in {"auction", "continuous"}
            or type(window.start_utc) is not datetime
            or type(window.end_utc) is not datetime
            or window.start_utc.utcoffset() != timedelta(0)
            or window.end_utc.utcoffset() != timedelta(0)
        ):
            return unknown("WINDOW_PROVENANCE_UNAVAILABLE", status)
        return BoundSessionEvaluation(
            window.phase.upper(),
            "EXPLICIT_SIMULATION_WINDOW",
            status,
            owner_day,
            window,
            deepcopy(sources[0]),
            deepcopy(attribution),
        )
    except Exception:
        return unknown("QUERY_UNAVAILABLE", captured.binding)
