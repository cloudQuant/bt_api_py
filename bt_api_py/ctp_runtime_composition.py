"""Compose one BtApi CTP request route with an existing CTP execution runtime.

This is an SDK-internal lifecycle bridge.  The caller supplies an already
started, ready ``CtpExecutionRuntime`` and the public CTP route configuration.
The composition creates exactly one ``BtApi`` with exactly one request feed,
binds that exact feed to the runtime-owned TraderClient before connecting it,
then publishes only the exact context registered and verified by the runtime.

Runtime context provenance proves assembly identity only.  It does not grant
order authority.  Any RegisteredSim authority, request-account validator,
submit-context resolver, or cancel-authorization validator must come from the
runtime's separately configured trusted dependency factory; without that
factory, those capabilities remain unavailable.  A market stream may be
passed explicitly to let the runtime construct its own instrument metadata
reader; arbitrary resolver objects are not accepted here.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Mapping
from typing import Any, Optional

__all__ = (
    "CTP_FUTURE_EXCHANGE_NAME",
    "CtpRuntimeApiCompositionError",
    "CtpRuntimeBtApiComposition",
    "compose_ctp_runtime_bt_api",
)


CTP_FUTURE_EXCHANGE_NAME = "CTP___FUTURE"
_DEFAULT_TIMEOUT_SECONDS = 5.0
_MAX_TIMEOUT_SECONDS = 30.0


class CtpRuntimeApiCompositionError(RuntimeError):
    """Finite composition failure that never includes configuration values."""

    def __init__(self, code: str, *, cleanup_code: Optional[str] = None) -> None:
        self.code = code
        self.cleanup_code = cleanup_code
        super().__init__(code)


class CtpRuntimeBtApiComposition:
    """Bound API handle with owner-thread API close and caller-owned runtime.

    ``api`` is the BtApi facade to pass to SDK consumers.  ``close_api``
    closes its single borrowed feed on the existing runtime owner thread.  The
    runtime itself remains owned by its existing ServiceHost/runtime lifecycle
    and must be closed there; this object never stops or recreates it.
    """

    def __init__(self, api: Any, runtime: Any, exchange_name: str) -> None:
        self._api = api
        self._runtime = runtime
        self._exchange_name = exchange_name
        self._close_lock = threading.Lock()
        self._close_attempted = False

    @property
    def api(self) -> Any:
        """Return the composed BtApi facade."""

        return self._api

    @property
    def exchange_name(self) -> str:
        return self._exchange_name

    def close_api(self, *, timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS) -> None:
        """Close the single-route BtApi on the CTP owner thread, once.

        This detaches the borrowed request feed.  It deliberately does not
        close the CTP runtime, which remains owned by its runtime host.
        """

        _validate_timeout(timeout_seconds)
        with self._close_lock:
            if self._close_attempted:
                raise CtpRuntimeApiCompositionError("ctp_api_close_already_attempted")
            self._close_attempted = True
        try:
            self._runtime.call_on_owner_thread(
                self._api.close,
                timeout_seconds=float(timeout_seconds),
            )
        except Exception as exc:
            cleanup_code = _owner_call_failure_code(
                exc,
                fallback="ctp_api_close_unconfirmed",
            )
            raise CtpRuntimeApiCompositionError(cleanup_code) from None


def compose_ctp_runtime_bt_api(
    *,
    runtime: Any,
    exchange_params: Mapping[str, Any],
    order_correlation_issuer: Any = None,
    market_stream: Any = None,
    owner_call_timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    instrument_metadata_timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> CtpRuntimeBtApiComposition:
    """Build one exact CTP BtApi request route over an existing runtime.

    The route name is fixed to ``CTP___FUTURE``.  ``exchange_params`` is an
    explicit caller-provided route configuration; this function does not read
    files, inspect credential stores, or infer runtime identity from settings.
    ``order_correlation_issuer`` is an optional exact
    ``DurableOrderCorrelationIssuer`` instance supplied by trusted SDK
    composition; if it is absent, correlated order writes remain unavailable.
    Provider authorities and request resolvers are obtained only from the
    runtime's trusted dependency factory, never from this function's arguments.
    The optional ``market_stream`` is the only metadata injection supported by
    the runtime builder; it must be the selected SDK stream accepted there.

    All feed operations are performed through the existing runtime owner
    mailbox.  A timed-out operation is never repeated.  Any failure is mapped
    to a finite code, and a created BtApi is detached once on the owner thread
    when possible.  The runtime is never stopped or replaced here.
    """

    _validate_timeout(owner_call_timeout_seconds)
    _validate_timeout(instrument_metadata_timeout_seconds)
    if not isinstance(exchange_params, Mapping):
        raise CtpRuntimeApiCompositionError("ctp_exchange_configuration_unavailable")

    try:
        from bt_api_py.bt_api import BtApi
        from bt_api_py.ctp_execution_runtime import CtpExecutionRuntime
    except Exception:
        raise CtpRuntimeApiCompositionError("ctp_runtime_composition_unavailable") from None

    if type(runtime) is not CtpExecutionRuntime:
        raise CtpRuntimeApiCompositionError("ctp_execution_runtime_unavailable")
    try:
        ready = runtime.is_ready()
    except Exception:
        ready = False
    if type(ready) is not bool or ready is not True:
        raise CtpRuntimeApiCompositionError("ctp_execution_runtime_not_ready")

    if order_correlation_issuer is not None:
        try:
            from bt_api_py.order_correlation_issuer import (
                DurableOrderCorrelationIssuer,
            )
        except Exception:
            raise CtpRuntimeApiCompositionError(
                "order_correlation_issuer_unavailable"
            ) from None
        if type(order_correlation_issuer) is not DurableOrderCorrelationIssuer:
            raise CtpRuntimeApiCompositionError("order_correlation_issuer_invalid")

    try:
        route_settings = dict(exchange_params)
    except Exception:
        raise CtpRuntimeApiCompositionError("ctp_exchange_configuration_unavailable") from None

    try:
        api = BtApi(
            exchange_kwargs={CTP_FUTURE_EXCHANGE_NAME: route_settings},
            debug=False,
            execution_config=None,
            order_correlation_issuer=order_correlation_issuer,
            provider_execution_contexts=None,
        )
    except Exception:
        raise CtpRuntimeApiCompositionError("ctp_bt_api_route_initialization_failed") from None

    feed = None
    try:
        feed = api.exchange_feeds.get(CTP_FUTURE_EXCHANGE_NAME)
        if feed is None:
            raise CtpRuntimeApiCompositionError("ctp_bt_api_route_unavailable")

        # Bind before connect.  The borrowed CTP feed's connect only attaches
        # the exact runtime-owned TraderClient; it must not start another one.
        runtime.bind_selected_request_feed(
            feed,
            timeout_seconds=float(owner_call_timeout_seconds),
        )
        runtime.call_on_owner_thread(
            feed.connect,
            timeout_seconds=float(owner_call_timeout_seconds),
        )
        context = runtime.build_selected_feed_order_provider_context(
            feed,
            market_stream=market_stream,
            owner_call_timeout_seconds=float(owner_call_timeout_seconds),
            instrument_metadata_timeout_seconds=float(
                instrument_metadata_timeout_seconds
            ),
        )
        if context is None:
            raise CtpRuntimeApiCompositionError("ctp_order_provider_context_unavailable")

        # BtApi calls the runtime's exact owner-thread context verifier while
        # atomically binding this one route before its typed-order router exists.
        api.bind_provider_execution_context(
            CTP_FUTURE_EXCHANGE_NAME,
            feed,
            context,
            context_verifier=runtime,
            timeout_seconds=float(owner_call_timeout_seconds),
        )
    except CtpRuntimeApiCompositionError as exc:
        cleanup_code = _close_api_once(runtime, api, owner_call_timeout_seconds)
        if cleanup_code is None:
            raise
        raise CtpRuntimeApiCompositionError(
            exc.code,
            cleanup_code=cleanup_code,
        ) from None
    except Exception as exc:
        cleanup_code = _close_api_once(runtime, api, owner_call_timeout_seconds)
        raise CtpRuntimeApiCompositionError(
            _owner_call_failure_code(
                exc,
                fallback="ctp_runtime_route_composition_failed",
            ),
            cleanup_code=cleanup_code,
        ) from None

    return CtpRuntimeBtApiComposition(
        api,
        runtime,
        CTP_FUTURE_EXCHANGE_NAME,
    )


def _close_api_once(runtime: Any, api: Any, timeout_seconds: float) -> Optional[str]:
    """Best-effort one-shot detach; never stop the runtime or retry a call."""

    try:
        runtime.call_on_owner_thread(
            api.close,
            timeout_seconds=float(timeout_seconds),
        )
    except Exception as exc:
        return _owner_call_failure_code(
            exc,
            fallback="ctp_api_close_unconfirmed",
        )
    return None


def _owner_call_failure_code(exc: Exception, *, fallback: str) -> str:
    """Preserve only finite owner-mailbox states, never exception text."""

    try:
        from bt_api_base.managed_execution_runtime_protocol import OwnerThreadCallError
    except Exception:
        return fallback
    if type(exc) is not OwnerThreadCallError:
        return fallback
    code = exc.code
    if code == "OWNER_THREAD_CALL_TIMEOUT_AFTER_START":
        return "ctp_runtime_owner_call_after_start_unknown"
    if code == "OWNER_THREAD_CALL_TIMEOUT_BEFORE_START":
        return "ctp_runtime_owner_call_timeout_before_start"
    if code == "OWNER_THREAD_CALL_QUEUE_FULL":
        return "ctp_runtime_owner_call_queue_full"
    if code in {"OWNER_THREAD_CALL_NOT_READY", "OWNER_THREAD_RUNTIME_CLOSING"}:
        return "ctp_runtime_owner_call_unavailable"
    if code == "OWNER_THREAD_CALL_CLOSED":
        return "ctp_runtime_owner_call_closed"
    return fallback


def _validate_timeout(value: object) -> float:
    if type(value) not in (int, float) or isinstance(value, bool):
        raise CtpRuntimeApiCompositionError(
            "ctp_runtime_composition_timeout_invalid"
        )
    try:
        normalized = float(value)
    except (OverflowError, TypeError, ValueError):
        raise CtpRuntimeApiCompositionError(
            "ctp_runtime_composition_timeout_invalid"
        ) from None
    if not math.isfinite(normalized) or not 0.01 <= normalized <= _MAX_TIMEOUT_SECONDS:
        raise CtpRuntimeApiCompositionError(
            "ctp_runtime_composition_timeout_invalid"
        )
    return normalized
