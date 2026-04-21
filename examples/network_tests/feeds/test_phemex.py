"""
Test Phemex exchange integration.

Run tests:
    SKIP_LIVE_TESTS=true pytest tests/feeds/test_phemex.py -q --tb=short -o "addopts="
"""

import hashlib
import hmac
import os
import queue
from unittest.mock import patch

import pytest

# Import registration to auto-register Phemex
import bt_api_py.exchange_registers.register_phemex  # noqa: F401
from bt_api_py.containers.exchanges.phemex_exchange_data import (
    PhemexExchangeData,
    PhemexExchangeDataSpot,
)
from bt_api_base.containers.requestdatas.request_data import RequestData
from bt_api_py.containers.tickers.phemex_ticker import PhemexRequestTickerData
from bt_api_base.feeds.capability import Capability
from bt_api_py.feeds.live_phemex.request_base import PhemexRequestData
from bt_api_py.feeds.live_phemex.spot import PhemexRequestDataSpot
from bt_api_py.registry import ExchangeRegistry

SKIP_LIVE = os.environ.get("SKIP_LIVE_TESTS", "true").lower() in ("1", "true", "yes")


# ── Sample API responses ─────────────────────────────────────────────────

SAMPLE_TICKER_RESP = {
    "code": 0,
    "msg": "",
    "data": {
        "symbol": "sBTCUSDT",
        "lastEp": 5000000000000,
        "highEp": 5100000000000,
        "lowEp": 4900000000000,
        "openEp": 4950000000000,
        "volumeEv": 100000000000,
        "turnoverEv": 500000000000000,
        "bidEp": 4999900000000,
        "askEp": 5000100000000,
    },
}

SAMPLE_DEPTH_RESP = {
    "code": 0,
    "msg": "",
    "data": {
        "book": {
            "bids": [[4999900000000, 15000000], [4999800000000, 20000000]],
            "asks": [[5000100000000, 10000000], [5000200000000, 25000000]],
        }
    },
}

SAMPLE_KLINE_RESP = {
    "code": 0,
    "msg": "",
    "data": {
        "rows": [
            [1640995200, 5000000000000, 5050000000000, 4950000000000, 5020000000000, 100000000, 0],
            [1640998800, 5020000000000, 5060000000000, 5010000000000, 5040000000000, 80000000, 0],
        ]
    },
}

SAMPLE_TRADES_RESP = {
    "code": 0,
    "msg": "",
    "data": {
        "trades": [
            [5000000000000, 5000000, "Buy", 1640995200],
            [5001000000000, 3000000, "Sell", 1640995201],
        ]
    },
}

SAMPLE_EXCHANGE_INFO_RESP = {
    "code": 0,
    "msg": "",
    "data": {
        "products": [
            {
                "symbol": "sBTCUSDT",
                "type": "Spot",
                "baseCurrency": "BTC",
                "quoteCurrency": "USDT",
                "status": "Listed",
                "pricePrecision": 8,
                "baseTickSize": "0.00000001",
            },
            {
                "symbol": "sETHUSDT",
                "type": "Spot",
                "baseCurrency": "ETH",
                "quoteCurrency": "USDT",
                "status": "Listed",
                "pricePrecision": 8,
                "baseTickSize": "0.0001",
            },
        ]
    },
}

SAMPLE_SERVER_TIME_RESP = {
    "code": 0,
    "msg": "",
    "data": {"timestamp": 1640995200},
}

SAMPLE_BALANCE_RESP = {
    "code": 0,
    "msg": "",
    "data": [
        {"currency": "USDT", "balanceEv": 100000000000, "lockedBalanceEv": 5000000000},
        {"currency": "BTC", "balanceEv": 100000000, "lockedBalanceEv": 0},
    ],
}

SAMPLE_MAKE_ORDER_RESP = {
    "code": 0,
    "msg": "",
    "data": {
        "orderID": "abc-123-def",
        "clOrdID": "",
        "symbol": "sBTCUSDT",
        "side": "Buy",
        "orderType": "Limit",
        "priceEp": 5000000000000,
        "baseQtyEv": 100000,
        "ordStatus": "New",
    },
}

SAMPLE_CANCEL_ORDER_RESP = {"code": 0, "msg": ""}

SAMPLE_OPEN_ORDERS_RESP = {
    "code": 0,
    "msg": "",
    "data": {
        "rows": [
            {
                "orderID": "abc-123",
                "symbol": "sBTCUSDT",
                "side": "Buy",
                "priceEp": 4000000000000,
                "baseQtyEv": 100000,
                "ordStatus": "New",
            }
        ]
    },
}

SAMPLE_ERROR_RESP = {"code": 10001, "msg": "Invalid parameter"}


# ── Helper ────────────────────────────────────────────────────────────────


def _make_feed():
    """Create a PhemexRequestDataSpot instance with a queue."""
    return PhemexRequestDataSpot(queue.Queue())


# ═══════════════════════════════════════════════════════════════════════════
#  1. Exchange Data
# ═══════════════════════════════════════════════════════════════════════════


class TestExchangeData:
    def test_base_init(self):
        d = PhemexExchangeData()
        assert d.rest_url == "https://api.phemex.com"
        assert d.wss_url == "wss://ws.phemex.com"
        assert isinstance(d.kline_periods, dict)
        assert "1h" in d.kline_periods
        assert "USDT" in d.legal_currency

    def test_spot_init(self):
        d = PhemexExchangeDataSpot()
        assert d.exchange_name == "PHEMEX___SPOT"
        assert d.asset_type == "SPOT"
        assert d.rest_url == "https://api.phemex.com"

    def test_spot_rest_paths_populated(self):
        d = PhemexExchangeDataSpot()
        for key in (
            "get_server_time",
            "get_exchange_info",
            "get_tick",
            "get_depth",
            "get_kline",
            "get_trades",
            "get_account",
            "get_balance",
            "make_order",
            "cancel_order",
            "query_order",
            "get_open_orders",
        ):
            path = d.get_rest_path(key)
            assert path != "", f"Missing rest_path: {key}"

    def test_get_symbol_spot(self):
        d = PhemexExchangeDataSpot()
        assert d.get_symbol("BTC/USDT") == "sBTCUSDT"
        assert d.get_symbol("ETHUSDT") == "sETHUSDT"
        # Already prefixed
        assert d.get_symbol("sBTCUSDT") == "sBTCUSDT"

    def test_get_period(self):
        d = PhemexExchangeDataSpot()
        assert d.get_period("1m") == "60"
        assert d.get_period("1h") == "3600"
        assert d.get_period("1d") == "86400"

    def test_scale_unscale(self):
        d = PhemexExchangeDataSpot()
        scaled = d.scale_price(50000.0)
        assert scaled == int(50000.0 * 1e8)
        assert d.unscale_price(scaled) == 50000.0

    def test_get_rest_path_missing_raises(self):
        d = PhemexExchangeDataSpot()
        with pytest.raises(NotImplementedError):
            d.get_rest_path("nonexistent_path")


# ═══════════════════════════════════════════════════════════════════════════
#  2. Parameter Generation
# ═══════════════════════════════════════════════════════════════════════════


class TestParameterGeneration:
    def test_get_tick_params(self):
        f = _make_feed()
        path, params, ed = f._get_tick("BTC/USDT")
        assert "GET" in path
        assert "ticker" in path.lower() or "tick" in path.lower()
        assert params["symbol"] == "sBTCUSDT"
        assert ed["request_type"] == "get_tick"
        assert ed["symbol_name"] == "BTC/USDT"
        assert callable(ed["normalize_function"])

    def test_get_depth_params(self):
        f = _make_feed()
        path, params, ed = f._get_depth("BTC/USDT")
        assert "GET" in path
        assert "orderbook" in path.lower()
        assert params["symbol"] == "sBTCUSDT"
        assert ed["request_type"] == "get_depth"

    def test_get_kline_params(self):
        f = _make_feed()
        path, params, ed = f._get_kline("BTC/USDT", period="1h", count=50)
        assert "GET" in path
        assert "kline" in path.lower()
        assert params["symbol"] == "sBTCUSDT"
        assert params["resolution"] == "3600"
        assert params["limit"] == 50
        assert ed["request_type"] == "get_kline"
        assert ed["period"] == "1h"

    def test_get_kline_with_time_range(self):
        f = _make_feed()
        path, params, ed = f._get_kline(
            "BTC/USDT", period="1h", count=100, from_time=1000, to_time=2000
        )
        assert params["from"] == 1000
        assert params["to"] == 2000

    def test_get_trade_history_params(self):
        f = _make_feed()
        path, params, ed = f._get_trade_history("ETH/USDT")
        assert "GET" in path
        assert params["symbol"] == "sETHUSDT"
        assert ed["request_type"] == "get_trades"

    def test_get_exchange_info_params(self):
        f = _make_feed()
        path, params, ed = f._get_exchange_info()
        assert "GET" in path
        assert "products" in path.lower()
        assert params == {}
        assert ed["request_type"] == "get_exchange_info"

    def test_get_server_time_params(self):
        f = _make_feed()
        path, params, ed = f._get_server_time()
        assert "GET" in path
        assert "timestamp" in path.lower()
        assert params == {}
        assert ed["request_type"] == "get_server_time"

    def test_make_order_params(self):
        f = _make_feed()
        path, params, ed = f._make_order("BTC/USDT", 0.001, price=50000, order_type="buy-limit")
        assert "POST" in path
        assert params["symbol"] == "sBTCUSDT"
        assert params["side"] == "Buy"
        assert params["type"] == "Limit"
        assert params["baseQtyEv"] == int(0.001 * 1e8)
        assert params["priceEp"] == int(50000 * 1e8)
        assert ed["request_type"] == "make_order"

    def test_cancel_order_params(self):
        f = _make_feed()
        path, params, ed = f._cancel_order(symbol="BTC/USDT", order_id="abc-123")
        assert "DELETE" in path
        assert params["symbol"] == "sBTCUSDT"
        assert params["orderID"] == "abc-123"

    def test_query_order_params(self):
        f = _make_feed()
        path, params, ed = f._query_order(symbol="BTC/USDT")
        assert "GET" in path
        assert params["symbol"] == "sBTCUSDT"

    def test_get_open_orders_params(self):
        f = _make_feed()
        path, params, ed = f._get_open_orders(symbol="BTC/USDT")
        assert "GET" in path
        assert params["symbol"] == "sBTCUSDT"

    def test_get_account_params(self):
        f = _make_feed()
        path, params, ed = f._get_account()
        assert "GET" in path
        assert "wallets" in path.lower()

    def test_get_balance_params(self):
        f = _make_feed()
        path, params, ed = f._get_balance()
        assert "GET" in path
        assert "wallets" in path.lower()


# ═══════════════════════════════════════════════════════════════════════════
#  3. Normalize Functions
# ═══════════════════════════════════════════════════════════════════════════


class TestNormalization:
    @pytest.mark.ticker
    def test_tick_normalize_success(self):
        result, ok = PhemexRequestData._get_tick_normalize_function(
            SAMPLE_TICKER_RESP, {"symbol_name": "BTC/USDT", "asset_type": "SPOT"}
        )
        assert ok is True
        assert len(result) == 1
        assert isinstance(result[0], PhemexRequestTickerData)
        assert result[0].last_price == 50000.0

    @pytest.mark.ticker
    def test_tick_normalize_error(self):
        result, ok = PhemexRequestData._get_tick_normalize_function(SAMPLE_ERROR_RESP, {})
        assert ok is False
        assert result == []

    @pytest.mark.ticker
    def test_tick_normalize_empty(self):
        result, ok = PhemexRequestData._get_tick_normalize_function(None, {})
        assert ok is False

    @pytest.mark.orderbook
    def test_depth_normalize_success(self):
        result, ok = PhemexRequestData._get_depth_normalize_function(SAMPLE_DEPTH_RESP, {})
        assert ok is True
        assert len(result) == 1
        assert "bids" in result[0]
        assert "asks" in result[0]

    @pytest.mark.orderbook
    def test_depth_normalize_error(self):
        result, ok = PhemexRequestData._get_depth_normalize_function(SAMPLE_ERROR_RESP, {})
        assert ok is False

    @pytest.mark.kline
    def test_kline_normalize_success(self):
        result, ok = PhemexRequestData._get_kline_normalize_function(SAMPLE_KLINE_RESP, {})
        assert ok is True
        assert len(result) == 2

    @pytest.mark.kline
    def test_kline_normalize_error(self):
        result, ok = PhemexRequestData._get_kline_normalize_function(SAMPLE_ERROR_RESP, {})
        assert ok is False

    def test_trade_history_normalize_success(self):
        result, ok = PhemexRequestData._get_trade_history_normalize_function(SAMPLE_TRADES_RESP, {})
        assert ok is True
        assert len(result) == 2

    def test_exchange_info_normalize_success(self):
        result, ok = PhemexRequestData._get_exchange_info_normalize_function(
            SAMPLE_EXCHANGE_INFO_RESP, {}
        )
        assert ok is True
        assert len(result) == 1
        assert "symbols" in result[0]
        assert len(result[0]["symbols"]) == 2

    def test_server_time_normalize_success(self):
        result, ok = PhemexRequestData._get_server_time_normalize_function(
            SAMPLE_SERVER_TIME_RESP, {}
        )
        assert ok is True
        assert len(result) == 1

    def test_make_order_normalize_success(self):
        result, ok = PhemexRequestData._make_order_normalize_function(SAMPLE_MAKE_ORDER_RESP, {})
        assert ok is True
        assert result[0]["orderID"] == "abc-123-def"

    def test_cancel_order_normalize_success(self):
        result, ok = PhemexRequestData._cancel_order_normalize_function(
            SAMPLE_CANCEL_ORDER_RESP, {}
        )
        assert ok is True
        assert result[0]["success"] is True

    def test_cancel_order_normalize_error(self):
        result, ok = PhemexRequestData._cancel_order_normalize_function(SAMPLE_ERROR_RESP, {})
        assert ok is False

    def test_open_orders_normalize_success(self):
        result, ok = PhemexRequestData._get_open_orders_normalize_function(
            SAMPLE_OPEN_ORDERS_RESP, {}
        )
        assert ok is True
        assert len(result) == 1

    def test_account_normalize_success(self):
        result, ok = PhemexRequestData._get_account_normalize_function(SAMPLE_BALANCE_RESP, {})
        assert ok is True
        assert len(result) == 2

    def test_balance_normalize_success(self):
        result, ok = PhemexRequestData._get_balance_normalize_function(SAMPLE_BALANCE_RESP, {})
        assert ok is True
        assert len(result) == 2


# ═══════════════════════════════════════════════════════════════════════════
#  4. Mocked Synchronous Calls
# ═══════════════════════════════════════════════════════════════════════════


class TestMockedSyncCalls:
    """Test each sync method with mocked http_request."""

    @patch.object(PhemexRequestData, "http_request", return_value=SAMPLE_SERVER_TIME_RESP)
    def test_get_server_time(self, mock_req):
        f = _make_feed()
        rd = f.get_server_time()
        assert isinstance(rd, RequestData)
        mock_req.assert_called_once()
        args = mock_req.call_args
        assert args[0][0] == "GET"
        assert "timestamp" in args[0][1]

    @patch.object(PhemexRequestData, "http_request", return_value=SAMPLE_EXCHANGE_INFO_RESP)
    def test_get_exchange_info(self, mock_req):
        f = _make_feed()
        rd = f.get_exchange_info()
        assert isinstance(rd, RequestData)
        mock_req.assert_called_once()

    @patch.object(PhemexRequestData, "http_request", return_value=SAMPLE_TICKER_RESP)
    def test_get_tick(self, mock_req):
        f = _make_feed()
        rd = f.get_tick("BTC/USDT")
        assert isinstance(rd, RequestData)
        mock_req.assert_called_once()
        url = mock_req.call_args[0][1]
        assert "sBTCUSDT" in url

    @patch.object(PhemexRequestData, "http_request", return_value=SAMPLE_TICKER_RESP)
    def test_get_ticker_alias(self, mock_req):
        f = _make_feed()
        rd = f.get_ticker("BTC/USDT")
        assert isinstance(rd, RequestData)

    @patch.object(PhemexRequestData, "http_request", return_value=SAMPLE_DEPTH_RESP)
    def test_get_depth(self, mock_req):
        f = _make_feed()
        rd = f.get_depth("BTC/USDT", count=20)
        assert isinstance(rd, RequestData)
        url = mock_req.call_args[0][1]
        assert "sBTCUSDT" in url

    @patch.object(PhemexRequestData, "http_request", return_value=SAMPLE_KLINE_RESP)
    def test_get_kline(self, mock_req):
        f = _make_feed()
        rd = f.get_kline("BTC/USDT", period="1h", count=50)
        assert isinstance(rd, RequestData)
        url = mock_req.call_args[0][1]
        assert "resolution=3600" in url

    @patch.object(PhemexRequestData, "http_request", return_value=SAMPLE_TRADES_RESP)
    def test_get_trade_history(self, mock_req):
        f = _make_feed()
        rd = f.get_trade_history("BTC/USDT")
        assert isinstance(rd, RequestData)

    @patch.object(PhemexRequestData, "http_request", return_value=SAMPLE_MAKE_ORDER_RESP)
    def test_make_order(self, mock_req):
        f = _make_feed()
        f.api_key = "test_key"
        f.api_secret = "test_secret"
        rd = f.make_order("BTC/USDT", 0.001, price=50000, order_type="buy-limit")
        assert isinstance(rd, RequestData)
        mock_req.assert_called_once()

    @patch.object(PhemexRequestData, "http_request", return_value=SAMPLE_CANCEL_ORDER_RESP)
    def test_cancel_order(self, mock_req):
        f = _make_feed()
        f.api_key = "test_key"
        f.api_secret = "test_secret"
        rd = f.cancel_order(symbol="BTC/USDT", order_id="abc-123")
        assert isinstance(rd, RequestData)

    @patch.object(PhemexRequestData, "http_request", return_value=SAMPLE_OPEN_ORDERS_RESP)
    def test_query_order(self, mock_req):
        f = _make_feed()
        f.api_key = "test_key"
        f.api_secret = "test_secret"
        rd = f.query_order(symbol="BTC/USDT")
        assert isinstance(rd, RequestData)

    @patch.object(PhemexRequestData, "http_request", return_value=SAMPLE_OPEN_ORDERS_RESP)
    def test_get_open_orders(self, mock_req):
        f = _make_feed()
        f.api_key = "test_key"
        f.api_secret = "test_secret"
        rd = f.get_open_orders(symbol="BTC/USDT")
        assert isinstance(rd, RequestData)

    @patch.object(PhemexRequestData, "http_request", return_value=SAMPLE_BALANCE_RESP)
    def test_get_account(self, mock_req):
        f = _make_feed()
        f.api_key = "test_key"
        f.api_secret = "test_secret"
        rd = f.get_account()
        assert isinstance(rd, RequestData)

    @patch.object(PhemexRequestData, "http_request", return_value=SAMPLE_BALANCE_RESP)
    def test_get_balance(self, mock_req):
        f = _make_feed()
        f.api_key = "test_key"
        f.api_secret = "test_secret"
        rd = f.get_balance()
        assert isinstance(rd, RequestData)


# ═══════════════════════════════════════════════════════════════════════════
#  5. Container Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestContainers:
    @pytest.mark.ticker
    def test_ticker_container_parse(self):
        ticker = PhemexRequestTickerData(SAMPLE_TICKER_RESP, "BTC/USDT", "SPOT")
        assert ticker.symbol == "sBTCUSDT"
        assert ticker.last_price == 50000.0
        assert ticker.high_price == 51000.0
        assert ticker.low_price == 49000.0
        assert ticker.bid_price == 49999.0
        assert ticker.ask_price == 50001.0

    @pytest.mark.ticker
    def test_ticker_validate(self):
        ticker = PhemexRequestTickerData(SAMPLE_TICKER_RESP, "BTC/USDT", "SPOT")
        assert ticker.validate() is True

    @pytest.mark.ticker
    def test_ticker_to_dict(self):
        ticker = PhemexRequestTickerData(SAMPLE_TICKER_RESP, "BTC/USDT", "SPOT")
        d = ticker.to_dict()
        assert d["symbol"] == "sBTCUSDT"
        assert d["last_price"] == 50000.0


# ═══════════════════════════════════════════════════════════════════════════
#  6. Registry Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestRegistry:
    def test_feed_registered(self):
        assert ExchangeRegistry.has_exchange("PHEMEX___SPOT")
        cls = ExchangeRegistry._feed_classes.get("PHEMEX___SPOT")
        assert cls is PhemexRequestDataSpot

    def test_exchange_data_registered(self):
        cls = ExchangeRegistry._exchange_data_classes.get("PHEMEX___SPOT")
        assert cls is PhemexExchangeDataSpot

    def test_balance_handler_registered(self):
        h = ExchangeRegistry._balance_handlers.get("PHEMEX___SPOT")
        assert h is not None

    def test_create_feed(self):
        q = queue.Queue()
        f = ExchangeRegistry.create_feed("PHEMEX___SPOT", q)
        assert isinstance(f, PhemexRequestDataSpot)
        assert f.exchange_name == "PHEMEX___SPOT"

    def test_create_exchange_data(self):
        d = ExchangeRegistry.create_exchange_data("PHEMEX___SPOT")
        assert isinstance(d, PhemexExchangeDataSpot)


# ═══════════════════════════════════════════════════════════════════════════
#  7. Method Existence & Capabilities
# ═══════════════════════════════════════════════════════════════════════════


class TestMethodExistence:
    def test_capabilities(self):
        caps = PhemexRequestData._capabilities()
        for c in [
            Capability.GET_TICK,
            Capability.GET_DEPTH,
            Capability.GET_KLINE,
            Capability.GET_DEALS,
            Capability.MAKE_ORDER,
            Capability.CANCEL_ORDER,
            Capability.QUERY_ORDER,
            Capability.QUERY_OPEN_ORDERS,
            Capability.GET_BALANCE,
            Capability.GET_ACCOUNT,
            Capability.GET_EXCHANGE_INFO,
            Capability.GET_SERVER_TIME,
        ]:
            assert c in caps, f"Missing capability: {c}"

    def test_public_sync_methods(self):
        f = _make_feed()
        for m in (
            "get_server_time",
            "get_exchange_info",
            "get_tick",
            "get_ticker",
            "get_depth",
            "get_kline",
            "get_trade_history",
        ):
            assert callable(getattr(f, m, None)), f"Missing method: {m}"

    def test_private_sync_methods(self):
        f = _make_feed()
        for m in (
            "make_order",
            "cancel_order",
            "query_order",
            "get_open_orders",
            "get_account",
            "get_balance",
        ):
            assert callable(getattr(f, m, None)), f"Missing method: {m}"

    def test_public_async_methods(self):
        f = _make_feed()
        for m in (
            "async_get_server_time",
            "async_get_exchange_info",
            "async_get_tick",
            "async_get_depth",
            "async_get_kline",
            "async_get_trade_history",
        ):
            assert callable(getattr(f, m, None)), f"Missing async method: {m}"

    def test_private_async_methods(self):
        f = _make_feed()
        for m in (
            "async_make_order",
            "async_cancel_order",
            "async_query_order",
            "async_get_open_orders",
            "async_get_account",
            "async_get_balance",
        ):
            assert callable(getattr(f, m, None)), f"Missing async method: {m}"

    def test_internal_methods(self):
        f = _make_feed()
        for m in (
            "_get_server_time",
            "_get_exchange_info",
            "_get_tick",
            "_get_depth",
            "_get_kline",
            "_get_trade_history",
            "_make_order",
            "_cancel_order",
            "_query_order",
            "_get_open_orders",
            "_get_account",
            "_get_balance",
        ):
            assert callable(getattr(f, m, None)), f"Missing internal method: {m}"


# ═══════════════════════════════════════════════════════════════════════════
#  8. Signature Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestSignature:
    def test_sign_basic(self):
        f = _make_feed()
        f.api_secret = "my_secret"
        sig = f.sign("/spot/orders/active", "symbol=sBTCUSDT", "1700000060", "")
        # Verify it matches manual computation
        sign_str = "/spot/orders/active" + "symbol=sBTCUSDT" + "1700000060" + ""
        expected = hmac.new(b"my_secret", sign_str.encode("utf-8"), hashlib.sha256).hexdigest()
        assert sig == expected

    def test_sign_with_body(self):
        f = _make_feed()
        f.api_secret = "test_sec"
        body = '{"symbol":"sBTCUSDT","side":"Buy"}'
        sig = f.sign("/spot/orders/create", "", "1700000060", body)
        sign_str = "/spot/orders/create" + "" + "1700000060" + body
        expected = hmac.new(b"test_sec", sign_str.encode("utf-8"), hashlib.sha256).hexdigest()
        assert sig == expected

    @patch.object(PhemexRequestData, "http_request", return_value=SAMPLE_BALANCE_RESP)
    def test_signed_request_headers(self, mock_req):
        f = _make_feed()
        f.api_key = "my_key"
        f.api_secret = "my_secret"
        f.get_balance()
        headers = mock_req.call_args[0][2]
        assert headers.get("x-phemex-access-token") == "my_key"
        assert "x-phemex-request-expiry" in headers
        assert "x-phemex-request-signature" in headers

    @patch.object(PhemexRequestData, "http_request", return_value=SAMPLE_TICKER_RESP)
    def test_unsigned_request_no_auth_headers(self, mock_req):
        f = _make_feed()
        f.api_key = "my_key"
        f.api_secret = "my_secret"
        f.get_tick("BTC/USDT")
        headers = mock_req.call_args[0][2]
        assert "x-phemex-access-token" not in headers


# ═══════════════════════════════════════════════════════════════════════════
#  9. Integration Tests (skipped by default)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.integration
@pytest.mark.skipif(SKIP_LIVE, reason="SKIP_LIVE_TESTS is set")
class TestLiveIntegration:
    def test_get_server_time(self):
        f = _make_feed()
        rd = f.get_server_time()
        assert isinstance(rd, RequestData)

    def test_get_tick(self):
        f = _make_feed()
        rd = f.get_tick("BTC/USDT")
        assert isinstance(rd, RequestData)

    def test_get_depth(self):
        f = _make_feed()
        rd = f.get_depth("BTC/USDT")
        assert isinstance(rd, RequestData)

    def test_get_kline(self):
        f = _make_feed()
        rd = f.get_kline("BTC/USDT", period="1h", count=10)
        assert isinstance(rd, RequestData)

    def test_get_exchange_info(self):
        f = _make_feed()
        rd = f.get_exchange_info()
        assert isinstance(rd, RequestData)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
