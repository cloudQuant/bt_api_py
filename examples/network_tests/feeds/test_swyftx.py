"""
Tests for Swyftx exchange – Feed pattern.

Run:  pytest tests/feeds/test_swyftx.py -v
"""

import queue
from unittest.mock import patch

import pytest

import bt_api_py.exchange_registers.register_swyftx  # noqa: F401
from bt_api_py.containers.exchanges.swyftx_exchange_data import (
    SwyftxExchangeData,
    SwyftxExchangeDataSpot,
)
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_swyftx.request_base import SwyftxRequestData
from bt_api_py.feeds.live_swyftx.spot import (
    SwyftxAccountWssDataSpot,
    SwyftxMarketWssDataSpot,
    SwyftxRequestDataSpot,
)
from bt_api_py.registry import ExchangeRegistry

# ── sample fixtures ──────────────────────────────────────────

SAMPLE_TICK = {
    "market": "BTC-AUD",
    "lastPrice": "75000.00",
    "bid": "74999",
    "ask": "75001",
    "volume": "1.5",
}
SAMPLE_DEPTH = {"bids": [[74999, "1.0"]], "asks": [[75001, "1.0"]]}
SAMPLE_KLINE = [[1672531200, "75000", "76000", "74000", "75500", "1.5"]]
SAMPLE_EXCHANGE_INFO = [{"id": "BTC-AUD", "baseAsset": "BTC", "quoteAsset": "AUD"}]
SAMPLE_SERVER_TIME = {"serverTime": 1678901234000}
SAMPLE_BALANCE = [{"asset": "AUD", "available": "1000.00"}]
SAMPLE_ACCOUNT = {"username": "testuser", "email": "test@test.com"}
SAMPLE_ERROR = {"error": {"message": "Bad request", "code": 400}}


@pytest.fixture
def feed():
    return SwyftxRequestDataSpot(queue.Queue())


@pytest.fixture
def exdata():
    return SwyftxExchangeDataSpot()


# ═══════════════════════════════════════════════════════════════
# 1) ExchangeData
# ═══════════════════════════════════════════════════════════════


class TestExchangeData:
    def test_exchange_name(self, exdata):
        assert exdata.exchange_name == "SWYFTX___SPOT"

    def test_asset_type(self, exdata):
        assert exdata.asset_type == "SPOT"

    def test_rest_url(self, exdata):
        assert exdata.rest_url == "https://api.swyftx.com.au"

    def test_base_exchange_name(self):
        d = SwyftxExchangeData()
        assert d.exchange_name == "SWYFTX"

    def test_get_symbol(self):
        assert SwyftxExchangeDataSpot.get_symbol("btc/aud") == "BTC-AUD"
        assert SwyftxExchangeDataSpot.get_symbol("btc_aud") == "BTC-AUD"
        assert SwyftxExchangeDataSpot.get_symbol("BTC-AUD") == "BTC-AUD"

    def test_get_reverse_symbol(self):
        assert SwyftxExchangeDataSpot.get_reverse_symbol("btc_aud") == "BTC-AUD"

    def test_get_period(self, exdata):
        assert exdata.get_period("1h") == "3600"
        assert exdata.get_period("1d") == "86400"

    def test_get_reverse_period(self, exdata):
        assert exdata.get_reverse_period("3600") == "1h"

    def test_legal_currency(self, exdata):
        for c in ("AUD", "USD", "BTC", "ETH", "USDT"):
            assert c in exdata.legal_currency

    @pytest.mark.kline
    def test_kline_periods(self, exdata):
        for k in ("1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"):
            assert k in exdata.kline_periods

    def test_get_rest_path_ok(self, exdata):
        p = exdata.get_rest_path("get_tick", symbol="BTC-AUD")
        assert "BTC-AUD" in p
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
        path, params, extra = feed._get_tick("BTC-AUD")
        assert "ticker" in path.lower()
        assert "BTC-AUD" in path
        assert extra["request_type"] == "get_tick"
        assert extra["symbol_name"] == "BTC-AUD"

    @pytest.mark.orderbook
    def test_get_depth_params(self, feed):
        path, params, extra = feed._get_depth("BTC-AUD", 20)
        assert "orderbook" in path.lower()
        assert params["depth"] == 20

    @pytest.mark.kline
    def test_get_kline_params(self, feed):
        path, params, extra = feed._get_kline("BTC-AUD", "1h", 10)
        assert "candle" in path.lower()
        assert params["interval"] == "3600"
        assert params["limit"] == 10

    def test_get_exchange_info_params(self, feed):
        path, params, extra = feed._get_exchange_info()
        assert "markets" in path.lower()
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


# ═══════════════════════════════════════════════════════════════
# 3) Normalization functions
# ═══════════════════════════════════════════════════════════════


class TestNormalization:
    @pytest.mark.ticker
    def test_tick_ok(self):
        result, ok = SwyftxRequestData._get_tick_normalize_function(SAMPLE_TICK, {})
        assert ok is True
        assert isinstance(result, list) and len(result) > 0

    @pytest.mark.ticker
    def test_tick_error(self):
        result, ok = SwyftxRequestData._get_tick_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    @pytest.mark.ticker
    def test_tick_none(self):
        result, ok = SwyftxRequestData._get_tick_normalize_function(None, {})
        assert ok is False

    @pytest.mark.orderbook
    def test_depth_ok(self):
        result, ok = SwyftxRequestData._get_depth_normalize_function(SAMPLE_DEPTH, {})
        assert ok is True

    @pytest.mark.orderbook
    def test_depth_error(self):
        result, ok = SwyftxRequestData._get_depth_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    @pytest.mark.kline
    def test_kline_ok(self):
        result, ok = SwyftxRequestData._get_kline_normalize_function(SAMPLE_KLINE, {})
        assert ok is True
        assert len(result) == 1

    @pytest.mark.kline
    def test_kline_none(self):
        result, ok = SwyftxRequestData._get_kline_normalize_function(None, {})
        assert ok is False

    def test_exchange_info_list(self):
        result, ok = SwyftxRequestData._get_exchange_info_normalize_function(
            SAMPLE_EXCHANGE_INFO, {}
        )
        assert ok is True

    def test_exchange_info_dict(self):
        result, ok = SwyftxRequestData._get_exchange_info_normalize_function({"markets": []}, {})
        assert ok is True

    def test_server_time_ok(self):
        result, ok = SwyftxRequestData._get_server_time_normalize_function(SAMPLE_SERVER_TIME, {})
        assert ok is True

    def test_server_time_none(self):
        result, ok = SwyftxRequestData._get_server_time_normalize_function(None, {})
        assert ok is False

    def test_balance_ok(self):
        result, ok = SwyftxRequestData._get_balance_normalize_function(SAMPLE_BALANCE, {})
        assert ok is True

    def test_account_ok(self):
        result, ok = SwyftxRequestData._get_account_normalize_function(SAMPLE_ACCOUNT, {})
        assert ok is True

    def test_account_error(self):
        result, ok = SwyftxRequestData._get_account_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_is_error_none(self):
        assert SwyftxRequestData._is_error(None) is True

    def test_is_error_with_key(self):
        assert SwyftxRequestData._is_error(SAMPLE_ERROR) is True

    def test_is_error_ok(self):
        assert SwyftxRequestData._is_error(SAMPLE_TICK) is False


# ═══════════════════════════════════════════════════════════════
# 4) Mocked sync calls
# ═══════════════════════════════════════════════════════════════


class TestSyncCalls:
    @patch.object(SwyftxRequestData, "http_request", return_value=SAMPLE_TICK)
    @pytest.mark.ticker
    def test_get_tick(self, mock_http, feed):
        rd = feed.get_tick("BTC-AUD")
        assert isinstance(rd, RequestData)
        mock_http.assert_called_once()

    @patch.object(SwyftxRequestData, "http_request", return_value=SAMPLE_TICK)
    @pytest.mark.ticker
    def test_get_ticker(self, mock_http, feed):
        rd = feed.get_ticker("BTC-AUD")
        assert isinstance(rd, RequestData)

    @patch.object(SwyftxRequestData, "http_request", return_value=SAMPLE_DEPTH)
    @pytest.mark.orderbook
    def test_get_depth(self, mock_http, feed):
        rd = feed.get_depth("BTC-AUD", 20)
        assert isinstance(rd, RequestData)

    @patch.object(SwyftxRequestData, "http_request", return_value=SAMPLE_KLINE)
    @pytest.mark.kline
    def test_get_kline(self, mock_http, feed):
        rd = feed.get_kline("BTC-AUD", "1h", 10)
        assert isinstance(rd, RequestData)

    @patch.object(SwyftxRequestData, "http_request", return_value=SAMPLE_EXCHANGE_INFO)
    def test_get_exchange_info(self, mock_http, feed):
        rd = feed.get_exchange_info()
        assert isinstance(rd, RequestData)

    @patch.object(SwyftxRequestData, "http_request", return_value=SAMPLE_SERVER_TIME)
    def test_get_server_time(self, mock_http, feed):
        rd = feed.get_server_time()
        assert isinstance(rd, RequestData)

    @patch.object(SwyftxRequestData, "http_request", return_value=SAMPLE_BALANCE)
    def test_get_balance(self, mock_http, feed):
        rd = feed.get_balance()
        assert isinstance(rd, RequestData)

    @patch.object(SwyftxRequestData, "http_request", return_value=SAMPLE_ACCOUNT)
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
        f = SwyftxRequestDataSpot(queue.Queue(), public_key="mytoken")
        h = f._get_headers()
        assert h["Authorization"] == "Bearer mytoken"


# ═══════════════════════════════════════════════════════════════
# 6) Registry
# ═══════════════════════════════════════════════════════════════


class TestRegistry:
    def test_feed_registered(self):
        assert "SWYFTX___SPOT" in ExchangeRegistry._feed_classes

    def test_exchange_data_registered(self):
        assert "SWYFTX___SPOT" in ExchangeRegistry._exchange_data_classes

    def test_balance_handler_registered(self):
        assert "SWYFTX___SPOT" in ExchangeRegistry._balance_handlers

    def test_stream_registered(self):
        stream_handlers = ExchangeRegistry._stream_classes.get("SWYFTX___SPOT", {})
        assert "subscribe" in stream_handlers

    def test_create_feed(self):
        f = ExchangeRegistry.create_feed("SWYFTX___SPOT", queue.Queue())
        assert isinstance(f, SwyftxRequestDataSpot)

    def test_create_exchange_data(self):
        ed = ExchangeRegistry.create_exchange_data("SWYFTX___SPOT")
        assert isinstance(ed, SwyftxExchangeDataSpot)


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
        assert feed.exchange_name == "SWYFTX___SPOT"

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
        wss = SwyftxMarketWssDataSpot(queue.Queue(), topics=[{"topic": "ticker"}])
        wss.start()
        assert wss.running is True
        wss.stop()
        assert wss.running is False

    def test_account_wss_start_stop(self):
        wss = SwyftxAccountWssDataSpot(queue.Queue(), topics=[{"topic": "account"}])
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
        f = SwyftxRequestDataSpot(queue.Queue())
        rd = f.get_tick("BTC-AUD")
        assert isinstance(rd, RequestData)

    @pytest.mark.skip(reason="Requires network access")
    def test_live_get_exchange_info(self):
        f = SwyftxRequestDataSpot(queue.Queue())
        rd = f.get_exchange_info()
        assert isinstance(rd, RequestData)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
