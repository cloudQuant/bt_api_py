"""Local SDK journal recovery performed before a direct client starts dispatch.

The standard SDK hooks operate on the local journal. A missing hook is
recorded as unsupported; it is not evidence that execution state is clean.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


class StartupRecoveryError(RuntimeError):
    """A local recovery hook failed or returned an unproven result."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class StartupRecoveryResult:
    """Report which SDK hooks completed; absent hooks remain unproven."""

    reservation_cancel: str
    documented_rejection: str


def _run_hook(api: Any, name: str, *, failure_code: str, unproven_code: str) -> str:
    hook = getattr(api, name, None)
    if not callable(hook):
        return "unsupported"
    try:
        result = hook()
    except Exception:
        # Do not expose provider or journal exception text to framework logs.
        raise StartupRecoveryError(failure_code) from None
    if (
        not isinstance(result, Mapping)
        or result.get("completed") is not True
        or not isinstance(result.get("recovered_client_order_ids"), list)
    ):
        raise StartupRecoveryError(unproven_code)
    return "completed"


def recover_local_startup_journal(api: Any) -> StartupRecoveryResult:
    """Run each available local recovery once, in the existing startup order.

    This operation delegates to the SDK client and returns only finite status;
    it does not assert that unresolved orders or the account are clean.
    """

    reservation_cancel = _run_hook(
        api,
        "recover_reservation_only_cancel_unknowns",
        failure_code="reservation_cancel_recovery_failed",
        unproven_code="reservation_cancel_recovery_unproven",
    )
    documented_rejection = _run_hook(
        api,
        "recover_historical_documented_definite_rejections",
        failure_code="documented_rejection_recovery_failed",
        unproven_code="documented_rejection_recovery_unproven",
    )
    return StartupRecoveryResult(reservation_cancel, documented_rejection)


__all__ = ("StartupRecoveryError", "StartupRecoveryResult", "recover_local_startup_journal")
