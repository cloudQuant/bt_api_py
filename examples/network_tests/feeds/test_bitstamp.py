"""
Test Bitstamp exchange integration.

Run tests:
    pytest tests/feeds/test_bitstamp.py -v
"""

import queue
from unittest.mock import MagicMock

import pytest

import bt_api_py.exchange_registers.register_bitstamp  # noqa: F401
from bt_api_py.containers.exchanges.bitstamp_exchange_data import (
    BitstampExchangeDataSpot,
)
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_bitstamp.request_base import BitstampRequestData
from bt_api_py.feeds.live_bitstamp.spot import BitstampRequestDataSpot
from bt_api_py.registry import ExchangeRegistry

# ── fixtures ────────────────────────────────────────────────────


@pytest.fixture
def exchange_data():
    return BitstampExchangeDataSpot()


@pytest.fixture
def feed():
    q = queue.Queue()
    return BitstampRequestDataSpot(q)


@pytest.fixture
def feed_with_keys():
    q = queue.Queue()
    return BitstampRequestDataSpot(q, api_key="test_key", api_secret="test_secret")


# ── sample API responses (Bitstamp direct JSON) ────────────────

SAMPLE_TICK = {
    "last": "50000",
    "bid": "49990",
    "ask": "50010",
    "volume": "1234.56",
    "high": "51000",
    "low": "49000",
    "open": "49500",
    "timestamp": "1704067200",
}

SAMPLE_DEPTH = {
    "timestamp": "1704067200",
    "bids": [["49990", "0.5"], ["49980", "1.0"]],
    "asks": [["50010", "0.3"], ["50020", "0.8"]],
}

SAMPLE_KLINE = {
    "data": {
        "pair": "BTC/USD",
        "ohlc": [
            {
                "timestamp": "1704067200",
                "open": "49500",
                "close": "50000",
                "high": "51000",
                "low": "49000",
                "volume": "100.5",
            },
            {
                "timestamp": "1704070800",
                "open": "50000",
                "close": "50500",
                "high": "50800",
                "low": "49800",
                "volume": "80.3",
            },
        ],
    }
}

SAMPLE_TRADES = [
    {"tid": "1", "price": "50000", "amount": "0.01", "type": "0", "date": "1704067200"},
    {"tid": "2", "price": "50010", "amount": "0.02", "type": "1", "date": "1704067201"},
]

SAMPLE_SERVER_TIME = "1704067200"

SAMPLE_EXCHANGE_INFO = [
    {
        "name": "BTC/USD",
        "url_symbol": "btcusd",
        "base_decimals": 8,
        "counter_decimals": 2,
        "trading": "Enabled",
    },
]

SAMPLE_MAKE_ORDER = {
    "id": "12345",
    "datetime": "2024-01-01 00:00:00",
    "type": "0",
    "price": "50000",
    "amount": "0.01",
}

SAMPLE_ACCOUNT = {
    "btc_balance": "0.5",
    "btc_available": "0.4",
    "btc_reserved": "0.1",
    "usd_balance": "10000",
    "usd_available": "9000",
    "usd_reserved": "1000",
    "fee": "0.50",
}

SAMPLE_OPEN_ORDERS = [
    {
        "id": "111",
        "datetime": "2024-01-01",
        "type": "0",
        "price": "49000",
        "amount": "0.01",
        "currency_pair": "BTC/USD",
    },
]

SAMPLE_ERROR = {"status": "error", "reason": "Invalid API key"}


# ── TestExchangeData ────────────────────────────────────────────


class TestExchangeData:
    def test_exchange_name(self, exchange_data):
        assert exchange_data.exchange_name == "BITSTAMP___SPOT"

    def test_asset_type(self, exchange_data):
        assert exchange_data.asset_type == "SPOT"

    def test_rest_url(self, exchange_data):
        assert "bitstamp.net" in exchange_data.rest_url

    def test_wss_url(self, exchange_data):
        assert "bitstamp.net" in exchange_data.wss_url

    def test_get_symbol_dash(self, exchange_data):
        assert exchange_data.get_symbol("BTC-USD") == "btcusd"

    def test_get_symbol_slash(self, exchange_data):
        assert exchange_data.get_symbol("BTC/USD") == "btcusd"

    def test_get_symbol_underscore(self, exchange_data):
        assert exchange_data.get_symbol("BTC_USD") == "btcusd"

    def test_get_period(self, exchange_data):
        assert exchange_data.get_period("1m") == "60"
        assert exchange_data.get_period("1h") == "3600"
        assert exchange_data.get_period("1d") == "86400"

    @pytest.mark.kline
    def test_kline_periods(self, exchange_data):
        assert "1m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods

    def test_legal_currencies(self, exchange_data):
        assert "USD" in exchange_data.legal_currency
        assert "EUR" in exchange_data.legal_currency
        assert "GBP" in exchange_data.legal_currency
        assert "USDC" in exchange_data.legal_currency

    @pytest.mark.ticker
    def test_get_rest_path_tick(self, exchange_data):
        path = exchange_data.get_rest_path("get_tick")
        assert "ticker" in path.lower()

    @pytest.mark.orderbook
    def test_get_rest_path_depth(self, exchange_data):
        path = exchange_data.get_rest_path("get_depth")
        assert "order_book" in path.lower()

    def test_get_rest_path_missing_raises(self, exchange_data):
        with pytest.raises(ValueError):
            exchange_data.get_rest_path("nonexistent_path")

    def test_get_wss_path(self, exchange_data):
        path = exchange_data.get_wss_path("ticker", "BTC-USD")
        assert "btcusd" in path


# ── TestParameterGeneration ─────────────────────────────────────


class TestParameterGeneration:
    @pytest.mark.ticker
    def test_get_tick_params(self, feed):
        path, params, ed = feed._get_tick("BTC-USD")
        assert "GET" in path
        assert "btcusd" in path
        assert ed["request_type"] == "get_tick"

    @pytest.mark.orderbook
    def test_get_depth_params(self, feed):
        path, params, ed = feed._get_depth("BTC-USD", 50)
        assert "GET" in path
        assert "btcusd" in path
        assert ed["request_type"] == "get_depth"

    @pytest.mark.kline
    def test_get_kline_params(self, feed):
        path, params, ed = feed._get_kline("BTC-USD", "1h", 100)
        assert "GET" in path
        assert "btcusd" in path
        assert params["step"] == "3600"
        assert params["limit"] == 100
        assert ed["request_type"] == "get_kline"

    def test_get_trade_history_params(self, feed):
        path, params, ed = feed._get_trade_history("BTC-USD", 50)
        assert "GET" in path
        assert "btcusd" in path
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
        path, body, ed = feed._make_order("BTC-USD", 0.01, 50000, "buy-limit")
        assert "POST" in path
        assert "/buy/" in path
        assert "btcusd" in path
        assert body["amount"] == "0.01"
        assert body["price"] == "50000"
        assert ed["request_type"] == "make_order"

    def test_make_order_sell(self, feed):
        path, body, ed = feed._make_order("BTC-USD", 0.01, 50000, "sell-limit")
        assert "/sell/" in path

    def test_cancel_order_params(self, feed):
        path, body, ed = feed._cancel_order("BTC-USD", "12345")
        assert "POST" in path
        assert body["id"] == "12345"
        assert ed["request_type"] == "cancel_order"

    def test_query_order_params(self, feed):
        path, body, ed = feed._query_order("BTC-USD", "12345")
        assert "POST" in path
        assert body["id"] == "12345"
        assert ed["request_type"] == "query_order"

    def test_get_open_orders_params(self, feed):
        path, body, ed = feed._get_open_orders("BTC-USD")
        assert "POST" in path
        assert ed["request_type"] == "get_open_orders"

    def test_get_deals_params(self, feed):
        path, body, ed = feed._get_deals("BTC-USD")
        assert "POST" in path
        assert ed["request_type"] == "get_deals"

    def test_get_account_params(self, feed):
        path, body, ed = feed._get_account()
        assert "POST" in path
        assert ed["request_type"] == "get_account"

    def test_get_balance_params(self, feed):
        path, body, ed = feed._get_balance()
        assert "POST" in path
        assert ed["request_type"] == "get_balance"


# ── TestNormalization ───────────────────────────────────────────


class TestNormalization:
    @pytest.mark.ticker
    def test_tick_ok(self):
        result, ok = BitstampRequestData._get_tick_normalize_function(SAMPLE_TICK, {})
        assert ok is True
        assert result[0]["last"] == "50000"

    @pytest.mark.ticker
    def test_tick_error(self):
        result, ok = BitstampRequestData._get_tick_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    @pytest.mark.ticker
    def test_tick_empty(self):
        result, ok = BitstampRequestData._get_tick_normalize_function({}, {})
        assert ok is False

    @pytest.mark.ticker
    def test_tick_none(self):
        result, ok = BitstampRequestData._get_tick_normalize_function(None, {})
        assert ok is False

    @pytest.mark.orderbook
    def test_depth_ok(self):
        result, ok = BitstampRequestData._get_depth_normalize_function(SAMPLE_DEPTH, {})
        assert ok is True
        assert "bids" in result[0]
        assert "asks" in result[0]

    @pytest.mark.orderbook
    def test_depth_error(self):
        result, ok = BitstampRequestData._get_depth_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    @pytest.mark.kline
    def test_kline_ok(self):
        result, ok = BitstampRequestData._get_kline_normalize_function(SAMPLE_KLINE, {})
        assert ok is True
        assert len(result) == 2

    @pytest.mark.kline
    def test_kline_empty(self):
        result, ok = BitstampRequestData._get_kline_normalize_function({"data": {"ohlc": []}}, {})
        assert ok is False

    def test_trades_ok(self):
        result, ok = BitstampRequestData._get_trade_history_normalize_function(SAMPLE_TRADES, {})
        assert ok is True
        assert len(result) == 2

    def test_trades_empty(self):
        result, ok = BitstampRequestData._get_trade_history_normalize_function([], {})
        assert ok is False

    def test_server_time_ok(self):
        result, ok = BitstampRequestData._get_server_time_normalize_function(SAMPLE_SERVER_TIME, {})
        assert ok is True
        assert "server_time" in result[0]

    def test_exchange_info_ok(self):
        result, ok = BitstampRequestData._get_exchange_info_normalize_function(
            SAMPLE_EXCHANGE_INFO, {}
        )
        assert ok is True
        assert "pairs" in result[0]

    def test_make_order_ok(self):
        result, ok = BitstampRequestData._make_order_normalize_function(SAMPLE_MAKE_ORDER, {})
        assert ok is True
        assert result[0]["id"] == "12345"

    def test_make_order_error(self):
        result, ok = BitstampRequestData._make_order_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_cancel_order_ok(self):
        result, ok = BitstampRequestData._cancel_order_normalize_function(True, {})
        assert ok is True

    def test_cancel_order_error(self):
        result, ok = BitstampRequestData._cancel_order_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_query_order_ok(self):
        result, ok = BitstampRequestData._query_order_normalize_function(
            {"id": "12345", "status": "Finished"}, {}
        )
        assert ok is True

    def test_open_orders_ok(self):
        result, ok = BitstampRequestData._get_open_orders_normalize_function(SAMPLE_OPEN_ORDERS, {})
        assert ok is True
        assert len(result) == 1

    def test_open_orders_empty(self):
        result, ok = BitstampRequestData._get_open_orders_normalize_function([], {})
        assert ok is True
        assert result == []

    def test_deals_ok(self):
        result, ok = BitstampRequestData._get_deals_normalize_function(
            [{"id": "1", "type": "0", "usd": "50"}], {}
        )
        assert ok is True

    def test_deals_empty(self):
        result, ok = BitstampRequestData._get_deals_normalize_function([], {})
        assert ok is True
        assert result == []

    def test_account_ok(self):
        result, ok = BitstampRequestData._get_account_normalize_function(SAMPLE_ACCOUNT, {})
        assert ok is True
        assert "btc_balance" in result[0]

    def test_balance_ok(self):
        result, ok = BitstampRequestData._get_balance_normalize_function(SAMPLE_ACCOUNT, {})
        assert ok is True

    def test_balance_error(self):
        result, ok = BitstampRequestData._get_balance_normalize_function(SAMPLE_ERROR, {})
        assert ok is False


# ── TestSyncCalls (mocked) ──────────────────────────────────────


class TestSyncCalls:
    def _mock_feed(self, mock_response):
        q = queue.Queue()
        feed = BitstampRequestDataSpot(q)
        feed.http_request = MagicMock(return_value=mock_response)
        return feed

    @pytest.mark.ticker
    def test_get_tick(self):
        feed = self._mock_feed(SAMPLE_TICK)
        rd = feed.get_tick("BTC-USD")
        assert isinstance(rd, RequestData)
        feed.http_request.assert_called_once()

    @pytest.mark.orderbook
    def test_get_depth(self):
        feed = self._mock_feed(SAMPLE_DEPTH)
        rd = feed.get_depth("BTC-USD", 20)
        assert isinstance(rd, RequestData)

    @pytest.mark.kline
    def test_get_kline(self):
        feed = self._mock_feed(SAMPLE_KLINE)
        rd = feed.get_kline("BTC-USD", "1h", 50)
        assert isinstance(rd, RequestData)

    def test_get_trade_history(self):
        feed = self._mock_feed(SAMPLE_TRADES)
        rd = feed.get_trade_history("BTC-USD")
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
        rd = feed.get_open_orders("BTC-USD")
        assert isinstance(rd, RequestData)

    def test_make_order(self):
        feed = self._mock_feed(SAMPLE_MAKE_ORDER)
        rd = feed.make_order("BTC-USD", 0.01, 50000, "buy-limit")
        assert isinstance(rd, RequestData)

    def test_cancel_order(self):
        feed = self._mock_feed(True)
        rd = feed.cancel_order("BTC-USD", "12345")
        assert isinstance(rd, RequestData)

    def test_query_order(self):
        feed = self._mock_feed({"id": "12345", "status": "Finished"})
        rd = feed.query_order("BTC-USD", "12345")
        assert isinstance(rd, RequestData)


# ── TestAuth ────────────────────────────────────────────────────


class TestAuth:
    def test_auth_headers_generation(self, feed_with_keys):
        headers = feed_with_keys._generate_auth_headers("POST", "/balance/")
        assert "X-Auth" in headers
        assert "X-Auth-Signature" in headers
        assert "X-Auth-Nonce" in headers
        assert "X-Auth-Timestamp" in headers
        assert "X-Auth-Version" in headers
        assert headers["X-Auth-Version"] == "v2"
        assert len(headers["X-Auth-Signature"]) == 64  # SHA256 hex

    def test_auth_empty_without_secret(self, feed):
        headers = feed._generate_auth_headers("POST", "/balance/")
        assert headers == {}

    def test_api_key_property(self, feed_with_keys):
        assert feed_with_keys.api_key == "test_key"
        assert feed_with_keys.api_secret == "test_secret"


# ── TestRegistry ────────────────────────────────────────────────


class TestRegistry:
    def test_bitstamp_spot_registered(self):
        assert "BITSTAMP___SPOT" in ExchangeRegistry._feed_classes
        assert ExchangeRegistry._feed_classes["BITSTAMP___SPOT"] == BitstampRequestDataSpot

    def test_exchange_data_registered(self):
        assert "BITSTAMP___SPOT" in ExchangeRegistry._exchange_data_classes
        assert (
            ExchangeRegistry._exchange_data_classes["BITSTAMP___SPOT"] == BitstampExchangeDataSpot
        )

    def test_balance_handler_registered(self):
        assert "BITSTAMP___SPOT" in ExchangeRegistry._balance_handlers

    def test_create_exchange_data(self):
        ed = ExchangeRegistry.create_exchange_data("BITSTAMP___SPOT")
        assert isinstance(ed, BitstampExchangeDataSpot)


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
        assert feed.exchange_name == "BITSTAMP___SPOT"

    def test_default_asset_type(self, feed):
        assert feed.asset_type == "SPOT"

    def test_api_keys(self, feed_with_keys):
        assert feed_with_keys.api_key == "test_key"
        assert feed_with_keys.api_secret == "test_secret"

    def test_capabilities(self):
        caps = BitstampRequestDataSpot._capabilities()
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
    @pytest.mark.ticker
    def test_live_get_tick(self):
        q = queue.Queue()
        feed = BitstampRequestDataSpot(q)
        rd = feed.get_tick("BTC-USD")
        assert isinstance(rd, RequestData)

    @pytest.mark.skip(reason="Requires network access")
    @pytest.mark.orderbook
    def test_live_get_depth(self):
        q = queue.Queue()
        feed = BitstampRequestDataSpot(q)
        rd = feed.get_depth("BTC-USD", 10)
        assert isinstance(rd, RequestData)

    @pytest.mark.skip(reason="Requires network access")
    @pytest.mark.kline
    def test_live_get_kline(self):
        q = queue.Queue()
        feed = BitstampRequestDataSpot(q)
        rd = feed.get_kline("BTC-USD", "1h", 5)
        assert isinstance(rd, RequestData)

    @pytest.mark.skip(reason="Requires network access")
    def test_live_get_exchange_info(self):
        q = queue.Queue()
        feed = BitstampRequestDataSpot(q)
        rd = feed.get_exchange_info()
        assert isinstance(rd, RequestData)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
