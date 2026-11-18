"""Explicit pre-bound TD catalog, MD membership and calendar fact resolution.

A point-in-time ProductID or MD membership snapshot proves only the instant it
was verified. This coordinator refuses to backfill historical BEGIN episodes
whose time, observation sequence, source epoch, MD generation, trading day, or
membership predates or mismatches an established binding.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from bt_api_base.market_protocol import MarketInstrumentKey, MarketSubscriptionKey
from bt_api_base.market_recovery_observation import RecoveryObservationMember
from bt_api_monitor.recovery_duration import CalendarWindow, RecoveryCalendarFact
from bt_api_monitor.recovery_pending_fact import RecoveryPendingFactResponse

from bt_api_py.gateway_instrument_product_client import (
    GatewayInstrumentProductClient,
    InstrumentProductClientError,
    InstrumentProductResult,
)
from bt_api_py.gateway_monitor_calendar_client import (
    GatewayMonitorCalendarClient,
    MonitorCalendarClientError,
    MonitorCalendarWindowsResult,
)

_MAX_INT = (1 << 63) - 1
_EPOCH_UTC = datetime(1970, 1, 1, tzinfo=UTC)
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_HEX32 = re.compile(r"[0-9a-f]{32}\Z")


class RecoveryFactResolutionError(RuntimeError):
    """Finite failure without source payload, endpoints, or raw exceptions."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _text(value: object) -> bool:
    return (
        type(value) is str
        and bool(value)
        and value == value.strip()
        and value.isprintable()
        and len(value.encode("utf-8", "strict")) <= 256
    )


def _canonical(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _parse_utc_ns(value: object) -> int:
    if type(value) is not str or not value.endswith("Z"):
        raise RecoveryFactResolutionError("INVALID_UTC_TIMESTAMP")
    try:
        dt = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise RecoveryFactResolutionError("INVALID_UTC_TIMESTAMP") from None
    if dt.utcoffset() != timedelta(0):
        raise RecoveryFactResolutionError("INVALID_UTC_TIMESTAMP")
    delta = dt - _EPOCH_UTC
    return (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1_000


def _floor_utc_iso(ns: int) -> tuple[int, str]:
    us = ns // 1_000
    dt = _EPOCH_UTC + timedelta(microseconds=us)
    return us * 1_000, dt.isoformat().replace("+00:00", "Z")


def _ceil_utc_iso(ns: int) -> tuple[int, str]:
    us = (ns + 999) // 1_000
    dt = _EPOCH_UTC + timedelta(microseconds=us)
    return us * 1_000, dt.isoformat().replace("+00:00", "Z")


def _probe_window_utc(trading_day: str) -> tuple[str, str]:
    day = date.fromisoformat(trading_day)
    start_local = datetime(day.year, day.month, day.day, 12, 0, tzinfo=_SHANGHAI)
    start_utc = start_local.astimezone(UTC)
    end_utc = start_utc + timedelta(minutes=1)
    return (
        start_utc.isoformat().replace("+00:00", "Z"),
        end_utc.isoformat().replace("+00:00", "Z"),
    )


def _calendar_version_from_binding(cal_binding: object) -> str:
    ref = cal_binding["calendar_ref"]
    sha = cal_binding["version_sha256"]
    version = f"{ref}:{sha}"
    if not _text(version):
        raise RecoveryFactResolutionError("CALENDAR_BINDING_INVALID")
    return version


@dataclass(frozen=True, slots=True)
class RecoveryMdMembershipBinding:
    """Verified MD handle membership snapshot bound before a future BEGIN."""

    source_ref: str
    source_epoch: str
    md_generation: int
    after_observation_sequence: int
    members: tuple[RecoveryObservationMember, ...]
    verify_current: Callable[[], bool] | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if (
            not _text(self.source_ref)
            or type(self.source_epoch) is not str
            or _HEX32.fullmatch(self.source_epoch) is None
            or type(self.md_generation) is not int
            or not 1 <= self.md_generation <= _MAX_INT
            or type(self.after_observation_sequence) is not int
            or not 0 <= self.after_observation_sequence <= _MAX_INT
            or type(self.members) is not tuple
            or not 1 <= len(self.members) <= 1024
            or any(type(member) is not RecoveryObservationMember for member in self.members)
            or (self.verify_current is not None and not callable(self.verify_current))
        ):
            raise RecoveryFactResolutionError("INVALID_MD_MEMBERSHIP_BINDING")
        keys = tuple((member.key.stream_id, member.generation) for member in self.members)
        if keys != tuple(sorted(set(keys))):
            raise RecoveryFactResolutionError("INVALID_MD_MEMBERSHIP_BINDING")

    def member_for(self, key: MarketSubscriptionKey) -> RecoveryObservationMember:
        if type(key) is not MarketSubscriptionKey:
            raise RecoveryFactResolutionError("INVALID_MEMBER_KEY")
        matches = [member for member in self.members if member.key == key]
        if not matches:
            raise RecoveryFactResolutionError("MEMBERSHIP_MEMBER_MISSING")
        if len(matches) != 1:
            raise RecoveryFactResolutionError("MEMBERSHIP_MEMBER_AMBIGUOUS")
        return matches[0]


@dataclass(frozen=True, slots=True)
class FutureBeginFactBinding:
    """Immutable pre-BEGIN binding of TD catalog ProductID, MD membership, and calendar."""

    consumer_id: str
    source_ref: str
    source_epoch: str
    md_generation: int
    after_observation_sequence: int
    member_key: MarketSubscriptionKey
    member_generation: str
    members: tuple[RecoveryObservationMember, ...]
    route: str
    product_id: str
    session_generation: str
    connection_generation: int
    trading_day: str
    product_completed_at_utc_ns: int
    established_at_utc_ns: int
    calendar_version: str
    calendar_source_generation: str
    data_origin: str
    policy_declaration: str
    source_authentication: str
    mapping_version: str
    binding_digest: str


@dataclass(frozen=True, slots=True)
class FactResolutionResult:
    """Finite outcome of one pending-fact resolution attempt."""

    status: str
    reason: str
    request_id: str | None = None
    consumer_id: str | None = None
    mapping_version: str | None = None
    calendar_version: str | None = None
    permanent_refusal: bool = False


class RecoveryFactResolutionCoordinator:
    """Owner-thread coordinator resolving future-BEGIN facts without historical backfill."""

    def __init__(
        self,
        *,
        product_client: GatewayInstrumentProductClient,
        calendar_client: GatewayMonitorCalendarClient,
        route: str,
        expected_session_generation: str | None = None,
        expected_trading_day: str | None = None,
        verify_product_on_resolve: bool = True,
        product_timeout_seconds: float = 5.0,
        poll_interval_seconds: float = 0.02,
        clock_utc_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        self._pid = os.getpid()
        self._thread = threading.current_thread()
        if (
            type(product_client) is not GatewayInstrumentProductClient
            or type(calendar_client) is not GatewayMonitorCalendarClient
            or product_client._thread is not self._thread
            or calendar_client._thread is not self._thread
            or product_client._pid != self._pid
            or calendar_client._pid != self._pid
            or not _text(route)
            or (
                expected_session_generation is not None
                and not _text(expected_session_generation)
            )
            or (expected_trading_day is not None and not _text(expected_trading_day))
            or type(verify_product_on_resolve) is not bool
            or type(product_timeout_seconds) not in (int, float)
            or not math.isfinite(product_timeout_seconds)
            or not 0.05 <= product_timeout_seconds <= 60.0
            or type(poll_interval_seconds) not in (int, float)
            or not math.isfinite(poll_interval_seconds)
            or not 0.001 <= poll_interval_seconds <= 1.0
            or not callable(clock_utc_ns)
        ):
            raise RecoveryFactResolutionError("INVALID_FACT_COORDINATOR_CONFIG")
        if expected_trading_day is not None:
            try:
                if date.fromisoformat(expected_trading_day).isoformat() != expected_trading_day:
                    raise ValueError
            except ValueError:
                raise RecoveryFactResolutionError("INVALID_FACT_COORDINATOR_CONFIG") from None
        self._product_client = product_client
        self._calendar_client = calendar_client
        self._route = route
        self._expected_session_generation = expected_session_generation
        self._expected_trading_day = expected_trading_day
        self._verify_product_on_resolve = verify_product_on_resolve
        self._product_timeout_seconds = float(product_timeout_seconds)
        self._poll_interval_seconds = float(poll_interval_seconds)
        self._clock_utc_ns = clock_utc_ns
        self._bindings: dict[str, FutureBeginFactBinding] = {}
        self._closed = False

    def _check_owner(self) -> None:
        if os.getpid() != self._pid or threading.current_thread() is not self._thread:
            raise RecoveryFactResolutionError("WRONG_OWNER")
        if self._closed:
            raise RecoveryFactResolutionError("COORDINATOR_CLOSED")

    def binding_for(self, consumer_id: str) -> FutureBeginFactBinding | None:
        self._check_owner()
        return self._bindings.get(consumer_id)

    def _query_product(self, instrument: MarketInstrumentKey) -> InstrumentProductResult:
        op_id = uuid4().hex
        terminal = False
        try:
            initial = self._product_client.begin(op_id, instrument)
            if initial.status in {"ready", "failed", "cancelled", "not_found"}:
                terminal = True
                result = initial
            else:
                deadline = time.monotonic() + self._product_timeout_seconds
                while True:
                    result = self._product_client.status(op_id)
                    if result.status != "pending":
                        terminal = True
                        break
                    if time.monotonic() >= deadline:
                        with suppress(Exception):
                            self._product_client.cancel(op_id)
                            terminal = True
                        raise RecoveryFactResolutionError("PRODUCT_QUERY_TIMEOUT")
                    time.sleep(self._poll_interval_seconds)
        except RecoveryFactResolutionError:
            raise
        except InstrumentProductClientError:
            raise RecoveryFactResolutionError("PRODUCT_UNAVAILABLE") from None
        finally:
            if terminal:
                with suppress(Exception):
                    self._product_client.retire(op_id)
        if (
            result.status != "ready"
            or result.instrument != instrument
            or not _text(result.product_id)
            or not _text(result.session_generation)
            or type(result.connection_generation) is not int
            or result.connection_generation <= 0
            or not _text(result.trading_day)
            or type(result.valid_until_monotonic) not in (int, float)
            or not math.isfinite(result.valid_until_monotonic)
            or time.monotonic() >= result.valid_until_monotonic
        ):
            raise RecoveryFactResolutionError("PRODUCT_UNAVAILABLE")
        return result

    def _query_calendar(
        self,
        *,
        instrument: MarketInstrumentKey,
        product_id: str,
        start_utc: str,
        end_utc: str,
    ) -> MonitorCalendarWindowsResult:
        try:
            result = self._calendar_client.windows(
                route=self._route,
                venue=instrument.venue,
                kind=instrument.product_type,
                product=product_id,
                contract=instrument.instrument_id,
                start_utc=start_utc,
                end_utc=end_utc,
            )
        except MonitorCalendarClientError:
            raise RecoveryFactResolutionError("CALENDAR_UNAVAILABLE") from None
        if (
            result.status != "COMPLETE"
            or result.reason != "OK"
            or result.binding is None
            or result.policy is None
            or result.binding.get("load_status") != "CURRENT"
            or result.source_authentication != "NOT_PROVEN"
            or result.policy.get("declaration") != "DEPLOYMENT_DECLARED_UNVERIFIED"
            or result.binding.get("data_origin")
            not in ("UNSPECIFIED", "AKSHARE_MOCK", "SYNTHETIC_MOCK")
        ):
            raise RecoveryFactResolutionError("CALENDAR_UNAVAILABLE")
        return result

    def establish_binding(
        self,
        *,
        consumer_id: str,
        member_key: MarketSubscriptionKey,
        membership: RecoveryMdMembershipBinding,
    ) -> FutureBeginFactBinding:
        """Verify TD catalog ProductID, MD membership and calendar before a future BEGIN."""
        self._check_owner()
        if (
            not _text(consumer_id)
            or type(member_key) is not MarketSubscriptionKey
            or type(membership) is not RecoveryMdMembershipBinding
        ):
            raise RecoveryFactResolutionError("INVALID_BINDING_ARGUMENT")
        target = membership.member_for(member_key)
        if membership.verify_current is not None and membership.verify_current() is not True:
            raise RecoveryFactResolutionError("MEMBERSHIP_UNAVAILABLE")

        product_result = self._query_product(member_key.instrument)
        if (
            self._expected_session_generation is not None
            and product_result.session_generation != self._expected_session_generation
        ):
            raise RecoveryFactResolutionError("SESSION_GENERATION_MISMATCH")
        if (
            self._expected_trading_day is not None
            and product_result.trading_day != self._expected_trading_day
        ):
            raise RecoveryFactResolutionError("TRADING_DAY_MISMATCH")
        product_completed_ns = _parse_utc_ns(product_result.completed_at_utc)

        probe_start, probe_end = _probe_window_utc(product_result.trading_day)
        cal_result = self._query_calendar(
            instrument=member_key.instrument,
            product_id=product_result.product_id,
            start_utc=probe_start,
            end_utc=probe_end,
        )
        if cal_result.binding["bound_trading_day"] != product_result.trading_day:
            raise RecoveryFactResolutionError("TRADING_DAY_MISMATCH")

        if time.monotonic() >= product_result.valid_until_monotonic:
            raise RecoveryFactResolutionError("PRODUCT_OBSERVATION_EXPIRED")
        if membership.verify_current is not None and membership.verify_current() is not True:
            raise RecoveryFactResolutionError("MEMBERSHIP_UNAVAILABLE")

        utc_now_ns = self._clock_utc_ns()
        if type(utc_now_ns) is not int or utc_now_ns <= 0:
            raise RecoveryFactResolutionError("CLOCK_UNAVAILABLE")
        established_at_utc_ns = max(product_completed_ns, utc_now_ns)

        calendar_version = _calendar_version_from_binding(cal_result.binding)
        mapping_payload = {
            "source_ref": membership.source_ref,
            "source_epoch": membership.source_epoch,
            "md_generation": membership.md_generation,
            "after_observation_sequence": membership.after_observation_sequence,
            "member_key": member_key.to_dict(),
            "member_generation": target.generation,
            "members": [
                {"key": item.key.to_dict(), "generation": item.generation}
                for item in membership.members
            ],
            "product_id": product_result.product_id,
            "session_generation": product_result.session_generation,
            "connection_generation": product_result.connection_generation,
            "trading_day": product_result.trading_day,
            "established_at_utc_ns": established_at_utc_ns,
        }
        mapping_digest = hashlib.sha256(_canonical(mapping_payload).encode("ascii")).hexdigest()
        mapping_version = f"td-md-map:{mapping_digest[:32]}"
        binding_digest = hashlib.sha256(
            _canonical(
                {
                    "consumer_id": consumer_id,
                    "mapping_version": mapping_version,
                    "calendar_version": calendar_version,
                }
            ).encode("ascii")
        ).hexdigest()
        binding = FutureBeginFactBinding(
            consumer_id=consumer_id,
            source_ref=membership.source_ref,
            source_epoch=membership.source_epoch,
            md_generation=membership.md_generation,
            after_observation_sequence=membership.after_observation_sequence,
            member_key=MarketSubscriptionKey.from_dict(member_key.to_dict()),
            member_generation=target.generation,
            members=membership.members,
            route=self._route,
            product_id=product_result.product_id,
            session_generation=product_result.session_generation,
            connection_generation=product_result.connection_generation,
            trading_day=product_result.trading_day,
            product_completed_at_utc_ns=product_completed_ns,
            established_at_utc_ns=established_at_utc_ns,
            calendar_version=calendar_version,
            calendar_source_generation=cal_result.binding["source_generation"],
            data_origin=cal_result.binding["data_origin"],
            policy_declaration=cal_result.policy["declaration"],
            source_authentication=cal_result.source_authentication,
            mapping_version=mapping_version,
            binding_digest=binding_digest,
        )
        self._bindings[consumer_id] = binding
        return binding

    @staticmethod
    def _validate_request_against_binding(
        request: object, binding: FutureBeginFactBinding
    ) -> str | None:
        if request.source_ref != binding.source_ref:
            return "SOURCE_REF_MISMATCH"
        if request.source_epoch != binding.source_epoch:
            return "SOURCE_EPOCH_MISMATCH"
        if request.member_key != binding.member_key:
            return "MEMBER_KEY_MISMATCH"
        if request.member_generation != binding.member_generation:
            return "MEMBER_GENERATION_MISMATCH"
        if request.episode_members is None or request.episode_members != binding.members:
            return "MEMBERSHIP_MISMATCH"
        if (
            request.origin_connection_generation is None
            or request.origin_connection_generation != binding.md_generation
        ):
            return "MD_GENERATION_MISMATCH"
        if (
            request.begin_observation_sequence is None
            or request.begin_observation_sequence <= binding.after_observation_sequence
        ):
            return "HISTORICAL_BEGIN_SEQUENCE_PREDATES_BINDING"
        if (
            type(request.start_utc_lower_ns) is not int
            or type(request.start_utc_upper_ns) is not int
            or type(request.end_utc_lower_ns) is not int
            or not (
                request.start_utc_lower_ns
                <= request.start_utc_upper_ns
                <= request.end_utc_lower_ns
            )
        ):
            return "INTERVAL_INVALID"
        if request.start_utc_lower_ns < binding.established_at_utc_ns:
            return "HISTORICAL_BEGIN_PREDATES_BINDING"
        return None

    def resolve_pending_once(self, consumer_id: str, consumer: object) -> FactResolutionResult:
        """Resolve one pending head only when the episode's BEGIN is covered by its binding."""
        self._check_owner()
        binding = self._bindings.get(consumer_id)
        if binding is None:
            return FactResolutionResult(
                status="REFUSED",
                reason="NO_PREBOUND_FACT_BINDING",
                consumer_id=consumer_id,
                permanent_refusal=True,
            )
        request = consumer.pending_fact_request(
            mapping_version=binding.mapping_version,
            calendar_version=binding.calendar_version,
        )
        if request is None:
            return FactResolutionResult(status="IDLE", reason="NO_PENDING", consumer_id=consumer_id)
        if request.status != "READY":
            return FactResolutionResult(
                status="REFUSED",
                reason=request.reason,
                request_id=request.request_id,
                consumer_id=consumer_id,
                permanent_refusal=True,
            )

        mismatch = self._validate_request_against_binding(request, binding)
        if mismatch is not None:
            return FactResolutionResult(
                status="REFUSED",
                reason=mismatch,
                request_id=request.request_id,
                consumer_id=consumer_id,
                mapping_version=binding.mapping_version,
                calendar_version=binding.calendar_version,
                permanent_refusal=True,
            )

        if self._verify_product_on_resolve:
            try:
                current_product = self._query_product(binding.member_key.instrument)
            except RecoveryFactResolutionError as exc:
                return FactResolutionResult(
                    status="REFUSED",
                    reason=exc.code,
                    request_id=request.request_id,
                    consumer_id=consumer_id,
                    mapping_version=binding.mapping_version,
                    calendar_version=binding.calendar_version,
                    permanent_refusal=False,
                )
            if current_product.session_generation != binding.session_generation:
                return FactResolutionResult(
                    status="REFUSED",
                    reason="SESSION_GENERATION_MISMATCH",
                    request_id=request.request_id,
                    consumer_id=consumer_id,
                    permanent_refusal=True,
                )
            if current_product.trading_day != binding.trading_day:
                return FactResolutionResult(
                    status="REFUSED",
                    reason="TRADING_DAY_MISMATCH",
                    request_id=request.request_id,
                    consumer_id=consumer_id,
                    permanent_refusal=True,
                )
            if current_product.connection_generation != binding.connection_generation:
                return FactResolutionResult(
                    status="REFUSED",
                    reason="CONNECTION_GENERATION_MISMATCH",
                    request_id=request.request_id,
                    consumer_id=consumer_id,
                    permanent_refusal=True,
                )
            if current_product.product_id != binding.product_id:
                return FactResolutionResult(
                    status="REFUSED",
                    reason="PRODUCT_MISMATCH",
                    request_id=request.request_id,
                    consumer_id=consumer_id,
                    permanent_refusal=True,
                )

        cov_start_ns, start_iso = _floor_utc_iso(request.start_utc_upper_ns)
        cov_end_ns, end_iso = _ceil_utc_iso(request.end_utc_lower_ns)
        try:
            cal_result = self._query_calendar(
                instrument=binding.member_key.instrument,
                product_id=binding.product_id,
                start_utc=start_iso,
                end_utc=end_iso,
            )
        except RecoveryFactResolutionError as exc:
            return FactResolutionResult(
                status="REFUSED",
                reason=exc.code,
                request_id=request.request_id,
                consumer_id=consumer_id,
                mapping_version=binding.mapping_version,
                calendar_version=binding.calendar_version,
                permanent_refusal=False,
            )

        if cal_result.binding["bound_trading_day"] != binding.trading_day:
            return FactResolutionResult(
                status="REFUSED",
                reason="TRADING_DAY_MISMATCH",
                request_id=request.request_id,
                consumer_id=consumer_id,
                permanent_refusal=True,
            )
        actual_calendar_version = _calendar_version_from_binding(cal_result.binding)
        if actual_calendar_version != binding.calendar_version:
            return FactResolutionResult(
                status="REFUSED",
                reason="CALENDAR_VERSION_MISMATCH",
                request_id=request.request_id,
                consumer_id=consumer_id,
                permanent_refusal=True,
            )
        if (
            cal_result.binding["data_origin"] != binding.data_origin
            or cal_result.policy["declaration"] != binding.policy_declaration
            or cal_result.source_authentication != binding.source_authentication
        ):
            return FactResolutionResult(
                status="REFUSED",
                reason="CALENDAR_POLICY_MISMATCH",
                request_id=request.request_id,
                consumer_id=consumer_id,
                permanent_refusal=True,
            )

        clipped = []
        for raw_window in cal_result.windows:
            w_start = max(cov_start_ns, _parse_utc_ns(raw_window["start_utc"]))
            w_end = min(cov_end_ns, _parse_utc_ns(raw_window["end_utc"]))
            if w_start < w_end:
                clipped.append(CalendarWindow(w_start, w_end, raw_window["phase"]))
        fact = RecoveryCalendarFact(
            source_ref=request.source_ref,
            source_epoch=request.source_epoch,
            key=request.member_key,
            mapping_version=binding.mapping_version,
            venue=binding.member_key.instrument.venue,
            product=binding.product_id,
            contract=binding.member_key.instrument.instrument_id,
            calendar_version=binding.calendar_version,
            coverage_start_utc_ns=cov_start_ns,
            coverage_end_utc_ns=cov_end_ns,
            windows=tuple(clipped),
            mode="SIMULATION_ONLY",
            data_origin=binding.data_origin,
            policy_declaration=binding.policy_declaration,
            source_authentication=binding.source_authentication,
        )
        response = RecoveryPendingFactResponse(
            request_id=request.request_id,
            member_generation=request.member_generation,
            fact=fact,
        )
        code = consumer.retry_pending_fact(
            request,
            response,
            mapping_version=binding.mapping_version,
            calendar_version=binding.calendar_version,
        )
        return FactResolutionResult(
            status="RESOLVED",
            reason=code,
            request_id=request.request_id,
            consumer_id=consumer_id,
            mapping_version=binding.mapping_version,
            calendar_version=binding.calendar_version,
        )

    def maintain(self) -> None:
        self._check_owner()
        self._product_client._runtime.maintain()
        if self._calendar_client._runtime is not self._product_client._runtime:
            self._calendar_client._runtime.maintain()

    def close(self) -> None:
        if os.getpid() != self._pid or threading.current_thread() is not self._thread:
            raise RecoveryFactResolutionError("WRONG_OWNER")
        self._closed = True
        self._bindings.clear()
