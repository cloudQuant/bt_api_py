"""Local SDK journal recovery performed before a direct client starts dispatch.

The standard SDK hooks operate on the local journal. A missing hook is
recorded as unsupported; it is not evidence that execution state is clean.
"""

from __future__ import annotations

from collections.abc import Mapping
import math
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


def normalise_startup_account_state(value, *, on_unreadable=None):
    """Return a safe, non-final-state observation summary for shutdown.

    This accepts only provider-neutral aggregate counts.  It deliberately
    does not turn the supplied startup snapshot into a final remote query:
    absent evidence remains absent, while unknown or malformed supplied
    evidence is retained as a conservative non-flat shutdown condition.
    """
    keys = (
        "nonzero_position_record_count",
        "gross_position_lots",
        "active_orders_count",
    )
    evidence = {
        "provided": value is not None,
        "validation_status": "not_provided",
        "is_final_state": False,
        "proves_nonflat": False,
        "requires_nonflat": False,
        "nonzero_position_record_count": None,
        "gross_position_lots": None,
        "active_orders_count": None,
        "validation_errors": [],
    }
    if value is None:
        return evidence
    if not isinstance(value, Mapping):
        evidence.update(
            validation_status="malformed",
            requires_nonflat=True,
            validation_errors=["startup_account_state_not_mapping"],
        )
        return evidence

    raw_values = {}
    missing = []
    unknown = []
    malformed = []
    for key in keys:
        try:
            raw_value = value[key]
        except KeyError:
            missing.append(key)
            continue
        except Exception:
            if on_unreadable is not None:
                on_unreadable()
            malformed.append(f"unreadable_{key}")
            continue
        if raw_value is None:
            unknown.append(key)
            continue
        raw_values[key] = raw_value

    if missing:
        malformed.extend(f"missing_{key}" for key in missing)
    if not missing and not unknown and not malformed:
        for key in ("nonzero_position_record_count", "active_orders_count"):
            number = raw_values[key]
            if isinstance(number, bool) or not isinstance(number, int) or number < 0:
                malformed.append(f"invalid_{key}")
                continue
            evidence[key] = number

        gross_lots = raw_values["gross_position_lots"]
        if (
            isinstance(gross_lots, bool)
            or not isinstance(gross_lots, (int, float))
            or not math.isfinite(float(gross_lots))
            or float(gross_lots) < 0.0
        ):
            malformed.append("invalid_gross_position_lots")
        else:
            evidence["gross_position_lots"] = float(gross_lots)

    if malformed:
        evidence.update(
            validation_status="malformed",
            requires_nonflat=True,
            validation_errors=sorted(set(malformed)),
            nonzero_position_record_count=None,
            gross_position_lots=None,
            active_orders_count=None,
        )
        return evidence
    if unknown:
        evidence.update(
            validation_status="unknown",
            requires_nonflat=True,
            validation_errors=sorted(f"unknown_{key}" for key in unknown),
            nonzero_position_record_count=None,
            gross_position_lots=None,
            active_orders_count=None,
        )
        return evidence

    proves_nonflat = bool(
        evidence["nonzero_position_record_count"]
        or evidence["gross_position_lots"]
        or evidence["active_orders_count"]
    )
    evidence.update(
        validation_status="valid",
        proves_nonflat=proves_nonflat,
        requires_nonflat=proves_nonflat,
    )
    return evidence


__all__ = ("StartupRecoveryError", "StartupRecoveryResult", "recover_local_startup_journal", "normalise_startup_account_state")
