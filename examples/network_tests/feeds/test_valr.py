"""
Tests for VALR exchange – Feed pattern.

Run:  pytest tests/feeds/test_valr.py -v
"""

import queue
from unittest.mock import patch

import pytest

import bt_api_py.exchange_registers.register_valr  # noqa: F401
from bt_api_py.containers.exchanges.valr_exchange_data import (
    ValrExchangeData,
    ValrExchangeDataSpot,
)
from bt_api_base.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_valr.request_base import ValrRequestData
from bt_api_py.feeds.live_valr.spot import (
    ValrAccountWssDataSpot,
    ValrMarketWssDataSpot,
    ValrRequestDataSpot,
)
from bt_api_py.registry import ExchangeRegistry

# ── sample fixtures ──────────────────────────────────────────

SAMPLE_TICK = {
    "currencyPair": "BTCZAR",
    "lastTradedPrice": "750000",
    "bidPrice": "749990",
    "askPrice": "750010",
}
SAMPLE_DEPTH = {
    "Asks": [{"price": "750010", "quantity": "1.0"}],
    "Bids": [{"price": "749990", "quantity": "1.0"}],
}
SAMPLE_KLINE = {"currencyPair": "BTCZAR", "changeFromPrevious": "0.5"}
SAMPLE_EXCHANGE_INFO = [{"symbol": "BTCZAR", "baseCurrency": "BTC", "quoteCurrency": "ZAR"}]
SAMPLE_SERVER_TIME = {"epochTime": 1678901234000}
SAMPLE_BALANCE = [{"currency": "ZAR", "available": "10000.00"}]
SAMPLE_ACCOUNT = [{"currency": "BTC", "available": "0.5"}]
SAMPLE_ERROR = {"error": "Bad request"}


@pytest.fixture
def feed():
    return ValrRequestDataSpot(queue.Queue())


@pytest.fixture
def exdata():
    return ValrExchangeDataSpot()


# ═══════════════════════════════════════════════════════════════
# 1) ExchangeData
# ═══════════════════════════════════════════════════════════════


class TestExchangeData:
    def test_exchange_name(self, exdata):
        assert exdata.exchange_name == "VALR___SPOT"

    def test_asset_type(self, exdata):
        assert exdata.asset_type == "SPOT"

    def test_rest_url(self, exdata):
        assert exdata.rest_url == "https://api.valr.com"

    def test_wss_url(self, exdata):
        assert exdata.wss_url == "wss://api.valr.com/ws"

    def test_base_exchange_name(self):
        d = ValrExchangeData()
        assert d.exchange_name == "VALR"

    def test_get_symbol(self):
        assert ValrExchangeDataSpot.get_symbol("btc/zar") == "BTCZAR"
        assert ValrExchangeDataSpot.get_symbol("btc_zar") == "BTCZAR"
        assert ValrExchangeDataSpot.get_symbol("BTC-ZAR") == "BTCZAR"
        assert ValrExchangeDataSpot.get_symbol("BTCZAR") == "BTCZAR"

    def test_get_reverse_symbol(self):
        assert ValrExchangeDataSpot.get_reverse_symbol("btc_zar") == "BTCZAR"

    def test_get_period(self, exdata):
        assert exdata.get_period("1h") == "1h"
        assert exdata.get_period("1d") == "1d"

    def test_get_reverse_period(self, exdata):
        assert exdata.get_reverse_period("1h") == "1h"

    def test_legal_currency(self, exdata):
        for c in ("USDC", "ZAR", "BTC", "ETH"):
            assert c in exdata.legal_currency

    @pytest.mark.kline
    def test_kline_periods(self, exdata):
        for k in ("1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d", "1w", "1M"):
            assert k in exdata.kline_periods

    def test_get_rest_path_ok(self, exdata):
        p = exdata.get_rest_path("get_tick", symbol="BTCZAR")
        assert "BTCZAR" in p
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
        path, params, extra = feed._get_tick("BTCZAR")
        assert "ticker" in path.lower()
        assert "BTCZAR" in path
        assert extra["request_type"] == "get_tick"
        assert extra["symbol_name"] == "BTCZAR"

    @pytest.mark.orderbook
    def test_get_depth_params(self, feed):
        path, params, extra = feed._get_depth("BTCZAR")
        assert "orderbook" in path.lower()
        assert extra["symbol_name"] == "BTCZAR"

    @pytest.mark.kline
    def test_get_kline_params(self, feed):
        path, params, extra = feed._get_kline("BTCZAR", "1h")
        assert extra["request_type"] == "get_kline"
        assert extra["symbol_name"] == "BTCZAR"

    def test_get_exchange_info_params(self, feed):
        path, params, extra = feed._get_exchange_info()
        assert "pairs" in path.lower()
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
        assert extra["request_type"] == "get_account"


# ═══════════════════════════════════════════════════════════════
# 3) Normalization functions
# ═══════════════════════════════════════════════════════════════


class TestNormalization:
    @pytest.mark.ticker
    def test_tick_ok(self):
        result, ok = ValrRequestData._get_tick_normalize_function(SAMPLE_TICK, {})
        assert ok is True
        assert len(result) > 0

    @pytest.mark.ticker
    def test_tick_error(self):
        result, ok = ValrRequestData._get_tick_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    @pytest.mark.ticker
    def test_tick_none(self):
        result, ok = ValrRequestData._get_tick_normalize_function(None, {})
        assert ok is False

    @pytest.mark.orderbook
    def test_depth_ok(self):
        result, ok = ValrRequestData._get_depth_normalize_function(SAMPLE_DEPTH, {})
        assert ok is True

    @pytest.mark.orderbook
    def test_depth_error(self):
        result, ok = ValrRequestData._get_depth_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    @pytest.mark.kline
    def test_kline_dict(self):
        result, ok = ValrRequestData._get_kline_normalize_function(SAMPLE_KLINE, {})
        assert ok is True

    @pytest.mark.kline
    def test_kline_none(self):
        result, ok = ValrRequestData._get_kline_normalize_function(None, {})
        assert ok is False

    def test_exchange_info_list(self):
        result, ok = ValrRequestData._get_exchange_info_normalize_function(SAMPLE_EXCHANGE_INFO, {})
        assert ok is True

    def test_server_time_ok(self):
        result, ok = ValrRequestData._get_server_time_normalize_function(SAMPLE_SERVER_TIME, {})
        assert ok is True

    def test_server_time_none(self):
        result, ok = ValrRequestData._get_server_time_normalize_function(None, {})
        assert ok is False

    def test_balance_ok(self):
        result, ok = ValrRequestData._get_balance_normalize_function(SAMPLE_BALANCE, {})
        assert ok is True

    def test_account_ok(self):
        result, ok = ValrRequestData._get_account_normalize_function(SAMPLE_ACCOUNT, {})
        assert ok is True

    def test_account_error(self):
        result, ok = ValrRequestData._get_account_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_is_error_none(self):
        assert ValrRequestData._is_error(None) is True

    def test_is_error_with_key(self):
        assert ValrRequestData._is_error(SAMPLE_ERROR) is True

    def test_is_error_ok(self):
        assert ValrRequestData._is_error(SAMPLE_TICK) is False


# ═══════════════════════════════════════════════════════════════
# 4) Mocked sync calls
# ═══════════════════════════════════════════════════════════════


class TestSyncCalls:
    @patch.object(ValrRequestData, "http_request", return_value=SAMPLE_TICK)
    @pytest.mark.ticker
    def test_get_tick(self, mock_http, feed):
        rd = feed.get_tick("BTCZAR")
        assert isinstance(rd, RequestData)
        mock_http.assert_called_once()

    @patch.object(ValrRequestData, "http_request", return_value=SAMPLE_TICK)
    @pytest.mark.ticker
    def test_get_ticker(self, mock_http, feed):
        rd = feed.get_ticker("BTCZAR")
        assert isinstance(rd, RequestData)

    @patch.object(ValrRequestData, "http_request", return_value=SAMPLE_DEPTH)
    @pytest.mark.orderbook
    def test_get_depth(self, mock_http, feed):
        rd = feed.get_depth("BTCZAR", 20)
        assert isinstance(rd, RequestData)

    @patch.object(ValrRequestData, "http_request", return_value=SAMPLE_KLINE)
    @pytest.mark.kline
    def test_get_kline(self, mock_http, feed):
        rd = feed.get_kline("BTCZAR", "1h")
        assert isinstance(rd, RequestData)

    @patch.object(ValrRequestData, "http_request", return_value=SAMPLE_EXCHANGE_INFO)
    def test_get_exchange_info(self, mock_http, feed):
        rd = feed.get_exchange_info()
        assert isinstance(rd, RequestData)

    @patch.object(ValrRequestData, "http_request", return_value=SAMPLE_SERVER_TIME)
    def test_get_server_time(self, mock_http, feed):
        rd = feed.get_server_time()
        assert isinstance(rd, RequestData)

    @patch.object(ValrRequestData, "http_request", return_value=SAMPLE_BALANCE)
    def test_get_balance(self, mock_http, feed):
        rd = feed.get_balance()
        assert isinstance(rd, RequestData)

    @patch.object(ValrRequestData, "http_request", return_value=SAMPLE_ACCOUNT)
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
        assert "X-VALR-API-KEY" not in h

    def test_headers_with_key(self):
        f = ValrRequestDataSpot(
            queue.Queue(),
            public_key="mykey",
            secret_key="mysecret",
        )
        h = f._get_headers(method="GET", request_path="/v1/public/time")
        assert h["X-VALR-API-KEY"] == "mykey"
        assert "X-VALR-TIMESTAMP" in h
        assert "X-VALR-SIGNATURE" in h
        assert len(h["X-VALR-SIGNATURE"]) > 0

    def test_generate_signature_no_secret(self, feed):
        sig = feed._generate_signature("12345", "GET", "/v1/public/time")
        assert sig == ""


# ═══════════════════════════════════════════════════════════════
# 6) Registry
# ═══════════════════════════════════════════════════════════════


class TestRegistry:
    def test_feed_registered(self):
        assert "VALR___SPOT" in ExchangeRegistry._feed_classes

    def test_exchange_data_registered(self):
        assert "VALR___SPOT" in ExchangeRegistry._exchange_data_classes

    def test_balance_handler_registered(self):
        assert "VALR___SPOT" in ExchangeRegistry._balance_handlers

    def test_stream_registered(self):
        stream_handlers = ExchangeRegistry._stream_classes.get("VALR___SPOT", {})
        assert "subscribe" in stream_handlers

    def test_create_feed(self):
        f = ExchangeRegistry.create_feed("VALR___SPOT", queue.Queue())
        assert isinstance(f, ValrRequestDataSpot)

    def test_create_exchange_data(self):
        ed = ExchangeRegistry.create_exchange_data("VALR___SPOT")
        assert isinstance(ed, ValrExchangeDataSpot)


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
        assert feed.exchange_name == "VALR___SPOT"

    def test_default_asset_type(self, feed):
        assert feed.asset_type == "SPOT"

    def test_capabilities(self, feed):
        from bt_api_base.feeds.capability import Capability

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
        wss = ValrMarketWssDataSpot(queue.Queue(), topics=[{"topic": "ticker"}])
        wss.start()
        assert wss.running is True
        wss.stop()
        assert wss.running is False

    def test_account_wss_start_stop(self):
        wss = ValrAccountWssDataSpot(queue.Queue(), topics=[{"topic": "account"}])
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
        f = ValrRequestDataSpot(queue.Queue())
        rd = f.get_tick("BTCZAR")
        assert isinstance(rd, RequestData)

    @pytest.mark.skip(reason="Requires network access")
    def test_live_get_exchange_info(self):
        f = ValrRequestDataSpot(queue.Queue())
        rd = f.get_exchange_info()
        assert isinstance(rd, RequestData)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
