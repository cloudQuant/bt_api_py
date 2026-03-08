"""
Test Bitso exchange integration.

Run tests:
    pytest tests/feeds/test_bitso.py -v
"""

import queue
from unittest.mock import MagicMock

import pytest

import bt_api_py.exchange_registers.register_bitso  # noqa: F401
from bt_api_py.containers.exchanges.bitso_exchange_data import (
    BitsoExchangeDataSpot,
)
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_bitso.request_base import BitsoRequestData
from bt_api_py.feeds.live_bitso.spot import BitsoRequestDataSpot
from bt_api_py.registry import ExchangeRegistry

# ── fixtures ────────────────────────────────────────────────────


@pytest.fixture
def exchange_data():
    return BitsoExchangeDataSpot()


@pytest.fixture
def feed():
    q = queue.Queue()
    return BitsoRequestDataSpot(q)


@pytest.fixture
def feed_with_keys():
    q = queue.Queue()
    return BitsoRequestDataSpot(q, api_key="test_key", api_secret="test_secret")


# ── sample API responses (Bitso envelope) ───────────────────────

SAMPLE_TICK = {
    "success": True,
    "payload": {
        "book": "btc_mxn",
        "last": "800000",
        "bid": "799000",
        "ask": "801000",
        "high": "810000",
        "low": "790000",
        "volume": "123.45",
    },
}

SAMPLE_DEPTH = {
    "success": True,
    "payload": {
        "bids": [{"book": "btc_mxn", "price": "799000", "amount": "0.5"}],
        "asks": [{"book": "btc_mxn", "price": "801000", "amount": "0.3"}],
    },
}

SAMPLE_KLINE = {
    "success": True,
    "payload": [
        {
            "bucket_start_time": "2024-01-01T00:00:00",
            "open": "800000",
            "close": "810000",
            "high": "815000",
            "low": "795000",
            "volume": "50.0",
        },
        {
            "bucket_start_time": "2024-01-01T01:00:00",
            "open": "810000",
            "close": "805000",
            "high": "812000",
            "low": "803000",
            "volume": "30.0",
        },
    ],
}

SAMPLE_TRADES = {
    "success": True,
    "payload": [
        {
            "tid": 1,
            "price": "800000",
            "amount": "0.01",
            "maker_side": "buy",
            "created_at": "2024-01-01T00:00:00",
        },
        {
            "tid": 2,
            "price": "801000",
            "amount": "0.02",
            "maker_side": "sell",
            "created_at": "2024-01-01T00:00:01",
        },
    ],
}

SAMPLE_SERVER_TIME = {
    "success": True,
    "payload": {"iso": "2024-01-01T00:00:00+00:00"},
}

SAMPLE_EXCHANGE_INFO = {
    "success": True,
    "payload": [
        {
            "book": "btc_mxn",
            "minimum_amount": "0.00001",
            "minimum_price": "10",
            "minimum_value": "10",
        },
    ],
}

SAMPLE_MAKE_ORDER = {
    "success": True,
    "payload": {"oid": "abc123"},
}

SAMPLE_ACCOUNT = {
    "success": True,
    "payload": {
        "balances": [
            {"currency": "btc", "available": "0.5", "locked": "0.1", "total": "0.6"},
            {"currency": "mxn", "available": "100000", "locked": "0", "total": "100000"},
        ],
    },
}

SAMPLE_OPEN_ORDERS = {
    "success": True,
    "payload": [
        {
            "oid": "o1",
            "book": "btc_mxn",
            "side": "buy",
            "type": "limit",
            "price": "790000",
            "original_amount": "0.01",
        },
    ],
}

SAMPLE_ERROR = {"success": False, "error": {"code": "0301", "message": "Unauthorized"}}


# ── TestExchangeData ────────────────────────────────────────────


class TestExchangeData:
    def test_exchange_name(self, exchange_data):
        assert exchange_data.exchange_name == "BITSO___SPOT"

    def test_asset_type(self, exchange_data):
        assert exchange_data.asset_type == "SPOT"

    def test_rest_url(self, exchange_data):
        assert "bitso.com" in exchange_data.rest_url

    def test_wss_url(self, exchange_data):
        assert "bitso.com" in exchange_data.wss_url

    def test_get_symbol_dash(self, exchange_data):
        assert exchange_data.get_symbol("BTC-MXN") == "btc_mxn"

    def test_get_symbol_slash(self, exchange_data):
        assert exchange_data.get_symbol("BTC/MXN") == "btc_mxn"

    def test_get_symbol_underscore(self, exchange_data):
        assert exchange_data.get_symbol("BTC_MXN") == "btc_mxn"

    def test_get_period(self, exchange_data):
        assert exchange_data.get_period("1m") == "60"
        assert exchange_data.get_period("1h") == "3600"
        assert exchange_data.get_period("1d") == "86400"

    def test_kline_periods(self, exchange_data):
        assert "1m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods

    def test_legal_currencies(self, exchange_data):
        assert "MXN" in exchange_data.legal_currency
        assert "USD" in exchange_data.legal_currency
        assert "USDC" in exchange_data.legal_currency

    def test_get_rest_path_tick(self, exchange_data):
        path = exchange_data.get_rest_path("get_tick")
        assert "ticker" in path.lower()

    def test_get_rest_path_depth(self, exchange_data):
        path = exchange_data.get_rest_path("get_depth")
        assert "order_book" in path.lower()

    def test_get_rest_path_missing_raises(self, exchange_data):
        with pytest.raises(ValueError):
            exchange_data.get_rest_path("nonexistent_path")

    def test_get_wss_path(self, exchange_data):
        if exchange_data.wss_paths:
            path = exchange_data.get_wss_path("ticker", "BTC-MXN")
            assert "btc_mxn" in path
        else:
            path = exchange_data.get_wss_path("ticker", "BTC-MXN")
            assert isinstance(path, str)


# ── TestParameterGeneration ─────────────────────────────────────


class TestParameterGeneration:
    def test_get_tick_params(self, feed):
        path, params, ed = feed._get_tick("BTC-MXN")
        assert "GET" in path
        assert params["book"] == "btc_mxn"
        assert ed["request_type"] == "get_tick"

    def test_get_depth_params(self, feed):
        path, params, ed = feed._get_depth("BTC-MXN", 50)
        assert "GET" in path
        assert params["book"] == "btc_mxn"
        assert ed["request_type"] == "get_depth"

    def test_get_kline_params(self, feed):
        path, params, ed = feed._get_kline("BTC-MXN", "1h", 100)
        assert "GET" in path
        assert params["book"] == "btc_mxn"
        assert params["time_bucket"] == "3600"
        assert ed["request_type"] == "get_kline"

    def test_get_trade_history_params(self, feed):
        path, params, ed = feed._get_trade_history("BTC-MXN", 50)
        assert "GET" in path
        assert params["book"] == "btc_mxn"
        assert ed["request_type"] == "get_trades"

    def test_get_server_time_params(self, feed):
        path, params, ed = feed._get_server_time()
        assert "GET" in path
        assert ed["request_type"] == "get_server_time"

    def test_get_exchange_info_params(self, feed):
        path, params, ed = feed._get_exchange_info()
        assert "GET" in path
        assert ed["request_type"] == "get_exchange_info"

    def test_make_order_params(self, feed):
        path, body, ed = feed._make_order("BTC-MXN", 0.001, 800000, "buy-limit")
        assert "POST" in path
        assert body["book"] == "btc_mxn"
        assert body["side"] == "buy"
        assert body["type"] == "limit"
        assert body["major"] == "0.001"
        assert body["price"] == "800000"
        assert ed["request_type"] == "make_order"

    def test_cancel_order_params(self, feed):
        path, params, ed = feed._cancel_order("BTC-MXN", "abc123")
        assert "DELETE" in path
        assert "abc123" in path  # oid appended to path

    def test_query_order_params(self, feed):
        path, params, ed = feed._query_order("BTC-MXN", "abc123")
        assert "GET" in path
        assert "abc123" in path

    def test_get_open_orders_params(self, feed):
        path, params, ed = feed._get_open_orders("BTC-MXN")
        assert "GET" in path
        assert params["book"] == "btc_mxn"

    def test_get_deals_params(self, feed):
        path, params, ed = feed._get_deals("BTC-MXN")
        assert "GET" in path
        assert params["book"] == "btc_mxn"

    def test_get_account_params(self, feed):
        path, params, ed = feed._get_account()
        assert "GET" in path
        assert ed["request_type"] == "get_account"

    def test_get_balance_params(self, feed):
        path, params, ed = feed._get_balance()
        assert "GET" in path
        assert ed["request_type"] == "get_balance"


# ── TestNormalization ───────────────────────────────────────────


class TestNormalization:
    def test_tick_ok(self):
        result, ok = BitsoRequestData._get_tick_normalize_function(SAMPLE_TICK, {})
        assert ok is True
        assert result[0]["book"] == "btc_mxn"

    def test_tick_error(self):
        result, ok = BitsoRequestData._get_tick_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_tick_empty(self):
        result, ok = BitsoRequestData._get_tick_normalize_function({}, {})
        assert ok is False

    def test_tick_none(self):
        result, ok = BitsoRequestData._get_tick_normalize_function(None, {})
        assert ok is False

    def test_depth_ok(self):
        result, ok = BitsoRequestData._get_depth_normalize_function(SAMPLE_DEPTH, {})
        assert ok is True
        assert "bids" in result[0]
        assert "asks" in result[0]

    def test_depth_error(self):
        result, ok = BitsoRequestData._get_depth_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_kline_ok(self):
        result, ok = BitsoRequestData._get_kline_normalize_function(SAMPLE_KLINE, {})
        assert ok is True
        assert len(result) == 2

    def test_kline_empty(self):
        result, ok = BitsoRequestData._get_kline_normalize_function(
            {"success": True, "payload": []}, {}
        )
        assert ok is False

    def test_trades_ok(self):
        result, ok = BitsoRequestData._get_trade_history_normalize_function(SAMPLE_TRADES, {})
        assert ok is True
        assert len(result) == 2

    def test_server_time_ok(self):
        result, ok = BitsoRequestData._get_server_time_normalize_function(SAMPLE_SERVER_TIME, {})
        assert ok is True
        assert "server_time" in result[0]

    def test_exchange_info_ok(self):
        result, ok = BitsoRequestData._get_exchange_info_normalize_function(
            SAMPLE_EXCHANGE_INFO, {}
        )
        assert ok is True
        assert "books" in result[0]

    def test_make_order_ok(self):
        result, ok = BitsoRequestData._make_order_normalize_function(SAMPLE_MAKE_ORDER, {})
        assert ok is True
        assert result[0]["oid"] == "abc123"

    def test_make_order_error(self):
        result, ok = BitsoRequestData._make_order_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_cancel_order_ok(self):
        result, ok = BitsoRequestData._cancel_order_normalize_function(
            {"success": True, "payload": ["abc123"]}, {}
        )
        assert ok is True

    def test_cancel_order_error(self):
        result, ok = BitsoRequestData._cancel_order_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_query_order_ok(self):
        result, ok = BitsoRequestData._query_order_normalize_function(
            {"success": True, "payload": [{"oid": "abc123", "status": "complete"}]}, {}
        )
        assert ok is True

    def test_open_orders_ok(self):
        result, ok = BitsoRequestData._get_open_orders_normalize_function(SAMPLE_OPEN_ORDERS, {})
        assert ok is True
        assert len(result) == 1

    def test_open_orders_empty(self):
        result, ok = BitsoRequestData._get_open_orders_normalize_function(
            {"success": True, "payload": []}, {}
        )
        assert ok is True
        assert result == []

    def test_deals_ok(self):
        result, ok = BitsoRequestData._get_deals_normalize_function(
            {"success": True, "payload": [{"tid": 1, "price": "800000"}]}, {}
        )
        assert ok is True

    def test_account_ok(self):
        result, ok = BitsoRequestData._get_account_normalize_function(SAMPLE_ACCOUNT, {})
        assert ok is True
        assert len(result) == 2

    def test_balance_ok(self):
        result, ok = BitsoRequestData._get_balance_normalize_function(SAMPLE_ACCOUNT, {})
        assert ok is True
        assert len(result) == 2

    def test_balance_error(self):
        result, ok = BitsoRequestData._get_balance_normalize_function(SAMPLE_ERROR, {})
        assert ok is False


# ── TestSyncCalls (mocked) ──────────────────────────────────────


class TestSyncCalls:
    def _mock_feed(self, mock_response):
        q = queue.Queue()
        feed = BitsoRequestDataSpot(q)
        feed.http_request = MagicMock(return_value=mock_response)
        return feed

    def test_get_tick(self):
        feed = self._mock_feed(SAMPLE_TICK)
        rd = feed.get_tick("BTC-MXN")
        assert isinstance(rd, RequestData)
        feed.http_request.assert_called_once()

    def test_get_depth(self):
        feed = self._mock_feed(SAMPLE_DEPTH)
        rd = feed.get_depth("BTC-MXN", 20)
        assert isinstance(rd, RequestData)

    def test_get_kline(self):
        feed = self._mock_feed(SAMPLE_KLINE)
        rd = feed.get_kline("BTC-MXN", "1h", 50)
        assert isinstance(rd, RequestData)

    def test_get_trade_history(self):
        feed = self._mock_feed(SAMPLE_TRADES)
        rd = feed.get_trade_history("BTC-MXN")
        assert isinstance(rd, RequestData)

    def test_get_server_time(self):
        feed = self._mock_feed(SAMPLE_SERVER_TIME)
        rd = feed.get_server_time()
        assert isinstance(rd, RequestData)

    def test_get_exchange_info(self):
        feed = self._mock_feed(SAMPLE_EXCHANGE_INFO)
        rd = feed.get_exchange_info()
        assert isinstance(rd, RequestData)

    def test_get_account(self):
        feed = self._mock_feed(SAMPLE_ACCOUNT)
        rd = feed.get_account()
        assert isinstance(rd, RequestData)

    def test_get_open_orders(self):
        feed = self._mock_feed(SAMPLE_OPEN_ORDERS)
        rd = feed.get_open_orders("BTC-MXN")
        assert isinstance(rd, RequestData)

    def test_make_order(self):
        feed = self._mock_feed(SAMPLE_MAKE_ORDER)
        rd = feed.make_order("BTC-MXN", 0.001, 800000, "buy-limit")
        assert isinstance(rd, RequestData)

    def test_cancel_order(self):
        feed = self._mock_feed({"success": True, "payload": ["abc123"]})
        rd = feed.cancel_order("BTC-MXN", "abc123")
        assert isinstance(rd, RequestData)

    def test_query_order(self):
        feed = self._mock_feed({"success": True, "payload": {"oid": "abc123"}})
        rd = feed.query_order("BTC-MXN", "abc123")
        assert isinstance(rd, RequestData)


# ── TestAuth ────────────────────────────────────────────────────


class TestAuth:
    def test_signature_generation(self, feed_with_keys):
        sig = feed_with_keys._generate_signature("1234567890", "GET", "/api/v3/balance")
        assert isinstance(sig, str)
        assert len(sig) == 64  # SHA256 hex

    def test_signature_empty_without_secret(self, feed):
        sig = feed._generate_signature("123", "GET", "/api/v3/balance")
        assert sig == ""

    def test_api_key_property(self, feed_with_keys):
        assert feed_with_keys.api_key == "test_key"
        assert feed_with_keys.api_secret == "test_secret"


# ── TestRegistry ────────────────────────────────────────────────


class TestRegistry:
    def test_bitso_spot_registered(self):
        assert "BITSO___SPOT" in ExchangeRegistry._feed_classes
        assert ExchangeRegistry._feed_classes["BITSO___SPOT"] == BitsoRequestDataSpot

    def test_exchange_data_registered(self):
        assert "BITSO___SPOT" in ExchangeRegistry._exchange_data_classes
        assert ExchangeRegistry._exchange_data_classes["BITSO___SPOT"] == BitsoExchangeDataSpot

    def test_balance_handler_registered(self):
        assert "BITSO___SPOT" in ExchangeRegistry._balance_handlers
        assert ExchangeRegistry._balance_handlers["BITSO___SPOT"] is not None

    def test_create_exchange_data(self):
        ed = ExchangeRegistry.create_exchange_data("BITSO___SPOT")
        assert isinstance(ed, BitsoExchangeDataSpot)


# ── TestMethodExistence ─────────────────────────────────────────


class TestMethodExistence:
    @pytest.mark.parametrize(
        "method_name",
        [
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
            "get_deals",
            "async_get_deals",
            "get_account",
            "async_get_account",
            "get_balance",
            "async_get_balance",
            "get_server_time",
            "async_get_server_time",
            "get_exchange_info",
            "async_get_exchange_info",
        ],
    )
    def test_method_exists(self, feed, method_name):
        assert hasattr(feed, method_name)
        assert callable(getattr(feed, method_name))


# ── TestFeedInit ────────────────────────────────────────────────


class TestFeedInit:
    def test_default_exchange_name(self, feed):
        assert feed.exchange_name == "BITSO___SPOT"

    def test_default_asset_type(self, feed):
        assert feed.asset_type == "SPOT"

    def test_api_keys(self, feed_with_keys):
        assert feed_with_keys.api_key == "test_key"
        assert feed_with_keys.api_secret == "test_secret"

    def test_capabilities(self):
        caps = BitsoRequestDataSpot._capabilities()
        assert Capability.GET_TICK in caps
        assert Capability.GET_DEPTH in caps
        assert Capability.GET_KLINE in caps
        assert Capability.GET_EXCHANGE_INFO in caps
        assert Capability.MAKE_ORDER in caps
        assert Capability.CANCEL_ORDER in caps
        assert Capability.GET_ACCOUNT in caps
        assert Capability.GET_BALANCE in caps

    def test_push_data_to_queue(self, feed):
        feed.push_data_to_queue({"test": True})
        assert not feed.data_queue.empty()
        assert feed.data_queue.get()["test"] is True


# ── TestIntegration (skipped) ───────────────────────────────────


class TestIntegration:
    @pytest.mark.skip(reason="Requires network access")
    def test_live_get_tick(self):
        q = queue.Queue()
        feed = BitsoRequestDataSpot(q)
        rd = feed.get_tick("BTC-MXN")
        assert isinstance(rd, RequestData)

    @pytest.mark.skip(reason="Requires network access")
    def test_live_get_depth(self):
        q = queue.Queue()
        feed = BitsoRequestDataSpot(q)
        rd = feed.get_depth("BTC-MXN", 10)
        assert isinstance(rd, RequestData)

    @pytest.mark.skip(reason="Requires network access")
    def test_live_get_kline(self):
        q = queue.Queue()
        feed = BitsoRequestDataSpot(q)
        rd = feed.get_kline("BTC-MXN", "1h", 5)
        assert isinstance(rd, RequestData)

    @pytest.mark.skip(reason="Requires network access")
    def test_live_get_exchange_info(self):
        q = queue.Queue()
        feed = BitsoRequestDataSpot(q)
        rd = feed.get_exchange_info()
        assert isinstance(rd, RequestData)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
