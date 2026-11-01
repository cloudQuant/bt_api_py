"""SDK-owned bounded read-only metadata probing for a supplied session.

The caller supplies lifecycle and data callbacks. This workflow does not
establish provider identity or grant account or execution authority.
"""

from __future__ import annotations

import datetime as _dt
import math
import threading
from collections.abc import Mapping, Sequence
from dataclasses import is_dataclass
from typing import Any, Callable, Dict, Optional, Tuple


_ERROR_MESSAGES = {
    "invalid_timeout": "bounded read-only metadata probe timeout is invalid",
    "invalid_datanames": "bounded read-only metadata probe datanames are invalid",
    "already_active": "bounded read-only metadata probe is already active",
    "timeout": "bounded read-only metadata probe timed out before shutdown was proven",
    "typed_metadata_incomplete": (
        "bounded read-only metadata probe returned incomplete typed metadata"
    ),
    "shutdown_incomplete": "bounded read-only metadata probe shutdown is incomplete",
    "probe_stopped": "bounded read-only metadata probe was stopped before completion",
    "probe_failed": "bounded read-only metadata probe failed",
}


class BoundedMetadataProbeError(RuntimeError):
    """Finite, non-sensitive error projection for a metadata probe."""

    def __init__(self, code: str) -> None:
        if type(code) is not str or code not in _ERROR_MESSAGES:
            code = "probe_failed"
        self.code = code
        super().__init__(_ERROR_MESSAGES[code])


class _DeadlineReached(Exception):
    pass


class _StopRequested(Exception):
    pass


class _BoundedMetadataProbeWorker:
    """Run one read-only metadata pass on a caller-owned SDK session.

    ``prepare_session`` and ``close_session`` remain caller callbacks so the
    component never owns or closes the SDK client itself.  Read callbacks are
    called serially on the probe thread.  Validators must return the original
    typed contract (or another typed normalized value) and raise on invalid
    evidence.  ``shutdown_proven`` must return the exact boolean ``True``;
    truthy substitutes cannot establish cleanup.
    """

    def __init__(self, *, worker_name: str = "SdkReadOnlyMetadataProbe") -> None:
        if type(worker_name) is not str or not worker_name or worker_name != worker_name.strip():
            raise ValueError("worker_name must be non-empty trimmed text")
        self._worker_name = worker_name
        self._condition = threading.Condition(threading.RLock())
        self._worker: Optional[threading.Thread] = None
        self._active = False
        self._generation = 0
        self._stop_event: Optional[threading.Event] = None
        self._last_timed_out = False
        self._last_error_code = ""

    def is_active(self) -> bool:
        """Whether a probe owns the SDK lane or its worker is still exiting."""

        with self._condition:
            worker = self._worker
            return bool(self._active or (worker is not None and worker.is_alive()))

    @property
    def worker_alive(self) -> bool:
        with self._condition:
            worker = self._worker
            return bool(worker is not None and worker.is_alive())

    def owns_current_thread(self) -> bool:
        """Whether the caller is the worker allowed to perform SDK shutdown."""

        with self._condition:
            return self._worker is threading.current_thread()

    def health(self) -> Dict[str, Any]:
        """Return bounded lifecycle facts without SDK objects or error text."""

        with self._condition:
            worker = self._worker
            worker_alive = bool(worker is not None and worker.is_alive())
            return {
                "active": bool(self._active or worker_alive),
                "worker_alive": worker_alive,
                "generation": self._generation,
                "stop_requested": bool(self._stop_event and self._stop_event.is_set()),
                "timed_out": self._last_timed_out,
                "last_error_code": self._last_error_code,
            }

    def stop_and_join(self, timeout: float) -> bool:
        """Fence another metadata read and wait boundedly for its owner.

        The synchronous SDK call itself is not cancelled.  Its owner observes
        the fence after that call returns, skips subsequent reads, and runs
        ``close_session``.  A self-join is rejected so a caller callback running
        on the owner thread cannot mistake itself for a stopped worker.
        """

        wait_timeout = self._validated_join_timeout(timeout, threading.TIMEOUT_MAX)
        with self._condition:
            worker = self._worker
            if worker is None:
                return not self._active
            if worker is threading.current_thread():
                if self._stop_event is not None:
                    self._stop_event.set()
                return False
            if self._active and self._stop_event is not None:
                self._stop_event.set()
        worker.join(wait_timeout)
        return not worker.is_alive() and not self.is_active()

    def run(
        self,
        datanames: Sequence[str],
        timeout_seconds: float,
        *,
        prepare_session: Callable[[], Any],
        close_session: Callable[[float], Any],
        read_instrument: Callable[[Any, str], Any],
        read_funding: Callable[[Any, str], Any],
        validate_instrument: Callable[[Any, str], Any],
        validate_funding: Callable[[Any, str], Any],
        shutdown_proven: Callable[[Any], bool],
        shutdown_timeout: float,
        timeout_max: float,
        monotonic: Callable[[], float],
        thread_factory: Callable[..., threading.Thread],
        error_projector: Callable[[BaseException], str],
    ) -> Dict[str, Any]:
        """Run the complete two-family read and return only after worker exit.

        On a caller deadline, this raises a finite ``timeout`` error while the
        background worker remains registered and fenced.  Partial results are
        never returned or retained by the component.
        """

        symbols, wait_timeout = self.validate_inputs(
            datanames,
            timeout_seconds,
            timeout_max=timeout_max,
        )
        shutdown_wait = self._validated_shutdown_timeout(shutdown_timeout, timeout_max)
        callbacks = (
            prepare_session,
            close_session,
            read_instrument,
            read_funding,
            validate_instrument,
            validate_funding,
            shutdown_proven,
            monotonic,
            thread_factory,
            error_projector,
        )
        if any(not callable(callback) for callback in callbacks):
            raise BoundedMetadataProbeError("probe_failed")

        try:
            deadline = float(monotonic()) + wait_timeout
        except BaseException:
            raise BoundedMetadataProbeError("probe_failed") from None

        state: Dict[str, Any] = {
            "instrument_specs": {},
            "funding_snapshots": {},
            "session_health": None,
            "timed_out": False,
            "error_code": "",
        }
        stop_event = threading.Event()
        with self._condition:
            worker = self._worker
            if self._active or (worker is not None and worker.is_alive()):
                raise BoundedMetadataProbeError("already_active")
            self._generation += 1
            generation = self._generation
            self._active = True
            self._stop_event = stop_event
            self._last_timed_out = False
            self._last_error_code = ""

            def remaining() -> float:
                try:
                    return max(deadline - float(monotonic()), 0.0)
                except BaseException:
                    return 0.0

            def assert_running() -> None:
                if stop_event.is_set():
                    raise _StopRequested()
                if remaining() <= 0:
                    state["timed_out"] = True
                    raise _DeadlineReached()

            def project_error(error: BaseException) -> str:
                try:
                    code = error_projector(error)
                except BaseException:
                    return "probe_failed"
                if type(code) is str and code in {
                    "typed_metadata_incomplete",
                    "shutdown_incomplete",
                    "probe_failed",
                }:
                    return code
                return "probe_failed"

            def worker_main() -> None:
                api = None
                try:
                    assert_running()
                    api = prepare_session()
                    for symbol in symbols:
                        assert_running()
                        instrument = read_instrument(api, symbol)
                        state["instrument_specs"][symbol] = validate_instrument(
                            instrument, symbol
                        )
                        assert_running()
                        funding = read_funding(api, symbol)
                        state["funding_snapshots"][symbol] = validate_funding(
                            funding, symbol
                        )
                        assert_running()
                except _DeadlineReached:
                    state["timed_out"] = True
                except _StopRequested:
                    state["error_code"] = "probe_stopped"
                except TimeoutError:
                    state["timed_out"] = True
                except BaseException as error:
                    state["error_code"] = project_error(error)
                finally:
                    close_wait = remaining()
                    if close_wait <= 0:
                        close_wait = shutdown_wait
                    try:
                        health = close_session(close_wait)
                        state["session_health"] = health
                        try:
                            proven = shutdown_proven(health)
                        except BaseException:
                            proven = False
                        if type(proven) is not bool or proven is not True:
                            if not state["error_code"]:
                                state["error_code"] = "shutdown_incomplete"
                    except BaseException as error:
                        if isinstance(error, TimeoutError):
                            state["timed_out"] = True
                        elif not state["error_code"]:
                            state["error_code"] = project_error(error)
                    if remaining() <= 0:
                        state["timed_out"] = True
                    with self._condition:
                        if (
                            self._generation == generation
                            and self._worker is threading.current_thread()
                        ):
                            self._last_timed_out = bool(state["timed_out"])
                            self._last_error_code = state["error_code"]
                            self._active = False
                        self._condition.notify_all()

            try:
                worker = thread_factory(
                    target=worker_main,
                    name=self._worker_name,
                    daemon=True,
                )
                self._worker = worker
                worker.start()
            except BaseException:
                self._active = False
                self._worker = None
                self._stop_event = None
                self._last_error_code = "probe_failed"
                self._condition.notify_all()
                raise BoundedMetadataProbeError("probe_failed") from None

        worker.join(wait_timeout)
        if worker.is_alive():
            # Do not clear the worker or active fence.  A caller may stop and
            # join it later; until then it still owns the shared SDK lane.
            with self._condition:
                self._last_timed_out = True
                self._last_error_code = "timeout"
            raise BoundedMetadataProbeError("timeout")

        if state["timed_out"] or remaining() <= 0:
            with self._condition:
                self._last_timed_out = True
                self._last_error_code = "timeout"
            raise BoundedMetadataProbeError("timeout")
        if state["error_code"]:
            raise BoundedMetadataProbeError(state["error_code"])
        return {
            "instrument_specs": dict(state["instrument_specs"]),
            "funding_snapshots": dict(state["funding_snapshots"]),
            "session_health": state["session_health"],
        }

    @staticmethod
    def validate_inputs(
        datanames: Sequence[str],
        timeout_seconds: float,
        *,
        timeout_max: float,
    ) -> Tuple[Tuple[str, ...], float]:
        """Validate public input in the legacy timeout-then-symbol order."""

        timeout = _BoundedMetadataProbeWorker._validated_timeout(timeout_seconds, timeout_max)
        symbols = _BoundedMetadataProbeWorker._validated_symbols(datanames)
        return symbols, timeout

    @staticmethod
    def _validated_timeout(value: Any, timeout_max: Any) -> float:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or isinstance(timeout_max, bool)
            or not isinstance(timeout_max, (int, float))
        ):
            raise BoundedMetadataProbeError("invalid_timeout")
        try:
            selected = float(value)
            maximum = float(timeout_max)
        except (OverflowError, ValueError):
            raise BoundedMetadataProbeError("invalid_timeout") from None
        if (
            not math.isfinite(selected)
            or not math.isfinite(maximum)
            or selected <= 0
            or maximum <= 0
            or selected > maximum
        ):
            raise BoundedMetadataProbeError("invalid_timeout")
        return selected

    @staticmethod
    def _validated_join_timeout(value: Any, timeout_max: Any) -> float:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or isinstance(timeout_max, bool)
            or not isinstance(timeout_max, (int, float))
        ):
            raise BoundedMetadataProbeError("invalid_timeout")
        try:
            selected = float(value)
            maximum = float(timeout_max)
        except (OverflowError, ValueError):
            raise BoundedMetadataProbeError("invalid_timeout") from None
        if (
            not math.isfinite(selected)
            or not math.isfinite(maximum)
            or selected < 0
            or maximum <= 0
            or selected > maximum
        ):
            raise BoundedMetadataProbeError("invalid_timeout")
        return selected

    @staticmethod
    def _validated_shutdown_timeout(value: Any, timeout_max: float) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise BoundedMetadataProbeError("invalid_timeout")
        try:
            selected = float(value)
        except (OverflowError, ValueError):
            raise BoundedMetadataProbeError("invalid_timeout") from None
        if not math.isfinite(selected) or selected < 0 or selected > timeout_max:
            raise BoundedMetadataProbeError("invalid_timeout")
        return selected

    @staticmethod
    def _validated_symbols(datanames: Sequence[str]) -> Tuple[str, ...]:
        if isinstance(datanames, (str, bytes)) or not isinstance(datanames, Sequence):
            raise BoundedMetadataProbeError("invalid_datanames")
        if any(not isinstance(dataname, str) for dataname in datanames):
            raise BoundedMetadataProbeError("invalid_datanames")
        symbols = tuple(dataname.strip() for dataname in datanames)
        if (
            not symbols
            or any(not symbol for symbol in symbols)
            or len(set(symbols)) != len(symbols)
        ):
            raise BoundedMetadataProbeError("invalid_datanames")
        return symbols


_INSTRUMENT_REQUIRED_FIELDS = (
    "contract_value",
    "contract_multiplier",
    "price_tick",
    "quantity_step",
    "min_quantity",
    "quantity_unit",
    "quote_currency",
)
_FUNDING_REQUIRED_FIELDS = (
    "rate",
    "next_funding_time",
    "settlement_interval_seconds",
    "source",
)


def _require_typed_contract(
    value: Any,
    *,
    expected_exchange_name: str,
    expected_symbol: str,
    required_fields: Tuple[str, ...],
) -> Any:
    """Require a fresh, complete typed SDK contract without remapping it."""

    if not is_dataclass(value) or isinstance(value, type):
        raise BoundedMetadataProbeError("typed_metadata_incomplete")
    if (
        str(getattr(value, "exchange_name", "")) != expected_exchange_name
        or str(getattr(value, "symbol", "")) != expected_symbol
        or getattr(value, "available", None) is not True
    ):
        raise BoundedMetadataProbeError("typed_metadata_incomplete")
    freshness = getattr(value, "freshness", None)
    observed_at = getattr(freshness, "observed_at", None)
    if (
        not is_dataclass(freshness)
        or isinstance(freshness, type)
        or getattr(freshness, "stale", None) is not False
        or not isinstance(observed_at, _dt.datetime)
        or observed_at.tzinfo is None
        or observed_at.utcoffset() is None
        or any(getattr(value, field, None) in (None, "") for field in required_fields)
    ):
        raise BoundedMetadataProbeError("typed_metadata_incomplete")
    return value


class BoundedMetadataProbeService:
    """Own one complete read-only metadata probe operation at a time."""

    def __init__(self, *, worker_name: str = "SdkReadOnlyMetadataProbe") -> None:
        self._worker = _BoundedMetadataProbeWorker(worker_name=worker_name)
        self._lock = threading.RLock()
        self._operation_reserved = False
        self._requires_market_data_only = False

    @property
    def requires_market_data_only(self) -> bool:
        with self._lock:
            return self._requires_market_data_only

    def is_active(self) -> bool:
        with self._lock:
            operation_reserved = self._operation_reserved
        return bool(operation_reserved or self._worker.is_active())

    @property
    def worker_alive(self) -> bool:
        return self._worker.worker_alive

    def owns_current_thread(self) -> bool:
        return self._worker.owns_current_thread()

    def run(
        self,
        datanames: Sequence[str],
        timeout_seconds: float,
        *,
        validate_owner: Callable[[], None],
        validate_idle: Callable[[], None],
        prepare_session: Callable[[], Any],
        verify_market_data_only: Callable[[], None],
        is_market_data_only: Callable[[], bool],
        close_session: Callable[[float], Any],
        read_instrument: Callable[[Any, str], Any],
        read_funding: Callable[[Any, str], Any],
        exchange_name_for: Callable[[str], str],
        set_accept_openings: Callable[[bool], None],
        shutdown_proven: Callable[[Any], bool],
        shutdown_timeout: float,
        timeout_max: float,
        monotonic: Callable[[], float],
        thread_factory: Callable[..., threading.Thread],
        on_error: Callable[[BaseException], None],
        error_projector: Callable[[BaseException], str],
    ) -> Dict[str, Any]:
        """Run preparation, typed reads, shutdown, and final projection.

        ``validate_owner`` and ``validate_idle`` preserve caller-specific
        preconditions.  They execute under the service reservation lock so a
        second probe cannot pass the same idle check concurrently.
        """

        symbols, timeout_value = self._worker.validate_inputs(
            datanames,
            timeout_seconds,
            timeout_max=timeout_max,
        )
        callbacks = (
            validate_owner,
            validate_idle,
            prepare_session,
            verify_market_data_only,
            is_market_data_only,
            close_session,
            read_instrument,
            read_funding,
            exchange_name_for,
            set_accept_openings,
            shutdown_proven,
            monotonic,
            thread_factory,
            on_error,
            error_projector,
        )
        if any(not callable(callback) for callback in callbacks):
            raise BoundedMetadataProbeError("probe_failed")

        with self._lock:
            validate_owner()
            if self._operation_reserved or self._worker.is_active():
                raise BoundedMetadataProbeError("already_active")
            validate_idle()
            self._operation_reserved = True
            self._requires_market_data_only = True

        def prepare_read_only_session() -> Any:
            set_accept_openings(False)
            api = prepare_session()
            verify_market_data_only()
            if not is_market_data_only():
                raise RuntimeError("market_data_only_guard_unavailable")
            set_accept_openings(False)
            return api

        def finish_session(wait_timeout: float) -> Any:
            try:
                return close_session(wait_timeout)
            finally:
                with self._lock:
                    self._requires_market_data_only = False
                    self._operation_reserved = False

        def validate_instrument(value: Any, symbol: str) -> Any:
            return _require_typed_contract(
                value,
                expected_exchange_name=str(exchange_name_for(symbol)),
                expected_symbol=symbol,
                required_fields=_INSTRUMENT_REQUIRED_FIELDS,
            )

        def validate_funding(value: Any, symbol: str) -> Any:
            return _require_typed_contract(
                value,
                expected_exchange_name=str(exchange_name_for(symbol)),
                expected_symbol=symbol,
                required_fields=_FUNDING_REQUIRED_FIELDS,
            )

        def project_error(error: BaseException) -> str:
            try:
                on_error(error)
            except BaseException:
                pass
            try:
                code = error_projector(error)
            except BaseException:
                return "probe_failed"
            return code if type(code) is str else "probe_failed"

        try:
            result = self._worker.run(
                symbols,
                timeout_value,
                prepare_session=prepare_read_only_session,
                close_session=finish_session,
                read_instrument=read_instrument,
                read_funding=read_funding,
                validate_instrument=validate_instrument,
                validate_funding=validate_funding,
                shutdown_proven=shutdown_proven,
                shutdown_timeout=shutdown_timeout,
                timeout_max=timeout_max,
                monotonic=monotonic,
                thread_factory=thread_factory,
                error_projector=project_error,
            )
        except BaseException:
            if not self._worker.is_active():
                with self._lock:
                    self._requires_market_data_only = False
                    self._operation_reserved = False
            raise

        return {
            "instrument_specs": dict(result["instrument_specs"]),
            "funding_snapshots": dict(result["funding_snapshots"]),
            "session_health": dict(result["session_health"]),
        }


__all__ = ("BoundedMetadataProbeError", "BoundedMetadataProbeService")
