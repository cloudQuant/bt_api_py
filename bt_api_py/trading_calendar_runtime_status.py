"""Explicit read-only status of a locally published calendar.

This is an observation, not a runtime binding or source authentication. The
caller must provide absolute local roots and exact route names. CURRENT can
change as soon as the store lock is released. No accounts or networks are used.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass
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


class TradingCalendarStatusReader:
    """Per-instance explicit route map; reconfiguration invalidates in-flight reads."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._generation = 0
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
