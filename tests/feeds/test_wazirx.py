"""
Tests for WazirX exchange – Feed pattern.

Run:  pytest tests/feeds/test_wazirx.py -v
"""

import queue
from unittest.mock import patch

import pytest

import bt_api_py.exchange_registers.register_wazirx  # noqa: F401
from bt_api_py.containers.exchanges.wazirx_exchange_data import (
    WazirxExchangeData,
    WazirxExchangeDataSpot,
)
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_wazirx.request_base import WazirxRequestData
from bt_api_py.feeds.live_wazirx.spot import (
    WazirxAccountWssDataSpot,
    WazirxMarketWssDataSpot,
    WazirxRequestDataSpot,
)
from bt_api_py.registry import ExchangeRegistry

# ── sample fixtures ──────────────────────────────────────────

SAMPLE_TICK = {
    "symbol": "btcinr",
    "lastPrice": "5000000",
    "bidPrice": "4999000",
    "askPrice": "5001000",
}
SAMPLE_DEPTH = {"asks": [["5001000", "0.5"]], "bids": [["4999000", "0.5"]]}
SAMPLE_KLINE = [["1678901234000", "5000000", "5010000", "4990000", "5005000", "100"]]
SAMPLE_EXCHANGE_INFO = {"symbols": [{"symbol": "btcinr", "baseAsset": "btc", "quoteAsset": "inr"}]}
SAMPLE_SERVER_TIME = {"serverTime": 1678901234000}
SAMPLE_BALANCE = [{"asset": "inr", "free": "100000", "locked": "0"}]
SAMPLE_ACCOUNT = {"balances": [{"asset": "btc", "free": "0.5", "locked": "0"}]}
SAMPLE_ERROR = {"code": -1000, "message": "Bad request"}


@pytest.fixture
def feed():
    return WazirxRequestDataSpot(queue.Queue())


@pytest.fixture
def exdata():
    return WazirxExchangeDataSpot()


# ═══════════════════════════════════════════════════════════════
# 1) ExchangeData
# ═══════════════════════════════════════════════════════════════


class TestExchangeData:
    def test_exchange_name(self, exdata):
        assert exdata.exchange_name == "WAZIRX___SPOT"

    def test_asset_type(self, exdata):
        assert exdata.asset_type == "SPOT"

    def test_rest_url(self, exdata):
        assert exdata.rest_url == "https://api.wazirx.com"

    def test_wss_url(self, exdata):
        assert exdata.wss_url == "wss://stream.wazirx.com/stream"

    def test_base_exchange_name(self):
        d = WazirxExchangeData()
        assert d.exchange_name == "WAZIRX"

    def test_get_symbol(self):
        assert WazirxExchangeDataSpot.get_symbol("BTC/INR") == "btcinr"
        assert WazirxExchangeDataSpot.get_symbol("BTC_INR") == "btcinr"
        assert WazirxExchangeDataSpot.get_symbol("BTC-INR") == "btcinr"
        assert WazirxExchangeDataSpot.get_symbol("btcinr") == "btcinr"

    def test_get_reverse_symbol(self):
        assert WazirxExchangeDataSpot.get_reverse_symbol("BTC_INR") == "btcinr"

    def test_get_period(self, exdata):
        assert exdata.get_period("1h") == "1h"
        assert exdata.get_period("1d") == "1d"

    def test_get_reverse_period(self, exdata):
        assert exdata.get_reverse_period("1h") == "1h"

    def test_legal_currency(self, exdata):
        for c in ("INR", "USDT", "WRX", "BTC", "ETH"):
            assert c in exdata.legal_currency

    @pytest.mark.kline
    def test_kline_periods(self, exdata):
        for k in ("1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d", "1w"):
            assert k in exdata.kline_periods

    def test_get_rest_path_ok(self, exdata):
        p = exdata.get_rest_path("get_tick")
        assert "ticker" in p

    def test_get_rest_path_missing(self, exdata):
        with pytest.raises(ValueError):
            exdata.get_rest_path("nonexistent")

    def test_rest_paths_keys(self, exdata):
        for key in (
            "get_tick",
            "get_ticker",
            "get_depth",
            "get_kline",
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
    @pytest.mark.ticker
    def test_get_tick_params(self, feed):
        path, params, extra = feed._get_tick("btcinr")
        assert "ticker" in path.lower()
        assert params["symbol"] == "btcinr"
        assert extra["request_type"] == "get_tick"

    @pytest.mark.orderbook
    def test_get_depth_params(self, feed):
        path, params, extra = feed._get_depth("btcinr", 10)
        assert "depth" in path.lower()
        assert params["symbol"] == "btcinr"
        assert params["limit"] == 10

    @pytest.mark.kline
    def test_get_kline_params(self, feed):
        path, params, extra = feed._get_kline("btcinr", "1h", 50)
        assert "kline" in path.lower()
        assert params["symbol"] == "btcinr"
        assert params["interval"] == "1h"
        assert params["limit"] == 50

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
        assert "funds" in path.lower()
        assert extra["request_type"] == "get_balance"

    def test_get_account_params(self, feed):
        path, params, extra = feed._get_account()
        assert "account" in path.lower()
        assert extra["request_type"] == "get_account"


# ═══════════════════════════════════════════════════════════════
# 3) Normalization functions
# ═══════════════════════════════════════════════════════════════


class TestNormalization:
    @pytest.mark.ticker
    def test_tick_ok(self):
        result, ok = WazirxRequestData._get_tick_normalize_function(SAMPLE_TICK, {})
        assert ok is True
        assert len(result) > 0

    @pytest.mark.ticker
    def test_tick_error(self):
        result, ok = WazirxRequestData._get_tick_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    @pytest.mark.ticker
    def test_tick_none(self):
        result, ok = WazirxRequestData._get_tick_normalize_function(None, {})
        assert ok is False

    @pytest.mark.orderbook
    def test_depth_ok(self):
        result, ok = WazirxRequestData._get_depth_normalize_function(SAMPLE_DEPTH, {})
        assert ok is True

    @pytest.mark.orderbook
    def test_depth_error(self):
        result, ok = WazirxRequestData._get_depth_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    @pytest.mark.kline
    def test_kline_list(self):
        result, ok = WazirxRequestData._get_kline_normalize_function(SAMPLE_KLINE, {})
        assert ok is True

    @pytest.mark.kline
    def test_kline_none(self):
        result, ok = WazirxRequestData._get_kline_normalize_function(None, {})
        assert ok is False

    def test_exchange_info_dict(self):
        result, ok = WazirxRequestData._get_exchange_info_normalize_function(
            SAMPLE_EXCHANGE_INFO, {}
        )
        assert ok is True

    def test_server_time_ok(self):
        result, ok = WazirxRequestData._get_server_time_normalize_function(SAMPLE_SERVER_TIME, {})
        assert ok is True

    def test_server_time_none(self):
        result, ok = WazirxRequestData._get_server_time_normalize_function(None, {})
        assert ok is False

    def test_balance_ok(self):
        result, ok = WazirxRequestData._get_balance_normalize_function(SAMPLE_BALANCE, {})
        assert ok is True

    def test_account_ok(self):
        result, ok = WazirxRequestData._get_account_normalize_function(SAMPLE_ACCOUNT, {})
        assert ok is True

    def test_account_error(self):
        result, ok = WazirxRequestData._get_account_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_is_error_none(self):
        assert WazirxRequestData._is_error(None) is True

    def test_is_error_with_code(self):
        assert WazirxRequestData._is_error(SAMPLE_ERROR) is True

    def test_is_error_ok(self):
        assert WazirxRequestData._is_error(SAMPLE_TICK) is False


# ═══════════════════════════════════════════════════════════════
# 4) Mocked sync calls
# ═══════════════════════════════════════════════════════════════


class TestSyncCalls:
    @patch.object(WazirxRequestData, "http_request", return_value=SAMPLE_TICK)
    @pytest.mark.ticker
    def test_get_tick(self, mock_http, feed):
        rd = feed.get_tick("btcinr")
        assert isinstance(rd, RequestData)
        mock_http.assert_called_once()

    @patch.object(WazirxRequestData, "http_request", return_value=SAMPLE_TICK)
    @pytest.mark.ticker
    def test_get_ticker(self, mock_http, feed):
        rd = feed.get_ticker("btcinr")
        assert isinstance(rd, RequestData)

    @patch.object(WazirxRequestData, "http_request", return_value=SAMPLE_DEPTH)
    @pytest.mark.orderbook
    def test_get_depth(self, mock_http, feed):
        rd = feed.get_depth("btcinr", 20)
        assert isinstance(rd, RequestData)

    @patch.object(WazirxRequestData, "http_request", return_value=SAMPLE_KLINE)
    @pytest.mark.kline
    def test_get_kline(self, mock_http, feed):
        rd = feed.get_kline("btcinr", "1h")
        assert isinstance(rd, RequestData)

    @patch.object(WazirxRequestData, "http_request", return_value=SAMPLE_EXCHANGE_INFO)
    def test_get_exchange_info(self, mock_http, feed):
        rd = feed.get_exchange_info()
        assert isinstance(rd, RequestData)

    @patch.object(WazirxRequestData, "http_request", return_value=SAMPLE_SERVER_TIME)
    def test_get_server_time(self, mock_http, feed):
        rd = feed.get_server_time()
        assert isinstance(rd, RequestData)

    @patch.object(WazirxRequestData, "http_request", return_value=SAMPLE_BALANCE)
    def test_get_balance(self, mock_http, feed):
        rd = feed.get_balance()
        assert isinstance(rd, RequestData)

    @patch.object(WazirxRequestData, "http_request", return_value=SAMPLE_ACCOUNT)
    def test_get_account(self, mock_http, feed):
        rd = feed.get_account()
        assert isinstance(rd, RequestData)


# ═══════════════════════════════════════════════════════════════
# 5) Auth
# ═══════════════════════════════════════════════════════════════


class TestAuth:
    def test_headers_no_key(self, feed):
        h = feed._get_headers()
        assert "Content-Type" in h
        assert "X-API-KEY" not in h

    def test_headers_with_key(self):
        f = WazirxRequestDataSpot(queue.Queue(), public_key="mykey", secret_key="mysecret")
        h = f._get_headers()
        assert h["X-API-KEY"] == "mykey"

    def test_generate_signature_no_secret(self, feed):
        sig = feed._generate_signature("timestamp=12345")
        assert sig == ""

    def test_generate_signature_with_secret(self):
        f = WazirxRequestDataSpot(queue.Queue(), secret_key="testsecret")
        sig = f._generate_signature("timestamp=12345")
        assert len(sig) > 0


# ═══════════════════════════════════════════════════════════════
# 6) Registry
# ═══════════════════════════════════════════════════════════════


class TestRegistry:
    def test_feed_registered(self):
        assert "WAZIRX___SPOT" in ExchangeRegistry._feed_classes

    def test_exchange_data_registered(self):
        assert "WAZIRX___SPOT" in ExchangeRegistry._exchange_data_classes

    def test_balance_handler_registered(self):
        assert "WAZIRX___SPOT" in ExchangeRegistry._balance_handlers

    def test_stream_registered(self):
        stream_handlers = ExchangeRegistry._stream_classes.get("WAZIRX___SPOT", {})
        assert "subscribe" in stream_handlers

    def test_create_feed(self):
        f = ExchangeRegistry.create_feed("WAZIRX___SPOT", queue.Queue())
        assert isinstance(f, WazirxRequestDataSpot)

    def test_create_exchange_data(self):
        ed = ExchangeRegistry.create_exchange_data("WAZIRX___SPOT")
        assert isinstance(ed, WazirxExchangeDataSpot)


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
    "get_exchange_info",
    "async_get_exchange_info",
    "get_server_time",
    "async_get_server_time",
    "get_balance",
    "async_get_balance",
    "get_account",
    "async_get_account",
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
        assert feed.exchange_name == "WAZIRX___SPOT"

    def test_default_asset_type(self, feed):
        assert feed.asset_type == "SPOT"

    def test_capabilities(self, feed):
        from bt_api_py.feeds.capability import Capability

        caps = feed._capabilities()
        assert Capability.GET_TICK in caps
        assert Capability.GET_DEPTH in caps
        assert Capability.GET_KLINE in caps
        assert Capability.GET_EXCHANGE_INFO in caps

    def test_push_data_to_queue(self, feed):
        feed.push_data_to_queue({"test": 1})
        assert not feed.data_queue.empty()


# ═══════════════════════════════════════════════════════════════
# 9) WebSocket stubs
# ═══════════════════════════════════════════════════════════════


class TestWebSocketStubs:
    def test_market_wss_start_stop(self):
        wss = WazirxMarketWssDataSpot(queue.Queue(), topics=[{"topic": "ticker"}])
        wss.start()
        assert wss.running is True
        wss.stop()
        assert wss.running is False

    def test_account_wss_start_stop(self):
        wss = WazirxAccountWssDataSpot(queue.Queue(), topics=[{"topic": "account"}])
        wss.start()
        assert wss.running is True
        wss.stop()
        assert wss.running is False


# ═══════════════════════════════════════════════════════════════
# 10) Integration (skipped)
# ═══════════════════════════════════════════════════════════════


class TestIntegration:
    @pytest.mark.skip(reason="Requires network access")
    @pytest.mark.ticker
    def test_live_get_tick(self):
        f = WazirxRequestDataSpot(queue.Queue())
        rd = f.get_tick("btcinr")
        assert isinstance(rd, RequestData)

    @pytest.mark.skip(reason="Requires network access")
    def test_live_get_exchange_info(self):
        f = WazirxRequestDataSpot(queue.Queue())
        rd = f.get_exchange_info()
        assert isinstance(rd, RequestData)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
