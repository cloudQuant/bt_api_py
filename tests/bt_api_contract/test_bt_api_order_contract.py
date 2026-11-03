"""BtApi typed order contract tests (Task 2.3)."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest
from bt_api_base.order_quantity_units import validate_generic_order_quantity_units
from bt_api_base.order_request_projection import legacy_order_request_projection

from bt_api_py import BtApi
from bt_api_py._contracts.errors import NormalizedApiError
from bt_api_py._contracts.models import OrderRequest, OrderType, Side
from bt_api_py.provider_entrypoints import ProviderEntryPointError


@pytest.fixture(autouse=True)
def _mock_route_declarations(monkeypatch):
    """Declare the synthetic route explicitly, as a provider would."""
    from bt_api_py import _venue_mappers, order_quantity_units, venue_normalization_policy

    def resolve(group, exchange_name):
        assert exchange_name == "MOCK___SPOT"
        return {
            "bt_api.normalization_policies": lambda _route: SimpleNamespace(
                position_mode_placement_guard_required=False
            ),
            "bt_api.order_quantity_unit_validators": validate_generic_order_quantity_units,
            "bt_api.order_request_projections": legacy_order_request_projection,
        }[group]

    for module in (_venue_mappers, order_quantity_units, venue_normalization_policy):
        monkeypatch.setattr(module, "load_route_entrypoint", resolve)


def _api_with_spy_feed() -> tuple[BtApi, list[tuple]]:
    api = BtApi(None, debug=False)
    calls: list[tuple] = []

    class _SpyFeed:
        def make_order(self, *args, **kwargs):
            calls.append((args, kwargs))
            return "order-1"

    api.exchange_feeds["MOCK___SPOT"] = _SpyFeed()
    api.data_queues["MOCK___SPOT"] = object()
    return api, calls


def test_make_order_accepts_typed_request() -> None:
    api, calls = _api_with_spy_feed()
    request = OrderRequest(
        symbol="BTCUSDT",
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("0.001"),
        price=Decimal("50000"),
        account_id="paper",
        client_order_id="cid-1",
    )
    result = api.make_order("MOCK___SPOT", request)
    assert result == "order-1"
    assert len(calls) == 1
    args, _ = calls[0]
    assert args[3] == "buy-limit"


def test_typed_sell_market_maps_side() -> None:
    api, calls = _api_with_spy_feed()
    request = OrderRequest(
        symbol="BTCUSDT",
        side=Side.SELL,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
        price=None,
        account_id="paper",
        client_order_id="cid-2",
    )
    api.make_order("MOCK___SPOT", request)
    args, _ = calls[0]
    assert args[3] == "sell-market"


def test_undeclared_route_rejects_before_feed_call(monkeypatch) -> None:
    from bt_api_py import venue_normalization_policy

    def unavailable(group, exchange_name):
        raise ProviderEntryPointError("provider_entry_point_unavailable")

    monkeypatch.setattr(venue_normalization_policy, "load_route_entrypoint", unavailable)
    api, calls = _api_with_spy_feed()
    request = OrderRequest(
        symbol="BTCUSDT",
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("1"),
        price=Decimal("1"),
        account_id="paper",
        client_order_id="cid-1",
    )

    with pytest.raises(NormalizedApiError) as excinfo:
        api.make_order("MOCK___SPOT", request)
    assert excinfo.value.code == "position_mode_guard_policy_unavailable"
    assert calls == []
