"""
Tests for CoinSwitch exchange – Feed pattern.

Run:  pytest tests/feeds/test_coinswitch.py -v
"""

import queue
from unittest.mock import patch

import pytest

from bt_api_py.containers.exchanges.coinswitch_exchange_data import CoinSwitchExchangeDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_coinswitch.request_base import CoinSwitchRequestData
from bt_api_py.feeds.live_coinswitch.spot import CoinSwitchRequestDataSpot
from bt_api_py.registry import ExchangeRegistry

import bt_api_py.exchange_registers.register_coinswitch  # noqa: F401

# ── sample response fixtures ─────────────────────────────────

SAMPLE_TICK = {"data": {"symbol": "BTCINR", "last": "5500000", "bid": "5490000", "ask": "5510000"}}

SAMPLE_ALL_TICKERS = {"data": [
    {"symbol": "BTCINR", "last": "5500000"},
    {"symbol": "ETHINR", "last": "250000"},
]}

SAMPLE_EXCHANGE_INFO = {"data": [
    {"symbol": "BTCINR", "base": "BTC", "quote": "INR"},
    {"symbol": "ETHINR", "base": "ETH", "quote": "INR"},
]}

SAMPLE_TRADES = {"data": [
    {"id": "t1", "price": "5500000", "amount": "0.01", "side": "buy"},
]}

SAMPLE_ORDER = {"data": {"orderId": "ord123", "status": "open"}}

SAMPLE_CANCEL = {"message": "Order cancelled"}

SAMPLE_OPEN_ORDERS = {"data": [{"orderId": "ord123", "symbol": "BTCINR"}]}

SAMPLE_BALANCE = {"data": {"BTC": "1.5", "INR": "100000"}}

SAMPLE_ERROR = {"error": {"code": 400, "message": "Bad request"}}


# ── helpers ───────────────────────────────────────────────────

@pytest.fixture
def feed():
    return CoinSwitchRequestDataSpot(queue.Queue())


@pytest.fixture
def exdata():
    return CoinSwitchExchangeDataSpot()


# ═══════════════════════════════════════════════════════════════
# 1) ExchangeData
# ═══════════════════════════════════════════════════════════════

class TestExchangeData:
    def test_exchange_name(self, exdata):
        assert exdata.exchange_name == "COINSWITCH___SPOT"

    def test_asset_type(self, exdata):
        assert exdata.asset_type == "SPOT"

    def test_rest_url(self, exdata):
        assert exdata.rest_url == "https://api.coinswitch.co"

    def test_wss_url(self, exdata):
        assert exdata.wss_url == ""

    def test_get_symbol(self, exdata):
        assert CoinSwitchExchangeDataSpot.get_symbol("BTCINR") == "BTCINR"

    def test_get_period(self, exdata):
        assert exdata.get_period("1h") == "60"
        assert exdata.get_period("1d") == "D"

    def test_kline_periods(self, exdata):
        for k in ("1m", "5m", "15m", "30m", "1h", "4h", "1d"):
            assert k in exdata.kline_periods

    def test_legal_currency(self, exdata):
        for c in ("INR", "USD", "EUR", "USDT", "BTC", "ETH"):
            assert c in exdata.legal_currency

    def test_get_rest_path_ok(self, exdata):
        p = exdata.get_rest_path("get_tick")
        assert "/v2/tickers" in p

    def test_get_rest_path_missing(self, exdata):
        with pytest.raises(ValueError):
            exdata.get_rest_path("nonexistent")

    def test_rest_paths_keys(self, exdata):
        for key in ("get_tick", "get_exchange_info", "get_trade_history",
                     "get_balance", "get_account", "make_order",
                     "cancel_order", "get_open_orders"):
            assert key in exdata.rest_paths


# ═══════════════════════════════════════════════════════════════
# 2) Parameter generation (_get_xxx)
# ═══════════════════════════════════════════════════════════════

class TestParamGeneration:
    def test_get_tick_params(self, feed):
        path, params, extra = feed._get_tick("BTCINR")
        assert "GET" in path
        assert "/v2/tickers/BTCINR" in path
        assert extra["request_type"] == "get_tick"
        assert extra["symbol_name"] == "BTCINR"

    def test_get_all_tickers_params(self, feed):
        path, params, extra = feed._get_all_tickers()
        assert "/v2/tickers" in path
        assert extra["request_type"] == "get_ticker_all"

    def test_get_exchange_info_params(self, feed):
        path, params, extra = feed._get_exchange_info()
        assert "/v2/markets" in path
        assert extra["request_type"] == "get_exchange_info"

    def test_get_trade_history_params(self, feed):
        path, params, extra = feed._get_trade_history("BTCINR")
        assert "/v2/trades" in path
        assert params["symbol"] == "BTCINR"
        assert extra["request_type"] == "get_trade_history"

    def test_make_order_params(self, feed):
        path, body, extra = feed._make_order("BTCINR", "buy", "limit", 0.001, price=5500000)
        assert "POST" in path
        assert "/v2/orders" in path
        assert body["symbol"] == "BTCINR"
        assert body["side"] == "buy"
        assert body["amount"] == 0.001
        assert body["price"] == 5500000

    def test_make_order_market(self, feed):
        path, body, extra = feed._make_order("BTCINR", "buy", "market", 0.001)
        assert "price" not in body

    def test_cancel_order_params(self, feed):
        path, params, extra = feed._cancel_order("ord123")
        assert "DELETE" in path
        assert "/v2/orders/ord123" in path
        assert extra["request_type"] == "cancel_order"

    def test_get_open_orders_params(self, feed):
        path, params, extra = feed._get_open_orders()
        assert "/v2/orders" in path
        assert extra["request_type"] == "get_open_orders"

    def test_get_balance_params(self, feed):
        path, params, extra = feed._get_balance()
        assert "/v2/account/balance" in path
        assert extra["request_type"] == "get_balance"

    def test_get_account_params(self, feed):
        path, params, extra = feed._get_account()
        assert "/v2/account/balance" in path
        assert extra["request_type"] == "get_account"


# ═══════════════════════════════════════════════════════════════
# 3) Normalization functions
# ═══════════════════════════════════════════════════════════════

class TestNormalization:
    def test_tick_ok(self):
        result, ok = CoinSwitchRequestData._get_tick_normalize_function(SAMPLE_TICK, {})
        assert ok is True
        assert result[0]["symbol"] == "BTCINR"

    def test_tick_error(self):
        result, ok = CoinSwitchRequestData._get_tick_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_all_tickers_ok(self):
        result, ok = CoinSwitchRequestData._get_all_tickers_normalize_function(SAMPLE_ALL_TICKERS, {})
        assert ok is True

    def test_exchange_info_ok(self):
        result, ok = CoinSwitchRequestData._get_exchange_info_normalize_function(SAMPLE_EXCHANGE_INFO, {})
        assert ok is True

    def test_trade_history_ok(self):
        result, ok = CoinSwitchRequestData._get_trade_history_normalize_function(SAMPLE_TRADES, {})
        assert ok is True
        assert result[0]["id"] == "t1"

    def test_make_order_ok(self):
        result, ok = CoinSwitchRequestData._make_order_normalize_function(SAMPLE_ORDER, {})
        assert ok is True

    def test_make_order_error(self):
        result, ok = CoinSwitchRequestData._make_order_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_cancel_order_ok(self):
        result, ok = CoinSwitchRequestData._cancel_order_normalize_function(SAMPLE_CANCEL, {})
        assert ok is True

    def test_open_orders_ok(self):
        result, ok = CoinSwitchRequestData._get_open_orders_normalize_function(SAMPLE_OPEN_ORDERS, {})
        assert ok is True

    def test_balance_ok(self):
        result, ok = CoinSwitchRequestData._get_balance_normalize_function(SAMPLE_BALANCE, {})
        assert ok is True

    def test_balance_error(self):
        result, ok = CoinSwitchRequestData._get_balance_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_account_ok(self):
        result, ok = CoinSwitchRequestData._get_account_normalize_function(SAMPLE_BALANCE, {})
        assert ok is True

    def test_is_error_none(self):
        assert CoinSwitchRequestData._is_error(None) is True

    def test_is_error_error(self):
        assert CoinSwitchRequestData._is_error(SAMPLE_ERROR) is True

    def test_is_error_ok(self):
        assert CoinSwitchRequestData._is_error(SAMPLE_TICK) is False

    def test_unwrap(self):
        assert CoinSwitchRequestData._unwrap(SAMPLE_TICK)["symbol"] == "BTCINR"
        assert CoinSwitchRequestData._unwrap(None) is None


# ═══════════════════════════════════════════════════════════════
# 4) Mocked sync calls
# ═══════════════════════════════════════════════════════════════

class TestSyncCalls:
    @patch.object(CoinSwitchRequestData, "http_request", return_value=SAMPLE_TICK)
    def test_get_tick(self, mock_http, feed):
        rd = feed.get_tick("BTCINR")
        assert isinstance(rd, RequestData)
        mock_http.assert_called_once()

    @patch.object(CoinSwitchRequestData, "http_request", return_value=SAMPLE_ALL_TICKERS)
    def test_get_all_tickers(self, mock_http, feed):
        rd = feed.get_all_tickers()
        assert isinstance(rd, RequestData)

    @patch.object(CoinSwitchRequestData, "http_request", return_value=SAMPLE_EXCHANGE_INFO)
    def test_get_exchange_info(self, mock_http, feed):
        rd = feed.get_exchange_info()
        assert isinstance(rd, RequestData)

    @patch.object(CoinSwitchRequestData, "http_request", return_value=SAMPLE_TRADES)
    def test_get_trade_history(self, mock_http, feed):
        rd = feed.get_trade_history("BTCINR")
        assert isinstance(rd, RequestData)

    @patch.object(CoinSwitchRequestData, "http_request", return_value=SAMPLE_ORDER)
    def test_make_order(self, mock_http, feed):
        rd = feed.make_order("BTCINR", "buy", "limit", 0.001, price=5500000)
        assert isinstance(rd, RequestData)

    @patch.object(CoinSwitchRequestData, "http_request", return_value=SAMPLE_CANCEL)
    def test_cancel_order(self, mock_http, feed):
        rd = feed.cancel_order("ord123")
        assert isinstance(rd, RequestData)

    @patch.object(CoinSwitchRequestData, "http_request", return_value=SAMPLE_OPEN_ORDERS)
    def test_get_open_orders(self, mock_http, feed):
        rd = feed.get_open_orders()
        assert isinstance(rd, RequestData)

    @patch.object(CoinSwitchRequestData, "http_request", return_value=SAMPLE_BALANCE)
    def test_get_balance(self, mock_http, feed):
        rd = feed.get_balance()
        assert isinstance(rd, RequestData)

    @patch.object(CoinSwitchRequestData, "http_request", return_value=SAMPLE_BALANCE)
    def test_get_account(self, mock_http, feed):
        rd = feed.get_account()
        assert isinstance(rd, RequestData)


# ═══════════════════════════════════════════════════════════════
# 5) Auth headers
# ═══════════════════════════════════════════════════════════════

class TestAuth:
    def test_no_api_key(self, feed):
        headers = feed._get_headers()
        assert "x-api-key" not in headers

    def test_api_key_property(self, feed):
        assert feed.api_key == ""

    def test_with_api_key(self):
        f = CoinSwitchRequestDataSpot(queue.Queue(), api_key="mykey")
        headers = f._get_headers()
        assert headers["x-api-key"] == "mykey"
        assert f.api_key == "mykey"


# ═══════════════════════════════════════════════════════════════
# 6) Registry
# ═══════════════════════════════════════════════════════════════

class TestRegistry:
    def test_feed_registered(self):
        assert "COINSWITCH___SPOT" in ExchangeRegistry._feed_classes

    def test_exchange_data_registered(self):
        assert "COINSWITCH___SPOT" in ExchangeRegistry._exchange_data_classes

    def test_balance_handler_registered(self):
        assert "COINSWITCH___SPOT" in ExchangeRegistry._balance_handlers

    def test_create_exchange_data(self):
        ed = ExchangeRegistry.create_exchange_data("COINSWITCH___SPOT")
        assert isinstance(ed, CoinSwitchExchangeDataSpot)


# ═══════════════════════════════════════════════════════════════
# 7) Method existence
# ═══════════════════════════════════════════════════════════════

_EXPECTED_METHODS = [
    "get_tick", "async_get_tick",
    "get_ticker", "async_get_ticker",
    "get_all_tickers", "async_get_all_tickers",
    "get_exchange_info", "async_get_exchange_info",
    "get_trade_history", "async_get_trade_history",
    "get_trades", "async_get_trades",
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
        assert feed.exchange_name == "COINSWITCH___SPOT"

    def test_default_asset_type(self, feed):
        assert feed.asset_type == "SPOT"

    def test_api_key(self):
        f = CoinSwitchRequestDataSpot(queue.Queue(), api_key="ak")
        assert f.api_key == "ak"

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
    def test_live_get_tick(self):
        f = CoinSwitchRequestDataSpot(queue.Queue(), api_key="test")
        rd = f.get_tick("BTCINR")
        assert isinstance(rd, RequestData)

    @pytest.mark.skip(reason="Requires network access and API key")
    def test_live_get_exchange_info(self):
        f = CoinSwitchRequestDataSpot(queue.Queue(), api_key="test")
        rd = f.get_exchange_info()
        assert isinstance(rd, RequestData)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
