"""Run one claim-bound SDK submit/cancel handoff.

This module owns the common call sequence between a selected SDK
correlated-execution facade and its verifier. The caller retains route,
authorization, local bindings, queue, and registry ownership. In particular,
``before_dispatch`` performs the caller's second currentness check and sets
its dispatch-entered marker only after that check succeeds. The SDK facade and
acknowledgement type are supplied by the caller.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from decimal import Decimal
from typing import Any, Type


def correlated_command_types() -> dict[str, Any]:
    """Load public SDK command contracts without selecting a provider."""
    from bt_api_py import CancelOrderRequest, OrderRequest, OrderType, Side
    from bt_api_py.facade_protocol import (
        OrderCorrelation,
        VerifiedOrderAcknowledgement,
        is_correlated_order_execution_facade,
        is_correlated_order_execution_selector,
    )

    return {
        "CancelOrderRequest": CancelOrderRequest,
        "OrderRequest": OrderRequest,
        "OrderType": OrderType,
        "Side": Side,
        "OrderCorrelation": OrderCorrelation,
        "VerifiedOrderAcknowledgement": VerifiedOrderAcknowledgement,
        "is_correlated_order_execution_facade": is_correlated_order_execution_facade,
        "is_correlated_order_execution_selector": is_correlated_order_execution_selector,
    }


def build_order_request(
    request_type: Type[Any],
    order_type_type: Type[Any],
    side_type: Type[Any],
    payload: Mapping[str, Any],
    *,
    account_id: str,
    client_order_id: str,
    default_quantity_unit: str | None = None,
    instrument: Any = None,
    include_instrument: bool = False,
    strategy_identity_sha256: str = "",
) -> Any:
    """Construct one typed neutral SDK request from resolved order fields."""
    optional_fields = {
        key: payload[key]
        for key in (
            "position_side",
            "position_id",
            "offset",
            "position_mode",
            "execution_cycle_id",
            "execution_role",
            "hedge_class",
        )
        if payload.get(key) is not None
    }
    if type(include_instrument) is not bool:
        raise TypeError("include_instrument must be an exact boolean")
    selected_instrument = {"instrument": instrument} if include_instrument else {}
    strategy_identity = (
        {"strategy_identity_sha256": strategy_identity_sha256}
        if strategy_identity_sha256
        else {}
    )
    return request_type(
        symbol=payload["symbol"],
        account_id=account_id,
        client_order_id=client_order_id,
        side=side_type(payload["side"]),
        order_type=order_type_type(payload["order_type"]),
        quantity=Decimal(str(payload["size"])),
        price=(
            Decimal(str(payload["price"]))
            if payload.get("price") is not None
            else None
        ),
        quantity_unit=(
            payload.get("quantity_unit") or default_quantity_unit or "native"
        ),
        time_in_force=str(payload.get("time_in_force", "GTC")).upper(),
        reduce_only=bool(payload.get("reduce_only", False)),
        **optional_fields,
        **selected_instrument,
        **strategy_identity,
    )


def build_cancel_order_request(
    request_type: Type[Any],
    *,
    symbol: str,
    account_id: str,
    client_order_id: str,
) -> Any:
    """Construct one typed neutral cancel request from resolved identifiers."""
    return request_type(
        symbol=symbol,
        account_id=account_id,
        client_order_id=client_order_id,
    )


def require_correlated_selector(api: Any, *, error_type: Type[Exception]) -> None:
    """Require the public SDK selector for route-bound order execution."""
    from .facade_protocol import is_correlated_order_execution_selector

    if not is_correlated_order_execution_selector(api):
        raise error_type("SDK facade does not select route-bound correlated order execution")


def select_correlated_facade(api: Any, exchange_name: str, *, error_type: Type[Exception]) -> Any:
    """Select and validate the SDK facade for one route."""
    from .facade_protocol import is_correlated_order_execution_facade

    require_correlated_selector(api, error_type=error_type)
    selector = getattr(api, "correlated_order_execution", None)
    if not callable(selector):
        raise error_type("SDK correlated order route selector is unavailable")
    facade = selector(exchange_name)
    if not is_correlated_order_execution_facade(facade):
        raise error_type("SDK route selector returned an invalid correlated facade")
    for name in (
        "prepare_order_correlation",
        "claim_correlated_order",
        "submit_correlated_order",
        "cancel_correlated_order",
        "verify_order_response",
        "verify_cancel_response",
    ):
        if not callable(getattr(facade, name, None)):
            raise error_type(f"SDK correlated route facade lacks {name}")
    return facade


def issue_order_correlation(
    facade: Any, exchange_name: str, symbol: str, *, error_type: Type[Exception]
) -> Any:
    """Issue and check a route-bound SDK correlation without owning caller state."""
    from .facade_protocol import OrderCorrelation

    correlation = facade.prepare_order_correlation(exchange_name, symbol)
    if inspect.isawaitable(correlation):
        close = getattr(correlation, "close", None)
        if callable(close):
            close()
        raise error_type("SDK correlation preparation must be synchronous")
    if type(correlation) is not OrderCorrelation:
        raise error_type("SDK did not return an exact OrderCorrelation")
    if (
        correlation.exchange_name != exchange_name
        or correlation.symbol != symbol
        or type(correlation.client_order_id) is not str
        or not correlation.client_order_id
    ):
        raise error_type("SDK correlation does not match the requested route and symbol")
    return correlation


def _new_error(
    error_type: Type[BaseException],
    message: str,
    *,
    code: str = "",
    definite_reject: bool = False,
    execution_unknown: bool = False,
) -> BaseException:
    """Build the caller's error class with the original finite outcome flags."""

    error = error_type(message)
    if code:
        error.code = code
    if definite_reject:
        error.definite_reject = True
    if execution_unknown:
        error.execution_unknown = True
    return error


def require_correlated_request_binding(
    command: Mapping[str, Any],
    request: Any,
    authorization: Any,
    *,
    correlation_type: type,
    error_type: Type[BaseException],
) -> Any:
    """Check neutral request/correlation shape without granting authorization."""
    correlation = command.get("correlation")
    if type(correlation) is not correlation_type:
        raise _new_error(
            error_type,
            "correlated SDK write lacks an exact OrderCorrelation",
            code="sdk_correlation_missing",
            definite_reject=True,
        )
    if authorization is None:
        raise _new_error(
            error_type,
            "explicit SDK execution authorization is unavailable",
            code="sdk_execution_authorization_unavailable",
            definite_reject=True,
        )
    if (
        correlation.exchange_name != command.get("venue")
        or correlation.symbol != command.get("symbol")
        or getattr(request, "client_order_id", None) != correlation.client_order_id
        or getattr(request, "symbol", None) != correlation.symbol
    ):
        raise _new_error(
            error_type,
            "SDK correlated request differs from its reservation",
            code="sdk_correlated_request_mismatch",
            definite_reject=True,
        )
    return correlation


async def dispatch_correlated_command(
    operation: str,
    execution_facade: Any,
    correlation: Any,
    request: Any,
    authorization: Any,
    *,
    acknowledgement_type: type,
    error_type: Type[BaseException],
    before_dispatch: Callable[[], Any],
    sanitize_exception: Callable[[BaseException], Any],
) -> Any:
    """Claim, dispatch once, and return the exact SDK-verified acknowledgement.

    The caller must perform its initial route/request/correlation checks before
    calling this helper. ``before_dispatch`` runs after a synchronous claim and
    after the dispatch method is found, immediately before dispatch. It must
    recheck the caller-owned route, authorization identity, and binding indexes;
    it must set any dispatch-entered marker only after those checks pass. Any
    exception from that callback propagates unchanged.

    Claim failures are sanitized by the caller and conservatively reported as
    UNKNOWN when the caller lacks a request-bound no-side-effect proof.
    Dispatch exceptions and cancellation propagate unchanged so the existing
    command coordinator preserves its UNKNOWN/remote-attempt semantics.
    """

    if operation == "submit":
        async_name = "submit_correlated_order"
        verifier_name = "verify_order_response"
    elif operation == "cancel":
        async_name = "cancel_correlated_order"
        verifier_name = "verify_cancel_response"
    else:
        raise error_type("unsupported SDK command operation")

    claim_method = getattr(execution_facade, "claim_correlated_order", None)
    if not callable(claim_method):
        raise _new_error(
            error_type,
            "SDK correlated claim capability is unavailable",
            code="sdk_correlated_claim_unavailable",
            definite_reject=True,
        )

    try:
        claim = claim_method(
            operation,
            correlation,
            request,
            authorization=authorization,
        )
        if inspect.isawaitable(claim):
            close = getattr(claim, "close", None)
            if callable(close):
                close()
            raise error_type("SDK correlated claim must be synchronous")
        if claim is None:
            raise error_type("SDK correlated claim was not issued")
    except Exception as exc:
        sanitize_exception(exc)
        raise _new_error(
            error_type,
            "SDK correlated claim outcome is unverified",
            code="sdk_correlated_claim_unverified",
            execution_unknown=True,
        ) from None

    async_method = getattr(execution_facade, async_name, None)
    if not callable(async_method):
        raise error_type("SDK facade does not expose {}".format(async_name))

    before_dispatch()
    result = async_method(
        correlation,
        request,
        claim=claim,
        authorization=authorization,
    )
    if not inspect.isawaitable(result):
        close = getattr(result, "close", None)
        if callable(close):
            close()
        raise error_type("SDK {} did not return an awaitable".format(async_name))

    result = await result
    if not isinstance(result, Mapping):
        raise error_type("SDK {} did not return a normalized mapping".format(async_name))

    verifier = getattr(execution_facade, verifier_name, None)
    if not callable(verifier):
        raise _new_error(
            error_type,
            "SDK correlated response verifier is unavailable",
            code="sdk_order_response_unverified",
            execution_unknown=True,
        )

    acknowledgement = verifier(correlation, result)
    if inspect.isawaitable(acknowledgement):
        close = getattr(acknowledgement, "close", None)
        if callable(close):
            close()
        raise _new_error(
            error_type,
            "SDK correlated response verifier must be synchronous",
            code="sdk_order_response_unverified",
            execution_unknown=True,
        )

    if (
        type(acknowledgement) is not acknowledgement_type
        or acknowledgement.correlation is not correlation
        or acknowledgement.operation != operation
        or acknowledgement.disposition not in {"accepted", "rejected"}
    ):
        raise _new_error(
            error_type,
            "SDK correlated response could not be verified",
            code="sdk_order_response_unverified",
            execution_unknown=True,
        )

    return acknowledgement


__all__ = (
    "build_cancel_order_request",
    "build_order_request",
    "correlated_command_types",
    "dispatch_correlated_command",
    "issue_order_correlation",
    "require_correlated_selector",
    "require_correlated_request_binding",
    "select_correlated_facade",
)
