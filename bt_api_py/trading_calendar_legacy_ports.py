"""Fail-closed file-backed adapters for legacy calendar ports.

These adapters prevent ``BtApi.trading_session_schedule()`` and
``BtApi.futures_expected_calendar()`` from bypassing
``configure_trading_calendar_status()`` with hardcoded 2026 constants, and ensure
that ``SIMULATION_ONLY`` / ``SYNTHETIC_MOCK`` / ``DECLARED_UNVERIFIED`` /
``NOT_PROVEN`` file snapshots are never upgraded or falsified into official
``COMPLETE`` / ``VERIFIED`` legacy results.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections.abc import Callable, Mapping
from datetime import date, datetime, timezone
from threading import RLock
from typing import TYPE_CHECKING, Any
from weakref import WeakValueDictionary

from bt_api_base.futures_expected_calendar_protocol import (
    FuturesExpectedCalendarError,
    FuturesExpectedCalendarPort,
    FuturesExpectedCalendarProviderFactory,
    FuturesExpectedCalendarRequest,
    FuturesExpectedCalendarSnapshot,
    FuturesExpectedCalendarStatus,
    SelectedFuturesExpectedCalendarRoute,
)
from bt_api_base.trading_session_protocol import (
    TradingPhase,
    TradingSessionSchedulePort,
    TradingSessionState,
    TradingWindow,
)

from bt_api_py.futures_expected_calendar_composition import (
    FuturesExpectedCalendarCompositionError,
)
from bt_api_py.trading_calendar_runtime_status import (
    _READ_BUDGET,
    TradingCalendarStatusReader,
)
from bt_api_py.trading_calendar_store import (
    CalendarStoreError,
    ReceiptConsistentCurrent,
    TradingCalendarStore,
)

if TYPE_CHECKING:
    pass

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SIMULATION_MARKERS = ("mock", "akshare", "synthetic", "simulation", "unverified", "fictional")
_SIMULATION_ORIGINS = frozenset({"SYNTHETIC_MOCK", "AKSHARE_MOCK", "MOCK", "SIMULATION_ONLY"})


def _sha256_json(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _valid_utc(value: Any, name: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _has_simulation_markers(observed: ReceiptConsistentCurrent) -> bool:
    snapshot = observed.snapshot
    if snapshot.simulation_provenance is not None:
        return True
    if snapshot.coverage_status == "DECLARED_UNVERIFIED":
        return True
    manifest_bytes = snapshot.coverage_manifest_bytes
    if type(manifest_bytes) is bytes and manifest_bytes:
        try:
            manifest = json.loads(manifest_bytes.decode("utf-8"))
        except Exception:
            manifest = None
        if isinstance(manifest, Mapping):
            schema = manifest.get("schema")
            origin = manifest.get("data_origin")
            if (
                type(schema) is str
                and schema.startswith("bt_api.calendar-simulation-coverage/")
            ) or (type(origin) is str and origin in _SIMULATION_ORIGINS):
                return True
    content = snapshot.content
    if isinstance(content, Mapping):
        sources = content.get("sources")
        if isinstance(sources, list):
            for entry in sources:
                if isinstance(entry, Mapping):
                    for key in ("id", "description"):
                        text = entry.get(key)
                        if type(text) is str:
                            lowered = text.lower()
                            if any(marker in lowered for marker in _SIMULATION_MARKERS):
                                return True
    return False


def classify_store_snapshot_provenance(observed: ReceiptConsistentCurrent) -> tuple[str, str]:
    """Return finite fail-closed reason/blocker codes for unverified file snapshots.

    Current store snapshots carry ``source_authentication == "NOT_PROVEN"`` and
    lack official verified rule provenance. Simulation/mock/multi-source
    evidence must never be falsified into an official ``COMPLETE`` or verified
    schedule result.
    """
    if _has_simulation_markers(observed):
        return (
            "calendar_simulation_source_unverified",
            "FUTURES_EXPECTED_CALENDAR_SIMULATION_SOURCE_UNVERIFIED",
        )
    return (
        "calendar_source_unverified",
        "FUTURES_EXPECTED_CALENDAR_SOURCE_UNVERIFIED",
    )


def is_legacy_ctp_trading_session_factory(factory: object) -> bool:
    """Detect the hardcoded 2026 CTP trading-session provider factory."""
    return getattr(factory, "__module__", None) == "bt_api_ctp.trading_session_schedule"


def is_legacy_ctp_trading_session_schedule(schedule: object) -> bool:
    """Detect an instance of the hardcoded 2026 CTP trading-session schedule."""
    cls = type(schedule)
    if cls.__module__ == "bt_api_ctp.trading_session_schedule" and (
        cls.__name__ == "CtpTradingSessionSchedule"
    ):
        return True
    mod = sys.modules.get("bt_api_ctp.trading_session_schedule")
    if mod is not None:
        legacy_cls = getattr(mod, "CtpTradingSessionSchedule", None)
        if isinstance(legacy_cls, type) and isinstance(schedule, legacy_cls):
            return True
    return False


def is_legacy_ctp_expected_calendar_factory(factory: object) -> bool:
    """Detect the hardcoded 2026 CTP expected-calendar provider factory."""
    return getattr(factory, "__module__", None) == "bt_api_ctp.futures_expected_calendar_provider"


def is_legacy_ctp_expected_calendar_port(port: object) -> bool:
    """Detect an instance of the hardcoded 2026 CTP expected-calendar provider."""
    cls = type(port)
    if cls.__module__ == "bt_api_ctp.futures_expected_calendar_provider" and (
        cls.__name__ == "CtpFuturesExpectedCalendarProvider"
    ):
        return True
    mod = sys.modules.get("bt_api_ctp.futures_expected_calendar_provider")
    if mod is not None:
        legacy_cls = getattr(mod, "CtpFuturesExpectedCalendarProvider", None)
        if isinstance(legacy_cls, type) and isinstance(port, legacy_cls):
            return True
    return False


class FileBackedTradingSessionSchedule(TradingSessionSchedulePort):
    """Fail-closed ``TradingSessionSchedulePort`` respecting configured file roots."""

    def __init__(
        self,
        *,
        route: str,
        root: str | None,
        reader: TradingCalendarStatusReader | None,
        token: object,
        unconfigured_reason: str = "calendar_root_unconfigured",
    ) -> None:
        self._route = route
        self._root = root
        self._reader = reader
        self._token = token
        self._unconfigured_reason = unconfigured_reason

    def session_state(self, instrument_id: str, at_utc: datetime) -> TradingSessionState:
        if type(instrument_id) is not str or not instrument_id:
            raise ValueError("instrument_id must be a non-empty string")
        at = _valid_utc(at_utc, "at_utc")
        reader = self._reader
        if type(reader) is not TradingCalendarStatusReader:
            return TradingSessionState(
                instrument_id=instrument_id,
                at_utc=at,
                phase=TradingPhase.UNKNOWN,
                trading_day=None,
                current_window=None,
                next_continuous=None,
                calendar_ref="calendar.unavailable",
                reason=self._unconfigured_reason,
            )
        if not reader.configuration_is_current(self._token):
            return TradingSessionState(
                instrument_id=instrument_id,
                at_utc=at,
                phase=TradingPhase.UNKNOWN,
                trading_day=None,
                current_window=None,
                next_continuous=None,
                calendar_ref="calendar.configuration_changed",
                reason="calendar_configuration_changed",
            )
        if self._root is None:
            return TradingSessionState(
                instrument_id=instrument_id,
                at_utc=at,
                phase=TradingPhase.UNKNOWN,
                trading_day=None,
                current_window=None,
                next_continuous=None,
                calendar_ref="calendar.unconfigured",
                reason=self._unconfigured_reason,
            )
        try:
            observed = TradingCalendarStore(self._root).read_current_with_consistent_receipts(
                budget=_READ_BUDGET
            )
        except (CalendarStoreError, OSError, ValueError, TypeError, RecursionError):
            return TradingSessionState(
                instrument_id=instrument_id,
                at_utc=at,
                phase=TradingPhase.UNKNOWN,
                trading_day=None,
                current_window=None,
                next_continuous=None,
                calendar_ref="calendar.unavailable",
                reason="calendar_current_unavailable",
            )
        if not reader.configuration_is_current(self._token):
            return TradingSessionState(
                instrument_id=instrument_id,
                at_utc=at,
                phase=TradingPhase.UNKNOWN,
                trading_day=None,
                current_window=None,
                next_continuous=None,
                calendar_ref="calendar.configuration_changed",
                reason="calendar_configuration_changed",
            )
        reason, _ = classify_store_snapshot_provenance(observed)
        version_id = observed.snapshot.version_id
        calendar_ref = (
            version_id if type(version_id) is str and version_id else "calendar.unverified"
        )
        return TradingSessionState(
            instrument_id=instrument_id,
            at_utc=at,
            phase=TradingPhase.UNKNOWN,
            trading_day=None,
            current_window=None,
            next_continuous=None,
            calendar_ref=calendar_ref,
            reason=reason,
        )

    def windows(
        self, instrument_id: str, start_utc: datetime, end_utc: datetime
    ) -> tuple[TradingWindow, ...]:
        if type(instrument_id) is not str or not instrument_id:
            raise ValueError("instrument_id must be a non-empty string")
        _valid_utc(start_utc, "start_utc")
        _valid_utc(end_utc, "end_utc")
        return ()


class FileBackedFuturesExpectedCalendarPort(FuturesExpectedCalendarPort):
    """Fail-closed ``FuturesExpectedCalendarPort`` respecting file configuration."""

    def __init__(
        self,
        route: SelectedFuturesExpectedCalendarRoute,
        *,
        root: str | None,
        reader: TradingCalendarStatusReader,
        token: object,
    ) -> None:
        self._route = route
        self._root = root
        self._reader = reader
        self._token = token
        self._issued: WeakValueDictionary[int, FuturesExpectedCalendarSnapshot] = (
            WeakValueDictionary()
        )
        self._issued_lock = RLock()

    def expected_trading_days(self, venue: str, year: int) -> tuple[date, ...]:
        if (
            type(self._reader) is not TradingCalendarStatusReader
            or not self._reader.configuration_is_current(self._token)
        ):
            raise FuturesExpectedCalendarCompositionError("FUTURES_EXPECTED_CALENDAR_ROUTE_CHANGED")
        # Neither unconfigured routes nor simulation/unverified file stores may
        # return hardcoded 2026 constants or pretend to be official trading days.
        raise FuturesExpectedCalendarCompositionError(
            "FUTURES_EXPECTED_CALENDAR_PROVIDER_UNAVAILABLE"
        )

    def validate(self, snapshot: FuturesExpectedCalendarSnapshot) -> bool:
        if type(snapshot) is not FuturesExpectedCalendarSnapshot:
            return False
        if (
            type(self._reader) is not TradingCalendarStatusReader
            or not self._reader.configuration_is_current(self._token)
        ):
            return False
        with self._issued_lock:
            return self._issued.get(id(snapshot)) is snapshot

    def build(
        self,
        request: FuturesExpectedCalendarRequest,
    ) -> FuturesExpectedCalendarSnapshot:
        if type(request) is not FuturesExpectedCalendarRequest:
            raise FuturesExpectedCalendarError("FUTURES_EXPECTED_CALENDAR_REQUEST_INVALID")
        if (
            type(self._reader) is not TradingCalendarStatusReader
            or not self._reader.configuration_is_current(self._token)
        ):
            return self._unavailable(
                request,
                "FUTURES_EXPECTED_CALENDAR_CONFIGURATION_CHANGED",
                None,
            )
        if self._root is None:
            return self._unavailable(
                request,
                "FUTURES_EXPECTED_CALENDAR_ROOT_UNCONFIGURED",
                None,
            )
        try:
            observed = TradingCalendarStore(self._root).read_current_with_consistent_receipts(
                budget=_READ_BUDGET
            )
        except (CalendarStoreError, OSError, ValueError, TypeError, RecursionError):
            return self._unavailable(
                request,
                "FUTURES_EXPECTED_CALENDAR_CURRENT_UNAVAILABLE",
                None,
            )
        if not self._reader.configuration_is_current(self._token):
            return self._unavailable(
                request,
                "FUTURES_EXPECTED_CALENDAR_CONFIGURATION_CHANGED",
                None,
            )
        _, blocker = classify_store_snapshot_provenance(observed)
        return self._unavailable(request, blocker, observed.snapshot.content_sha256)

    def _unavailable(
        self,
        request: FuturesExpectedCalendarRequest,
        blocker: str,
        content_sha256: str | None,
    ) -> FuturesExpectedCalendarSnapshot:
        manifest = (
            content_sha256
            if type(content_sha256) is str and _SHA256.fullmatch(content_sha256) is not None
            else _sha256_json(
                {
                    "scope_digest": request.scope_digest,
                    "blocker": blocker,
                }
            )
        )
        snapshot = FuturesExpectedCalendarSnapshot(
            request=request,
            status=FuturesExpectedCalendarStatus.UNAVAILABLE,
            venue_calendars=(),
            product_days=(),
            sources=(),
            fifth_future_trading_days=(),
            rules_manifest_sha256=manifest,
            blockers=(blocker,),
        )
        with self._issued_lock:
            self._issued[id(snapshot)] = snapshot
        return snapshot


def make_futures_expected_calendar_factory_resolver(
    reader: TradingCalendarStatusReader,
    token: object,
) -> Callable[
    [str, Callable[[], FuturesExpectedCalendarProviderFactory]],
    FuturesExpectedCalendarProviderFactory,
]:
    """Resolve file-backed or fail-closed expected-calendar factories per route."""

    def resolve_factory(
        exchange_name: str,
        load_default: Callable[[], FuturesExpectedCalendarProviderFactory],
    ) -> FuturesExpectedCalendarProviderFactory:
        root, current_token = reader.route_root_and_token(exchange_name)
        if current_token is not token or not reader.configuration_is_current(token):
            raise FuturesExpectedCalendarCompositionError("FUTURES_EXPECTED_CALENDAR_ROUTE_CHANGED")
        if root is not None:
            return lambda route: FileBackedFuturesExpectedCalendarPort(
                route,
                root=root,
                reader=reader,
                token=token,
            )
        default_factory = load_default()
        if is_legacy_ctp_expected_calendar_factory(default_factory):
            return lambda route: FileBackedFuturesExpectedCalendarPort(
                route,
                root=None,
                reader=reader,
                token=token,
            )

        def guarded_factory(
            route: SelectedFuturesExpectedCalendarRoute,
        ) -> FuturesExpectedCalendarPort:
            delegate = default_factory(route)
            if is_legacy_ctp_expected_calendar_port(delegate):
                return FileBackedFuturesExpectedCalendarPort(
                    route,
                    root=None,
                    reader=reader,
                    token=token,
                )
            return delegate

        return guarded_factory

    return resolve_factory


__all__ = [
    "FileBackedFuturesExpectedCalendarPort",
    "FileBackedTradingSessionSchedule",
    "classify_store_snapshot_provenance",
    "is_legacy_ctp_expected_calendar_factory",
    "is_legacy_ctp_expected_calendar_port",
    "is_legacy_ctp_trading_session_factory",
    "is_legacy_ctp_trading_session_schedule",
    "make_futures_expected_calendar_factory_resolver",
]
