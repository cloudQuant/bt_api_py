"""Explicit read-only status of a locally published calendar.

This is an observation, not a runtime binding or source authentication. The
caller must provide absolute local roots and exact route names. CURRENT can
change as soon as the store lock is released. No accounts or networks are used.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from bt_api_py.trading_calendar_store import (
    CalendarReadBudget,
    CalendarStoreError,
    TradingCalendarStore,
)

_ROUTE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_READ_BUDGET = CalendarReadBudget(8 * 1024 * 1024, 64 * 1024 * 1024, 2048)


@dataclass(frozen=True)
class TradingCalendarStatus:
    status: str
    reason: str
    published: str = "UNKNOWN"
    publication_id: str | None = None
    publication_sha256: str | None = None
    version_id: str | None = None
    content_sha256: str | None = None
    coverage_status: str = "UNKNOWN"
    bound: str = "NOT_BOUND"
    binding_reason: str = "RUNTIME_BINDING_NOT_IMPLEMENTED"
    source_authentication: str = "NOT_PROVEN"
    identity_authentication: str = "NOT_PROVEN"
    route_mapping: str = "CALLER_DECLARED_ONLY"
    publication_authentication: str = "NOT_PROVEN"
    durability: str = "UNCONFIRMED"
    observation: str = "CURRENT_NOT_PINNED"


@dataclass(frozen=True)
class TradingCalendarQueryWindow:
    window_id: str
    phase: str
    trading_day: date
    start_utc: datetime
    end_utc: datetime
    start_local: datetime
    end_local: datetime
    interval_semantics: str = "START_INCLUSIVE_END_EXCLUSIVE"


@dataclass(frozen=True)
class TradingCalendarCurrentQuery:
    """Simulation-only observation; source IDs describe the whole result, not each window.

    Legacy DECLARED_MOCK reflects an included ID, not verified identity. For
    a mixed snapshot, simulation_provenance contains only the queried windows
    within its target scope; source_ids remain whole-result references. An empty
    COMPLETE result is not a closure proof.
    """

    status: str
    reason: str
    windows: tuple[TradingCalendarQueryWindow, ...] = ()
    calendar_ref: str | None = None
    publication_id: str | None = None
    content_sha256: str | None = None
    version_sha256: str | None = None
    coverage_status: str = "UNKNOWN"
    source_authentication: str = "NOT_PROVEN"
    identity_authentication: str = "NOT_PROVEN"
    publication_authentication: str = "NOT_PROVEN"
    route_mapping: str = "CALLER_DECLARED_ONLY"
    bound: str = "NOT_BOUND"
    observation: str = "CURRENT_NOT_PINNED"
    trading_day_transition: str = "NOT_PROVEN"
    environment: str = "SIMULATION_ONLY"
    account_use: str = "NOT_AUTHORIZED"
    provider: str = "CTP"
    provider_selection: str = "EXPLICIT_CTP_ONLY_ROUTE_UNVERIFIED"
    source_ids: tuple[str, ...] = ()
    source_classification: str = "UNVERIFIED"
    data_origin: str = "UNSPECIFIED"
    simulation_provenance: dict | None = None


class TradingCalendarStatusReader:
    """Per-instance explicit route map; reconfiguration invalidates in-flight reads."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._generation = 0
        self._configuration_token = object()
        self._roots: dict[str, str] = {}

    def configure(self, roots: Mapping[str, str]) -> None:
        if not isinstance(roots, Mapping):
            raise ValueError("calendar route roots must be a mapping")
        captured: dict[str, str] = {}
        for route, root in roots.items():
            if type(route) is not str or not _ROUTE.fullmatch(route):
                raise ValueError("invalid calendar route")
            if type(root) is not str or not root or "\x00" in root:
                raise ValueError("invalid calendar root")
            path = Path(root)
            if not path.is_absolute() or ".." in path.parts:
                raise ValueError("calendar root must be absolute")
            captured[route] = str(path)
        with self._lock:
            self._roots = captured
            self._generation += 1
            self._configuration_token = object()

    def configuration_token(self) -> object:
        """Observe this reader's opaque configuration token, not an authorization or binding."""
        with self._lock:
            return self._configuration_token

    def configuration_is_current(self, token: object) -> bool:
        """Check that a token came from this instance and predates no reconfiguration."""
        with self._lock:
            return token is self._configuration_token

    def status(self, route: str) -> TradingCalendarStatus:
        if type(route) is not str or not _ROUTE.fullmatch(route):
            return TradingCalendarStatus("UNKNOWN", "INVALID_ROUTE")
        with self._lock:
            root = self._roots.get(route)
            generation = self._generation
        if root is None:
            return TradingCalendarStatus("UNKNOWN", "ROUTE_UNCONFIGURED")
        try:
            observed = TradingCalendarStore(root).read_current_with_consistent_receipts(
                budget=_READ_BUDGET
            )
            snapshot = observed.snapshot
        except (CalendarStoreError, OSError, ValueError, TypeError, RecursionError):
            return TradingCalendarStatus("UNKNOWN", "CURRENT_UNAVAILABLE")
        with self._lock:
            if generation != self._generation:
                return TradingCalendarStatus("UNKNOWN", "CONFIGURATION_CHANGED")
        return TradingCalendarStatus(
            "OBSERVED_UNAUTHENTICATED",
            "CURRENT_RECEIPTS_CONSISTENT",
            published="CURRENT_OBSERVED",
            publication_id=snapshot.publication_id,
            publication_sha256=snapshot.publication_sha256,
            version_id=snapshot.version_id,
            content_sha256=snapshot.content_sha256,
            coverage_status=snapshot.coverage_status,
        )

    def query(
        self,
        route: str,
        *,
        venue: str,
        kind: str,
        product: str,
        contract: str,
        trading_day: date,
        mode: str | None = None,
    ) -> TradingCalendarCurrentQuery:
        """Query one explicitly specified day in CURRENT, without pinning or authority.

        Route selects a configured root, not a verified provider identity. Coverage
        and binding are simulation-only declarations, not source authentication.
        CURRENT may change after this observation; this is not a day-switch event.
        """
        if type(mode) is not str or mode != "SIMULATION_ONLY":
            return TradingCalendarCurrentQuery("UNKNOWN", "SIMULATION_MODE_REQUIRED")
        if type(route) is not str or not _ROUTE.fullmatch(route):
            return TradingCalendarCurrentQuery("UNKNOWN", "INVALID_ROUTE")
        if (
            any(
                type(v) is not str or not _ROUTE.fullmatch(v)
                for v in (venue, kind, product, contract)
            )
            or type(trading_day) is not date
        ):
            return TradingCalendarCurrentQuery("UNKNOWN", "INVALID_QUERY_SCOPE")
        with self._lock:
            root = self._roots.get(route)
            generation = self._generation
        if root is None:
            return TradingCalendarCurrentQuery("UNKNOWN", "ROUTE_UNCONFIGURED")
        result = self._query_current(root, venue, kind, product, contract, trading_day)
        with self._lock:
            if generation != self._generation:
                return TradingCalendarCurrentQuery("UNKNOWN", "CONFIGURATION_CHANGED")
        return result

    @staticmethod
    def _query_current(root, venue, kind, product, contract, trading_day):
        try:
            stored = (
                TradingCalendarStore(root)
                .read_current_with_consistent_receipts(budget=_READ_BUDGET)
                .snapshot
            )
        except (CalendarStoreError, OSError, ValueError, TypeError, RecursionError):
            return TradingCalendarCurrentQuery("UNKNOWN", "CURRENT_UNAVAILABLE")
        try:
            # This helper also bounds the work required by the provider constructor.
            # Import lazily: a status-only installation does not require CTP.
            from bt_api_ctp.calendar_snapshot import CalendarSnapshot

            from bt_api_py.trading_calendar_query_simulation import _bounded_content
        except ImportError:
            return TradingCalendarCurrentQuery("UNKNOWN", "PROVIDER_UNAVAILABLE")
        try:
            from bt_api_py.trading_calendar_binding import _canonical, _covered_source

            _bounded_content(stored.content)
            coverage, content_bytes = _covered_source(stored)
            declared = next((item for item in coverage if item.venue == venue), None)
            if declared is None or not declared.start_day <= trading_day <= declared.end_day:
                return TradingCalendarCurrentQuery("UNKNOWN", "COVERAGE_UNAVAILABLE")
            snapshot = CalendarSnapshot(stored.content, calendar_ref=stored.version_id)
            if _canonical(stored.content) != content_bytes:
                raise ValueError("snapshot content changed")
            observed = snapshot.query(
                venue=venue, kind=kind, product=product, contract=contract, trading_day=trading_day
            )
            if (
                observed.calendar_ref != stored.version_id
                or observed.sources_verified is not False
                or observed.status not in ("COMPLETE", "UNKNOWN", "OUT_OF_SCOPE")
                or type(observed.source_ids) is not tuple
                or any(
                    type(source) is not str or not _ROUTE.fullmatch(source)
                    for source in observed.source_ids
                )
            ):
                raise ValueError("invalid provider result")
            if observed.status != "COMPLETE":
                return TradingCalendarCurrentQuery("UNKNOWN", "SCOPE_OR_DATA_UNAVAILABLE")
            windows = tuple(
                TradingCalendarQueryWindow(
                    w.window_id,
                    w.phase,
                    w.trading_day,
                    w.start_utc,
                    w.end_utc,
                    w.start_local,
                    w.end_local,
                )
                for w in observed.windows
            )
            provenance = stored.simulation_provenance
            attribution = None
            classification = (
                "DECLARED_MOCK" if "MOCK.simulation_only" in observed.source_ids else "UNVERIFIED"
            )
            if provenance is not None:
                # Source IDs combine day, rule, mapping and exception references;
                # they cannot establish attribution for an individual window.
                classification = (
                    "DECLARED_MOCK"
                    if set(observed.source_ids) == {"MOCK.simulation_only"}
                    else "UNVERIFIED"
                )
                scopes = (
                    provenance["scopes"]
                    if provenance["schema"]
                    in {
                        "bt_api.calendar-mixed-scopes-provenance/1",
                        "bt_api.calendar-mixed-scopes-provenance/2",
                    }
                    else [provenance]
                )
                matches = [
                    item
                    for item in scopes
                    if (venue, kind, product, contract)
                    == (
                        item["target"]["venue"],
                        item["target"]["kind"],
                        item["target"]["product"],
                        item["target"]["mock_contract"],
                    )
                    and item["target"]["valid_from"]
                    <= trading_day.isoformat()
                    <= item["target"]["valid_to"]
                ]
                if len(matches) > 1:
                    raise ValueError("ambiguous simulation attribution")
                if matches:
                    selected = matches[0]
                    names = {window.window_id for window in windows}
                    attribution = deepcopy(
                        {key: value for key, value in selected.items() if key != "windows"}
                    )
                    attribution["windows"] = deepcopy(
                        [row for row in selected["windows"] if row["window_id"] in names]
                    )
                    if {row["window_id"] for row in attribution["windows"]} != names:
                        raise ValueError("incomplete window attribution")
                    classification = "AKSHARE_MOCK"
            return TradingCalendarCurrentQuery(
                "COMPLETE",
                "EXPLICIT_DATA",
                windows,
                stored.version_id,
                stored.publication_id,
                stored.content_sha256,
                stored.version_sha256,
                stored.coverage_status,
                source_ids=observed.source_ids,
                source_classification=classification,
                data_origin="AKSHARE_MOCK" if provenance is not None else "UNSPECIFIED",
                simulation_provenance=attribution,
            )
        except Exception:
            # Do not expose paths, provider exception text, or malformed contents.
            return TradingCalendarCurrentQuery("UNKNOWN", "CALENDAR_UNAVAILABLE")
