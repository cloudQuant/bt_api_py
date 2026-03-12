"""
Test Kraken exchange integration — pure unit tests, no network calls.

Run tests:
    pytest tests/feeds/test_kraken.py -v
"""

import queue
from unittest.mock import MagicMock, patch

import pytest

import bt_api_py.exchange_registers.register_kraken  # noqa: F401
from bt_api_py.containers.balances.kraken_balance import KrakenSpotWssBalanceData
from bt_api_py.containers.exchanges.kraken_exchange_data import (
    KrakenExchangeDataFutures,
    KrakenExchangeDataSpot,
)
from bt_api_py.containers.orderbooks.kraken_orderbook import KrakenRequestOrderBookData
from bt_api_py.containers.orders.kraken_order import KrakenRequestOrderData
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.containers.tickers.kraken_ticker import KrakenRequestTickerData
from bt_api_py.feeds.live_kraken import KrakenRequestData, KrakenRequestDataSpot
from bt_api_py.feeds.live_kraken.futures import KrakenRequestDataFutures
from bt_api_py.registry import ExchangeRegistry

# ── Sample Kraken API responses ──────────────────────────────────────────

SAMPLE_TICKER_RESP = {
    "error": [],
    "result": {
        "XBTUSD": {
            "a": ["50001.00000", "1", "1.000"],
            "b": ["49999.00000", "2", "2.000"],
            "c": ["50000.00000", "0.00100000"],
            "v": ["1234.56789012", "5678.90123456"],
            "p": ["49500.00000", "49800.00000"],
            "t": [1000, 5000],
            "l": ["49000.00000", "48500.00000"],
            "h": ["51000.00000", "51500.00000"],
            "o": "49500.00000",
        }
    },
}

SAMPLE_DEPTH_RESP = {
    "error": [],
    "result": {
        "XBTUSD": {
            "asks": [
                ["50001.00000", "1.000", 1234567890],
                ["50002.00000", "2.000", 1234567890],
            ],
            "bids": [
                ["49999.00000", "1.000", 1234567890],
                ["49998.00000", "2.000", 1234567890],
            ],
        }
    },
}

SAMPLE_BALANCE_RESP = {
    "error": [],
    "result": {
        "XXBT": "0.5000000000",
        "XETH": "5.0000000000",
        "ZUSD": "10000.00",
    },
}

SAMPLE_MAKE_ORDER_RESP = {
    "error": [],
    "result": {
        "descr": {"order": "buy 0.001 XBTUSD @ limit 50000.0"},
        "txid": ["OUF4EM-FRGI2-MQMWZD"],
    },
}

SAMPLE_QUERY_ORDER_RESP = {
    "error": [],
    "result": {
        "OUF4EM-FRGI2-MQMWZD": {
            "status": "open",
            "opentm": 1688671955.1234,
            "descr": {
                "pair": "XBTUSD",
                "type": "buy",
                "ordertype": "limit",
                "price": "50000.0",
                "order": "buy 0.001 XBTUSD @ limit 50000.0",
            },
            "vol": "0.00100000",
            "vol_exec": "0.00000000",
            "cost": "0.00000",
            "fee": "0.00000",
        }
    },
}

SAMPLE_OPEN_ORDERS_RESP = {
    "error": [],
    "result": {
        "open": {
            "OUF4EM-FRGI2-MQMWZD": {
                "status": "open",
                "opentm": 1688671955.1234,
                "descr": {
                    "pair": "XBTUSD",
                    "type": "buy",
                    "ordertype": "limit",
                    "price": "50000.0",
                },
                "vol": "0.00100000",
                "vol_exec": "0.00000000",
            }
        }
    },
}

SAMPLE_SERVER_TIME_RESP = {
    "error": [],
    "result": {"unixtime": 1688671955, "rfc1123": "Thu, 06 Jul 23 14:12:35 +0000"},
}

SAMPLE_CANCEL_ORDER_RESP = {
    "error": [],
    "result": {"count": 1},
}


# ── Helper ───────────────────────────────────────────────────────────────


def _make_spot_feed():
    data_queue = queue.Queue()
    return KrakenRequestDataSpot(
        data_queue,
        public_key="test_api_key",
        private_key="dGVzdF9zZWNyZXQ=",  # base64("test_secret")
        exchange_name="KRAKEN",
    )


def _setup_mock(mock_post, resp_json):
    """Configure a mock for requests.post that returns resp_json."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = resp_json
    mock_resp.status_code = 200
    mock_post.return_value = mock_resp


# ══════════════════════════════════════════════════════════════════════════
# 1. ExchangeData tests
# ══════════════════════════════════════════════════════════════════════════


class TestKrakenExchangeData:
    def test_spot_creation(self):
        ed = KrakenExchangeDataSpot()
        assert ed.exchange_name == "kraken"
        assert ed.asset_type == "SPOT"
        assert ed.rest_url == "https://api.kraken.com"

    def test_futures_creation(self):
        ed = KrakenExchangeDataFutures()
        assert ed.exchange_name == "krakenFutures"
        assert ed.asset_type == "FUTURES"
        assert ed.rest_url == "https://futures.kraken.com"

    def test_get_symbol_btc(self):
        ed = KrakenExchangeDataSpot()
        assert ed.get_symbol("BTC/USD") == "XBTUSD"
        assert ed.get_symbol("BTC/USDT") == "XBTUSDT"
        assert ed.get_symbol("ETH/BTC") == "ETHXBT"

    def test_get_symbol_non_btc(self):
        ed = KrakenExchangeDataSpot()
        assert ed.get_symbol("ETH/USD") == "ETHUSD"
        assert ed.get_symbol("SOL/USDT") == "SOLUSDT"

    def test_get_period(self):
        ed = KrakenExchangeDataSpot()
        assert ed.get_period("1m") == "1"
        assert ed.get_period("1h") == "60"
        assert ed.get_period("1d") == "1440"
        assert ed.get_period("unknown") == "unknown"

    def test_get_rest_path_spot(self):
        ed = KrakenExchangeDataSpot()
        assert "Ticker" in ed.get_rest_path("get_tick")
        assert "Ticker" in ed.get_rest_path("get_ticker")
        assert "AddOrder" in ed.get_rest_path("make_order")
        assert "Balance" in ed.get_rest_path("get_balance")
        assert "Depth" in ed.get_rest_path("get_depth")
        assert ed.get_rest_path("nonexistent") == ""

    def test_get_rest_path_futures(self):
        ed = KrakenExchangeDataFutures()
        assert "ticker" in ed.get_rest_path("get_tick").lower()
        assert "orders" in ed.get_rest_path("make_order").lower()

    def test_account_wss_symbol(self):
        ed = KrakenExchangeDataSpot()
        assert ed.account_wss_symbol("BTC/USD") == "XBTUSD"


# ══════════════════════════════════════════════════════════════════════════
# 2. Layer-1 parameter generation
# ══════════════════════════════════════════════════════════════════════════


class TestKrakenParamGeneration:
    def test_get_ticker_params(self):
        feed = _make_spot_feed()
        path, params, extra = feed._get_ticker("BTC/USD")
        assert "Ticker" in path
        assert params["pair"] == "XBTUSD"
        assert extra["symbol_name"] == "BTC/USD"
        assert extra["normalize_function"] is not None

    def test_get_depth_params(self):
        feed = _make_spot_feed()
        path, params, extra = feed._get_depth("BTC/USD", count=100)
        assert "Depth" in path
        assert params["pair"] == "XBTUSD"
        assert params["count"] == 100

    def test_get_kline_params(self):
        feed = _make_spot_feed()
        path, params, extra = feed._get_kline("BTC/USD", period="1h")
        assert "OHLC" in path or "Kline" in path
        assert params["pair"] == "XBTUSD"
        assert params["interval"] == "60"

    def test_make_order_params_limit(self):
        feed = _make_spot_feed()
        path, body, extra = feed._make_order("BTC/USD", "0.001", "50000", "buy-limit")
        assert "AddOrder" in path
        assert body["type"] == "buy"
        assert body["ordertype"] == "limit"
        assert body["volume"] == "0.001"
        assert body["price"] == "50000"
        assert body["pair"] == "XBTUSD"

    def test_make_order_params_market(self):
        feed = _make_spot_feed()
        path, body, extra = feed._make_order("BTC/USD", "0.001", order_type="sell-market")
        assert body["type"] == "sell"
        assert body["ordertype"] == "market"
        assert "price" not in body

    def test_cancel_order_params(self):
        feed = _make_spot_feed()
        path, body, extra = feed._cancel_order("OXXXX-YYYY-ZZZZZ")
        assert "CancelOrder" in path
        assert body["txid"] == "OXXXX-YYYY-ZZZZZ"

    def test_query_order_params(self):
        feed = _make_spot_feed()
        path, body, extra = feed._query_order("OXXXX-YYYY-ZZZZZ")
        assert "QueryOrders" in path
        assert body["txid"] == "OXXXX-YYYY-ZZZZZ"

    def test_get_balance_params(self):
        feed = _make_spot_feed()
        path, params, extra = feed._get_balance()
        assert "Balance" in path

    def test_get_open_orders_params(self):
        feed = _make_spot_feed()
        path, body, extra = feed._get_open_orders()
        assert "OpenOrders" in path

    def test_get_server_time_params(self):
        feed = _make_spot_feed()
        path, params, extra = feed._get_server_time()
        assert "Time" in path


# ══════════════════════════════════════════════════════════════════════════
# 3. Normalize functions
# ══════════════════════════════════════════════════════════════════════════


class TestKrakenNormalize:
    @pytest.mark.ticker
    def test_ticker_normalize(self):
        from bt_api_py.feeds.live_kraken.spot import KrakenRequestDataSpot

        extra = {"symbol_name": "BTC/USD", "asset_type": "SPOT", "exchange_name": "KRAKEN"}
        tickers, ok = KrakenRequestDataSpot._get_ticker_normalize_function(
            SAMPLE_TICKER_RESP, extra
        )
        assert ok is True
        assert len(tickers) == 1
        t = tickers[0]
        assert isinstance(t, KrakenRequestTickerData)
        assert t.last_price == 50000.0
        assert t.bid_price == 49999.0
        assert t.ask_price == 50001.0

    @pytest.mark.ticker
    def test_ticker_normalize_error(self):
        from bt_api_py.feeds.live_kraken.spot import KrakenRequestDataSpot

        extra = {"symbol_name": "BTC/USD", "asset_type": "SPOT"}
        data = {"error": ["EGeneral:Invalid arguments"], "result": {}}
        tickers, ok = KrakenRequestDataSpot._get_ticker_normalize_function(data, extra)
        assert ok is False
        assert tickers == []

    @pytest.mark.ticker
    def test_ticker_normalize_none(self):
        from bt_api_py.feeds.live_kraken.spot import KrakenRequestDataSpot

        extra = {"symbol_name": "BTC/USD", "asset_type": "SPOT"}
        tickers, ok = KrakenRequestDataSpot._get_ticker_normalize_function(None, extra)
        assert ok is False

    @pytest.mark.orderbook
    def test_depth_normalize(self):
        from bt_api_py.feeds.live_kraken.spot import KrakenRequestDataSpot

        extra = {"symbol_name": "BTC/USD", "asset_type": "SPOT"}
        books, ok = KrakenRequestDataSpot._get_depth_normalize_function(SAMPLE_DEPTH_RESP, extra)
        assert ok is True
        assert len(books) == 1
        b = books[0]
        assert isinstance(b, KrakenRequestOrderBookData)
        bids = b.get_bid_price_list()
        asks = b.get_ask_price_list()
        assert bids[0] == 49999.0
        assert asks[0] == 50001.0

    def test_balance_normalize(self):
        from bt_api_py.feeds.live_kraken.spot import KrakenRequestDataSpot

        extra = {"asset_type": "SPOT"}
        balances, ok = KrakenRequestDataSpot._get_balance_normalize_function(
            SAMPLE_BALANCE_RESP, extra
        )
        assert ok is True
        assert len(balances) == 3  # XXBT, XETH, ZUSD all > 0
        currencies = [bal.currency for bal in balances]
        assert "XXBT" in currencies

    def test_make_order_normalize(self):
        from bt_api_py.feeds.live_kraken.spot import KrakenRequestDataSpot

        extra = {"symbol_name": "BTC/USD", "asset_type": "SPOT"}
        orders, ok = KrakenRequestDataSpot._make_order_normalize_function(
            SAMPLE_MAKE_ORDER_RESP, extra
        )
        assert ok is True
        assert len(orders) == 1
        assert isinstance(orders[0], KrakenRequestOrderData)

    def test_query_order_normalize(self):
        from bt_api_py.feeds.live_kraken.spot import KrakenRequestDataSpot

        extra = {"symbol_name": "BTC/USD", "asset_type": "SPOT"}
        orders, ok = KrakenRequestDataSpot._query_order_normalize_function(
            SAMPLE_QUERY_ORDER_RESP, extra
        )
        assert ok is True
        assert len(orders) == 1
        o = orders[0]
        assert isinstance(o, KrakenRequestOrderData)

    def test_open_orders_normalize(self):
        from bt_api_py.feeds.live_kraken.spot import KrakenRequestDataSpot

        extra = {"asset_type": "SPOT"}
        orders, ok = KrakenRequestDataSpot._get_open_orders_normalize_function(
            SAMPLE_OPEN_ORDERS_RESP, extra
        )
        assert ok is True
        assert len(orders) == 1

    def test_extract_data_normalize(self):
        data, ok = KrakenRequestData._extract_data_normalize_function(SAMPLE_SERVER_TIME_RESP, {})
        assert ok is True
        assert len(data) == 1
        assert data[0]["unixtime"] == 1688671955


# ══════════════════════════════════════════════════════════════════════════
# 4. Data containers
# ══════════════════════════════════════════════════════════════════════════


class TestKrakenDataContainers:
    @pytest.mark.ticker
    def test_ticker_container(self):
        ticker = KrakenRequestTickerData(SAMPLE_TICKER_RESP, "BTC/USD", "SPOT")
        assert ticker.symbol == "BTC/USD"
        assert ticker.exchange == "kraken"
        assert ticker.last_price == 50000.0
        assert ticker.bid_price == 49999.0
        assert ticker.ask_price == 50001.0
        assert ticker.init_data() is ticker

    @pytest.mark.orderbook
    def test_orderbook_container(self):
        ob = KrakenRequestOrderBookData(SAMPLE_DEPTH_RESP, "BTC/USD", "SPOT")
        assert ob.init_data() is ob
        assert ob.symbol_name == "BTC/USD"
        bids = ob.get_bid_price_list()
        asks = ob.get_ask_price_list()
        assert len(bids) == 2
        assert len(asks) == 2
        assert bids[0] == 49999.0
        assert asks[0] == 50001.0

    def test_balance_container(self):
        bal = KrakenSpotWssBalanceData({"XXBT": "0.5"}, "SPOT")
        assert bal.currency == "XXBT"
        assert bal.free == 0.5

    def test_order_container(self):
        order_data = {
            "txid": "O123456",
            "descr": {
                "pair": "XBTUSD",
                "type": "buy",
                "ordertype": "limit",
                "price": "50000.0",
                "order": "buy 0.001 XBTUSD @ limit 50000.0",
            },
            "status": "open",
            "opentm": 1688671955.1234,
            "vol": "0.00100000",
            "vol_exec": "0.00000000",
        }
        order = KrakenRequestOrderData(order_data, "BTC/USD", "SPOT", True)
        assert order.init_data() is order
        assert order.get_order_id() == "O123456"
        assert order.side == "buy"
        assert order.status == "open"


# ══════════════════════════════════════════════════════════════════════════
# 5. Layer-2 sync calls (mocked HTTP)
# ══════════════════════════════════════════════════════════════════════════


class TestKrakenSyncCalls:
    @patch("bt_api_py.feeds.live_kraken.request_base.req_lib.post")
    def test_get_ticker(self, mock_post):
        _setup_mock(mock_post, SAMPLE_TICKER_RESP)
        feed = _make_spot_feed()
        rd = feed.get_ticker("BTC/USD")
        assert isinstance(rd, RequestData)
        data = rd.get_data()
        assert isinstance(data, list)
        assert len(data) == 1
        assert isinstance(data[0], KrakenRequestTickerData)
        assert data[0].last_price == 50000.0

    @patch("bt_api_py.feeds.live_kraken.request_base.req_lib.post")
    def test_get_tick_alias(self, mock_post):
        _setup_mock(mock_post, SAMPLE_TICKER_RESP)
        feed = _make_spot_feed()
        rd = feed.get_tick("BTC/USD")
        assert isinstance(rd, RequestData)

    @patch("bt_api_py.feeds.live_kraken.request_base.req_lib.post")
    def test_get_depth(self, mock_post):
        _setup_mock(mock_post, SAMPLE_DEPTH_RESP)
        feed = _make_spot_feed()
        rd = feed.get_depth("BTC/USD", 20)
        assert isinstance(rd, RequestData)
        data = rd.get_data()
        assert len(data) == 1
        ob = data[0]
        assert isinstance(ob, KrakenRequestOrderBookData)
        assert ob.get_bid_price_list()[0] == 49999.0

    @patch("bt_api_py.feeds.live_kraken.request_base.req_lib.post")
    def test_get_server_time(self, mock_post):
        _setup_mock(mock_post, SAMPLE_SERVER_TIME_RESP)
        feed = _make_spot_feed()
        rd = feed.get_server_time()
        assert isinstance(rd, RequestData)
        data = rd.get_data()
        assert isinstance(data, list)
        assert data[0]["unixtime"] == 1688671955

    @patch("bt_api_py.feeds.live_kraken.request_base.req_lib.post")
    def test_get_balance(self, mock_post):
        _setup_mock(mock_post, SAMPLE_BALANCE_RESP)
        feed = _make_spot_feed()
        rd = feed.get_balance()
        assert isinstance(rd, RequestData)
        data = rd.get_data()
        assert isinstance(data, list)
        assert len(data) == 3

    @patch("bt_api_py.feeds.live_kraken.request_base.req_lib.post")
    def test_get_account_alias(self, mock_post):
        _setup_mock(mock_post, SAMPLE_BALANCE_RESP)
        feed = _make_spot_feed()
        rd = feed.get_account()
        assert isinstance(rd, RequestData)

    @patch("bt_api_py.feeds.live_kraken.request_base.req_lib.post")
    def test_make_order(self, mock_post):
        _setup_mock(mock_post, SAMPLE_MAKE_ORDER_RESP)
        feed = _make_spot_feed()
        rd = feed.make_order("BTC/USD", "0.001", "50000", "buy-limit")
        assert isinstance(rd, RequestData)
        data = rd.get_data()
        assert len(data) == 1
        assert isinstance(data[0], KrakenRequestOrderData)

    @patch("bt_api_py.feeds.live_kraken.request_base.req_lib.post")
    def test_cancel_order(self, mock_post):
        _setup_mock(mock_post, SAMPLE_CANCEL_ORDER_RESP)
        feed = _make_spot_feed()
        rd = feed.cancel_order(symbol="BTC/USD", order_id="OXXXX")
        assert isinstance(rd, RequestData)

    @patch("bt_api_py.feeds.live_kraken.request_base.req_lib.post")
    def test_query_order(self, mock_post):
        _setup_mock(mock_post, SAMPLE_QUERY_ORDER_RESP)
        feed = _make_spot_feed()
        rd = feed.query_order(symbol="BTC/USD", order_id="OUF4EM-FRGI2-MQMWZD")
        assert isinstance(rd, RequestData)
        data = rd.get_data()
        assert len(data) == 1

    @patch("bt_api_py.feeds.live_kraken.request_base.req_lib.post")
    def test_get_open_orders(self, mock_post):
        _setup_mock(mock_post, SAMPLE_OPEN_ORDERS_RESP)
        feed = _make_spot_feed()
        rd = feed.get_open_orders()
        assert isinstance(rd, RequestData)
        data = rd.get_data()
        assert len(data) == 1

    @patch("bt_api_py.feeds.live_kraken.request_base.req_lib.post")
    def test_get_exchange_info(self, mock_post):
        _setup_mock(mock_post, SAMPLE_SERVER_TIME_RESP)
        feed = _make_spot_feed()
        rd = feed.get_exchange_info()
        assert isinstance(rd, RequestData)


# ══════════════════════════════════════════════════════════════════════════
# 6. Registry tests
# ══════════════════════════════════════════════════════════════════════════


class TestKrakenRegistry:
    def test_spot_registered(self):
        assert "KRAKEN___SPOT" in ExchangeRegistry._feed_classes
        assert ExchangeRegistry._feed_classes["KRAKEN___SPOT"] is KrakenRequestDataSpot
        assert "KRAKEN___SPOT" in ExchangeRegistry._exchange_data_classes
        assert ExchangeRegistry._exchange_data_classes["KRAKEN___SPOT"] is KrakenExchangeDataSpot
        assert "KRAKEN___SPOT" in ExchangeRegistry._balance_handlers

    def test_futures_registered(self):
        assert "KRAKEN___FUTURES" in ExchangeRegistry._feed_classes
        assert ExchangeRegistry._feed_classes["KRAKEN___FUTURES"] is KrakenRequestDataFutures
        assert "KRAKEN___FUTURES" in ExchangeRegistry._exchange_data_classes
        assert (
            ExchangeRegistry._exchange_data_classes["KRAKEN___FUTURES"] is KrakenExchangeDataFutures
        )

    def test_create_exchange_data_spot(self):
        ed = ExchangeRegistry.create_exchange_data("KRAKEN___SPOT")
        assert isinstance(ed, KrakenExchangeDataSpot)

    def test_create_exchange_data_futures(self):
        ed = ExchangeRegistry.create_exchange_data("KRAKEN___FUTURES")
        assert isinstance(ed, KrakenExchangeDataFutures)


# ══════════════════════════════════════════════════════════════════════════
# 7. Signing test
# ══════════════════════════════════════════════════════════════════════════


class TestKrakenSigning:
    def test_sign_request(self):
        feed = _make_spot_feed()
        data = {"pair": "XBTUSD", "type": "buy"}
        headers = feed._sign_request("/0/private/AddOrder", data)
        assert "API-Key" in headers
        assert "API-Sign" in headers
        assert "nonce" in data

    def test_sign_request_no_keys(self):
        data_queue = queue.Queue()
        feed = KrakenRequestDataSpot(data_queue)
        data = {"pair": "XBTUSD"}
        headers = feed._sign_request("/0/private/AddOrder", data)
        assert headers == {}

    def test_nonce_increases(self):
        feed = _make_spot_feed()
        n1 = feed._generate_nonce()
        n2 = feed._generate_nonce()
        assert int(n2) > int(n1)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
