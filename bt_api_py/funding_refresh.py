"""SDK-owned provider-neutral funding snapshot cache and refresh lane.

The caller supplies reads, route binding, validation, and session lifecycle.
A timeout fences publication until the prior worker and owned client are handled.
"""

from __future__ import annotations

import collections
import datetime as _dt
import math
import threading
import time
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict, is_dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Deque, Dict, Optional, Tuple


FundingCacheKey = Tuple[str, str]


def normalize_funding_refresh_options(options: Mapping[str, Any]) -> Tuple[float, float]:
    """Resolve the existing funding age and refresh interval settings."""
    max_age = float(options.get("funding_max_age_seconds", 30.0))
    if not math.isfinite(max_age) or max_age < 0:
        raise ValueError("funding_max_age_seconds must be finite and nonnegative")
    refresh_interval = float(
        options.get("funding_refresh_interval_seconds", max_age / 2.0 if max_age else 0.0)
    )
    if not math.isfinite(refresh_interval) or refresh_interval < 0:
        raise ValueError("funding_refresh_interval_seconds must be finite and nonnegative")
    return max_age, refresh_interval


class FundingRefreshError(RuntimeError):
    """Default error raised when the funding lane cannot be used safely."""


class FundingRefreshController:
    """Own funding cache policy and its single serialized metadata worker.

    All callbacks are supplied by the caller so this component does not know
    about a provider, SDK facade, framework session, or credential/config
    objects.  ``validate_snapshot`` returns an empty string for a valid typed
    SDK contract or a stable public reason code for an invalid one.

    ``read_snapshot(api, dataname)`` must perform one provider read and return
    a mapping.  ``cache_key(dataname)`` returns the route-qualified identity.
    ``api_getter`` and ``ensure_api_ready`` are consulted at the lane boundary;
    the API object captured when a refresh is queued must still be the active
    object when the result is published.
    """

    def __init__(
        self,
        *,
        read_snapshot: Callable[[Any, str], Mapping],
        cache_key: Callable[[str], FundingCacheKey],
        api_getter: Callable[[], Any],
        ensure_api_ready: Callable[[], Any],
        can_queue_refresh: Callable[[], bool],
        session_active: Callable[[], bool],
        typed_snapshot_required: Callable[[], bool],
        max_age_seconds: float,
        refresh_interval_seconds: float,
        shutdown_timeout: float,
        error_code: Callable[[BaseException, str], str],
        observe_error: Optional[Callable[[BaseException], None]] = None,
        validate_snapshot: Optional[
            Callable[[Mapping[str, Any], float, FundingCacheKey], str]
        ] = None,
        on_stale_worker_restart: Optional[Callable[[Any], None]] = None,
        cache_source: str = "sdk_funding_cache",
        inactive_status: str = "session_not_running",
        worker_name: str = "SdkFundingRefresh",
        monotonic: Callable[[], float] = time.monotonic,
        wall_time: Callable[[], float] = time.time,
        utcnow: Optional[Callable[[], _dt.datetime]] = None,
        error_factory: Optional[Callable[[str], BaseException]] = None,
    ) -> None:
        max_age = float(max_age_seconds)
        refresh_interval = float(refresh_interval_seconds)
        shutdown = float(shutdown_timeout)
        if not math.isfinite(max_age) or max_age < 0:
            raise ValueError("funding max_age_seconds must be finite and nonnegative")
        if not math.isfinite(refresh_interval) or refresh_interval < 0:
            raise ValueError("funding refresh_interval_seconds must be finite and nonnegative")
        if not math.isfinite(shutdown) or shutdown < 0:
            raise ValueError("funding shutdown_timeout must be finite and nonnegative")

        self._read_snapshot = read_snapshot
        self._cache_key = cache_key
        self._api_getter = api_getter
        self._ensure_api_ready = ensure_api_ready
        self._can_queue_refresh = can_queue_refresh
        self._session_active = session_active
        self._typed_snapshot_required = typed_snapshot_required
        self.max_age_seconds = max_age
        self.refresh_interval_seconds = refresh_interval
        self.shutdown_timeout = shutdown
        self._error_code = error_code
        self._observe_error = observe_error or (lambda _exc: None)
        self._validate_snapshot = validate_snapshot
        self._on_stale_worker_restart = on_stale_worker_restart
        if any(type(value) is not str or not value for value in (cache_source, inactive_status)):
            raise ValueError("funding cache source and inactive status must be non-empty strings")
        self._cache_source = cache_source
        self._inactive_status = inactive_status
        self._worker_name = str(worker_name)
        self._monotonic = monotonic
        self._wall_time = wall_time
        self._utcnow = utcnow or (lambda: _dt.datetime.now(_dt.timezone.utc))
        self._error_factory = error_factory or FundingRefreshError

        self._condition = threading.Condition(threading.RLock())
        self._transport_lock = threading.Lock()
        self._cache: Dict[FundingCacheKey, Dict[str, Any]] = {}
        self._last_errors: Dict[FundingCacheKey, str] = {}
        self._last_requested: Dict[FundingCacheKey, float] = {}
        self._queue: Deque[Tuple[int, FundingCacheKey, str, Any]] = collections.deque()
        self._pending = set()
        self._inflight_key: Optional[FundingCacheKey] = None
        self._direct_inflight = 0
        self._worker_thread: Optional[threading.Thread] = None
        self._generation = 0
        self._accept_results = False
        self._stop_requested = False
        self._restart_blocked_by_worker = False
        self._restart_preparing = False
        self._restart_api_pending = False
        self._restart_api = None
        self._health: "collections.Counter[str]" = collections.Counter()

    @property
    def transport_lock(self):
        """The lock shared by direct and queued reads of the same API client."""
        return self._transport_lock

    def prepare_start(self) -> None:
        """Reject restart until any timed-out metadata reader has exited.

        If the previous reader timed out and later exited, its captured API
        object is handed to the injected callback so the caller can close an
        owned client before allowing another generation to use it.
        The callback must return only after that client is safe to reuse or has
        been conclusively closed.  If it raises, its API object is retained
        and the same callback is invoked again on the next explicit restart
        attempt.
        """
        stale_api = None
        resolve_restart_fence = False
        capture_stale_api = False
        with self._condition:
            worker = self._worker_thread
            if self._direct_inflight:
                self._restart_blocked_by_worker = True
                raise self._error_factory(
                    "Cannot restart while the previous funding refresh worker is still running"
                )
            if self._restart_preparing:
                raise self._error_factory(
                    "Cannot restart while the previous funding reader is being closed"
                )
            if worker is not None and worker.is_alive():
                if self._restart_blocked_by_worker or self._stop_requested:
                    raise self._error_factory(
                        "Cannot restart while the previous funding refresh worker is still running"
                    )
                return
            if worker is not None:
                self._worker_thread = None
            if self._restart_blocked_by_worker:
                # Keep the fence raised until stale-client handling completes.
                # Other direct readers observe the blocked state and cannot
                # acquire an API reference while the close callback runs.
                self._restart_preparing = True
                resolve_restart_fence = True
                if self._restart_api_pending:
                    stale_api = self._restart_api
                else:
                    capture_stale_api = True

        if not resolve_restart_fence:
            return

        try:
            if capture_stale_api:
                stale_api = self._api_getter()
                with self._condition:
                    self._restart_api = stale_api
                    self._restart_api_pending = True
            if stale_api is not None:
                if self._on_stale_worker_restart is None:
                    raise self._error_factory(
                        "Cannot restart after a funding worker timeout without an API restart callback"
                    )
                self._on_stale_worker_restart(stale_api)
        except Exception:
            with self._condition:
                self._restart_preparing = False
                self._condition.notify_all()
            raise
        with self._condition:
            # Atomic release: every competing caller sees either the blocked
            # state or a completed stale-client callback, never the gap
            # between those states.
            self._restart_preparing = False
            self._restart_api_pending = False
            self._restart_api = None
            self._restart_blocked_by_worker = False
            self._condition.notify_all()

    def begin_generation(self) -> None:
        """Create an empty cache generation for one client session."""
        with self._condition:
            worker = self._worker_thread
            if (
                self._restart_blocked_by_worker
                or self._restart_preparing
                or self._direct_inflight
                or (worker is not None and worker.is_alive())
            ):
                self._restart_blocked_by_worker = True
                raise self._error_factory(
                    "Cannot begin a funding generation until the previous reader is safe to reuse"
                )
            self._generation += 1
            self._cache.clear()
            self._last_errors.clear()
            self._last_requested.clear()
            self._queue.clear()
            self._pending.clear()
            self._inflight_key = None
            self._stop_requested = False
            self._accept_results = True
            self._restart_blocked_by_worker = False
            self._condition.notify_all()

    def prepare_if_restart_blocked(self) -> None:
        """Resolve only a prior timeout fence without rejecting ordinary reads."""
        with self._condition:
            restart_blocked = self._restart_blocked_by_worker
        if restart_blocked:
            self.prepare_start()

    def _start_worker_locked(self) -> None:
        worker = self._worker_thread
        if worker is not None and worker.is_alive():
            return
        worker = threading.Thread(
            target=self._run_worker,
            name=self._worker_name,
            daemon=True,
        )
        self._worker_thread = worker
        worker.start()

    def _run_worker(self) -> None:
        current = threading.current_thread()
        try:
            while True:
                with self._condition:
                    while not self._queue and not self._stop_requested:
                        self._condition.wait(timeout=0.05)
                    if self._stop_requested and not self._queue:
                        return
                    generation, key, dataname, api = self._queue.popleft()
                    if (
                        generation != self._generation
                        or not self._accept_results
                        or api is not self._api_getter()
                    ):
                        self._pending.discard(key)
                        self._health["stale_generation_results"] += 1
                        self._condition.notify_all()
                        continue
                    self._inflight_key = key
                    self._health["dequeued"] += 1

                snapshot = None
                error = None
                try:
                    with self._transport_lock:
                        with self._condition:
                            can_read = bool(
                                generation == self._generation
                                and self._accept_results
                                and api is self._api_getter()
                            )
                        if can_read:
                            snapshot = self._read_snapshot(api, dataname)
                except Exception as exc:
                    try:
                        self._observe_error(exc)
                    except Exception:
                        # Diagnostic callbacks must not strand the lane's
                        # pending key or prevent later metadata reads.
                        pass
                    error = exc

                with self._condition:
                    self._pending.discard(key)
                    self._inflight_key = None
                    if (
                        generation != self._generation
                        or not self._accept_results
                        or api is not self._api_getter()
                    ):
                        self._health["stale_generation_results"] += 1
                    elif error is not None:
                        self._record_refresh_error_locked(key, error, generation)
                    elif snapshot is not None:
                        self._publish_snapshot_locked(key, snapshot, generation)
                    self._condition.notify_all()
        finally:
            with self._condition:
                if self._worker_thread is current:
                    self._worker_thread = None
                self._inflight_key = None
                self._condition.notify_all()

    def signal_stop(self) -> None:
        """Fence new publications and discard metadata reads not yet started."""
        with self._condition:
            self._accept_results = False
            self._stop_requested = True
            while self._queue:
                _generation, key, _dataname, _api = self._queue.popleft()
                self._pending.discard(key)
                self._health["discarded_unsent"] += 1
            self._condition.notify_all()

    def stop_worker(self, timeout: float) -> bool:
        """Wait a bounded time for direct and queued metadata reads to stop."""
        self.signal_stop()
        deadline = self._monotonic() + max(float(timeout), 0.0)
        with self._condition:
            while self._direct_inflight:
                remaining = deadline - self._monotonic()
                if remaining <= 0:
                    self._health["worker_stop_timeouts"] += 1
                    self._restart_blocked_by_worker = True
                    return False
                self._condition.wait(timeout=remaining)
            worker = self._worker_thread
        if worker is None:
            return True
        worker.join(max(deadline - self._monotonic(), 0.0))
        stopped = not worker.is_alive()
        with self._condition:
            if stopped and self._worker_thread is worker:
                self._worker_thread = None
            if not stopped:
                self._health["worker_stop_timeouts"] += 1
                self._restart_blocked_by_worker = True
            self._condition.notify_all()
        return stopped

    def read_direct(self, dataname: str) -> Mapping[str, Any]:
        """Read one synchronous snapshot through the same transport lock."""
        return self.read_direct_value(dataname, self._read_snapshot)

    def read_direct_value(
        self,
        dataname: str,
        reader: Callable[[Any, str], Any],
    ) -> Any:
        """Read an exact value through the funding lane's guarded transport.

        This supports SDK APIs that must return an exact typed DTO instead of
        the normalized mapping used by the funding cache.  The same restart
        gate, direct-inflight counter, API acquisition, and transport lock
        apply to both read shapes.
        """
        self._begin_direct_read()
        try:
            api = self._ensure_api_ready()
            with self._transport_lock:
                return reader(api, dataname)
        finally:
            with self._condition:
                self._direct_inflight -= 1
                self._condition.notify_all()

    def _begin_direct_read(self) -> None:
        """Acquire a direct-read slot, resolving a prior timeout if needed."""
        while True:
            with self._condition:
                restart_blocked = self._restart_blocked_by_worker
                if not restart_blocked:
                    if self._stop_requested and self._session_active():
                        raise self._error_factory(
                            "The session is stopping; funding reads are unavailable"
                        )
                    self._direct_inflight += 1
                    return
            # Ordinary direct reads remain concurrent at the API boundary and
            # serialize on the transport lock.  Restart preparation is needed
            # only after a previous stop timed out and fenced this generation.
            self.prepare_start()

    @staticmethod
    def compat_snapshot(snapshot: Mapping[str, Any]) -> Dict[str, Any]:
        """Preserve the historical synchronous API's Unix timestamp shape."""
        result = deepcopy(dict(snapshot))
        next_funding_time = result.get("next_funding_time")
        if isinstance(next_funding_time, _dt.datetime):
            if next_funding_time.tzinfo is None:
                next_funding_time = next_funding_time.replace(tzinfo=_dt.timezone.utc)
            result["next_funding_time"] = next_funding_time.timestamp()
        return result

    def _canonical_snapshot(
        self, snapshot: Mapping[str, Any], key: FundingCacheKey
    ) -> Dict[str, Any]:
        result = deepcopy(dict(snapshot))
        freshness = result.get("freshness")
        if is_dataclass(freshness) and not isinstance(freshness, type):
            freshness = asdict(freshness)
        elif isinstance(freshness, Mapping):
            freshness = deepcopy(dict(freshness))
        if freshness is not None:
            result["freshness"] = freshness
        if not self._typed_snapshot_required():
            result.setdefault("exchange_name", key[0])
            result.setdefault("symbol", key[1])
        return result

    def _snapshot_invalid_reason(
        self,
        snapshot: Mapping[str, Any],
        now_epoch: float,
        expected_key: Optional[FundingCacheKey] = None,
        max_age_seconds: Optional[float] = None,
    ) -> str:
        if expected_key is not None:
            exchange_name = snapshot.get("exchange_name")
            symbol = snapshot.get("symbol")
            if exchange_name in (None, ""):
                return "funding_exchange_name_missing"
            if str(exchange_name) != expected_key[0]:
                return "funding_exchange_name_mismatch"
            if symbol in (None, ""):
                return "funding_symbol_missing"
            if str(symbol) != expected_key[1]:
                return "funding_symbol_mismatch"

        freshness = snapshot.get("freshness")
        if snapshot.get("available") is not True:
            if isinstance(freshness, Mapping):
                reason = str(freshness.get("stale_reason") or "").strip()
                if reason:
                    return reason
            return "funding_unavailable"
        if not isinstance(freshness, Mapping):
            return "funding_freshness_missing"
        if freshness.get("stale") is not False:
            return str(freshness.get("stale_reason") or "funding_stale")

        if expected_key is not None:
            observed_at = freshness.get("observed_at")
            if observed_at in (None, ""):
                return "funding_observed_at_missing"
            if not isinstance(observed_at, _dt.datetime):
                return "funding_observed_at_invalid"
            if observed_at.tzinfo is None or observed_at.utcoffset() is None:
                return "funding_observed_at_timezone_missing"
            try:
                observed_epoch = float(observed_at.timestamp())
            except (OverflowError, OSError, ValueError):
                return "funding_observed_at_invalid"
            if not math.isfinite(observed_epoch):
                return "funding_observed_at_invalid"
            if observed_epoch > now_epoch:
                return "funding_observed_at_in_future"
            if max_age_seconds is not None and now_epoch - observed_epoch >= max_age_seconds:
                return "funding_cache_ttl_expired"
            if self._validate_snapshot is not None:
                reason = self._validate_snapshot(snapshot, now_epoch, expected_key)
                if reason:
                    return str(reason)
        return ""

    @staticmethod
    def _next_epoch(snapshot: Mapping[str, Any]) -> Optional[float]:
        value = snapshot.get("next_funding_time")
        try:
            if isinstance(value, _dt.datetime):
                if value.tzinfo is None or value.utcoffset() is None:
                    return None
                value = value.timestamp()
            result = float(Decimal(str(value)))
        except (InvalidOperation, TypeError, ValueError, OverflowError):
            return None
        return result if math.isfinite(result) else None

    @staticmethod
    def _source_age_seconds(snapshot: Mapping[str, Any], now_epoch: float) -> float:
        freshness = snapshot.get("freshness")
        if not isinstance(freshness, Mapping):
            return 0.0
        observed_at = freshness.get("observed_at")
        if not isinstance(observed_at, _dt.datetime):
            return 0.0
        return max(now_epoch - observed_at.timestamp(), 0.0)

    @staticmethod
    def _is_transport_error(exc: BaseException) -> bool:
        if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
            return True
        if bool(getattr(exc, "transport_error", False)) or bool(
            getattr(exc, "retryable", False)
        ):
            return True
        name = type(exc).__name__.lower()
        return any(token in name for token in ("connection", "network", "timeout", "transport"))

    @staticmethod
    def _typed_transport_failure(snapshot: Mapping[str, Any]) -> bool:
        freshness = snapshot.get("freshness")
        return bool(
            snapshot.get("available") is False
            and snapshot.get("unavailable_reason") == "funding_transport_failed"
            and isinstance(freshness, Mapping)
            and freshness.get("stale") is True
            and freshness.get("stale_reason") == "funding_transport_failed"
        )

    def _has_unexpired_record_locked(
        self,
        key: FundingCacheKey,
        generation: int,
        now_monotonic: float,
        now_epoch: float,
    ) -> bool:
        record = self._cache.get(key)
        if (
            record is None
            or int(record.get("generation", -1)) != generation
            or record.get("invalid_reason")
            or now_monotonic >= float(record.get("deadline_monotonic", 0.0))
        ):
            return False
        return not self._snapshot_invalid_reason(
            record["snapshot"],
            now_epoch,
            expected_key=key if self._typed_snapshot_required() else None,
            max_age_seconds=self.max_age_seconds if self._typed_snapshot_required() else None,
        )

    def _publish_snapshot_locked(
        self, key: FundingCacheKey, snapshot: Mapping[str, Any], generation: int
    ) -> Dict[str, Any]:
        now_monotonic = self._monotonic()
        now_epoch = self._wall_time()
        normalized = self._canonical_snapshot(snapshot, key)
        invalid_reason = self._snapshot_invalid_reason(
            normalized,
            now_epoch,
            expected_key=key if self._typed_snapshot_required() else None,
            max_age_seconds=self.max_age_seconds if self._typed_snapshot_required() else None,
        )
        if invalid_reason == "funding_transport_failed" and self._typed_transport_failure(
            normalized
        ):
            self._last_errors[key] = invalid_reason
            self._health["failed"] += 1
            self._health["transport_errors"] += 1
            if self._has_unexpired_record_locked(key, generation, now_monotonic, now_epoch):
                return normalized

        next_epoch = self._next_epoch(normalized)
        source_age = (
            self._source_age_seconds(normalized, now_epoch)
            if self._typed_snapshot_required() and not invalid_reason
            else 0.0
        )
        source_origin = now_monotonic - source_age
        ttl_deadline = source_origin + self.max_age_seconds
        schedule_deadline = (
            None if next_epoch is None else now_monotonic + max(next_epoch - now_epoch, 0.0)
        )
        deadline = (
            ttl_deadline if schedule_deadline is None else min(ttl_deadline, schedule_deadline)
        )
        if invalid_reason:
            normalized["available"] = False
            freshness = normalized.get("freshness")
            if not isinstance(freshness, Mapping):
                freshness = {"source": self._cache_source, "observed_at": None}
            else:
                freshness = dict(freshness)
            freshness["stale"] = True
            freshness["stale_reason"] = invalid_reason
            normalized["freshness"] = freshness
            self._last_errors[key] = invalid_reason
            self._health["unavailable"] += 1
        else:
            self._last_errors.pop(key, None)
            self._health["available"] += 1
        self._cache[key] = {
            "snapshot": normalized,
            "stored_monotonic": now_monotonic,
            "source_age_at_store_seconds": source_age,
            "source_origin_monotonic": source_origin,
            "deadline_monotonic": deadline,
            "schedule_deadline_monotonic": schedule_deadline,
            "next_funding_epoch": next_epoch,
            "generation": generation,
            "invalid_reason": invalid_reason,
        }
        self._health["completed"] += 1
        return normalized

    def _record_refresh_error_locked(
        self, key: FundingCacheKey, exc: BaseException, generation: int
    ) -> None:
        error_code = self._error_code(exc, type(exc).__name__)
        self._last_errors[key] = error_code
        self._health["failed"] += 1
        if self._is_transport_error(exc):
            self._health["transport_errors"] += 1
            return
        self._health["contract_errors"] += 1
        now = self._utcnow()
        unavailable = {
            "exchange_name": key[0],
            "symbol": key[1],
            "available": False,
            "source": self._cache_source,
            "freshness": {
                "source": self._cache_source,
                "observed_at": now,
                "stale": True,
                "stale_reason": "funding_refresh_failed",
            },
        }
        self._publish_snapshot_locked(key, unavailable, generation)
        self._last_errors[key] = error_code

    def _cache_view_locked(
        self, key: FundingCacheKey, max_age_seconds: float
    ) -> Dict[str, Any]:
        now_monotonic = self._monotonic()
        now_epoch = self._wall_time()
        record = self._cache.get(key)
        pending = key in self._pending
        generation = self._generation
        last_error = self._last_errors.get(key)
        cache_age = None
        deadline = None
        invalid_reason = "funding_cache_missing"

        if record is None:
            result = {
                "exchange_name": key[0],
                "symbol": key[1],
                "available": False,
                "source": self._cache_source,
                "freshness": {
                    "source": self._cache_source,
                    "observed_at": None,
                    "stale": True,
                    "stale_reason": invalid_reason,
                },
            }
            cache_generation = generation
        else:
            result = deepcopy(record["snapshot"])
            cache_generation = int(record["generation"])
            local_age = max(now_monotonic - float(record["stored_monotonic"]), 0.0)
            source_age = max(float(record.get("source_age_at_store_seconds", 0.0)), 0.0)
            cache_age = source_age + local_age
            source_origin = float(
                record.get(
                    "source_origin_monotonic",
                    float(record["stored_monotonic"]) - source_age,
                )
            )
            age_deadline = source_origin + max_age_seconds
            schedule_deadline = record.get("schedule_deadline_monotonic")
            configured_deadline = float(record.get("deadline_monotonic", age_deadline))
            deadlines = [age_deadline, configured_deadline]
            if schedule_deadline is not None:
                deadlines.append(float(schedule_deadline))
            deadline = min(deadlines)
            invalid_reason = str(record.get("invalid_reason") or "")
            if cache_generation != generation:
                invalid_reason = "funding_cache_generation_mismatch"
            elif generation > 0 and not self._accept_results:
                invalid_reason = "funding_cache_generation_inactive"
            elif max_age_seconds <= 0 or cache_age >= max_age_seconds:
                invalid_reason = "funding_cache_ttl_expired"
            elif deadline is not None and now_monotonic >= deadline:
                invalid_reason = (
                    "funding_schedule_expired"
                    if schedule_deadline is not None and now_monotonic >= float(schedule_deadline)
                    else "funding_cache_ttl_expired"
                )
            else:
                next_epoch = record.get("next_funding_epoch")
                if next_epoch is not None and float(next_epoch) <= now_epoch:
                    invalid_reason = "funding_schedule_expired"
                elif not invalid_reason:
                    invalid_reason = self._snapshot_invalid_reason(
                        result,
                        now_epoch,
                        expected_key=key if self._typed_snapshot_required() else None,
                        max_age_seconds=max_age_seconds if self._typed_snapshot_required() else None,
                    )

            if invalid_reason:
                result["available"] = False
                freshness = result.get("freshness")
                freshness = dict(freshness) if isinstance(freshness, Mapping) else {}
                freshness.setdefault("source", result.get("source") or self._cache_source)
                freshness.setdefault("observed_at", None)
                freshness["stale"] = True
                freshness["stale_reason"] = invalid_reason
                result["freshness"] = freshness

        result.update(
            cache_age_seconds=cache_age,
            cache_deadline_monotonic=deadline,
            cache_generation=cache_generation,
            funding_generation=generation,
            refresh_pending=pending,
            last_refresh_error=last_error,
        )
        return result

    def request(self, dataname: str, *, force: bool = False) -> Dict[str, Any]:
        """Coalesce a non-blocking refresh onto the metadata-only worker."""
        key = self._cache_key(str(dataname))
        now = self._monotonic()
        with self._condition:
            api = self._api_getter()
            if (
                not self._can_queue_refresh()
                or not self._accept_results
                or self._stop_requested
                or api is None
            ):
                return {"queued": False, "status": self._inactive_status}
            if key in self._pending:
                self._health["coalesced"] += 1
                return {"queued": True, "status": "already_pending"}
            last_requested = self._last_requested.get(key)
            if (
                not force
                and last_requested is not None
                and now - last_requested < self.refresh_interval_seconds
            ):
                self._health["throttled"] += 1
                return {"queued": False, "status": "refresh_interval"}
            generation = self._generation
            self._last_requested[key] = now
            self._pending.add(key)
            self._queue.append((generation, key, str(dataname), api))
            self._health["requested"] += 1
            self._start_worker_locked()
            self._condition.notify_all()
            return {
                "queued": True,
                "status": "queued",
                "exchange_name": key[0],
                "symbol": key[1],
                "generation": generation,
            }

    def wait(self, timeout: Optional[float] = None) -> bool:
        """Wait for queued/direct work, primarily for session shutdown handling."""
        actual_timeout = self.shutdown_timeout if timeout is None else max(float(timeout), 0.0)
        deadline = self._monotonic() + actual_timeout
        with self._condition:
            while (
                self._queue
                or self._inflight_key is not None
                or self._direct_inflight
                or (
                    self._stop_requested
                    and self._worker_thread is not None
                    and self._worker_thread.is_alive()
                )
            ):
                remaining = deadline - self._monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(timeout=remaining)
        return True

    def get_cached(
        self,
        dataname: str,
        *,
        max_age_seconds: Optional[float] = None,
        request_refresh: bool = True,
    ) -> Dict[str, Any]:
        """Return a local cache view and optionally queue a refresh."""
        max_age = self.max_age_seconds if max_age_seconds is None else float(max_age_seconds)
        if not math.isfinite(max_age) or max_age < 0:
            raise ValueError("max_age_seconds must be finite and nonnegative")
        key = self._cache_key(str(dataname))
        with self._condition:
            view = self._cache_view_locked(key, max_age)
        should_refresh = bool(
            request_refresh
            and (
                view.get("available") is not True
                or view.get("cache_age_seconds") is None
                or float(view["cache_age_seconds"]) >= self.refresh_interval_seconds
            )
        )
        if should_refresh:
            self.request(str(dataname))
            with self._condition:
                view = self._cache_view_locked(key, max_age)
        return view

    def get_health(self, dataname: Optional[str] = None) -> Dict[str, Any]:
        """Return a self-consistent snapshot of lane health and cache entries."""
        key = None if dataname is None else self._cache_key(str(dataname))
        with self._condition:
            entries = {}
            for entry_key, record in self._cache.items():
                entries["{}:{}".format(entry_key[0], entry_key[1])] = {
                    "generation": record["generation"],
                    "stored_monotonic": record["stored_monotonic"],
                    "source_age_at_store_seconds": record.get(
                        "source_age_at_store_seconds", 0.0
                    ),
                    "deadline_monotonic": record["deadline_monotonic"],
                    "invalid_reason": record["invalid_reason"],
                    "last_refresh_error": self._last_errors.get(entry_key),
                    "pending": entry_key in self._pending,
                }
            last_error = self._last_errors.get(key) if key is not None else None
            if key is None and self._last_errors:
                last_error = next(reversed(self._last_errors.values()))
            result = {
                **dict(self._health),
                "generation": self._generation,
                "worker_alive": bool(
                    self._worker_thread and self._worker_thread.is_alive()
                ),
                "queue_depth": len(self._queue),
                "inflight": bool(self._inflight_key is not None or self._direct_inflight),
                "inflight_key": self._inflight_key,
                "direct_inflight": self._direct_inflight,
                "pending": len(self._pending),
                "accepting_results": self._accept_results,
                "restart_blocked_by_worker": self._restart_blocked_by_worker,
                "restart_preparing": self._restart_preparing,
                "restart_api_pending": self._restart_api_pending,
                "last_refresh_error": last_error,
                "cache_entries": entries,
            }
            for counter in (
                "requested",
                "dequeued",
                "completed",
                "available",
                "unavailable",
                "failed",
                "transport_errors",
                "contract_errors",
                "coalesced",
                "throttled",
                "discarded_unsent",
                "stale_generation_results",
                "worker_stop_timeouts",
            ):
                result.setdefault(counter, 0)
            return result

    def get_snapshot(self, dataname: str) -> Dict[str, Any]:
        """Read, validate, publish, and return the historical mapping shape."""
        key = self._cache_key(str(dataname))
        with self._condition:
            generation = self._generation
        try:
            snapshot = self.read_direct(dataname)
        except Exception as exc:
            try:
                self._observe_error(exc)
            except Exception:
                pass
            with self._condition:
                if generation == self._generation and (
                    self._accept_results or generation == 0
                ):
                    self._record_refresh_error_locked(key, exc, generation)
            raise
        with self._condition:
            if generation == self._generation and (self._accept_results or generation == 0):
                published = self._publish_snapshot_locked(key, snapshot, generation)
            else:
                published = snapshot
        return self.compat_snapshot(published)



__all__ = (
    "FundingCacheKey", "FundingRefreshController", "FundingRefreshError",
    "normalize_funding_refresh_options",
)
