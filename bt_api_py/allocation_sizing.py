"""Public SDK facade for optional local allocation sizing."""

from __future__ import annotations

from decimal import Decimal
from importlib import import_module

__all__ = ["AllocationSizingUnavailableError", "size_for_strategy_allocation"]


class AllocationSizingUnavailableError(RuntimeError):
    """Raised when the optional canonical risk sizing implementation is absent."""

    code = "allocation_sizing_unavailable"


def size_for_strategy_allocation(
    allocation: object,
    registry: object,
    *,
    instrument: str,
    limit_price: Decimal,
    now_ns: int,
    fx_rate: object | None = None,
) -> Decimal:
    """Delegate advisory sizing to the canonical optional ``bt_api_risk`` implementation.

    This facade deliberately owns no sizing rules. The optional risk package is
    imported only when the caller requests a sizing result.
    """

    try:
        sizing_module = import_module("bt_api_risk.allocation_sizing")
    except ModuleNotFoundError as exc:
        if exc.name not in {"bt_api_risk", "bt_api_risk.allocation_sizing"}:
            raise
        raise AllocationSizingUnavailableError(
            "optional bt_api_risk allocation sizing is unavailable"
        ) from exc

    size = sizing_module.size_for_strategy_allocation
    return size(
        allocation,
        registry,
        instrument=instrument,
        limit_price=limit_price,
        now_ns=now_ns,
        fx_rate=fx_rate,
    )
