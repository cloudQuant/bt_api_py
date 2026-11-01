"""Neutral checks for an explicitly attached operation-limit lease.

These checks do not issue an authorization or prove the origin of a lease.
The caller remains responsible for binding values to its trusted authority.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


class ApprovalLeaseRejected(ValueError):
    """An attached lease failed a definite pre-dispatch check."""

    definite_reject = True

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def validate_approval_lease(
    *, expires_at: Any, operation_count: Any, maximum_count: Any, risk_reducing: Any
) -> None:
    """Validate present lease fields without interpreting missing fields as authority."""

    fields = (expires_at, operation_count, maximum_count, risk_reducing)
    if all(value is None for value in fields):
        return
    if any(value is None for value in fields):
        raise ApprovalLeaseRejected("demo_approval_lease_incomplete")
    if not isinstance(expires_at, str) or not expires_at.endswith("Z"):
        raise ApprovalLeaseRejected("demo_approval_expiry_invalid")
    try:
        parsed_expiry = datetime.fromisoformat(expires_at[:-1] + "+00:00")
    except ValueError:
        raise ApprovalLeaseRejected("demo_approval_expiry_invalid") from None
    if (
        parsed_expiry.utcoffset() != timedelta(0)
        or isinstance(operation_count, bool)
        or not isinstance(operation_count, int)
        or operation_count <= 0
        or isinstance(maximum_count, bool)
        or not isinstance(maximum_count, int)
        or maximum_count <= 0
        or not isinstance(risk_reducing, bool)
    ):
        raise ApprovalLeaseRejected("demo_approval_lease_invalid")
    if risk_reducing:
        return
    if operation_count > maximum_count:
        raise ApprovalLeaseRejected("demo_approval_order_limit")
    if datetime.now(timezone.utc) >= parsed_expiry:
        raise ApprovalLeaseRejected("demo_approval_expired")


__all__ = ("ApprovalLeaseRejected", "validate_approval_lease")
