"""
Test MEXC exchange integration — pure unit tests, no network calls.

Run tests:
    pytest tests/feeds/test_mexc.py -v
"""

import queue
from unittest.mock import patch, MagicMock

import pytest

from bt_api_py.containers.exchanges.mexc_exchange_data import MexcExchangeDataSpot
from bt_api_py.containers.orders.mexc_order import MexcRequestOrderData
from bt_api_py.containers.tickers.mexc_ticker import MexcRequestTickerData
from bt_api_py.containers.orderbooks.mexc_orderbook import MexcRequestOrderBookData
from bt_api_py.containers.balances.mexc_balance import MexcRequestBalanceData
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_mexc.spot import MexcRequestDataSpot
from bt_api_py.registry import ExchangeRegistry

import bt_api_py.feeds.register_mexc  # noqa: F401


# ── Sample MEXC API responses ────────────────────────────────────────

SAMPLE_TICKER_RESP = {
    "symbol": "BTCUSDT",
    "priceChange": "1000.00",
    "priceChangePercent": "2.05",
    "prevClosePrice": "49000.00",
    "lastPrice": "50000.00",
    "lastQty": "0.001",
    "bidPrice": "49999.00",
    "bidQty": "1.5",
    "askPrice": "50001.00",
    "askQty": "2.3",
    "openPrice": "49500.00",
    "highPrice": "51000.00",
    "lowPrice": "49000.00",
    "volume": "1234.56",
    "quoteVolume": "61728000",
    "openTime": 1688671955000,
    "closeTime": 1688758355000,
    "count": 10000,
}

SAMPLE_DEPTH_RESP = {
    "lastUpdateId": 123456,
    "bids": [
        ["49999.00", "1.5"],
        ["49998.00", "2.0"],
    ],
    "asks": [
        ["50001.00", "1.3"],
        ["50002.00", "2.5"],
    ],
}

SAMPLE_KLINE_RESP = [
    [1672531200000, "50000", "51000", "49000", "50500",
     "100", 1672534800000, "5050000", 100, "50", "2500000"],
    [1672534800000, "50500", "51500", "50000", "51000",
     "150", 1672538400000, "7650000", 150, "75", "3825000"],
]

SAMPLE_SERVER_TIME_RESP = {
    "serverTime": 1704067200000,
}

SAMPLE_EXCHANGE_INFO_RESP = {
    "timezone": "UTC",
    "serverTime": 1704067200000,
    "symbols": [
        {"symbol": "BTCUSDT", "status": "TRADING", "baseAsset": "BTC", "quoteAsset": "USDT"},
        {"symbol": "ETHUSDT", "status": "TRADING", "baseAsset": "ETH", "quoteAsset": "USDT"},
    ],
}

SAMPLE_MAKE_ORDER_RESP = {
    "symbol": "BTCUSDT",
    "orderId": "123456",
    "clientOrderId": "myOrder001",
    "transactTime": 1688671955000,
    "price": "50000",
    "origQty": "0.001",
    "executedQty": "0.0",
    "cummulativeQuoteQty": "0.0",
    "status": "NEW",
    "timeInForce": "GTC",
    "type": "LIMIT",
    "side": "BUY",
}

SAMPLE_CANCEL_ORDER_RESP = {
    "symbol": "BTCUSDT",
    "orderId": "123456",
    "clientOrderId": "myOrder001",
    "price": "50000",
    "origQty": "0.001",
    "executedQty": "0.0",
    "cummulativeQuoteQty": "0.0",
    "status": "CANCELED",
    "timeInForce": "GTC",
    "type": "LIMIT",
    "side": "BUY",
}

SAMPLE_QUERY_ORDER_RESP = {
    "symbol": "BTCUSDT",
    "orderId": "123456",
    "clientOrderId": "myOrder001",
    "price": "50000",
    "origQty": "0.001",
    "executedQty": "0.001",
    "cummulativeQuoteQty": "50.0",
    "status": "FILLED",
    "timeInForce": "GTC",
    "type": "LIMIT",
    "side": "BUY",
    "time": 1688671955000,
    "updateTime": 1688671960000,
    "isWorking": True,
}

SAMPLE_OPEN_ORDERS_RESP = [
    {"symbol": "BTCUSDT", "orderId": "111", "status": "NEW",
     "side": "BUY", "type": "LIMIT", "origQty": "0.01", "price": "48000"},
    {"symbol": "ETHUSDT", "orderId": "222", "status": "NEW",
     "side": "SELL", "type": "LIMIT", "origQty": "1.0", "price": "3500"},
]

SAMPLE_ACCOUNT_RESP = {
    "makerCommission": 10,
    "takerCommission": 10,
    "buyerCommission": 0,
    "sellerCommission": 0,
    "canTrade": True,
    "canWithdraw": True,
    "canDeposit": True,
    "accountType": "SPOT",
    "balances": [
        {"asset": "BTC", "free": "1.5", "locked": "0.1"},
        {"asset": "USDT", "free": "50000", "locked": "1000"},
        {"asset": "ETH", "free": "0.0", "locked": "0.0"},
    ],
}


# ── Helper ───────────────────────────────────────────────────────────────

MOCK_PATH = "bt_api_py.feeds.feed.requests.request"


def _make_spot_feed():
    data_queue = queue.Queue()
    return MexcRequestDataSpot(
        data_queue,
        public_key="test_key",
        private_key="test_secret",
        exchange_name="MEXC___SPOT",
    )


def _setup_mock(mock_req, resp_json, status_code=200):
    """Configure a mock for requests.request that returns resp_json."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = resp_json
    mock_resp.status_code = status_code
    mock_req.return_value = mock_resp


# ══════════════════════════════════════════════════════════════════════════
# 1. ExchangeData tests
# ══════════════════════════════════════════════════════════════════════════

class TestMexcExchangeData:

    def test_spot_creation(self):
        ed = MexcExchangeDataSpot()
        assert ed.exchange_name == "MEXC___SPOT"
        assert ed.asset_type == "SPOT"

    def test_get_symbol_no_separator(self):
        ed = MexcExchangeDataSpot()
        assert ed.get_symbol("BTCUSDT") == "BTCUSDT"
        assert ed.get_symbol("BTC/USDT") == "BTCUSDT"
        assert ed.get_symbol("BTC-USDT") == "BTCUSDT"

    def test_rest_url(self):
        ed = MexcExchangeDataSpot()
        assert "mexc" in ed.rest_url.lower()

    def test_legal_currency(self):
        ed = MexcExchangeDataSpot()
        assert "USDT" in ed.legal_currency

    def test_rest_paths_present(self):
        ed = MexcExchangeDataSpot()
        for key in ("get_server_time", "get_exchange_info", "get_tick",
                     "get_depth", "get_order_book", "get_kline", "get_klines",
                     "get_24hr_ticker", "get_recent_trades",
                     "make_order", "cancel_order", "query_order",
                     "get_open_orders", "get_all_orders",
                     "get_account", "get_balance"):
            path = ed.get_rest_path(key)
            assert path, f"rest_path '{key}' should not be empty"


# ══════════════════════════════════════════════════════════════════════════
# 2. Layer-1 parameter generation
# ══════════════════════════════════════════════════════════════════════════

class TestMexcParamGeneration:

    def test_get_ticker_params(self):
        feed = _make_spot_feed()
        path, params, extra = feed._get_ticker("BTCUSDT")
        assert "ticker" in path.lower()
        assert params["symbol"] == "BTCUSDT"
        assert extra["request_type"] == "get_24hr_ticker"
        assert extra["normalize_function"] is not None

    def test_get_order_book_params(self):
        feed = _make_spot_feed()
        path, params, extra = feed._get_order_book("BTCUSDT", limit=100)
        assert "depth" in path.lower()
        assert params["symbol"] == "BTCUSDT"
        assert params["limit"] == 100

    def test_get_klines_params(self):
        feed = _make_spot_feed()
        path, params, extra = feed._get_klines("BTCUSDT", interval="1h", limit=50)
        assert "kline" in path.lower()
        assert params["symbol"] == "BTCUSDT"
        assert params["interval"] == "1h"
        assert params["limit"] == 50

    def test_get_server_time_params(self):
        feed = _make_spot_feed()
        path, params, extra = feed._get_server_time()
        assert "time" in path.lower()

    def test_get_exchange_info_params(self):
        feed = _make_spot_feed()
        path, params, extra = feed._get_exchange_info()
        assert "exchangeInfo" in path or "exchange" in path.lower()
        assert extra["normalize_function"] is not None

    def test_make_order_params_limit(self):
        feed = _make_spot_feed()
        path, params, extra = feed._make_order(
            symbol="BTCUSDT", vol="0.001", price="50000", order_type="buy-limit")
        assert "order" in path.lower()
        assert params["side"] == "BUY"
        assert params["type"] == "LIMIT"
        assert params["quantity"] == "0.001"
        assert params["price"] == "50000"
        assert params["timeInForce"] == "GTC"

    def test_make_order_params_market(self):
        feed = _make_spot_feed()
        path, params, extra = feed._make_order(
            symbol="BTCUSDT", vol="0.001", order_type="buy-market")
        assert params["side"] == "BUY"
        assert params["type"] == "MARKET"
        assert "timeInForce" not in params
        assert "price" not in params

    def test_cancel_order_params(self):
        feed = _make_spot_feed()
        path, params, extra = feed._cancel_order(symbol="BTCUSDT", order_id="123456")
        assert "DELETE" in path.upper()
        assert params["symbol"] == "BTCUSDT"
        assert params["orderId"] == "123456"

    def test_get_order_params(self):
        feed = _make_spot_feed()
        path, params, extra = feed._get_order(symbol="BTCUSDT", order_id="123456")
        assert "order" in path.lower()
        assert params["symbol"] == "BTCUSDT"
        assert params["orderId"] == "123456"

    def test_get_open_orders_params(self):
        feed = _make_spot_feed()
        path, params, extra = feed._get_open_orders(symbol="BTCUSDT")
        assert "open" in path.lower()
        assert params["symbol"] == "BTCUSDT"

    def test_get_account_params(self):
        feed = _make_spot_feed()
        path, params, extra = feed._get_account()
        assert "account" in path.lower()
        assert extra["normalize_function"] is not None


# ══════════════════════════════════════════════════════════════════════════
# 3. Normalize functions
# ══════════════════════════════════════════════════════════════════════════

class TestMexcNormalize:

    def test_ticker_normalize(self):
        extra = {"symbol_name": "BTCUSDT", "asset_type": "SPOT"}
        tickers, ok = MexcRequestDataSpot._get_ticker_normalize_function(
            SAMPLE_TICKER_RESP, extra)
        assert ok is True
        assert len(tickers) == 1
        assert isinstance(tickers[0], MexcRequestTickerData)

    def test_ticker_normalize_none(self):
        extra = {"symbol_name": "BTCUSDT", "asset_type": "SPOT"}
        tickers, ok = MexcRequestDataSpot._get_ticker_normalize_function(None, extra)
        assert ok is False
        assert tickers == []

    def test_depth_normalize(self):
        extra = {"symbol_name": "BTCUSDT", "asset_type": "SPOT"}
        books, ok = MexcRequestDataSpot._get_order_book_normalize_function(
            SAMPLE_DEPTH_RESP, extra)
        assert ok is True
        assert len(books) == 1
        b = books[0]
        assert isinstance(b, MexcRequestOrderBookData)
        b.init_data()
        assert b.get_bids()[0][0] == 49999.0
        assert b.get_asks()[0][0] == 50001.0

    def test_depth_normalize_none(self):
        extra = {"symbol_name": "BTCUSDT", "asset_type": "SPOT"}
        books, ok = MexcRequestDataSpot._get_order_book_normalize_function(None, extra)
        assert ok is False
        assert books == []

    def test_kline_normalize(self):
        extra = {"symbol_name": "BTCUSDT", "asset_type": "SPOT", "interval": "1h"}
        bars, ok = MexcRequestDataSpot._get_klines_normalize_function(
            SAMPLE_KLINE_RESP, extra)
        assert ok is True
        assert len(bars) > 0
        klines = bars[0]
        assert isinstance(klines, list)
        assert len(klines) == 2
        assert klines[0]["open"] == 50000.0
        assert klines[0]["close"] == 50500.0

    def test_kline_normalize_none(self):
        extra = {"symbol_name": "BTCUSDT", "asset_type": "SPOT", "interval": "1h"}
        bars, ok = MexcRequestDataSpot._get_klines_normalize_function(None, extra)
        assert ok is False
        assert bars == []

    def test_server_time_normalize(self):
        extra = {"asset_type": "SPOT", "exchange_name": "MEXC___SPOT"}
        data, ok = MexcRequestDataSpot._get_server_time_normalize_function(
            SAMPLE_SERVER_TIME_RESP, extra)
        assert ok is True
        assert data[0]["server_time"] == 1704067200000

    def test_exchange_info_normalize(self):
        extra = {"symbol_name": None, "asset_type": "SPOT", "exchange_name": "MEXC___SPOT"}
        data, ok = MexcRequestDataSpot._get_exchange_info_normalize_function(
            SAMPLE_EXCHANGE_INFO_RESP, extra)
        assert ok is True
        assert len(data) == 1
        assert "exchange_info" in data[0]

    def test_make_order_normalize(self):
        extra = {"symbol_name": "BTCUSDT", "asset_type": "SPOT", "exchange_name": "MEXC___SPOT"}
        orders, ok = MexcRequestDataSpot._make_order_normalize_function(
            SAMPLE_MAKE_ORDER_RESP, extra)
        assert ok is True
        assert len(orders) == 1
        assert isinstance(orders[0], MexcRequestOrderData)

    def test_cancel_order_normalize(self):
        extra = {"symbol_name": "BTCUSDT", "asset_type": "SPOT"}
        orders, ok = MexcRequestDataSpot._cancel_order_normalize_function(
            SAMPLE_CANCEL_ORDER_RESP, extra)
        assert ok is True
        assert len(orders) == 1
        assert isinstance(orders[0], MexcRequestOrderData)

    def test_get_order_normalize(self):
        extra = {"symbol_name": "BTCUSDT", "asset_type": "SPOT"}
        orders, ok = MexcRequestDataSpot._get_order_normalize_function(
            SAMPLE_QUERY_ORDER_RESP, extra)
        assert ok is True
        assert len(orders) == 1
        assert isinstance(orders[0], MexcRequestOrderData)

    def test_open_orders_normalize(self):
        extra = {"symbol_name": "BTCUSDT", "asset_type": "SPOT"}
        orders, ok = MexcRequestDataSpot._get_open_orders_normalize_function(
            SAMPLE_OPEN_ORDERS_RESP, extra)
        assert ok is True
        assert len(orders) > 0

    def test_account_normalize(self):
        extra = {"asset_type": "SPOT", "exchange_name": "MEXC___SPOT"}
        data, ok = MexcRequestDataSpot._get_account_normalize_function(
            SAMPLE_ACCOUNT_RESP, extra)
        assert ok is True
        assert len(data) == 1
        account = data[0]
        assert account["can_trade"] is True
        assert len(account["balances"]) == 2  # ETH has 0 balance, excluded


# ══════════════════════════════════════════════════════════════════════════
# 4. Synchronous API calls (mocked HTTP)
# ══════════════════════════════════════════════════════════════════════════

class TestMexcSyncCalls:

    @patch(MOCK_PATH)
    def test_get_server_time(self, mock_req):
        _setup_mock(mock_req, SAMPLE_SERVER_TIME_RESP)
        feed = _make_spot_feed()
        result = feed.get_server_time()
        assert isinstance(result, RequestData)

    @patch(MOCK_PATH)
    def test_get_tick(self, mock_req):
        _setup_mock(mock_req, SAMPLE_TICKER_RESP)
        feed = _make_spot_feed()
        result = feed.get_tick("BTCUSDT")
        assert isinstance(result, RequestData)

    @patch(MOCK_PATH)
    def test_get_depth(self, mock_req):
        _setup_mock(mock_req, SAMPLE_DEPTH_RESP)
        feed = _make_spot_feed()
        result = feed.get_depth("BTCUSDT", 20)
        assert isinstance(result, RequestData)

    @patch(MOCK_PATH)
    def test_get_kline(self, mock_req):
        _setup_mock(mock_req, SAMPLE_KLINE_RESP)
        feed = _make_spot_feed()
        result = feed.get_kline("BTCUSDT", "1h", count=10)
        assert isinstance(result, RequestData)

    @patch(MOCK_PATH)
    def test_get_exchange_info(self, mock_req):
        _setup_mock(mock_req, SAMPLE_EXCHANGE_INFO_RESP)
        feed = _make_spot_feed()
        result = feed.get_exchange_info()
        assert isinstance(result, RequestData)

    @patch(MOCK_PATH)
    def test_make_order(self, mock_req):
        _setup_mock(mock_req, SAMPLE_MAKE_ORDER_RESP)
        feed = _make_spot_feed()
        result = feed.make_order(symbol="BTCUSDT", vol="0.001",
                                 price="50000", order_type="buy-limit")
        assert isinstance(result, RequestData)

    @patch(MOCK_PATH)
    def test_cancel_order(self, mock_req):
        _setup_mock(mock_req, SAMPLE_CANCEL_ORDER_RESP)
        feed = _make_spot_feed()
        result = feed.cancel_order(symbol="BTCUSDT", order_id="123456")
        assert isinstance(result, RequestData)

    @patch(MOCK_PATH)
    def test_query_order(self, mock_req):
        _setup_mock(mock_req, SAMPLE_QUERY_ORDER_RESP)
        feed = _make_spot_feed()
        result = feed.query_order(symbol="BTCUSDT", order_id="123456")
        assert isinstance(result, RequestData)

    @patch(MOCK_PATH)
    def test_get_open_orders(self, mock_req):
        _setup_mock(mock_req, SAMPLE_OPEN_ORDERS_RESP)
        feed = _make_spot_feed()
        result = feed.get_open_orders(symbol="BTCUSDT")
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

class TestMexcContainers:

    def test_ticker_container(self):
        t = MexcRequestTickerData(SAMPLE_TICKER_RESP, "BTCUSDT", "SPOT")
        t.init_data()
        assert t.get_exchange_name() == "MEXC"
        assert t.get_symbol_name() == "BTCUSDT"
        assert t.get_last_price() == 50000.0
        assert t.get_bid_price() == 49999.0
        assert t.get_ask_price() == 50001.0

    def test_ticker_init_returns_self(self):
        t = MexcRequestTickerData(SAMPLE_TICKER_RESP, "BTCUSDT", "SPOT")
        result = t.init_data()
        assert result is t

    def test_orderbook_container(self):
        ob = MexcRequestOrderBookData(SAMPLE_DEPTH_RESP, "BTCUSDT", "SPOT")
        ob.init_data()
        assert ob.get_exchange_name() == "MEXC"
        assert ob.get_symbol_name() == "BTCUSDT"
        assert len(ob.get_bids()) == 2
        assert len(ob.get_asks()) == 2
        assert ob.get_best_bid() == 49999.0
        assert ob.get_best_ask() == 50001.0

    def test_orderbook_init_returns_self(self):
        ob = MexcRequestOrderBookData(SAMPLE_DEPTH_RESP, "BTCUSDT", "SPOT")
        result = ob.init_data()
        assert result is ob

    def test_order_container(self):
        o = MexcRequestOrderData(SAMPLE_QUERY_ORDER_RESP, "BTCUSDT", "SPOT")
        o.init_data()
        assert o.get_exchange_name() == "MEXC"
        assert o.get_symbol_name() == "BTCUSDT"
        assert o.get_quantity() == 0.001
        assert o.get_status() == "FILLED"
        assert o.get_side() == "BUY"
        assert o.get_price() == 50000.0

    def test_order_init_returns_self(self):
        o = MexcRequestOrderData(SAMPLE_QUERY_ORDER_RESP, "BTCUSDT", "SPOT")
        result = o.init_data()
        assert result is o

    def test_balance_container(self):
        b = MexcRequestBalanceData(
            {"asset": "USDT", "free": "1000.50", "locked": "100.00"},
            None, "SPOT")
        b.init_data()
        assert b.get_asset() == "USDT"
        assert b.get_free() == 1000.50
        assert b.get_locked() == 100.0
        assert b.get_total() == 1100.50

    def test_balance_init_returns_self(self):
        b = MexcRequestBalanceData(
            {"asset": "USDT", "free": "1000.50", "locked": "100.00"},
            None, "SPOT")
        result = b.init_data()
        assert result is b


# ══════════════════════════════════════════════════════════════════════════
# 6. Registry tests
# ══════════════════════════════════════════════════════════════════════════

class TestMexcRegistry:

    def test_mexc_registered(self):
        assert "MEXC___SPOT" in ExchangeRegistry._feed_classes
        assert ExchangeRegistry._feed_classes["MEXC___SPOT"] == MexcRequestDataSpot
        assert "MEXC___SPOT" in ExchangeRegistry._exchange_data_classes
        assert ExchangeRegistry._exchange_data_classes["MEXC___SPOT"] == MexcExchangeDataSpot
        assert "MEXC___SPOT" in ExchangeRegistry._balance_handlers

    def test_mexc_create_feed(self):
        data_queue = queue.Queue()
        feed = ExchangeRegistry.create_feed(
            "MEXC___SPOT",
            data_queue,
            public_key="test",
            private_key="test",
        )
        assert isinstance(feed, MexcRequestDataSpot)

    def test_mexc_create_exchange_data(self):
        exchange_data = ExchangeRegistry.create_exchange_data("MEXC___SPOT")
        assert isinstance(exchange_data, MexcExchangeDataSpot)


# ══════════════════════════════════════════════════════════════════════════
# 7. Method existence checks
# ══════════════════════════════════════════════════════════════════════════

class TestMexcMethodExistence:

    def test_has_standard_methods(self):
        feed = _make_spot_feed()
        for method_name in (
            "get_tick", "get_depth", "get_kline",
            "get_server_time", "get_exchange_info",
            "make_order", "cancel_order", "query_order", "get_open_orders",
            "get_account", "get_balance",
            "async_get_tick", "async_get_depth", "async_get_kline",
            "async_get_server_time", "async_get_exchange_info",
            "async_make_order", "async_cancel_order", "async_query_order",
            "async_get_open_orders",
            "async_get_account", "async_get_balance",
        ):
            assert hasattr(feed, method_name), f"Missing method: {method_name}"

    def test_has_internal_methods(self):
        feed = _make_spot_feed()
        for method_name in (
            "_get_ticker", "_get_order_book", "_get_klines",
            "_get_server_time", "_get_exchange_info",
            "_make_order", "_cancel_order", "_get_order",
            "_get_open_orders", "_get_account", "_get_my_trades",
        ):
            assert hasattr(feed, method_name), f"Missing internal method: {method_name}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
