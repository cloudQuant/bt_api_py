"""
Tests for Coinone exchange – Feed pattern.

Run:  pytest tests/feeds/test_coinone.py -v
"""

import queue
from unittest.mock import patch

import pytest

import bt_api_py.exchange_registers.register_coinone  # noqa: F401
from bt_api_py.containers.exchanges.coinone_exchange_data import CoinoneExchangeDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_coinone.request_base import CoinoneRequestData
from bt_api_py.feeds.live_coinone.spot import CoinoneRequestDataSpot
from bt_api_py.registry import ExchangeRegistry

# ── sample response fixtures ─────────────────────────────────

SAMPLE_TICK = {
    "result": "success",
    "errorCode": "0",
    "tickers": [
        {
            "target_currency": "BTC",
            "last": "50000000",
            "high": "51000000",
            "low": "49000000",
            "volume": "123.4",
        }
    ],
}

SAMPLE_TICK_SINGLE = {
    "result": "success",
    "errorCode": "0",
    "last": "50000000",
    "high": "51000000",
    "low": "49000000",
}

SAMPLE_DEPTH = {
    "result": "success",
    "errorCode": "0",
    "asks": [{"price": "51000000", "qty": "0.1"}],
    "bids": [{"price": "49000000", "qty": "0.5"}],
}

SAMPLE_KLINE = {
    "result": "success",
    "errorCode": "0",
    "chart": [
        {"open": "49000000", "high": "51000000", "low": "48000000", "close": "50000000"},
        {"open": "50000000", "high": "52000000", "low": "49000000", "close": "51000000"},
    ],
}

SAMPLE_TRADES = {
    "result": "success",
    "errorCode": "0",
    "trades": [
        {"price": "50000000", "qty": "0.01", "is_seller_maker": False, "timestamp": "1700000000"}
    ],
}

SAMPLE_EXCHANGE_INFO = {
    "result": "success",
    "errorCode": "0",
    "markets": [
        {"target_currency": "BTC", "min_qty": "0.0001", "max_qty": "100"},
        {"target_currency": "ETH", "min_qty": "0.001", "max_qty": "1000"},
    ],
}

SAMPLE_ORDER = {"result": "success", "errorCode": "0", "orderId": "abc-123", "status": "live"}

SAMPLE_OPEN_ORDERS = {
    "result": "success",
    "errorCode": "0",
    "limitOrders": [
        {"orderId": "abc-123", "price": "50000000", "qty": "0.001", "type": "bid"},
    ],
}

SAMPLE_DEALS = {
    "result": "success",
    "errorCode": "0",
    "completeOrders": [
        {"orderId": "abc-456", "price": "50000000", "qty": "0.001", "type": "bid"},
    ],
}

SAMPLE_ACCOUNT = {
    "result": "success",
    "errorCode": "0",
    "balances": {
        "BTC": {"available": "1.5", "limit": "0.0"},
        "KRW": {"available": "1000000", "limit": "0"},
    },
}

SAMPLE_ERROR = {"result": "error", "error_code": "107", "error_msg": "Parameter value is wrong"}


# ── helpers ───────────────────────────────────────────────────


@pytest.fixture
def feed():
    return CoinoneRequestDataSpot(queue.Queue())


@pytest.fixture
def exdata():
    return CoinoneExchangeDataSpot()


# ═══════════════════════════════════════════════════════════════
# 1) ExchangeData
# ═══════════════════════════════════════════════════════════════


class TestExchangeData:
    def test_exchange_name(self, exdata):
        assert exdata.exchange_name == "COINONE___SPOT"

    def test_asset_type(self, exdata):
        assert exdata.asset_type == "SPOT"

    def test_rest_url(self, exdata):
        assert exdata.rest_url == "https://api.coinone.co.kr"

    def test_wss_url(self, exdata):
        assert "coinone.co.kr" in exdata.wss_url

    def test_get_symbol(self, exdata):
        assert exdata.get_symbol("KRW-BTC") == "KRW-BTC"

    def test_parse_symbol(self, exdata):
        q, t = CoinoneExchangeDataSpot.parse_symbol("KRW-BTC")
        assert q == "KRW" and t == "BTC"
        q, t = CoinoneExchangeDataSpot.parse_symbol("BTC")
        assert q == "KRW" and t == "BTC"

    def test_get_period(self, exdata):
        assert exdata.get_period("1m") == "1m"
        assert exdata.get_period("1h") == "1h"
        assert exdata.get_period("1d") == "1d"

    def test_kline_periods(self, exdata):
        for k in ("1m", "5m", "15m", "1h", "4h", "1d", "1w"):
            assert k in exdata.kline_periods

    def test_legal_currency(self, exdata):
        assert "KRW" in exdata.legal_currency

    def test_get_rest_path_ok(self, exdata):
        p = exdata.get_rest_path("get_tick")
        assert "/public/v2/ticker_new" in p

    def test_get_rest_path_missing(self, exdata):
        with pytest.raises(ValueError):
            exdata.get_rest_path("nonexistent_endpoint")

    def test_rest_paths_keys(self, exdata):
        for key in (
            "get_tick",
            "get_depth",
            "get_kline",
            "get_exchange_info",
            "get_trades",
            "get_account",
            "get_balance",
            "make_order",
            "cancel_order",
            "query_order",
            "get_open_orders",
            "get_deals",
        ):
            assert key in exdata.rest_paths


# ═══════════════════════════════════════════════════════════════
# 2) Parameter generation (_get_xxx)
# ═══════════════════════════════════════════════════════════════


class TestParamGeneration:
    def test_get_tick_params(self, feed):
        path, params, extra = feed._get_tick("KRW-BTC")
        assert "GET" in path
        assert "/public/v2/ticker_new/KRW/BTC" in path
        assert extra["request_type"] == "get_tick"
        assert extra["symbol_name"] == "KRW-BTC"

    def test_get_depth_params(self, feed):
        path, params, extra = feed._get_depth("KRW-BTC", 20)
        assert "/public/v2/orderbook/KRW/BTC" in path
        assert params["size"] == 20

    def test_get_kline_params(self, feed):
        path, params, extra = feed._get_kline("KRW-BTC", "1h", 50)
        assert "/public/v2/chart/KRW/BTC" in path
        assert params["interval"] == "1h"
        assert params["limit"] == 50

    def test_get_trade_history_params(self, feed):
        path, params, extra = feed._get_trade_history("KRW-BTC")
        assert "/public/v2/trades/KRW/BTC" in path
        assert extra["request_type"] == "get_trades"

    def test_get_exchange_info_params(self, feed):
        path, params, extra = feed._get_exchange_info()
        assert "/public/v2/markets/KRW" in path
        assert extra["request_type"] == "get_exchange_info"

    def test_make_order_params(self, feed):
        path, body, extra = feed._make_order("KRW-BTC", 0.001, price=50000000, order_type="bid")
        assert "POST" in path
        assert body["quote_currency"] == "KRW"
        assert body["target_currency"] == "BTC"
        assert body["type"] == "bid"
        assert body["qty"] == "0.001"
        assert body["price"] == "50000000"

    def test_cancel_order_params(self, feed):
        path, body, extra = feed._cancel_order(symbol="KRW-BTC", order_id="abc-123")
        assert "POST" in path
        assert body["order_id"] == "abc-123"
        assert body["quote_currency"] == "KRW"

    def test_query_order_params(self, feed):
        path, body, extra = feed._query_order(symbol="KRW-BTC", order_id="abc-123")
        assert "POST" in path
        assert body["order_id"] == "abc-123"

    def test_get_open_orders_params(self, feed):
        path, body, extra = feed._get_open_orders(symbol="KRW-BTC")
        assert body["quote_currency"] == "KRW"
        assert body["target_currency"] == "BTC"

    def test_get_deals_params(self, feed):
        path, body, extra = feed._get_deals(symbol="KRW-BTC")
        assert body["quote_currency"] == "KRW"

    def test_get_account_params(self, feed):
        path, body, extra = feed._get_account()
        assert "/v2.1/account/balance/all" in path
        assert extra["request_type"] == "get_account"

    def test_get_balance_params(self, feed):
        path, body, extra = feed._get_balance()
        assert "/v2.1/account/balance/all" in path
        assert extra["request_type"] == "get_balance"


# ═══════════════════════════════════════════════════════════════
# 3) Normalization functions
# ═══════════════════════════════════════════════════════════════


class TestNormalization:
    def test_tick_ok(self):
        result, ok = CoinoneRequestData._get_tick_normalize_function(SAMPLE_TICK, {})
        assert ok is True
        assert len(result) == 1

    def test_tick_single_ok(self):
        result, ok = CoinoneRequestData._get_tick_normalize_function(SAMPLE_TICK_SINGLE, {})
        assert ok is True
        assert "last" in result[0]

    def test_tick_error(self):
        result, ok = CoinoneRequestData._get_tick_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_depth_ok(self):
        result, ok = CoinoneRequestData._get_depth_normalize_function(SAMPLE_DEPTH, {})
        assert ok is True

    def test_depth_error(self):
        result, ok = CoinoneRequestData._get_depth_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_kline_ok(self):
        result, ok = CoinoneRequestData._get_kline_normalize_function(SAMPLE_KLINE, {})
        assert ok is True
        assert len(result) == 2

    def test_kline_error(self):
        result, ok = CoinoneRequestData._get_kline_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_trades_ok(self):
        result, ok = CoinoneRequestData._get_trade_history_normalize_function(SAMPLE_TRADES, {})
        assert ok is True

    def test_trades_error(self):
        result, ok = CoinoneRequestData._get_trade_history_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_exchange_info_ok(self):
        result, ok = CoinoneRequestData._get_exchange_info_normalize_function(
            SAMPLE_EXCHANGE_INFO, {}
        )
        assert ok is True
        assert "markets" in result[0]

    def test_make_order_ok(self):
        result, ok = CoinoneRequestData._make_order_normalize_function(SAMPLE_ORDER, {})
        assert ok is True

    def test_make_order_error(self):
        result, ok = CoinoneRequestData._make_order_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_cancel_order_ok(self):
        result, ok = CoinoneRequestData._cancel_order_normalize_function(SAMPLE_ORDER, {})
        assert ok is True

    def test_query_order_ok(self):
        result, ok = CoinoneRequestData._query_order_normalize_function(SAMPLE_ORDER, {})
        assert ok is True

    def test_query_order_error(self):
        result, ok = CoinoneRequestData._query_order_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_open_orders_ok(self):
        result, ok = CoinoneRequestData._get_open_orders_normalize_function(SAMPLE_OPEN_ORDERS, {})
        assert ok is True
        assert len(result) == 1

    def test_deals_ok(self):
        result, ok = CoinoneRequestData._get_deals_normalize_function(SAMPLE_DEALS, {})
        assert ok is True
        assert len(result) == 1

    def test_account_ok(self):
        result, ok = CoinoneRequestData._get_account_normalize_function(SAMPLE_ACCOUNT, {})
        assert ok is True

    def test_account_error(self):
        result, ok = CoinoneRequestData._get_account_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_balance_ok(self):
        result, ok = CoinoneRequestData._get_balance_normalize_function(SAMPLE_ACCOUNT, {})
        assert ok is True
        assert "BTC" in result[0]

    def test_balance_error(self):
        result, ok = CoinoneRequestData._get_balance_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_is_error_none(self):
        assert CoinoneRequestData._is_error(None) is True

    def test_is_error_error_result(self):
        assert CoinoneRequestData._is_error(SAMPLE_ERROR) is True

    def test_is_error_ok(self):
        assert CoinoneRequestData._is_error(SAMPLE_TICK) is False


# ═══════════════════════════════════════════════════════════════
# 4) Mocked sync calls
# ═══════════════════════════════════════════════════════════════


class TestSyncCalls:
    @patch.object(CoinoneRequestData, "http_request", return_value=SAMPLE_TICK)
    def test_get_tick(self, mock_http, feed):
        rd = feed.get_tick("KRW-BTC")
        assert isinstance(rd, RequestData)
        mock_http.assert_called_once()

    @patch.object(CoinoneRequestData, "http_request", return_value=SAMPLE_DEPTH)
    def test_get_depth(self, mock_http, feed):
        rd = feed.get_depth("KRW-BTC")
        assert isinstance(rd, RequestData)

    @patch.object(CoinoneRequestData, "http_request", return_value=SAMPLE_KLINE)
    def test_get_kline(self, mock_http, feed):
        rd = feed.get_kline("KRW-BTC", "1h", 10)
        assert isinstance(rd, RequestData)

    @patch.object(CoinoneRequestData, "http_request", return_value=SAMPLE_TRADES)
    def test_get_trade_history(self, mock_http, feed):
        rd = feed.get_trade_history("KRW-BTC")
        assert isinstance(rd, RequestData)

    @patch.object(CoinoneRequestData, "http_request", return_value=SAMPLE_EXCHANGE_INFO)
    def test_get_exchange_info(self, mock_http, feed):
        rd = feed.get_exchange_info()
        assert isinstance(rd, RequestData)

    @patch.object(CoinoneRequestData, "http_request", return_value=SAMPLE_ACCOUNT)
    def test_get_account(self, mock_http, feed):
        rd = feed.get_account()
        assert isinstance(rd, RequestData)

    @patch.object(CoinoneRequestData, "http_request", return_value=SAMPLE_ACCOUNT)
    def test_get_balance(self, mock_http, feed):
        rd = feed.get_balance()
        assert isinstance(rd, RequestData)

    @patch.object(CoinoneRequestData, "http_request", return_value=SAMPLE_ORDER)
    def test_make_order(self, mock_http, feed):
        rd = feed.make_order("KRW-BTC", 0.001, price=50000000)
        assert isinstance(rd, RequestData)

    @patch.object(CoinoneRequestData, "http_request", return_value=SAMPLE_ORDER)
    def test_cancel_order(self, mock_http, feed):
        rd = feed.cancel_order(symbol="KRW-BTC", order_id="abc-123")
        assert isinstance(rd, RequestData)

    @patch.object(CoinoneRequestData, "http_request", return_value=SAMPLE_ORDER)
    def test_query_order(self, mock_http, feed):
        rd = feed.query_order(symbol="KRW-BTC", order_id="abc-123")
        assert isinstance(rd, RequestData)

    @patch.object(CoinoneRequestData, "http_request", return_value=SAMPLE_OPEN_ORDERS)
    def test_get_open_orders(self, mock_http, feed):
        rd = feed.get_open_orders(symbol="KRW-BTC")
        assert isinstance(rd, RequestData)

    @patch.object(CoinoneRequestData, "http_request", return_value=SAMPLE_DEALS)
    def test_get_deals(self, mock_http, feed):
        rd = feed.get_deals(symbol="KRW-BTC")
        assert isinstance(rd, RequestData)


# ═══════════════════════════════════════════════════════════════
# 5) Auth
# ═══════════════════════════════════════════════════════════════


class TestAuth:
    def test_no_auth_without_keys(self, feed):
        headers, payload = feed._generate_auth_headers()
        assert headers == {}

    def test_api_key_property(self, feed):
        assert feed.api_key == ""
        assert feed.api_secret == ""

    def test_auth_with_keys(self):
        f = CoinoneRequestDataSpot(queue.Queue(), api_key="mykey", api_secret="mysecret")
        headers, payload = f._generate_auth_headers({"test": 1})
        assert "X-COINONE-PAYLOAD" in headers
        assert "X-COINONE-SIGNATURE" in headers
        assert len(headers["X-COINONE-SIGNATURE"]) == 128  # SHA-512 hex

    def test_signature_deterministic(self):
        f = CoinoneRequestDataSpot(queue.Queue(), api_key="k", api_secret="s")
        sig1 = f._generate_signature("testpayload")
        sig2 = f._generate_signature("testpayload")
        assert sig1 == sig2
        assert len(sig1) == 128


# ═══════════════════════════════════════════════════════════════
# 6) Registry
# ═══════════════════════════════════════════════════════════════


class TestRegistry:
    def test_feed_registered(self):
        assert "COINONE___SPOT" in ExchangeRegistry._feed_classes

    def test_exchange_data_registered(self):
        assert "COINONE___SPOT" in ExchangeRegistry._exchange_data_classes

    def test_balance_handler_registered(self):
        assert "COINONE___SPOT" in ExchangeRegistry._balance_handlers

    def test_create_exchange_data(self):
        ed = ExchangeRegistry.create_exchange_data("COINONE___SPOT")
        assert isinstance(ed, CoinoneExchangeDataSpot)


# ═══════════════════════════════════════════════════════════════
# 7) Method existence
# ═══════════════════════════════════════════════════════════════

_EXPECTED_METHODS = [
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
    "get_exchange_info",
    "async_get_exchange_info",
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
        assert feed.exchange_name == "COINONE___SPOT"

    def test_default_asset_type(self, feed):
        assert feed.asset_type == "SPOT"

    def test_api_keys(self):
        f = CoinoneRequestDataSpot(queue.Queue(), api_key="ak", api_secret="sk")
        assert f.api_key == "ak"
        assert f.api_secret == "sk"

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
        f = CoinoneRequestDataSpot(queue.Queue())
        rd = f.get_tick("KRW-BTC")
        assert isinstance(rd, RequestData)

    @pytest.mark.skip(reason="Requires network access")
    def test_live_get_depth(self):
        f = CoinoneRequestDataSpot(queue.Queue())
        rd = f.get_depth("KRW-BTC")
        assert isinstance(rd, RequestData)

    @pytest.mark.skip(reason="Requires network access")
    def test_live_get_kline(self):
        f = CoinoneRequestDataSpot(queue.Queue())
        rd = f.get_kline("KRW-BTC", "1h", 5)
        assert isinstance(rd, RequestData)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
