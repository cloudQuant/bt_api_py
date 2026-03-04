"""
Tests for CoinEx exchange – Feed pattern.

Run:  pytest tests/feeds/test_coinex.py -v
"""

import queue
from unittest.mock import patch

import pytest

from bt_api_py.containers.exchanges.coinex_exchange_data import CoinExExchangeDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_coinex.request_base import CoinExRequestData
from bt_api_py.feeds.live_coinex.spot import CoinExRequestDataSpot
from bt_api_py.registry import ExchangeRegistry

import bt_api_py.exchange_registers.register_coinex  # noqa: F401

# ── sample V2 response fixtures ──────────────────────────────

SAMPLE_TICK = {"code": 0, "data": [
    {"market": "BTCUSDT", "last": "50000", "volume": "1234", "high": "51000", "low": "49000"}
], "message": "OK"}

SAMPLE_DEPTH = {"code": 0, "data": {
    "depth": {"asks": [["50010", "1"]], "bids": [["49990", "0.5"]]},
    "market": "BTCUSDT",
}, "message": "OK"}

SAMPLE_KLINE = {"code": 0, "data": [
    {"open": "49000", "high": "51000", "low": "48000", "close": "50000", "volume": "100"},
    {"open": "50000", "high": "52000", "low": "49000", "close": "51000", "volume": "120"},
], "message": "OK"}

SAMPLE_TRADES = {"code": 0, "data": [
    {"price": "50000", "amount": "0.01", "side": "buy", "created_at": 1700000000},
], "message": "OK"}

SAMPLE_EXCHANGE_INFO = {"code": 0, "data": [
    {"market": "BTCUSDT", "base_ccy": "BTC", "quote_ccy": "USDT"},
    {"market": "ETHUSDT", "base_ccy": "ETH", "quote_ccy": "USDT"},
], "message": "OK"}

SAMPLE_ACCOUNT = {"code": 0, "data": {"user_id": 12345}, "message": "OK"}

SAMPLE_BALANCE = {"code": 0, "data": [
    {"ccy": "BTC", "available": "1.5", "frozen": "0.0"},
    {"ccy": "USDT", "available": "10000", "frozen": "0.0"},
], "message": "OK"}

SAMPLE_ORDER = {"code": 0, "data": {
    "order_id": 12345, "market": "BTCUSDT", "side": "buy", "type": "limit",
    "amount": "0.001", "price": "50000", "status": "done",
}, "message": "OK"}

SAMPLE_OPEN_ORDERS = {"code": 0, "data": [
    {"order_id": 11111, "market": "BTCUSDT", "side": "buy", "type": "limit",
     "amount": "0.01", "price": "40000", "status": "open"},
], "message": "OK"}

SAMPLE_ERROR = {"code": 2, "data": {}, "message": "invalid parameter"}


# ── helpers ───────────────────────────────────────────────────

@pytest.fixture
def feed():
    q = queue.Queue()
    return CoinExRequestDataSpot(q)


@pytest.fixture
def exdata():
    return CoinExExchangeDataSpot()


# ═══════════════════════════════════════════════════════════════
# 1) ExchangeData
# ═══════════════════════════════════════════════════════════════


class TestExchangeData:
    def test_exchange_name(self, exdata):
        assert exdata.exchange_name == "COINEX___SPOT"

    def test_asset_type(self, exdata):
        assert exdata.asset_type == "SPOT"

    def test_rest_url(self, exdata):
        assert "api.coinex.com" in exdata.rest_url

    def test_wss_url(self, exdata):
        assert "coinex.com" in exdata.wss_url

    def test_get_symbol(self, exdata):
        assert exdata.get_symbol("BTCUSDT") == "BTCUSDT"
        assert exdata.get_symbol("BTC-USDT") == "BTCUSDT"

    def test_get_period(self, exdata):
        assert exdata.get_period("1m") == "1min"
        assert exdata.get_period("1h") == "1hour"
        assert exdata.get_period("1d") == "1day"
        assert exdata.get_period("1w") == "1week"

    def test_kline_periods(self, exdata):
        for k in ("1m", "5m", "15m", "1h", "4h", "1d", "1w"):
            assert k in exdata.kline_periods

    def test_legal_currency(self, exdata):
        for c in ("USDT", "USD", "BTC", "ETH", "USDC"):
            assert c in exdata.legal_currency

    def test_get_rest_path_ok(self, exdata):
        p = exdata.get_rest_path("get_tick")
        assert "/v2/spot/ticker" in p

    def test_get_rest_path_missing(self, exdata):
        with pytest.raises(ValueError):
            exdata.get_rest_path("nonexistent_endpoint")

    def test_rest_paths_keys(self, exdata):
        for key in ("get_tick", "get_depth", "get_kline", "get_exchange_info",
                     "get_trades", "get_account", "get_balance", "make_order",
                     "cancel_order", "query_order", "get_open_orders", "get_deals"):
            assert key in exdata.rest_paths


# ═══════════════════════════════════════════════════════════════
# 2) Parameter generation (_get_xxx)
# ═══════════════════════════════════════════════════════════════


class TestParamGeneration:
    def test_get_tick_params(self, feed):
        path, params, extra = feed._get_tick("BTCUSDT")
        assert "GET" in path
        assert "/v2/spot/ticker" in path
        assert params["market"] == "BTCUSDT"
        assert extra["request_type"] == "get_tick"

    def test_get_depth_params(self, feed):
        path, params, extra = feed._get_depth("BTCUSDT", 10)
        assert "/v2/spot/depth" in path
        assert params["market"] == "BTCUSDT"
        assert params["limit"] == 10

    def test_get_kline_params(self, feed):
        path, params, extra = feed._get_kline("BTCUSDT", "1h", 50)
        assert "/v2/spot/kline" in path
        assert params["market"] == "BTCUSDT"
        assert params["period"] == "1hour"
        assert params["limit"] == 50

    def test_get_trade_history_params(self, feed):
        path, params, extra = feed._get_trade_history("BTCUSDT", count=20)
        assert "/v2/spot/deals" in path
        assert params["market"] == "BTCUSDT"
        assert params["limit"] == 20

    def test_get_exchange_info_params(self, feed):
        path, params, extra = feed._get_exchange_info()
        assert "/v2/spot/market" in path
        assert extra["request_type"] == "get_exchange_info"

    def test_make_order_params(self, feed):
        path, body, extra = feed._make_order("BTCUSDT", 0.001, price=50000, order_type="buy-limit")
        assert "POST" in path
        assert body["market"] == "BTCUSDT"
        assert body["side"] == "buy"
        assert body["type"] == "limit"
        assert body["amount"] == "0.001"

    def test_cancel_order_params(self, feed):
        path, params, extra = feed._cancel_order(symbol="BTCUSDT", order_id="12345")
        assert "DELETE" in path
        assert params["market"] == "BTCUSDT"
        assert params["order_id"] == "12345"

    def test_query_order_params(self, feed):
        path, params, extra = feed._query_order(symbol="BTCUSDT", order_id="12345")
        assert "GET" in path
        assert params["order_id"] == "12345"

    def test_get_open_orders_params(self, feed):
        path, params, extra = feed._get_open_orders(symbol="BTCUSDT")
        assert params["market_type"] == "SPOT"
        assert params["market"] == "BTCUSDT"

    def test_get_deals_params(self, feed):
        path, params, extra = feed._get_deals(symbol="BTCUSDT")
        assert params["market_type"] == "SPOT"

    def test_get_account_params(self, feed):
        path, params, extra = feed._get_account()
        assert "/v2/account/info" in path
        assert extra["request_type"] == "get_account"

    def test_get_balance_params(self, feed):
        path, params, extra = feed._get_balance()
        assert "/v2/assets/spot/balance" in path
        assert extra["request_type"] == "get_balance"


# ═══════════════════════════════════════════════════════════════
# 3) Normalization functions
# ═══════════════════════════════════════════════════════════════


class TestNormalization:
    def test_tick_ok(self):
        result, ok = CoinExRequestData._get_tick_normalize_function(SAMPLE_TICK, {})
        assert ok is True
        assert len(result) > 0

    def test_tick_error(self):
        result, ok = CoinExRequestData._get_tick_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_depth_ok(self):
        result, ok = CoinExRequestData._get_depth_normalize_function(SAMPLE_DEPTH, {})
        assert ok is True
        assert "depth" in result[0]

    def test_depth_error(self):
        result, ok = CoinExRequestData._get_depth_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_kline_ok(self):
        result, ok = CoinExRequestData._get_kline_normalize_function(SAMPLE_KLINE, {})
        assert ok is True
        assert len(result) == 2

    def test_kline_error(self):
        result, ok = CoinExRequestData._get_kline_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_trades_ok(self):
        result, ok = CoinExRequestData._get_trade_history_normalize_function(SAMPLE_TRADES, {})
        assert ok is True

    def test_trades_error(self):
        result, ok = CoinExRequestData._get_trade_history_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_exchange_info_ok(self):
        result, ok = CoinExRequestData._get_exchange_info_normalize_function(SAMPLE_EXCHANGE_INFO, {})
        assert ok is True
        assert "markets" in result[0]

    def test_make_order_ok(self):
        result, ok = CoinExRequestData._make_order_normalize_function(SAMPLE_ORDER, {})
        assert ok is True
        assert result[0]["order_id"] == 12345

    def test_make_order_error(self):
        result, ok = CoinExRequestData._make_order_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_cancel_order_ok(self):
        result, ok = CoinExRequestData._cancel_order_normalize_function(SAMPLE_ORDER, {})
        assert ok is True

    def test_query_order_ok(self):
        result, ok = CoinExRequestData._query_order_normalize_function(SAMPLE_ORDER, {})
        assert ok is True

    def test_query_order_error(self):
        result, ok = CoinExRequestData._query_order_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_open_orders_ok(self):
        result, ok = CoinExRequestData._get_open_orders_normalize_function(SAMPLE_OPEN_ORDERS, {})
        assert ok is True
        assert len(result) == 1

    def test_deals_ok(self):
        result, ok = CoinExRequestData._get_deals_normalize_function(SAMPLE_TRADES, {})
        assert ok is True

    def test_account_ok(self):
        result, ok = CoinExRequestData._get_account_normalize_function(SAMPLE_ACCOUNT, {})
        assert ok is True

    def test_account_error(self):
        result, ok = CoinExRequestData._get_account_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_balance_ok(self):
        result, ok = CoinExRequestData._get_balance_normalize_function(SAMPLE_BALANCE, {})
        assert ok is True
        assert len(result) == 2

    def test_balance_error(self):
        result, ok = CoinExRequestData._get_balance_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_is_error_none(self):
        assert CoinExRequestData._is_error(None) is True

    def test_is_error_nonzero_code(self):
        assert CoinExRequestData._is_error(SAMPLE_ERROR) is True

    def test_is_error_ok(self):
        assert CoinExRequestData._is_error(SAMPLE_TICK) is False

    def test_unwrap(self):
        assert CoinExRequestData._unwrap(SAMPLE_TICK) == SAMPLE_TICK["data"]
        assert CoinExRequestData._unwrap([1, 2]) == [1, 2]


# ═══════════════════════════════════════════════════════════════
# 4) Mocked sync calls
# ═══════════════════════════════════════════════════════════════


class TestSyncCalls:
    @patch.object(CoinExRequestData, "http_request", return_value=SAMPLE_TICK)
    def test_get_tick(self, mock_http, feed):
        rd = feed.get_tick("BTCUSDT")
        assert isinstance(rd, RequestData)
        mock_http.assert_called_once()

    @patch.object(CoinExRequestData, "http_request", return_value=SAMPLE_DEPTH)
    def test_get_depth(self, mock_http, feed):
        rd = feed.get_depth("BTCUSDT")
        assert isinstance(rd, RequestData)

    @patch.object(CoinExRequestData, "http_request", return_value=SAMPLE_KLINE)
    def test_get_kline(self, mock_http, feed):
        rd = feed.get_kline("BTCUSDT", "1h", 10)
        assert isinstance(rd, RequestData)

    @patch.object(CoinExRequestData, "http_request", return_value=SAMPLE_TRADES)
    def test_get_trade_history(self, mock_http, feed):
        rd = feed.get_trade_history("BTCUSDT", count=5)
        assert isinstance(rd, RequestData)

    @patch.object(CoinExRequestData, "http_request", return_value=SAMPLE_EXCHANGE_INFO)
    def test_get_exchange_info(self, mock_http, feed):
        rd = feed.get_exchange_info()
        assert isinstance(rd, RequestData)

    @patch.object(CoinExRequestData, "http_request", return_value=SAMPLE_ACCOUNT)
    def test_get_account(self, mock_http, feed):
        rd = feed.get_account()
        assert isinstance(rd, RequestData)

    @patch.object(CoinExRequestData, "http_request", return_value=SAMPLE_BALANCE)
    def test_get_balance(self, mock_http, feed):
        rd = feed.get_balance()
        assert isinstance(rd, RequestData)

    @patch.object(CoinExRequestData, "http_request", return_value=SAMPLE_ORDER)
    def test_make_order(self, mock_http, feed):
        rd = feed.make_order("BTCUSDT", 0.001, price=50000)
        assert isinstance(rd, RequestData)

    @patch.object(CoinExRequestData, "http_request", return_value=SAMPLE_ORDER)
    def test_cancel_order(self, mock_http, feed):
        rd = feed.cancel_order(symbol="BTCUSDT", order_id="12345")
        assert isinstance(rd, RequestData)

    @patch.object(CoinExRequestData, "http_request", return_value=SAMPLE_ORDER)
    def test_query_order(self, mock_http, feed):
        rd = feed.query_order(symbol="BTCUSDT", order_id="12345")
        assert isinstance(rd, RequestData)

    @patch.object(CoinExRequestData, "http_request", return_value=SAMPLE_OPEN_ORDERS)
    def test_get_open_orders(self, mock_http, feed):
        rd = feed.get_open_orders(symbol="BTCUSDT")
        assert isinstance(rd, RequestData)


# ═══════════════════════════════════════════════════════════════
# 5) Auth
# ═══════════════════════════════════════════════════════════════


class TestAuth:
    def test_no_auth_without_keys(self, feed):
        headers = feed._generate_auth_headers("GET", "/v2/spot/ticker")
        assert headers == {}

    def test_api_key_property(self, feed):
        assert feed.api_key == ""
        assert feed.api_secret == ""

    def test_auth_with_keys(self):
        q = queue.Queue()
        feed = CoinExRequestDataSpot(q, api_key="mykey", api_secret="mysecret")
        headers = feed._generate_auth_headers("GET", "/v2/spot/ticker")
        assert "X-COINEX-KEY" in headers
        assert headers["X-COINEX-KEY"] == "mykey"
        assert "X-COINEX-SIGN" in headers
        assert len(headers["X-COINEX-SIGN"]) == 64
        assert "X-COINEX-TIMESTAMP" in headers

    def test_signature_deterministic(self):
        q = queue.Queue()
        feed = CoinExRequestDataSpot(q, api_key="k", api_secret="s")
        sig1 = feed._generate_signature("GET", "/v2/spot/ticker", "", "1000")
        sig2 = feed._generate_signature("GET", "/v2/spot/ticker", "", "1000")
        assert sig1 == sig2
        assert len(sig1) == 64


# ═══════════════════════════════════════════════════════════════
# 6) Registry
# ═══════════════════════════════════════════════════════════════


class TestRegistry:
    def test_feed_registered(self):
        assert "COINEX___SPOT" in ExchangeRegistry._feed_classes

    def test_exchange_data_registered(self):
        assert "COINEX___SPOT" in ExchangeRegistry._exchange_data_classes

    def test_balance_handler_registered(self):
        assert "COINEX___SPOT" in ExchangeRegistry._balance_handlers

    def test_create_exchange_data(self):
        ed = ExchangeRegistry.create_exchange_data("COINEX___SPOT")
        assert isinstance(ed, CoinExExchangeDataSpot)


# ═══════════════════════════════════════════════════════════════
# 7) Method existence
# ═══════════════════════════════════════════════════════════════


_EXPECTED_METHODS = [
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
    "get_deals", "async_get_deals",
    "get_account", "async_get_account",
    "get_balance", "async_get_balance",
    "get_exchange_info", "async_get_exchange_info",
]


class TestMethodExistence:
    @pytest.mark.parametrize("method_name", _EXPECTED_METHODS)
    def test_method_exists(self, feed, method_name):
        assert hasattr(feed, method_name), f"Missing method: {method_name}"
        assert callable(getattr(feed, method_name))


# ═══════════════════════════════════════════════════════════════
# 8) Feed init
# ═══════════════════════════════════════════════════════════════


class TestFeedInit:
    def test_default_exchange_name(self, feed):
        assert feed.exchange_name == "COINEX___SPOT"

    def test_default_asset_type(self, feed):
        assert feed.asset_type == "SPOT"

    def test_api_keys(self):
        q = queue.Queue()
        feed = CoinExRequestDataSpot(q, api_key="ak", api_secret="sk")
        assert feed.api_key == "ak"
        assert feed.api_secret == "sk"

    def test_capabilities(self, feed):
        caps = feed._capabilities()
        assert len(caps) > 0

    def test_push_data_to_queue(self, feed):
        feed.push_data_to_queue({"test": 1})
        assert not feed.data_queue.empty()


# ═══════════════════════════════════════════════════════════════
# 9) Integration (skipped)
# ═══════════════════════════════════════════════════════════════


class TestIntegration:
    @pytest.mark.skip(reason="Requires network access")
    def test_live_get_tick(self):
        q = queue.Queue()
        feed = CoinExRequestDataSpot(q)
        rd = feed.get_tick("BTCUSDT")
        assert isinstance(rd, RequestData)

    @pytest.mark.skip(reason="Requires network access")
    def test_live_get_depth(self):
        q = queue.Queue()
        feed = CoinExRequestDataSpot(q)
        rd = feed.get_depth("BTCUSDT")
        assert isinstance(rd, RequestData)

    @pytest.mark.skip(reason="Requires network access")
    def test_live_get_kline(self):
        q = queue.Queue()
        feed = CoinExRequestDataSpot(q)
        rd = feed.get_kline("BTCUSDT", "1h", 5)
        assert isinstance(rd, RequestData)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
