"""
Tests for Zaif exchange – Feed pattern.

Run:  pytest tests/feeds/test_zaif.py -v
"""

import queue
from unittest.mock import patch

import pytest

import bt_api_py.exchange_registers.register_zaif  # noqa: F401
from bt_api_py.containers.exchanges.zaif_exchange_data import (
    ZaifExchangeData,
    ZaifExchangeDataSpot,
)
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.containers.tickers.zaif_ticker import ZaifRequestTickerData
from bt_api_py.feeds.live_zaif.request_base import ZaifRequestData
from bt_api_py.feeds.live_zaif.spot import (
    ZaifAccountWssDataSpot,
    ZaifMarketWssDataSpot,
    ZaifRequestDataSpot,
)
from bt_api_py.registry import ExchangeRegistry

# ── sample fixtures ──────────────────────────────────────────

SAMPLE_TICK = {
    "last": 5000000,
    "high": 5100000,
    "low": 4900000,
    "vwap": 5000000,
    "volume": 100,
    "bid": 4999000,
    "ask": 5001000,
}
SAMPLE_DEPTH = {"asks": [[5001000, 0.5]], "bids": [[4999000, 0.5]]}
SAMPLE_KLINE = [
    {
        "date": 1678901234,
        "price": 5000000,
        "amount": 0.1,
        "tid": 1,
        "currency_pair": "btc_jpy",
        "trade_type": "bid",
    }
]
SAMPLE_EXCHANGE_INFO = [{"name": "btc_jpy", "title": "BTC/JPY"}]
SAMPLE_SERVER_TIME = {"last_price": 5000000}
SAMPLE_BALANCE = {"success": 1, "return": {"funds": {"btc": 0.5, "jpy": 100000}}}
SAMPLE_ACCOUNT = {"success": 1, "return": {"funds": {"btc": 0.5}}}
SAMPLE_ERROR = {"error": "invalid pair"}
SAMPLE_ORDER = {"success": 1, "return": {"order_id": 12345, "received": 0.1, "remains": 0.0}}
SAMPLE_CANCEL = {"success": 1, "return": {"order_id": 12345, "funds": {}}}
SAMPLE_QUERY = {
    "success": 1,
    "return": {"12345": {"currency_pair": "btc_jpy", "action": "bid", "amount": 0.1}},
}


@pytest.fixture
def feed():
    return ZaifRequestDataSpot(queue.Queue())


@pytest.fixture
def exdata():
    return ZaifExchangeDataSpot()


# ═══════════════════════════════════════════════════════════════
# 1) ExchangeData
# ═══════════════════════════════════════════════════════════════


class TestExchangeData:
    def test_exchange_name(self, exdata):
        assert exdata.exchange_name == "ZAIF___SPOT"

    def test_asset_type(self, exdata):
        assert exdata.asset_type == "SPOT"

    def test_rest_url(self, exdata):
        assert exdata.rest_url == "https://api.zaif.jp"

    def test_wss_url(self, exdata):
        assert exdata.wss_url == "wss://ws.zaif.jp:8888"

    def test_base_exchange_name(self):
        d = ZaifExchangeData()
        assert d.exchange_name == "ZAIF"

    def test_get_symbol(self):
        assert ZaifExchangeDataSpot.get_symbol("BTC/JPY") == "btc_jpy"
        assert ZaifExchangeDataSpot.get_symbol("BTC-JPY") == "btc_jpy"
        assert ZaifExchangeDataSpot.get_symbol("btc_jpy") == "btc_jpy"

    def test_get_reverse_symbol(self):
        assert ZaifExchangeDataSpot.get_reverse_symbol("BTC/JPY") == "btc_jpy"

    def test_get_period(self, exdata):
        assert exdata.get_period("1h") == "1hour"
        assert exdata.get_period("1d") == "1day"
        assert exdata.get_period("1m") == "1min"

    def test_get_reverse_period(self, exdata):
        assert exdata.get_reverse_period("1hour") == "1h"

    def test_legal_currency(self, exdata):
        for c in ("JPY", "BTC", "ETH"):
            assert c in exdata.legal_currency

    @pytest.mark.kline
    def test_kline_periods(self, exdata):
        for k in ("1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"):
            assert k in exdata.kline_periods

    def test_get_rest_path_with_pair(self, exdata):
        p = exdata.get_rest_path("get_tick", pair="btc_jpy")
        assert "btc_jpy" in p
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
        path, params, extra = feed._get_tick("BTC/JPY")
        assert "ticker" in path.lower()
        assert "btc_jpy" in path
        assert extra["request_type"] == "get_tick"
        assert extra["symbol_name"] == "BTC/JPY"
        assert extra["asset_type"] == "SPOT"
        assert extra["exchange_name"] == "ZAIF___SPOT"

    @pytest.mark.orderbook
    def test_get_depth_params(self, feed):
        path, params, extra = feed._get_depth("ETH/BTC")
        assert "depth" in path.lower()
        assert "eth_btc" in path

    @pytest.mark.kline
    def test_get_kline_params(self, feed):
        path, params, extra = feed._get_kline("BTC/JPY", "1h")
        assert "trades" in path.lower()
        assert "btc_jpy" in path

    def test_get_exchange_info_params(self, feed):
        path, params, extra = feed._get_exchange_info()
        assert "currency_pairs" in path.lower()
        assert extra["request_type"] == "get_exchange_info"

    def test_get_server_time_params(self, feed):
        path, params, extra = feed._get_server_time()
        assert "last_price" in path.lower()
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
        assert extra["exchange_name"] == "ZAIF___SPOT"

    def test_make_order_params(self, feed):
        path, params, extra = feed._make_order("BTC/JPY", 0.1, 5000000, "buy-limit")
        assert "tapi" in path.lower()
        assert params["method"] == "trade"
        assert params["currency_pair"] == "btc_jpy"
        assert params["action"] == "buy"
        assert params["amount"] == 0.1
        assert extra["request_type"] == "make_order"
        assert extra["symbol_name"] == "BTC/JPY"
        assert extra["asset_type"] == "SPOT"

    def test_cancel_order_params(self, feed):
        path, params, extra = feed._cancel_order("BTC/JPY", order_id=12345)
        assert params["method"] == "cancel_order"
        assert params["order_id"] == 12345
        assert extra["request_type"] == "cancel_order"
        assert extra["symbol_name"] == "BTC/JPY"

    def test_query_order_params(self, feed):
        path, params, extra = feed._query_order("BTC/JPY", order_id=12345)
        assert params["method"] == "active_orders"
        assert params["currency_pair"] == "btc_jpy"
        assert extra["request_type"] == "query_order"


# ═══════════════════════════════════════════════════════════════
# 3) Normalization functions
# ═══════════════════════════════════════════════════════════════


class TestNormalization:
    @pytest.mark.ticker
    def test_tick_ok(self):
        result, ok = ZaifRequestData._get_tick_normalize_function(SAMPLE_TICK, {})
        assert ok is True
        assert len(result) > 0

    @pytest.mark.ticker
    def test_tick_error(self):
        result, ok = ZaifRequestData._get_tick_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    @pytest.mark.ticker
    def test_tick_none(self):
        result, ok = ZaifRequestData._get_tick_normalize_function(None, {})
        assert ok is False

    @pytest.mark.orderbook
    def test_depth_ok(self):
        result, ok = ZaifRequestData._get_depth_normalize_function(SAMPLE_DEPTH, {})
        assert ok is True

    @pytest.mark.orderbook
    def test_depth_error(self):
        result, ok = ZaifRequestData._get_depth_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    @pytest.mark.kline
    def test_kline_list(self):
        result, ok = ZaifRequestData._get_kline_normalize_function(SAMPLE_KLINE, {})
        assert ok is True

    @pytest.mark.kline
    def test_kline_none(self):
        result, ok = ZaifRequestData._get_kline_normalize_function(None, {})
        assert ok is False

    def test_exchange_info_list(self):
        result, ok = ZaifRequestData._get_exchange_info_normalize_function(SAMPLE_EXCHANGE_INFO, {})
        assert ok is True

    def test_server_time_ok(self):
        result, ok = ZaifRequestData._get_server_time_normalize_function(SAMPLE_SERVER_TIME, {})
        assert ok is True

    def test_server_time_none(self):
        result, ok = ZaifRequestData._get_server_time_normalize_function(None, {})
        assert ok is False

    def test_balance_ok(self):
        result, ok = ZaifRequestData._get_balance_normalize_function(SAMPLE_BALANCE, {})
        assert ok is True

    def test_account_ok(self):
        result, ok = ZaifRequestData._get_account_normalize_function(SAMPLE_ACCOUNT, {})
        assert ok is True

    def test_account_error(self):
        result, ok = ZaifRequestData._get_account_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_make_order_ok(self):
        result, ok = ZaifRequestData._make_order_normalize_function(SAMPLE_ORDER, {})
        assert ok is True
        assert len(result) == 1

    def test_make_order_error(self):
        result, ok = ZaifRequestData._make_order_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_cancel_order_ok(self):
        result, ok = ZaifRequestData._cancel_order_normalize_function(SAMPLE_CANCEL, {})
        assert ok is True

    def test_query_order_ok(self):
        result, ok = ZaifRequestData._query_order_normalize_function(SAMPLE_QUERY, {})
        assert ok is True

    def test_is_error_none(self):
        assert ZaifRequestData._is_error(None) is True

    def test_is_error_with_key(self):
        assert ZaifRequestData._is_error(SAMPLE_ERROR) is True

    def test_is_error_ok(self):
        assert ZaifRequestData._is_error(SAMPLE_TICK) is False


# ═══════════════════════════════════════════════════════════════
# 4) Mocked sync calls
# ═══════════════════════════════════════════════════════════════


class TestSyncCalls:
    @patch.object(ZaifRequestData, "http_request", return_value=SAMPLE_TICK)
    @pytest.mark.ticker
    def test_get_tick(self, mock_http, feed):
        rd = feed.get_tick("BTC/JPY")
        assert isinstance(rd, RequestData)
        mock_http.assert_called_once()

    @patch.object(ZaifRequestData, "http_request", return_value=SAMPLE_TICK)
    @pytest.mark.ticker
    def test_get_ticker(self, mock_http, feed):
        rd = feed.get_ticker("BTC/JPY")
        assert isinstance(rd, RequestData)

    @patch.object(ZaifRequestData, "http_request", return_value=SAMPLE_DEPTH)
    @pytest.mark.orderbook
    def test_get_depth(self, mock_http, feed):
        rd = feed.get_depth("ETH/BTC")
        assert isinstance(rd, RequestData)

    @patch.object(ZaifRequestData, "http_request", return_value=SAMPLE_KLINE)
    @pytest.mark.kline
    def test_get_kline(self, mock_http, feed):
        rd = feed.get_kline("BTC/JPY", "1h")
        assert isinstance(rd, RequestData)

    @patch.object(ZaifRequestData, "http_request", return_value=SAMPLE_EXCHANGE_INFO)
    def test_get_exchange_info(self, mock_http, feed):
        rd = feed.get_exchange_info()
        assert isinstance(rd, RequestData)

    @patch.object(ZaifRequestData, "http_request", return_value=SAMPLE_SERVER_TIME)
    def test_get_server_time(self, mock_http, feed):
        rd = feed.get_server_time()
        assert isinstance(rd, RequestData)

    @patch.object(ZaifRequestData, "http_request", return_value=SAMPLE_BALANCE)
    def test_get_balance(self, mock_http, feed):
        rd = feed.get_balance()
        assert isinstance(rd, RequestData)

    @patch.object(ZaifRequestData, "http_request", return_value=SAMPLE_ACCOUNT)
    def test_get_account(self, mock_http, feed):
        rd = feed.get_account()
        assert isinstance(rd, RequestData)

    @patch.object(ZaifRequestData, "http_request", return_value=SAMPLE_ORDER)
    def test_make_order(self, mock_http, feed):
        rd = feed.make_order("BTC/JPY", 0.1, 5000000)
        assert isinstance(rd, RequestData)
        mock_http.assert_called_once()

    @patch.object(ZaifRequestData, "http_request", return_value=SAMPLE_CANCEL)
    def test_cancel_order(self, mock_http, feed):
        rd = feed.cancel_order("BTC/JPY", order_id=12345)
        assert isinstance(rd, RequestData)

    @patch.object(ZaifRequestData, "http_request", return_value=SAMPLE_QUERY)
    def test_query_order(self, mock_http, feed):
        rd = feed.query_order("BTC/JPY", order_id=12345)
        assert isinstance(rd, RequestData)


# ═══════════════════════════════════════════════════════════════
# 5) Auth
# ═══════════════════════════════════════════════════════════════


class TestAuth:
    def test_headers_no_key(self, feed):
        h = feed._get_headers()
        assert "Content-Type" in h
        assert "Key" not in h

    def test_headers_with_key(self):
        f = ZaifRequestDataSpot(queue.Queue(), public_key="mykey", secret_key="mysecret")
        h = f._get_headers("method=getInfo&nonce=1")
        assert h["Key"] == "mykey"
        assert "Sign" in h
        assert len(h["Sign"]) == 128

    def test_generate_signature_no_secret(self, feed):
        sig = feed._generate_signature("method=getInfo")
        assert sig == ""

    def test_generate_signature_with_secret(self):
        f = ZaifRequestDataSpot(queue.Queue(), secret_key="testsecret")
        sig = f._generate_signature("method=trade&nonce=12345678.12345678")
        assert len(sig) == 128


# ═══════════════════════════════════════════════════════════════
# 6) Registry
# ═══════════════════════════════════════════════════════════════


class TestRegistry:
    def test_feed_registered(self):
        assert "ZAIF___SPOT" in ExchangeRegistry._feed_classes
        assert ExchangeRegistry._feed_classes["ZAIF___SPOT"] == ZaifRequestDataSpot

    def test_exchange_data_registered(self):
        assert "ZAIF___SPOT" in ExchangeRegistry._exchange_data_classes
        assert ExchangeRegistry._exchange_data_classes["ZAIF___SPOT"] == ZaifExchangeDataSpot

    def test_balance_handler_registered(self):
        assert "ZAIF___SPOT" in ExchangeRegistry._balance_handlers

    def test_stream_registered(self):
        stream_handlers = ExchangeRegistry._stream_classes.get("ZAIF___SPOT", {})
        assert "subscribe" in stream_handlers

    def test_create_feed(self):
        f = ExchangeRegistry.create_feed("ZAIF___SPOT", queue.Queue())
        assert isinstance(f, ZaifRequestDataSpot)

    def test_create_exchange_data(self):
        ed = ExchangeRegistry.create_exchange_data("ZAIF___SPOT")
        assert isinstance(ed, ZaifExchangeDataSpot)


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
        assert feed.exchange_name == "ZAIF___SPOT"

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
        wss = ZaifMarketWssDataSpot(queue.Queue(), topics=[{"topic": "ticker"}])
        wss.start()
        assert wss.running is True
        wss.stop()
        assert wss.running is False

    def test_account_wss_start_stop(self):
        wss = ZaifAccountWssDataSpot(queue.Queue(), topics=[{"topic": "account"}])
        wss.start()
        assert wss.running is True
        wss.stop()
        assert wss.running is False


# ═══════════════════════════════════════════════════════════════
# 10) Data containers
# ═══════════════════════════════════════════════════════════════


class TestDataContainers:
    @pytest.mark.ticker
    def test_ticker_float_parsing(self):
        assert ZaifRequestTickerData._parse_float("5000000") == 5000000.0
        assert ZaifRequestTickerData._parse_float(5000000) == 5000000.0
        assert ZaifRequestTickerData._parse_float(None) is None
        assert ZaifRequestTickerData._parse_float("invalid") is None

    @pytest.mark.ticker
    def test_ticker_class_creation(self):
        assert hasattr(ZaifRequestTickerData, "_parse_float")
        assert hasattr(ZaifRequestTickerData, "init_data")


# ═══════════════════════════════════════════════════════════════
# 11) Integration (skipped)
# ═══════════════════════════════════════════════════════════════


class TestIntegration:
    @pytest.mark.skip(reason="Requires network access")
    @pytest.mark.ticker
    def test_live_get_tick(self):
        f = ZaifRequestDataSpot(queue.Queue())
        rd = f.get_tick("btc_jpy")
        assert isinstance(rd, RequestData)

    @pytest.mark.skip(reason="Requires network access")
    @pytest.mark.orderbook
    def test_live_get_depth(self):
        f = ZaifRequestDataSpot(queue.Queue())
        rd = f.get_depth("btc_jpy")
        assert isinstance(rd, RequestData)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
