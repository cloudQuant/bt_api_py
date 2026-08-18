"""Tests for the v1 BtApi typed request/result contract (Task 1.1)."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from bt_api_py._contracts.models import (
    CancelAllRequest,
    CancelOrderRequest,
    Consistency,
    ForwardingConfig,
    Freshness,
    OrderRequest,
    OrderType,
    QueryOrderRequest,
    Side,
    SubscribeRequest,
    TransportMode,
)


def _order_request(**overrides: object) -> OrderRequest:
    kwargs: dict[str, object] = {
        "symbol": "BTCUSDT",
        "side": Side.BUY,
        "order_type": OrderType.LIMIT,
        "quantity": Decimal("0.001"),
        "price": Decimal("50000"),
        "account_id": "paper",
        "client_order_id": "cid-1",
    }
    kwargs.update(overrides)
    return OrderRequest(**kwargs)  # type: ignore[arg-type]


def test_side_values() -> None:
    assert Side.BUY.value == "buy"
    assert Side.SELL.value == "sell"


def test_order_type_values() -> None:
    assert OrderType.MARKET.value == "market"
    assert OrderType.LIMIT.value == "limit"


def test_consistency_values() -> None:
    assert Consistency.LIVE.value == "live"
    assert Consistency.CACHE_OK.value == "cache_ok"


def test_transport_mode_values() -> None:
    assert TransportMode.DIRECT.value == "direct"
    assert TransportMode.ZMQ.value == "zmq"


def test_order_request_is_frozen() -> None:
    req = _order_request()
    with pytest.raises(dataclasses.FrozenInstanceError):
        req.symbol = "ETHUSDT"  # type: ignore[misc]


def test_order_request_quantity_is_decimal() -> None:
    req = _order_request()
    assert isinstance(req.quantity, Decimal)


def test_freshness_defaults() -> None:
    freshness = Freshness(source="live", observed_at=datetime.now(UTC))
    assert freshness.stale is False
    assert freshness.stale_reason is None


def test_forwarding_config_is_frozen() -> None:
    config = ForwardingConfig(
        command_endpoint="tcp://127.0.0.1:7002",
        market_endpoint="tcp://127.0.0.1:7001",
        private_endpoint="tcp://127.0.0.1:7003",
        account_id="paper",
        strategy_id="s1",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.account_id = "other"  # type: ignore[misc]


def test_subscribe_request_fields() -> None:
    req = SubscribeRequest(
        exchange_name="BINANCE___SPOT",
        symbols=["BTCUSDT"],
        topics=["ticker", "depth"],
    )
    assert req.exchange_name == "BINANCE___SPOT"
    assert req.symbols == ["BTCUSDT"]
    assert req.account_id is None


def test_cancel_order_request_requires_one_locator() -> None:
    with pytest.raises(ValueError):
        CancelOrderRequest(symbol="BTCUSDT", account_id="paper")


def test_cancel_order_request_accepts_order_id() -> None:
    req = CancelOrderRequest(symbol="BTCUSDT", account_id="paper", order_id="o1")
    assert req.order_id == "o1"


def test_query_order_request_fields() -> None:
    req = QueryOrderRequest(symbol="BTCUSDT", account_id="paper", client_order_id="c1")
    assert req.client_order_id == "c1"


def test_cancel_all_request_symbol_is_optional() -> None:
    req = CancelAllRequest(account_id="paper")
    assert req.symbol is None
