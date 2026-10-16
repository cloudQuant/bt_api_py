"""Narrow DIM06 local-rejection guards for unsupported venue TIF combinations.

These tests run the canonical typed ``OrderRequest`` through its real venue
mapper and ``FeedAdapter`` into the production request builder.  Only the final
network method is replaced with a call counter; rejected cases must fail with
the SDK's ``InvalidOrderError`` before that boundary.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from bt_api_base.exceptions import InvalidOrderError
from bt_api_py._contracts.models import OrderRequest, OrderType, Side
from bt_api_py._feed_adapter import FeedAdapter
from bt_api_py._venue_mappers import get_venue_mapper

from bt_api_binance.feeds.normalize import NormalizeMixin
from bt_api_binance.feeds.rest_trade import RestTradeMixin
from bt_api_okx.feeds.live_okx.mixins.trade_mixin import TradeMixin as OkxTradeMixin


class _BinanceParams:
    def __init__(self, asset_type: str) -> None:
        self.asset_type = asset_type

    def get_symbol(self, symbol: Any) -> Any:
        return symbol

    def get_rest_path(self, request_type: str) -> str:
        assert request_type == "make_order"
        return "/api/v3/order" if self.asset_type == "SPOT" else "/fapi/v1/order"


class _BinanceFeed(RestTradeMixin, NormalizeMixin):
    def __init__(self, asset_type: str) -> None:
        self.asset_type = asset_type
        self.exchange_name = f"BINANCE___{asset_type}"
        self._params = _BinanceParams(asset_type)
        self.requests: list[dict[str, Any]] = []

    def request(
        self,
        path: str,
        params: Any = None,
        extra_data: Any = None,
        is_sign: bool = False,
        **_kwargs: Any,
    ) -> dict[str, str]:
        self.requests.append({"path": path, "params": params, "is_sign": is_sign})
        return {"orderId": "synthetic-order"}


class _OkxParams:
    symbol_leverage_dict: dict[str, int] = {}

    def get_symbol(self, symbol: Any) -> Any:
        return symbol

    def get_rest_path(self, request_type: str) -> str:
        assert request_type == "make_order"
        return "/api/v5/trade/order"


class _NullLogger:
    def warning(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class _OkxFeed(OkxTradeMixin):
    def __init__(self) -> None:
        self.exchange_name = "OKX___SWAP"
        self.asset_type = "SWAP"
        self._params = _OkxParams()
        self.request_logger = _NullLogger()
        self.requests: list[dict[str, Any]] = []

    def request(
        self,
        path: str,
        body: Any = None,
        extra_data: Any = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        self.requests.append({"path": path, "body": body})
        return {"code": "0", "data": []}


def _binance_order(**overrides: Any) -> OrderRequest:
    values: dict[str, Any] = {
        "symbol": "BTCUSDT",
        "side": Side.BUY,
        "order_type": OrderType.LIMIT,
        "quantity": Decimal("0.001"),
        "price": Decimal("50000"),
        "account_id": "synthetic",
        "client_order_id": "dim06-tif-1",
        "time_in_force": "GTC",
    }
    values.update(overrides)
    return OrderRequest(**values)


def _okx_order(**overrides: Any) -> OrderRequest:
    values: dict[str, Any] = {
        "symbol": "BTC-USDT-SWAP",
        "side": Side.BUY,
        "order_type": OrderType.LIMIT,
        "quantity": Decimal("1"),
        "price": Decimal("50000"),
        "account_id": "synthetic",
        "client_order_id": "dim06-tif-2",
        "time_in_force": "GTC",
        "quantity_unit": "contracts",
    }
    values.update(overrides)
    return OrderRequest(**values)


@pytest.mark.parametrize(
    ("asset_type", "tif"),
    [
        ("SPOT", "GTC"),
        ("SPOT", "IOC"),
        ("SPOT", "FOK"),
        ("SWAP", "GTC"),
        ("SWAP", "IOC"),
        ("SWAP", "FOK"),
        ("SWAP", "GTX"),
        ("SWAP", "RPI"),
    ],
    ids=[
        "spot-gtc",
        "spot-ioc",
        "spot-fok",
        "swap-gtc",
        "swap-ioc",
        "swap-fok",
        "swap-gtx",
        "swap-rpi",
    ],
)
def test_binance_accepts_documented_limit_tif_for_asset_type(asset_type: str, tif: str) -> None:
    feed = _BinanceFeed(asset_type)
    adapter = FeedAdapter(feed, get_venue_mapper(f"BINANCE___{asset_type}"))

    result = adapter.make_order(_binance_order(time_in_force=tif))

    assert result == {"orderId": "synthetic-order"}
    assert len(feed.requests) == 1
    request = feed.requests[0]
    assert request["path"] == (
        "/api/v3/order" if asset_type == "SPOT" else "/fapi/v1/order"
    )
    assert request["params"]["timeInForce"] == tif


@pytest.mark.parametrize(
    ("asset_type", "tif", "message"),
    [
        ("SPOT", "DAY", "Unsupported Binance SPOT LIMIT time_in_force"),
        ("SPOT", "GFD", "Unsupported Binance SPOT LIMIT time_in_force"),
        ("SPOT", "GTX", "Unsupported Binance SPOT LIMIT time_in_force"),
        ("SPOT", "RPI", "Unsupported Binance SPOT LIMIT time_in_force"),
        ("SWAP", "DAY", "Unsupported Binance SWAP LIMIT time_in_force"),
        ("SWAP", "GFD", "Unsupported Binance SWAP LIMIT time_in_force"),
        ("SWAP", "post_only", "Unsupported Binance SWAP LIMIT time_in_force"),
        ("SWAP", "ZZZ", "Unsupported Binance SWAP LIMIT time_in_force"),
        ("SWAP", "GTD", "GTD requires goodTillDate"),
    ],
    ids=[
        "spot-day",
        "spot-gfd",
        "spot-gtx",
        "spot-rpi",
        "swap-day",
        "swap-gfd",
        "swap-post-only-alias",
        "swap-unknown",
        "swap-gtd-missing-required-field",
    ],
)
def test_binance_rejects_unsupported_tif_before_provider_request(
    asset_type: str, tif: str, message: str
) -> None:
    feed = _BinanceFeed(asset_type)
    adapter = FeedAdapter(feed, get_venue_mapper(f"BINANCE___{asset_type}"))

    with pytest.raises(InvalidOrderError) as exc_info:
        adapter.make_order(_binance_order(time_in_force=tif))

    assert type(exc_info.value) is InvalidOrderError
    assert message in str(exc_info.value)
    assert feed.requests == []


def test_binance_market_order_keeps_tif_irrelevant_to_the_wire_request() -> None:
    feed = _BinanceFeed("SWAP")
    adapter = FeedAdapter(feed, get_venue_mapper("BINANCE___SWAP"))

    result = adapter.make_order(
        _binance_order(
            order_type=OrderType.MARKET,
            price=None,
            time_in_force="DAY",
        )
    )

    assert result == {"orderId": "synthetic-order"}
    assert len(feed.requests) == 1
    params = feed.requests[0]["params"]
    assert params["type"] == "MARKET"
    assert "timeInForce" not in params
    assert "price" not in params


def test_binance_native_swap_feed_accepts_gtd_with_required_good_till_date() -> None:
    feed = _BinanceFeed("SWAP")

    result = feed.make_order(
        "BTCUSDT",
        "0.001",
        "50000",
        "buy-limit",
        time_in_force="GTD",
        goodTillDate=1_900_000_000_000,
    )

    assert result == {"orderId": "synthetic-order"}
    assert len(feed.requests) == 1
    params = feed.requests[0]["params"]
    assert params["timeInForce"] == "GTD"
    assert params["goodTillDate"] == 1_900_000_000_000


def test_okx_rejects_market_post_only_before_provider_request() -> None:
    feed = _OkxFeed()
    adapter = FeedAdapter(feed, get_venue_mapper("OKX___SWAP"))

    with pytest.raises(InvalidOrderError) as exc_info:
        adapter.make_order(
            _okx_order(
                order_type=OrderType.MARKET,
                price=None,
                time_in_force="post_only",
            )
        )

    assert type(exc_info.value) is InvalidOrderError
    assert "post_only requires a limit order" in str(exc_info.value)
    assert feed.requests == []


def test_okx_valid_post_only_limit_and_market_gtc_remain_available() -> None:
    feed = _OkxFeed()
    adapter = FeedAdapter(feed, get_venue_mapper("OKX___SWAP"))

    limit_result = adapter.make_order(_okx_order(time_in_force="post_only"))
    market_result = adapter.make_order(
        _okx_order(order_type=OrderType.MARKET, price=None, time_in_force="GTC")
    )

    assert limit_result == {"code": "0", "data": []}
    assert market_result == {"code": "0", "data": []}
    assert len(feed.requests) == 2
    limit_body = feed.requests[0]["body"]
    assert limit_body["ordType"] == "post_only"
    assert limit_body["px"] == "50000"
    market_body = feed.requests[1]["body"]
    assert market_body["ordType"] == "market"
    assert "px" not in market_body
