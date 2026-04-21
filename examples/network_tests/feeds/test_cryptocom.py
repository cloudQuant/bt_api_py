"""
Test Crypto.com exchange integration.

Run tests:
    SKIP_LIVE_TESTS=true pytest tests/feeds/test_cryptocom.py -q --tb=short -o "addopts="
"""

import queue
from unittest.mock import patch

import pytest

# Import registration to auto-register Crypto.com
import bt_api_py.exchange_registers.register_cryptocom  # noqa: F401
from bt_api_py.containers.exchanges.cryptocom_exchange_data import (
    CryptoComExchangeDataSpot,
)
from bt_api_py.containers.orderbooks.cryptocom_orderbook import CryptoComOrderBook
from bt_api_py.containers.orders.cryptocom_order import CryptoComOrder
from bt_api_base.containers.requestdatas.request_data import RequestData
from bt_api_py.containers.tickers.cryptocom_ticker import CryptoComTicker
from bt_api_base.feeds.capability import Capability
from bt_api_py.feeds.live_cryptocom.request_base import CryptoComRequestData
from bt_api_py.feeds.live_cryptocom.spot import CryptoComRequestDataSpot
from bt_api_py.registry import ExchangeRegistry

# ── Sample API responses ─────────────────────────────────────────────────

SAMPLE_TICKER_RESP = {
    "id": -1,
    "method": "public/get-tickers",
    "code": 0,
    "result": {
        "data": [
            {
                "i": "BTC_USDT",
                "a": "50000.0",
                "b": "49900.0",
                "k": "50100.0",
                "h": "51000.0",
                "l": "48000.0",
                "v": "1234.56",
                "vv": "60000000",
                "t": 1640995200000,
            }
        ]
    },
}

SAMPLE_ORDERBOOK_RESP = {
    "id": -1,
    "method": "public/get-book",
    "code": 0,
    "result": {
        "data": [
            {
                "bids": [["49990", "1.5", "1"], ["49980", "2.0", "2"]],
                "asks": [["50010", "1.0", "1"], ["50020", "2.5", "2"]],
            }
        ]
    },
}

SAMPLE_KLINE_RESP = {
    "id": -1,
    "method": "public/get-candlestick",
    "code": 0,
    "result": {
        "data": [
            {
                "t": 1640995200000,
                "o": "50000",
                "h": "50500",
                "l": "49500",
                "c": "50200",
                "v": "1000",
            },
            {
                "t": 1640998800000,
                "o": "50200",
                "h": "50600",
                "l": "50100",
                "c": "50400",
                "v": "800",
            },
        ]
    },
}

SAMPLE_TRADES_RESP = {
    "id": -1,
    "method": "public/get-trades",
    "code": 0,
    "result": {
        "data": [
            {"p": "50000", "q": "0.5", "s": "BUY", "t": 1640995200000},
            {"p": "50010", "q": "0.3", "s": "SELL", "t": 1640995201000},
        ]
    },
}

SAMPLE_EXCHANGE_INFO_RESP = {
    "id": -1,
    "method": "public/get-instruments",
    "code": 0,
    "result": {
        "data": [
            {
                "symbol": "BTC_USDT",
                "instrument_name": "BTC_USDT",
                "base_ccy": "BTC",
                "quote_ccy": "USDT",
                "price_decimals": 2,
                "quantity_decimals": 6,
                "status": "ACTIVE",
            }
        ]
    },
}

SAMPLE_SERVER_TIME_RESP = {
    "id": -1,
    "method": "public/get-ticker",
    "code": 0,
    "result": {"data": 1640995200000},
}

SAMPLE_MAKE_ORDER_RESP = {
    "id": 1,
    "method": "private/create-order",
    "code": 0,
    "result": {
        "order_id": "12345678",
        "side": "BUY",
        "type": "LIMIT",
        "quantity": "0.001",
        "price": "50000",
        "status": "ACTIVE",
    },
}

SAMPLE_CANCEL_ORDER_RESP = {
    "id": 1,
    "method": "private/cancel-order",
    "code": 0,
}

SAMPLE_QUERY_ORDER_RESP = {
    "id": 1,
    "method": "private/get-order-detail",
    "code": 0,
    "result": {
        "order_id": "12345678",
        "instrument_name": "BTC_USDT",
        "side": "BUY",
        "type": "LIMIT",
        "quantity": "0.001",
        "price": "50000",
        "status": "ACTIVE",
        "filled_quantity": "0",
        "remaining_quantity": "0.001",
    },
}

SAMPLE_OPEN_ORDERS_RESP = {
    "id": 1,
    "method": "private/get-open-orders",
    "code": 0,
    "result": {
        "data": [
            {
                "order_id": "12345678",
                "instrument_name": "BTC_USDT",
                "side": "BUY",
                "type": "LIMIT",
                "quantity": "0.001",
                "price": "50000",
                "status": "ACTIVE",
            }
        ]
    },
}

SAMPLE_ACCOUNT_RESP = {
    "id": 1,
    "method": "private/get-account-summary",
    "code": 0,
    "result": {
        "data": [
            {"instrument_name": "BTC", "total_balance": "0.5", "total_available": "0.5"},
            {"instrument_name": "USDT", "total_balance": "10000", "total_available": "9500"},
        ]
    },
}

SAMPLE_BALANCE_RESP = SAMPLE_ACCOUNT_RESP


# ── Helper ────────────────────────────────────────────────────────────────


def _make_feed():
    """Create a CryptoComRequestDataSpot feed for testing."""
    data_queue = queue.Queue()
    return CryptoComRequestDataSpot(
        data_queue,
        public_key="test_key",
        private_key="test_secret",
    )


# ══════════════════════════════════════════════════════════════════════════
# 1. Exchange Data
# ══════════════════════════════════════════════════════════════════════════


class TestCryptoComExchangeData:
    """Test Crypto.com exchange data configuration."""

    def test_exchange_data_spot_creation(self):
        ed = CryptoComExchangeDataSpot()
        assert ed.exchange_name == "CRYPTOCOM___SPOT"
        assert ed.rest_url
        assert ed.wss_url
        assert ed.acct_wss_url

    def test_get_symbol(self):
        ed = CryptoComExchangeDataSpot()
        assert ed.get_symbol("BTC/USDT") == "BTC_USDT"
        assert ed.get_symbol("ETH-USDT") == "ETH_USDT"
        assert ed.get_symbol("BTC_USDT") == "BTC_USDT"

    def test_get_instrument_name(self):
        ed = CryptoComExchangeDataSpot()
        assert ed.get_instrument_name("BTC/USDT") == "BTC_USDT"

    def test_get_symbol_from_instrument(self):
        ed = CryptoComExchangeDataSpot()
        assert ed.get_symbol_from_instrument("BTC_USDT") == "BTC/USDT"

    def test_validate_symbol(self):
        ed = CryptoComExchangeDataSpot()
        assert ed.validate_symbol("BTC/USDT") is True
        assert ed.validate_symbol("BTC_USDT") is True
        assert ed.validate_symbol("") is False

    @pytest.mark.kline
    def test_get_kline_period(self):
        ed = CryptoComExchangeDataSpot()
        assert ed.get_kline_period("1m") == "1m"
        assert ed.get_kline_period("1d") == "1D"

    @pytest.mark.kline
    def test_get_period_from_kline(self):
        ed = CryptoComExchangeDataSpot()
        assert ed.get_period_from_kline("1D") == "1d"

    @pytest.mark.orderbook
    def test_get_depth_levels(self):
        ed = CryptoComExchangeDataSpot()
        assert ed.get_depth_levels(50) == 50
        assert ed.get_depth_levels(100) == 50
        assert ed.get_depth_levels(0) == 1

    def test_rest_paths_present(self):
        ed = CryptoComExchangeDataSpot()
        for key in [
            "get_server_time",
            "get_exchange_info",
            "get_tick",
            "get_depth",
            "get_kline",
            "get_account",
            "get_balance",
            "make_order",
            "cancel_order",
            "query_order",
            "get_open_orders",
        ]:
            assert ed.get_rest_path(key) is not None, f"Missing rest_path: {key}"


# ══════════════════════════════════════════════════════════════════════════
# 2. Parameter Generation (_get_xxx)
# ══════════════════════════════════════════════════════════════════════════


class TestParameterGeneration:
    """Verify _get_xxx returns correct (path, params, extra_data) tuples."""

    def test_get_server_time_params(self):
        feed = _make_feed()
        path, params, extra = feed._get_server_time()
        assert "GET" in path
        assert extra["request_type"] == "get_server_time"
        assert extra["exchange_name"] == "CRYPTOCOM___SPOT"

    def test_get_exchange_info_params(self):
        feed = _make_feed()
        path, params, extra = feed._get_exchange_info()
        assert "GET" in path
        assert extra["request_type"] == "get_exchange_info"

    def test_get_tick_params(self):
        feed = _make_feed()
        path, params, extra = feed._get_tick("BTC/USDT")
        assert "GET" in path
        assert params["instrument_name"] == "BTC_USDT"
        assert extra["symbol_name"] == "BTC/USDT"
        assert extra["normalize_function"] is not None

    def test_get_depth_params(self):
        feed = _make_feed()
        path, params, extra = feed._get_depth("BTC/USDT", size=10)
        assert "GET" in path
        assert params["instrument_name"] == "BTC_USDT"
        assert params["depth"] == 10
        assert extra["symbol_name"] == "BTC/USDT"

    def test_get_kline_params(self):
        feed = _make_feed()
        path, params, extra = feed._get_kline("BTC/USDT", period="1h", count=24)
        assert "GET" in path
        assert params["instrument_name"] == "BTC_USDT"
        assert params["period"] == "1h"
        assert params["count"] == 24

    def test_get_trade_history_params(self):
        feed = _make_feed()
        path, params, extra = feed._get_trade_history("BTC/USDT", count=50)
        assert "GET" in path
        assert params["instrument_name"] == "BTC_USDT"
        assert params["count"] == 50

    def test_make_order_params(self):
        feed = _make_feed()
        path, params, extra = feed._make_order(
            "BTC/USDT", 0.001, price=50000, order_type="buy-limit"
        )
        assert "POST" in path
        assert params["instrument_name"] == "BTC_USDT"
        assert params["side"] == "BUY"
        assert params["type"] == "LIMIT"
        assert params["quantity"] == "0.001"
        assert params["price"] == "50000"

    def test_cancel_order_params(self):
        feed = _make_feed()
        path, params, extra = feed._cancel_order(symbol="BTC/USDT", order_id="12345")
        assert "POST" in path
        assert params["instrument_name"] == "BTC_USDT"
        assert params["order_id"] == "12345"

    def test_query_order_params(self):
        feed = _make_feed()
        path, params, extra = feed._query_order(symbol="BTC/USDT", order_id="12345")
        assert "POST" in path
        assert params["order_id"] == "12345"

    def test_get_open_orders_params(self):
        feed = _make_feed()
        path, params, extra = feed._get_open_orders(symbol="BTC/USDT")
        assert "POST" in path
        assert params["instrument_name"] == "BTC_USDT"

    def test_get_account_params(self):
        feed = _make_feed()
        path, params, extra = feed._get_account()
        assert "POST" in path
        assert extra["request_type"] == "get_account"

    def test_get_balance_params(self):
        feed = _make_feed()
        path, params, extra = feed._get_balance()
        assert "POST" in path
        assert extra["request_type"] == "get_balance"


# ══════════════════════════════════════════════════════════════════════════
# 3. Normalization Functions
# ══════════════════════════════════════════════════════════════════════════


class TestNormalization:
    """Verify normalize functions return (data_list, status)."""

    def test_server_time_normalize(self):
        extra = {"symbol_name": None, "asset_type": "SPOT", "exchange_name": "CRYPTOCOM___SPOT"}
        data, status = CryptoComRequestData._get_server_time_normalize_function(
            SAMPLE_SERVER_TIME_RESP, extra
        )
        assert status is True
        assert len(data) == 1
        assert data[0]["server_time"] == 1640995200000

    def test_exchange_info_normalize(self):
        extra = {"symbol_name": None, "asset_type": "SPOT", "exchange_name": "CRYPTOCOM___SPOT"}
        data, status = CryptoComRequestData._get_exchange_info_normalize_function(
            SAMPLE_EXCHANGE_INFO_RESP, extra
        )
        assert status is True
        assert len(data) == 1
        assert len(data[0]["symbols"]) == 1

    @pytest.mark.ticker
    def test_tick_normalize(self):
        extra = {
            "symbol_name": "BTC/USDT",
            "asset_type": "SPOT",
            "exchange_name": "CRYPTOCOM___SPOT",
        }
        data, status = CryptoComRequestData._get_tick_normalize_function(SAMPLE_TICKER_RESP, extra)
        assert status is True
        assert len(data) == 1
        assert isinstance(data[0], CryptoComTicker)

    @pytest.mark.orderbook
    def test_depth_normalize(self):
        extra = {
            "symbol_name": "BTC/USDT",
            "asset_type": "SPOT",
            "exchange_name": "CRYPTOCOM___SPOT",
        }
        data, status = CryptoComRequestData._get_depth_normalize_function(
            SAMPLE_ORDERBOOK_RESP, extra
        )
        assert status is True
        assert len(data) == 1
        assert isinstance(data[0], CryptoComOrderBook)

    @pytest.mark.kline
    def test_kline_normalize(self):
        extra = {
            "symbol_name": "BTC/USDT",
            "asset_type": "SPOT",
            "exchange_name": "CRYPTOCOM___SPOT",
        }
        data, status = CryptoComRequestData._get_kline_normalize_function(SAMPLE_KLINE_RESP, extra)
        assert status is True
        assert len(data) == 2
        assert data[0]["open"] == 50000.0

    def test_trade_history_normalize(self):
        extra = {
            "symbol_name": "BTC/USDT",
            "asset_type": "SPOT",
            "exchange_name": "CRYPTOCOM___SPOT",
        }
        data, status = CryptoComRequestData._get_trade_history_normalize_function(
            SAMPLE_TRADES_RESP, extra
        )
        assert status is True
        assert len(data) == 2

    def test_make_order_normalize(self):
        extra = {
            "symbol_name": "BTC/USDT",
            "asset_type": "SPOT",
            "exchange_name": "CRYPTOCOM___SPOT",
        }
        data, status = CryptoComRequestData._make_order_normalize_function(
            SAMPLE_MAKE_ORDER_RESP, extra
        )
        assert status is True
        assert len(data) == 1
        assert isinstance(data[0], CryptoComOrder)

    def test_cancel_order_normalize(self):
        extra = {
            "symbol_name": "BTC/USDT",
            "asset_type": "SPOT",
            "exchange_name": "CRYPTOCOM___SPOT",
        }
        data, status = CryptoComRequestData._cancel_order_normalize_function(
            SAMPLE_CANCEL_ORDER_RESP, extra
        )
        assert status is True
        assert data[0]["success"] is True

    def test_query_order_normalize(self):
        extra = {
            "symbol_name": "BTC/USDT",
            "asset_type": "SPOT",
            "exchange_name": "CRYPTOCOM___SPOT",
        }
        data, status = CryptoComRequestData._query_order_normalize_function(
            SAMPLE_QUERY_ORDER_RESP, extra
        )
        assert status is True
        assert len(data) == 1

    def test_open_orders_normalize(self):
        extra = {
            "symbol_name": "BTC/USDT",
            "asset_type": "SPOT",
            "exchange_name": "CRYPTOCOM___SPOT",
        }
        data, status = CryptoComRequestData._get_open_orders_normalize_function(
            SAMPLE_OPEN_ORDERS_RESP, extra
        )
        assert status is True
        assert len(data) == 1

    def test_account_normalize(self):
        extra = {"symbol_name": None, "asset_type": "SPOT", "exchange_name": "CRYPTOCOM___SPOT"}
        data, status = CryptoComRequestData._get_account_normalize_function(
            SAMPLE_ACCOUNT_RESP, extra
        )
        assert status is True
        assert len(data) == 2

    def test_balance_normalize(self):
        extra = {"symbol_name": None, "asset_type": "SPOT", "exchange_name": "CRYPTOCOM___SPOT"}
        data, status = CryptoComRequestData._get_balance_normalize_function(
            SAMPLE_BALANCE_RESP, extra
        )
        assert status is True
        assert len(data) == 2

    def test_normalize_with_none_input(self):
        extra = {
            "symbol_name": "BTC/USDT",
            "asset_type": "SPOT",
            "exchange_name": "CRYPTOCOM___SPOT",
        }
        data, status = CryptoComRequestData._get_tick_normalize_function(None, extra)
        assert status is False
        assert data == []


# ══════════════════════════════════════════════════════════════════════════
# 4. Synchronous Calls (mocked HTTP)
# ══════════════════════════════════════════════════════════════════════════


class TestSyncCalls:
    """Test synchronous API calls with mocked HTTP."""

    def test_get_server_time(self):
        feed = _make_feed()
        with patch.object(feed, "http_request", return_value=SAMPLE_SERVER_TIME_RESP):
            result = feed.get_server_time()
        assert isinstance(result, RequestData)
        assert result.input_data == SAMPLE_SERVER_TIME_RESP

    def test_get_exchange_info(self):
        feed = _make_feed()
        with patch.object(feed, "http_request", return_value=SAMPLE_EXCHANGE_INFO_RESP):
            result = feed.get_exchange_info()
        assert isinstance(result, RequestData)
        assert result.input_data == SAMPLE_EXCHANGE_INFO_RESP

    def test_get_tick(self):
        feed = _make_feed()
        with patch.object(feed, "http_request", return_value=SAMPLE_TICKER_RESP):
            result = feed.get_tick("BTC/USDT")
        assert isinstance(result, RequestData)
        assert result.input_data == SAMPLE_TICKER_RESP

    def test_get_ticker_alias(self):
        feed = _make_feed()
        with patch.object(feed, "http_request", return_value=SAMPLE_TICKER_RESP):
            result = feed.get_ticker("BTC/USDT")
        assert isinstance(result, RequestData)

    def test_get_depth(self):
        feed = _make_feed()
        with patch.object(feed, "http_request", return_value=SAMPLE_ORDERBOOK_RESP):
            result = feed.get_depth("BTC/USDT", count=10)
        assert isinstance(result, RequestData)
        assert result.input_data == SAMPLE_ORDERBOOK_RESP

    def test_get_kline(self):
        feed = _make_feed()
        with patch.object(feed, "http_request", return_value=SAMPLE_KLINE_RESP):
            result = feed.get_kline("BTC/USDT", "1h", count=24)
        assert isinstance(result, RequestData)
        assert result.input_data == SAMPLE_KLINE_RESP

    def test_get_trade_history(self):
        feed = _make_feed()
        with patch.object(feed, "http_request", return_value=SAMPLE_TRADES_RESP):
            result = feed.get_trade_history("BTC/USDT")
        assert isinstance(result, RequestData)

    @pytest.mark.integration
    def test_make_order(self):
        feed = _make_feed()
        with patch.object(feed, "http_request", return_value=SAMPLE_MAKE_ORDER_RESP):
            result = feed.make_order("BTC/USDT", 0.001, price=50000, order_type="buy-limit")
        assert isinstance(result, RequestData)

    @pytest.mark.integration
    def test_cancel_order(self):
        feed = _make_feed()
        with patch.object(feed, "http_request", return_value=SAMPLE_CANCEL_ORDER_RESP):
            result = feed.cancel_order(symbol="BTC/USDT", order_id="12345678")
        assert isinstance(result, RequestData)

    @pytest.mark.integration
    def test_query_order(self):
        feed = _make_feed()
        with patch.object(feed, "http_request", return_value=SAMPLE_QUERY_ORDER_RESP):
            result = feed.query_order(symbol="BTC/USDT", order_id="12345678")
        assert isinstance(result, RequestData)

    @pytest.mark.integration
    def test_get_open_orders(self):
        feed = _make_feed()
        with patch.object(feed, "http_request", return_value=SAMPLE_OPEN_ORDERS_RESP):
            result = feed.get_open_orders(symbol="BTC/USDT")
        assert isinstance(result, RequestData)

    @pytest.mark.integration
    def test_get_account(self):
        feed = _make_feed()
        with patch.object(feed, "http_request", return_value=SAMPLE_ACCOUNT_RESP):
            result = feed.get_account()
        assert isinstance(result, RequestData)

    @pytest.mark.integration
    def test_get_balance(self):
        feed = _make_feed()
        with patch.object(feed, "http_request", return_value=SAMPLE_BALANCE_RESP):
            result = feed.get_balance()
        assert isinstance(result, RequestData)


# ══════════════════════════════════════════════════════════════════════════
# 5. Container Tests
# ══════════════════════════════════════════════════════════════════════════


class TestContainers:
    """Test data containers init_data returns self and parses correctly."""

    @pytest.mark.ticker
    def test_ticker_container(self):
        ticker = CryptoComTicker.from_api_response(
            {
                "a": "50000.0",
                "b": "49900.0",
                "k": "50100.0",
                "h": "51000.0",
                "l": "48000.0",
                "v": "1234.56",
                "vv": "60000000",
                "t": 1640995200000,
            },
            "BTC_USDT",
        )
        result = ticker.init_data()
        assert result is ticker
        assert ticker.last_price == 50000.0
        assert ticker.bid_price == 49900.0
        assert ticker.ask_price == 50100.0
        assert ticker.high_24h == 51000.0
        assert ticker.low_24h == 48000.0
        assert ticker.volume_24h == 1234.56

    @pytest.mark.orderbook
    def test_orderbook_container(self):
        ob = CryptoComOrderBook.from_api_response(
            {
                "data": [{"bids": [["49990", "1.5", "1"]], "asks": [["50010", "1.0", "1"]]}],
                "t": 1640995200000,
            },
            "BTC_USDT",
        )
        result = ob.init_data()
        assert result is ob
        assert len(ob.bids) == 1
        assert len(ob.asks) == 1
        assert ob.bids[0][0] == 49990.0
        assert ob.asks[0][0] == 50010.0

    def test_order_container(self):
        order = CryptoComOrder.from_api_response(
            {
                "instrument_name": "BTC_USDT",
                "order_id": "123456",
                "client_oid": "c_123",
                "side": "BUY",
                "type": "LIMIT",
                "quantity": "0.001",
                "price": "50000",
                "status": "ACTIVE",
                "filled_quantity": "0.0005",
                "remaining_quantity": "0.0005",
            },
            "BTC_USDT",
        )
        result = order.init_data()
        assert result is order
        assert order.order_id == "123456"
        assert order.client_oid == "c_123"
        assert order.side == "BUY"
        assert order.quantity == 0.001
        assert order.price == 50000.0
        assert order.filled_quantity == 0.0005

    def test_order_to_dict(self):
        order = CryptoComOrder.from_api_response(
            {
                "instrument_name": "BTC_USDT",
                "order_id": "123456",
                "side": "SELL",
                "type": "MARKET",
                "quantity": "0.01",
                "price": "0",
                "status": "FILLED",
                "filled_quantity": "0.01",
                "remaining_quantity": "0",
            },
            "BTC_USDT",
        )
        d = order.to_dict()
        assert d["order_id"] == "123456"
        assert d["side"] == "SELL"
        assert d["status"] == "FILLED"


# ══════════════════════════════════════════════════════════════════════════
# 6. Registry
# ══════════════════════════════════════════════════════════════════════════


class TestRegistry:
    """Test Crypto.com registry."""

    def test_feed_registered(self):
        assert "CRYPTOCOM___SPOT" in ExchangeRegistry._feed_classes
        assert ExchangeRegistry._feed_classes["CRYPTOCOM___SPOT"] is CryptoComRequestDataSpot

    def test_exchange_data_registered(self):
        assert "CRYPTOCOM___SPOT" in ExchangeRegistry._exchange_data_classes
        assert (
            ExchangeRegistry._exchange_data_classes["CRYPTOCOM___SPOT"] is CryptoComExchangeDataSpot
        )

    def test_balance_handler_registered(self):
        assert "CRYPTOCOM___SPOT" in ExchangeRegistry._balance_handlers

    def test_create_feed(self):
        dq = queue.Queue()
        feed = ExchangeRegistry.create_feed("CRYPTOCOM___SPOT", dq, public_key="k", private_key="s")
        assert isinstance(feed, CryptoComRequestDataSpot)

    def test_create_exchange_data(self):
        ed = ExchangeRegistry.create_exchange_data("CRYPTOCOM___SPOT")
        assert isinstance(ed, CryptoComExchangeDataSpot)


# ══════════════════════════════════════════════════════════════════════════
# 7. Method Existence & Capabilities
# ══════════════════════════════════════════════════════════════════════════


class TestMethodExistence:
    """Verify all expected public / async methods exist."""

    def test_capabilities(self):
        caps = CryptoComRequestData._capabilities()
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

    @pytest.mark.parametrize(
        "method_name",
        [
            "get_server_time",
            "get_exchange_info",
            "get_tick",
            "get_ticker",
            "get_depth",
            "get_kline",
            "get_trade_history",
            "make_order",
            "cancel_order",
            "query_order",
            "get_open_orders",
            "get_account",
            "get_balance",
        ],
    )
    def test_sync_methods_exist(self, method_name):
        feed = _make_feed()
        assert hasattr(feed, method_name), f"Missing method: {method_name}"
        assert callable(getattr(feed, method_name))

    @pytest.mark.parametrize(
        "method_name",
        [
            "async_get_server_time",
            "async_get_exchange_info",
            "async_get_tick",
            "async_get_depth",
            "async_get_kline",
            "async_get_trade_history",
            "async_make_order",
            "async_cancel_order",
            "async_query_order",
            "async_get_open_orders",
            "async_get_account",
            "async_get_balance",
        ],
    )
    def test_async_methods_exist(self, method_name):
        feed = _make_feed()
        assert hasattr(feed, method_name), f"Missing method: {method_name}"
        assert callable(getattr(feed, method_name))

    @pytest.mark.parametrize(
        "method_name",
        [
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
        ],
    )
    def test_internal_methods_exist(self, method_name):
        feed = _make_feed()
        assert hasattr(feed, method_name), f"Missing method: {method_name}"


# ══════════════════════════════════════════════════════════════════════════
# 8. Signature
# ══════════════════════════════════════════════════════════════════════════


class TestSignature:
    """Test HMAC-SHA256 signature generation."""

    def test_build_param_string(self):
        params = {"instrument_name": "BTC_USDT", "side": "BUY", "type": "LIMIT"}
        result = CryptoComRequestData._build_param_string(params)
        assert "instrument_name" in result
        assert "side" in result

    def test_build_param_string_empty(self):
        assert CryptoComRequestData._build_param_string({}) == ""
        assert CryptoComRequestData._build_param_string(None) == ""

    def test_sign_returns_hex_string(self):
        feed = _make_feed()
        sig = feed.sign("private/create-order", {"instrument_name": "BTC_USDT"}, 123, 456)
        assert isinstance(sig, str)
        assert len(sig) == 64  # SHA256 hex digest


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
