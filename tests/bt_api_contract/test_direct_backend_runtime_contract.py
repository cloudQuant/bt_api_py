"""Direct transport regression coverage for the unified BtApi boundary."""

from __future__ import annotations

import queue
from decimal import Decimal
from typing import Any, cast

import pytest

from bt_api_py._contracts.errors import CapabilityNotSupportedError
from bt_api_py._contracts.models import OrderRequest, OrderType, Side
from bt_api_py.bt_api import BtApi
from bt_api_py.exceptions import InvalidOrderError


class _Capabilities:
    def as_dict(self) -> dict[str, bool]:
        return {"get_tick": True, "make_order": True}


class _DirectFeed:
    """Feed double that exposes every operation handled by DirectBackend."""

    capabilities = _Capabilities()

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.disconnect_calls = 0

    def _record(self, operation: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        if self.fail:
            raise RuntimeError(f"{operation} unavailable")
        self.calls.append((operation, args, kwargs))
        return {"operation": operation, "args": args, "kwargs": kwargs}

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

    def get_trades(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._record("get_trades", *args, **kwargs)

    def make_order(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._record("make_order", *args, **kwargs)

    def cancel_order(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._record("cancel_order", *args, **kwargs)

    def cancel_all(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._record("cancel_all", *args, **kwargs)

    def query_order(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._record("query_order", *args, **kwargs)

    async def async_get_tick(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._record("async_get_tick", *args, **kwargs)

    async def async_get_depth(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._record("async_get_depth", *args, **kwargs)

    async def async_get_kline(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._record("async_get_kline", *args, **kwargs)

    async def async_make_order(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._record("async_make_order", *args, **kwargs)

    async def async_cancel_order(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._record("async_cancel_order", *args, **kwargs)

    async def async_cancel_all(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._record("async_cancel_all", *args, **kwargs)

    async def async_query_order(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._record("async_query_order", *args, **kwargs)

    async def async_get_open_orders(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._record("async_get_open_orders", *args, **kwargs)

    async def async_get_balance(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._record("async_get_balance", *args, **kwargs)

    async def async_get_account(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._record("async_get_account", *args, **kwargs)

    async def async_get_position(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._record("async_get_position", *args, **kwargs)

    def disconnect(self) -> None:
        self.disconnect_calls += 1


class _Bars:
    def get_data(self) -> list[dict[str, str]]:
        return [{"close": "101"}, {"close": "102"}]


def _api_with_direct_feed() -> tuple[BtApi, _DirectFeed]:
    api = BtApi(debug=False)
    feed = _DirectFeed()
    api.exchange_feeds["COVERAGE___SPOT"] = feed
    api.data_queues["COVERAGE___SPOT"] = queue.Queue()
    return api, feed


def test_direct_backend_preserves_all_public_v1_operations() -> None:
    api, feed = _api_with_direct_feed()
    request = OrderRequest(
        symbol="BTC-USDT",
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("1"),
        price=Decimal("100"),
        account_id="acct-1",
        client_order_id="client-1",
    )

    assert api.get_tick("COVERAGE___SPOT", "BTC-USDT")["operation"] == "get_tick"
    assert api.get_depth("COVERAGE___SPOT", "BTC-USDT", 3)["operation"] == "get_depth"
    assert api.get_kline("COVERAGE___SPOT", "BTC-USDT", "1m", 3)["operation"] == "get_kline"
    assert api.get_account("COVERAGE___SPOT")["operation"] == "get_account"
    assert api.get_balance("COVERAGE___SPOT", "USDT")["operation"] == "get_balance"
    assert api.get_position("COVERAGE___SPOT", "BTC-USDT")["operation"] == "get_position"
    assert api.get_open_orders("COVERAGE___SPOT")["operation"] == "get_open_orders"
    assert api.get_deals("COVERAGE___SPOT")["operation"] == "get_deals"
    assert api.get_trades("COVERAGE___SPOT", "BTC-USDT")["operation"] == "get_trades"
    assert api.make_order("COVERAGE___SPOT", request)["operation"] == "make_order"
    assert (
        api.make_order("COVERAGE___SPOT", "BTC-USDT", 1, 100, "buy-limit")["operation"]
        == "make_order"
    )
    assert api.cancel_order("COVERAGE___SPOT", "BTC-USDT", "order-1")["operation"] == "cancel_order"
    assert api.cancel_all("COVERAGE___SPOT", "BTC-USDT")["operation"] == "cancel_all"
    assert api.query_order("COVERAGE___SPOT", "BTC-USDT", "order-1")["operation"] == "query_order"

    assert api.get_request_api("COVERAGE___SPOT") is feed
    assert api.get_capabilities("COVERAGE___SPOT").operations["make_order"] is True
    with pytest.raises(CapabilityNotSupportedError, match="transport=direct"):
        api.get_command_status("COVERAGE___SPOT", "command-1")


def test_direct_runtime_utilities_and_batch_failures_are_explicit() -> None:
    api, feed = _api_with_direct_feed()
    failed_feed = _DirectFeed(fail=True)
    api.exchange_feeds["FAILED___SPOT"] = failed_feed

    assert api.get_data_queue("COVERAGE___SPOT") is api.data_queues["COVERAGE___SPOT"]
    assert api.get_data_queue("UNKNOWN___SPOT") is None
    assert api.get_event_bus() is api.event_bus
    assert api.put_ticker({"price": 100}, "COVERAGE___SPOT") == {"price": 100}
    api.push_bar_data_to_queue("COVERAGE___SPOT", _Bars())
    assert api.data_queues["COVERAGE___SPOT"].qsize() == 3
    assert api.list_exchanges() == ["COVERAGE___SPOT", "FAILED___SPOT"]
    assert api._validate_order_args("COVERAGE___SPOT", "BTC-USDT", 1, 100, "limit") == "limit"
    with pytest.raises(InvalidOrderError, match="volume"):
        api._validate_order_args("COVERAGE___SPOT", "BTC-USDT", 0, 100, "limit")
    with pytest.raises(TypeError, match="mapping"):
        api._copy_exchange_params(cast("dict[str, Any]", "not-a-mapping"))

    for results in (
        api.get_all_ticks("BTC-USDT"),
        api.get_all_balances(),
        api.get_all_positions(),
        api.cancel_all_orders(),
    ):
        assert results["COVERAGE___SPOT"]
        assert isinstance(results["FAILED___SPOT"], RuntimeError)

    with api as entered:
        assert entered is api
    assert feed.disconnect_calls == 1
    assert failed_feed.disconnect_calls == 1


@pytest.mark.asyncio
async def test_direct_async_operations_keep_the_legacy_feed_boundary() -> None:
    api, feed = _api_with_direct_feed()

    assert (await api.async_get_tick("COVERAGE___SPOT", "BTC-USDT"))[
        "operation"
    ] == "async_get_tick"
    assert (await api.async_get_depth("COVERAGE___SPOT", "BTC-USDT", 3))[
        "operation"
    ] == "async_get_depth"
    assert (await api.async_get_kline("COVERAGE___SPOT", "BTC-USDT", "1m", 3))[
        "operation"
    ] == "async_get_kline"
    assert (await api.async_make_order("COVERAGE___SPOT", "BTC-USDT"))[
        "operation"
    ] == "async_make_order"
    assert (await api.async_cancel_order("COVERAGE___SPOT", "order-1"))[
        "operation"
    ] == "async_cancel_order"
    assert (await api.async_cancel_all("COVERAGE___SPOT"))["operation"] == "async_cancel_all"
    assert (await api.async_query_order("COVERAGE___SPOT", "order-1"))[
        "operation"
    ] == "async_query_order"
    assert (await api.async_get_open_orders("COVERAGE___SPOT"))[
        "operation"
    ] == "async_get_open_orders"
    assert (await api.async_get_balance("COVERAGE___SPOT"))["operation"] == "async_get_balance"
    assert (await api.async_get_account("COVERAGE___SPOT"))["operation"] == "async_get_account"
    assert (await api.async_get_position("COVERAGE___SPOT"))["operation"] == "async_get_position"

    async with api as entered:
        assert entered is api
    assert feed.disconnect_calls == 1
