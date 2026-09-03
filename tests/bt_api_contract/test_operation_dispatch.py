"""Public ``BtApi`` synchronous operation dispatch contract."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from bt_api_py._contracts.errors import CapabilityNotSupportedError
from bt_api_py._contracts.models import (
    CancelAllRequest,
    CancelOrderRequest,
    Consistency,
    ForwardingConfig,
    OrderRequest,
    OrderType,
    QueryOrderRequest,
    Side,
    TransportMode,
)
from bt_api_py.bt_api import BtApi


class RecordingBackend:
    """Small backend double that proves public methods cross one boundary."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def _record(self, operation: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls.append((operation, args, kwargs))
        return {"operation": operation}

    def get_tick(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._record("get_tick", *args, **kwargs)

    def get_depth(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._record("get_depth", *args, **kwargs)

    def get_kline(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._record("get_kline", *args, **kwargs)

    def get_account(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._record("get_account", *args, **kwargs)

    def get_balance(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._record("get_balance", *args, **kwargs)

    def get_position(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._record("get_position", *args, **kwargs)

    def get_open_orders(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._record("get_open_orders", *args, **kwargs)

    def get_deals(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._record("get_deals", *args, **kwargs)

    def make_order(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._record("make_order", *args, **kwargs)

    def cancel_order(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._record("cancel_order", *args, **kwargs)

    def cancel_all(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._record("cancel_all", *args, **kwargs)

    def query_order(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._record("query_order", *args, **kwargs)


class ExplodingFeed:
    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"ZMQ operation unexpectedly accessed a local feed: {name}")


def _zmq_api() -> BtApi:
    return BtApi(
        debug=False,
        transport_mode=TransportMode.ZMQ,
        forwarding_config=ForwardingConfig(
            command_endpoint="inproc://commands",
            market_endpoint="inproc://market",
            private_endpoint="inproc://private",
            account_id="acct-1",
            strategy_id="strategy-1",
        ),
    )


@pytest.mark.parametrize(
    ("operation", "invoke", "request_type"),
    [
        ("get_tick", lambda api: api.get_tick("SIM___SPOT", "BTC-USDT"), None),
        ("get_depth", lambda api: api.get_depth("SIM___SPOT", "BTC-USDT", 5), None),
        (
            "get_kline",
            lambda api: api.get_kline("SIM___SPOT", "BTC-USDT", "1m", 5),
            None,
        ),
        ("get_account", lambda api: api.get_account("SIM___SPOT"), None),
        ("get_balance", lambda api: api.get_balance("SIM___SPOT"), None),
        ("get_position", lambda api: api.get_position("SIM___SPOT"), None),
        ("get_open_orders", lambda api: api.get_open_orders("SIM___SPOT"), None),
        ("get_deals", lambda api: api.get_deals("SIM___SPOT"), None),
        (
            "make_order",
            lambda api: api.make_order(
                "SIM___SPOT",
                OrderRequest(
                    symbol="BTC-USDT",
                    side=Side.BUY,
                    order_type=OrderType.LIMIT,
                    quantity=Decimal("1"),
                    price=Decimal("100"),
                    account_id="acct-1",
                    client_order_id="client-1",
                ),
            ),
            OrderRequest,
        ),
        (
            "cancel_order",
            lambda api: api.cancel_order(
                "SIM___SPOT",
                CancelOrderRequest(symbol="BTC-USDT", account_id="acct-1", order_id="order-1"),
            ),
            CancelOrderRequest,
        ),
        (
            "cancel_all",
            lambda api: api.cancel_all(
                "SIM___SPOT", CancelAllRequest(account_id="acct-1", symbol="BTC-USDT")
            ),
            CancelAllRequest,
        ),
        (
            "query_order",
            lambda api: api.query_order(
                "SIM___SPOT",
                QueryOrderRequest(symbol="BTC-USDT", account_id="acct-1", order_id="order-1"),
            ),
            QueryOrderRequest,
        ),
    ],
)
def test_zmq_v1_operations_route_only_to_backend(
    operation: str, invoke: Any, request_type: type[Any] | None
) -> None:
    api = _zmq_api()
    backend = RecordingBackend()
    api._backend = backend
    api.exchange_feeds["SIM___SPOT"] = ExplodingFeed()

    assert invoke(api) == {"operation": operation}
    assert backend.calls[0][0] == operation
    assert backend.calls[0][1][0] == "SIM___SPOT"
    if request_type is not None:
        assert isinstance(backend.calls[0][1][1], request_type)
    else:
        assert backend.calls[0][2].get("consistency", Consistency.LIVE) is Consistency.LIVE


def test_direct_operations_route_to_backend_without_losing_legacy_options() -> None:
    api = BtApi(debug=False)
    backend = RecordingBackend()
    api._backend = backend

    result = api.get_tick("SIM___SPOT", "BTC-USDT", extra_data={"source": "legacy"}, limit=1)

    assert result == {"operation": "get_tick"}
    operation, args, kwargs = backend.calls[0]
    assert operation == "get_tick"
    assert args == ("SIM___SPOT", "BTC-USDT")
    assert kwargs["extra_data"] == {"source": "legacy"}
    assert kwargs["limit"] == 1


def test_zmq_legacy_cancel_is_converted_to_a_typed_request() -> None:
    api = _zmq_api()
    backend = RecordingBackend()
    api._backend = backend

    assert api.cancel_order("SIM___SPOT", "BTC-USDT", "order-1") == {"operation": "cancel_order"}
    request = backend.calls[0][1][1]
    assert isinstance(request, CancelOrderRequest)
    assert request.symbol == "BTC-USDT"
    assert request.order_id == "order-1"
    assert request.account_id == "legacy"


def test_zmq_get_trades_is_an_explicit_capability_failure() -> None:
    api = _zmq_api()
    api.exchange_feeds["SIM___SPOT"] = ExplodingFeed()

    with pytest.raises(CapabilityNotSupportedError, match=r"get_trades.*transport=zmq"):
        api.get_trades("SIM___SPOT")
