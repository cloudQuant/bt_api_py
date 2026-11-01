"""Provider-neutral classification of SDK command results.

Classification reflects the supplied result only. It does not verify the
provider, create an acknowledgement, or authorize a retry.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def classify_command_result(
    operation: str, result: Any
) -> tuple[dict[str, Any], tuple[str, ...], str | None]:
    """Return completion fields, health increments and an optional error code."""
    if isinstance(result, Mapping) and result.get("execution_unknown") is True:
        code = str(result.get("error_code") or "remote_execution_unknown")
        return (
            {
                "success": False,
                "status": "unknown",
                "response": result,
                "execution_unknown": True,
                "remote_write_attempted": operation in {"submit", "cancel"},
                "definite_reject": False,
                "terminal_confirmed": False,
                "error_code": code,
                "error_msg": "remote execution outcome is unknown",
            },
            ("failed", "unknown"),
            code,
        )
    if operation in {"reconcile", "reconciliation"}:
        if (
            isinstance(result, Mapping)
            and result.get("complete") is True
            and result.get("safe_to_resume") is True
        ):
            return (
                {"success": True, "status": "completed", "response": result},
                ("completed",),
                None,
            )
        code = "reconciliation_evidence_incomplete"
        return (
            {
                "success": False,
                "status": "incomplete",
                "response": result if isinstance(result, Mapping) else None,
                "execution_unknown": False,
                "error_code": code,
                "error_msg": "SDK reconciliation did not provide a verified complete snapshot",
            },
            ("failed",),
            code,
        )
    if operation == "order_reconciliation":
        if (
            isinstance(result, Mapping)
            and result.get("kind") == "order_reconciliation"
            and result.get("complete") is True
            and result.get("evidence_complete") is True
            and result.get("safe_to_resume") is False
        ):
            return (
                {"success": True, "status": "completed", "response": result},
                ("completed",),
                None,
            )
        code = "order_reconciliation_evidence_incomplete"
        return (
            {
                "success": False,
                "status": "incomplete",
                "response": result if isinstance(result, Mapping) else None,
                "execution_unknown": False,
                "error_code": code,
                "error_msg": "SDK order reconciliation did not provide a verified order snapshot",
            },
            ("failed",),
            code,
        )
    if (
        operation in {"submit", "cancel"}
        and isinstance(result, Mapping)
        and result.get("kind") == "verified_order_ack"
        and result.get("verified_order_ack") is True
        and result.get("status") == "rejected"
        and result.get("definite_reject") is True
        and result.get("terminal_confirmed") is True
    ):
        code = str(result.get("error_code") or "sdk_order_rejected")
        return (
            {
                "success": False,
                "status": "rejected",
                "response": result,
                "execution_unknown": False,
                "remote_write_attempted": True,
                "definite_reject": True,
                "terminal_confirmed": True,
                "error_code": code,
                "error_msg": "SDK verified a definite order rejection",
            },
            ("failed",),
            code,
        )
    return {"success": True, "status": "completed", "response": result}, ("completed",), None


__all__ = ("classify_command_result",)
