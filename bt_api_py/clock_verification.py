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
import threading
import time
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
    """Return ``(offset_ns, error_bound_ns)`` for one SNTP exchange."""

    packet = b"\x23" + 47 * b"\0"
    t1 = time.time_ns()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(packet, (host, 123))
        data, _ = sock.recvfrom(512)
    finally:
        sock.close()
    t4 = time.time_ns()
    if len(data) < 48:
        raise OSError("short SNTP response")
    stratum = data[1]
    if stratum == 0 or stratum > 15:
        raise OSError("SNTP server not synchronized")
    root_delay = struct.unpack("!i", data[4:8])[0] / 65536.0
    root_dispersion = struct.unpack("!I", data[8:12])[0] / 65536.0

    def stamp(offset: int) -> int:
        seconds, fraction = struct.unpack("!II", data[offset : offset + 8])
        return int((seconds - _NTP_DELTA) * 1_000_000_000 + fraction * 1_000_000_000 / 2**32)

    t2, t3 = stamp(32), stamp(40)
    offset = ((t2 - t1) + (t3 - t4)) // 2
    delay = (t4 - t1) - (t3 - t2)
    bound = int(max(delay, 0) / 2 + abs(root_delay) * 1e9 / 2 + root_dispersion * 1e9)
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
    ) -> Optional[VenueClockCheck]:
        """Compare an exchange ``HH:MM:SS`` time (China time) with the local clock.

        ``local_received_ns`` is the local wall time (``time.time_ns()``) at
        which the venue time was received.  Returns ``None`` when no valid
        evidence or no parseable time exists.

        The check proves only that the venue clock domain agrees with the
        verified local clock within the reported granularity (truncated
        seconds), the evidence error bound and the configured latency budget
        (``latency_allowance_ms``, the same limit applied to every tick).  It is
        not a per-tick freshness check: each tick's measured event-to-receive
        latency is compared with the limit directly (D62-37).  SimNow measured
        on 2026-11-05: login venue time about 2.1 s behind the NTP-corrected
        local clock (1 s truncation plus delivery lag).
        """

        evidence = self.evidence()
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
    ) -> Optional[VenueClockCheck]:
        """Compare a venue server time (epoch ms) with the verified local clock.

        ``local_sent_ns`` / ``local_received_ns`` are local wall-clock times
        (``time.time_ns()``) around the server-time request; the venue time is
        compared with the NTP-corrected midpoint and half the round trip is
        added to the tolerance, together with the venue granularity, the
        evidence error bound and the configured latency budget (as for
        :meth:`check_venue_time`).
        """

        evidence = self.evidence()
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
