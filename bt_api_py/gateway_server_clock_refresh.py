"""Owner-bound, background SNTP calibration for Gateway tick observations.

SNTP is not authenticated. Servers and policy must come from an independently
trusted service composition, never from a market client or an RPC request.
"""
from __future__ import annotations

import math
import os
import threading
import time
from dataclasses import dataclass, replace
from contextlib import contextmanager

from bt_api_py import clock_verification
from bt_api_py.clock_verification import ClockEvidence, ClockVerifier


@dataclass(frozen=True, slots=True)
class GatewayServerClockRefreshSettings:
    """Trusted service policy; never populated from a market request."""

    max_error_bound_ns: int
    max_sample_validity_seconds: float
    refresh_interval_seconds: float

    def __post_init__(self):
        if (type(self.max_error_bound_ns) is not int or not 0 <= self.max_error_bound_ns <= 10_000_000_000
                or type(self.max_sample_validity_seconds) not in (int, float)
                or not math.isfinite(self.max_sample_validity_seconds)
                or not 0 < self.max_sample_validity_seconds <= 3600
                or type(self.refresh_interval_seconds) not in (int, float)
                or not math.isfinite(self.refresh_interval_seconds)
                or not 0.01 <= self.refresh_interval_seconds <= 3600):
            raise ValueError("INVALID_GATEWAY_SERVER_CLOCK")


@dataclass(frozen=True, slots=True)
class GatewayServerClockSnapshot:
    evidence: ClockEvidence
    anchor_wall_ns: int
    anchor_monotonic_ns: int
    pid: int


class GatewayServerClockRefresher:
    """Publish only current locally measured snapshots to the owner thread."""

    def __init__(self, verifier, *, allowed_servers, max_error_bound_ns,
                 max_sample_validity_seconds, refresh_interval_seconds, claim_coordinator=None):
        if (type(verifier) is not ClockVerifier or type(allowed_servers) is not frozenset
                or not allowed_servers
                or any(type(s) is not str or not s or s != s.strip() for s in allowed_servers)
                or type(verifier._servers) is not tuple or not verifier._servers
                or any(s not in allowed_servers for s in verifier._servers)
                or verifier._query is not clock_verification._sntp_query
                or type(max_error_bound_ns) is not int or not 0 <= max_error_bound_ns <= 10_000_000_000
                or type(max_sample_validity_seconds) not in (int, float)
                or not math.isfinite(max_sample_validity_seconds)
                or not 0 < max_sample_validity_seconds <= 3600
                or type(refresh_interval_seconds) not in (int, float)
                or not math.isfinite(refresh_interval_seconds)
                or not 0.01 <= refresh_interval_seconds <= 3600):
            raise ValueError("INVALID_GATEWAY_SERVER_CLOCK")
        if claim_coordinator is not None:
            from bt_api_py.gateway_dispatch_claim import GatewayDispatchClaimCoordinator
            if type(claim_coordinator) is not GatewayDispatchClaimCoordinator:
                raise ValueError("INVALID_GATEWAY_CLOCK_COORDINATOR")
        self._verifier = verifier
        self._servers = verifier._servers
        self._query = verifier._query
        self._allowed_servers = allowed_servers
        self._max_error_bound_ns = max_error_bound_ns
        self._max_validity_ns = int(max_sample_validity_seconds * 1e9)
        self._interval = float(refresh_interval_seconds)
        self._pid = os.getpid()
        self._owner = threading.current_thread()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._worker = None
        self._sample = None
        self._claim_coordinator = self._original_claim_coordinator = claim_coordinator

    @contextmanager
    def _sample_change(self):
        """Fence only publication; DNS and SNTP never hold the coordinator lock."""
        coordinator = self._claim_coordinator
        if coordinator is not self._original_claim_coordinator:
            if self._original_claim_coordinator is not None:
                self._original_claim_coordinator.poison_domain('session_source')
            raise RuntimeError("GATEWAY_CLOCK_COORDINATOR_CHANGED")
        if coordinator is None:
            yield
            return
        try:
            token = coordinator.begin_change('session_source')
        except BaseException:
            coordinator.poison_domain('session_source')
            raise
        try:
            yield
        except BaseException:
            coordinator.poison(token)
            raise
        else:
            try:
                coordinator.finish_change(token, 'UNAVAILABLE')
            except BaseException:
                coordinator.poison_domain('session_source')
                raise

    def _assert_owner(self):
        if os.getpid() != self._pid or threading.current_thread() is not self._owner:
            raise RuntimeError("GATEWAY_CLOCK_OWNER_CHANGED")

    def _configuration_valid(self):
        return (self._verifier._servers is self._servers
                and all(s in self._allowed_servers for s in self._servers)
                and self._query is clock_verification._sntp_query
                and self._verifier._query is self._query)

    def _capture(self):
        if not self._configuration_valid():
            return None
        before_mono, before_wall = time.monotonic_ns(), time.time_ns()
        try:
            evidence = ClockVerifier.refresh(self._verifier)
        except Exception:
            return None
        after_mono, after_wall = time.monotonic_ns(), time.time_ns()
        wall_difference = abs((after_wall - before_wall) - (after_mono - before_mono))
        if (not self._configuration_valid() or before_mono <= 0 or after_mono < before_mono
                or before_wall <= 0 or after_wall <= 0
                or wall_difference > 10_000_000
                or type(evidence) is not ClockEvidence or evidence.method != 'sntp'
                or type(evidence.server) is not str or evidence.server not in self._allowed_servers
                or type(evidence.offset_ns) is not int
                or type(evidence.error_bound_ns) is not int
                or not 0 <= evidence.error_bound_ns
                or evidence.error_bound_ns + wall_difference > self._max_error_bound_ns
                or type(evidence.measured_monotonic_ns) is not int
                or type(evidence.valid_until_monotonic_ns) is not int
                or not before_mono <= evidence.measured_monotonic_ns <= after_mono):
            return None
        expiry = min(evidence.valid_until_monotonic_ns,
                     evidence.measured_monotonic_ns + self._max_validity_ns)
        if expiry <= after_mono:
            return None
        evidence = replace(evidence, valid_until_monotonic_ns=expiry,
                           error_bound_ns=evidence.error_bound_ns + wall_difference)
        return GatewayServerClockSnapshot(evidence, after_wall, after_mono, self._pid)

    def start(self):
        self._assert_owner()
        with self._lock:
            if self._worker is not None:
                return
            self._stop.clear()

            def run():
                while not self._stop.is_set():
                    sample = self._capture()
                    if self._stop.is_set():
                        return
                    try:
                        with self._sample_change():
                            with self._lock:
                                self._sample = None if self._stop.is_set() else sample
                    except Exception:
                        if self._original_claim_coordinator is not None:
                            self._original_claim_coordinator.close()
                        return
                    self._stop.wait(self._interval)

            worker = threading.Thread(target=run, name='gateway-server-clock', daemon=True)
            self._worker = worker
            try:
                worker.start()
            except Exception:
                self._worker = None
                self._stop.set()
                raise

    def _valid_locked(self, sample):
        return (type(sample) is GatewayServerClockSnapshot and sample is self._sample
                and self._claim_coordinator is self._original_claim_coordinator
                and self._worker is not None and self._worker.is_alive()
                and not self._stop.is_set() and self._configuration_valid()
                and sample.pid == self._pid and sample.evidence.is_valid()
                and sample.evidence.valid_until_monotonic_ns > time.monotonic_ns())

    def current(self):
        self._assert_owner()
        with self._lock:
            return self._sample if self._valid_locked(self._sample) else None

    def is_current(self, sample):
        self._assert_owner()
        with self._lock:
            return self._valid_locked(sample)

    def stop(self, *, timeout_seconds=5.0):
        self._assert_owner()
        if (type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
                or not 0 <= timeout_seconds <= 30):
            raise ValueError("INVALID_GATEWAY_CLOCK_TIMEOUT")
        try:
            with self._sample_change():
                with self._lock:
                    worker = self._worker
                    self._stop.set()
                    self._sample = None
        except Exception:
            # A poisoned or closed coordinator rejects begin_change (and a
            # closed one also rejects poison_domain). Irreversibly close the
            # whole coordinator before changing source state without a token;
            # otherwise shutdown could leak the worker or leave claims usable.
            coordinator = self._original_claim_coordinator
            if coordinator is None:
                raise
            coordinator.close()
            with self._lock:
                worker = self._worker
                self._stop.set()
                self._sample = None
        if worker is not None:
            worker.join(timeout_seconds)
            if worker.is_alive():
                return False
        with self._lock:
            if self._worker is worker:
                self._worker = None
        return True
