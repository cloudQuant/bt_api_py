"""
Tests for BigONE Spot Feed implementation.
Comprehensive coverage: parameter generation, normalization, mocked HTTP sync
calls, registry, method existence, feed init, auth, and integration tests.

BigONE API v3 with JWT (HS256) authentication.
Responses wrapped in {"data": ...}.
"""

import queue
import pytest
from unittest.mock import patch

from bt_api_py.containers.exchanges.bigone_exchange_data import (
    BigONEExchangeData,
    BigONEExchangeDataSpot,
)
from bt_api_py.feeds.live_bigone.spot import BigONERequestDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData

# Import registration to auto-register BigONE
import bt_api_py.feeds.register_bigone  # noqa: F401

# ── Sample API responses (BigONE wraps in {"data": ...}) ─────

SAMPLE_TICKER = {
    "data": {
        "asset_pair_name": "BTC-USDT",
        "bid": {"price": "49999.00", "quantity": "0.5"},
        "ask": {"price": "50001.00", "quantity": "0.3"},
        "open": "49500.00", "high": "51000.00", "low": "49000.00",
        "close": "50000.50", "volume": "1234.56",
    }
}

SAMPLE_ORDERBOOK = {
    "data": {
        "asset_pair_name": "BTC-USDT",
        "bids": [{"price": "49999", "quantity": "0.5"}, {"price": "49998", "quantity": "1.2"}],
        "asks": [{"price": "50001", "quantity": "0.3"}, {"price": "50002", "quantity": "0.8"}],
    }
}

SAMPLE_KLINES = {
    "data": [
        {"time": "2024-01-01T00:00:00Z", "open": "50000", "high": "51000",
         "low": "49000", "close": "50500", "volume": "1000"},
        {"time": "2024-01-01T01:00:00Z", "open": "50500", "high": "51500",
         "low": "50000", "close": "51000", "volume": "1500"},
    ]
}

SAMPLE_TRADES = {
    "data": [
        {"id": "1001", "price": "50000", "amount": "0.01", "side": "BID",
         "created_at": "2024-01-01T00:00:00Z"},
        {"id": "1002", "price": "50001", "amount": "0.02", "side": "ASK",
         "created_at": "2024-01-01T00:00:01Z"},
    ]
}

SAMPLE_BALANCE = {
    "data": [
        {"asset_symbol": "USDT", "balance": "1000.00", "locked_balance": "100.00"},
        {"asset_symbol": "BTC", "balance": "0.5", "locked_balance": "0.1"},
    ]
}

SAMPLE_ORDER = {
    "data": {
        "id": "12345678", "asset_pair_name": "BTC-USDT", "side": "BID",
        "type": "LIMIT", "price": "50000", "amount": "0.001",
        "filled_amount": "0", "state": "PENDING",
        "created_at": "2024-01-01T00:00:00Z",
    }
}

SAMPLE_OPEN_ORDERS = {
    "data": [
        {"id": "12345678", "asset_pair_name": "BTC-USDT", "side": "BID",
         "state": "PENDING", "price": "50000", "amount": "0.001"},
    ]
}

SAMPLE_SERVER_TIME = {"data": {"timestamp": "2024-01-01T00:00:00Z"}}

SAMPLE_SYMBOLS = {
    "data": [
        {"name": "BTC-USDT", "base_asset": {"symbol": "BTC"},
         "quote_asset": {"symbol": "USDT"}, "base_scale": 8, "quote_scale": 2},
        {"name": "ETH-USDT", "base_asset": {"symbol": "ETH"},
         "quote_asset": {"symbol": "USDT"}, "base_scale": 8, "quote_scale": 2},
    ]
}

SAMPLE_ERROR = {"errors": [{"code": 10001, "message": "Insufficient funds"}]}
SAMPLE_CANCEL_OK = {"data": {"id": "12345678", "state": "CANCELLED"}}


# ── Helpers ──────────────────────────────────────────────────

def _make_feed(**kwargs):
    q = kwargs.pop("data_queue", queue.Queue())
    defaults = {"public_key": "test_key", "private_key": "test_secret"}
    defaults.update(kwargs)
    return BigONERequestDataSpot(q, **defaults)


# ── 1. Exchange Data Container ───────────────────────────────


class TestExchangeData:
    def test_exchange_name(self):
        ed = BigONEExchangeDataSpot()
        assert ed.exchange_name == "BIGONE___SPOT"

    def test_rest_url(self):
        ed = BigONEExchangeDataSpot()
        assert ed.rest_url.startswith("https://")
        assert "big.one" in ed.rest_url

    def test_wss_url(self):
        ed = BigONEExchangeDataSpot()
        assert ed.wss_url.startswith("wss://")

    def test_get_symbol_slash(self):
        ed = BigONEExchangeDataSpot()
        assert ed.get_symbol("BTC/USDT") == "BTC-USDT"

    def test_get_symbol_underscore(self):
        ed = BigONEExchangeDataSpot()
        assert ed.get_symbol("BTC_USDT") == "BTC-USDT"

    def test_get_symbol_dash(self):
        ed = BigONEExchangeDataSpot()
        assert ed.get_symbol("BTC-USDT") == "BTC-USDT"

    def test_get_symbol_lowercase(self):
        ed = BigONEExchangeDataSpot()
        assert ed.get_symbol("btc/usdt") == "BTC-USDT"

    def test_get_period(self):
        ed = BigONEExchangeDataSpot()
        assert ed.get_period("1m") == "min1"
        assert ed.get_period("1h") == "hour1"
        assert ed.get_period("1d") == "day1"

    def test_kline_periods_complete(self):
        ed = BigONEExchangeDataSpot()
        for k in ("1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"):
            assert k in ed.kline_periods

    def test_get_rest_path_valid(self):
        ed = BigONEExchangeDataSpot()
        path = ed.get_rest_path("get_tick")
        assert "GET" in path and "{symbol}" in path

    def test_get_rest_path_invalid_raises(self):
        ed = BigONEExchangeDataSpot()
        with pytest.raises(ValueError, match="Unknown rest path"):
            ed.get_rest_path("nonexistent_endpoint")

    def test_rest_paths_complete(self):
        ed = BigONEExchangeDataSpot()
        required = ["get_server_time", "get_exchange_info", "get_tick", "get_depth",
                     "get_kline", "get_trades", "make_order", "cancel_order",
                     "query_order", "get_open_orders", "get_balance", "get_account"]
        for key in required:
            assert key in ed.rest_paths, f"Missing rest_path: {key}"

    def test_legal_currency(self):
        ed = BigONEExchangeDataSpot()
        assert "USDT" in ed.legal_currency
        assert "BTC" in ed.legal_currency


# ── 2. Parameter Generation ──────────────────────────────────


class TestParameterGeneration:
    def test_tick_params(self):
        feed = _make_feed()
        path, params, ed = feed._get_tick("BTC/USDT")
        assert "GET" in path
        assert "BTC-USDT" in path
        assert ed["request_type"] == "get_tick"
        assert ed["symbol_name"] == "BTC/USDT"

    def test_depth_params(self):
        feed = _make_feed()
        path, params, ed = feed._get_depth("BTC/USDT", count=20)
        assert params["limit"] == 20
        assert "BTC-USDT" in path

    def test_kline_params(self):
        feed = _make_feed()
        path, params, ed = feed._get_kline("BTC/USDT", period="1h", count=100)
        assert params["period"] == "hour1"
        assert params["limit"] == 100
        assert "BTC-USDT" in path

    def test_kline_limit_capped(self):
        feed = _make_feed()
        _, params, _ = feed._get_kline("BTC/USDT", count=9999)
        assert params["limit"] == 500

    def test_trade_history_params(self):
        feed = _make_feed()
        path, params, ed = feed._get_trade_history("BTC/USDT", count=200)
        assert params["limit"] == 200
        assert "BTC-USDT" in path
        assert ed["request_type"] == "get_trades"

    def test_server_time_params(self):
        feed = _make_feed()
        path, params, ed = feed._get_server_time()
        assert "GET" in path and "/ping" in path
        assert params == {}
        assert ed["request_type"] == "get_server_time"

    def test_exchange_info_params(self):
        feed = _make_feed()
        path, params, ed = feed._get_exchange_info()
        assert "GET" in path and "/asset_pairs" in path
        assert ed["request_type"] == "get_exchange_info"

    def test_make_order_limit_buy(self):
        feed = _make_feed()
        path, body, ed = feed._make_order("BTC/USDT", "0.001", "50000", order_type="buy-limit")
        assert body["side"] == "BID"
        assert body["type"] == "LIMIT"
        assert body["amount"] == "0.001"
        assert body["price"] == "50000"
        assert body["asset_pair_name"] == "BTC-USDT"
        assert "POST" in path

    def test_make_order_market_sell(self):
        feed = _make_feed()
        _, body, _ = feed._make_order("BTC/USDT", "0.001", order_type="sell-market")
        assert body["side"] == "ASK"
        assert body["type"] == "MARKET"

    def test_cancel_order(self):
        feed = _make_feed()
        path, params, ed = feed._cancel_order(order_id="12345678")
        assert "POST" in path
        assert "12345678" in path
        assert ed["request_type"] == "cancel_order"

    def test_query_order(self):
        feed = _make_feed()
        path, params, ed = feed._query_order(order_id="12345678")
        assert "GET" in path
        assert "12345678" in path
        assert ed["request_type"] == "query_order"

    def test_get_open_orders_with_symbol(self):
        feed = _make_feed()
        path, params, ed = feed._get_open_orders(symbol="BTC/USDT")
        assert params["asset_pair_name"] == "BTC-USDT"
        assert params["state"] == "PENDING"

    def test_get_open_orders_all(self):
        feed = _make_feed()
        _, params, _ = feed._get_open_orders()
        assert "state" in params
        assert "asset_pair_name" not in params

    def test_get_balance(self):
        feed = _make_feed()
        path, params, ed = feed._get_balance()
        assert ed["request_type"] == "get_balance"
        assert "GET" in path and "/viewer/accounts" in path

    def test_get_account(self):
        feed = _make_feed()
        path, params, ed = feed._get_account()
        assert ed["request_type"] == "get_account"


# ── 3. Normalization Functions ────────────────────────────────


class TestNormalization:
    def test_tick_wrapped(self):
        data, ok = BigONERequestDataSpot._get_tick_normalize_function(SAMPLE_TICKER, {})
        assert ok is True
        assert len(data) == 1
        assert "close" in data[0]

    def test_tick_none(self):
        data, ok = BigONERequestDataSpot._get_tick_normalize_function(None, {})
        assert ok is False

    def test_tick_error(self):
        data, ok = BigONERequestDataSpot._get_tick_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_depth_wrapped(self):
        data, ok = BigONERequestDataSpot._get_depth_normalize_function(SAMPLE_ORDERBOOK, {})
        assert ok is True
        assert "bids" in data[0]

    def test_kline_wrapped(self):
        data, ok = BigONERequestDataSpot._get_kline_normalize_function(SAMPLE_KLINES, {})
        assert ok is True
        assert len(data) == 2

    def test_kline_empty(self):
        data, ok = BigONERequestDataSpot._get_kline_normalize_function({"data": []}, {})
        assert ok is False

    def test_kline_error(self):
        data, ok = BigONERequestDataSpot._get_kline_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_trades_wrapped(self):
        data, ok = BigONERequestDataSpot._get_trade_history_normalize_function(SAMPLE_TRADES, {})
        assert ok is True
        assert len(data) == 2

    def test_balance_wrapped(self):
        data, ok = BigONERequestDataSpot._get_balance_normalize_function(SAMPLE_BALANCE, {})
        assert ok is True
        assert len(data) == 2

    def test_make_order_wrapped(self):
        data, ok = BigONERequestDataSpot._make_order_normalize_function(SAMPLE_ORDER, {})
        assert ok is True
        assert data[0]["id"] == "12345678"

    def test_make_order_error(self):
        data, ok = BigONERequestDataSpot._make_order_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_server_time(self):
        data, ok = BigONERequestDataSpot._get_server_time_normalize_function(SAMPLE_SERVER_TIME, {})
        assert ok is True

    def test_exchange_info_wrapped(self):
        data, ok = BigONERequestDataSpot._get_exchange_info_normalize_function(SAMPLE_SYMBOLS, {})
        assert ok is True
        assert "symbols" in data[0]

    def test_account_wrapped(self):
        data, ok = BigONERequestDataSpot._get_account_normalize_function(SAMPLE_BALANCE, {})
        assert ok is True

    def test_open_orders_wrapped(self):
        data, ok = BigONERequestDataSpot._get_open_orders_normalize_function(SAMPLE_OPEN_ORDERS, {})
        assert ok is True
        assert len(data) == 1

    def test_cancel_order_ok(self):
        data, ok = BigONERequestDataSpot._cancel_order_normalize_function(SAMPLE_CANCEL_OK, {})
        assert ok is True

    def test_query_order_wrapped(self):
        data, ok = BigONERequestDataSpot._query_order_normalize_function(SAMPLE_ORDER, {})
        assert ok is True

    def test_is_error(self):
        assert BigONERequestDataSpot._is_error(None) is True
        assert BigONERequestDataSpot._is_error(SAMPLE_ERROR) is True
        assert BigONERequestDataSpot._is_error(SAMPLE_TICKER) is False

    def test_unwrap(self):
        assert BigONERequestDataSpot._unwrap({"data": "hello"}) == "hello"
        assert BigONERequestDataSpot._unwrap({"other": 1}) == {"other": 1}


# ── 4. Mocked Sync Calls ─────────────────────────────────────


class TestSyncCalls:
    def _call(self, method_name, return_json, *args, **kwargs):
        feed = _make_feed()
        with patch.object(feed, "http_request", return_value=return_json):
            method = getattr(feed, method_name)
            result = method(*args, **kwargs)
            assert isinstance(result, RequestData)
            return result

    def test_get_tick(self):
        self._call("get_tick", SAMPLE_TICKER, "BTC/USDT")

    def test_get_depth(self):
        self._call("get_depth", SAMPLE_ORDERBOOK, "BTC/USDT", count=10)

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
        self._call("get_open_orders", SAMPLE_OPEN_ORDERS, symbol="BTC/USDT")

    def test_make_order(self):
        feed = _make_feed()
        with patch.object(feed, "http_request", return_value=SAMPLE_ORDER):
            result = feed.make_order("BTC/USDT", "0.001", "50000", order_type="buy-limit")
            assert isinstance(result, RequestData)

    def test_cancel_order(self):
        feed = _make_feed()
        with patch.object(feed, "http_request", return_value=SAMPLE_CANCEL_OK):
            result = feed.cancel_order(order_id="12345678")
            assert isinstance(result, RequestData)

    def test_query_order(self):
        self._call("query_order", SAMPLE_ORDER, order_id="12345678")


# ── 5. Auth (JWT) ────────────────────────────────────────────


class TestAuth:
    def test_jwt_token_generation(self):
        feed = _make_feed(public_key="mykey", private_key="mysecret")
        token = feed._generate_jwt_token()
        assert isinstance(token, str)
        assert len(token) > 10

    def test_jwt_empty_without_keys(self):
        feed = _make_feed(public_key="", private_key="")
        token = feed._generate_jwt_token()
        assert token == ""


# ── 6. Registry ──────────────────────────────────────────────


class TestRegistry:
    def test_bigone_spot_registered(self):
        from bt_api_py.registry import ExchangeRegistry
        assert ExchangeRegistry.has_exchange("BIGONE___SPOT")
        assert ExchangeRegistry._feed_classes["BIGONE___SPOT"] is BigONERequestDataSpot

    def test_exchange_data_registered(self):
        from bt_api_py.registry import ExchangeRegistry
        assert ExchangeRegistry._exchange_data_classes["BIGONE___SPOT"] is BigONEExchangeDataSpot

    def test_balance_handler_registered(self):
        from bt_api_py.registry import ExchangeRegistry
        handler = ExchangeRegistry.get_balance_handler("BIGONE___SPOT")
        assert callable(handler)

    def test_create_exchange_data(self):
        from bt_api_py.registry import ExchangeRegistry
        ed = ExchangeRegistry.create_exchange_data("BIGONE___SPOT")
        assert isinstance(ed, BigONEExchangeDataSpot)


# ── 7. Method Existence ──────────────────────────────────────


class TestMethodExistence:
    METHODS = [
        "get_tick", "async_get_tick",
        "get_ticker", "async_get_ticker",
        "get_depth", "async_get_depth",
        "get_kline", "async_get_kline",
        "get_trade_history", "async_get_trade_history",
        "get_trades", "async_get_trades",
        "make_order", "async_make_order",
        "cancel_order", "async_cancel_order",
        "query_order", "async_query_order",
        "get_open_orders", "async_get_open_orders",
        "get_account", "async_get_account",
        "get_balance", "async_get_balance",
        "get_server_time", "async_get_server_time",
        "get_exchange_info", "async_get_exchange_info",
    ]

    @pytest.mark.parametrize("method_name", METHODS, ids=METHODS)
    def test_method_exists(self, method_name):
        feed = _make_feed()
        assert hasattr(feed, method_name) and callable(getattr(feed, method_name))


# ── 8. Feed Init ─────────────────────────────────────────────


class TestFeedInit:
    def test_default_exchange_name(self):
        feed = _make_feed()
        assert feed.exchange_name == "BIGONE___SPOT"

    def test_default_asset_type(self):
        feed = _make_feed()
        assert feed.asset_type == "SPOT"

    def test_api_keys(self):
        feed = _make_feed(public_key="k1", private_key="s1")
        assert feed.api_key == "k1"
        assert feed.api_secret == "s1"

    def test_api_key_alias(self):
        q = queue.Queue()
        feed = BigONERequestDataSpot(q, api_key="k2", api_secret="s2")
        assert feed.api_key == "k2"
        assert feed.api_secret == "s2"

    def test_capabilities(self):
        from bt_api_py.feeds.capability import Capability
        caps = BigONERequestDataSpot._capabilities()
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
        result = feed.get_tick("BTC-USDT")
        assert isinstance(result, RequestData)

    @pytest.mark.skip(reason="Requires network")
    def test_live_get_depth(self):
        feed = _make_feed()
        result = feed.get_depth("BTC-USDT")
        assert isinstance(result, RequestData)

    @pytest.mark.skip(reason="Requires network")
    def test_live_get_kline(self):
        feed = _make_feed()
        result = feed.get_kline("BTC-USDT", period="1h")
        assert isinstance(result, RequestData)

    @pytest.mark.skip(reason="Requires network")
    def test_live_get_exchange_info(self):
        feed = _make_feed()
        result = feed.get_exchange_info()
        assert isinstance(result, RequestData)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
