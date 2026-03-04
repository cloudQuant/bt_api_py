"""
Test Bitfinex exchange integration.

Run tests:
    pytest tests/feeds/test_bitfinex.py -v

Run with coverage:
    pytest tests/feeds/test_bitfinex.py --cov=bt_api_py.feeds.live_bitfinex --cov-report=term-missing
"""

import json
import queue
from unittest.mock import MagicMock, patch

import pytest

from bt_api_py.containers.exchanges.bitfinex_exchange_data import (
    BitfinexExchangeData,
    BitfinexExchangeDataSpot,
)
from bt_api_py.containers.tickers.bitfinex_ticker import (
    BitfinexRequestTickerData,
)
from bt_api_py.containers.orderbooks.bitfinex_orderbook import (
    BitfinexRequestOrderBookData,
)
from bt_api_py.containers.bars.bitfinex_bar import (
    BitfinexRequestBarData,
)
from bt_api_py.containers.orders.bitfinex_order import (
    BitfinexRequestOrderData,
)
from bt_api_py.containers.balances.bitfinex_balance import (
    BitfinexSpotRequestBalanceData,
)
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.registry import ExchangeRegistry
from bt_api_py.feeds.live_bitfinex import BitfinexRequestDataSpot

# Import registration to auto-register Bitfinex
import bt_api_py.exchange_registers.register_bitfinex  # noqa: F401


# ── Sample API responses ─────────────────────────────────────────────────

# Bitfinex ticker: [BID, BID_SIZE, ASK, ASK_SIZE, DAILY_CHANGE, DAILY_CHANGE_PERC, LAST_PRICE, VOLUME, HIGH, LOW]
SAMPLE_TICKER_RESP = [
    49950.0, 1.5, 50050.0, 2.3, 100.5, 0.002, 50000.0, 1234.56, 51000.0, 49000.0
]

# Bitfinex orderbook: list of [PRICE, COUNT, AMOUNT]
SAMPLE_ORDERBOOK_RESP = [
    [49900.0, 3, 0.5],
    [49800.0, 2, 1.0],
    [50100.0, 1, -0.3],
    [50200.0, 4, -0.8],
]

# Bitfinex kline: list of [MTS, OPEN, CLOSE, HIGH, LOW, VOLUME]
SAMPLE_KLINE_RESP = [
    [1640995200000, 50000.0, 50200.0, 50500.0, 49500.0, 1000.0],
    [1640998800000, 50200.0, 50400.0, 50600.0, 50100.0, 800.0],
]

# Bitfinex trade history: list of [ID, MTS, AMOUNT, PRICE]
SAMPLE_TRADES_RESP = [
    [111, 1640995200000, 0.5, 50000.0],
    [112, 1640995201000, -0.3, 50010.0],
]

# Bitfinex order submit response
SAMPLE_MAKE_ORDER_RESP = [
    1234567890, None, 111, "tBTCUSD", 1640995200000, 1640995200000,
    0.001, 0.001, "EXCHANGE LIMIT", None, 0, "ACTIVE",
    50000.0, 0.0, None, None, None, None, 0, 0, None, None, None, 0, 0, None, None, None,
    "API>BFX", None, None, None,
]

# Bitfinex cancel order response
SAMPLE_CANCEL_ORDER_RESP = [
    1234567890, None, 111, "tBTCUSD", 1640995200000, 1640995200000,
    0.0, 0.001, "EXCHANGE LIMIT", None, 0, "CANCELED",
    50000.0, 0.0, None, None,
]

# Bitfinex open orders response
SAMPLE_OPEN_ORDERS_RESP = [
    SAMPLE_MAKE_ORDER_RESP,
]

# Bitfinex account (wallets) response: list of [WALLET_TYPE, CURRENCY, BALANCE, UNSETTLED_INTEREST, BALANCE_AVAILABLE]
SAMPLE_ACCOUNT_RESP = [
    ["exchange", "BTC", 0.5, 0.0, 0.5],
    ["exchange", "USD", 10000.0, 0.0, 9500.0],
]

# Bitfinex server time response
SAMPLE_SERVER_TIME_RESP = [1640995200000]

# Bitfinex exchange info (symbols list)
SAMPLE_EXCHANGE_INFO_RESP = [
    ["BTCUSD", "ETHUSD", "LTCUSD"],
]

# Bitfinex query order response (single order as list)
SAMPLE_QUERY_ORDER_RESP = SAMPLE_MAKE_ORDER_RESP


# ── Helper ───────────────────────────────────────────────────────────────

MOCK_PATH = "bt_api_py.feeds.http_client.HttpClient.request"


def _make_spot_feed():
    data_queue = queue.Queue()
    return BitfinexRequestDataSpot(
        data_queue,
        api_key="test_api_key",
        api_secret="test_secret",
        exchange_name="BITFINEX___SPOT",
    )


def _setup_mock(mock_req, resp_json, status_code=200):
    """Configure a mock for HttpClient.request that returns resp_json dict."""
    mock_req.return_value = resp_json


# ══════════════════════════════════════════════════════════════════════════
# 1. ExchangeData tests
# ══════════════════════════════════════════════════════════════════════════

class TestBitfinexExchangeData:

    def test_spot_creation(self):
        ed = BitfinexExchangeDataSpot()
        assert ed.exchange_name == "BITFINEX___SPOT"
        assert ed.asset_type == "spot"
        assert ed.rest_url != ""
        assert len(ed.rest_paths) > 0

    def test_get_symbol(self):
        ed = BitfinexExchangeDataSpot()
        assert ed.get_symbol("BTC-USD") == "tBTCUSD"
        assert ed.get_symbol("ETH-USD") == "tETHUSD"

    def test_get_symbol_from_mappings(self):
        ed = BitfinexExchangeDataSpot()
        assert ed.get_symbol("BTC-USD") == "tBTCUSD"
        assert ed.get_symbol("ETH-USD") == "tETHUSD"
        assert ed.get_symbol("XRP-USD") == "tXRPUSD"

    def test_get_reverse_symbol(self):
        ed = BitfinexExchangeDataSpot()
        assert ed.get_reverse_symbol("tBTCUSD") == "BTC-USD"
        assert ed.get_reverse_symbol("tETHUSD") == "ETH-USD"

    def test_get_period(self):
        ed = BitfinexExchangeDataSpot()
        assert ed.get_period("1m") == "1m"
        assert ed.get_period("1h") == "1h"
        assert ed.get_period("1d") == "1D"

    def test_kline_periods(self):
        ed = BitfinexExchangeDataSpot()
        assert "1m" in ed.kline_periods
        assert "1h" in ed.kline_periods
        assert "1d" in ed.kline_periods

    def test_legal_currencies(self):
        ed = BitfinexExchangeDataSpot()
        assert "USD" in ed.legal_currency
        assert "USDT" in ed.legal_currency
        assert "BTC" in ed.legal_currency

    def test_rest_paths_present(self):
        ed = BitfinexExchangeDataSpot()
        for key in ("get_tick", "get_depth", "get_kline", "get_exchange_info",
                     "make_order", "cancel_order", "get_order", "get_open_orders",
                     "get_account", "get_balance", "get_server_time"):
            path = ed.get_rest_path(key)
            assert path, f"rest_path '{key}' should not be empty"


# ══════════════════════════════════════════════════════════════════════════
# 2. Layer-1 parameter generation
# ══════════════════════════════════════════════════════════════════════════

class TestBitfinexParamGeneration:

    def test_get_ticker_params(self):
        feed = _make_spot_feed()
        path, params, extra = feed._get_ticker("BTC-USD")
        assert "tBTCUSD" in path
        assert extra["symbol_name"] == "BTC-USD"
        assert extra["normalize_function"] is not None

    def test_get_order_book_params(self):
        feed = _make_spot_feed()
        path, params, extra = feed._get_order_book("BTC-USD", precision="P0", length="25")
        assert "tBTCUSD" in path
        assert extra["symbol_name"] == "BTC-USD"
        assert extra["normalize_function"] is not None

    def test_get_klines_params(self):
        feed = _make_spot_feed()
        path, params, extra = feed._get_klines("BTC-USD", period="1m", limit=100)
        assert "tBTCUSD" in path
        assert extra["symbol_name"] == "BTC-USD"

    def test_make_order_params(self):
        feed = _make_spot_feed()
        path, params, extra = feed._make_order("BTC-USD", "0.001", "50000", "buy-limit")
        assert params["symbol"] == "tBTCUSD"
        assert params["amount"] == "0.001"
        assert params["price"] == "50000"
        assert params["type"] == "LIMIT"

    def test_make_order_market(self):
        feed = _make_spot_feed()
        path, params, extra = feed._make_order("BTC-USD", "0.001", order_type="buy-market")
        assert params["type"] == "MARKET"
        assert "price" not in params

    def test_cancel_order_params(self):
        feed = _make_spot_feed()
        path, params, extra = feed._cancel_order(symbol="BTC-USD", order_id=12345)
        assert params["id"] == 12345
        assert extra["normalize_function"] is not None

    def test_get_order_params(self):
        feed = _make_spot_feed()
        path, params, extra = feed._get_order(symbol="BTC-USD", order_id=12345)
        assert extra["order_id"] == 12345
        assert extra["normalize_function"] is not None

    def test_get_open_orders_params(self):
        feed = _make_spot_feed()
        path, params, extra = feed._get_open_orders()
        assert extra["normalize_function"] is not None

    def test_get_account_params(self):
        feed = _make_spot_feed()
        path, params, extra = feed._get_account()
        assert extra["normalize_function"] is not None

    def test_get_server_time_params(self):
        feed = _make_spot_feed()
        path, params, extra = feed._get_server_time()
        assert extra["normalize_function"] is not None

    def test_get_exchange_info_params(self):
        feed = _make_spot_feed()
        path, params, extra = feed._get_exchange_info()
        assert extra["normalize_function"] is not None


# ══════════════════════════════════════════════════════════════════════════
# 3. Normalize functions
# ══════════════════════════════════════════════════════════════════════════

class TestBitfinexNormalize:

    def test_ticker_normalize(self):
        extra = {"symbol_name": "BTC-USD", "asset_type": "SPOT", "exchange_name": "BITFINEX___SPOT"}
        tickers, ok = BitfinexRequestDataSpot._get_ticker_normalize_function(
            SAMPLE_TICKER_RESP, extra)
        assert ok is True
        assert len(tickers) == 1
        assert isinstance(tickers[0], BitfinexRequestTickerData)

    def test_ticker_normalize_none(self):
        extra = {"symbol_name": "BTC-USD", "asset_type": "SPOT"}
        tickers, ok = BitfinexRequestDataSpot._get_ticker_normalize_function(None, extra)
        assert ok is False
        assert tickers == []

    def test_orderbook_normalize(self):
        extra = {"symbol_name": "BTC-USD", "asset_type": "SPOT"}
        books, ok = BitfinexRequestDataSpot._get_order_book_normalize_function(
            SAMPLE_ORDERBOOK_RESP, extra)
        assert ok is True
        assert len(books) == 1
        assert isinstance(books[0], BitfinexRequestOrderBookData)

    def test_orderbook_normalize_none(self):
        extra = {"symbol_name": "BTC-USD", "asset_type": "SPOT"}
        books, ok = BitfinexRequestDataSpot._get_order_book_normalize_function(None, extra)
        assert ok is False
        assert books == []

    def test_kline_normalize(self):
        extra = {"symbol_name": "BTC-USD", "asset_type": "SPOT"}
        bars, ok = BitfinexRequestDataSpot._get_klines_normalize_function(
            SAMPLE_KLINE_RESP, extra)
        assert ok is True
        assert len(bars) == 1
        assert isinstance(bars[0], BitfinexRequestBarData)

    def test_kline_normalize_none(self):
        extra = {"symbol_name": "BTC-USD", "asset_type": "SPOT"}
        bars, ok = BitfinexRequestDataSpot._get_klines_normalize_function(None, extra)
        assert ok is False
        assert bars == []

    def test_make_order_normalize(self):
        extra = {"symbol_name": "BTC-USD", "asset_type": "SPOT", "exchange_name": "BITFINEX___SPOT"}
        orders, ok = BitfinexRequestDataSpot._make_order_normalize_function(
            SAMPLE_MAKE_ORDER_RESP, extra)
        assert ok is True
        assert len(orders) > 0

    def test_make_order_normalize_none(self):
        extra = {"symbol_name": "BTC-USD", "asset_type": "SPOT"}
        orders, ok = BitfinexRequestDataSpot._make_order_normalize_function(None, extra)
        assert ok is False
        assert orders == []

    def test_cancel_order_normalize(self):
        extra = {"order_id": 12345, "asset_type": "SPOT"}
        orders, ok = BitfinexRequestDataSpot._cancel_order_normalize_function(
            SAMPLE_CANCEL_ORDER_RESP, extra)
        assert ok is True
        assert len(orders) == 1

    def test_open_orders_normalize(self):
        extra = {"symbol_name": "BTC-USD", "asset_type": "SPOT"}
        orders, ok = BitfinexRequestDataSpot._get_open_orders_normalize_function(
            SAMPLE_OPEN_ORDERS_RESP, extra)
        assert ok is True
        assert len(orders) == 1

    def test_account_normalize(self):
        extra = {"asset_type": "SPOT"}
        balances, ok = BitfinexRequestDataSpot._get_account_normalize_function(
            SAMPLE_ACCOUNT_RESP, extra)
        assert ok is True
        assert len(balances) == 1
        assert isinstance(balances[0], BitfinexSpotRequestBalanceData)

    def test_server_time_normalize(self):
        extra = {"asset_type": "SPOT"}
        data, ok = BitfinexRequestDataSpot._get_server_time_normalize_function(
            SAMPLE_SERVER_TIME_RESP, extra)
        assert ok is True
        assert data[0]["server_time"] == 1640995200000

    def test_exchange_info_normalize(self):
        extra = {"symbol_name": None, "asset_type": "SPOT"}
        data, ok = BitfinexRequestDataSpot._get_exchange_info_normalize_function(
            SAMPLE_EXCHANGE_INFO_RESP, extra)
        assert ok is True

    def test_trade_history_normalize(self):
        extra = {"symbol_name": "BTC-USD", "asset_type": "SPOT"}
        trades, ok = BitfinexRequestDataSpot._get_trade_history_normalize_function(
            SAMPLE_TRADES_RESP, extra)
        assert ok is True
        assert len(trades) == 1


# ══════════════════════════════════════════════════════════════════════════
# 4. Synchronous API calls (mocked HTTP)
# ══════════════════════════════════════════════════════════════════════════

class TestBitfinexSyncCalls:

    @patch(MOCK_PATH)
    def test_get_ticker(self, mock_req):
        _setup_mock(mock_req, SAMPLE_TICKER_RESP)
        feed = _make_spot_feed()
        result = feed.get_ticker("BTC-USD")
        assert isinstance(result, RequestData)

    @patch(MOCK_PATH)
    def test_get_tick_alias(self, mock_req):
        _setup_mock(mock_req, SAMPLE_TICKER_RESP)
        feed = _make_spot_feed()
        result = feed.get_tick("BTC-USD")
        assert isinstance(result, RequestData)

    @patch(MOCK_PATH)
    def test_get_depth(self, mock_req):
        _setup_mock(mock_req, SAMPLE_ORDERBOOK_RESP)
        feed = _make_spot_feed()
        result = feed.get_depth("BTC-USD", count=25)
        assert isinstance(result, RequestData)

    @patch(MOCK_PATH)
    def test_get_kline(self, mock_req):
        _setup_mock(mock_req, SAMPLE_KLINE_RESP)
        feed = _make_spot_feed()
        result = feed.get_kline("BTC-USD", period="1m", count=20)
        assert isinstance(result, RequestData)

    @patch(MOCK_PATH)
    def test_get_server_time(self, mock_req):
        _setup_mock(mock_req, SAMPLE_SERVER_TIME_RESP)
        feed = _make_spot_feed()
        result = feed.get_server_time()
        assert isinstance(result, RequestData)

    @patch(MOCK_PATH)
    def test_get_exchange_info(self, mock_req):
        _setup_mock(mock_req, SAMPLE_EXCHANGE_INFO_RESP)
        feed = _make_spot_feed()
        result = feed.get_exchange_info()
        assert isinstance(result, RequestData)

    @patch(MOCK_PATH)
    def test_get_trade_history(self, mock_req):
        _setup_mock(mock_req, SAMPLE_TRADES_RESP)
        feed = _make_spot_feed()
        result = feed.get_trade_history("BTC-USD", limit=10)
        assert isinstance(result, RequestData)

    @patch(MOCK_PATH)
    def test_make_order(self, mock_req):
        _setup_mock(mock_req, SAMPLE_MAKE_ORDER_RESP)
        feed = _make_spot_feed()
        result = feed.make_order(symbol="BTC-USD", vol="0.001", price="50000",
                                 order_type="buy-limit")
        assert isinstance(result, RequestData)

    @patch(MOCK_PATH)
    def test_cancel_order(self, mock_req):
        _setup_mock(mock_req, SAMPLE_CANCEL_ORDER_RESP)
        feed = _make_spot_feed()
        result = feed.cancel_order(symbol="BTC-USD", order_id=12345)
        assert isinstance(result, RequestData)

    @patch(MOCK_PATH)
    def test_query_order(self, mock_req):
        _setup_mock(mock_req, SAMPLE_QUERY_ORDER_RESP)
        feed = _make_spot_feed()
        result = feed.query_order(symbol="BTC-USD", order_id=12345)
        assert isinstance(result, RequestData)

    @patch(MOCK_PATH)
    def test_get_open_orders(self, mock_req):
        _setup_mock(mock_req, SAMPLE_OPEN_ORDERS_RESP)
        feed = _make_spot_feed()
        result = feed.get_open_orders()
        assert isinstance(result, RequestData)

    @patch(MOCK_PATH)
    def test_get_account(self, mock_req):
        _setup_mock(mock_req, SAMPLE_ACCOUNT_RESP)
        feed = _make_spot_feed()
        result = feed.get_account()
        assert isinstance(result, RequestData)

    @patch(MOCK_PATH)
    def test_get_balance(self, mock_req):
        _setup_mock(mock_req, SAMPLE_ACCOUNT_RESP)
        feed = _make_spot_feed()
        result = feed.get_balance()
        assert isinstance(result, RequestData)


# ══════════════════════════════════════════════════════════════════════════
# 5. Container tests
# ══════════════════════════════════════════════════════════════════════════

class TestBitfinexContainers:

    def test_ticker_container(self):
        ticker_data = [
            "tBTCUSD", 49950.0, 1.5, 50050.0, 2.3,
            100.5, 0.2, 50000.0, 1234.56, 51000.0, 49000.0,
        ]
        t = BitfinexRequestTickerData(json.dumps(ticker_data), "BTC-USD", "SPOT", False)
        result = t.init_data()
        assert result is t
        assert t.get_exchange_name() == "BITFINEX"
        assert t.get_symbol_name() == "BTC-USD"
        assert t.get_last_price() == 50000.0
        assert t.get_bid_price() == 49950.0
        assert t.get_ask_price() == 50050.0
        assert t.get_high() == 51000.0
        assert t.get_low() == 49000.0

    def test_orderbook_container(self):
        orderbook_data = {
            "bids": [[49900.0, 0.5], [49800.0, 1.0]],
            "asks": [[50100.0, 0.3], [50200.0, 0.8]],
        }
        ob = BitfinexRequestOrderBookData(json.dumps(orderbook_data), "BTC-USD", "SPOT")
        result = ob.init_data()
        assert result is ob
        assert ob.symbol_name == "BTC-USD"

    def test_order_container(self):
        order_data = {
            "id": 123456789, "gid": None, "cid": 123456, "symbol": "tBTCUSD",
            "mts_create": 1640995200000, "mts_update": 1640995200000,
            "amount": 0.001, "amount_orig": 0.001, "type": "EXCHANGE LIMIT",
            "type_prev": None, "flags": 0, "status": "ACTIVE",
            "price": 50000.0, "price_avg": 50000.0,
            "price_trailing": None, "price_aux_limit": None,
            "notify": 0, "hidden": 0, "placed_id": None, "routing": None, "meta": None,
        }
        o = BitfinexRequestOrderData(json.dumps(order_data), "BTC-USD", "SPOT")
        result = o.init_data()
        assert result is o
        assert o.symbol_name == "BTC-USD"
        assert o.exchange_name == "BITFINEX"

    def test_balance_container(self):
        balance_data = ["BTC", 0.5, 0.1, None]
        b = BitfinexSpotRequestBalanceData(json.dumps(balance_data), "SPOT", has_been_json_encoded=False)
        result = b.init_data()
        assert result is b
        assert b.get_exchange_name() == "BITFINEX"
        assert b.get_asset_type() == "SPOT"


# ══════════════════════════════════════════════════════════════════════════
# 6. Registry tests
# ══════════════════════════════════════════════════════════════════════════

class TestBitfinexRegistry:

    def test_bitfinex_registered(self):
        assert "BITFINEX___SPOT" in ExchangeRegistry._feed_classes
        assert "BITFINEX___SPOT" in ExchangeRegistry._exchange_data_classes
        assert ExchangeRegistry._exchange_data_classes["BITFINEX___SPOT"] == BitfinexExchangeDataSpot

    def test_bitfinex_balance_handler(self):
        assert "BITFINEX___SPOT" in ExchangeRegistry._balance_handlers
        assert ExchangeRegistry._balance_handlers["BITFINEX___SPOT"] is not None

    def test_bitfinex_create_feed(self):
        data_queue = queue.Queue()
        feed = ExchangeRegistry.create_feed(
            "BITFINEX___SPOT",
            data_queue,
            api_key="test",
            api_secret="test",
        )
        assert isinstance(feed, BitfinexRequestDataSpot)

    def test_bitfinex_create_exchange_data(self):
        exchange_data = ExchangeRegistry.create_exchange_data("BITFINEX___SPOT")
        assert isinstance(exchange_data, BitfinexExchangeDataSpot)


# ══════════════════════════════════════════════════════════════════════════
# 7. Method existence checks
# ══════════════════════════════════════════════════════════════════════════

class TestBitfinexMethodExistence:

    def test_has_standard_methods(self):
        feed = _make_spot_feed()
        for method_name in (
            "get_ticker", "get_tick", "get_depth", "get_kline",
            "get_server_time", "get_exchange_info", "get_trade_history",
            "make_order", "cancel_order", "query_order", "get_open_orders",
            "get_account", "get_balance",
            "async_get_ticker", "async_get_tick", "async_get_depth", "async_get_kline",
            "async_make_order", "async_cancel_order", "async_query_order",
            "async_get_open_orders", "async_get_account", "async_get_balance",
        ):
            assert hasattr(feed, method_name), f"Missing method: {method_name}"

    def test_has_internal_methods(self):
        feed = _make_spot_feed()
        for method_name in (
            "_get_ticker", "_get_order_book", "_get_klines", "_get_trade_history",
            "_get_server_time", "_get_exchange_info",
            "_make_order", "_cancel_order", "_get_order", "_get_open_orders",
            "_get_account",
        ):
            assert hasattr(feed, method_name), f"Missing internal method: {method_name}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
