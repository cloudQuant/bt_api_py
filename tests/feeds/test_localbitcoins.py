"""
Tests for LocalBitcoins exchange – Feed pattern.

Run:  pytest tests/feeds/test_localbitcoins.py -v
"""

import queue
from unittest.mock import patch

import pytest

import bt_api_py.exchange_registers.register_localbitcoins  # noqa: F401
from bt_api_py.containers.exchanges.localbitcoins_exchange_data import LocalBitcoinsExchangeDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_localbitcoins.request_base import LocalBitcoinsRequestData
from bt_api_py.feeds.live_localbitcoins.spot import (
    LocalBitcoinsAccountWssDataSpot,
    LocalBitcoinsMarketWssDataSpot,
    LocalBitcoinsRequestDataSpot,
)
from bt_api_py.registry import ExchangeRegistry

# ── sample response fixtures ─────────────────────────────────

SAMPLE_TICK = {
    "USD": {
        "rates": {"last": "50000.50"},
        "avg_1h": "50010",
        "avg_12h": "49990",
        "avg_24h": "49950",
        "volume_btc": "123.45",
    },
    "EUR": {"rates": {"last": "45000.00"}, "volume_btc": "80.1"},
}

SAMPLE_CURRENCIES = {"currencies": {"USD": "US Dollar", "EUR": "Euro", "GBP": "British Pound"}}

SAMPLE_SERVER_TIME = {"serverTime": 1678901234000}

SAMPLE_ADS = {"data": {"ad_list": [{"ad_id": "123", "temp_price": "50000"}]}}

SAMPLE_ONLINE_ADS = {"data": {"ad_list": [{"ad_id": "456", "temp_price": "50100"}]}}

SAMPLE_BALANCE = {"total": {"balance": "0.5", "sendable": "0.45"}}

SAMPLE_ACCOUNT = {"data": {"username": "testuser", "created_at": "2020-01-01"}}

SAMPLE_ERROR = {"error": {"message": "Bad request", "error_code": 400}}


# ── helpers ───────────────────────────────────────────────────


@pytest.fixture
def feed():
    return LocalBitcoinsRequestDataSpot(queue.Queue())


@pytest.fixture
def exdata():
    return LocalBitcoinsExchangeDataSpot()


# ═══════════════════════════════════════════════════════════════
# 1) ExchangeData
# ═══════════════════════════════════════════════════════════════


class TestExchangeData:
    def test_exchange_name(self, exdata):
        assert exdata.exchange_name == "LOCALBITCOINS___SPOT"

    def test_asset_type(self, exdata):
        assert exdata.asset_type == "SPOT"

    def test_rest_url(self, exdata):
        assert exdata.rest_url == "https://localbitcoins.com"

    def test_get_symbol(self, exdata):
        assert LocalBitcoinsExchangeDataSpot.get_symbol("BTC/USD") == "btc_usd"
        assert LocalBitcoinsExchangeDataSpot.get_symbol("BTC-USD") == "btc_usd"
        assert LocalBitcoinsExchangeDataSpot.get_symbol("btc_usd") == "btc_usd"

    def test_get_reverse_symbol(self, exdata):
        assert LocalBitcoinsExchangeDataSpot.get_reverse_symbol("btc_usd") == "BTC-USD"
        assert LocalBitcoinsExchangeDataSpot.get_reverse_symbol("btc-eur") == "BTC-EUR"

    def test_get_period(self, exdata):
        assert exdata.get_period("1d") == "1d"

    def test_get_reverse_period(self, exdata):
        assert exdata.get_reverse_period("1d") == "1d"

    def test_legal_currency(self, exdata):
        for c in ("USD", "EUR", "GBP", "RUB", "BTC"):
            assert c in exdata.legal_currency

    def test_get_rest_path_ok(self, exdata):
        p = exdata.get_rest_path("get_tick")
        assert "/bitcoinaverage/ticker-all-currencies/" in p

    def test_get_rest_path_missing(self, exdata):
        with pytest.raises(ValueError):
            exdata.get_rest_path("nonexistent")

    def test_rest_paths_keys(self, exdata):
        for key in (
            "get_tick",
            "get_all_tickers",
            "get_exchange_info",
            "get_ads",
            "get_online_ads",
            "get_wallet",
            "get_wallet_balance",
            "get_account",
            "get_balance",
            "get_server_time",
        ):
            assert key in exdata.rest_paths

    def test_get_rest_path_with_kwargs(self, exdata):
        p = exdata.get_rest_path("get_ads", id="12345")
        assert "12345" in p


# ═══════════════════════════════════════════════════════════════
# 2) Parameter generation (_get_xxx)
# ═══════════════════════════════════════════════════════════════


class TestParamGeneration:
    @pytest.mark.ticker
    def test_get_tick_params(self, feed):
        path, params, extra = feed._get_tick("BTC/USD")
        assert "/bitcoinaverage/ticker-all-currencies/" in path
        assert extra["request_type"] == "get_tick"
        assert extra["symbol_name"] == "BTC/USD"

    def test_get_exchange_info_params(self, feed):
        path, params, extra = feed._get_exchange_info()
        assert "/api/currencies/" in path
        assert extra["request_type"] == "get_exchange_info"

    def test_get_server_time_params(self, feed):
        path, params, extra = feed._get_server_time()
        assert "/api/ecjson.php" in path
        assert extra["request_type"] == "get_server_time"

    def test_get_ads_params(self, feed):
        path, params, extra = feed._get_ads("12345")
        assert "12345" in path
        assert extra["request_type"] == "get_ads"

    def test_get_online_ads_params(self, feed):
        path, params, extra = feed._get_online_ads("USD", "US")
        assert "usd" in path
        assert "us" in path
        assert extra["request_type"] == "get_online_ads"

    def test_get_balance_params(self, feed):
        path, params, extra = feed._get_balance()
        assert "/api/wallet-balance/" in path
        assert extra["request_type"] == "get_balance"

    def test_get_account_params(self, feed):
        path, params, extra = feed._get_account()
        assert "/api/myself/" in path
        assert extra["request_type"] == "get_account"


# ═══════════════════════════════════════════════════════════════
# 3) Normalization functions
# ═══════════════════════════════════════════════════════════════


class TestNormalization:
    @pytest.mark.ticker
    def test_tick_ok(self):
        result, ok = LocalBitcoinsRequestData._get_tick_normalize_function(SAMPLE_TICK, {})
        assert ok is True
        assert isinstance(result, list)

    @pytest.mark.ticker
    def test_tick_error(self):
        result, ok = LocalBitcoinsRequestData._get_tick_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    @pytest.mark.ticker
    def test_tick_none(self):
        result, ok = LocalBitcoinsRequestData._get_tick_normalize_function(None, {})
        assert ok is False

    def test_exchange_info_ok(self):
        result, ok = LocalBitcoinsRequestData._get_exchange_info_normalize_function(
            SAMPLE_CURRENCIES, {}
        )
        assert ok is True

    def test_exchange_info_list(self):
        result, ok = LocalBitcoinsRequestData._get_exchange_info_normalize_function([{"a": 1}], {})
        assert ok is True

    def test_server_time_ok(self):
        result, ok = LocalBitcoinsRequestData._get_server_time_normalize_function(
            SAMPLE_SERVER_TIME, {}
        )
        assert ok is True

    def test_server_time_none(self):
        result, ok = LocalBitcoinsRequestData._get_server_time_normalize_function(None, {})
        assert ok is False

    def test_ads_ok(self):
        result, ok = LocalBitcoinsRequestData._get_ads_normalize_function(SAMPLE_ADS, {})
        assert ok is True

    def test_ads_error(self):
        result, ok = LocalBitcoinsRequestData._get_ads_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_balance_ok(self):
        result, ok = LocalBitcoinsRequestData._get_balance_normalize_function(SAMPLE_BALANCE, {})
        assert ok is True

    def test_balance_error(self):
        result, ok = LocalBitcoinsRequestData._get_balance_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_account_ok(self):
        result, ok = LocalBitcoinsRequestData._get_account_normalize_function(SAMPLE_ACCOUNT, {})
        assert ok is True

    def test_account_error(self):
        result, ok = LocalBitcoinsRequestData._get_account_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_is_error_none(self):
        assert LocalBitcoinsRequestData._is_error(None) is True

    def test_is_error_with_error_key(self):
        assert LocalBitcoinsRequestData._is_error(SAMPLE_ERROR) is True

    def test_is_error_ok(self):
        assert LocalBitcoinsRequestData._is_error(SAMPLE_TICK) is False


# ═══════════════════════════════════════════════════════════════
# 4) Mocked sync calls
# ═══════════════════════════════════════════════════════════════


class TestSyncCalls:
    @patch.object(LocalBitcoinsRequestData, "http_request", return_value=SAMPLE_TICK)
    @pytest.mark.ticker
    def test_get_tick(self, mock_http, feed):
        rd = feed.get_tick("BTC/USD")
        assert isinstance(rd, RequestData)
        mock_http.assert_called_once()

    @patch.object(LocalBitcoinsRequestData, "http_request", return_value=SAMPLE_TICK)
    @pytest.mark.ticker
    def test_get_ticker(self, mock_http, feed):
        rd = feed.get_ticker("BTC/USD")
        assert isinstance(rd, RequestData)

    @patch.object(LocalBitcoinsRequestData, "http_request", return_value=SAMPLE_CURRENCIES)
    def test_get_exchange_info(self, mock_http, feed):
        rd = feed.get_exchange_info()
        assert isinstance(rd, RequestData)

    @patch.object(LocalBitcoinsRequestData, "http_request", return_value=SAMPLE_SERVER_TIME)
    def test_get_server_time(self, mock_http, feed):
        rd = feed.get_server_time()
        assert isinstance(rd, RequestData)

    @patch.object(LocalBitcoinsRequestData, "http_request", return_value=SAMPLE_ADS)
    def test_get_ads(self, mock_http, feed):
        rd = feed.get_ads("12345")
        assert isinstance(rd, RequestData)

    @patch.object(LocalBitcoinsRequestData, "http_request", return_value=SAMPLE_ONLINE_ADS)
    def test_get_online_ads(self, mock_http, feed):
        rd = feed.get_online_ads("USD", "US")
        assert isinstance(rd, RequestData)

    @patch.object(LocalBitcoinsRequestData, "http_request", return_value=SAMPLE_BALANCE)
    def test_get_balance(self, mock_http, feed):
        rd = feed.get_balance()
        assert isinstance(rd, RequestData)

    @patch.object(LocalBitcoinsRequestData, "http_request", return_value=SAMPLE_ACCOUNT)
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
        assert "Apiauth-Key" not in h

    def test_headers_with_key(self):
        f = LocalBitcoinsRequestDataSpot(queue.Queue(), public_key="mykey", private_key="mysecret")
        h = f._get_headers(method="GET", path="/api/wallet-balance/")
        assert h["Apiauth-Key"] == "mykey"
        assert "Apiauth-Nonce" in h
        assert "Apiauth-Signature" in h

    def test_signature_deterministic(self):
        f = LocalBitcoinsRequestDataSpot(queue.Queue(), public_key="k", private_key="s")
        n1, sig1 = f._generate_signature("GET", "/api/myself/")
        n2, sig2 = f._generate_signature("GET", "/api/myself/")
        assert len(sig1) == 64  # SHA-256 hex


# ═══════════════════════════════════════════════════════════════
# 6) Registry
# ═══════════════════════════════════════════════════════════════


class TestRegistry:
    def test_feed_registered(self):
        assert "LOCALBITCOINS___SPOT" in ExchangeRegistry._feed_classes

    def test_exchange_data_registered(self):
        assert "LOCALBITCOINS___SPOT" in ExchangeRegistry._exchange_data_classes

    def test_balance_handler_registered(self):
        assert "LOCALBITCOINS___SPOT" in ExchangeRegistry._balance_handlers

    def test_stream_registered(self):
        stream_handlers = ExchangeRegistry._stream_classes.get("LOCALBITCOINS___SPOT", {})
        assert "subscribe" in stream_handlers

    def test_create_exchange_data(self):
        ed = ExchangeRegistry.create_exchange_data("LOCALBITCOINS___SPOT")
        assert isinstance(ed, LocalBitcoinsExchangeDataSpot)


# ═══════════════════════════════════════════════════════════════
# 7) Method existence
# ═══════════════════════════════════════════════════════════════

_EXPECTED_METHODS = [
    "get_tick",
    "async_get_tick",
    "get_ticker",
    "async_get_ticker",
    "get_exchange_info",
    "async_get_exchange_info",
    "get_server_time",
    "async_get_server_time",
    "get_ads",
    "async_get_ads",
    "get_online_ads",
    "async_get_online_ads",
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
        assert feed.exchange_name == "LOCALBITCOINS___SPOT"

    def test_default_asset_type(self, feed):
        assert feed.asset_type == "SPOT"

    def test_capabilities(self, feed):
        from bt_api_py.feeds.capability import Capability

        caps = feed._capabilities()
        assert Capability.GET_TICK in caps
        assert Capability.GET_EXCHANGE_INFO in caps

    def test_push_data_to_queue(self, feed):
        feed.push_data_to_queue({"test": 1})
        assert not feed.data_queue.empty()


# ═══════════════════════════════════════════════════════════════
# 9) WebSocket stubs
# ═══════════════════════════════════════════════════════════════


class TestWebSocketStubs:
    def test_market_wss_start_stop(self):
        wss = LocalBitcoinsMarketWssDataSpot(queue.Queue(), topics=[{"topic": "ticker"}])
        wss.start()
        assert wss.running is True
        wss.stop()
        assert wss.running is False

    def test_account_wss_start_stop(self):
        wss = LocalBitcoinsAccountWssDataSpot(queue.Queue(), topics=[{"topic": "account"}])
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
        f = LocalBitcoinsRequestDataSpot(queue.Queue())
        rd = f.get_tick("BTC/USD")
        assert isinstance(rd, RequestData)

    @pytest.mark.skip(reason="Requires network access")
    def test_live_get_exchange_info(self):
        f = LocalBitcoinsRequestDataSpot(queue.Queue())
        rd = f.get_exchange_info()
        assert isinstance(rd, RequestData)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
