"""
Tests for Zebpay exchange – Feed pattern.

Run:  pytest tests/feeds/test_zebpay.py -v
"""

import queue
from unittest.mock import patch

import pytest

from bt_api_py.containers.exchanges.zebpay_exchange_data import (
    ZebpayExchangeData,
    ZebpayExchangeDataSpot,
)
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.containers.tickers.zebpay_ticker import ZebpayRequestTickerData
from bt_api_py.feeds.live_zebpay.request_base import ZebpayRequestData
from bt_api_py.feeds.live_zebpay.spot import (
    ZebpayRequestDataSpot,
    ZebpayMarketWssDataSpot,
    ZebpayAccountWssDataSpot,
)
from bt_api_py.registry import ExchangeRegistry

import bt_api_py.feeds.register_zebpay  # noqa: F401

# ── sample fixtures ──────────────────────────────────────────

SAMPLE_TICK = {"data": {"symbol": "BTC-INR", "last": "5000000", "bid": "4990000", "ask": "5010000"}}
SAMPLE_TICK_RAW = {"last": "5000000", "bid": "4990000", "ask": "5010000"}
SAMPLE_DEPTH = {"data": {"bids": [["4990000", "1.5"]], "asks": [["5010000", "2.0"]]}}
SAMPLE_KLINE = {"data": [[1642696800000, "4900000", "5100000", "4800000", "5000000", "1000"]]}
SAMPLE_EXCHANGE_INFO = {"symbols": [{"symbol": "BTC-INR"}]}
SAMPLE_SERVER_TIME = {"serverTime": 1678901234000}
SAMPLE_BALANCE = {"balances": [{"asset": "BTC", "free": "0.5"}]}
SAMPLE_ACCOUNT = {"accountType": "SPOT", "balances": []}
SAMPLE_ERROR = {"error": "invalid symbol"}
SAMPLE_ORDER = {"orderId": 12345, "symbol": "BTC-INR", "status": "NEW"}
SAMPLE_CANCEL = {"orderId": 12345, "symbol": "BTC-INR", "status": "CANCELED"}
SAMPLE_QUERY = {"orderId": 12345, "symbol": "BTC-INR", "status": "FILLED"}


@pytest.fixture
def feed():
    return ZebpayRequestDataSpot(queue.Queue())


@pytest.fixture
def exdata():
    return ZebpayExchangeDataSpot()


# ═══════════════════════════════════════════════════════════════
# 1) ExchangeData
# ═══════════════════════════════════════════════════════════════

class TestExchangeData:
    def test_exchange_name(self, exdata):
        assert exdata.exchange_name == "ZEBPAY___SPOT"

    def test_asset_type(self, exdata):
        assert exdata.asset_type == "SPOT"

    def test_rest_url(self, exdata):
        assert exdata.rest_url == "https://sapi.zebpay.com"

    def test_wss_url(self, exdata):
        assert exdata.wss_url == "wss://stream.zebpay.com"

    def test_base_exchange_name(self):
        d = ZebpayExchangeData()
        assert d.exchange_name == "ZEBPAY"

    def test_get_symbol(self):
        assert ZebpayExchangeDataSpot.get_symbol("BTC/INR") == "BTC-INR"
        assert ZebpayExchangeDataSpot.get_symbol("btc-inr") == "BTC-INR"
        assert ZebpayExchangeDataSpot.get_symbol("btc_inr") == "BTC-INR"

    def test_get_reverse_symbol(self):
        assert ZebpayExchangeDataSpot.get_reverse_symbol("BTC/INR") == "BTC-INR"

    def test_get_period(self, exdata):
        assert exdata.get_period("1h") == "1h"
        assert exdata.get_period("1d") == "1d"
        assert exdata.get_period("1m") == "1m"

    def test_get_reverse_period(self, exdata):
        assert exdata.get_reverse_period("1h") == "1h"

    def test_legal_currency(self, exdata):
        for c in ("INR", "USDT"):
            assert c in exdata.legal_currency

    def test_kline_periods(self, exdata):
        for k in ("1m", "5m", "15m", "30m", "1h", "2h", "4h", "12h", "1d", "1w"):
            assert k in exdata.kline_periods

    def test_get_rest_path(self, exdata):
        p = exdata.get_rest_path("get_tick")
        assert "ticker" in p

    def test_get_rest_path_missing(self, exdata):
        with pytest.raises(ValueError):
            exdata.get_rest_path("nonexistent")

    def test_rest_paths_keys(self, exdata):
        for key in ("get_tick", "get_ticker", "get_depth", "get_kline",
                     "get_exchange_info", "get_account", "get_balance",
                     "get_server_time", "make_order", "cancel_order"):
            assert key in exdata.rest_paths


# ═══════════════════════════════════════════════════════════════
# 2) Parameter generation (_get_xxx)
# ═══════════════════════════════════════════════════════════════

class TestParamGeneration:
    def test_get_tick_params(self, feed):
        path, params, extra = feed._get_tick("BTC/INR")
        assert "ticker" in path.lower()
        assert params["symbol"] == "BTC-INR"
        assert extra["request_type"] == "get_tick"
        assert extra["symbol_name"] == "BTC/INR"
        assert extra["asset_type"] == "SPOT"
        assert extra["exchange_name"] == "ZEBPAY___SPOT"

    def test_get_depth_params(self, feed):
        path, params, extra = feed._get_depth("ETH/USDT")
        assert "orderbook" in path.lower()
        assert params["symbol"] == "ETH-USDT"

    def test_get_kline_params(self, feed):
        path, params, extra = feed._get_kline("BTC/INR", "1h")
        assert "klines" in path.lower()
        assert params["symbol"] == "BTC-INR"
        assert params["interval"] == "1h"

    def test_get_exchange_info_params(self, feed):
        path, params, extra = feed._get_exchange_info()
        assert "exchangeinfo" in path.lower()
        assert extra["request_type"] == "get_exchange_info"

    def test_get_server_time_params(self, feed):
        path, params, extra = feed._get_server_time()
        assert "time" in path.lower()
        assert extra["request_type"] == "get_server_time"

    def test_get_balance_params(self, feed):
        path, params, extra = feed._get_balance()
        assert "balance" in path.lower()
        assert extra["request_type"] == "get_balance"

    def test_get_account_params(self, feed):
        path, params, extra = feed._get_account()
        assert "account" in path.lower()
        assert extra["request_type"] == "get_account"
        assert extra["asset_type"] == "SPOT"
        assert extra["exchange_name"] == "ZEBPAY___SPOT"

    def test_make_order_params(self, feed):
        path, params, extra = feed._make_order("BTC/INR", 0.1, 5000000, "buy-limit")
        assert "order" in path.lower()
        assert params["symbol"] == "BTC-INR"
        assert params["side"] == "BUY"
        assert params["type"] == "LIMIT"
        assert params["quantity"] == "0.1"
        assert extra["request_type"] == "make_order"
        assert extra["symbol_name"] == "BTC/INR"
        assert extra["asset_type"] == "SPOT"

    def test_cancel_order_params(self, feed):
        path, params, extra = feed._cancel_order("BTC/INR", order_id=12345)
        assert params["symbol"] == "BTC-INR"
        assert params["orderId"] == 12345
        assert extra["request_type"] == "cancel_order"
        assert extra["symbol_name"] == "BTC/INR"

    def test_query_order_params(self, feed):
        path, params, extra = feed._query_order("BTC/INR", order_id=12345)
        assert params["symbol"] == "BTC-INR"
        assert params["orderId"] == 12345
        assert extra["request_type"] == "query_order"


# ═══════════════════════════════════════════════════════════════
# 3) Normalization functions
# ═══════════════════════════════════════════════════════════════

class TestNormalization:
    def test_tick_ok(self):
        result, ok = ZebpayRequestData._get_tick_normalize_function(SAMPLE_TICK, {})
        assert ok is True
        assert len(result) == 1
        assert result[0]["symbol"] == "BTC-INR"

    def test_tick_error(self):
        result, ok = ZebpayRequestData._get_tick_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_tick_none(self):
        result, ok = ZebpayRequestData._get_tick_normalize_function(None, {})
        assert ok is False

    def test_depth_ok(self):
        result, ok = ZebpayRequestData._get_depth_normalize_function(SAMPLE_DEPTH, {})
        assert ok is True

    def test_depth_error(self):
        result, ok = ZebpayRequestData._get_depth_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_kline_dict(self):
        result, ok = ZebpayRequestData._get_kline_normalize_function(SAMPLE_KLINE, {})
        assert ok is True

    def test_kline_error(self):
        result, ok = ZebpayRequestData._get_kline_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_exchange_info_ok(self):
        result, ok = ZebpayRequestData._get_exchange_info_normalize_function(SAMPLE_EXCHANGE_INFO, {})
        assert ok is True

    def test_server_time_ok(self):
        result, ok = ZebpayRequestData._get_server_time_normalize_function(SAMPLE_SERVER_TIME, {})
        assert ok is True

    def test_server_time_none(self):
        result, ok = ZebpayRequestData._get_server_time_normalize_function(None, {})
        assert ok is False

    def test_balance_ok(self):
        result, ok = ZebpayRequestData._get_balance_normalize_function(SAMPLE_BALANCE, {})
        assert ok is True

    def test_account_ok(self):
        result, ok = ZebpayRequestData._get_account_normalize_function(SAMPLE_ACCOUNT, {})
        assert ok is True

    def test_account_error(self):
        result, ok = ZebpayRequestData._get_account_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_make_order_ok(self):
        result, ok = ZebpayRequestData._make_order_normalize_function(SAMPLE_ORDER, {})
        assert ok is True
        assert len(result) == 1

    def test_make_order_error(self):
        result, ok = ZebpayRequestData._make_order_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_cancel_order_ok(self):
        result, ok = ZebpayRequestData._cancel_order_normalize_function(SAMPLE_CANCEL, {})
        assert ok is True

    def test_query_order_ok(self):
        result, ok = ZebpayRequestData._query_order_normalize_function(SAMPLE_QUERY, {})
        assert ok is True

    def test_is_error_none(self):
        assert ZebpayRequestData._is_error(None) is True

    def test_is_error_with_key(self):
        assert ZebpayRequestData._is_error(SAMPLE_ERROR) is True

    def test_is_error_ok(self):
        assert ZebpayRequestData._is_error(SAMPLE_TICK) is False


# ═══════════════════════════════════════════════════════════════
# 4) Mocked sync calls
# ═══════════════════════════════════════════════════════════════

class TestSyncCalls:
    @patch.object(ZebpayRequestData, "http_request", return_value=SAMPLE_TICK)
    def test_get_tick(self, mock_http, feed):
        rd = feed.get_tick("BTC/INR")
        assert isinstance(rd, RequestData)
        mock_http.assert_called_once()

    @patch.object(ZebpayRequestData, "http_request", return_value=SAMPLE_TICK)
    def test_get_ticker(self, mock_http, feed):
        rd = feed.get_ticker("BTC/INR")
        assert isinstance(rd, RequestData)

    @patch.object(ZebpayRequestData, "http_request", return_value=SAMPLE_DEPTH)
    def test_get_depth(self, mock_http, feed):
        rd = feed.get_depth("ETH/USDT")
        assert isinstance(rd, RequestData)

    @patch.object(ZebpayRequestData, "http_request", return_value=SAMPLE_KLINE)
    def test_get_kline(self, mock_http, feed):
        rd = feed.get_kline("BTC/INR", "1h")
        assert isinstance(rd, RequestData)

    @patch.object(ZebpayRequestData, "http_request", return_value=SAMPLE_EXCHANGE_INFO)
    def test_get_exchange_info(self, mock_http, feed):
        rd = feed.get_exchange_info()
        assert isinstance(rd, RequestData)

    @patch.object(ZebpayRequestData, "http_request", return_value=SAMPLE_SERVER_TIME)
    def test_get_server_time(self, mock_http, feed):
        rd = feed.get_server_time()
        assert isinstance(rd, RequestData)

    @patch.object(ZebpayRequestData, "http_request", return_value=SAMPLE_BALANCE)
    def test_get_balance(self, mock_http, feed):
        rd = feed.get_balance()
        assert isinstance(rd, RequestData)

    @patch.object(ZebpayRequestData, "http_request", return_value=SAMPLE_ACCOUNT)
    def test_get_account(self, mock_http, feed):
        rd = feed.get_account()
        assert isinstance(rd, RequestData)

    @patch.object(ZebpayRequestData, "http_request", return_value=SAMPLE_ORDER)
    def test_make_order(self, mock_http, feed):
        rd = feed.make_order("BTC/INR", 0.1, 5000000)
        assert isinstance(rd, RequestData)
        mock_http.assert_called_once()

    @patch.object(ZebpayRequestData, "http_request", return_value=SAMPLE_CANCEL)
    def test_cancel_order(self, mock_http, feed):
        rd = feed.cancel_order("BTC/INR", order_id=12345)
        assert isinstance(rd, RequestData)

    @patch.object(ZebpayRequestData, "http_request", return_value=SAMPLE_QUERY)
    def test_query_order(self, mock_http, feed):
        rd = feed.query_order("BTC/INR", order_id=12345)
        assert isinstance(rd, RequestData)


# ═══════════════════════════════════════════════════════════════
# 5) Auth
# ═══════════════════════════════════════════════════════════════

class TestAuth:
    def test_headers_no_key(self, feed):
        h = feed._get_headers()
        assert "Content-Type" in h
        assert h["Content-Type"] == "application/json"
        assert "X-AUTH-APIKEY" not in h

    def test_headers_with_key_get(self):
        f = ZebpayRequestDataSpot(queue.Queue(), public_key="mykey", secret_key="mysecret")
        h = f._get_headers("GET", params={"symbol": "BTC-INR"})
        assert h["X-AUTH-APIKEY"] == "mykey"
        assert "X-AUTH-SIGNATURE" in h
        assert len(h["X-AUTH-SIGNATURE"]) == 64

    def test_headers_with_key_post(self):
        f = ZebpayRequestDataSpot(queue.Queue(), public_key="mykey", secret_key="mysecret")
        h = f._get_headers("POST", body={"symbol": "BTC-INR", "side": "buy"})
        assert h["X-AUTH-APIKEY"] == "mykey"
        assert "X-AUTH-SIGNATURE" in h
        assert len(h["X-AUTH-SIGNATURE"]) == 64

    def test_generate_signature_no_secret(self, feed):
        sig = feed._generate_signature("symbol=BTC-INR")
        assert sig == ""

    def test_generate_signature_with_secret(self):
        f = ZebpayRequestDataSpot(queue.Queue(), secret_key="testsecret")
        sig = f._generate_signature("symbol=BTC-INR&timestamp=1234567890")
        assert len(sig) == 64


# ═══════════════════════════════════════════════════════════════
# 6) Registry
# ═══════════════════════════════════════════════════════════════

class TestRegistry:
    def test_feed_registered(self):
        assert "ZEBPAY___SPOT" in ExchangeRegistry._feed_classes
        assert ExchangeRegistry._feed_classes["ZEBPAY___SPOT"] == ZebpayRequestDataSpot

    def test_exchange_data_registered(self):
        assert "ZEBPAY___SPOT" in ExchangeRegistry._exchange_data_classes
        assert ExchangeRegistry._exchange_data_classes["ZEBPAY___SPOT"] == ZebpayExchangeDataSpot

    def test_balance_handler_registered(self):
        assert "ZEBPAY___SPOT" in ExchangeRegistry._balance_handlers

    def test_stream_registered(self):
        stream_handlers = ExchangeRegistry._stream_classes.get("ZEBPAY___SPOT", {})
        assert "subscribe" in stream_handlers

    def test_create_feed(self):
        f = ExchangeRegistry.create_feed("ZEBPAY___SPOT", queue.Queue())
        assert isinstance(f, ZebpayRequestDataSpot)

    def test_create_exchange_data(self):
        ed = ExchangeRegistry.create_exchange_data("ZEBPAY___SPOT")
        assert isinstance(ed, ZebpayExchangeDataSpot)


# ═══════════════════════════════════════════════════════════════
# 7) Method existence
# ═══════════════════════════════════════════════════════════════

_EXPECTED_METHODS = [
    "get_tick", "async_get_tick",
    "get_ticker", "async_get_ticker",
    "get_depth", "async_get_depth",
    "get_kline", "async_get_kline",
    "get_exchange_info", "async_get_exchange_info",
    "get_server_time", "async_get_server_time",
    "get_balance", "async_get_balance",
    "get_account", "async_get_account",
    "make_order", "async_make_order",
    "cancel_order", "async_cancel_order",
    "query_order", "async_query_order",
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
        assert feed.exchange_name == "ZEBPAY___SPOT"

    def test_default_asset_type(self, feed):
        assert feed.asset_type == "SPOT"

    def test_capabilities(self, feed):
        from bt_api_py.feeds.capability import Capability
        caps = feed._capabilities()
        assert Capability.GET_TICK in caps
        assert Capability.GET_DEPTH in caps
        assert Capability.GET_KLINE in caps
        assert Capability.GET_EXCHANGE_INFO in caps
        assert Capability.MAKE_ORDER in caps
        assert Capability.CANCEL_ORDER in caps
        assert Capability.QUERY_ORDER in caps

    def test_push_data_to_queue(self, feed):
        feed.push_data_to_queue({"test": 1})
        assert not feed.data_queue.empty()


# ═══════════════════════════════════════════════════════════════
# 9) WebSocket stubs
# ═══════════════════════════════════════════════════════════════

class TestWebSocketStubs:
    def test_market_wss_start_stop(self):
        wss = ZebpayMarketWssDataSpot(queue.Queue(), topics=[{"topic": "ticker"}])
        wss.start()
        assert wss.running is True
        wss.stop()
        assert wss.running is False

    def test_account_wss_start_stop(self):
        wss = ZebpayAccountWssDataSpot(queue.Queue(), topics=[{"topic": "account"}])
        wss.start()
        assert wss.running is True
        wss.stop()
        assert wss.running is False


# ═══════════════════════════════════════════════════════════════
# 10) Data containers
# ═══════════════════════════════════════════════════════════════

class TestDataContainers:
    def test_ticker_float_parsing(self):
        assert ZebpayRequestTickerData._parse_float("5000000") == 5000000.0
        assert ZebpayRequestTickerData._parse_float(5000000) == 5000000.0
        assert ZebpayRequestTickerData._parse_float(None) is None
        assert ZebpayRequestTickerData._parse_float("invalid") is None

    def test_ticker_class_methods(self):
        assert hasattr(ZebpayRequestTickerData, '_parse_float')
        assert hasattr(ZebpayRequestTickerData, 'init_data')


# ═══════════════════════════════════════════════════════════════
# 11) Integration (skipped)
# ═══════════════════════════════════════════════════════════════

class TestIntegration:
    @pytest.mark.skip(reason="Requires network access")
    def test_live_get_tick(self):
        f = ZebpayRequestDataSpot(queue.Queue())
        rd = f.get_tick("BTC-INR")
        assert isinstance(rd, RequestData)

    @pytest.mark.skip(reason="Requires network access")
    def test_live_get_depth(self):
        f = ZebpayRequestDataSpot(queue.Queue())
        rd = f.get_depth("BTC-INR")
        assert isinstance(rd, RequestData)

    @pytest.mark.skip(reason="Requires network access")
    def test_live_get_kline(self):
        f = ZebpayRequestDataSpot(queue.Queue())
        rd = f.get_kline("BTC-INR", period="1h", count=10)
        assert isinstance(rd, RequestData)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
