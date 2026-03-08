"""
Test Bitrue exchange integration.

Run tests:
    pytest tests/feeds/test_bitrue.py -v
"""

import queue
from unittest.mock import MagicMock

import pytest

import bt_api_py.exchange_registers.register_bitrue  # noqa: F401
from bt_api_py.containers.exchanges.bitrue_exchange_data import (
    BitrueExchangeDataSpot,
)
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_bitrue.request_base import BitrueRequestData
from bt_api_py.feeds.live_bitrue.spot import BitrueRequestDataSpot
from bt_api_py.registry import ExchangeRegistry

# ── fixtures ────────────────────────────────────────────────────


@pytest.fixture
def exchange_data():
    return BitrueExchangeDataSpot()


@pytest.fixture
def feed():
    q = queue.Queue()
    return BitrueRequestDataSpot(q)


@pytest.fixture
def feed_with_keys():
    q = queue.Queue()
    return BitrueRequestDataSpot(q, api_key="test_key", api_secret="test_secret")


# ── sample API responses ────────────────────────────────────────

SAMPLE_TICK = {
    "symbol": "BTCUSDT",
    "lastPrice": "50000.00",
    "bidPrice": "49999.00",
    "askPrice": "50001.00",
    "highPrice": "51000.00",
    "lowPrice": "49000.00",
    "volume": "1234.56",
    "priceChangePercent": "2.5",
}

SAMPLE_DEPTH = {
    "bids": [["49999", "0.5"], ["49998", "1.0"]],
    "asks": [["50001", "0.3"], ["50002", "0.8"]],
}

SAMPLE_KLINE = [
    [1700000000000, "50000", "51000", "49000", "50500", "123.45"],
    [1700003600000, "50500", "52000", "50000", "51000", "200.00"],
]

SAMPLE_TRADES = [
    {"price": "50000", "qty": "0.1", "time": 1700000000},
    {"price": "50001", "qty": "0.2", "time": 1700000001},
]

SAMPLE_SERVER_TIME = {"serverTime": 1700000000000}

SAMPLE_EXCHANGE_INFO = {
    "symbols": [
        {"symbol": "BTCUSDT", "baseAsset": "BTC", "quoteAsset": "USDT", "status": "TRADING"},
    ],
}

SAMPLE_MAKE_ORDER = {
    "orderId": 12345678,
    "symbol": "BTCUSDT",
    "status": "NEW",
}

SAMPLE_ACCOUNT = {
    "balances": [
        {"asset": "BTC", "free": "0.5", "locked": "0.1"},
        {"asset": "USDT", "free": "10000", "locked": "0"},
    ],
}

SAMPLE_OPEN_ORDERS = [
    {"orderId": 111, "symbol": "BTCUSDT", "side": "BUY", "price": "40000", "origQty": "0.01"},
]

SAMPLE_ERROR = {"code": -1002, "msg": "Unauthorized"}


# ── TestExchangeData ────────────────────────────────────────────


class TestExchangeData:
    def test_exchange_name(self, exchange_data):
        assert exchange_data.exchange_name == "BITRUE___SPOT"

    def test_asset_type(self, exchange_data):
        assert exchange_data.asset_type == "SPOT"

    def test_rest_url(self, exchange_data):
        assert "bitrue.com" in exchange_data.rest_url

    def test_wss_url(self, exchange_data):
        assert "bitrue.com" in exchange_data.wss_url

    def test_get_symbol_slash(self, exchange_data):
        assert exchange_data.get_symbol("BTC/USDT") == "BTCUSDT"

    def test_get_symbol_dash(self, exchange_data):
        assert exchange_data.get_symbol("BTC-USDT") == "BTCUSDT"

    def test_get_symbol_underscore(self, exchange_data):
        assert exchange_data.get_symbol("BTC_USDT") == "BTCUSDT"

    def test_get_symbol_concat(self, exchange_data):
        assert exchange_data.get_symbol("BTCUSDT") == "BTCUSDT"

    def test_get_period(self, exchange_data):
        assert exchange_data.get_period("1h") == "1h"
        assert exchange_data.get_period("1d") == "1d"

    def test_kline_periods(self, exchange_data):
        assert "1m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods

    def test_legal_currencies(self, exchange_data):
        assert "USDT" in exchange_data.legal_currency
        assert "BTC" in exchange_data.legal_currency

    def test_get_rest_path_tick(self, exchange_data):
        path = exchange_data.get_rest_path("get_tick")
        assert "ticker" in path.lower()

    def test_get_rest_path_depth(self, exchange_data):
        path = exchange_data.get_rest_path("get_depth")
        assert "depth" in path.lower()

    def test_get_rest_path_missing_raises(self, exchange_data):
        with pytest.raises(ValueError):
            exchange_data.get_rest_path("nonexistent_path")

    def test_get_wss_path(self, exchange_data):
        if exchange_data.wss_paths:
            path = exchange_data.get_wss_path("ticker", "BTC/USDT")
            assert "btcusdt" in path
        else:
            path = exchange_data.get_wss_path("ticker", "BTC/USDT")
            assert isinstance(path, str)


# ── TestParameterGeneration ─────────────────────────────────────


class TestParameterGeneration:
    def test_get_tick_params(self, feed):
        path, params, ed = feed._get_tick("BTC/USDT")
        assert "GET" in path
        assert params["symbol"] == "BTCUSDT"
        assert ed["request_type"] == "get_tick"

    def test_get_depth_params(self, feed):
        path, params, ed = feed._get_depth("BTC/USDT", 50)
        assert "GET" in path
        assert params["symbol"] == "BTCUSDT"
        assert params["limit"] == 50
        assert ed["request_type"] == "get_depth"

    def test_get_kline_params(self, feed):
        path, params, ed = feed._get_kline("BTC/USDT", "1h", 100)
        assert "GET" in path
        assert params["symbol"] == "BTCUSDT"
        assert params["scale"] == "1h"
        assert params["limit"] == 100
        assert ed["request_type"] == "get_kline"

    def test_get_trade_history_params(self, feed):
        path, params, ed = feed._get_trade_history("BTC/USDT", 50)
        assert "GET" in path
        assert params["symbol"] == "BTCUSDT"
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
        path, params, ed = feed._make_order("BTC/USDT", 0.01, 50000, "buy-limit")
        assert "POST" in path
        assert params["symbol"] == "BTCUSDT"
        assert params["side"] == "BUY"
        assert params["type"] == "LIMIT"
        assert params["quantity"] == "0.01"
        assert params["price"] == "50000"
        assert ed["request_type"] == "make_order"

    def test_cancel_order_params(self, feed):
        path, params, ed = feed._cancel_order("BTC/USDT", "12345")
        assert "DELETE" in path
        assert params["symbol"] == "BTCUSDT"
        assert params["orderId"] == "12345"

    def test_query_order_params(self, feed):
        path, params, ed = feed._query_order("BTC/USDT", "12345")
        assert "GET" in path
        assert params["symbol"] == "BTCUSDT"
        assert params["orderId"] == "12345"

    def test_get_open_orders_params(self, feed):
        path, params, ed = feed._get_open_orders("BTC/USDT")
        assert "GET" in path
        assert params["symbol"] == "BTCUSDT"

    def test_get_deals_params(self, feed):
        path, params, ed = feed._get_deals("BTC/USDT")
        assert "GET" in path
        assert params["symbol"] == "BTCUSDT"

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
        result, ok = BitrueRequestData._get_tick_normalize_function(SAMPLE_TICK, {})
        assert ok is True
        assert result[0]["symbol"] == "BTCUSDT"

    def test_tick_error(self):
        result, ok = BitrueRequestData._get_tick_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_tick_empty(self):
        result, ok = BitrueRequestData._get_tick_normalize_function({}, {})
        assert ok is False  # empty dict has no data and is_error returns True

    def test_tick_none(self):
        result, ok = BitrueRequestData._get_tick_normalize_function(None, {})
        assert ok is False

    def test_depth_ok(self):
        result, ok = BitrueRequestData._get_depth_normalize_function(SAMPLE_DEPTH, {})
        assert ok is True
        assert "bids" in result[0]
        assert "asks" in result[0]

    def test_depth_error(self):
        result, ok = BitrueRequestData._get_depth_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_kline_ok(self):
        result, ok = BitrueRequestData._get_kline_normalize_function(SAMPLE_KLINE, {})
        assert ok is True
        assert len(result) == 2

    def test_kline_empty(self):
        result, ok = BitrueRequestData._get_kline_normalize_function([], {})
        assert ok is False

    def test_trades_ok(self):
        result, ok = BitrueRequestData._get_trade_history_normalize_function(SAMPLE_TRADES, {})
        assert ok is True
        assert len(result) == 2

    def test_server_time_ok(self):
        result, ok = BitrueRequestData._get_server_time_normalize_function(SAMPLE_SERVER_TIME, {})
        assert ok is True
        assert result[0]["server_time"] == 1700000000000

    def test_exchange_info_ok(self):
        result, ok = BitrueRequestData._get_exchange_info_normalize_function(
            SAMPLE_EXCHANGE_INFO, {}
        )
        assert ok is True
        assert "symbols" in result[0]

    def test_make_order_ok(self):
        result, ok = BitrueRequestData._make_order_normalize_function(SAMPLE_MAKE_ORDER, {})
        assert ok is True
        assert result[0]["orderId"] == 12345678

    def test_make_order_error(self):
        result, ok = BitrueRequestData._make_order_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_cancel_order_ok(self):
        result, ok = BitrueRequestData._cancel_order_normalize_function({"status": "ok"}, {})
        assert ok is True

    def test_cancel_order_error(self):
        result, ok = BitrueRequestData._cancel_order_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_query_order_ok(self):
        result, ok = BitrueRequestData._query_order_normalize_function(
            {"orderId": 123, "status": "FILLED"}, {}
        )
        assert ok is True

    def test_open_orders_ok(self):
        result, ok = BitrueRequestData._get_open_orders_normalize_function(SAMPLE_OPEN_ORDERS, {})
        assert ok is True
        assert len(result) == 1

    def test_open_orders_empty(self):
        result, ok = BitrueRequestData._get_open_orders_normalize_function([], {})
        assert ok is False  # empty list is falsy, _is_error returns True

    def test_deals_ok(self):
        result, ok = BitrueRequestData._get_deals_normalize_function(
            [{"id": 1, "price": "50000"}], {}
        )
        assert ok is True

    def test_account_ok(self):
        result, ok = BitrueRequestData._get_account_normalize_function(SAMPLE_ACCOUNT, {})
        assert ok is True
        assert len(result) == 2  # two balance entries

    def test_balance_ok(self):
        result, ok = BitrueRequestData._get_balance_normalize_function(SAMPLE_ACCOUNT, {})
        assert ok is True
        assert len(result) == 2

    def test_balance_error(self):
        result, ok = BitrueRequestData._get_balance_normalize_function(SAMPLE_ERROR, {})
        assert ok is False


# ── TestSyncCalls (mocked) ──────────────────────────────────────


class TestSyncCalls:
    def _mock_feed(self, mock_response):
        q = queue.Queue()
        feed = BitrueRequestDataSpot(q)
        feed.http_request = MagicMock(return_value=mock_response)
        return feed

    def test_get_tick(self):
        feed = self._mock_feed(SAMPLE_TICK)
        rd = feed.get_tick("BTC/USDT")
        assert isinstance(rd, RequestData)
        feed.http_request.assert_called_once()

    def test_get_depth(self):
        feed = self._mock_feed(SAMPLE_DEPTH)
        rd = feed.get_depth("BTC/USDT", 20)
        assert isinstance(rd, RequestData)

    def test_get_kline(self):
        feed = self._mock_feed(SAMPLE_KLINE)
        rd = feed.get_kline("BTC/USDT", "1h", 50)
        assert isinstance(rd, RequestData)

    def test_get_trade_history(self):
        feed = self._mock_feed(SAMPLE_TRADES)
        rd = feed.get_trade_history("BTC/USDT")
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
        rd = feed.get_open_orders("BTC/USDT")
        assert isinstance(rd, RequestData)

    def test_make_order(self):
        feed = self._mock_feed(SAMPLE_MAKE_ORDER)
        rd = feed.make_order("BTC/USDT", 0.01, 50000, "buy-limit")
        assert isinstance(rd, RequestData)

    def test_cancel_order(self):
        feed = self._mock_feed({"status": "ok"})
        rd = feed.cancel_order("BTC/USDT", "12345")
        assert isinstance(rd, RequestData)

    def test_query_order(self):
        feed = self._mock_feed({"orderId": 123, "status": "FILLED"})
        rd = feed.query_order("BTC/USDT", "12345")
        assert isinstance(rd, RequestData)


# ── TestAuth ────────────────────────────────────────────────────


class TestAuth:
    def test_signature_generation(self, feed_with_keys):
        sig = feed_with_keys._generate_signature("timestamp=1234567890&symbol=BTCUSDT")
        assert isinstance(sig, str)
        assert len(sig) == 64  # SHA256 hex

    def test_signature_empty_without_secret(self, feed):
        sig = feed._generate_signature("timestamp=123")
        assert sig == ""

    def test_api_key_property(self, feed_with_keys):
        assert feed_with_keys.api_key == "test_key"
        assert feed_with_keys.api_secret == "test_secret"


# ── TestRegistry ────────────────────────────────────────────────


class TestRegistry:
    def test_bitrue_spot_registered(self):
        assert "BITRUE___SPOT" in ExchangeRegistry._feed_classes
        assert ExchangeRegistry._feed_classes["BITRUE___SPOT"] == BitrueRequestDataSpot

    def test_exchange_data_registered(self):
        assert "BITRUE___SPOT" in ExchangeRegistry._exchange_data_classes
        assert ExchangeRegistry._exchange_data_classes["BITRUE___SPOT"] == BitrueExchangeDataSpot

    def test_balance_handler_registered(self):
        assert "BITRUE___SPOT" in ExchangeRegistry._balance_handlers
        assert ExchangeRegistry._balance_handlers["BITRUE___SPOT"] is not None

    def test_create_exchange_data(self):
        ed = ExchangeRegistry.create_exchange_data("BITRUE___SPOT")
        assert isinstance(ed, BitrueExchangeDataSpot)


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
        assert feed.exchange_name == "BITRUE___SPOT"

    def test_default_asset_type(self, feed):
        assert feed.asset_type == "SPOT"

    def test_api_keys(self, feed_with_keys):
        assert feed_with_keys.api_key == "test_key"
        assert feed_with_keys.api_secret == "test_secret"

    def test_capabilities(self):
        caps = BitrueRequestDataSpot._capabilities()
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


# ── TestIntegration (skipped, need network / keys) ──────────────


class TestIntegration:
    @pytest.mark.skip(reason="Requires network access")
    def test_live_get_tick(self):
        q = queue.Queue()
        feed = BitrueRequestDataSpot(q)
        rd = feed.get_tick("BTCUSDT")
        assert isinstance(rd, RequestData)

    @pytest.mark.skip(reason="Requires network access")
    def test_live_get_depth(self):
        q = queue.Queue()
        feed = BitrueRequestDataSpot(q)
        rd = feed.get_depth("BTCUSDT", 10)
        assert isinstance(rd, RequestData)

    @pytest.mark.skip(reason="Requires network access")
    def test_live_get_kline(self):
        q = queue.Queue()
        feed = BitrueRequestDataSpot(q)
        rd = feed.get_kline("BTCUSDT", "1h", 5)
        assert isinstance(rd, RequestData)

    @pytest.mark.skip(reason="Requires network access")
    def test_live_get_exchange_info(self):
        q = queue.Queue()
        feed = BitrueRequestDataSpot(q)
        rd = feed.get_exchange_info()
        assert isinstance(rd, RequestData)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
