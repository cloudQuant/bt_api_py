"""
Tests for CoinSpot exchange – Feed pattern.

Run:  pytest tests/feeds/test_coinspot.py -v
"""

import queue
from unittest.mock import patch

import pytest

from bt_api_py.containers.exchanges.coinspot_exchange_data import CoinSpotExchangeDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_coinspot.request_base import CoinSpotRequestData
from bt_api_py.feeds.live_coinspot.spot import CoinSpotRequestDataSpot
from bt_api_py.registry import ExchangeRegistry

import bt_api_py.exchange_registers.register_coinspot  # noqa: F401

# ── sample response fixtures ─────────────────────────────────

SAMPLE_TICK = {"status": "ok",
    "prices": {"bid": "95000", "ask": "96000", "last": "95500"}}

SAMPLE_ALL_TICKERS = {"status": "ok",
    "prices": {"BTC": {"bid": "95000", "ask": "96000", "last": "95500"},
               "ETH": {"bid": "3000", "ask": "3100", "last": "3050"}}}

SAMPLE_DEPTH = {"status": "ok",
    "buyorders": [{"amount": "0.1", "rate": "95000"}],
    "sellorders": [{"amount": "0.2", "rate": "96000"}]}

SAMPLE_DEALS = {"status": "ok",
    "buyorders": [{"amount": "0.1", "rate": "95000", "total": "9500"}]}

SAMPLE_ORDER = {"status": "ok", "id": "order123"}

SAMPLE_CANCEL = {"status": "ok"}

SAMPLE_BALANCE = {"status": "ok",
    "balances": [{"BTC": {"balance": "1.5", "audbalance": "142500", "rate": "95000"}}]}

SAMPLE_ERROR = {"status": "error", "message": "Invalid coin type"}


# ── helpers ───────────────────────────────────────────────────

@pytest.fixture
def feed():
    return CoinSpotRequestDataSpot(queue.Queue())


@pytest.fixture
def exdata():
    return CoinSpotExchangeDataSpot()


# ═══════════════════════════════════════════════════════════════
# 1) ExchangeData
# ═══════════════════════════════════════════════════════════════

class TestExchangeData:
    def test_exchange_name(self, exdata):
        assert exdata.exchange_name == "COINSPOT___SPOT"

    def test_asset_type(self, exdata):
        assert exdata.asset_type == "SPOT"

    def test_rest_url(self, exdata):
        assert exdata.rest_url == "https://www.coinspot.com.au"

    def test_wss_url(self, exdata):
        assert exdata.wss_url == ""

    def test_get_symbol(self, exdata):
        assert CoinSpotExchangeDataSpot.get_symbol("BTC") == "BTC"

    def test_get_period(self, exdata):
        assert exdata.get_period("1h") == "60"
        assert exdata.get_period("1d") == "D"

    def test_kline_periods(self, exdata):
        for k in ("1m", "5m", "15m", "30m", "1h", "4h", "1d"):
            assert k in exdata.kline_periods

    def test_legal_currency(self, exdata):
        for c in ("AUD", "USDT", "USD", "BTC", "ETH"):
            assert c in exdata.legal_currency

    def test_get_rest_path_ok(self, exdata):
        p = exdata.get_rest_path("get_tick")
        assert "/pubapi/v2/latest" in p

    def test_get_rest_path_missing(self, exdata):
        with pytest.raises(ValueError):
            exdata.get_rest_path("nonexistent")

    def test_rest_paths_keys(self, exdata):
        for key in ("get_tick", "get_depth", "get_deals", "get_exchange_info",
                     "get_balance", "get_account", "make_order_buy",
                     "make_order_sell", "cancel_order_buy", "cancel_order_sell"):
            assert key in exdata.rest_paths


# ═══════════════════════════════════════════════════════════════
# 2) Parameter generation (_get_xxx)
# ═══════════════════════════════════════════════════════════════

class TestParamGeneration:
    def test_get_tick_params(self, feed):
        path, params, extra = feed._get_tick("BTC")
        assert "GET" in path
        assert "/pubapi/v2/latest/BTC" in path
        assert extra["request_type"] == "get_tick"
        assert extra["symbol_name"] == "BTC"

    def test_get_all_tickers_params(self, feed):
        path, params, extra = feed._get_all_tickers()
        assert "/pubapi/v2/latest" in path
        assert extra["request_type"] == "get_ticker_all"

    def test_get_exchange_info_params(self, feed):
        path, params, extra = feed._get_exchange_info()
        assert "/pubapi/v2/latest" in path
        assert extra["request_type"] == "get_exchange_info"

    def test_get_depth_params(self, feed):
        path, params, extra = feed._get_depth("BTC")
        assert "/pubapi/v2/orders/open/BTC" in path
        assert extra["request_type"] == "get_depth"

    def test_get_deals_params(self, feed):
        path, params, extra = feed._get_deals("BTC")
        assert "/pubapi/v2/orders/completed/BTC" in path
        assert extra["request_type"] == "get_deals"

    def test_make_order_buy_params(self, feed):
        path, body, extra = feed._make_order("BTC", 0.001, 95000, side="buy")
        assert "POST" in path
        assert "/api/v2/my/buy" in path
        assert body["cointype"] == "BTC"
        assert body["amount"] == 0.001
        assert body["rate"] == 95000

    def test_make_order_sell_params(self, feed):
        path, body, extra = feed._make_order("BTC", 0.001, 96000, side="sell")
        assert "/api/v2/my/sell" in path

    def test_cancel_order_buy_params(self, feed):
        path, body, extra = feed._cancel_order("order123", side="buy")
        assert "/api/v2/my/buy/cancel" in path
        assert body["id"] == "order123"

    def test_cancel_order_sell_params(self, feed):
        path, body, extra = feed._cancel_order("order456", side="sell")
        assert "/api/v2/my/sell/cancel" in path

    def test_get_balance_params(self, feed):
        path, body, extra = feed._get_balance()
        assert "/api/v2/my/balances" in path
        assert extra["request_type"] == "get_balance"

    def test_get_account_params(self, feed):
        path, body, extra = feed._get_account()
        assert "/api/v2/my/balances" in path
        assert extra["request_type"] == "get_account"


# ═══════════════════════════════════════════════════════════════
# 3) Normalization functions
# ═══════════════════════════════════════════════════════════════

class TestNormalization:
    def test_tick_ok(self):
        result, ok = CoinSpotRequestData._get_tick_normalize_function(SAMPLE_TICK, {})
        assert ok is True
        assert "bid" in result[0]

    def test_tick_error(self):
        result, ok = CoinSpotRequestData._get_tick_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_all_tickers_ok(self):
        result, ok = CoinSpotRequestData._get_all_tickers_normalize_function(SAMPLE_ALL_TICKERS, {})
        assert ok is True
        assert "prices" in result[0]

    def test_exchange_info_ok(self):
        result, ok = CoinSpotRequestData._get_exchange_info_normalize_function(SAMPLE_ALL_TICKERS, {})
        assert ok is True

    def test_depth_ok(self):
        result, ok = CoinSpotRequestData._get_depth_normalize_function(SAMPLE_DEPTH, {})
        assert ok is True
        assert "buyorders" in result[0]

    def test_depth_error(self):
        result, ok = CoinSpotRequestData._get_depth_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_deals_ok(self):
        result, ok = CoinSpotRequestData._get_deals_normalize_function(SAMPLE_DEALS, {})
        assert ok is True

    def test_make_order_ok(self):
        result, ok = CoinSpotRequestData._make_order_normalize_function(SAMPLE_ORDER, {})
        assert ok is True

    def test_make_order_error(self):
        result, ok = CoinSpotRequestData._make_order_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_cancel_order_ok(self):
        result, ok = CoinSpotRequestData._cancel_order_normalize_function(SAMPLE_CANCEL, {})
        assert ok is True

    def test_balance_ok(self):
        result, ok = CoinSpotRequestData._get_balance_normalize_function(SAMPLE_BALANCE, {})
        assert ok is True

    def test_balance_error(self):
        result, ok = CoinSpotRequestData._get_balance_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_account_ok(self):
        result, ok = CoinSpotRequestData._get_account_normalize_function(SAMPLE_BALANCE, {})
        assert ok is True

    def test_is_error_none(self):
        assert CoinSpotRequestData._is_error(None) is True

    def test_is_error_error(self):
        assert CoinSpotRequestData._is_error(SAMPLE_ERROR) is True

    def test_is_error_ok(self):
        assert CoinSpotRequestData._is_error(SAMPLE_TICK) is False


# ═══════════════════════════════════════════════════════════════
# 4) Mocked sync calls
# ═══════════════════════════════════════════════════════════════

class TestSyncCalls:
    @patch.object(CoinSpotRequestData, "http_request", return_value=SAMPLE_TICK)
    def test_get_tick(self, mock_http, feed):
        rd = feed.get_tick("BTC")
        assert isinstance(rd, RequestData)
        mock_http.assert_called_once()

    @patch.object(CoinSpotRequestData, "http_request", return_value=SAMPLE_ALL_TICKERS)
    def test_get_all_tickers(self, mock_http, feed):
        rd = feed.get_all_tickers()
        assert isinstance(rd, RequestData)

    @patch.object(CoinSpotRequestData, "http_request", return_value=SAMPLE_ALL_TICKERS)
    def test_get_exchange_info(self, mock_http, feed):
        rd = feed.get_exchange_info()
        assert isinstance(rd, RequestData)

    @patch.object(CoinSpotRequestData, "http_request", return_value=SAMPLE_DEPTH)
    def test_get_depth(self, mock_http, feed):
        rd = feed.get_depth("BTC")
        assert isinstance(rd, RequestData)

    @patch.object(CoinSpotRequestData, "http_request", return_value=SAMPLE_DEALS)
    def test_get_deals(self, mock_http, feed):
        rd = feed.get_deals("BTC")
        assert isinstance(rd, RequestData)

    @patch.object(CoinSpotRequestData, "http_request", return_value=SAMPLE_ORDER)
    def test_make_order(self, mock_http, feed):
        rd = feed.make_order("BTC", 0.001, 95000)
        assert isinstance(rd, RequestData)

    @patch.object(CoinSpotRequestData, "http_request", return_value=SAMPLE_CANCEL)
    def test_cancel_order(self, mock_http, feed):
        rd = feed.cancel_order("order123")
        assert isinstance(rd, RequestData)

    @patch.object(CoinSpotRequestData, "http_request", return_value=SAMPLE_BALANCE)
    def test_get_balance(self, mock_http, feed):
        rd = feed.get_balance()
        assert isinstance(rd, RequestData)

    @patch.object(CoinSpotRequestData, "http_request", return_value=SAMPLE_BALANCE)
    def test_get_account(self, mock_http, feed):
        rd = feed.get_account()
        assert isinstance(rd, RequestData)


# ═══════════════════════════════════════════════════════════════
# 5) Auth
# ═══════════════════════════════════════════════════════════════

class TestAuth:
    def test_no_auth_without_keys(self, feed):
        headers, body_str = feed._generate_auth_headers()
        assert headers["key"] == ""

    def test_api_key_property(self, feed):
        assert feed.api_key == ""
        assert feed.api_secret == ""

    def test_auth_with_keys(self):
        f = CoinSpotRequestDataSpot(queue.Queue(), api_key="mykey", api_secret="mysecret")
        headers, body_str = f._generate_auth_headers({"cointype": "BTC"})
        assert headers["key"] == "mykey"
        assert len(headers["sign"]) == 128  # SHA-512 hex

    def test_signature_deterministic(self):
        f = CoinSpotRequestDataSpot(queue.Queue(), api_key="k", api_secret="s")
        sig1 = f._generate_signature('{"test": 1}')
        sig2 = f._generate_signature('{"test": 1}')
        assert sig1 == sig2
        assert len(sig1) == 128


# ═══════════════════════════════════════════════════════════════
# 6) Registry
# ═══════════════════════════════════════════════════════════════

class TestRegistry:
    def test_feed_registered(self):
        assert "COINSPOT___SPOT" in ExchangeRegistry._feed_classes

    def test_exchange_data_registered(self):
        assert "COINSPOT___SPOT" in ExchangeRegistry._exchange_data_classes

    def test_balance_handler_registered(self):
        assert "COINSPOT___SPOT" in ExchangeRegistry._balance_handlers

    def test_create_exchange_data(self):
        ed = ExchangeRegistry.create_exchange_data("COINSPOT___SPOT")
        assert isinstance(ed, CoinSpotExchangeDataSpot)


# ═══════════════════════════════════════════════════════════════
# 7) Method existence
# ═══════════════════════════════════════════════════════════════

_EXPECTED_METHODS = [
    "get_tick", "async_get_tick",
    "get_ticker", "async_get_ticker",
    "get_all_tickers", "async_get_all_tickers",
    "get_depth", "async_get_depth",
    "get_deals", "async_get_deals",
    "make_order", "async_make_order",
    "cancel_order", "async_cancel_order",
    "get_balance", "async_get_balance",
    "get_account", "async_get_account",
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
        assert feed.exchange_name == "COINSPOT___SPOT"

    def test_default_asset_type(self, feed):
        assert feed.asset_type == "SPOT"

    def test_api_keys(self):
        f = CoinSpotRequestDataSpot(queue.Queue(), api_key="ak", api_secret="sk")
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
        f = CoinSpotRequestDataSpot(queue.Queue())
        rd = f.get_tick("BTC")
        assert isinstance(rd, RequestData)

    @pytest.mark.skip(reason="Requires network access")
    def test_live_get_all_tickers(self):
        f = CoinSpotRequestDataSpot(queue.Queue())
        rd = f.get_all_tickers()
        assert isinstance(rd, RequestData)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
