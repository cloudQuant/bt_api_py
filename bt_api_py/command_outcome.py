"""Provider-neutral classification of SDK command results.

Classification reflects the supplied result only. It does not verify the
provider, create an acknowledgement, or authorize a retry.
"""

from __future__ import annotations

import collections
import threading
from collections.abc import Mapping
from typing import Any


class CommandSafetyState:
    """Own one command stream's UNKNOWN latch and outcome counters.

    This protocol-safety state does not make a provider, account, or business
    risk decision; the caller supplies the opening-admission callback.
    """

    def __init__(self, *, set_accept_openings):
        if not callable(set_accept_openings):
            raise TypeError("set_accept_openings must be callable")
        self._set_accept_openings = set_accept_openings
        self._risk_lock = threading.Lock()
        self._risk_state_unknown = False
        self._risk_incident_epoch = 0
        self._last_risk_incident_reason = ""
        self._health = collections.Counter()

    @property
    def risk_state_unknown(self):
        with self._risk_lock:
            return self._risk_state_unknown

    def latch_unknown(self, reason):
        self._set_accept_openings(False)
        with self._risk_lock:
            self._risk_incident_epoch += 1
            self._risk_state_unknown = True
            self._last_risk_incident_reason = str(reason)
            return self._risk_incident_epoch

    def risk_incident_epoch(self):
        with self._risk_lock:
            return self._risk_incident_epoch

    def health(self):
        with self._risk_lock:
            result = dict(self._health)
            result.update(
                risk_state_latched=self._risk_state_unknown,
                risk_incident_epoch=self._risk_incident_epoch,
                last_risk_incident_reason=self._last_risk_incident_reason,
            )
            if self._risk_state_unknown:
                result["risk_state_unknown"] = 1
            return result

    def increment_health(self, name):
        with self._risk_lock:
            self._health[name] += 1


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


def classify_command_exception(
    operation: str, exc: Exception, *, error_code: Any, remote_write_attempted: bool
) -> tuple[dict[str, Any], tuple[str, ...], str | None]:
    """Project exception facts conservatively without granting retry authority."""
    definite_reject = bool(getattr(exc, "definite_reject", False))
    execution_unknown = (
        bool(getattr(exc, "execution_unknown", False))
        or isinstance(exc, TimeoutError)
        or (operation in {"submit", "cancel", "query"} and not definite_reject)
    )
    state = None
    if operation in {"submit", "cancel"}:
        state = "rejected_before_dispatch" if definite_reject else "unknown"
    fields = {
        "success": False,
        "status": "unknown" if execution_unknown else "failed",
        "execution_unknown": execution_unknown,
        "remote_write_attempted": bool(remote_write_attempted),
        "definite_reject": definite_reject,
        "terminal_confirmed": definite_reject,
        "error_code": error_code,
        "error_msg": (
            "remote execution outcome is unknown" if execution_unknown else "SDK command failed"
        ),
    }
    return fields, (("failed", "unknown") if execution_unknown else ("failed",)), state


__all__ = ("CommandSafetyState", "classify_command_exception", "classify_command_result")
