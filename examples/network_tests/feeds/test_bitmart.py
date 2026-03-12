"""
Test BitMart exchange integration.

Run tests:
    pytest tests/feeds/test_bitmart.py -v
"""

import queue
from unittest.mock import patch

import pytest

import bt_api_py.exchange_registers.register_bitmart  # noqa: F401
from bt_api_py.containers.exchanges.bitmart_exchange_data import (
    BitmartExchangeDataSpot,
)
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_bitmart.request_base import BitmartRequestData
from bt_api_py.feeds.live_bitmart.spot import BitmartRequestDataSpot
from bt_api_py.registry import ExchangeRegistry

# ── fixtures ────────────────────────────────────────────────────


@pytest.fixture
def exchange_data():
    return BitmartExchangeDataSpot()


@pytest.fixture
def feed():
    q = queue.Queue()
    return BitmartRequestDataSpot(q)


@pytest.fixture
def feed_with_keys():
    q = queue.Queue()
    return BitmartRequestDataSpot(q, api_key="test_key", api_secret="test_secret", memo="test_memo")


# ── sample API responses ────────────────────────────────────────

SAMPLE_TICK = {
    "code": 1000,
    "message": "OK",
    "data": {
        "symbol": "BTC_USDT",
        "last_price": "50000",
        "bid_1": "49999",
        "ask_1": "50001",
        "high_24h": "51000",
        "low_24h": "49000",
        "volume_24h": "1234.56",
    },
}

SAMPLE_DEPTH = {
    "code": 1000,
    "message": "OK",
    "data": {
        "symbol": "BTC_USDT",
        "timestamp": 1700000000,
        "buys": [["49999", "0.5"], ["49998", "1.0"]],
        "sells": [["50001", "0.3"], ["50002", "0.8"]],
    },
}

SAMPLE_KLINE = {
    "code": 1000,
    "message": "OK",
    "data": {
        "symbol": "BTC_USDT",
        "klines": [
            ["50000", "51000", "49000", "50500", "123.45", 1700000000],
            ["50500", "52000", "50000", "51000", "200.00", 1700003600],
        ],
    },
}

SAMPLE_TRADES = {
    "code": 1000,
    "message": "OK",
    "data": {
        "symbol": "BTC_USDT",
        "trades": [
            {"price": "50000", "amount": "0.1", "time": 1700000000},
            {"price": "50001", "amount": "0.2", "time": 1700000001},
        ],
    },
}

SAMPLE_SERVER_TIME = {"code": 1000, "message": "OK", "data": {"server_time": 1700000000}}

SAMPLE_EXCHANGE_INFO = {
    "code": 1000,
    "message": "OK",
    "data": {"currencies": [{"id": "BTC", "name": "Bitcoin"}]},
}

SAMPLE_MAKE_ORDER = {
    "code": 1000,
    "message": "OK",
    "data": {"order_id": "12345678"},
}

SAMPLE_CANCEL_ORDER = {"code": 1000, "message": "OK", "data": True}

SAMPLE_QUERY_ORDER = {
    "code": 1000,
    "message": "OK",
    "data": {"order_id": "12345678", "state": "filled", "filled_size": "0.001"},
}

SAMPLE_OPEN_ORDERS = {
    "code": 1000,
    "message": "OK",
    "data": {"orders": [{"order_id": "111"}, {"order_id": "222"}]},
}

SAMPLE_BALANCE = {
    "code": 1000,
    "message": "OK",
    "data": {"wallet": [{"id": "BTC", "available": "1.0", "frozen": "0.0"}]},
}

SAMPLE_ERROR = {"code": 40001, "message": "Invalid parameter"}


# ── TestExchangeData ────────────────────────────────────────────


class TestExchangeData:
    def test_exchange_name(self, exchange_data):
        assert exchange_data.exchange_name == "BITMART___SPOT"

    def test_asset_type(self, exchange_data):
        assert exchange_data.asset_type == "SPOT"

    def test_rest_url(self, exchange_data):
        assert "api-cloud.bitmart.com" in exchange_data.rest_url

    def test_wss_url(self, exchange_data):
        assert "ws-manager-compress.bitmart.com" in exchange_data.wss_url

    def test_get_symbol_slash(self, exchange_data):
        assert exchange_data.get_symbol("BTC/USDT") == "BTC_USDT"

    def test_get_symbol_dash(self, exchange_data):
        assert exchange_data.get_symbol("BTC-USDT") == "BTC_USDT"

    def test_get_symbol_underscore(self, exchange_data):
        assert exchange_data.get_symbol("BTC_USDT") == "BTC_USDT"

    def test_get_period(self, exchange_data):
        assert exchange_data.get_period("1m") == "1"
        assert exchange_data.get_period("1h") == "60"
        assert exchange_data.get_period("1d") == "1440"

    @pytest.mark.kline
    def test_kline_periods(self, exchange_data):
        for k in ("1m", "5m", "15m", "1h", "4h", "1d"):
            assert k in exchange_data.kline_periods

    def test_legal_currencies(self, exchange_data):
        assert "USDT" in exchange_data.legal_currency

    @pytest.mark.ticker
    def test_get_rest_path_tick(self, exchange_data):
        path = exchange_data.get_rest_path("get_tick")
        assert "ticker" in path.lower()

    @pytest.mark.orderbook
    def test_get_rest_path_depth(self, exchange_data):
        path = exchange_data.get_rest_path("get_depth")
        assert "books" in path.lower()

    def test_get_rest_path_missing_raises(self, exchange_data):
        with pytest.raises(ValueError):
            exchange_data.get_rest_path("nonexistent_path")

    def test_get_wss_path(self, exchange_data):
        # wss_paths may be loaded from YAML; if present, verify substitution
        if exchange_data.wss_paths:
            path = exchange_data.get_wss_path("ticker", "BTC/USDT")
            assert "BTC_USDT" in path
        else:
            # fallback: get_wss_path returns empty string for missing key
            path = exchange_data.get_wss_path("ticker", "BTC/USDT")
            assert isinstance(path, str)


# ── TestParameterGeneration ─────────────────────────────────────


class TestParameterGeneration:
    @pytest.mark.ticker
    def test_get_tick_params(self, feed):
        path, params, ed = feed._get_tick("BTC/USDT")
        assert "GET" in path
        assert params["symbol"] == "BTC_USDT"
        assert ed["request_type"] == "get_tick"
        assert callable(ed["normalize_function"])

    @pytest.mark.orderbook
    def test_get_depth_params(self, feed):
        path, params, ed = feed._get_depth("BTC/USDT", 20)
        assert params["symbol"] == "BTC_USDT"
        assert params["limit"] == 20

    @pytest.mark.orderbook
    def test_get_depth_max_limit(self, feed):
        _, params, _ = feed._get_depth("BTC/USDT", 100)
        assert params["limit"] == 50

    @pytest.mark.kline
    def test_get_kline_params(self, feed):
        path, params, ed = feed._get_kline("BTC/USDT", "1h", 50)
        assert params["symbol"] == "BTC_USDT"
        assert params["step"] == "60"
        assert params["limit"] == 50
        assert ed["period"] == "1h"

    def test_get_trade_history_params(self, feed):
        path, params, ed = feed._get_trade_history("ETH/USDT", 30)
        assert params["symbol"] == "ETH_USDT"
        assert params["limit"] == 30

    def test_make_order_params(self, feed):
        path, body, ed = feed._make_order("BTC/USDT", 0.001, 50000, "buy-limit")
        assert "POST" in path
        assert body["symbol"] == "BTC_USDT"
        assert body["side"] == "buy"
        assert body["type"] == "limit"
        assert body["size"] == "0.001"
        assert body["price"] == "50000"

    def test_cancel_order_params(self, feed):
        path, body, ed = feed._cancel_order("BTC/USDT", "12345")
        assert "POST" in path
        assert body["symbol"] == "BTC_USDT"
        assert body["order_id"] == "12345"

    def test_query_order_params(self, feed):
        path, body, ed = feed._query_order("BTC/USDT", "12345")
        assert body["symbol"] == "BTC_USDT"
        assert body["orderId"] == "12345"

    def test_get_open_orders_params(self, feed):
        path, body, ed = feed._get_open_orders("BTC/USDT")
        assert body["symbol"] == "BTC_USDT"
        assert body["limit"] == 50

    def test_get_deals_params(self, feed):
        path, body, ed = feed._get_deals("BTC/USDT")
        assert body["symbol"] == "BTC_USDT"

    def test_get_server_time_params(self, feed):
        path, params, ed = feed._get_server_time()
        assert "GET" in path
        assert ed["request_type"] == "get_server_time"

    def test_get_exchange_info_params(self, feed):
        path, params, ed = feed._get_exchange_info()
        assert "GET" in path
        assert ed["request_type"] == "get_exchange_info"


# ── TestNormalization ───────────────────────────────────────────


class TestNormalization:
    @pytest.mark.ticker
    def test_tick_ok(self):
        result, ok = BitmartRequestData._get_tick_normalize_function(SAMPLE_TICK, {})
        assert ok is True
        assert result[0]["symbol"] == "BTC_USDT"

    @pytest.mark.ticker
    def test_tick_error(self):
        result, ok = BitmartRequestData._get_tick_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    @pytest.mark.ticker
    def test_tick_empty(self):
        result, ok = BitmartRequestData._get_tick_normalize_function({}, {})
        assert ok is False  # {} has no 'data' key, unwrap returns {}, which is falsy

    @pytest.mark.ticker
    def test_tick_none(self):
        result, ok = BitmartRequestData._get_tick_normalize_function(None, {})
        assert ok is False

    @pytest.mark.orderbook
    def test_depth_ok(self):
        result, ok = BitmartRequestData._get_depth_normalize_function(SAMPLE_DEPTH, {})
        assert ok is True
        assert "buys" in result[0]
        assert "sells" in result[0]

    @pytest.mark.kline
    def test_kline_ok(self):
        result, ok = BitmartRequestData._get_kline_normalize_function(SAMPLE_KLINE, {})
        assert ok is True
        assert len(result) == 2

    @pytest.mark.kline
    def test_kline_empty(self):
        empty = {"code": 1000, "message": "OK", "data": {"klines": []}}
        result, ok = BitmartRequestData._get_kline_normalize_function(empty, {})
        assert ok is False

    def test_trades_ok(self):
        result, ok = BitmartRequestData._get_trade_history_normalize_function(SAMPLE_TRADES, {})
        assert ok is True
        assert len(result) == 2

    def test_trades_empty(self):
        empty = {"code": 1000, "message": "OK", "data": {"trades": []}}
        result, ok = BitmartRequestData._get_trade_history_normalize_function(empty, {})
        assert ok is False

    def test_server_time_ok(self):
        result, ok = BitmartRequestData._get_server_time_normalize_function(SAMPLE_SERVER_TIME, {})
        assert ok is True

    def test_exchange_info_ok(self):
        result, ok = BitmartRequestData._get_exchange_info_normalize_function(
            SAMPLE_EXCHANGE_INFO, {}
        )
        assert ok is True
        assert "currencies" in result[0]

    def test_make_order_ok(self):
        result, ok = BitmartRequestData._make_order_normalize_function(SAMPLE_MAKE_ORDER, {})
        assert ok is True
        assert result[0]["order_id"] == "12345678"

    def test_cancel_order_ok(self):
        result, ok = BitmartRequestData._cancel_order_normalize_function(SAMPLE_CANCEL_ORDER, {})
        assert ok is True

    def test_cancel_order_error(self):
        result, ok = BitmartRequestData._cancel_order_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_query_order_ok(self):
        result, ok = BitmartRequestData._query_order_normalize_function(SAMPLE_QUERY_ORDER, {})
        assert ok is True
        assert result[0]["state"] == "filled"

    def test_open_orders_ok(self):
        result, ok = BitmartRequestData._get_open_orders_normalize_function(SAMPLE_OPEN_ORDERS, {})
        assert ok is True
        assert len(result) == 2

    def test_balance_ok(self):
        result, ok = BitmartRequestData._get_balance_normalize_function(SAMPLE_BALANCE, {})
        assert ok is True
        assert result[0]["id"] == "BTC"

    def test_account_ok(self):
        result, ok = BitmartRequestData._get_account_normalize_function(SAMPLE_BALANCE, {})
        assert ok is True


# ── TestSyncCalls (mocked) ──────────────────────────────────────


class TestSyncCalls:
    @patch.object(BitmartRequestDataSpot, "http_request", return_value=SAMPLE_TICK)
    @pytest.mark.ticker
    def test_get_tick(self, mock_req, feed):
        rd = feed.get_tick("BTC/USDT")
        assert isinstance(rd, RequestData)
        mock_req.assert_called_once()

    @patch.object(BitmartRequestDataSpot, "http_request", return_value=SAMPLE_DEPTH)
    @pytest.mark.orderbook
    def test_get_depth(self, mock_req, feed):
        rd = feed.get_depth("BTC/USDT", 20)
        assert isinstance(rd, RequestData)

    @patch.object(BitmartRequestDataSpot, "http_request", return_value=SAMPLE_KLINE)
    @pytest.mark.kline
    def test_get_kline(self, mock_req, feed):
        rd = feed.get_kline("BTC/USDT", "1h", 50)
        assert isinstance(rd, RequestData)

    @patch.object(BitmartRequestDataSpot, "http_request", return_value=SAMPLE_TRADES)
    def test_get_trade_history(self, mock_req, feed):
        rd = feed.get_trade_history("BTC/USDT")
        assert isinstance(rd, RequestData)

    @patch.object(BitmartRequestDataSpot, "http_request", return_value=SAMPLE_SERVER_TIME)
    def test_get_server_time(self, mock_req, feed):
        rd = feed.get_server_time()
        assert isinstance(rd, RequestData)

    @patch.object(BitmartRequestDataSpot, "http_request", return_value=SAMPLE_EXCHANGE_INFO)
    def test_get_exchange_info(self, mock_req, feed):
        rd = feed.get_exchange_info()
        assert isinstance(rd, RequestData)

    @patch.object(BitmartRequestDataSpot, "http_request", return_value=SAMPLE_BALANCE)
    def test_get_balance(self, mock_req, feed_with_keys):
        rd = feed_with_keys.get_balance()
        assert isinstance(rd, RequestData)

    @patch.object(BitmartRequestDataSpot, "http_request", return_value=SAMPLE_BALANCE)
    def test_get_account(self, mock_req, feed_with_keys):
        rd = feed_with_keys.get_account()
        assert isinstance(rd, RequestData)

    @patch.object(BitmartRequestDataSpot, "http_request", return_value=SAMPLE_OPEN_ORDERS)
    def test_get_open_orders(self, mock_req, feed_with_keys):
        rd = feed_with_keys.get_open_orders("BTC/USDT")
        assert isinstance(rd, RequestData)

    @patch.object(BitmartRequestDataSpot, "http_request", return_value=SAMPLE_MAKE_ORDER)
    def test_make_order(self, mock_req, feed_with_keys):
        rd = feed_with_keys.make_order("BTC/USDT", 0.001, 50000, "buy-limit")
        assert isinstance(rd, RequestData)

    @patch.object(BitmartRequestDataSpot, "http_request", return_value=SAMPLE_CANCEL_ORDER)
    def test_cancel_order(self, mock_req, feed_with_keys):
        rd = feed_with_keys.cancel_order("BTC/USDT", "12345")
        assert isinstance(rd, RequestData)

    @patch.object(BitmartRequestDataSpot, "http_request", return_value=SAMPLE_QUERY_ORDER)
    def test_query_order(self, mock_req, feed_with_keys):
        rd = feed_with_keys.query_order("BTC/USDT", "12345")
        assert isinstance(rd, RequestData)


# ── TestAuth ────────────────────────────────────────────────────


class TestAuth:
    def test_signature_generation(self, feed_with_keys):
        sig = feed_with_keys._generate_signature("1700000000000", "body_str")
        assert isinstance(sig, str)
        assert len(sig) == 64  # hex sha256

    def test_signature_empty_without_secret(self, feed):
        sig = feed._generate_signature("1700000000000", "body")
        assert sig == ""

    def test_api_key_property(self, feed_with_keys):
        assert feed_with_keys.api_key == "test_key"
        assert feed_with_keys.api_secret == "test_secret"
        assert feed_with_keys.api_memo == "test_memo"


# ── TestRegistry ────────────────────────────────────────────────


class TestRegistry:
    def test_bitmart_spot_registered(self):
        assert "BITMART___SPOT" in ExchangeRegistry._feed_classes
        assert ExchangeRegistry._feed_classes["BITMART___SPOT"] is BitmartRequestDataSpot

    def test_exchange_data_registered(self):
        assert "BITMART___SPOT" in ExchangeRegistry._exchange_data_classes
        assert ExchangeRegistry._exchange_data_classes["BITMART___SPOT"] is BitmartExchangeDataSpot

    def test_balance_handler_registered(self):
        assert "BITMART___SPOT" in ExchangeRegistry._balance_handlers

    def test_create_exchange_data(self):
        ed = ExchangeRegistry.create_exchange_data("BITMART___SPOT")
        assert isinstance(ed, BitmartExchangeDataSpot)


# ── TestMethodExistence ─────────────────────────────────────────

_REQUIRED_METHODS = [
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
]


class TestMethodExistence:
    @pytest.mark.parametrize("method_name", _REQUIRED_METHODS)
    def test_method_exists(self, method_name, feed):
        assert hasattr(feed, method_name), f"Missing method: {method_name}"
        assert callable(getattr(feed, method_name))


# ── TestFeedInit ────────────────────────────────────────────────


class TestFeedInit:
    def test_default_exchange_name(self, feed):
        assert feed.exchange_name == "BITMART___SPOT"

    def test_default_asset_type(self, feed):
        assert feed.asset_type == "SPOT"

    def test_api_keys(self, feed_with_keys):
        assert feed_with_keys.api_key == "test_key"
        assert feed_with_keys.api_secret == "test_secret"
        assert feed_with_keys.api_memo == "test_memo"

    def test_capabilities(self):
        caps = BitmartRequestDataSpot._capabilities()
        assert Capability.GET_TICK in caps
        assert Capability.GET_DEPTH in caps
        assert Capability.GET_KLINE in caps
        assert Capability.MAKE_ORDER in caps
        assert Capability.CANCEL_ORDER in caps

    def test_push_data_to_queue(self, feed):
        feed.push_data_to_queue({"test": 1})
        assert not feed.data_queue.empty()
        assert feed.data_queue.get() == {"test": 1}


# ── TestIntegration (skipped by default) ────────────────────────


class TestIntegration:
    @pytest.mark.skip(reason="requires network")
    @pytest.mark.ticker
    def test_live_get_tick(self):
        q = queue.Queue()
        f = BitmartRequestDataSpot(q)
        rd = f.get_tick("BTC_USDT")
        assert isinstance(rd, RequestData)

    @pytest.mark.skip(reason="requires network")
    @pytest.mark.orderbook
    def test_live_get_depth(self):
        q = queue.Queue()
        f = BitmartRequestDataSpot(q)
        rd = f.get_depth("BTC_USDT")
        assert isinstance(rd, RequestData)

    @pytest.mark.skip(reason="requires network")
    @pytest.mark.kline
    def test_live_get_kline(self):
        q = queue.Queue()
        f = BitmartRequestDataSpot(q)
        rd = f.get_kline("BTC_USDT", "1h", 5)
        assert isinstance(rd, RequestData)

    @pytest.mark.skip(reason="requires network")
    def test_live_get_exchange_info(self):
        q = queue.Queue()
        f = BitmartRequestDataSpot(q)
        rd = f.get_exchange_info()
        assert isinstance(rd, RequestData)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
