"""
Tests for Independent Reserve exchange – Feed pattern.

Run:  pytest tests/feeds/test_independent_reserve.py -v
"""

import queue
from unittest.mock import patch

import pytest

import bt_api_py.exchange_registers.register_independent_reserve  # noqa: F401
from bt_api_py.containers.exchanges.independent_reserve_exchange_data import (
    IndependentReserveExchangeDataSpot,
)
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_independent_reserve.request_base import IndependentReserveRequestData
from bt_api_py.feeds.live_independent_reserve.spot import IndependentReserveRequestDataSpot
from bt_api_py.registry import ExchangeRegistry

# ── sample response fixtures ─────────────────────────────────

SAMPLE_TICK = {
    "LastPrice": 50000.0,
    "CurrentHighestBidPrice": 49950.0,
    "CurrentLowestOfferPrice": 50050.0,
    "DayVolumeXbt": 123.45,
    "DayHighestPrice": 51000.0,
    "DayLowestPrice": 49000.0,
    "DayAvgPrice": 50000.0,
    "CreatedTimestamp": "2026-01-01T00:00:00Z",
}

SAMPLE_DEPTH = {
    "BuyOrders": [{"Price": 49950.0, "Volume": 1.5}],
    "SellOrders": [{"Price": 50050.0, "Volume": 2.0}],
}

SAMPLE_CURRENCIES = ["Xbt", "Eth", "Ltc", "Usdt"]

SAMPLE_DEALS = {
    "Trades": [
        {
            "TradeTimestampUtc": "2026-01-01T00:00:00Z",
            "PrimaryCurrencyAmount": 0.01,
            "SecondaryCurrencyTradePrice": 50000.0,
        },
    ]
}

SAMPLE_ORDER = {"OrderGuid": "abc-123", "Status": "Open"}

SAMPLE_CANCEL = {}

SAMPLE_OPEN_ORDERS = {"Data": [{"OrderGuid": "abc-123", "Status": "Open"}]}

SAMPLE_ACCOUNTS = [
    {"CurrencyCode": "Xbt", "TotalBalance": 1.5, "AvailableBalance": 1.0},
    {"CurrencyCode": "Aud", "TotalBalance": 100000, "AvailableBalance": 90000},
]

SAMPLE_ERROR = {"Message": "Invalid request"}


# ── helpers ───────────────────────────────────────────────────


@pytest.fixture
def feed():
    return IndependentReserveRequestDataSpot(queue.Queue())


@pytest.fixture
def exdata():
    return IndependentReserveExchangeDataSpot()


# ═══════════════════════════════════════════════════════════════
# 1) ExchangeData
# ═══════════════════════════════════════════════════════════════


class TestExchangeData:
    def test_exchange_name(self, exdata):
        assert exdata.exchange_name == "INDEPENDENT_RESERVE___SPOT"

    def test_asset_type(self, exdata):
        assert exdata.asset_type == "SPOT"

    def test_rest_url(self, exdata):
        assert exdata.rest_url == "https://api.independentreserve.com"

    def test_wss_url(self, exdata):
        assert exdata.wss_url == ""

    def test_get_symbol(self, exdata):
        assert IndependentReserveExchangeDataSpot.get_symbol("BTC/AUD") == ("Xbt", "Aud")
        assert IndependentReserveExchangeDataSpot.get_symbol("ETH/USD") == ("Eth", "Usd")

    def test_get_period(self, exdata):
        assert exdata.get_period("1h") == "1h"
        assert exdata.get_period("1d") == "1d"

    @pytest.mark.kline
    def test_kline_periods(self, exdata):
        for k in ("1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"):
            assert k in exdata.kline_periods

    def test_legal_currency(self, exdata):
        for c in ("AUD", "NZD", "USD", "SGD"):
            assert c in exdata.legal_currency

    def test_get_rest_path_ok(self, exdata):
        p = exdata.get_rest_path("get_tick")
        assert "/Public/GetMarketSummary" in p

    def test_get_rest_path_missing(self, exdata):
        with pytest.raises(ValueError):
            exdata.get_rest_path("nonexistent")

    def test_rest_paths_keys(self, exdata):
        for key in (
            "get_tick",
            "get_depth",
            "get_deals",
            "get_exchange_info",
            "make_order_limit",
            "make_order_market",
            "cancel_order",
            "get_open_orders",
            "get_account",
            "get_balance",
        ):
            assert key in exdata.rest_paths


# ═══════════════════════════════════════════════════════════════
# 2) Parameter generation (_get_xxx)
# ═══════════════════════════════════════════════════════════════


class TestParamGeneration:
    @pytest.mark.ticker
    def test_get_tick_params(self, feed):
        path, params, extra = feed._get_tick("BTC/AUD")
        assert "/Public/GetMarketSummary" in path
        assert params["primaryCurrencyCode"] == "Xbt"
        assert params["secondaryCurrencyCode"] == "Aud"
        assert extra["request_type"] == "get_tick"

    @pytest.mark.orderbook
    def test_get_depth_params(self, feed):
        path, params, extra = feed._get_depth("BTC/AUD")
        assert "/Public/GetOrderBook" in path
        assert params["primaryCurrencyCode"] == "Xbt"
        assert extra["request_type"] == "get_depth"

    def test_get_exchange_info_params(self, feed):
        path, params, extra = feed._get_exchange_info()
        assert "/Public/GetValidPrimaryCurrencyCodes" in path
        assert extra["request_type"] == "get_exchange_info"

    def test_get_deals_params(self, feed):
        path, params, extra = feed._get_deals("ETH/USD")
        assert "/Public/GetRecentTrades" in path
        assert params["primaryCurrencyCode"] == "Eth"
        assert params["secondaryCurrencyCode"] == "Usd"
        assert extra["request_type"] == "get_deals"

    def test_make_order_limit(self, feed):
        path, body, extra = feed._make_order("BTC/AUD", "buy", "limit", 0.001, price=50000)
        assert "POST" in path
        assert "/Private/PlaceLimitOrder" in path
        assert body["primaryCurrencyCode"] == "Xbt"
        assert body["orderType"] == "LimitBid"
        assert body["volume"] == 0.001
        assert body["price"] == 50000

    def test_make_order_market(self, feed):
        path, body, extra = feed._make_order("BTC/AUD", "sell", "market", 0.001)
        assert "/Private/PlaceMarketOrder" in path
        assert body["orderType"] == "MarketOffer"
        assert "price" not in body

    def test_cancel_order_params(self, feed):
        path, body, extra = feed._cancel_order("abc-123")
        assert "/Private/CancelOrder" in path
        assert body["orderGuid"] == "abc-123"
        assert extra["request_type"] == "cancel_order"

    def test_get_open_orders_params(self, feed):
        path, body, extra = feed._get_open_orders("BTC/AUD")
        assert "/Private/GetOpenOrders" in path
        assert body["primaryCurrencyCode"] == "Xbt"

    def test_get_open_orders_no_symbol(self, feed):
        path, body, extra = feed._get_open_orders()
        assert "primaryCurrencyCode" not in body

    def test_get_balance_params(self, feed):
        path, body, extra = feed._get_balance()
        assert "/Private/GetAccounts" in path
        assert extra["request_type"] == "get_balance"

    def test_get_account_params(self, feed):
        path, body, extra = feed._get_account()
        assert "/Private/GetAccounts" in path
        assert extra["request_type"] == "get_account"


# ═══════════════════════════════════════════════════════════════
# 3) Normalization functions
# ═══════════════════════════════════════════════════════════════


class TestNormalization:
    @pytest.mark.ticker
    def test_tick_ok(self):
        result, ok = IndependentReserveRequestData._get_tick_normalize_function(SAMPLE_TICK, {})
        assert ok is True
        assert result[0]["LastPrice"] == 50000.0

    @pytest.mark.ticker
    def test_tick_error(self):
        result, ok = IndependentReserveRequestData._get_tick_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    @pytest.mark.orderbook
    def test_depth_ok(self):
        result, ok = IndependentReserveRequestData._get_depth_normalize_function(SAMPLE_DEPTH, {})
        assert ok is True

    def test_exchange_info_list(self):
        result, ok = IndependentReserveRequestData._get_exchange_info_normalize_function(
            SAMPLE_CURRENCIES, {}
        )
        assert ok is True

    def test_deals_ok(self):
        result, ok = IndependentReserveRequestData._get_deals_normalize_function(SAMPLE_DEALS, {})
        assert ok is True
        assert len(result) == 1

    def test_make_order_ok(self):
        result, ok = IndependentReserveRequestData._make_order_normalize_function(SAMPLE_ORDER, {})
        assert ok is True

    def test_make_order_error(self):
        result, ok = IndependentReserveRequestData._make_order_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_cancel_order_empty(self):
        result, ok = IndependentReserveRequestData._cancel_order_normalize_function({}, {})
        assert ok is True

    def test_open_orders_ok(self):
        result, ok = IndependentReserveRequestData._get_open_orders_normalize_function(
            SAMPLE_OPEN_ORDERS, {}
        )
        assert ok is True

    def test_balance_ok(self):
        result, ok = IndependentReserveRequestData._get_balance_normalize_function(
            SAMPLE_ACCOUNTS, {}
        )
        assert ok is True
        assert len(result) == 2

    def test_account_ok(self):
        result, ok = IndependentReserveRequestData._get_account_normalize_function(
            SAMPLE_ACCOUNTS, {}
        )
        assert ok is True

    def test_is_error_none(self):
        assert IndependentReserveRequestData._is_error(None) is True

    def test_is_error_message(self):
        assert IndependentReserveRequestData._is_error(SAMPLE_ERROR) is True

    def test_is_error_ok(self):
        assert IndependentReserveRequestData._is_error(SAMPLE_TICK) is False


# ═══════════════════════════════════════════════════════════════
# 4) Mocked sync calls
# ═══════════════════════════════════════════════════════════════


class TestSyncCalls:
    @patch.object(IndependentReserveRequestData, "http_request", return_value=SAMPLE_TICK)
    @pytest.mark.ticker
    def test_get_tick(self, mock_http, feed):
        rd = feed.get_tick("BTC/AUD")
        assert isinstance(rd, RequestData)
        mock_http.assert_called_once()

    @patch.object(IndependentReserveRequestData, "http_request", return_value=SAMPLE_DEPTH)
    @pytest.mark.orderbook
    def test_get_depth(self, mock_http, feed):
        rd = feed.get_depth("BTC/AUD")
        assert isinstance(rd, RequestData)

    @patch.object(IndependentReserveRequestData, "http_request", return_value=SAMPLE_CURRENCIES)
    def test_get_exchange_info(self, mock_http, feed):
        rd = feed.get_exchange_info()
        assert isinstance(rd, RequestData)

    @patch.object(IndependentReserveRequestData, "http_request", return_value=SAMPLE_DEALS)
    def test_get_deals(self, mock_http, feed):
        rd = feed.get_deals("BTC/AUD")
        assert isinstance(rd, RequestData)

    @patch.object(IndependentReserveRequestData, "http_request", return_value=SAMPLE_ORDER)
    def test_make_order(self, mock_http, feed):
        rd = feed.make_order("BTC/AUD", "buy", "limit", 0.001, price=50000)
        assert isinstance(rd, RequestData)

    @patch.object(IndependentReserveRequestData, "http_request", return_value=SAMPLE_CANCEL)
    def test_cancel_order(self, mock_http, feed):
        rd = feed.cancel_order("abc-123")
        assert isinstance(rd, RequestData)

    @patch.object(IndependentReserveRequestData, "http_request", return_value=SAMPLE_OPEN_ORDERS)
    def test_get_open_orders(self, mock_http, feed):
        rd = feed.get_open_orders()
        assert isinstance(rd, RequestData)

    @patch.object(IndependentReserveRequestData, "http_request", return_value=SAMPLE_ACCOUNTS)
    def test_get_balance(self, mock_http, feed):
        rd = feed.get_balance()
        assert isinstance(rd, RequestData)

    @patch.object(IndependentReserveRequestData, "http_request", return_value=SAMPLE_ACCOUNTS)
    def test_get_account(self, mock_http, feed):
        rd = feed.get_account()
        assert isinstance(rd, RequestData)


# ═══════════════════════════════════════════════════════════════
# 5) Auth
# ═══════════════════════════════════════════════════════════════


class TestAuth:
    def test_headers(self, feed):
        h = feed._get_headers()
        assert h["Content-Type"] == "application/json"

    def test_no_api_key(self, feed):
        assert feed.api_key == ""

    def test_with_api_key(self):
        f = IndependentReserveRequestDataSpot(
            queue.Queue(), public_key="mykey", private_key="mysecret"
        )
        assert f.api_key == "mykey"
        assert f._api_secret == "mysecret"

    def test_signature_empty_without_secret(self, feed):
        sig = feed._generate_signature("http://example.com", 1234)
        assert sig == ""

    def test_signature_nonempty_with_secret(self):
        f = IndependentReserveRequestDataSpot(queue.Queue(), public_key="k", private_key="s")
        sig = f._generate_signature("http://example.com", 1234, {"foo": "bar"})
        assert len(sig) == 64
        assert sig == sig.upper()

    def test_sign_body(self):
        f = IndependentReserveRequestDataSpot(queue.Queue(), public_key="k", private_key="s")
        body = f._sign_body("http://example.com", {"foo": "bar"})
        assert "apiKey" in body
        assert "nonce" in body
        assert "signature" in body


# ═══════════════════════════════════════════════════════════════
# 6) Registry
# ═══════════════════════════════════════════════════════════════


class TestRegistry:
    def test_feed_registered(self):
        assert "INDEPENDENT_RESERVE___SPOT" in ExchangeRegistry._feed_classes

    def test_exchange_data_registered(self):
        assert "INDEPENDENT_RESERVE___SPOT" in ExchangeRegistry._exchange_data_classes

    def test_balance_handler_registered(self):
        assert "INDEPENDENT_RESERVE___SPOT" in ExchangeRegistry._balance_handlers

    def test_create_exchange_data(self):
        ed = ExchangeRegistry.create_exchange_data("INDEPENDENT_RESERVE___SPOT")
        assert isinstance(ed, IndependentReserveExchangeDataSpot)


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
    "get_deals",
    "async_get_deals",
    "get_recent_trades",
    "async_get_recent_trades",
    "make_order",
    "async_make_order",
    "cancel_order",
    "async_cancel_order",
    "get_open_orders",
    "async_get_open_orders",
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
        assert feed.exchange_name == "INDEPENDENT_RESERVE___SPOT"

    def test_default_asset_type(self, feed):
        assert feed.asset_type == "SPOT"

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
    @pytest.mark.skip(reason="Requires network access and API key")
    @pytest.mark.ticker
    def test_live_get_tick(self):
        f = IndependentReserveRequestDataSpot(queue.Queue())
        rd = f.get_tick("BTC/AUD")
        assert isinstance(rd, RequestData)

    @pytest.mark.skip(reason="Requires network access and API key")
    def test_live_get_exchange_info(self):
        f = IndependentReserveRequestDataSpot(queue.Queue())
        rd = f.get_exchange_info()
        assert isinstance(rd, RequestData)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
