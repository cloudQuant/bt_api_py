"""Select provider-declared native order-side extraction by exact route."""

from __future__ import annotations

from bt_api_py._contracts.errors import NormalizedApiError
from bt_api_py.provider_entrypoints import ProviderEntryPointError, load_route_entrypoint

ENTRY_POINT_GROUP = "bt_api.order_side_projections"


def order_side_candidates(exchange_name, row, native, *, position=False):
    """Select explicit provider field candidates without asserting their truth."""
    try:
        projector = load_route_entrypoint(ENTRY_POINT_GROUP, exchange_name)
    except ProviderEntryPointError:
        raise NormalizedApiError(
            "order_side_projection",
            "PROVIDER_ORDER_SIDE_PROJECTION_UNAVAILABLE",
            execution_unknown=True,
        ) from None
    return projector(row, native, position=position)


__all__ = ("order_side_candidates",)
