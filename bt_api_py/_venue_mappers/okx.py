"""OKX venue order mapping (Task 2.2)."""

from __future__ import annotations

from typing import Any

from bt_api_py._contracts.models import OrderRequest
from bt_api_py._normalization import _trading_permissions, number, pick, rows


def map_order_request(request: OrderRequest) -> dict[str, Any]:
    """Delegate native order projection to the OKX provider."""
    from bt_api_okx.order_request_mapping import map_order_request as provider_map

    return provider_map(request)


def normalize_order_readiness(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Delegate readiness projection to the OKX provider."""
    from bt_api_okx.order_request_mapping import build_order_readiness_normalizer

    project = build_order_readiness_normalizer(
        rows=rows,
        pick=pick,
        _trading_permissions=_trading_permissions,
        number=number,
    )
    return project(*args, **kwargs)
