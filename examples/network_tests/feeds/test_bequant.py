"""
Tests for BeQuant Spot Feed implementation.
Comprehensive coverage: parameter generation, normalization, mocked HTTP sync
calls, registry, method existence, feed init, auth, and integration tests.

BeQuant uses HitBTC V3 API (white-label) with HTTP Basic Auth.
"""

import queue
from unittest.mock import patch

import pytest

# Import registration to auto-register BeQuant
import bt_api_py.exchange_registers.register_bequant  # noqa: F401
from bt_api_py.containers.exchanges.bequant_exchange_data import (
    BeQuantExchangeDataSpot,
)
from bt_api_base.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_bequant.spot import BeQuantRequestDataSpot

# ── Sample API responses ─────────────────────────────────────

SAMPLE_TICKER = {
    "ask": "50001.00",
    "bid": "49999.00",
    "last": "50000.50",
    "open": "49500.00",
    "high": "51000.00",
    "low": "49000.00",
    "volume": "1234.56",
    "volume_quote": "61728000",
    "timestamp": "2024-01-01T00:00:00.000Z",
}

SAMPLE_ORDERBOOK = {
    "ask": [{"price": "50001", "size": "1.0"}, {"price": "50002", "size": "2.0"}],
    "bid": [{"price": "49999", "size": "1.5"}, {"price": "49998", "size": "3.0"}],
    "timestamp": "2024-01-01T00:00:00.000Z",
}

SAMPLE_KLINES = [
    {
        "timestamp": "2024-01-01T00:00:00.000Z",
        "open": "50000",
        "close": "50500",
        "min": "49000",
        "max": "51000",
        "volume": "1000",
        "volume_quote": "50000000",
    },
    {
        "timestamp": "2024-01-01T01:00:00.000Z",
        "open": "50500",
        "close": "51000",
        "min": "50000",
        "max": "51500",
        "volume": "1500",
        "volume_quote": "75000000",
    },
]

SAMPLE_TRADES = [
    {
        "id": 1001,
        "price": "50000",
        "quantity": "0.01",
        "side": "buy",
        "timestamp": "2024-01-01T00:00:00.000Z",
    },
    {
        "id": 1002,
        "price": "50001",
        "quantity": "0.02",
        "side": "sell",
        "timestamp": "2024-01-01T00:00:01.000Z",
    },
]

SAMPLE_BALANCE = [
    {"currency": "USDT", "available": "1000.00", "reserved": "100.00"},
    {"currency": "BTC", "available": "0.5", "reserved": "0.1"},
]

SAMPLE_ORDER = {
    "id": 123456,
    "client_order_id": "my-order-1",
    "symbol": "BTCUSDT",
    "side": "buy",
    "status": "new",
    "type": "limit",
    "time_in_force": "GTC",
    "quantity": "1.0",
    "price": "50000",
    "created_at": "2024-01-01T00:00:00.000Z",
}

SAMPLE_SERVER_TIME = {"iso": "2024-01-01T00:00:00.000Z", "timestamp": 1704067200000}

SAMPLE_SYMBOLS = {
    "BTCUSDT": {
        "type": "spot",
        "base_currency": "BTC",
        "quote_currency": "USDT",
        "status": "trading",
        "quantity_increment": "0.000001",
    },
    "ETHUSDT": {
        "type": "spot",
        "base_currency": "ETH",
        "quote_currency": "USDT",
        "status": "trading",
        "quantity_increment": "0.00001",
    },
}

SAMPLE_ERROR = {"error": {"code": 20001, "message": "Insufficient funds"}}


# ── Helpers ──────────────────────────────────────────────────


def _make_feed(**kwargs):
    q = kwargs.pop("data_queue", queue.Queue())
    defaults = {"public_key": "test_key", "private_key": "test_secret"}
    defaults.update(kwargs)
    return BeQuantRequestDataSpot(q, **defaults)


# ── 1. Exchange Data Container ───────────────────────────────


class TestExchangeData:
    def test_exchange_name(self):
        ed = BeQuantExchangeDataSpot()
        assert ed.exchange_name == "BEQUANT___SPOT"

    def test_rest_url(self):
        ed = BeQuantExchangeDataSpot()
        assert ed.rest_url.startswith("https://")
        assert "bequant" in ed.rest_url

    def test_wss_url(self):
        ed = BeQuantExchangeDataSpot()
        assert ed.wss_url.startswith("wss://")

    def test_get_symbol_slash(self):
        ed = BeQuantExchangeDataSpot()
        assert ed.get_symbol("BTC/USDT") == "BTCUSDT"

    def test_get_symbol_dash(self):
        ed = BeQuantExchangeDataSpot()
        assert ed.get_symbol("BTC-USDT") == "BTCUSDT"

    def test_get_symbol_underscore(self):
        ed = BeQuantExchangeDataSpot()
        assert ed.get_symbol("BTC_USDT") == "BTCUSDT"

    def test_get_symbol_noop(self):
        ed = BeQuantExchangeDataSpot()
        assert ed.get_symbol("BTCUSDT") == "BTCUSDT"

    def test_get_symbol_lowercase(self):
        ed = BeQuantExchangeDataSpot()
        assert ed.get_symbol("btcusdt") == "BTCUSDT"

    def test_get_period(self):
        ed = BeQuantExchangeDataSpot()
        assert ed.get_period("1m") == "M1"
        assert ed.get_period("1h") == "H1"
        assert ed.get_period("1d") == "D1"

    @pytest.mark.kline
    def test_kline_periods_complete(self):
        ed = BeQuantExchangeDataSpot()
        for k in ("1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"):
            assert k in ed.kline_periods

    def test_get_rest_path_valid(self):
        ed = BeQuantExchangeDataSpot()
        path = ed.get_rest_path("get_tick")
        assert "GET" in path and "{symbol}" in path

    def test_get_rest_path_invalid_raises(self):
        ed = BeQuantExchangeDataSpot()
        with pytest.raises(ValueError, match="Unknown rest path"):
            ed.get_rest_path("nonexistent_endpoint")

    def test_rest_paths_complete(self):
        ed = BeQuantExchangeDataSpot()
        required = [
            "get_server_time",
            "get_exchange_info",
            "get_tick",
            "get_depth",
            "get_kline",
            "get_trades",
            "make_order",
            "cancel_order",
            "query_order",
            "get_open_orders",
            "get_balance",
            "get_account",
        ]
        for key in required:
            assert key in ed.rest_paths, f"Missing rest_path: {key}"

    def test_legal_currency(self):
        ed = BeQuantExchangeDataSpot()
        assert "USDT" in ed.legal_currency
        assert "BTC" in ed.legal_currency


# ── 2. Parameter Generation ──────────────────────────────────


class TestParameterGeneration:
    def test_tick_params(self):
        feed = _make_feed()
        path, params, ed = feed._get_tick("BTC/USDT")
        assert "GET" in path
        assert "BTCUSDT" in path
        assert ed["request_type"] == "get_tick"
        assert ed["symbol_name"] == "BTC/USDT"

    def test_depth_params(self):
        feed = _make_feed()
        path, params, ed = feed._get_depth("BTC/USDT", count=20)
        assert params["depth"] == 20
        assert "BTCUSDT" in path

    def test_kline_params(self):
        feed = _make_feed()
        path, params, ed = feed._get_kline("BTC/USDT", period="1h", count=100)
        assert params["period"] == "H1"
        assert params["limit"] == 100
        assert "BTCUSDT" in path

    def test_kline_limit_capped(self):
        feed = _make_feed()
        _, params, _ = feed._get_kline("BTC/USDT", count=9999)
        assert params["limit"] == 1000

    def test_trade_history_params(self):
        feed = _make_feed()
        path, params, ed = feed._get_trade_history("BTC/USDT", count=200)
        assert params["limit"] == 200
        assert "BTCUSDT" in path
        assert ed["request_type"] == "get_trades"

    def test_server_time_params(self):
        feed = _make_feed()
        path, params, ed = feed._get_server_time()
        assert "GET" in path and "/public/time" in path
        assert params == {}
        assert ed["request_type"] == "get_server_time"

    def test_exchange_info_params(self):
        feed = _make_feed()
        path, params, ed = feed._get_exchange_info()
        assert "GET" in path and "/public/symbol" in path
        assert ed["request_type"] == "get_exchange_info"

    def test_make_order_limit_buy(self):
        feed = _make_feed()
        path, body, ed = feed._make_order("BTC/USDT", "1.0", "50000", order_type="buy-limit")
        assert body["side"] == "buy"
        assert body["type"] == "limit"
        assert body["quantity"] == "1.0"
        assert body["price"] == "50000"
        assert body["symbol"] == "BTCUSDT"
        assert "POST" in path

    def test_make_order_market_sell(self):
        feed = _make_feed()
        _, body, _ = feed._make_order("BTC/USDT", "1.0", order_type="sell-market")
        assert body["side"] == "sell"
        assert body["type"] == "market"

    def test_cancel_order_by_id(self):
        feed = _make_feed()
        path, params, ed = feed._cancel_order(order_id="abc123")
        assert path == "DELETE /spot/order/abc123"
        assert params == {}

    def test_query_order(self):
        feed = _make_feed()
        path, params, ed = feed._query_order(order_id="abc123")
        assert path == "GET /spot/order/abc123"
        assert ed["request_type"] == "query_order"

    def test_get_open_orders_with_symbol(self):
        feed = _make_feed()
        path, params, ed = feed._get_open_orders(symbol="BTC/USDT")
        assert params["symbol"] == "BTCUSDT"

    def test_get_open_orders_all(self):
        feed = _make_feed()
        _, params, _ = feed._get_open_orders()
        assert params == {}

    def test_get_balance(self):
        feed = _make_feed()
        path, params, ed = feed._get_balance()
        assert ed["request_type"] == "get_balance"
        assert "GET" in path and "/spot/balance" in path

    def test_get_account(self):
        feed = _make_feed()
        path, params, ed = feed._get_account()
        assert ed["request_type"] == "get_account"


# ── 3. Normalization Functions ────────────────────────────────


class TestNormalization:
    @pytest.mark.ticker
    def test_tick_dict(self):
        data, ok = BeQuantRequestDataSpot._get_tick_normalize_function(SAMPLE_TICKER, {})
        assert ok is True
        assert len(data) == 1

    @pytest.mark.ticker
    def test_tick_none(self):
        data, ok = BeQuantRequestDataSpot._get_tick_normalize_function(None, {})
        assert ok is False

    @pytest.mark.ticker
    def test_tick_error(self):
        data, ok = BeQuantRequestDataSpot._get_tick_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    @pytest.mark.orderbook
    def test_depth_dict(self):
        data, ok = BeQuantRequestDataSpot._get_depth_normalize_function(SAMPLE_ORDERBOOK, {})
        assert ok is True

    @pytest.mark.kline
    def test_kline_list(self):
        data, ok = BeQuantRequestDataSpot._get_kline_normalize_function(SAMPLE_KLINES, {})
        assert ok is True
        assert len(data) == 2

    @pytest.mark.kline
    def test_kline_empty(self):
        data, ok = BeQuantRequestDataSpot._get_kline_normalize_function([], {})
        assert ok is False

    @pytest.mark.kline
    def test_kline_error(self):
        data, ok = BeQuantRequestDataSpot._get_kline_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_trades_list(self):
        data, ok = BeQuantRequestDataSpot._get_trade_history_normalize_function(SAMPLE_TRADES, {})
        assert ok is True
        assert len(data) == 2

    def test_balance_list(self):
        data, ok = BeQuantRequestDataSpot._get_balance_normalize_function(SAMPLE_BALANCE, {})
        assert ok is True
        assert len(data) == 2

    def test_make_order_dict(self):
        data, ok = BeQuantRequestDataSpot._make_order_normalize_function(SAMPLE_ORDER, {})
        assert ok is True
        assert data[0]["id"] == 123456

    def test_make_order_error(self):
        data, ok = BeQuantRequestDataSpot._make_order_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_server_time(self):
        data, ok = BeQuantRequestDataSpot._get_server_time_normalize_function(
            SAMPLE_SERVER_TIME, {}
        )
        assert ok is True

    def test_exchange_info_dict(self):
        data, ok = BeQuantRequestDataSpot._get_exchange_info_normalize_function(SAMPLE_SYMBOLS, {})
        assert ok is True
        assert "symbols" in data[0]

    def test_account_list(self):
        data, ok = BeQuantRequestDataSpot._get_account_normalize_function(SAMPLE_BALANCE, {})
        assert ok is True

    def test_open_orders_list(self):
        data, ok = BeQuantRequestDataSpot._get_open_orders_normalize_function([SAMPLE_ORDER], {})
        assert ok is True
        assert len(data) == 1

    def test_cancel_order_dict(self):
        data, ok = BeQuantRequestDataSpot._cancel_order_normalize_function(
            {"id": 123, "status": "canceled"}, {}
        )
        assert ok is True

    def test_query_order_dict(self):
        data, ok = BeQuantRequestDataSpot._query_order_normalize_function(SAMPLE_ORDER, {})
        assert ok is True

    def test_is_error(self):
        assert BeQuantRequestDataSpot._is_error(None) is True
        assert BeQuantRequestDataSpot._is_error(SAMPLE_ERROR) is True
        assert BeQuantRequestDataSpot._is_error(SAMPLE_TICKER) is False


# ── 4. Mocked Sync Calls ─────────────────────────────────────


class TestSyncCalls:
    def _call(self, method_name, return_json, *args, **kwargs):
        feed = _make_feed()
        with patch.object(feed, "http_request", return_value=return_json):
            method = getattr(feed, method_name)
            result = method(*args, **kwargs)
            assert isinstance(result, RequestData)
            return result

    @pytest.mark.ticker
    def test_get_tick(self):
        self._call("get_tick", SAMPLE_TICKER, "BTC/USDT")

    @pytest.mark.orderbook
    def test_get_depth(self):
        self._call("get_depth", SAMPLE_ORDERBOOK, "BTC/USDT", count=10)

    @pytest.mark.kline
    def test_get_kline(self):
        self._call("get_kline", SAMPLE_KLINES, "BTC/USDT", period="1h", count=100)

    def test_get_trade_history(self):
        self._call("get_trade_history", SAMPLE_TRADES, "BTC/USDT")

    def test_get_server_time(self):
        self._call("get_server_time", SAMPLE_SERVER_TIME)

    def test_get_exchange_info(self):
        self._call("get_exchange_info", SAMPLE_SYMBOLS)

    def test_get_balance(self):
        self._call("get_balance", SAMPLE_BALANCE)

    def test_get_account(self):
        self._call("get_account", SAMPLE_BALANCE)

    def test_get_open_orders(self):
        self._call("get_open_orders", [SAMPLE_ORDER], symbol="BTC/USDT")

    def test_make_order(self):
        feed = _make_feed()
        with patch.object(feed, "http_request", return_value=SAMPLE_ORDER):
            result = feed.make_order("BTC/USDT", "1.0", "50000", order_type="buy-limit")
            assert isinstance(result, RequestData)

    def test_cancel_order(self):
        feed = _make_feed()
        with patch.object(feed, "http_request", return_value={"status": "canceled"}):
            result = feed.cancel_order(order_id="abc123")
            assert isinstance(result, RequestData)

    def test_query_order(self):
        self._call("query_order", SAMPLE_ORDER, order_id="abc123")


# ── 5. Auth ──────────────────────────────────────────────────


class TestAuth:
    def test_basic_auth_header(self):
        feed = _make_feed()
        header = feed._basic_auth_header()
        assert header.startswith("Basic ")
        assert len(header) > 10

    def test_basic_auth_contains_key(self):
        import base64

        feed = _make_feed(public_key="mykey", private_key="mysecret")
        header = feed._basic_auth_header()
        decoded = base64.b64decode(header.replace("Basic ", "")).decode("ascii")
        assert decoded == "mykey:mysecret"


# ── 6. Registry ──────────────────────────────────────────────


class TestRegistry:
    def test_bequant_spot_registered(self):
        from bt_api_py.registry import ExchangeRegistry

        assert ExchangeRegistry.has_exchange("BEQUANT___SPOT")
        assert ExchangeRegistry._feed_classes["BEQUANT___SPOT"] is BeQuantRequestDataSpot

    def test_exchange_data_registered(self):
        from bt_api_py.registry import ExchangeRegistry

        assert ExchangeRegistry._exchange_data_classes["BEQUANT___SPOT"] is BeQuantExchangeDataSpot

    def test_balance_handler_registered(self):
        from bt_api_py.registry import ExchangeRegistry

        handler = ExchangeRegistry.get_balance_handler("BEQUANT___SPOT")
        assert callable(handler)

    def test_create_exchange_data(self):
        from bt_api_py.registry import ExchangeRegistry

        ed = ExchangeRegistry.create_exchange_data("BEQUANT___SPOT")
        assert isinstance(ed, BeQuantExchangeDataSpot)


# ── 7. Method Existence ──────────────────────────────────────


class TestMethodExistence:
    METHODS = [
        "get_tick",
        "async_get_tick",
        "get_ticker",
        "async_get_ticker",
        "get_depth",
        "async_get_depth",
        "get_kline",
        "async_get_kline",
        "get_trade_history",
        "async_get_trade_history",
        "get_trades",
        "async_get_trades",
        "make_order",
        "async_make_order",
        "cancel_order",
        "async_cancel_order",
        "query_order",
        "async_query_order",
        "get_open_orders",
        "async_get_open_orders",
        "get_account",
        "async_get_account",
        "get_balance",
        "async_get_balance",
        "get_server_time",
        "async_get_server_time",
        "get_exchange_info",
        "async_get_exchange_info",
    ]

    @pytest.mark.parametrize("method_name", METHODS, ids=METHODS)
    def test_method_exists(self, method_name):
        feed = _make_feed()
        assert hasattr(feed, method_name) and callable(getattr(feed, method_name))


# ── 8. Feed Init ─────────────────────────────────────────────


class TestFeedInit:
    def test_default_exchange_name(self):
        feed = _make_feed()
        assert feed.exchange_name == "BEQUANT___SPOT"

    def test_default_asset_type(self):
        feed = _make_feed()
        assert feed.asset_type == "SPOT"

    def test_api_keys(self):
        feed = _make_feed(public_key="k1", private_key="s1")
        assert feed.api_key == "k1"
        assert feed.api_secret == "s1"

    def test_api_key_alias(self):
        q = queue.Queue()
        feed = BeQuantRequestDataSpot(q, api_key="k2", api_secret="s2")
        assert feed.api_key == "k2"
        assert feed.api_secret == "s2"

    def test_capabilities(self):
        from bt_api_base.feeds.capability import Capability

        caps = BeQuantRequestDataSpot._capabilities()
        assert Capability.GET_TICK in caps
        assert Capability.GET_DEPTH in caps
        assert Capability.GET_KLINE in caps
        assert Capability.MAKE_ORDER in caps
        assert Capability.GET_BALANCE in caps

    def test_push_data_to_queue(self):
        q = queue.Queue()
        feed = _make_feed(data_queue=q)
        feed.push_data_to_queue("test_data")
        assert q.get_nowait() == "test_data"


# ── 9. Integration (skipped by default) ──────────────────────


class TestIntegration:
    @pytest.mark.skip(reason="Requires network")
    def test_live_get_tick(self):
        feed = _make_feed()
        result = feed.get_tick("BTCUSDT")
        assert isinstance(result, RequestData)

    @pytest.mark.skip(reason="Requires network")
    def test_live_get_depth(self):
        feed = _make_feed()
        result = feed.get_depth("BTCUSDT")
        assert isinstance(result, RequestData)

    @pytest.mark.skip(reason="Requires network")
    def test_live_get_kline(self):
        feed = _make_feed()
        result = feed.get_kline("BTCUSDT", period="1h")
        assert isinstance(result, RequestData)

    @pytest.mark.skip(reason="Requires network")
    def test_live_get_exchange_info(self):
        feed = _make_feed()
        result = feed.get_exchange_info()
        assert isinstance(result, RequestData)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
