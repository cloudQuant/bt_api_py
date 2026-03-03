"""
Tests for Latoken exchange – Feed pattern.

Run:  pytest tests/feeds/test_latoken.py -v
"""

import queue
from unittest.mock import patch

import pytest

from bt_api_py.containers.exchanges.latoken_exchange_data import LatokenExchangeDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_latoken.request_base import LatokenRequestData
from bt_api_py.feeds.live_latoken.spot import (
    LatokenRequestDataSpot,
    LatokenMarketWssDataSpot,
    LatokenAccountWssDataSpot,
)
from bt_api_py.registry import ExchangeRegistry

import bt_api_py.feeds.register_latoken  # noqa: F401

# ── sample response fixtures ─────────────────────────────────

SAMPLE_TICK = {
    "symbol": "620f2013-f1f0-11e9-9ec2-0242ac130002/0c3a106d-bde3-4c13-a26e-3fd2394529e5",
    "baseCurrency": "620f2013-f1f0-11e9-9ec2-0242ac130002",
    "quoteCurrency": "0c3a106d-bde3-4c13-a26e-3fd2394529e5",
    "volume24h": "123.45", "volume7d": "890.12",
    "change24h": "1.5", "change7d": "3.2",
    "amount24h": "6000000", "amount7d": "40000000",
    "lastPrice": "50000.50", "lastQuantity": "0.01",
    "bestBid": "50000.00", "bestAsk": "50001.00",
    "bestBidQuantity": "0.5", "bestAskQuantity": "0.3",
}

SAMPLE_DEPTH = {
    "ask": [{"price": "50001", "quantity": "0.3", "cost": "15000.3", "accumulated": "0.3"}],
    "bid": [{"price": "50000", "quantity": "0.5", "cost": "25000", "accumulated": "0.5"}],
    "totalAsk": "10.5", "totalBid": "8.3",
}

SAMPLE_PAIRS = [
    {"id": "uuid1", "baseCurrency": "btc_uuid", "quoteCurrency": "usdt_uuid",
     "priceTick": "0.01", "quantityTick": "0.00001"},
]

SAMPLE_DEALS = [
    {"id": "t1", "direction": "BUY", "price": "50000", "quantity": "0.01",
     "cost": "500", "timestamp": 1678901234000},
]

SAMPLE_KLINE = [
    {"time": 1678901234000, "open": 49000, "high": 50500,
     "low": 48800, "close": 50000, "volume": 12.3},
]

SAMPLE_ORDER = {"id": "order_uuid", "status": "PLACED", "side": "BUY"}

SAMPLE_CANCEL = {"id": "order_uuid", "status": "CANCELLED"}

SAMPLE_OPEN_ORDERS = [{"id": "order_uuid", "side": "BUY", "price": "49000", "status": "PLACED"}]

SAMPLE_BALANCE = [
    {"id": "uuid", "currency": "btc_uuid", "available": "0.5",
     "blocked": "0.1", "timestamp": 1678901234000},
]

SAMPLE_SERVER_TIME = {"serverTime": 1678901234000}

SAMPLE_ERROR = {"status": "FAILURE", "error": "BAD_REQUEST", "message": "Invalid request"}


# ── helpers ───────────────────────────────────────────────────

@pytest.fixture
def feed():
    return LatokenRequestDataSpot(queue.Queue())


@pytest.fixture
def exdata():
    return LatokenExchangeDataSpot()


# ═══════════════════════════════════════════════════════════════
# 1) ExchangeData
# ═══════════════════════════════════════════════════════════════

class TestExchangeData:
    def test_exchange_name(self, exdata):
        assert exdata.exchange_name == "LATOKEN___SPOT"

    def test_asset_type(self, exdata):
        assert exdata.asset_type == "SPOT"

    def test_rest_url(self, exdata):
        assert exdata.rest_url == "https://api.latoken.com"

    def test_get_symbol(self, exdata):
        assert LatokenExchangeDataSpot.get_symbol("BTC/USDT") == "btc_usdt"
        assert LatokenExchangeDataSpot.get_symbol("BTC-USDT") == "btc_usdt"
        assert LatokenExchangeDataSpot.get_symbol("btc_usdt") == "btc_usdt"

    def test_get_reverse_symbol(self, exdata):
        assert LatokenExchangeDataSpot.get_reverse_symbol("btc_usdt") == "BTC-USDT"
        assert LatokenExchangeDataSpot.get_reverse_symbol("btc-usdt") == "BTC-USDT"

    def test_get_period(self, exdata):
        assert exdata.get_period("1m") == "1"
        assert exdata.get_period("1h") == "60"
        assert exdata.get_period("1d") == "1D"

    def test_get_reverse_period(self, exdata):
        assert exdata.get_reverse_period("1") == "1m"
        assert exdata.get_reverse_period("60") == "1h"
        assert exdata.get_reverse_period("1D") == "1d"

    def test_kline_periods(self, exdata):
        for k in ("1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"):
            assert k in exdata.kline_periods

    def test_legal_currency(self, exdata):
        for c in ("USDT", "BTC", "ETH", "LA"):
            assert c in exdata.legal_currency

    def test_get_rest_path_ok(self, exdata):
        p = exdata.get_rest_path("get_tick", base="btc", quote="usdt")
        assert "/v2/ticker" in p

    def test_get_rest_path_missing(self, exdata):
        with pytest.raises(ValueError):
            exdata.get_rest_path("nonexistent")

    def test_rest_paths_keys(self, exdata):
        for key in ("get_tick", "get_depth", "get_deals", "get_exchange_info",
                     "get_kline", "make_order", "cancel_order",
                     "get_open_orders", "get_balance", "get_account",
                     "get_server_time", "get_all_tickers", "get_currencies"):
            assert key in exdata.rest_paths


# ═══════════════════════════════════════════════════════════════
# 2) Parameter generation (_get_xxx)
# ═══════════════════════════════════════════════════════════════

class TestParamGeneration:
    def test_get_tick_params(self, feed):
        path, params, extra = feed._get_tick("BTC/USDT")
        assert "/v2/ticker" in path
        assert extra["request_type"] == "get_tick"

    def test_get_depth_params(self, feed):
        path, params, extra = feed._get_depth("BTC/USDT")
        assert "/v2/book" in path
        assert extra["request_type"] == "get_depth"

    def test_get_exchange_info_params(self, feed):
        path, params, extra = feed._get_exchange_info()
        assert "/v2/pair" in path
        assert extra["request_type"] == "get_exchange_info"

    def test_get_deals_params(self, feed):
        path, params, extra = feed._get_deals("ETH/USDT")
        assert "/v2/trade/history" in path
        assert extra["request_type"] == "get_deals"

    def test_get_kline_params(self, feed):
        path, params, extra = feed._get_kline("BTC/USDT", "1h", 50)
        assert "/v2/chart/week" in path
        assert extra["request_type"] == "get_kline"

    def test_get_server_time_params(self, feed):
        path, params, extra = feed._get_server_time()
        assert "/v2/time" in path
        assert extra["request_type"] == "get_server_time"

    def test_make_order_params(self, feed):
        path, body, extra = feed._make_order("BTC/USDT", "buy", "limit", 0.001, price=50000)
        assert "POST" in path
        assert "/auth/order/place" in path
        assert body["baseCurrency"] == "btc"
        assert body["quoteCurrency"] == "usdt"
        assert body["side"] == "BUY"
        assert body["type"] == "LIMIT"
        assert body["quantity"] == "0.001"
        assert body["price"] == "50000"

    def test_cancel_order_params(self, feed):
        path, body, extra = feed._cancel_order("order_uuid")
        assert "/auth/order/cancel" in path
        assert body["id"] == "order_uuid"
        assert extra["request_type"] == "cancel_order"

    def test_get_open_orders_with_symbol(self, feed):
        path, params, extra = feed._get_open_orders("BTC/USDT")
        assert "/auth/order/pair" in path
        assert extra["request_type"] == "get_open_orders"

    def test_get_open_orders_no_symbol(self, feed):
        path, params, extra = feed._get_open_orders()
        assert "/auth/account" in path  # fallback

    def test_get_balance_params(self, feed):
        path, params, extra = feed._get_balance()
        assert "/auth/account" in path
        assert extra["request_type"] == "get_balance"

    def test_get_account_params(self, feed):
        path, params, extra = feed._get_account()
        assert "/auth/account" in path
        assert extra["request_type"] == "get_account"


# ═══════════════════════════════════════════════════════════════
# 3) Normalization functions
# ═══════════════════════════════════════════════════════════════

class TestNormalization:
    def test_tick_ok(self):
        result, ok = LatokenRequestData._get_tick_normalize_function(SAMPLE_TICK, {})
        assert ok is True
        assert result[0]["lastPrice"] == "50000.50"

    def test_tick_error(self):
        result, ok = LatokenRequestData._get_tick_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_depth_ok(self):
        result, ok = LatokenRequestData._get_depth_normalize_function(SAMPLE_DEPTH, {})
        assert ok is True

    def test_exchange_info_ok(self):
        result, ok = LatokenRequestData._get_exchange_info_normalize_function(SAMPLE_PAIRS, {})
        assert ok is True
        assert len(result) == 1

    def test_deals_ok(self):
        result, ok = LatokenRequestData._get_deals_normalize_function(SAMPLE_DEALS, {})
        assert ok is True
        assert len(result) == 1

    def test_kline_ok(self):
        result, ok = LatokenRequestData._get_kline_normalize_function(SAMPLE_KLINE, {})
        assert ok is True

    def test_server_time_ok(self):
        result, ok = LatokenRequestData._get_server_time_normalize_function(SAMPLE_SERVER_TIME, {})
        assert ok is True

    def test_server_time_none(self):
        result, ok = LatokenRequestData._get_server_time_normalize_function(None, {})
        assert ok is False

    def test_make_order_ok(self):
        result, ok = LatokenRequestData._make_order_normalize_function(SAMPLE_ORDER, {})
        assert ok is True

    def test_make_order_error(self):
        result, ok = LatokenRequestData._make_order_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_cancel_order_ok(self):
        result, ok = LatokenRequestData._cancel_order_normalize_function(SAMPLE_CANCEL, {})
        assert ok is True

    def test_open_orders_ok(self):
        result, ok = LatokenRequestData._get_open_orders_normalize_function(SAMPLE_OPEN_ORDERS, {})
        assert ok is True

    def test_balance_ok(self):
        result, ok = LatokenRequestData._get_balance_normalize_function(SAMPLE_BALANCE, {})
        assert ok is True

    def test_account_ok(self):
        result, ok = LatokenRequestData._get_account_normalize_function(SAMPLE_BALANCE, {})
        assert ok is True

    def test_is_error_none(self):
        assert LatokenRequestData._is_error(None) is True

    def test_is_error_failure(self):
        assert LatokenRequestData._is_error(SAMPLE_ERROR) is True

    def test_is_error_ok(self):
        assert LatokenRequestData._is_error(SAMPLE_TICK) is False


# ═══════════════════════════════════════════════════════════════
# 4) Mocked sync calls
# ═══════════════════════════════════════════════════════════════

class TestSyncCalls:
    @patch.object(LatokenRequestData, "http_request", return_value=SAMPLE_TICK)
    def test_get_tick(self, mock_http, feed):
        rd = feed.get_tick("BTC/USDT")
        assert isinstance(rd, RequestData)
        mock_http.assert_called_once()

    @patch.object(LatokenRequestData, "http_request", return_value=SAMPLE_DEPTH)
    def test_get_depth(self, mock_http, feed):
        rd = feed.get_depth("BTC/USDT")
        assert isinstance(rd, RequestData)

    @patch.object(LatokenRequestData, "http_request", return_value=SAMPLE_PAIRS)
    def test_get_exchange_info(self, mock_http, feed):
        rd = feed.get_exchange_info()
        assert isinstance(rd, RequestData)

    @patch.object(LatokenRequestData, "http_request", return_value=SAMPLE_DEALS)
    def test_get_deals(self, mock_http, feed):
        rd = feed.get_deals("BTC/USDT")
        assert isinstance(rd, RequestData)

    @patch.object(LatokenRequestData, "http_request", return_value=SAMPLE_KLINE)
    def test_get_kline(self, mock_http, feed):
        rd = feed.get_kline("BTC/USDT", "1h", 50)
        assert isinstance(rd, RequestData)

    @patch.object(LatokenRequestData, "http_request", return_value=SAMPLE_SERVER_TIME)
    def test_get_server_time(self, mock_http, feed):
        rd = feed.get_server_time()
        assert isinstance(rd, RequestData)

    @patch.object(LatokenRequestData, "http_request", return_value=SAMPLE_ORDER)
    def test_make_order(self, mock_http, feed):
        rd = feed.make_order("BTC/USDT", "buy", "limit", 0.001, price=50000)
        assert isinstance(rd, RequestData)

    @patch.object(LatokenRequestData, "http_request", return_value=SAMPLE_CANCEL)
    def test_cancel_order(self, mock_http, feed):
        rd = feed.cancel_order("order_uuid")
        assert isinstance(rd, RequestData)

    @patch.object(LatokenRequestData, "http_request", return_value=SAMPLE_OPEN_ORDERS)
    def test_get_open_orders(self, mock_http, feed):
        rd = feed.get_open_orders("BTC/USDT")
        assert isinstance(rd, RequestData)

    @patch.object(LatokenRequestData, "http_request", return_value=SAMPLE_BALANCE)
    def test_get_balance(self, mock_http, feed):
        rd = feed.get_balance()
        assert isinstance(rd, RequestData)

    @patch.object(LatokenRequestData, "http_request", return_value=SAMPLE_BALANCE)
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
        assert "X-LA-APIKEY" not in h

    def test_headers_with_key(self):
        f = LatokenRequestDataSpot(queue.Queue(), public_key="mykey", private_key="mysecret")
        h = f._get_headers(method="GET", path="/v2/auth/account")
        assert h["X-LA-APIKEY"] == "mykey"
        assert "X-LA-SIGNATURE" in h
        assert h["X-LA-DIGEST"] == "HMAC-SHA512"

    def test_signature_deterministic(self):
        f = LatokenRequestDataSpot(queue.Queue(), public_key="k", private_key="s")
        sig1 = f._generate_signature("GET", "/v2/time")
        sig2 = f._generate_signature("GET", "/v2/time")
        assert sig1 == sig2
        assert len(sig1) == 128  # SHA-512 hex


# ═══════════════════════════════════════════════════════════════
# 6) Registry
# ═══════════════════════════════════════════════════════════════

class TestRegistry:
    def test_feed_registered(self):
        assert "LATOKEN___SPOT" in ExchangeRegistry._feed_classes

    def test_exchange_data_registered(self):
        assert "LATOKEN___SPOT" in ExchangeRegistry._exchange_data_classes

    def test_balance_handler_registered(self):
        assert "LATOKEN___SPOT" in ExchangeRegistry._balance_handlers

    def test_stream_registered(self):
        stream_handlers = ExchangeRegistry._stream_classes.get("LATOKEN___SPOT", {})
        assert "subscribe" in stream_handlers

    def test_create_exchange_data(self):
        ed = ExchangeRegistry.create_exchange_data("LATOKEN___SPOT")
        assert isinstance(ed, LatokenExchangeDataSpot)


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
    "get_server_time", "async_get_server_time",
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
        assert feed.exchange_name == "LATOKEN___SPOT"

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
        wss = LatokenMarketWssDataSpot(queue.Queue(), topics=[{"topic": "ticker"}])
        wss.start()
        assert wss.running is True
        wss.stop()
        assert wss.running is False

    def test_account_wss_start_stop(self):
        wss = LatokenAccountWssDataSpot(queue.Queue(), topics=[{"topic": "account"}])
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
        f = LatokenRequestDataSpot(queue.Queue())
        rd = f.get_tick("BTC/USDT")
        assert isinstance(rd, RequestData)

    @pytest.mark.skip(reason="Requires network access")
    def test_live_get_exchange_info(self):
        f = LatokenRequestDataSpot(queue.Queue())
        rd = f.get_exchange_info()
        assert isinstance(rd, RequestData)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
