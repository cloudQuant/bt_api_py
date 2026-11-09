"""SDK clock evidence and market-tick clock qualification (iteration 62).

D62-21/27/28/37 and the 62.01 design freeze:

* :class:`ClockVerifier` measures the local wall-clock offset against SNTP
  servers (several servers, the smallest error bound wins).  Evidence expires
  after ``validity_seconds``; expired or missing evidence is never treated as
  verified.
* A venue clock check compares an exchange-reported time with the corrected
  local clock (for CTP the login response carries exchange times).  Only when
  both checks hold does the SDK mark a tick's source and receive clocks
  VERIFIED.
* Freshness compares the *measured* event->receive latency (receive time
  corrected by the NTP offset) with the configured limit; the error bound is
  recorded but not added to the measurement (D62-37).

Unqualified ticks are passed through unchanged (UNKNOWN), so hosts can still
use them for data lines but not for orders (D62-27).
"""

from __future__ import annotations

import socket
import struct
import secrets
import threading
import time
import math
import os
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional, Sequence

from bt_api_base.market_tick_protocol import (
    MarketClockQuality,
    MarketFreshnessStatus,
    MarketTickEvent,
    bind_market_tick_event,
)

__all__ = (
    "ClockEvidence",
    "ClockVerifier",
    "GatewayClockVerifier",
    "GatewayTradeClockQualification",
    "GatewayTradeClockQualifier",
    "VenueClockCheck",
    "qualify_market_tick_event",
)

_NTP_DELTA = 2208988800
_DEFAULT_SERVERS = ("time.apple.com", "cn.pool.ntp.org", "ntp.aliyun.com")
_CHINA_TIME = timezone(timedelta(hours=8))


@dataclass(frozen=True)
class ClockEvidence:
    """Measured local clock offset (server minus local) with an error bound."""

    offset_ns: int
    error_bound_ns: int
    server: str
    method: str
    measured_monotonic_ns: int
    valid_until_monotonic_ns: int

    def is_valid(self, now_monotonic_ns: Optional[int] = None) -> bool:
        now = time.monotonic_ns() if now_monotonic_ns is None else now_monotonic_ns
        return now <= self.valid_until_monotonic_ns


@dataclass(frozen=True)
class VenueClockCheck:
    """Result of comparing one venue-reported time with the corrected local clock."""

    consistent: bool
    difference_ms: float
    tolerance_ms: float
    source: str


def _sntp_query(host: str, timeout: float) -> tuple[int, int]:
    """Return a correlated SNTP sample; SNTP itself is not authenticated."""

    addresses = socket.getaddrinfo(host, 123, socket.AF_INET, socket.SOCK_DGRAM)
    if not addresses:
        raise OSError("SNTP server not resolved")
    target = addresses[0][4]
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        # A connected UDP socket restricts receive to the resolved endpoint;
        # additionally verify the returned source rather than trusting a DTO.
        sock.connect(target)
        peer = sock.getpeername()
        t1_wall = time.time_ns()
        t1_mono = time.monotonic_ns()
        seconds, nanos = divmod(t1_wall, 1_000_000_000)
        # Randomized sub-4ms fraction makes concurrent/replayed origins hard
        # to guess; its displacement is included in the uncertainty below.
        fraction = ((nanos << 32) // 1_000_000_000 & ~0xffffff) | secrets.randbits(24)
        transmit = struct.pack("!II", (seconds + _NTP_DELTA) & 0xffffffff, fraction)
        packet = b"\x23" + 39 * b"\0" + transmit
        if sock.send(packet) != len(packet):
            raise OSError("short SNTP request")
        data, source = sock.recvfrom(512)
        t4_wall = time.time_ns()
        t4_mono = time.monotonic_ns()
    finally:
        sock.close()
    if source != peer or peer != target:
        raise OSError("unexpected SNTP source")
    if len(data) < 48:
        raise OSError("short SNTP response")
    leap, version, mode = data[0] >> 6, (data[0] >> 3) & 7, data[0] & 7
    if leap == 3 or version not in (3, 4) or mode != 4:
        raise OSError("invalid SNTP header")
    if data[24:32] != transmit:
        raise OSError("unmatched SNTP response")
    stratum = data[1]
    if stratum == 0 or stratum > 15:
        raise OSError("SNTP server not synchronized")
    root_delay = struct.unpack("!i", data[4:8])[0] / 65536.0
    root_dispersion = struct.unpack("!I", data[8:12])[0] / 65536.0

    def stamp(offset: int) -> int:
        seconds, fraction = struct.unpack("!II", data[offset : offset + 8])
        if seconds == 0 and fraction == 0:
            raise OSError("missing SNTP timestamp")
        # Pick the era nearest the local request, including the 2036 rollover.
        local_ntp_seconds = t1_wall // 1_000_000_000 + _NTP_DELTA
        era = (local_ntp_seconds - seconds + (1 << 31)) // (1 << 32)
        return (seconds + era * (1 << 32) - _NTP_DELTA) * 1_000_000_000 + (fraction * 1_000_000_000 >> 32)

    t1 = t1_wall // 1_000_000_000 * 1_000_000_000 + (fraction * 1_000_000_000 >> 32)
    t4 = t4_wall
    t2, t3 = stamp(32), stamp(40)
    elapsed = t4_mono - t1_mono
    wall_discontinuity = abs((t4_wall - t1_wall) - elapsed)
    if (elapsed < 0 or elapsed > int(timeout * 1_000_000_000) + 100_000_000
            or wall_discontinuity > 10_000_000
            or t3 < t2 or t3 - t2 > elapsed + 1_000_000):
        raise OSError("invalid SNTP timing")
    offset = ((t2 - t1) + (t3 - t4)) // 2
    if abs(offset) > 86_400_000_000_000:
        raise OSError("SNTP offset out of range")
    delay = (t4 - t1) - (t3 - t2)
    # A sub-threshold wall-clock jump is still uncertainty, not evidence of
    # network latency. Never erase it (or a negative calculated delay) by
    # clamping delay to zero.
    bound = (int(max(delay, 0) / 2 + abs(root_delay) * 1e9 / 2 + root_dispersion * 1e9)
             + abs(t1 - t1_wall) + wall_discontinuity + max(-delay, 0))
    return offset, bound


class ClockVerifier:
    """Thread-safe cache of SNTP clock evidence."""

    def __init__(
        self,
        servers: Sequence[str] = _DEFAULT_SERVERS,
        *,
        validity_seconds: float = 900.0,
        max_error_bound_ms: float = 500.0,
        timeout_seconds: float = 2.0,
        query: Optional[Callable[[str, float], tuple[int, int]]] = None,
    ) -> None:
        self._servers = tuple(servers)
        self._validity_ns = int(validity_seconds * 1e9)
        self._max_bound_ns = int(max_error_bound_ms * 1e6)
        self._timeout = float(timeout_seconds)
        self._query = query or _sntp_query
        self._lock = threading.Lock()
        self._evidence: Optional[ClockEvidence] = None
        self._last_error: Optional[str] = None

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    def refresh(self) -> Optional[ClockEvidence]:
        best: Optional[tuple[int, int, str]] = None
        errors = []
        for server in self._servers:
            try:
                offset, bound = self._query(server, self._timeout)
            except Exception as exc:  # network or protocol failure
                errors.append(f"{server}:{type(exc).__name__}")
                continue
            if best is None or bound < best[1]:
                best = (offset, bound, server)
        now = time.monotonic_ns()
        with self._lock:
            if best is None or best[1] > self._max_bound_ns:
                # A failed re-measurement does not void a measurement that is
                # still within its validity window; expiry still applies.
                # (Clearing it turned one lost SNTP exchange into minutes of
                # unverified ticks, SimNow 2026-11-06.)
                current = self._evidence
                if current is not None and not current.is_valid():
                    self._evidence = None
                self._last_error = "clock_evidence_unavailable" if best is None else "clock_error_bound_too_large"
                return None
            self._evidence = ClockEvidence(
                offset_ns=best[0],
                error_bound_ns=best[1],
                server=best[2],
                method="sntp",
                measured_monotonic_ns=now,
                valid_until_monotonic_ns=now + self._validity_ns,
            )
            self._last_error = None
            return self._evidence

    def evidence(self, *, refresh_if_expired: bool = True) -> Optional[ClockEvidence]:
        with self._lock:
            current = self._evidence
        if current is not None and current.is_valid():
            return current
        if refresh_if_expired:
            return self.refresh()
        return None

    def start_background_refresh(self, interval_seconds: float = 300.0) -> None:
        """Refresh evidence on a daemon thread so hot paths never block on SNTP."""

        with self._lock:
            if getattr(self, "_refresher", None) is not None:
                return
            stop = threading.Event()
            self._refresher_stop = stop

            def _loop() -> None:
                failures = 0
                while not stop.is_set():
                    try:
                        measured = self.refresh()
                    except Exception:
                        measured = None
                    if measured is None:
                        # The tick path never refreshes inline: retry a failed
                        # measurement soon (2, 4, 8 ... s) instead of waiting a
                        # full interval without evidence.
                        failures += 1
                        delay = min(float(interval_seconds), 2.0 * 2 ** min(failures - 1, 8))
                    else:
                        failures = 0
                        delay = float(interval_seconds)
                    stop.wait(max(1.0, delay))

            thread = threading.Thread(target=_loop, name="bt-api-clock-verifier", daemon=True)
            self._refresher = thread
        thread.start()

    def stop_background_refresh(self) -> None:
        stop = getattr(self, "_refresher_stop", None)
        if stop is not None:
            stop.set()
        self._refresher = None

    def corrected_now_utc(self) -> Optional[datetime]:
        evidence = self.evidence()
        if evidence is None:
            return None
        return datetime.fromtimestamp((time.time_ns() + evidence.offset_ns) / 1e9, timezone.utc)

    def check_venue_time(
        self,
        venue_local_hms: str,
        local_received_ns: int,
        *,
        granularity_ms: float = 1000.0,
        latency_allowance_ms: float = 2000.0,
        source: str = "venue",
        refresh_if_expired: bool = True,
    ) -> Optional[VenueClockCheck]:
        """Compare an exchange ``HH:MM:SS`` time (China time) with the local clock.

        ``local_received_ns`` is the local wall time (``time.time_ns()``) at
        which the venue time was received.  Returns ``None`` when no valid
        evidence or no parseable time exists.  ``refresh_if_expired=False``
        never measures SNTP inline (hot paths such as per-tick qualification;
        the background refresher measures).

        The check proves only that the venue clock domain agrees with the
        verified local clock within the reported granularity (truncated
        seconds), the evidence error bound and the configured latency budget
        (``latency_allowance_ms``, the same limit applied to every tick).  It is
        not a per-tick freshness check: each tick's measured event-to-receive
        latency is compared with the limit directly (D62-37).  SimNow measured
        on 2026-11-05: login venue time about 2.1 s behind the NTP-corrected
        local clock (1 s truncation plus delivery lag).
        """

        evidence = self.evidence(refresh_if_expired=refresh_if_expired)
        if evidence is None or not venue_local_hms:
            return None
        corrected = datetime.fromtimestamp(
            (local_received_ns + evidence.offset_ns) / 1e9, timezone.utc
        ).astimezone(_CHINA_TIME)
        try:
            hms = datetime.strptime(venue_local_hms.strip(), "%H:%M:%S").time()
        except ValueError:
            return None
        venue_dt = corrected.replace(
            hour=hms.hour, minute=hms.minute, second=hms.second, microsecond=0
        )
        # Choose the calendar day that puts the venue time nearest to local.
        candidates = [venue_dt + timedelta(days=offset) for offset in (-1, 0, 1)]
        venue_dt = min(candidates, key=lambda value: abs((value - corrected).total_seconds()))
        difference_ms = (venue_dt - corrected).total_seconds() * 1000.0
        tolerance_ms = (
            granularity_ms + evidence.error_bound_ns / 1e6 + max(float(latency_allowance_ms), 0.0)
        )
        return VenueClockCheck(
            consistent=abs(difference_ms) <= tolerance_ms,
            difference_ms=difference_ms,
            tolerance_ms=tolerance_ms,
            source=source,
        )


    def check_venue_epoch(
        self,
        venue_epoch_ms: float,
        local_sent_ns: int,
        local_received_ns: int,
        *,
        granularity_ms: float = 1.0,
        latency_allowance_ms: float = 2000.0,
        source: str = "venue",
        refresh_if_expired: bool = True,
    ) -> Optional[VenueClockCheck]:
        """Compare a venue server time (epoch ms) with the verified local clock.

        ``local_sent_ns`` / ``local_received_ns`` are local wall-clock times
        (``time.time_ns()``) around the server-time request; the venue time is
        compared with the NTP-corrected midpoint and half the round trip is
        added to the tolerance, together with the venue granularity, the
        evidence error bound and the configured latency budget (as for
        :meth:`check_venue_time`; also for ``refresh_if_expired``).
        """

        evidence = self.evidence(refresh_if_expired=refresh_if_expired)
        if evidence is None:
            return None
        try:
            venue_ms = float(venue_epoch_ms)
            sent, received = int(local_sent_ns), int(local_received_ns)
        except (TypeError, ValueError):
            return None
        if received < sent:
            return None
        corrected_mid_ms = ((sent + received) / 2 + evidence.offset_ns) / 1e6
        difference_ms = venue_ms - corrected_mid_ms
        tolerance_ms = (
            (received - sent) / 2e6
            + max(float(granularity_ms), 0.0)
            + evidence.error_bound_ns / 1e6
            + max(float(latency_allowance_ms), 0.0)
        )
        return VenueClockCheck(
            consistent=abs(difference_ms) <= tolerance_ms,
            difference_ms=difference_ms,
            tolerance_ms=tolerance_ms,
            source=source,
        )

def _parse_utc(text: Optional[str]) -> Optional[datetime]:
    if not text:
        return None
    try:
        value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        return None
    return value.astimezone(timezone.utc)


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


QUALIFICATION_POLICY_REF = "bt_api_py.market_clock_qualification.v1"


def qualify_market_tick_event(
    event: MarketTickEvent,
    evidence: Optional[ClockEvidence],
    venue_check: Optional[VenueClockCheck],
    *,
    max_latency_seconds: float = 2.0,
    source_granularity_ms: float = 1000.0,
) -> MarketTickEvent:
    """Return ``event`` re-bound with SDK clock qualification, or unchanged.

    The tick is qualified only with valid NTP evidence and a consistent venue
    clock check; otherwise the original (UNKNOWN) event is returned untouched.
    """

    if type(event) is not MarketTickEvent:
        return event
    if evidence is None or not evidence.is_valid() or venue_check is None or not venue_check.consistent:
        return event
    data = event.data
    event_time = _parse_utc(data.provider_event_time_utc)
    received = _parse_utc(data.received_at_utc)
    if event_time is None or received is None or data.received_monotonic_ns is None:
        return event
    corrected_received = received + timedelta(microseconds=evidence.offset_ns / 1000.0)
    latency = (corrected_received - event_time).total_seconds()
    bound_ms = evidence.error_bound_ns / 1e6
    source_error_ms = bound_ms + source_granularity_ms
    if latency < -(source_error_ms / 1000.0):
        status = MarketFreshnessStatus.UNAVAILABLE
        reasons: tuple[str, ...] = ("EVENT_TIME_AHEAD_OF_RECEIVE",)
    elif latency <= max_latency_seconds:
        status = MarketFreshnessStatus.FRESH
        reasons = ()
    else:
        status = MarketFreshnessStatus.STALE
        reasons = ("EVENT_TIME_STALE",)
    # Execution eligibility is decided here, under this SDK qualification
    # policy (I62-09).  A provider that attests no native quality rules (an
    # empty CTP rules receipt) gets the SDK policy reference instead; a
    # provider reference is kept unchanged.  A tick with a known continuity
    # defect is never eligible.
    contract_ref = data.quality_contract_ref or (
        f"{QUALIFICATION_POLICY_REF}:max_latency={max_latency_seconds:g}s"
    )
    eligible = status is MarketFreshnessStatus.FRESH and data.continuity_status == "continuous"
    qualified = replace(
        data,
        quality_contract_ref=contract_ref,
        received_at_utc=_utc_text(corrected_received),
        source_clock_quality=MarketClockQuality.VERIFIED,
        receive_clock_quality=MarketClockQuality.VERIFIED,
        source_clock_error_ms=source_error_ms,
        receive_clock_error_ms=bound_ms,
        freshness_status=status,
        freshness_reason_codes=reasons,
        execution_eligible=eligible,
    )
    return bind_market_tick_event(qualified, event.source, event.cursor)


class GatewayClockVerifier:
    """Bind an actual local SNTP sample to a stable local wall/monotonic pair.

    This wrapper does not start a network request on the trading path. Call
    ``refresh`` from the application's explicit clock-maintenance context.
    """

    def __init__(self, verifier: ClockVerifier, *, max_wall_jump_ns: int = 10_000_000):
        if (type(verifier) is not ClockVerifier or verifier._query is not _sntp_query
                or type(max_wall_jump_ns) is not int
                or not 0 <= max_wall_jump_ns <= 1_000_000_000):
            raise ValueError("INVALID_GATEWAY_CLOCK_VERIFIER")
        self._verifier, self._jump = verifier, max_wall_jump_ns
        self._pid = os.getpid()
        self._lock = threading.Lock()
        self._sample = None
        self._stop = threading.Event()
        self._refresher = None

    def refresh(self):
        if os.getpid() != self._pid:
            raise RuntimeError("GATEWAY_CLOCK_PROCESS_CHANGED")
        if self._verifier._query is not _sntp_query:
            with self._lock:
                self._sample = None
            return None
        before_mono, before_wall = time.monotonic_ns(), time.time_ns()
        evidence = self._verifier.refresh()
        after_wall, after_mono = time.time_ns(), time.monotonic_ns()
        stable = (type(evidence) is ClockEvidence
                  and before_mono <= evidence.measured_monotonic_ns <= after_mono
                  and abs((after_wall - before_wall) - (after_mono - before_mono)) <= self._jump)
        with self._lock:
            self._sample = (evidence, after_wall, after_mono) if stable else None
        return evidence if stable else None

    def evidence_for(self, received_wall_ns: int, received_monotonic_ns: int):
        if (os.getpid() != self._pid or self._stop.is_set()
                or self._verifier._query is not _sntp_query):
            return None
        with self._lock:
            sample = self._sample
        if sample is None:
            return None
        evidence, anchor_wall, anchor_mono = sample
        now_wall, now_mono = time.time_ns(), time.monotonic_ns()
        if (self._verifier.evidence(refresh_if_expired=False) is not evidence
                or not evidence.is_valid(now_mono)
                or not anchor_mono <= received_monotonic_ns <= now_mono
                or not evidence.is_valid(received_monotonic_ns)
                or abs((received_wall_ns - anchor_wall) - (received_monotonic_ns - anchor_mono)) > self._jump
                or abs((now_wall - anchor_wall) - (now_mono - anchor_mono)) > self._jump):
            return None
        return evidence

    def start_background_refresh(self, interval_seconds: float = 300.0):
        """Explicitly start SNTP maintenance outside the order/tick path."""
        if (type(interval_seconds) not in (int, float) or not math.isfinite(interval_seconds)
                or not 1 <= interval_seconds <= 3600 or os.getpid() != self._pid):
            raise ValueError("INVALID_GATEWAY_CLOCK_INTERVAL")
        with self._lock:
            if self._refresher is not None:
                return
            self._stop.clear()
            def run():
                failures = 0
                while not self._stop.is_set():
                    try:
                        result = self.refresh()
                    except Exception:
                        result = None
                    failures = 0 if result is not None else failures + 1
                    delay = interval_seconds if result is not None else min(interval_seconds, 2 ** min(failures, 8))
                    self._stop.wait(delay)
            self._refresher = threading.Thread(target=run, name="gateway-clock-verifier", daemon=True)
            thread = self._refresher
            try:
                thread.start()
            except Exception:
                self._refresher = None
                self._stop.set()
                raise

    def stop_background_refresh(self, *, timeout_seconds: float = 5.0) -> bool:
        if (type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
                or not 0 <= timeout_seconds <= 30 or os.getpid() != self._pid):
            raise ValueError("INVALID_GATEWAY_CLOCK_TIMEOUT")
        with self._lock:
            thread = self._refresher
            self._stop.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout_seconds)
        if thread is not None and thread.is_alive():
            return False
        with self._lock:
            if self._refresher is thread:
                self._refresher = None
            self._sample = None
        return True


@dataclass(frozen=True)
class GatewayTradeClockQualification:
    """Order-time result; the original provider event is never rewritten."""

    ok: bool
    reason: str | None
    client_received_at_utc: str | None = None
    client_received_monotonic_ns: int | None = None
    event_time_utc: str | None = None
    latency_seconds: float | None = None
    source_clock_error_ms: float | None = None
    receive_clock_error_ms: float | None = None
    expires_monotonic_ns: int | None = None


class GatewayTradeClockQualifier:
    """Recheck the latest SDK-issued PUB tick when preparing an order.

    Each check obtains a current authenticated observation on the same market
    session. Server and client clocks use separate SNTP evidence; no
    server monotonic timestamp is compared with the client's clock.
    """

    def __init__(self, client, verifier: GatewayClockVerifier, *, max_latency_seconds=2.0,
                 max_clock_error_ms=500.0, timeout_seconds=1.0):
        from bt_api_py.gateway_strategy_market import GatewayMarketSubscriptionClient

        if (type(client) is not GatewayMarketSubscriptionClient
                or type(verifier) is not GatewayClockVerifier
                or any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0
                       for v in (max_latency_seconds, max_clock_error_ms, timeout_seconds))
                or timeout_seconds > 30):
            raise ValueError("INVALID_GATEWAY_CLOCK_QUALIFIER")
        self._client, self._verifier = client, verifier
        self._latency = float(max_latency_seconds)
        self._max_error_ns = int(max_clock_error_ms * 1_000_000)
        self._timeout = float(timeout_seconds)
        self._pid = os.getpid()

    def qualify(self, event, receipt) -> GatewayTradeClockQualification:
        from bt_api_py.gateway_strategy_market import GatewayMarketClockObservation
        from bt_api_py.service_market import MarketReceiveReceipt

        def unavailable(reason):
            return GatewayTradeClockQualification(False, reason)

        if (os.getpid() != self._pid or type(receipt) is not MarketReceiveReceipt
                or receipt.source != "pub" or receipt.pid != self._pid):
            return unavailable("REALTIME_RECEIPT_UNAVAILABLE")
        local = self._verifier.evidence_for(receipt.received_wall_ns, receipt.received_monotonic_ns)
        if (local is None or type(local.offset_ns) is not int or type(local.error_bound_ns) is not int
                or not 0 <= local.error_bound_ns <= self._max_error_ns):
            return unavailable("CLIENT_CLOCK_UNAVAILABLE")
        try:
            observed = self._client.clock_observation(receipt=receipt, timeout_seconds=self._timeout)
        except Exception:
            return unavailable("SOURCE_CLOCK_UNAVAILABLE")
        if (type(observed) is not GatewayMarketClockObservation
                or not self._client._issued_clock_matches(event, receipt, observed)):
            return unavailable("CLOCK_BINDING_UNAVAILABLE")
        obs = observed.observation
        expected = {"schema_version", "provider", "account_scope", "venue", "service_generation",
                    "td_connection_generation", "td_source_ref", "trading_day",
                    "source_observed_wall_ns", "age_ns", "remaining_ns",
                    "market_source", "source_calibration"}
        if type(obs) is not dict or type(obs.get("schema_version")) is not int:
            return unavailable("SOURCE_CLOCK_INVALID")
        schema = obs["schema_version"]
        if schema == 1:
            expected |= {"venue_time", "login_received_wall_ns"}
            source_wall_field = "login_received_wall_ns"
        elif schema == 2:
            expected |= {"observation_kind", "venue_observed_at_utc", "source_event_received_wall_ns",
                         "provider_sequence", "provider_subscription_epoch"}
            source_wall_field = "source_event_received_wall_ns"
        else:
            return unavailable("SOURCE_CLOCK_INVALID")
        if set(obs) != expected:
            return unavailable("SOURCE_CLOCK_INVALID")
        calibration = obs["source_calibration"]
        cal_fields = {"schema_version", "method", "offset_ns", "error_bound_ns", "sample_age_ns", "remaining_ns"}
        if (type(calibration) is not dict or set(calibration) != cal_fields
                or obs["provider"] != "ctp" or obs["account_scope"] != observed.account_ref
                or obs["venue"] != event.data.instrument.venue
                or type(obs["service_generation"]) is not str or not obs["service_generation"]
                or obs["service_generation"] != obs["service_generation"].strip()
                or not obs["service_generation"].isprintable() or len(obs["service_generation"]) > 256
                or any(type(obs[name]) is not int or obs[name] <= 0 for name in
                       ("td_connection_generation", source_wall_field,
                        "source_observed_wall_ns", "remaining_ns"))
                or type(obs["age_ns"]) is not int or obs["age_ns"] < 0
                or type(obs["td_source_ref"]) is not str or not obs["td_source_ref"]
                or type(obs["trading_day"]) is not str
                or obs["trading_day"] != (event.data.trading_date or "").replace("-", "")
                or type(calibration["schema_version"]) is not int or calibration["schema_version"] != 1
                or calibration["method"] != "sntp"
                or any(type(calibration[name]) is not int for name in
                       ("offset_ns", "error_bound_ns", "sample_age_ns", "remaining_ns"))
                or not 0 <= calibration["error_bound_ns"] <= self._max_error_ns
                or calibration["sample_age_ns"] < obs["age_ns"]
                or calibration["remaining_ns"] <= 0
                or event.data.event_time_provenance != "provider_reported"
                or event.data.continuity_status != "continuous"):
            return unavailable("SOURCE_CLOCK_INVALID")
        if schema == 1:
            if type(obs["venue_time"]) is not str or len(obs["venue_time"]) != 8:
                return unavailable("SOURCE_CLOCK_INVALID")
        else:
            tick_stamp = (_parse_utc(obs["venue_observed_at_utc"])
                          if type(obs["venue_observed_at_utc"]) is str else None)
            if (obs["observation_kind"] != "native_md_tick" or tick_stamp is None
                    or tick_stamp != _parse_utc(event.data.provider_event_time_utc)
                    or type(obs["provider_sequence"]) is not int or obs["provider_sequence"] <= 0
                    or obs["provider_sequence"] != event.data.provider_sequence
                    or type(obs["provider_subscription_epoch"]) is not int
                    or obs["provider_subscription_epoch"] <= 0
                    or obs["provider_subscription_epoch"] != event.data.provider_subscription_epoch):
                return unavailable("SOURCE_CLOCK_INVALID")
        now_mono = time.monotonic_ns()
        rtt = observed.received_monotonic_ns - observed.request_started_monotonic_ns
        ttl = min(obs["remaining_ns"], calibration["remaining_ns"]) - rtt
        expiry = min(observed.received_monotonic_ns + ttl, local.valid_until_monotonic_ns)
        if ttl <= 0 or now_mono >= expiry:
            return unavailable("SOURCE_CLOCK_EXPIRED")
        # Source and client wall clocks are calibrated independently. Their
        # difference may only represent the bounded network leg plus errors.
        server_utc = obs["source_observed_wall_ns"] + calibration["offset_ns"]
        client_utc = observed.received_wall_ns + local.offset_ns
        uncertainty = calibration["error_bound_ns"] + local.error_bound_ns + 10_000_000
        if not -uncertainty <= client_utc - server_utc <= rtt + uncertainty:
            return unavailable("CLOCK_DOMAINS_INCONSISTENT")
        if schema == 1:
            gateway_check = ClockVerifier(servers=())
            gateway_check._evidence = ClockEvidence(calibration["offset_ns"], calibration["error_bound_ns"],
                "authenticated_gateway", "sntp", observed.received_monotonic_ns, expiry)
            try:
                venue = gateway_check.check_venue_time(obs["venue_time"], obs["login_received_wall_ns"],
                                                       refresh_if_expired=False)
            except (OverflowError, OSError, ValueError):
                return unavailable("SOURCE_CLOCK_INVALID")
            if venue is None or not venue.consistent:
                return unavailable("VENUE_CLOCK_UNAVAILABLE")
        else:
            try:
                source_delay_ns = obs[source_wall_field] + calibration["offset_ns"] - int(tick_stamp.timestamp() * 1e9)
            except (OverflowError, OSError, ValueError):
                return unavailable("SOURCE_CLOCK_INVALID")
            if not (-1_000_000_000 - calibration["error_bound_ns"] <= source_delay_ns
                    <= 2_000_000_000 + calibration["error_bound_ns"]):
                return unavailable("VENUE_CLOCK_UNAVAILABLE")
        # Recheck local evidence after the RPC: a clock jump or refreshed sample
        # during the request invalidates the event observation.
        if self._verifier.evidence_for(receipt.received_wall_ns, receipt.received_monotonic_ns) is not local:
            return unavailable("CLIENT_CLOCK_CHANGED")
        stamp = _parse_utc(event.data.provider_event_time_utc)
        if stamp is None:
            return unavailable("EVENT_TIME_UNAVAILABLE")
        try:
            received = datetime.fromtimestamp((receipt.received_wall_ns + local.offset_ns) / 1e9, timezone.utc)
            # Account for both network and the complete owner/feed queue delay.
            age_seconds = (time.time_ns() + local.offset_ns) / 1e9 - stamp.timestamp()
        except (OverflowError, OSError, ValueError):
            return unavailable("EVENT_TIME_UNAVAILABLE")
        source_error_ms = 1000.0 + calibration["error_bound_ns"] / 1e6
        if (age_seconds < -(source_error_ms + local.error_bound_ns / 1e6) / 1000.0
                or age_seconds > self._latency):
            return unavailable("EVENT_TIME_STALE")
        freshness_remaining = int((self._latency - age_seconds) * 1_000_000_000)
        expiry = min(expiry, now_mono + freshness_remaining)
        if (expiry <= time.monotonic_ns()
                or not self._client._issued_clock_matches(event, receipt, observed)):
            return unavailable("CLOCK_BINDING_UNAVAILABLE")
        return GatewayTradeClockQualification(True, None, _utc_text(received),
            receipt.received_monotonic_ns, _utc_text(stamp), age_seconds,
            source_error_ms, local.error_bound_ns / 1e6, expiry)
