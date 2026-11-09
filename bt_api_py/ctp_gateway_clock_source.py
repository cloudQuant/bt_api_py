"""Owner-local composition of CTP clock observations and SDK SNTP evidence.

The supplied verifier is a trusted service resource; this module never
performs an inline network measurement or asserts client clock calibration.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field

from bt_api_py.clock_verification import ClockEvidence, ClockVerifier


class CtpGatewayCalibratedClockError(RuntimeError):
    """Finite, value-free source or calibration failure."""


_ANCHOR_SEAL = object()
_MAX_WALL_STEP_NS = 250_000_000


@dataclass(frozen=True, slots=True)
class _ServerClockCalibration:
    evidence: ClockEvidence = field(repr=False)
    anchor_wall_ns: int = field(repr=False)
    anchor_monotonic_ns: int = field(repr=False)
    pid: int = field(repr=False)
    thread: object = field(repr=False)
    seal: object = field(repr=False)


def capture_gateway_server_clock_calibration(verifier, *, allowed_servers):
    """Perform one explicit pre-login SNTP measurement and pin its wall anchor.

    This may perform network I/O and must only be called by trusted startup
    code, never inline in a market poll or RPC handler. ``allowed_servers``
    is an independently supplied trusted allowlist, not a claim from YAML or
    the verifier itself. Only the SDK SNTP implementation is accepted; SNTP
    itself does not authenticate the remote server. A failed or expired
    measurement is not replaced by a guessed offset or local system clock.
    """
    from bt_api_py import clock_verification

    if (type(verifier) is not ClockVerifier or type(allowed_servers) is not frozenset
            or not allowed_servers
            or any(type(server) is not str or not server or server != server.strip()
                   for server in allowed_servers)
            or type(verifier._servers) is not tuple or not verifier._servers
            or any(server not in allowed_servers for server in verifier._servers)
            or verifier._query is not clock_verification._sntp_query):
        raise CtpGatewayCalibratedClockError("INVALID_CLOCK_SOURCE")
    servers = verifier._servers
    before_mono, before_wall = time.monotonic_ns(), time.time_ns()
    try:
        evidence = ClockVerifier.refresh(verifier)
    except Exception:
        raise CtpGatewayCalibratedClockError("CLOCK_CALIBRATION_UNAVAILABLE") from None
    after_mono, after_wall = time.monotonic_ns(), time.time_ns()
    if (type(before_mono) is not int or type(before_wall) is not int
            or type(after_mono) is not int or type(after_wall) is not int
            or before_mono <= 0 or after_mono < before_mono or before_wall <= 0
            or abs((after_wall - before_wall) - (after_mono - before_mono)) > _MAX_WALL_STEP_NS
            or type(evidence) is not ClockEvidence or evidence.method != "sntp"
            or verifier._servers is not servers or verifier._query is not clock_verification._sntp_query
            or type(evidence.server) is not str or evidence.server not in allowed_servers
            or type(evidence.offset_ns) is not int
            or type(evidence.error_bound_ns) is not int or evidence.error_bound_ns < 0
            or type(evidence.measured_monotonic_ns) is not int
            or type(evidence.valid_until_monotonic_ns) is not int
            or not before_mono <= evidence.measured_monotonic_ns <= after_mono
            or evidence.valid_until_monotonic_ns <= after_mono):
        raise CtpGatewayCalibratedClockError("CLOCK_CALIBRATION_UNAVAILABLE")
    return _ServerClockCalibration(evidence, after_wall, after_mono, os.getpid(),
                                   threading.current_thread(), _ANCHOR_SEAL)


class CtpGatewayCalibratedClockSource:
    """Use a pre-login service SNTP sample without renewing its validity.

    Construct on the existing CTP owner thread with a calibration captured by
    trusted service code before login. A newer sample cannot retroactively
    prove that login's wall clock; retain the original valid calibration.
    """

    def __init__(self, session, calibration, *, max_age_seconds=300.0, refresher=None):
        from bt_api_ctp.gateway_session import CtpGatewaySession

        if (type(session) is not CtpGatewaySession
                or type(calibration) is not _ServerClockCalibration
                or calibration.seal is not _ANCHOR_SEAL
                or calibration.pid != os.getpid()
                or calibration.thread is not threading.current_thread()):
            raise CtpGatewayCalibratedClockError("INVALID_CLOCK_SOURCE")
        if (type(max_age_seconds) not in (int, float)
                or not 0 < max_age_seconds <= 3600):
            raise CtpGatewayCalibratedClockError("INVALID_CLOCK_SOURCE")
        session._check_owner()
        if refresher is not None:
            from bt_api_py.gateway_server_clock_refresh import GatewayServerClockRefresher
            if type(refresher) is not GatewayServerClockRefresher:
                raise CtpGatewayCalibratedClockError("INVALID_CLOCK_SOURCE")
        self._session = self._original_session = session
        self._calibration = self._original_calibration = calibration
        self._refresher = self._original_refresher = refresher
        self._max_age_seconds = max_age_seconds
        self._pid = os.getpid()
        self._thread = threading.current_thread()

    def read(self, handle, *, key, generation, stream_id, epoch,
             provider_sequence=None, provider_subscription_epoch=None):
        """Return source facts and calibration for an authorized current stream.

        Caller must resolve stream handle/epoch from the authenticated Gateway
        hub. The observation contains no monotonic timestamp from this host;
        durations must be reduced by the client's own complete RPC round trip.
        """
        return self._read(handle, key=key, generation=generation, stream_id=stream_id,
                          epoch=epoch, provider_sequence=provider_sequence,
                          provider_subscription_epoch=provider_subscription_epoch, internal=False)

    def read_internal(self, handle, *, key, generation, stream_id, epoch,
                      provider_sequence, provider_subscription_epoch):
        """Return observation and same-process source instant for the local gate.

        The monotonic instant is internal only and must never enter an RPC or
        be compared with a different process or host clock.
        """
        return self._read(handle, key=key, generation=generation, stream_id=stream_id,
                          epoch=epoch, provider_sequence=provider_sequence,
                          provider_subscription_epoch=provider_subscription_epoch, internal=True)

    def _read(self, handle, *, key, generation, stream_id, epoch,
              provider_sequence, provider_subscription_epoch, internal):
        if (os.getpid() != self._pid or threading.current_thread() is not self._thread
                or self._session is not self._original_session
                or self._calibration is not self._original_calibration
                or self._refresher is not self._original_refresher):
            raise CtpGatewayCalibratedClockError("CLOCK_SOURCE_CHANGED")
        tick_requested = provider_sequence is not None or provider_subscription_epoch is not None
        if tick_requested and (type(provider_sequence) is not int or provider_sequence <= 0
                               or type(provider_subscription_epoch) is not int
                               or provider_subscription_epoch <= 0):
            raise CtpGatewayCalibratedClockError("CLOCK_SOURCE_UNAVAILABLE")
        anchor = self._calibration
        if tick_requested and self._refresher is not None:
            from bt_api_py.gateway_server_clock_refresh import GatewayServerClockSnapshot
            anchor = self._refresher.current()
            if (type(anchor) is not GatewayServerClockSnapshot or anchor.pid != os.getpid()
                    or self._refresher.is_current(anchor) is not True):
                raise CtpGatewayCalibratedClockError("CLOCK_CALIBRATION_UNAVAILABLE")
        before = time.monotonic_ns()
        try:
            if tick_requested:
                raw = self._session.read_market_tick_clock(
                    handle, key=key, generation=generation, stream_id=stream_id,
                    epoch=epoch, provider_sequence=provider_sequence,
                    provider_subscription_epoch=provider_subscription_epoch,
                    max_age_seconds=self._max_age_seconds, _internal=True,
                )
            else:
                raw = self._session.read_market_login_clock(
                    handle, key=key, generation=generation, stream_id=stream_id,
                    epoch=epoch, max_age_seconds=self._max_age_seconds,
                )
        except Exception:
            raise CtpGatewayCalibratedClockError("CLOCK_SOURCE_UNAVAILABLE") from None
        after = time.monotonic_ns()
        source_mono = None
        if tick_requested:
            if (type(raw) is not tuple or len(raw) != 2
                    or type(raw[1]) is not int or not before <= raw[1] <= after):
                raise CtpGatewayCalibratedClockError("CLOCK_SOURCE_UNAVAILABLE")
            raw, source_mono = raw
        elif internal:
            raise CtpGatewayCalibratedClockError("CLOCK_SOURCE_UNAVAILABLE")
        evidence = anchor.evidence
        if (tick_requested and self._refresher is not None
                and self._refresher.is_current(anchor) is not True):
            raise CtpGatewayCalibratedClockError("CLOCK_CALIBRATION_UNAVAILABLE")
        if (type(before) is not int or type(after) is not int or before <= 0 or after < before
                or type(raw) is not dict or type(raw.get("age_ns")) is not int
                or type(raw.get("remaining_ns")) is not int or raw["age_ns"] < 0
                or type(evidence) is not ClockEvidence or evidence.method != "sntp"
                or type(evidence.server) is not str or not evidence.server
                or type(evidence.offset_ns) is not int
                or type(evidence.error_bound_ns) is not int or evidence.error_bound_ns < 0
                or type(evidence.measured_monotonic_ns) is not int
                or type(evidence.valid_until_monotonic_ns) is not int
                or evidence.measured_monotonic_ns <= 0
                or evidence.valid_until_monotonic_ns <= after):
            raise CtpGatewayCalibratedClockError("CLOCK_CALIBRATION_UNAVAILABLE")
        # For a native tick, the owner-local source instant and age share the
        # same monotonic clock, so their difference is the exact receipt
        # instant. The v1 login path still only exposes a conservative bound.
        event_lower = (source_mono if tick_requested else before) - raw["age_ns"]
        event_upper = (source_mono if tick_requested else after) - raw["age_ns"]
        if anchor.anchor_monotonic_ns > event_lower:
            raise CtpGatewayCalibratedClockError("CLOCK_CALIBRATION_UNAVAILABLE")
        elapsed = after - before
        wall_field = "source_event_received_wall_ns" if tick_requested else "login_received_wall_ns"
        if type(raw.get(wall_field)) is not int:
            raise CtpGatewayCalibratedClockError("CLOCK_SOURCE_UNAVAILABLE")
        wall_elapsed = raw[wall_field] - anchor.anchor_wall_ns
        lower = event_lower - anchor.anchor_monotonic_ns
        upper = event_upper - anchor.anchor_monotonic_ns
        if not lower - _MAX_WALL_STEP_NS <= wall_elapsed <= upper + _MAX_WALL_STEP_NS:
            raise CtpGatewayCalibratedClockError("CLOCK_WALL_DISCONTINUITY")
        source_wall = raw.get("source_observed_wall_ns")
        if type(source_wall) is not int:
            raise CtpGatewayCalibratedClockError("CLOCK_SOURCE_UNAVAILABLE")
        current_wall_elapsed = source_wall - anchor.anchor_wall_ns
        current_lower = (source_mono if tick_requested else before) - anchor.anchor_monotonic_ns
        current_upper = (source_mono if tick_requested else after) - anchor.anchor_monotonic_ns
        if not current_lower - _MAX_WALL_STEP_NS <= current_wall_elapsed <= current_upper + _MAX_WALL_STEP_NS:
            raise CtpGatewayCalibratedClockError("CLOCK_WALL_DISCONTINUITY")
        wall_error = max(abs(wall_elapsed - lower), abs(wall_elapsed - upper),
                         abs(current_wall_elapsed - current_lower),
                         abs(current_wall_elapsed - current_upper))
        remaining = min(raw["remaining_ns"] - elapsed,
                        evidence.valid_until_monotonic_ns - after)
        if remaining <= 0:
            raise CtpGatewayCalibratedClockError("CLOCK_CALIBRATION_EXPIRED")
        result = dict(raw)
        result["age_ns"] = raw["age_ns"] + elapsed
        result["remaining_ns"] = remaining
        result["source_calibration"] = {
            "schema_version": 1,
            "method": "sntp",
            "offset_ns": evidence.offset_ns,
            "error_bound_ns": evidence.error_bound_ns + wall_error,
            "sample_age_ns": after - evidence.measured_monotonic_ns,
            "remaining_ns": evidence.valid_until_monotonic_ns - after,
        }
        if (tick_requested and self._refresher is not None
                and self._refresher.is_current(anchor) is not True):
            raise CtpGatewayCalibratedClockError("CLOCK_CALIBRATION_UNAVAILABLE")
        return (result, source_mono) if internal else result
