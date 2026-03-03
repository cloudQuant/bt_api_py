"""
Tests for Korbit exchange – Feed pattern.

Run:  pytest tests/feeds/test_korbit.py -v
"""

import queue
from unittest.mock import patch

import pytest

from bt_api_py.containers.exchanges.korbit_exchange_data import KorbitExchangeDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_korbit.request_base import KorbitRequestData
from bt_api_py.feeds.live_korbit.spot import (
    KorbitRequestDataSpot,
    KorbitMarketWssDataSpot,
    KorbitAccountWssDataSpot,
)
from bt_api_py.registry import ExchangeRegistry

import bt_api_py.feeds.register_korbit  # noqa: F401

# ── sample response fixtures ─────────────────────────────────

SAMPLE_TICK = {
    "last": "95000000", "bid": "94990000", "ask": "95010000",
    "low": "93500000", "high": "95800000", "volume": "1234.56",
    "timestamp": 1678901234000,
}

SAMPLE_DEPTH = {
    "timestamp": 1678901234000,
    "bids": [["94990000", "0.5", "1"]], "asks": [["95010000", "0.3", "1"]],
}

SAMPLE_CONSTANTS = {
    "exchange": {"minKrwDeposit": "5000"},
    "tradeFees": {"btcKrwFee": "0.0025"},
}

SAMPLE_DEALS = [
    {"timestamp": 1678901234000, "price": "95000000", "amount": "0.01", "tid": "1"},
]

SAMPLE_KLINE = [
    {"time": 1678901234000, "open": 94000000, "high": 95000000,
     "low": 93500000, "close": 94800000, "volume": 12.3},
]

SAMPLE_ORDER = {"orderId": "12345", "status": "success"}

SAMPLE_CANCEL = {"orderId": "12345", "status": "success"}

SAMPLE_OPEN_ORDERS = [{"orderId": "12345", "side": "buy", "price": "94000000"}]

SAMPLE_BALANCE = {
    "krw": {"available": "1000000", "trade_in_use": "500000", "total": "1500000"},
    "btc": {"available": "0.5", "trade_in_use": "0.1", "total": "0.6"},
}

SAMPLE_ERROR = {"errorCode": 1, "errorMessage": "Invalid request"}


# ── helpers ───────────────────────────────────────────────────

@pytest.fixture
def feed():
    return KorbitRequestDataSpot(queue.Queue())


@pytest.fixture
def exdata():
    return KorbitExchangeDataSpot()


# ═══════════════════════════════════════════════════════════════
# 1) ExchangeData
# ═══════════════════════════════════════════════════════════════

class TestExchangeData:
    def test_exchange_name(self, exdata):
        assert exdata.exchange_name == "KORBIT___SPOT"

    def test_asset_type(self, exdata):
        assert exdata.asset_type == "SPOT"

    def test_rest_url(self, exdata):
        assert exdata.rest_url == "https://api.korbit.co.kr"

    def test_get_symbol(self, exdata):
        assert KorbitExchangeDataSpot.get_symbol("BTC/KRW") == "btc_krw"
        assert KorbitExchangeDataSpot.get_symbol("BTC-KRW") == "btc_krw"
        assert KorbitExchangeDataSpot.get_symbol("btc_krw") == "btc_krw"

    def test_get_reverse_symbol(self, exdata):
        assert KorbitExchangeDataSpot.get_reverse_symbol("btc_krw") == "BTC-KRW"

    def test_get_period(self, exdata):
        assert exdata.get_period("1h") == "1h"
        assert exdata.get_period("1d") == "1d"

    def test_kline_periods(self, exdata):
        for k in ("1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d"):
            assert k in exdata.kline_periods

    def test_legal_currency(self, exdata):
        for c in ("KRW", "BTC", "ETH"):
            assert c in exdata.legal_currency

    def test_get_rest_path_ok(self, exdata):
        p = exdata.get_rest_path("get_tick")
        assert "/v1/ticker" in p

    def test_get_rest_path_missing(self, exdata):
        with pytest.raises(ValueError):
            exdata.get_rest_path("nonexistent")

    def test_rest_paths_keys(self, exdata):
        for key in ("get_tick", "get_depth", "get_deals", "get_exchange_info",
                     "get_kline", "make_order", "make_order_sell", "cancel_order",
                     "get_open_orders", "get_account", "get_balance"):
            assert key in exdata.rest_paths


# ═══════════════════════════════════════════════════════════════
# 2) Parameter generation (_get_xxx)
# ═══════════════════════════════════════════════════════════════

class TestParamGeneration:
    def test_get_tick_params(self, feed):
        path, params, extra = feed._get_tick("BTC/KRW")
        assert "/v1/ticker" in path
        assert params["currency_pair"] == "btc_krw"
        assert extra["request_type"] == "get_tick"

    def test_get_depth_params(self, feed):
        path, params, extra = feed._get_depth("BTC/KRW")
        assert "/v1/orderbook" in path
        assert params["currency_pair"] == "btc_krw"
        assert extra["request_type"] == "get_depth"

    def test_get_exchange_info_params(self, feed):
        path, params, extra = feed._get_exchange_info()
        assert "/v1/constants" in path
        assert extra["request_type"] == "get_exchange_info"

    def test_get_deals_params(self, feed):
        path, params, extra = feed._get_deals("ETH/KRW")
        assert "/v1/transactions" in path
        assert params["currency_pair"] == "eth_krw"
        assert extra["request_type"] == "get_deals"

    def test_get_kline_params(self, feed):
        path, params, extra = feed._get_kline("BTC/KRW", "1h", 50)
        assert "/v1/chart" in path
        assert params["currency_pair"] == "btc_krw"
        assert params["timeUnit"] == "1h"
        assert params["count"] == 50

    def test_make_order_buy(self, feed):
        path, body, extra = feed._make_order("BTC/KRW", "buy", "limit", 0.001, price=95000000)
        assert "POST" in path
        assert "/user/orders/buy" in path
        assert body["currency_pair"] == "btc_krw"
        assert body["coin_amount"] == 0.001
        assert body["price"] == 95000000

    def test_make_order_sell(self, feed):
        path, body, extra = feed._make_order("BTC/KRW", "sell", "limit", 0.001, price=95000000)
        assert "/user/orders/sell" in path

    def test_cancel_order_params(self, feed):
        path, body, extra = feed._cancel_order("12345")
        assert "/cancel" in path.lower()
        assert body["id"] == "12345"
        assert extra["request_type"] == "cancel_order"

    def test_get_open_orders_with_symbol(self, feed):
        path, params, extra = feed._get_open_orders("BTC/KRW")
        assert "/user/orders/open" in path
        assert params["currency_pair"] == "btc_krw"

    def test_get_open_orders_no_symbol(self, feed):
        path, params, extra = feed._get_open_orders()
        assert "currency_pair" not in params

    def test_get_balance_params(self, feed):
        path, params, extra = feed._get_balance()
        assert "/user/balances" in path
        assert extra["request_type"] == "get_balance"

    def test_get_account_params(self, feed):
        path, params, extra = feed._get_account()
        assert "/user/balances" in path
        assert extra["request_type"] == "get_account"


# ═══════════════════════════════════════════════════════════════
# 3) Normalization functions
# ═══════════════════════════════════════════════════════════════

class TestNormalization:
    def test_tick_ok(self):
        result, ok = KorbitRequestData._get_tick_normalize_function(SAMPLE_TICK, {})
        assert ok is True
        assert result[0]["last"] == "95000000"

    def test_tick_error(self):
        result, ok = KorbitRequestData._get_tick_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_depth_ok(self):
        result, ok = KorbitRequestData._get_depth_normalize_function(SAMPLE_DEPTH, {})
        assert ok is True

    def test_exchange_info_ok(self):
        result, ok = KorbitRequestData._get_exchange_info_normalize_function(SAMPLE_CONSTANTS, {})
        assert ok is True

    def test_deals_ok(self):
        result, ok = KorbitRequestData._get_deals_normalize_function(SAMPLE_DEALS, {})
        assert ok is True
        assert len(result) == 1

    def test_kline_ok(self):
        result, ok = KorbitRequestData._get_kline_normalize_function(SAMPLE_KLINE, {})
        assert ok is True
        assert len(result) == 1

    def test_make_order_ok(self):
        result, ok = KorbitRequestData._make_order_normalize_function(SAMPLE_ORDER, {})
        assert ok is True

    def test_make_order_error(self):
        result, ok = KorbitRequestData._make_order_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_cancel_order_ok(self):
        result, ok = KorbitRequestData._cancel_order_normalize_function(SAMPLE_CANCEL, {})
        assert ok is True

    def test_cancel_order_empty(self):
        result, ok = KorbitRequestData._cancel_order_normalize_function({}, {})
        assert ok is True

    def test_open_orders_ok(self):
        result, ok = KorbitRequestData._get_open_orders_normalize_function(SAMPLE_OPEN_ORDERS, {})
        assert ok is True

    def test_balance_ok(self):
        result, ok = KorbitRequestData._get_balance_normalize_function(SAMPLE_BALANCE, {})
        assert ok is True

    def test_account_ok(self):
        result, ok = KorbitRequestData._get_account_normalize_function(SAMPLE_BALANCE, {})
        assert ok is True

    def test_is_error_none(self):
        assert KorbitRequestData._is_error(None) is True

    def test_is_error_code(self):
        assert KorbitRequestData._is_error(SAMPLE_ERROR) is True

    def test_is_error_ok(self):
        assert KorbitRequestData._is_error(SAMPLE_TICK) is False


# ═══════════════════════════════════════════════════════════════
# 4) Mocked sync calls
# ═══════════════════════════════════════════════════════════════

class TestSyncCalls:
    @patch.object(KorbitRequestData, "http_request", return_value=SAMPLE_TICK)
    def test_get_tick(self, mock_http, feed):
        rd = feed.get_tick("BTC/KRW")
        assert isinstance(rd, RequestData)
        mock_http.assert_called_once()

    @patch.object(KorbitRequestData, "http_request", return_value=SAMPLE_DEPTH)
    def test_get_depth(self, mock_http, feed):
        rd = feed.get_depth("BTC/KRW")
        assert isinstance(rd, RequestData)

    @patch.object(KorbitRequestData, "http_request", return_value=SAMPLE_CONSTANTS)
    def test_get_exchange_info(self, mock_http, feed):
        rd = feed.get_exchange_info()
        assert isinstance(rd, RequestData)

    @patch.object(KorbitRequestData, "http_request", return_value=SAMPLE_DEALS)
    def test_get_deals(self, mock_http, feed):
        rd = feed.get_deals("BTC/KRW")
        assert isinstance(rd, RequestData)

    @patch.object(KorbitRequestData, "http_request", return_value=SAMPLE_KLINE)
    def test_get_kline(self, mock_http, feed):
        rd = feed.get_kline("BTC/KRW", "1h", 50)
        assert isinstance(rd, RequestData)

    @patch.object(KorbitRequestData, "http_request", return_value=SAMPLE_ORDER)
    def test_make_order(self, mock_http, feed):
        rd = feed.make_order("BTC/KRW", "buy", "limit", 0.001, price=95000000)
        assert isinstance(rd, RequestData)

    @patch.object(KorbitRequestData, "http_request", return_value=SAMPLE_CANCEL)
    def test_cancel_order(self, mock_http, feed):
        rd = feed.cancel_order("12345")
        assert isinstance(rd, RequestData)

    @patch.object(KorbitRequestData, "http_request", return_value=SAMPLE_OPEN_ORDERS)
    def test_get_open_orders(self, mock_http, feed):
        rd = feed.get_open_orders()
        assert isinstance(rd, RequestData)

    @patch.object(KorbitRequestData, "http_request", return_value=SAMPLE_BALANCE)
    def test_get_balance(self, mock_http, feed):
        rd = feed.get_balance()
        assert isinstance(rd, RequestData)

    @patch.object(KorbitRequestData, "http_request", return_value=SAMPLE_BALANCE)
    def test_get_account(self, mock_http, feed):
        rd = feed.get_account()
        assert isinstance(rd, RequestData)


# ═══════════════════════════════════════════════════════════════
# 5) Auth
# ═══════════════════════════════════════════════════════════════

class TestAuth:
    def test_headers_no_key(self, feed):
        h = feed._get_headers()
        assert h["Content-Type"] == "application/json"
        assert "Authorization" not in h

    def test_headers_with_key(self):
        f = KorbitRequestDataSpot(queue.Queue(), public_key="mytoken")
        h = f._get_headers()
        assert h["Authorization"] == "Bearer mytoken"


# ═══════════════════════════════════════════════════════════════
# 6) Registry
# ═══════════════════════════════════════════════════════════════

class TestRegistry:
    def test_feed_registered(self):
        assert "KORBIT___SPOT" in ExchangeRegistry._feed_classes

    def test_exchange_data_registered(self):
        assert "KORBIT___SPOT" in ExchangeRegistry._exchange_data_classes

    def test_balance_handler_registered(self):
        assert "KORBIT___SPOT" in ExchangeRegistry._balance_handlers

    def test_stream_registered(self):
        stream_handlers = ExchangeRegistry._stream_classes.get("KORBIT___SPOT", {})
        assert "subscribe" in stream_handlers

    def test_create_exchange_data(self):
        ed = ExchangeRegistry.create_exchange_data("KORBIT___SPOT")
        assert isinstance(ed, KorbitExchangeDataSpot)


# ═══════════════════════════════════════════════════════════════
# 7) Method existence
# ═══════════════════════════════════════════════════════════════

_EXPECTED_METHODS = [
    "get_tick", "async_get_tick",
    "get_ticker", "async_get_ticker",
    "get_depth", "async_get_depth",
    "get_exchange_info", "async_get_exchange_info",
    "get_deals", "async_get_deals",
    "get_recent_trades", "async_get_recent_trades",
    "get_kline", "async_get_kline",
    "make_order", "async_make_order",
    "cancel_order", "async_cancel_order",
    "get_open_orders", "async_get_open_orders",
    "get_balance", "async_get_balance",
    "get_account", "async_get_account",
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
        assert feed.exchange_name == "KORBIT___SPOT"

    def test_default_asset_type(self, feed):
        assert feed.asset_type == "SPOT"

    def test_capabilities(self, feed):
        caps = feed._capabilities()
        assert len(caps) > 0

    def test_push_data_to_queue(self, feed):
        feed.push_data_to_queue({"test": 1})
        assert not feed.data_queue.empty()


# ═══════════════════════════════════════════════════════════════
# 9) WebSocket stubs
# ═══════════════════════════════════════════════════════════════

class TestWebSocketStubs:
    def test_market_wss_start_stop(self):
        wss = KorbitMarketWssDataSpot(queue.Queue(), topics=[{"topic": "ticker"}])
        wss.start()
        assert wss.running is True
        wss.stop()
        assert wss.running is False

    def test_account_wss_start_stop(self):
        wss = KorbitAccountWssDataSpot(queue.Queue(), topics=[{"topic": "account"}])
        wss.start()
        assert wss.running is True
        wss.stop()
        assert wss.running is False


# ═══════════════════════════════════════════════════════════════
# 10) Integration (skipped)
# ═══════════════════════════════════════════════════════════════

class TestIntegration:
    @pytest.mark.skip(reason="Requires network access")
    def test_live_get_tick(self):
        f = KorbitRequestDataSpot(queue.Queue())
        rd = f.get_tick("BTC/KRW")
        assert isinstance(rd, RequestData)

    @pytest.mark.skip(reason="Requires network access")
    def test_live_get_exchange_info(self):
        f = KorbitRequestDataSpot(queue.Queue())
        rd = f.get_exchange_info()
        assert isinstance(rd, RequestData)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
