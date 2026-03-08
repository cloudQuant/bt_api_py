"""
Tests for YoBit exchange – Feed pattern.

Run:  pytest tests/feeds/test_yobit.py -v
"""

import queue
from unittest.mock import patch

import pytest

import bt_api_py.exchange_registers.register_yobit  # noqa: F401
from bt_api_py.containers.exchanges.yobit_exchange_data import (
    YobitExchangeData,
    YobitExchangeDataSpot,
)
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_yobit.request_base import YobitRequestData
from bt_api_py.feeds.live_yobit.spot import (
    YobitAccountWssDataSpot,
    YobitMarketWssDataSpot,
    YobitRequestDataSpot,
)
from bt_api_py.registry import ExchangeRegistry

# ── sample fixtures ──────────────────────────────────────────

SAMPLE_TICK = {
    "btc_usd": {"high": 30000, "low": 29000, "last": 29500, "buy": 29400, "sell": 29600, "vol": 100}
}
SAMPLE_DEPTH = {"btc_usd": {"asks": [[29600, 1.0]], "bids": [[29400, 1.0]]}}
SAMPLE_EXCHANGE_INFO = {"server_time": 1678901234, "pairs": {"btc_usd": {"decimal_places": 8}}}
SAMPLE_SERVER_TIME = {"server_time": 1678901234, "pairs": {}}
SAMPLE_BALANCE = {"return": {"funds": {"btc": 0.5, "usd": 1000}}, "success": 1}
SAMPLE_ACCOUNT = {"return": {"funds": {"btc": 0.5}}, "success": 1}
SAMPLE_ERROR = {"error": "Invalid pair name"}
SAMPLE_ORDER = {"return": {"order_id": 12345, "received": 0.1}, "success": 1}
SAMPLE_CANCEL = {"return": {"order_id": 12345, "funds": {}}, "success": 1}
SAMPLE_QUERY = {
    "return": {"12345": {"pair": "btc_usd", "type": "buy", "amount": 0.1}},
    "success": 1,
}


@pytest.fixture
def feed():
    return YobitRequestDataSpot(queue.Queue())


@pytest.fixture
def exdata():
    return YobitExchangeDataSpot()


# ═══════════════════════════════════════════════════════════════
# 1) ExchangeData
# ═══════════════════════════════════════════════════════════════


class TestExchangeData:
    def test_exchange_name(self, exdata):
        assert exdata.exchange_name == "YOBIT___SPOT"

    def test_asset_type(self, exdata):
        assert exdata.asset_type == "SPOT"

    def test_rest_url(self, exdata):
        assert exdata.rest_url == "https://yobit.net"

    def test_wss_url(self, exdata):
        assert exdata.wss_url == "wss://ws.yobit.net"

    def test_base_exchange_name(self):
        d = YobitExchangeData()
        assert d.exchange_name == "YOBIT"

    def test_get_symbol(self):
        assert YobitExchangeDataSpot.get_symbol("BTC/USD") == "btc_usd"
        assert YobitExchangeDataSpot.get_symbol("BTC-USD") == "btc_usd"
        assert YobitExchangeDataSpot.get_symbol("btc_usd") == "btc_usd"

    def test_get_reverse_symbol(self):
        assert YobitExchangeDataSpot.get_reverse_symbol("BTC/USD") == "btc_usd"

    def test_get_period(self, exdata):
        assert exdata.get_period("1h") == "60"
        assert exdata.get_period("1d") == "1d"

    def test_get_reverse_period(self, exdata):
        assert exdata.get_reverse_period("60") == "1h"

    def test_legal_currency(self, exdata):
        for c in ("USD", "USDT", "RUB", "BTC", "ETH", "DOGE"):
            assert c in exdata.legal_currency

    def test_kline_periods(self, exdata):
        for k in ("1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d", "1w"):
            assert k in exdata.kline_periods

    def test_get_rest_path_with_pair(self, exdata):
        p = exdata.get_rest_path("get_tick", pair="btc_usd")
        assert "btc_usd" in p
        assert "ticker" in p

    def test_get_rest_path_missing(self, exdata):
        with pytest.raises(ValueError):
            exdata.get_rest_path("nonexistent")

    def test_rest_paths_keys(self, exdata):
        for key in (
            "get_tick",
            "get_ticker",
            "get_depth",
            "get_exchange_info",
            "get_account",
            "get_balance",
            "get_server_time",
            "make_order",
            "cancel_order",
        ):
            assert key in exdata.rest_paths


# ═══════════════════════════════════════════════════════════════
# 2) Parameter generation (_get_xxx)
# ═══════════════════════════════════════════════════════════════


class TestParamGeneration:
    def test_get_tick_params(self, feed):
        path, params, extra = feed._get_tick("BTC/USD")
        assert "ticker" in path.lower()
        assert "btc_usd" in path
        assert extra["request_type"] == "get_tick"
        assert extra["symbol_name"] == "BTC/USD"
        assert extra["asset_type"] == "SPOT"
        assert extra["exchange_name"] == "YOBIT___SPOT"

    def test_get_depth_params(self, feed):
        path, params, extra = feed._get_depth("BTC/USD", 10)
        assert "depth" in path.lower()
        assert "btc_usd" in path
        assert params["limit"] == 10

    def test_get_exchange_info_params(self, feed):
        path, params, extra = feed._get_exchange_info()
        assert "info" in path.lower()
        assert extra["request_type"] == "get_exchange_info"

    def test_get_server_time_params(self, feed):
        path, params, extra = feed._get_server_time()
        assert "info" in path.lower()
        assert extra["request_type"] == "get_server_time"

    def test_get_balance_params(self, feed):
        path, params, extra = feed._get_balance()
        assert "tapi" in path.lower()
        assert extra["request_type"] == "get_balance"

    def test_get_account_params(self, feed):
        path, params, extra = feed._get_account()
        assert "tapi" in path.lower()
        assert extra["request_type"] == "get_account"
        assert extra["asset_type"] == "SPOT"
        assert extra["exchange_name"] == "YOBIT___SPOT"

    def test_make_order_params(self, feed):
        path, params, extra = feed._make_order("BTC/USD", 0.1, 29500, "buy-limit")
        assert "tapi" in path.lower()
        assert params["method"] == "Trade"
        assert params["pair"] == "btc_usd"
        assert params["type"] == "buy"
        assert params["amount"] == 0.1
        assert extra["request_type"] == "make_order"
        assert extra["symbol_name"] == "BTC/USD"
        assert extra["asset_type"] == "SPOT"

    def test_cancel_order_params(self, feed):
        path, params, extra = feed._cancel_order("BTC/USD", order_id=12345)
        assert params["method"] == "CancelOrder"
        assert params["order_id"] == 12345
        assert extra["request_type"] == "cancel_order"
        assert extra["symbol_name"] == "BTC/USD"

    def test_query_order_params(self, feed):
        path, params, extra = feed._query_order("BTC/USD", order_id=12345)
        assert params["method"] == "OrderInfo"
        assert params["order_id"] == 12345
        assert extra["request_type"] == "query_order"


# ═══════════════════════════════════════════════════════════════
# 3) Normalization functions
# ═══════════════════════════════════════════════════════════════


class TestNormalization:
    def test_tick_ok(self):
        result, ok = YobitRequestData._get_tick_normalize_function(SAMPLE_TICK, {})
        assert ok is True
        assert len(result) > 0

    def test_tick_error(self):
        result, ok = YobitRequestData._get_tick_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_tick_none(self):
        result, ok = YobitRequestData._get_tick_normalize_function(None, {})
        assert ok is False

    def test_depth_ok(self):
        result, ok = YobitRequestData._get_depth_normalize_function(SAMPLE_DEPTH, {})
        assert ok is True

    def test_depth_error(self):
        result, ok = YobitRequestData._get_depth_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_exchange_info_ok(self):
        result, ok = YobitRequestData._get_exchange_info_normalize_function(
            SAMPLE_EXCHANGE_INFO, {}
        )
        assert ok is True

    def test_server_time_ok(self):
        result, ok = YobitRequestData._get_server_time_normalize_function(SAMPLE_SERVER_TIME, {})
        assert ok is True

    def test_server_time_none(self):
        result, ok = YobitRequestData._get_server_time_normalize_function(None, {})
        assert ok is False

    def test_balance_ok(self):
        result, ok = YobitRequestData._get_balance_normalize_function(SAMPLE_BALANCE, {})
        assert ok is True

    def test_account_ok(self):
        result, ok = YobitRequestData._get_account_normalize_function(SAMPLE_ACCOUNT, {})
        assert ok is True

    def test_account_error(self):
        result, ok = YobitRequestData._get_account_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_make_order_ok(self):
        result, ok = YobitRequestData._make_order_normalize_function(SAMPLE_ORDER, {})
        assert ok is True
        assert len(result) == 1

    def test_make_order_error(self):
        result, ok = YobitRequestData._make_order_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_cancel_order_ok(self):
        result, ok = YobitRequestData._cancel_order_normalize_function(SAMPLE_CANCEL, {})
        assert ok is True

    def test_query_order_ok(self):
        result, ok = YobitRequestData._query_order_normalize_function(SAMPLE_QUERY, {})
        assert ok is True

    def test_is_error_none(self):
        assert YobitRequestData._is_error(None) is True

    def test_is_error_with_key(self):
        assert YobitRequestData._is_error(SAMPLE_ERROR) is True

    def test_is_error_ok(self):
        assert YobitRequestData._is_error(SAMPLE_TICK) is False


# ═══════════════════════════════════════════════════════════════
# 4) Mocked sync calls
# ═══════════════════════════════════════════════════════════════


class TestSyncCalls:
    @patch.object(YobitRequestData, "http_request", return_value=SAMPLE_TICK)
    def test_get_tick(self, mock_http, feed):
        rd = feed.get_tick("BTC/USD")
        assert isinstance(rd, RequestData)
        mock_http.assert_called_once()

    @patch.object(YobitRequestData, "http_request", return_value=SAMPLE_TICK)
    def test_get_ticker(self, mock_http, feed):
        rd = feed.get_ticker("BTC/USD")
        assert isinstance(rd, RequestData)

    @patch.object(YobitRequestData, "http_request", return_value=SAMPLE_DEPTH)
    def test_get_depth(self, mock_http, feed):
        rd = feed.get_depth("BTC/USD", 20)
        assert isinstance(rd, RequestData)

    @patch.object(YobitRequestData, "http_request", return_value=SAMPLE_EXCHANGE_INFO)
    def test_get_exchange_info(self, mock_http, feed):
        rd = feed.get_exchange_info()
        assert isinstance(rd, RequestData)

    @patch.object(YobitRequestData, "http_request", return_value=SAMPLE_SERVER_TIME)
    def test_get_server_time(self, mock_http, feed):
        rd = feed.get_server_time()
        assert isinstance(rd, RequestData)

    @patch.object(YobitRequestData, "http_request", return_value=SAMPLE_BALANCE)
    def test_get_balance(self, mock_http, feed):
        rd = feed.get_balance()
        assert isinstance(rd, RequestData)

    @patch.object(YobitRequestData, "http_request", return_value=SAMPLE_ACCOUNT)
    def test_get_account(self, mock_http, feed):
        rd = feed.get_account()
        assert isinstance(rd, RequestData)

    @patch.object(YobitRequestData, "http_request", return_value=SAMPLE_ORDER)
    def test_make_order(self, mock_http, feed):
        rd = feed.make_order("BTC/USD", 0.1, 29500)
        assert isinstance(rd, RequestData)
        mock_http.assert_called_once()

    @patch.object(YobitRequestData, "http_request", return_value=SAMPLE_CANCEL)
    def test_cancel_order(self, mock_http, feed):
        rd = feed.cancel_order("BTC/USD", order_id=12345)
        assert isinstance(rd, RequestData)

    @patch.object(YobitRequestData, "http_request", return_value=SAMPLE_QUERY)
    def test_query_order(self, mock_http, feed):
        rd = feed.query_order("BTC/USD", order_id=12345)
        assert isinstance(rd, RequestData)


# ═══════════════════════════════════════════════════════════════
# 5) Auth
# ═══════════════════════════════════════════════════════════════


class TestAuth:
    def test_headers_no_key(self, feed):
        h = feed._get_headers()
        assert "Content-Type" in h
        assert "Key" not in h

    def test_headers_with_key_tapi(self):
        f = YobitRequestDataSpot(queue.Queue(), public_key="mykey", secret_key="mysecret")
        h = f._get_headers(request_path="/tapi", body="method=getInfo&nonce=1")
        assert h["Key"] == "mykey"
        assert "Sign" in h
        assert len(h["Sign"]) > 0

    def test_headers_with_key_public(self):
        f = YobitRequestDataSpot(queue.Queue(), public_key="mykey", secret_key="mysecret")
        h = f._get_headers(request_path="/api/3/info")
        assert "Key" not in h

    def test_generate_signature_no_secret(self, feed):
        sig = feed._generate_signature("method=getInfo")
        assert sig == ""


# ═══════════════════════════════════════════════════════════════
# 6) Registry
# ═══════════════════════════════════════════════════════════════


class TestRegistry:
    def test_feed_registered(self):
        assert "YOBIT___SPOT" in ExchangeRegistry._feed_classes

    def test_exchange_data_registered(self):
        assert "YOBIT___SPOT" in ExchangeRegistry._exchange_data_classes

    def test_balance_handler_registered(self):
        assert "YOBIT___SPOT" in ExchangeRegistry._balance_handlers

    def test_stream_registered(self):
        stream_handlers = ExchangeRegistry._stream_classes.get("YOBIT___SPOT", {})
        assert "subscribe" in stream_handlers

    def test_create_feed(self):
        f = ExchangeRegistry.create_feed("YOBIT___SPOT", queue.Queue())
        assert isinstance(f, YobitRequestDataSpot)

    def test_create_exchange_data(self):
        ed = ExchangeRegistry.create_exchange_data("YOBIT___SPOT")
        assert isinstance(ed, YobitExchangeDataSpot)


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
    "get_exchange_info",
    "async_get_exchange_info",
    "get_server_time",
    "async_get_server_time",
    "get_balance",
    "async_get_balance",
    "get_account",
    "async_get_account",
    "make_order",
    "async_make_order",
    "cancel_order",
    "async_cancel_order",
    "query_order",
    "async_query_order",
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
        assert feed.exchange_name == "YOBIT___SPOT"

    def test_default_asset_type(self, feed):
        assert feed.asset_type == "SPOT"

    def test_capabilities(self, feed):
        from bt_api_py.feeds.capability import Capability

        caps = feed._capabilities()
        assert Capability.GET_TICK in caps
        assert Capability.GET_DEPTH in caps
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
        wss = YobitMarketWssDataSpot(queue.Queue(), topics=[{"topic": "ticker"}])
        wss.start()
        assert wss.running is True
        wss.stop()
        assert wss.running is False

    def test_account_wss_start_stop(self):
        wss = YobitAccountWssDataSpot(queue.Queue(), topics=[{"topic": "account"}])
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
        f = YobitRequestDataSpot(queue.Queue())
        rd = f.get_tick("btc_usd")
        assert isinstance(rd, RequestData)

    @pytest.mark.skip(reason="Requires network access")
    def test_live_get_exchange_info(self):
        f = YobitRequestDataSpot(queue.Queue())
        rd = f.get_exchange_info()
        assert isinstance(rd, RequestData)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
