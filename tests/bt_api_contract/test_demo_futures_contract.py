"""Protect paired futures order semantics at the public BtApi boundary."""

from decimal import Decimal

import pytest

from bt_api_py import BtApi
from bt_api_py._contracts.errors import CapabilityNotSupportedError
from bt_api_py._contracts.models import (
    CancelOrderRequest,
    ForwardingConfig,
    OrderRequest,
    OrderType,
    QueryOrderRequest,
    Side,
)
from bt_api_py.forwarding.btapi_backend import ZmqBtApiBackend


def order(**changes):
    values = {
        "symbol": "BTC-USDT-SWAP",
        "side": Side.SELL,
        "order_type": OrderType.LIMIT,
        "quantity": Decimal("0.1"),
        "price": Decimal("60000"),
        "account_id": "demo",
        "client_order_id": "arbclose001",
        "time_in_force": "IOC",
        "reduce_only": True,
    }
    values.update(changes)
    return OrderRequest(**values)


class Feed:
    def make_order(self, *args, **kwargs):
        return args, kwargs

    def query_order(self, *args, **kwargs):
        return args, kwargs

    cancel_order = query_order


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr("bt_api_py.bt_api._ensure_plugins_loaded", lambda: None)
    client = BtApi(debug=False)
    client.exchange_feeds.update({"OKX___SWAP": Feed(), "BINANCE___SWAP": Feed()})
    return client


@pytest.mark.parametrize("exchange", ["OKX___SWAP", "BINANCE___SWAP"])
def test_ioc_close_reaches_feed(api, exchange):
    args, kwargs = api.make_order(exchange, order())
    assert args[3] == "sell-limit"
    assert kwargs["time_in_force"] == "IOC"
    assert kwargs["reduce_only"] is True
    assert kwargs["offset"] == "close"


@pytest.mark.parametrize("quantity_unit", ["contracts", "native"])
def test_okx_explicit_contract_size_does_not_get_converted_twice(api, quantity_unit):
    args, kwargs = api.make_order("OKX___SWAP", order(quantity_unit=quantity_unit))
    assert args[1] == "0.1"
    assert kwargs["size_in_contracts"] is True
    _, default = api.make_order("OKX___SWAP", order())
    assert default["size_in_contracts"] is False


@pytest.mark.parametrize("exchange", ["OKX___SWAP", "BINANCE___SWAP"])
@pytest.mark.parametrize(
    "operation,request_type",
    [
        ("query_order", QueryOrderRequest),
        ("cancel_order", CancelOrderRequest),
    ],
)
def test_unknown_ack_reconciles_by_client_id(api, exchange, operation, request_type):
    args, kwargs = getattr(api, operation)(
        exchange, request_type(symbol="BTCUSDT", account_id="demo", client_order_id="arb001")
    )
    assert args == ("BTCUSDT", None)
    assert kwargs["client_order_id"] == "arb001"


def test_exchange_order_id_takes_precedence(api):
    args, kwargs = api.query_order(
        "OKX___SWAP",
        QueryOrderRequest(
            symbol="BTC-USDT-SWAP", account_id="demo", order_id="123", client_order_id="arb001"
        ),
    )
    assert args[1] == "123"
    assert "client_order_id" not in kwargs


def test_unsupported_quantity_units_fail_before_dispatch(api):
    with pytest.raises(CapabilityNotSupportedError):
        api.make_order("BINANCE___SWAP", order(quantity_unit="contracts"))
    backend = ZmqBtApiBackend(ForwardingConfig("unused", "unused", "unused", "demo", "arb"))
    with pytest.raises(CapabilityNotSupportedError):
        backend.make_order("OKX___SWAP", order(quantity_unit="contracts"))


@pytest.mark.parametrize(
    "field,value",
    [
        ("quantity", Decimal("NaN")),
        ("quantity", Decimal("Infinity")),
        ("price", Decimal("NaN")),
        ("price", Decimal("Infinity")),
        ("price", Decimal("0")),
        ("quantity_unit", "unknown"),
    ],
)
def test_invalid_numeric_contract_is_rejected(field, value):
    with pytest.raises(ValueError):
        order(**{field: value})
