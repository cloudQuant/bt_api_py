"""
Test Coinbase exchange integration — pure unit tests, no network calls.

Run tests:
    pytest tests/feeds/test_coinbase.py -v
"""

import queue
from unittest.mock import patch

import pytest

import bt_api_py.exchange_registers.register_coinbase  # noqa: F401
from bt_api_py.containers.accounts.coinbase_account import CoinbaseRequestAccountData
from bt_api_py.containers.bars.coinbase_bar import CoinbaseRequestBarData
from bt_api_py.containers.exchanges.coinbase_exchange_data import CoinbaseExchangeDataSpot
from bt_api_py.containers.orderbooks.coinbase_orderbook import CoinbaseRequestOrderBookData
from bt_api_py.containers.orders.coinbase_order import CoinbaseRequestOrderData
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.containers.tickers.coinbase_ticker import CoinbaseRequestTickerData
from bt_api_py.feeds.live_coinbase import CoinbaseRequestDataSpot
from bt_api_py.registry import ExchangeRegistry

# ── Sample Coinbase API responses ────────────────────────────────────────

SAMPLE_TICKER_RESP = {
    "trades": [
        {
            "trade_id": "123",
            "product_id": "BTC-USD",
            "price": "50000",
            "size": "0.001",
            "time": "2024-01-01T00:00:00Z",
            "side": "BUY",
            "bid": "49999",
            "ask": "50001",
        }
    ],
    "best_bid": "49999",
    "best_ask": "50001",
}

SAMPLE_DEPTH_RESP = {
    "pricebook": {
        "product_id": "BTC-USD",
        "bids": [
            {"price": "49999", "size": "1.5"},
            {"price": "49998", "size": "2.0"},
        ],
        "asks": [
            {"price": "50001", "size": "1.3"},
            {"price": "50002", "size": "1.8"},
        ],
        "time": "2024-01-01T00:00:00Z",
    }
}

SAMPLE_KLINE_RESP = {
    "candles": [
        {
            "start": "1688671800",
            "low": "49500",
            "high": "50500",
            "open": "50000",
            "close": "50200",
            "volume": "1000",
        },
        {
            "start": "1688675400",
            "low": "49800",
            "high": "50800",
            "open": "50200",
            "close": "50600",
            "volume": "800",
        },
    ]
}

SAMPLE_MAKE_ORDER_RESP = {
    "success": True,
    "success_response": {
        "order_id": "abc123",
        "product_id": "BTC-USD",
        "side": "BUY",
        "client_order_id": "my-order-001",
    },
}

SAMPLE_CANCEL_ORDER_RESP = {
    "results": [{"success": True, "order_id": "abc123"}],
}

SAMPLE_QUERY_ORDER_RESP = {
    "order": {
        "order_id": "abc123",
        "product_id": "BTC-USD",
        "side": "BUY",
        "status": "FILLED",
        "filled_size": "0.001",
        "average_filled_price": "50000",
        "created_time": "2024-01-01T00:00:00Z",
    }
}

SAMPLE_OPEN_ORDERS_RESP = {
    "orders": [
        {
            "order_id": "abc123",
            "product_id": "BTC-USD",
            "side": "BUY",
            "status": "OPEN",
            "created_time": "2024-01-01T00:00:00Z",
        },
        {
            "order_id": "def456",
            "product_id": "ETH-USD",
            "side": "SELL",
            "status": "OPEN",
            "created_time": "2024-01-01T01:00:00Z",
        },
    ],
    "has_next": False,
}

SAMPLE_ACCOUNT_RESP = {
    "accounts": [
        {
            "uuid": "acc-001",
            "name": "BTC Wallet",
            "currency": "BTC",
            "available_balance": {"value": "1.5", "currency": "BTC"},
            "hold": {"value": "0.1", "currency": "BTC"},
            "type": "ACCOUNT_TYPE_CRYPTO",
        },
        {
            "uuid": "acc-002",
            "name": "USD Wallet",
            "currency": "USD",
            "available_balance": {"value": "50000", "currency": "USD"},
            "hold": {"value": "1000", "currency": "USD"},
            "type": "ACCOUNT_TYPE_FIAT",
        },
    ],
    "has_next": False,
}

SAMPLE_SERVER_TIME_RESP = {
    "iso": "2024-01-01T00:00:00Z",
    "epochSeconds": "1704067200",
    "epochMillis": "1704067200000",
}

SAMPLE_EXCHANGE_INFO_RESP = {
    "products": [
        {
            "product_id": "BTC-USD",
            "price": "50000",
            "status": "online",
            "base_currency_id": "BTC",
            "quote_currency_id": "USD",
        },
        {
            "product_id": "ETH-USD",
            "price": "3000",
            "status": "online",
            "base_currency_id": "ETH",
            "quote_currency_id": "USD",
        },
    ],
    "num_products": 2,
}


# ── Helper ───────────────────────────────────────────────────────────────

MOCK_PATH = "bt_api_py.feeds.http_client.HttpClient.request"


def _make_spot_feed():
    data_queue = queue.Queue()
    return CoinbaseRequestDataSpot(
        data_queue,
        api_key="test_api_key",
        private_key="test_secret",
        exchange_name="COINBASE___SPOT",
    )


def _setup_mock(mock_req, resp_json, status_code=200):
    """Configure a mock for HttpClient.request that returns resp_json dict."""
    mock_req.return_value = resp_json


# ══════════════════════════════════════════════════════════════════════════
# 1. ExchangeData tests
# ══════════════════════════════════════════════════════════════════════════


class TestCoinbaseExchangeData:
    def test_spot_creation(self):
        ed = CoinbaseExchangeDataSpot()
        assert ed.exchange_name == "COINBASE___SPOT"
        assert ed.rest_url == "https://api.coinbase.com/api/v3"

    def test_get_symbol_keeps_hyphens(self):
        ed = CoinbaseExchangeDataSpot()
        assert ed.get_symbol("BTC-USD") == "BTC-USD"
        assert ed.get_symbol("ETH-USD") == "ETH-USD"

    def test_get_period(self):
        ed = CoinbaseExchangeDataSpot()
        assert ed.get_period("1m") == "ONE_MINUTE"
        assert ed.get_period("1h") == "ONE_HOUR"
        assert ed.get_period("1d") == "ONE_DAY"

    @pytest.mark.kline
    def test_kline_periods(self):
        ed = CoinbaseExchangeDataSpot()
        assert "1m" in ed.kline_periods
        assert "1h" in ed.kline_periods
        assert "1d" in ed.kline_periods

    def test_legal_currency(self):
        ed = CoinbaseExchangeDataSpot()
        assert "USD" in ed.legal_currency
        assert "USDC" in ed.legal_currency

    def test_rest_paths_present(self):
        ed = CoinbaseExchangeDataSpot()
        for key in (
            "get_ticker",
            "get_depth",
            "get_kline",
            "get_exchange_info",
            "make_order",
            "cancel_order",
            "query_order",
            "get_open_orders",
            "get_account",
            "get_balance",
            "get_server_time",
        ):
            path = ed.get_rest_path(key)
            assert path, f"rest_path '{key}' should not be empty"


# ══════════════════════════════════════════════════════════════════════════
# 2. Layer-1 parameter generation
# ══════════════════════════════════════════════════════════════════════════


class TestCoinbaseParamGeneration:
    def test_get_ticker_params(self):
        feed = _make_spot_feed()
        path, params, extra = feed._get_ticker("BTC-USD")
        assert "BTC-USD" in path
        assert extra["symbol_name"] == "BTC-USD"
        assert extra["normalize_function"] is not None

    def test_get_depth_params(self):
        feed = _make_spot_feed()
        path, params, extra = feed._get_depth("BTC-USD", limit=50)
        assert "product_book" in path
        assert params["product_id"] == "BTC-USD"
        assert params["limit"] == 50

    def test_get_kline_params(self):
        feed = _make_spot_feed()
        path, params, extra = feed._get_kline(
            "BTC-USD", period="1h", start_time=1000, end_time=2000
        )
        assert "BTC-USD" in path
        assert "candles" in path
        assert params["granularity"] == "ONE_HOUR"
        assert params["start"] == "1000"
        assert params["end"] == "2000"

    def test_make_order_params_limit(self):
        feed = _make_spot_feed()
        path, body, extra = feed._make_order("BTC-USD", "0.001", "50000", "buy-limit")
        assert "orders" in path
        assert body["product_id"] == "BTC-USD"
        assert body["side"] == "BUY"
        assert "limit_limit_gtc" in body["order_configuration"]
        cfg = body["order_configuration"]["limit_limit_gtc"]
        assert cfg["base_size"] == "0.001"
        assert cfg["limit_price"] == "50000"

    def test_make_order_params_market(self):
        feed = _make_spot_feed()
        path, body, extra = feed._make_order("BTC-USD", "100", order_type="buy-market")
        assert body["side"] == "BUY"
        assert "market_market_ioc" in body["order_configuration"]
        cfg = body["order_configuration"]["market_market_ioc"]
        assert cfg["quote_size"] == "100"

    def test_make_order_params_sell_market(self):
        feed = _make_spot_feed()
        path, body, extra = feed._make_order("BTC-USD", "0.01", order_type="sell-market")
        assert body["side"] == "SELL"
        cfg = body["order_configuration"]["market_market_ioc"]
        assert cfg["base_size"] == "0.01"

    def test_cancel_order_params(self):
        feed = _make_spot_feed()
        path, body, extra = feed._cancel_order(symbol="BTC-USD", order_id="abc123")
        assert "batch_cancel" in path
        assert "abc123" in body["order_ids"]

    def test_query_order_params(self):
        feed = _make_spot_feed()
        path, params, extra = feed._query_order("abc123")
        assert "abc123" in path
        assert extra["normalize_function"] is not None

    def test_get_open_orders_params(self):
        feed = _make_spot_feed()
        path, params, extra = feed._get_open_orders(symbol="BTC-USD")
        assert params["order_status"] == "OPEN"
        assert params["product_id"] == "BTC-USD"

    def test_get_account_params(self):
        feed = _make_spot_feed()
        path, params, extra = feed._get_account()
        assert "accounts" in path
        assert extra["normalize_function"] is not None

    def test_get_server_time_params(self):
        feed = _make_spot_feed()
        path, params, extra = feed._get_server_time()
        assert "time" in path.lower()

    def test_get_exchange_info_params(self):
        feed = _make_spot_feed()
        path, params, extra = feed._get_exchange_info()
        assert "products" in path
        assert extra["normalize_function"] is not None


# ══════════════════════════════════════════════════════════════════════════
# 3. Normalize functions
# ══════════════════════════════════════════════════════════════════════════


class TestCoinbaseNormalize:
    @pytest.mark.ticker
    def test_ticker_normalize(self):
        extra = {"symbol_name": "BTC-USD", "asset_type": "SPOT", "exchange_name": "COINBASE___SPOT"}
        tickers, ok = CoinbaseRequestDataSpot._get_ticker_normalize_function(
            SAMPLE_TICKER_RESP, extra
        )
        assert ok is True
        assert len(tickers) == 1
        assert isinstance(tickers[0], CoinbaseRequestTickerData)

    @pytest.mark.ticker
    def test_ticker_normalize_none(self):
        extra = {"symbol_name": "BTC-USD", "asset_type": "SPOT"}
        tickers, ok = CoinbaseRequestDataSpot._get_ticker_normalize_function(None, extra)
        assert ok is False
        assert tickers == []

    @pytest.mark.orderbook
    def test_depth_normalize(self):
        extra = {"symbol_name": "BTC-USD", "asset_type": "SPOT"}
        books, ok = CoinbaseRequestDataSpot._get_depth_normalize_function(SAMPLE_DEPTH_RESP, extra)
        assert ok is True
        assert len(books) == 1
        b = books[0]
        assert isinstance(b, CoinbaseRequestOrderBookData)
        b.init_data()
        assert b.get_bid_price_list()[0] == 49999.0
        assert b.get_ask_price_list()[0] == 50001.0

    @pytest.mark.orderbook
    def test_depth_normalize_none(self):
        extra = {"symbol_name": "BTC-USD", "asset_type": "SPOT"}
        books, ok = CoinbaseRequestDataSpot._get_depth_normalize_function(None, extra)
        assert ok is False
        assert books == []

    @pytest.mark.kline
    def test_kline_normalize(self):
        extra = {"symbol_name": "BTC-USD", "asset_type": "SPOT"}
        bars, ok = CoinbaseRequestDataSpot._get_kline_normalize_function(SAMPLE_KLINE_RESP, extra)
        assert ok is True
        assert len(bars) == 2
        bar = bars[0]
        assert isinstance(bar, CoinbaseRequestBarData)
        bar.init_data()
        assert bar.get_open() == 50000.0
        assert bar.get_close() == 50200.0

    @pytest.mark.kline
    def test_kline_normalize_none(self):
        extra = {"symbol_name": "BTC-USD", "asset_type": "SPOT"}
        bars, ok = CoinbaseRequestDataSpot._get_kline_normalize_function(None, extra)
        assert ok is False
        assert bars == []

    def test_make_order_normalize(self):
        extra = {"symbol_name": "BTC-USD", "asset_type": "SPOT", "exchange_name": "COINBASE___SPOT"}
        orders, ok = CoinbaseRequestDataSpot._make_order_normalize_function(
            SAMPLE_MAKE_ORDER_RESP, extra
        )
        assert ok is True
        assert len(orders) == 1
        assert isinstance(orders[0], CoinbaseRequestOrderData)

    def test_make_order_normalize_fail(self):
        extra = {"symbol_name": "BTC-USD", "asset_type": "SPOT"}
        resp = {"success": False, "error_response": {"error": "INVALID_PRICE"}}
        orders, ok = CoinbaseRequestDataSpot._make_order_normalize_function(resp, extra)
        assert ok is True  # input_data is not None
        assert orders == []

    def test_query_order_normalize(self):
        extra = {"symbol_name": "BTC-USD", "asset_type": "SPOT"}
        orders, ok = CoinbaseRequestDataSpot._query_order_normalize_function(
            SAMPLE_QUERY_ORDER_RESP, extra
        )
        assert ok is True
        assert len(orders) == 1
        assert isinstance(orders[0], CoinbaseRequestOrderData)

    def test_open_orders_normalize(self):
        extra = {"symbol_name": "ALL", "asset_type": "SPOT"}
        orders, ok = CoinbaseRequestDataSpot._get_open_orders_normalize_function(
            SAMPLE_OPEN_ORDERS_RESP, extra
        )
        assert ok is True
        assert len(orders) == 2

    def test_account_normalize(self):
        extra = {"symbol_name": "ALL", "asset_type": "SPOT"}
        accounts, ok = CoinbaseRequestDataSpot._get_account_normalize_function(
            SAMPLE_ACCOUNT_RESP, extra
        )
        assert ok is True
        assert len(accounts) == 2
        assert isinstance(accounts[0], CoinbaseRequestAccountData)

    def test_exchange_info_normalize(self):
        data, ok = CoinbaseRequestDataSpot._get_exchange_info_normalize_function(
            SAMPLE_EXCHANGE_INFO_RESP, {}
        )
        assert ok is True
        assert len(data) == 2


# ══════════════════════════════════════════════════════════════════════════
# 4. Synchronous API calls (mocked HTTP)
# ══════════════════════════════════════════════════════════════════════════


class TestCoinbaseSyncCalls:
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
        _setup_mock(mock_req, SAMPLE_DEPTH_RESP)
        feed = _make_spot_feed()
        result = feed.get_depth("BTC-USD", limit=50)
        assert isinstance(result, RequestData)

    @patch(MOCK_PATH)
    def test_get_kline(self, mock_req):
        _setup_mock(mock_req, SAMPLE_KLINE_RESP)
        feed = _make_spot_feed()
        result = feed.get_kline("BTC-USD", period="1h", start_time=1000, end_time=2000)
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
    def test_make_order(self, mock_req):
        _setup_mock(mock_req, SAMPLE_MAKE_ORDER_RESP)
        feed = _make_spot_feed()
        result = feed.make_order(
            symbol="BTC-USD", vol="0.001", price="50000", order_type="buy-limit"
        )
        assert isinstance(result, RequestData)

    @patch(MOCK_PATH)
    def test_cancel_order(self, mock_req):
        _setup_mock(mock_req, SAMPLE_CANCEL_ORDER_RESP)
        feed = _make_spot_feed()
        result = feed.cancel_order(symbol="BTC-USD", order_id="abc123")
        assert isinstance(result, RequestData)

    @patch(MOCK_PATH)
    def test_query_order(self, mock_req):
        _setup_mock(mock_req, SAMPLE_QUERY_ORDER_RESP)
        feed = _make_spot_feed()
        result = feed.query_order(order_id="abc123")
        assert isinstance(result, RequestData)

    @patch(MOCK_PATH)
    def test_get_open_orders(self, mock_req):
        _setup_mock(mock_req, SAMPLE_OPEN_ORDERS_RESP)
        feed = _make_spot_feed()
        result = feed.get_open_orders(symbol="BTC-USD")
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


class TestCoinbaseContainers:
    @pytest.mark.ticker
    def test_ticker_container(self):
        t = CoinbaseRequestTickerData(SAMPLE_TICKER_RESP, "BTC-USD", "SPOT", True)
        t.init_data()
        assert t.get_exchange_name() == "COINBASE"
        assert t.get_symbol_name() == "BTC-USD"

    @pytest.mark.orderbook
    def test_orderbook_container(self):
        ob = CoinbaseRequestOrderBookData(SAMPLE_DEPTH_RESP, "BTC-USD", "SPOT", True)
        ob.init_data()
        assert ob.get_exchange_name() == "COINBASE"
        assert ob.get_symbol_name() == "BTC-USD"
        assert len(ob.get_bid_price_list()) == 2
        assert len(ob.get_ask_price_list()) == 2
        assert ob.get_bid_price_list()[0] == 49999.0
        assert ob.get_ask_price_list()[0] == 50001.0

    @pytest.mark.kline
    def test_bar_container(self):
        candle = SAMPLE_KLINE_RESP["candles"][0]
        bar = CoinbaseRequestBarData(candle, "BTC-USD", "SPOT", True)
        bar.init_data()
        assert bar.get_exchange_name() == "COINBASE"
        assert bar.get_symbol_name() == "BTC-USD"
        assert bar.get_open() == 50000.0
        assert bar.get_high() == 50500.0
        assert bar.get_low() == 49500.0
        assert bar.get_close() == 50200.0
        assert bar.get_volume() == 1000.0

    def test_order_container(self):
        order_data = SAMPLE_MAKE_ORDER_RESP["success_response"]
        o = CoinbaseRequestOrderData(order_data, "BTC-USD", "SPOT", True)
        o.init_data()
        assert o.get_exchange_name() == "COINBASE"
        assert o.get_symbol_name() == "BTC-USD"

    def test_account_container(self):
        acc_data = SAMPLE_ACCOUNT_RESP["accounts"][0]
        a = CoinbaseRequestAccountData(acc_data, "ALL", "SPOT", True)
        a.init_data()
        assert a.get_exchange_name() == "COINBASE"

    @pytest.mark.orderbook
    def test_orderbook_init_returns_self(self):
        ob = CoinbaseRequestOrderBookData(SAMPLE_DEPTH_RESP, "BTC-USD", "SPOT", True)
        result = ob.init_data()
        assert result is ob

    @pytest.mark.kline
    def test_bar_init_returns_self(self):
        candle = SAMPLE_KLINE_RESP["candles"][0]
        bar = CoinbaseRequestBarData(candle, "BTC-USD", "SPOT", True)
        result = bar.init_data()
        assert result is bar


# ══════════════════════════════════════════════════════════════════════════
# 6. Registry tests
# ══════════════════════════════════════════════════════════════════════════


class TestCoinbaseRegistry:
    def test_coinbase_registered(self):
        assert "COINBASE___SPOT" in ExchangeRegistry._feed_classes
        assert "COINBASE___SPOT" in ExchangeRegistry._exchange_data_classes

    def test_coinbase_create_feed(self):
        data_queue = queue.Queue()
        feed = ExchangeRegistry.create_feed(
            "COINBASE___SPOT",
            data_queue,
            api_key="test",
            private_key="test",
        )
        assert isinstance(feed, CoinbaseRequestDataSpot)

    def test_coinbase_create_exchange_data(self):
        exchange_data = ExchangeRegistry.create_exchange_data("COINBASE___SPOT")
        assert isinstance(exchange_data, CoinbaseExchangeDataSpot)


# ══════════════════════════════════════════════════════════════════════════
# 7. Method existence checks
# ══════════════════════════════════════════════════════════════════════════


class TestCoinbaseMethodExistence:
    def test_has_standard_methods(self):
        feed = _make_spot_feed()
        for method_name in (
            "get_ticker",
            "get_tick",
            "get_depth",
            "get_kline",
            "get_server_time",
            "get_exchange_info",
            "make_order",
            "cancel_order",
            "query_order",
            "get_open_orders",
            "get_account",
            "get_balance",
            "async_get_ticker",
            "async_get_tick",
            "async_get_depth",
            "async_get_kline",
            "async_get_account",
            "async_get_balance",
        ):
            assert hasattr(feed, method_name), f"Missing method: {method_name}"

    def test_has_internal_methods(self):
        feed = _make_spot_feed()
        for method_name in (
            "_get_ticker",
            "_get_depth",
            "_get_kline",
            "_get_server_time",
            "_get_exchange_info",
            "_make_order",
            "_cancel_order",
            "_query_order",
            "_get_open_orders",
            "_get_account",
        ):
            assert hasattr(feed, method_name), f"Missing internal method: {method_name}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
