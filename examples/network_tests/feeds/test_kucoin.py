"""
Test KuCoin exchange integration — pure unit tests, no network calls.

Run tests:
    pytest tests/feeds/test_kucoin.py -v
"""

import queue
from unittest.mock import patch

import pytest

# Import registration to auto-register KuCoin
import bt_api_py.exchange_registers.register_kucoin  # noqa: F401
from bt_api_py.containers.bars.kucoin_bar import KuCoinRequestBarData
from bt_api_py.containers.exchanges.kucoin_exchange_data import (
    KuCoinExchangeDataFutures,
    KuCoinExchangeDataSpot,
)
from bt_api_py.containers.orderbooks.kucoin_orderbook import KuCoinRequestOrderBookData
from bt_api_py.containers.orders.kucoin_order import KuCoinRequestOrderData
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.containers.tickers.kucoin_ticker import KuCoinRequestTickerData
from bt_api_py.feeds.live_kucoin.futures import KuCoinRequestDataFutures
from bt_api_py.feeds.live_kucoin.spot import KuCoinRequestDataSpot
from bt_api_py.registry import ExchangeRegistry

# ── Sample KuCoin API responses ──────────────────────────────────────────

SAMPLE_TICKER_RESP = {
    "code": "200000",
    "data": {
        "time": 1688671955000,
        "sequence": "1234567890",
        "price": "50000",
        "size": "0.001",
        "bestBid": "49999",
        "bestBidSize": "1.5",
        "bestAsk": "50001",
        "bestAskSize": "2.3",
    },
}

SAMPLE_DEPTH_RESP = {
    "code": "200000",
    "data": {
        "time": 1688671955000,
        "sequence": "1234567890",
        "bids": [
            ["49999", "1.5"],
            ["49998", "2.3"],
        ],
        "asks": [
            ["50001", "1.8"],
            ["50002", "3.2"],
        ],
    },
}

SAMPLE_KLINE_RESP = {
    "code": "200000",
    "data": [
        ["1688671800", "50000", "50200", "50500", "49500", "1000.12345678", "50100000"],
        ["1688668200", "49800", "50000", "50100", "49700", "800.5", "39960000"],
    ],
}

SAMPLE_SERVER_TIME_RESP = {
    "code": "200000",
    "data": 1688671955000,
}

SAMPLE_EXCHANGE_INFO_RESP = {
    "code": "200000",
    "data": [
        {
            "symbol": "BTC-USDT",
            "name": "BTC-USDT",
            "baseCurrency": "BTC",
            "quoteCurrency": "USDT",
            "feeCurrency": "USDT",
            "market": "USDS",
            "baseMinSize": "0.00001",
            "quoteMinSize": "0.01",
            "baseMaxSize": "10000",
            "quoteMaxSize": "99999999",
            "baseIncrement": "0.00000001",
            "quoteIncrement": "0.000001",
            "priceIncrement": "0.1",
            "priceLimitRate": "0.1",
            "isMarginEnabled": True,
            "enableTrading": True,
        }
    ],
}

SAMPLE_MAKE_ORDER_RESP = {
    "code": "200000",
    "data": {
        "orderId": "5bd6e9286d99522a52e458de",
    },
}

SAMPLE_CANCEL_ORDER_RESP = {
    "code": "200000",
    "data": {
        "cancelledOrderIds": ["5bd6e9286d99522a52e458de"],
    },
}

SAMPLE_QUERY_ORDER_RESP = {
    "code": "200000",
    "data": {
        "id": "5bd6e9286d99522a52e458de",
        "symbol": "BTC-USDT",
        "type": "limit",
        "side": "buy",
        "price": "50000",
        "size": "0.001",
        "dealSize": "0.001",
        "isActive": False,
        "createdAt": 1688671955000,
        "clientOid": "client-123",
    },
}

SAMPLE_OPEN_ORDERS_RESP = {
    "code": "200000",
    "data": {
        "currentPage": 1,
        "pageSize": 50,
        "totalNum": 1,
        "totalPage": 1,
        "items": [
            {
                "id": "5bd6e9286d99522a52e458de",
                "symbol": "BTC-USDT",
                "type": "limit",
                "side": "buy",
                "price": "50000",
                "size": "0.001",
                "dealSize": "0",
                "isActive": True,
                "createdAt": 1688671955000,
            }
        ],
    },
}

SAMPLE_ACCOUNT_RESP = {
    "code": "200000",
    "data": [
        {
            "id": "5bd6e9286d99522a52e458de",
            "currency": "BTC",
            "type": "trade",
            "balance": "0.5",
            "available": "0.5",
            "holds": "0",
        },
        {
            "id": "5bd6e9286d99522a52e458df",
            "currency": "USDT",
            "type": "trade",
            "balance": "10000",
            "available": "9500",
            "holds": "500",
        },
    ],
}

SAMPLE_DEALS_RESP = {
    "code": "200000",
    "data": [
        {
            "sequence": "1234567890",
            "price": "50000",
            "size": "0.001",
            "side": "buy",
            "time": 1688671955000000000,
        },
    ],
}


# ── Helper ───────────────────────────────────────────────────────────────


def _make_spot_feed():
    data_queue = queue.Queue()
    return KuCoinRequestDataSpot(
        data_queue,
        public_key="test_key",
        private_key="test_secret",
        passphrase="test_passphrase",
        exchange_name="KUCOIN___SPOT",
    )


def _make_futures_feed():
    data_queue = queue.Queue()
    return KuCoinRequestDataFutures(
        data_queue,
        public_key="test_key",
        private_key="test_secret",
        passphrase="test_passphrase",
        exchange_name="KUCOIN___FUTURES",
    )


MOCK_PATH = "bt_api_py.feeds.http_client.HttpClient.request"


def _setup_mock(mock_request, resp_json):
    """Configure a mock for HttpClient.request that returns resp_json dict."""
    mock_request.return_value = resp_json


# ══════════════════════════════════════════════════════════════════════════
# 1. ExchangeData tests
# ══════════════════════════════════════════════════════════════════════════


class TestKuCoinExchangeData:
    def test_spot_creation(self):
        ed = KuCoinExchangeDataSpot()
        assert ed.exchange_name == "KUCOIN___SPOT"
        assert ed.rest_url == "https://api.kucoin.com"
        assert "make_order" in ed.rest_paths
        assert "get_ticker" in ed.rest_paths

    def test_futures_creation(self):
        ed = KuCoinExchangeDataFutures()
        assert "futures" in ed.exchange_name.lower() or "FUTURES" in ed.exchange_name
        assert "make_order" in ed.rest_paths

    def test_get_symbol(self):
        ed = KuCoinExchangeDataSpot()
        assert ed.get_symbol("BTC-USDT") == "BTC-USDT"
        assert ed.get_symbol("ETH-USDT") == "ETH-USDT"

    def test_get_rest_path(self):
        ed = KuCoinExchangeDataSpot()
        assert ed.get_rest_path("make_order") == "POST /api/v1/orders"
        assert ed.get_rest_path("get_ticker") == "GET /api/v1/market/orderbook/level1"

    def test_get_invalid_path_raises_error(self):
        ed = KuCoinExchangeDataSpot()
        with pytest.raises(NotImplementedError):
            ed.get_rest_path("invalid_path")

    @pytest.mark.kline
    def test_kline_periods(self):
        ed = KuCoinExchangeDataSpot()
        assert len(ed.kline_periods) > 0

    def test_legal_currency(self):
        ed = KuCoinExchangeDataSpot()
        assert "USDT" in ed.legal_currency


# ══════════════════════════════════════════════════════════════════════════
# 2. Layer-1 parameter generation
# ══════════════════════════════════════════════════════════════════════════


class TestKuCoinParamGeneration:
    def test_get_ticker_params(self):
        feed = _make_spot_feed()
        path, params, extra = feed._get_ticker("BTC-USDT")
        assert path == "GET /api/v1/market/orderbook/level1"
        assert params["symbol"] == "BTC-USDT"
        assert extra["symbol_name"] == "BTC-USDT"
        assert extra["normalize_function"] is not None

    def test_get_depth_params_small(self):
        feed = _make_spot_feed()
        path, params, extra = feed._get_depth("BTC-USDT", limit=20)
        assert "level2_20" in path
        assert params["symbol"] == "BTC-USDT"

    def test_get_depth_params_large(self):
        feed = _make_spot_feed()
        path, params, extra = feed._get_depth("BTC-USDT", limit=100)
        assert "level2_100" in path

    def test_get_kline_params(self):
        feed = _make_spot_feed()
        path, params, extra = feed._get_kline("BTC-USDT", period="1hour")
        assert path == "GET /api/v1/market/candles"
        assert params["symbol"] == "BTC-USDT"
        assert params["type"] == "1hour"

    def test_get_kline_params_with_time(self):
        feed = _make_spot_feed()
        path, params, extra = feed._get_kline(
            "BTC-USDT", period="5min", start_time=1000, end_time=2000
        )
        assert params["startAt"] == 1000
        assert params["endAt"] == 2000

    def test_make_order_params_limit(self):
        feed = _make_spot_feed()
        path, params, extra = feed._make_order(
            symbol="BTC-USDT", vol="0.001", price="50000", order_type="buy-limit"
        )
        assert path == "POST /api/v1/orders"
        assert params["symbol"] == "BTC-USDT"
        assert params["side"] == "BUY"
        assert params["type"] == "LIMIT"
        assert params["price"] == "50000"
        assert params["size"] == "0.001"
        assert "clientOid" in params

    def test_make_order_params_market_buy(self):
        feed = _make_spot_feed()
        path, params, extra = feed._make_order(
            symbol="BTC-USDT", vol="100", order_type="buy-market"
        )
        assert params["type"] == "MARKET"
        assert params["funds"] == "100"
        assert "size" not in params
        assert "price" not in params

    def test_make_order_params_market_sell(self):
        feed = _make_spot_feed()
        path, params, extra = feed._make_order(
            symbol="BTC-USDT", vol="0.5", order_type="sell-market"
        )
        assert params["type"] == "MARKET"
        assert params["size"] == "0.5"
        assert "funds" not in params

    def test_cancel_order_params_with_order_id(self):
        feed = _make_spot_feed()
        path, params, extra = feed._cancel_order(order_id="123456")
        assert path == "DELETE /api/v1/orders/123456"
        assert params == {}

    def test_cancel_order_params_with_client_order_id(self):
        feed = _make_spot_feed()
        path, params, extra = feed._cancel_order(client_order_id="client_123")
        assert path == "DELETE /api/v1/orders"
        assert params["clientOid"] == "client_123"

    def test_cancel_order_requires_id(self):
        feed = _make_spot_feed()
        with pytest.raises(ValueError):
            feed._cancel_order()

    def test_cancel_all_orders_params(self):
        feed = _make_spot_feed()
        path, params, extra = feed._cancel_all_orders(symbol="BTC-USDT")
        assert path == "DELETE /api/v1/orders"
        assert params["symbol"] == "BTC-USDT"

    def test_get_order_params(self):
        feed = _make_spot_feed()
        path, params, extra = feed._get_order(order_id="abc123")
        assert "abc123" in path
        assert extra["normalize_function"] is not None

    def test_get_open_orders_params(self):
        feed = _make_spot_feed()
        path, params, extra = feed._get_open_orders(symbol="BTC-USDT")
        assert params["status"] == "active"
        assert params["symbol"] == "BTC-USDT"

    def test_get_account_params(self):
        feed = _make_spot_feed()
        path, params, extra = feed._get_account(currency="BTC")
        assert params["currency"] == "BTC"

    def test_get_server_time_params(self):
        feed = _make_spot_feed()
        path, params, extra = feed._get_server_time()
        assert "timestamp" in path

    def test_get_exchange_info_params(self):
        feed = _make_spot_feed()
        path, params, extra = feed._get_exchange_info()
        assert "symbols" in path


# ══════════════════════════════════════════════════════════════════════════
# 3. Normalize functions
# ══════════════════════════════════════════════════════════════════════════


class TestKuCoinNormalize:
    @pytest.mark.ticker
    def test_ticker_normalize(self):
        extra = {"symbol_name": "BTC-USDT", "asset_type": "SPOT", "exchange_name": "KUCOIN"}
        tickers, ok = KuCoinRequestDataSpot._get_ticker_normalize_function(
            SAMPLE_TICKER_RESP, extra
        )
        assert ok is True
        assert len(tickers) == 1
        t = tickers[0]
        assert isinstance(t, KuCoinRequestTickerData)

    @pytest.mark.ticker
    def test_ticker_normalize_none(self):
        extra = {"symbol_name": "BTC-USDT", "asset_type": "SPOT"}
        tickers, ok = KuCoinRequestDataSpot._get_ticker_normalize_function(None, extra)
        assert tickers == []

    @pytest.mark.orderbook
    def test_depth_normalize(self):
        extra = {"symbol_name": "BTC-USDT", "asset_type": "SPOT"}
        books, ok = KuCoinRequestDataSpot._get_depth_normalize_function(SAMPLE_DEPTH_RESP, extra)
        assert ok is True
        assert len(books) == 1
        assert isinstance(books[0], KuCoinRequestOrderBookData)

    @pytest.mark.kline
    def test_kline_normalize(self):
        extra = {"symbol_name": "BTC-USDT", "asset_type": "SPOT"}
        bars, ok = KuCoinRequestDataSpot._get_kline_normalize_function(SAMPLE_KLINE_RESP, extra)
        assert ok is True
        assert len(bars) == 2
        assert isinstance(bars[0], KuCoinRequestBarData)

    def test_make_order_normalize(self):
        extra = {"symbol_name": "BTC-USDT", "asset_type": "SPOT"}
        orders, ok = KuCoinRequestDataSpot._make_order_normalize_function(
            SAMPLE_MAKE_ORDER_RESP, extra
        )
        assert ok is True
        assert len(orders) == 1
        assert isinstance(orders[0], KuCoinRequestOrderData)

    def test_get_order_normalize(self):
        extra = {"symbol_name": "BTC-USDT", "asset_type": "SPOT"}
        orders, ok = KuCoinRequestDataSpot._get_order_normalize_function(
            SAMPLE_QUERY_ORDER_RESP, extra
        )
        assert ok is True
        assert len(orders) == 1
        assert isinstance(orders[0], KuCoinRequestOrderData)

    def test_get_open_orders_normalize(self):
        extra = {"symbol_name": "ALL", "asset_type": "SPOT"}
        orders, ok = KuCoinRequestDataSpot._get_open_orders_normalize_function(
            SAMPLE_OPEN_ORDERS_RESP, extra
        )
        assert ok is True
        assert len(orders) == 1

    def test_exchange_info_normalize(self):
        data, ok = KuCoinRequestDataSpot._get_exchange_info_normalize_function(
            SAMPLE_EXCHANGE_INFO_RESP, {}
        )
        assert ok is True
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["symbol"] == "BTC-USDT"


# ══════════════════════════════════════════════════════════════════════════
# 4. Data containers
# ══════════════════════════════════════════════════════════════════════════


class TestKuCoinDataContainers:
    @pytest.mark.ticker
    def test_ticker_container(self):
        ticker = KuCoinRequestTickerData(SAMPLE_TICKER_RESP, "BTC-USDT", "SPOT", True)
        assert ticker.init_data() is ticker
        assert ticker.get_exchange_name() == "KUCOIN"
        assert ticker.get_symbol_name() == "BTC-USDT"
        assert ticker.get_last_price() == 50000
        assert ticker.get_bid_price() == 49999
        assert ticker.get_ask_price() == 50001

    @pytest.mark.ticker
    def test_ticker_container_idempotent(self):
        ticker = KuCoinRequestTickerData(SAMPLE_TICKER_RESP, "BTC-USDT", "SPOT", True)
        ticker.init_data()
        ticker.init_data()  # Second call should be safe
        assert ticker.get_last_price() == 50000

    @pytest.mark.orderbook
    def test_orderbook_container(self):
        ob = KuCoinRequestOrderBookData(SAMPLE_DEPTH_RESP, "BTC-USDT", "SPOT", True)
        assert ob.init_data() is ob
        assert ob.get_exchange_name() == "KUCOIN"
        assert ob.get_symbol_name() == "BTC-USDT"
        bids = ob.get_bid_price_list()
        asks = ob.get_ask_price_list()
        assert len(bids) == 2
        assert len(asks) == 2
        assert bids[0] == 49999.0
        assert asks[0] == 50001.0

    @pytest.mark.kline
    def test_bar_container(self):
        kline = ["1688671800", "50000", "50200", "50500", "49500", "1000.12", "50100000"]
        bar = KuCoinRequestBarData(kline, "BTC-USDT", "SPOT", True)
        assert bar.init_data() is bar
        assert bar.get_exchange_name() == "KUCOIN"
        assert bar.get_symbol_name() == "BTC-USDT"
        assert bar.get_open_price() == 50000.0
        assert bar.get_close_price() == 50200.0
        assert bar.get_high_price() == 50500.0
        assert bar.get_low_price() == 49500.0
        assert bar.get_volume() > 0

    def test_order_container(self):
        order = KuCoinRequestOrderData(SAMPLE_QUERY_ORDER_RESP, "BTC-USDT", "SPOT", True)
        assert order.init_data() is order
        assert order.get_exchange_name() == "KUCOIN"
        assert order.get_symbol_name() == "BTC-USDT"
        assert order.get_order_id() == "5bd6e9286d99522a52e458de"
        assert order.get_order_price() == 50000
        assert order.get_order_size() == 0.001


# ══════════════════════════════════════════════════════════════════════════
# 5. Layer-2 sync calls (mocked HTTP)
# ══════════════════════════════════════════════════════════════════════════


class TestKuCoinSyncCalls:
    @patch(MOCK_PATH)
    def test_get_ticker(self, mock_req):
        _setup_mock(mock_req, SAMPLE_TICKER_RESP)
        feed = _make_spot_feed()
        rd = feed.get_ticker("BTC-USDT")
        assert isinstance(rd, RequestData)
        data = rd.get_data()
        assert isinstance(data, list)
        assert len(data) == 1
        assert isinstance(data[0], KuCoinRequestTickerData)

    @patch(MOCK_PATH)
    def test_get_tick_alias(self, mock_req):
        _setup_mock(mock_req, SAMPLE_TICKER_RESP)
        feed = _make_spot_feed()
        rd = feed.get_tick("BTC-USDT")
        assert isinstance(rd, RequestData)

    @patch(MOCK_PATH)
    def test_get_depth(self, mock_req):
        _setup_mock(mock_req, SAMPLE_DEPTH_RESP)
        feed = _make_spot_feed()
        rd = feed.get_depth("BTC-USDT", 20)
        assert isinstance(rd, RequestData)
        data = rd.get_data()
        assert len(data) == 1
        ob = data[0]
        assert isinstance(ob, KuCoinRequestOrderBookData)
        ob.init_data()
        assert ob.get_bid_price_list()[0] == 49999.0

    @patch(MOCK_PATH)
    def test_get_kline(self, mock_req):
        _setup_mock(mock_req, SAMPLE_KLINE_RESP)
        feed = _make_spot_feed()
        rd = feed.get_kline("BTC-USDT", period="1hour")
        assert isinstance(rd, RequestData)
        data = rd.get_data()
        assert len(data) == 2
        bar = data[0]
        assert isinstance(bar, KuCoinRequestBarData)
        bar.init_data()
        assert bar.get_open_price() == 50000.0

    @patch(MOCK_PATH)
    def test_get_server_time(self, mock_req):
        _setup_mock(mock_req, SAMPLE_SERVER_TIME_RESP)
        feed = _make_spot_feed()
        rd = feed.get_server_time()
        assert isinstance(rd, RequestData)

    @patch(MOCK_PATH)
    def test_get_exchange_info(self, mock_req):
        _setup_mock(mock_req, SAMPLE_EXCHANGE_INFO_RESP)
        feed = _make_spot_feed()
        rd = feed.get_exchange_info()
        assert isinstance(rd, RequestData)
        data = rd.get_data()
        assert isinstance(data, list)
        assert len(data) == 1

    @patch(MOCK_PATH)
    def test_get_symbols_alias(self, mock_req):
        _setup_mock(mock_req, SAMPLE_EXCHANGE_INFO_RESP)
        feed = _make_spot_feed()
        rd = feed.get_symbols()
        assert isinstance(rd, RequestData)

    @patch(MOCK_PATH)
    def test_make_order(self, mock_req):
        _setup_mock(mock_req, SAMPLE_MAKE_ORDER_RESP)
        feed = _make_spot_feed()
        rd = feed.make_order("BTC-USDT", "0.001", "50000", "buy-limit")
        assert isinstance(rd, RequestData)
        data = rd.get_data()
        assert len(data) == 1
        assert isinstance(data[0], KuCoinRequestOrderData)

    @patch(MOCK_PATH)
    def test_cancel_order(self, mock_req):
        _setup_mock(mock_req, SAMPLE_CANCEL_ORDER_RESP)
        feed = _make_spot_feed()
        rd = feed.cancel_order(order_id="5bd6e9286d99522a52e458de")
        assert isinstance(rd, RequestData)

    @patch(MOCK_PATH)
    def test_cancel_all_orders(self, mock_req):
        _setup_mock(mock_req, SAMPLE_CANCEL_ORDER_RESP)
        feed = _make_spot_feed()
        rd = feed.cancel_all_orders(symbol="BTC-USDT")
        assert isinstance(rd, RequestData)

    @patch(MOCK_PATH)
    def test_get_order(self, mock_req):
        _setup_mock(mock_req, SAMPLE_QUERY_ORDER_RESP)
        feed = _make_spot_feed()
        rd = feed.get_order(order_id="5bd6e9286d99522a52e458de")
        assert isinstance(rd, RequestData)
        data = rd.get_data()
        assert len(data) == 1
        assert isinstance(data[0], KuCoinRequestOrderData)

    @patch(MOCK_PATH)
    def test_query_order_alias(self, mock_req):
        _setup_mock(mock_req, SAMPLE_QUERY_ORDER_RESP)
        feed = _make_spot_feed()
        rd = feed.query_order(order_id="5bd6e9286d99522a52e458de")
        assert isinstance(rd, RequestData)

    @patch(MOCK_PATH)
    def test_get_open_orders(self, mock_req):
        _setup_mock(mock_req, SAMPLE_OPEN_ORDERS_RESP)
        feed = _make_spot_feed()
        rd = feed.get_open_orders(symbol="BTC-USDT")
        assert isinstance(rd, RequestData)
        data = rd.get_data()
        assert len(data) == 1

    @patch(MOCK_PATH)
    def test_get_account(self, mock_req):
        _setup_mock(mock_req, SAMPLE_ACCOUNT_RESP)
        feed = _make_spot_feed()
        rd = feed.get_account()
        assert isinstance(rd, RequestData)

    @patch(MOCK_PATH)
    def test_get_balance(self, mock_req):
        _setup_mock(mock_req, SAMPLE_ACCOUNT_RESP)
        feed = _make_spot_feed()
        rd = feed.get_balance()
        assert isinstance(rd, RequestData)

    @patch(MOCK_PATH)
    def test_get_deals(self, mock_req):
        _setup_mock(mock_req, SAMPLE_DEALS_RESP)
        feed = _make_spot_feed()
        rd = feed.get_deals("BTC-USDT")
        assert isinstance(rd, RequestData)


# ══════════════════════════════════════════════════════════════════════════
# 6. Registry tests
# ══════════════════════════════════════════════════════════════════════════


class TestKuCoinRegistry:
    def test_spot_registered(self):
        assert "KUCOIN___SPOT" in ExchangeRegistry._feed_classes
        assert ExchangeRegistry._feed_classes["KUCOIN___SPOT"] is KuCoinRequestDataSpot
        assert "KUCOIN___SPOT" in ExchangeRegistry._exchange_data_classes
        assert ExchangeRegistry._exchange_data_classes["KUCOIN___SPOT"] is KuCoinExchangeDataSpot
        assert "KUCOIN___SPOT" in ExchangeRegistry._balance_handlers

    def test_futures_registered(self):
        assert "KUCOIN___FUTURES" in ExchangeRegistry._feed_classes
        assert ExchangeRegistry._feed_classes["KUCOIN___FUTURES"] is KuCoinRequestDataFutures
        assert "KUCOIN___FUTURES" in ExchangeRegistry._exchange_data_classes
        assert (
            ExchangeRegistry._exchange_data_classes["KUCOIN___FUTURES"] is KuCoinExchangeDataFutures
        )

    def test_create_feed_spot(self):
        data_queue = queue.Queue()
        feed = ExchangeRegistry.create_feed(
            "KUCOIN___SPOT",
            data_queue,
            public_key="test",
            private_key="test",
            passphrase="test",
        )
        assert isinstance(feed, KuCoinRequestDataSpot)

    def test_create_exchange_data_spot(self):
        ed = ExchangeRegistry.create_exchange_data("KUCOIN___SPOT")
        assert isinstance(ed, KuCoinExchangeDataSpot)

    def test_create_exchange_data_futures(self):
        ed = ExchangeRegistry.create_exchange_data("KUCOIN___FUTURES")
        assert isinstance(ed, KuCoinExchangeDataFutures)


# ══════════════════════════════════════════════════════════════════════════
# 7. Signing tests
# ══════════════════════════════════════════════════════════════════════════


class TestKuCoinSigning:
    def test_signature(self):
        feed = _make_spot_feed()
        sig = feed.signature(1234567890000, "GET", "/api/v1/orders", "test_secret", "")
        assert isinstance(sig, str)
        assert len(sig) > 0

    def test_encrypted_passphrase(self):
        feed = _make_spot_feed()
        enc = feed.get_encrypted_passphrase("test_passphrase", "test_secret")
        assert isinstance(enc, str)
        assert len(enc) > 0

    def test_get_header(self):
        feed = _make_spot_feed()
        headers = feed.get_header("key1", "sign1", 1234567890000, "pass1")
        assert headers["KC-API-KEY"] == "key1"
        assert headers["KC-API-SIGN"] == "sign1"
        assert headers["KC-API-TIMESTAMP"] == "1234567890000"
        assert headers["KC-API-PASSPHRASE"] == "pass1"
        assert headers["KC-API-KEY-VERSION"] == "2"

    def test_signed_request_has_headers(self):
        feed = _make_spot_feed()
        with patch(MOCK_PATH) as mock_req:
            _setup_mock(mock_req, SAMPLE_ACCOUNT_RESP)
            feed.get_account()
            call_kwargs = mock_req.call_args
            headers = (
                call_kwargs.kwargs.get("headers", call_kwargs[1].get("headers", {}))
                if call_kwargs
                else {}
            )
            assert "KC-API-KEY" in headers


# ══════════════════════════════════════════════════════════════════════════
# 8. Method existence checks
# ══════════════════════════════════════════════════════════════════════════


class TestKuCoinMethodExistence:
    def test_spot_has_all_methods(self):
        feed = _make_spot_feed()
        methods = [
            "get_ticker",
            "get_tick",
            "get_depth",
            "get_kline",
            "get_server_time",
            "get_exchange_info",
            "get_symbols",
            "make_order",
            "cancel_order",
            "cancel_all_orders",
            "get_order",
            "query_order",
            "get_open_orders",
            "get_account",
            "get_balance",
            "get_deals",
            "async_get_ticker",
            "async_get_tick",
            "async_get_depth",
            "async_get_kline",
            "async_get_account",
            "async_get_balance",
        ]
        for m in methods:
            assert hasattr(feed, m), f"Missing method: {m}"

    def test_futures_has_core_methods(self):
        feed = _make_futures_feed()
        methods = [
            "get_ticker",
            "get_tick",
            "get_depth",
            "get_kline",
            "get_server_time",
            "get_exchange_info",
            "make_order",
            "cancel_order",
            "get_open_orders",
            "get_account",
            "get_balance",
            "query_order",
            "async_get_ticker",
            "async_get_depth",
            "async_get_kline",
            "async_get_account",
            "async_get_balance",
        ]
        for m in methods:
            assert hasattr(feed, m), f"Missing method: {m}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
