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
from typing import Any, Type


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


__all__ = ("dispatch_correlated_command",)
