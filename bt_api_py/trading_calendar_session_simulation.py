"""Explicit simulation-only phase evaluation against one pinned calendar.

This is not a TradingSessionSchedulePort, research snapshot, account fact or
order gate. The caller supplies the exact venue, kind, product, contract and
owner day; no instrument identity, session ID or trading day is inferred.
An instant outside the supplied owner's explicit windows remains UNKNOWN: an
empty result cannot establish a real closure or exclude another owner day.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from bt_api_base.trading_session_protocol import TradingPhase
from bt_api_ctp.calendar_snapshot import CalendarSnapshot

from bt_api_py.trading_calendar_binding import SIMULATION_ONLY, _covered_source
from bt_api_py.trading_calendar_query_simulation import _MAX_BYTES, _bounded_content


@dataclass(frozen=True)
class SimulationSessionWindow:
    """An original, unclipped template window; its ID is not a session ID."""

    declared_window_id: str
    session_scope: str  # DAY or NIGHT, taken from the explicit source layer
    phase: TradingPhase
    owner_day: date
    start_utc: datetime
    end_utc: datetime
    start_local: datetime
    end_local: datetime
    source_ids: tuple[str, ...]


@dataclass(frozen=True)
class SimulationSessionEvaluation:
    """A phase comparison for one declared owner, never a legacy signed result."""

    mode: str
    venue: str
    kind: str
    product: str
    contract: str
    requested_owner_day: date
    at_utc: datetime
    phase: TradingPhase
    trading_day: date | None
    current_window: SimulationSessionWindow | None
    calendar_ref: str | None
    content_sha256: str | None
    version_sha256: str | None
    reason: str
    source_authentication: str = "NOT_PROVEN"
    publication: str = "NOT_AUTHORIZED"
    identity: str = "CALLER_DECLARED_NOT_PROVEN"


class SimulationSessionEvaluator:
    """Read one covered store snapshot once, and retain that detached version.

    ``evaluate`` does not return an old TradingSessionState or TradingWindow:
    those types require legacy session IDs and additional semantics not supplied
    by this slice. A later CURRENT change cannot change this instance's result.
    """

    def __init__(self, *, mode, store):
        if type(mode) is not str or mode != SIMULATION_ONLY:
            raise ValueError("explicit SIMULATION_ONLY mode required")
        self._snapshot = None
        self._rules = ()
        self._coverage = ()
        self._calendar_ref = None
        self._content_sha256 = None
        self._version_sha256 = None
        try:
            stored = store.read_current()
            _bounded_content(stored.content)
            if any(
                type(value) is not bytes or len(value) > _MAX_BYTES
                for value in (stored.coverage_manifest_bytes, stored.binding_bytes)
            ):
                raise ValueError("artifact limit")
            coverage, content_bytes = _covered_source(stored)
            if len(content_bytes) > _MAX_BYTES:
                raise ValueError("snapshot limit")
            content = json.loads(content_bytes)
            _bounded_content(content)
            snapshot = CalendarSnapshot(content, calendar_ref=stored.version_id)
            rules = tuple(
                (
                    (row["venue"], row["kind"], row["product"]),
                    tuple(row["contracts"]),
                    date.fromisoformat(row["valid_from"]),
                    date.fromisoformat(row["valid_to"]),
                    tuple(
                        (item["id"], scope)
                        for key, scope in (("day_windows", "DAY"), ("night_windows", "NIGHT"))
                        for item in row[key]
                    ),
                )
                for row in content["trading_hours"]
            )
            self._snapshot = snapshot
            self._rules = rules
            self._coverage = coverage
            self._calendar_ref = stored.version_id
            self._content_sha256 = stored.content_sha256
            self._version_sha256 = stored.version_sha256
        except Exception:
            # No fallbacks, second read, or synthesized version on load failure.
            return

    def evaluate(self, *, venue, kind, product, contract, owner_day, at_utc):
        """Evaluate one exact owner day at a UTC instant with half-open bounds."""

        def unknown(reason):
            return SimulationSessionEvaluation(
                SIMULATION_ONLY,
                venue,
                kind,
                product,
                contract,
                owner_day,
                at_utc,
                TradingPhase.UNKNOWN,
                None,
                None,
                self._calendar_ref,
                self._content_sha256,
                self._version_sha256,
                reason,
            )

        if self._snapshot is None:
            return unknown("SNAPSHOT_UNAVAILABLE")
        try:
            valid = not (
                any(
                    type(value) is not str or not value
                    for value in (venue, kind, product, contract)
                )
                or kind not in {"future", "option"}
                or type(owner_day) is not date
                or type(at_utc) is not datetime
                or at_utc.tzinfo is None
                or at_utc.utcoffset() != timedelta(0)
            )
        except Exception:
            valid = False
        if not valid:
            return unknown("INVALID_SCOPE_OR_INSTANT")
        coverage = next((entry for entry in self._coverage if entry.venue == venue), None)
        if coverage is None or not coverage.start_day <= owner_day <= coverage.end_day:
            return unknown("COVERAGE_UNAVAILABLE")
        try:
            rows = [
                row
                for row in self._rules
                if row[0] == (venue, kind, product)
                and contract in row[1]
                and row[2] <= owner_day <= row[3]
            ]
            if len(rows) != 1:
                return unknown("CONTRACT_OR_RULE_UNAVAILABLE")
            result = self._snapshot.query(
                venue=venue, kind=kind, product=product, contract=contract, trading_day=owner_day
            )
            if (
                result.status != "COMPLETE"
                or result.calendar_ref != self._calendar_ref
                or result.sources_verified is not False
                or (result.venue, result.kind, result.product, result.contract, result.trading_day)
                != (venue, kind, product, contract, owner_day)
                or not result.source_ids
            ):
                return unknown("OWNER_DAY_UNAVAILABLE")
            matched = [window for window in result.windows if window.contains(at_utc)]
            if len(matched) != 1:
                return unknown("INSTANT_NOT_PROVEN_BY_OWNER_WINDOW")
            window = matched[0]
            names = [item for item in rows[0][4] if item[0] == window.window_id]
            if (
                len(names) != 1
                or window.trading_day != owner_day
                or window.phase not in {"continuous", "auction"}
            ):
                return unknown("WINDOW_MAPPING_UNAVAILABLE")
            phase = (
                TradingPhase.CONTINUOUS if window.phase == "continuous" else TradingPhase.AUCTION
            )
            projected = SimulationSessionWindow(
                window.window_id,
                names[0][1],
                phase,
                owner_day,
                window.start_utc,
                window.end_utc,
                window.start_local,
                window.end_local,
                result.source_ids,
            )
            return SimulationSessionEvaluation(
                SIMULATION_ONLY,
                venue,
                kind,
                product,
                contract,
                owner_day,
                at_utc,
                phase,
                owner_day,
                projected,
                self._calendar_ref,
                self._content_sha256,
                self._version_sha256,
                "EXPLICIT_SIMULATION_WINDOW",
            )
        except Exception:
            return unknown("QUERY_UNAVAILABLE")
