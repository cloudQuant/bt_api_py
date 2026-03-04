"""
Tests for Poloniex Spot Feed implementation.
Comprehensive coverage: parameter generation, normalization, mocked HTTP sync
calls, registry, method existence, feed init, auth, and integration tests.
"""

import json
import queue
import pytest
from unittest.mock import patch, MagicMock

from bt_api_py.feeds.live_poloniex.spot import PoloniexRequestDataSpot
from bt_api_py.containers.exchanges.poloniex_exchange_data import (
    PoloniexExchangeDataSpot,
)
from bt_api_py.containers.requestdatas.request_data import RequestData

# ── Sample API responses ─────────────────────────────────────

SAMPLE_TICKER = {
    "symbol": "BTC_USDT",
    "open": "49500.00",
    "high": "51000.00",
    "low": "49000.00",
    "close": "50000.00",
    "quantity": "1234.56",
    "amount": "61728000",
    "bid": "49999.00",
    "bidQuantity": "1.0",
    "ask": "50001.00",
    "askQuantity": "2.0",
    "ts": 1704067200000,
}

SAMPLE_ORDERBOOK = {
    "bids": [["49999", "1.5"], ["49998", "3.0"]],
    "asks": [["50001", "1.0"], ["50002", "2.0"]],
    "ts": 1704067200000,
}

SAMPLE_KLINES = [
    ["50000", "51000", "49000", "50500", "1000", "50000000", "1234", 1704067200000, 1704070800000, 0],
    ["50500", "51500", "50000", "51000", "1500", "75000000", "2345", 1704070800000, 1704074400000, 0],
]

SAMPLE_TRADES = [
    {"id": "1001", "price": "50000", "quantity": "0.01", "takerSide": "buy", "ts": 1704067200000, "createTime": 1704067200000},
    {"id": "1002", "price": "50001", "quantity": "0.02", "takerSide": "sell", "ts": 1704067201000, "createTime": 1704067201000},
]

SAMPLE_BALANCE = [
    {"currencyId": "214", "currency": "USDT", "available": "1000.00", "hold": "100.00"},
    {"currencyId": "28", "currency": "BTC", "available": "0.5", "hold": "0.1"},
]

SAMPLE_ORDER = {
    "id": "123456",
    "clientOrderId": "my-order-1",
    "symbol": "BTC_USDT",
    "state": "NEW",
    "accountType": "SPOT",
    "side": "BUY",
    "type": "LIMIT",
    "timeInForce": "GTC",
    "quantity": "1.0",
    "price": "50000",
    "createTime": 1704067200000,
    "updateTime": 1704067200000,
}

SAMPLE_SERVER_TIME = {"serverTime": 1704067200000}

SAMPLE_MARKETS = [
    {
        "symbol": "BTC_USDT",
        "baseCurrencyName": "BTC",
        "quoteCurrencyName": "USDT",
        "displayName": "BTC/USDT",
        "state": "NORMAL",
        "symbolTradeLimit": {"symbol": "BTC_USDT", "priceScale": 2, "quantityScale": 6},
    },
]

SAMPLE_ERROR = {"code": 21304, "message": "Insufficient balance"}


# ── Helpers ──────────────────────────────────────────────────

def _make_feed(**kwargs):
    q = kwargs.pop("data_queue", queue.Queue())
    defaults = {
        "public_key": "test_key",
        "private_key": "test_secret",
    }
    defaults.update(kwargs)
    return PoloniexRequestDataSpot(q, **defaults)


def _mock_http(return_json):
    """Create a mock for Feed.http_request that returns JSON as a dict."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = return_json
    mock_resp.status_code = 200
    return return_json  # http_request returns parsed data


# ── 1. Exchange Data Container ───────────────────────────────


class TestExchangeData:
    def test_exchange_name(self):
        ed = PoloniexExchangeDataSpot()
        assert ed.exchange_name == "POLONIEX___SPOT"

    def test_rest_url(self):
        ed = PoloniexExchangeDataSpot()
        assert ed.rest_url.startswith("https://")

    def test_wss_url(self):
        ed = PoloniexExchangeDataSpot()
        assert ed.wss_url.startswith("wss://")

    def test_get_symbol_underscore(self):
        ed = PoloniexExchangeDataSpot()
        assert ed.get_symbol("BTC_USDT") == "BTC_USDT"

    def test_get_symbol_slash(self):
        ed = PoloniexExchangeDataSpot()
        assert ed.get_symbol("BTC/USDT") == "BTC_USDT"

    def test_get_symbol_dash(self):
        ed = PoloniexExchangeDataSpot()
        assert ed.get_symbol("BTC-USDT") == "BTC_USDT"

    def test_get_symbol_no_separator(self):
        ed = PoloniexExchangeDataSpot()
        assert ed.get_symbol("BTCUSDT") == "BTC_USDT"

    def test_get_symbol_lowercase(self):
        ed = PoloniexExchangeDataSpot()
        assert ed.get_symbol("btc_usdt") == "BTC_USDT"

    def test_get_period(self):
        ed = PoloniexExchangeDataSpot()
        assert ed.get_period("1m") == "MINUTE_1"
        assert ed.get_period("1h") == "HOUR_1"
        assert ed.get_period("1d") == "DAY_1"

    def test_kline_periods_complete(self):
        ed = PoloniexExchangeDataSpot()
        for k in ("1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"):
            assert k in ed.kline_periods

    def test_get_rest_path_valid(self):
        ed = PoloniexExchangeDataSpot()
        path = ed.get_rest_path("get_ticker")
        assert "GET" in path and "{symbol}" in path

    def test_get_rest_path_invalid_raises(self):
        ed = PoloniexExchangeDataSpot()
        with pytest.raises(ValueError, match="Unknown rest path"):
            ed.get_rest_path("nonexistent_endpoint")

    def test_account_wss_symbol(self):
        ed = PoloniexExchangeDataSpot()
        assert ed.account_wss_symbol("BTCUSDT") == "btc/usdt"

    def test_legal_currency(self):
        ed = PoloniexExchangeDataSpot()
        assert "USDT" in ed.legal_currency
        assert "BTC" in ed.legal_currency


# ── 2. Parameter Generation ──────────────────────────────────


class TestParameterGeneration:
    def test_ticker_params(self):
        feed = _make_feed()
        path, params, ed = feed._get_ticker("BTC/USDT")
        assert "GET" in path
        assert "BTC_USDT" in path
        assert ed["request_type"] == "get_ticker"
        assert ed["symbol_name"] == "BTC/USDT"

    def test_tick_is_alias(self):
        feed = _make_feed()
        path1, _, ed1 = feed._get_tick("BTC/USDT")
        path2, _, ed2 = feed._get_ticker("BTC/USDT")
        assert path1 == path2

    def test_depth_params(self):
        feed = _make_feed()
        path, params, ed = feed._get_depth("BTC/USDT", limit=20)
        assert params["limit"] == 20
        assert ed["request_type"] == "get_orderbook"

    def test_depth_limit_capped(self):
        feed = _make_feed()
        _, params, _ = feed._get_depth("BTC/USDT", limit=999)
        assert params["limit"] == 150

    def test_kline_params(self):
        feed = _make_feed()
        path, params, ed = feed._get_kline("BTC/USDT", period="1h", limit=100)
        assert params["interval"] == "HOUR_1"
        assert params["limit"] == 100
        assert "BTC_USDT" in path

    def test_kline_limit_capped(self):
        feed = _make_feed()
        _, params, _ = feed._get_kline("BTC/USDT", limit=9999)
        assert params["limit"] == 500

    def test_trades_params(self):
        feed = _make_feed()
        path, params, ed = feed._get_trades("BTC/USDT", limit=200)
        assert params["limit"] == 200
        assert "BTC_USDT" in path

    def test_trade_history_alias(self):
        feed = _make_feed()
        p1, _, _ = feed._get_trade_history("BTC/USDT")
        p2, _, _ = feed._get_trades("BTC/USDT")
        assert p1 == p2

    def test_server_time_params(self):
        feed = _make_feed()
        path, params, ed = feed._get_server_time()
        assert "GET" in path
        assert params == {}
        assert ed["request_type"] == "get_server_time"

    def test_exchange_info_params(self):
        feed = _make_feed()
        path, params, ed = feed._get_exchange_info()
        assert "GET" in path and "/markets" in path
        assert ed["request_type"] == "get_exchange_info"

    def test_make_order_limit_buy(self):
        feed = _make_feed()
        path, params, ed = feed._make_order("BTC/USDT", "1.0", "50000", order_type="buy-limit")
        assert params["side"] == "BUY"
        assert params["type"] == "LIMIT"
        assert params["quantity"] == "1.0"
        assert params["price"] == "50000"
        assert params["symbol"] == "BTC_USDT"

    def test_make_order_market_buy_with_amount(self):
        feed = _make_feed()
        _, params, _ = feed._make_order("BTC/USDT", "1.0", order_type="buy-market", amount="50000")
        assert params["type"] == "MARKET"
        assert params["amount"] == "50000"

    def test_make_order_post_only(self):
        feed = _make_feed()
        _, params, _ = feed._make_order("BTC/USDT", "1.0", "50000", order_type="buy-limit", post_only=True)
        assert params["timeInForce"] == "GTX"

    def test_cancel_order_by_id(self):
        feed = _make_feed()
        path, params, ed = feed._cancel_order(order_id="123456")
        assert "DELETE /orders/123456" == path
        assert params == {}

    def test_cancel_order_by_symbol(self):
        feed = _make_feed()
        path, params, ed = feed._cancel_order(symbol="BTC/USDT")
        assert "DELETE" in path
        assert params.get("symbol") == "BTC_USDT"

    def test_query_order(self):
        feed = _make_feed()
        path, params, ed = feed._query_order(symbol="BTC/USDT", order_id="123456")
        assert path == "GET /orders/123456"
        assert ed["request_type"] == "query_order"

    def test_get_open_orders_with_symbol(self):
        feed = _make_feed()
        path, params, ed = feed._get_open_orders(symbol="BTC/USDT")
        assert params["symbol"] == "BTC_USDT"

    def test_get_open_orders_all(self):
        feed = _make_feed()
        _, params, ed = feed._get_open_orders()
        assert params == {}
        assert ed["symbol_name"] == "ALL"

    def test_get_balance(self):
        feed = _make_feed()
        path, params, ed = feed._get_balance("USDT")
        assert ed["request_type"] == "get_balance"
        assert ed["symbol_name"] == "USDT"

    def test_get_account(self):
        feed = _make_feed()
        path, params, ed = feed._get_account()
        assert ed["request_type"] == "get_account"
        assert "GET" in path


# ── 3. Normalization Functions ────────────────────────────────


class TestNormalization:
    def test_ticker_dict(self):
        data, ok = PoloniexRequestDataSpot._get_ticker_normalize_function(SAMPLE_TICKER, {})
        assert ok is True
        assert len(data) == 1

    def test_ticker_none(self):
        data, ok = PoloniexRequestDataSpot._get_ticker_normalize_function(None, {})
        assert ok is False

    def test_ticker_error(self):
        data, ok = PoloniexRequestDataSpot._get_ticker_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_depth_dict(self):
        data, ok = PoloniexRequestDataSpot._get_depth_normalize_function(SAMPLE_ORDERBOOK, {})
        assert ok is True
        assert "bids" in data[0] and "asks" in data[0]

    def test_kline_list(self):
        data, ok = PoloniexRequestDataSpot._get_kline_normalize_function(SAMPLE_KLINES, {})
        assert ok is True
        assert len(data) == 2

    def test_kline_empty(self):
        data, ok = PoloniexRequestDataSpot._get_kline_normalize_function([], {})
        assert ok is False

    def test_trades_list(self):
        data, ok = PoloniexRequestDataSpot._get_trades_normalize_function(SAMPLE_TRADES, {})
        assert ok is True
        assert len(data) == 2

    def test_balance_list(self):
        data, ok = PoloniexRequestDataSpot._get_balance_normalize_function(SAMPLE_BALANCE, {})
        assert ok is True
        assert len(data) == 2

    def test_balance_dict_with_balances(self):
        data, ok = PoloniexRequestDataSpot._get_balance_normalize_function({"balances": SAMPLE_BALANCE}, {})
        assert ok is True
        assert len(data) == 2

    def test_make_order_dict(self):
        data, ok = PoloniexRequestDataSpot._make_order_normalize_function(SAMPLE_ORDER, {})
        assert ok is True
        assert data[0]["id"] == "123456"

    def test_make_order_error(self):
        data, ok = PoloniexRequestDataSpot._make_order_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_server_time(self):
        data, ok = PoloniexRequestDataSpot._get_server_time_normalize_function(SAMPLE_SERVER_TIME, {})
        assert ok is True

    def test_exchange_info_list(self):
        data, ok = PoloniexRequestDataSpot._get_exchange_info_normalize_function(SAMPLE_MARKETS, {})
        assert ok is True
        assert "symbols" in data[0]

    def test_account(self):
        data, ok = PoloniexRequestDataSpot._get_account_normalize_function(SAMPLE_BALANCE, {})
        assert ok is True

    def test_open_orders_list(self):
        data, ok = PoloniexRequestDataSpot._get_open_orders_normalize_function([SAMPLE_ORDER], {})
        assert ok is True
        assert len(data) == 1

    def test_cancel_order_dict(self):
        data, ok = PoloniexRequestDataSpot._cancel_order_normalize_function({"orderId": "123", "state": "CANCELED"}, {})
        assert ok is True

    def test_query_order_dict(self):
        data, ok = PoloniexRequestDataSpot._query_order_normalize_function(SAMPLE_ORDER, {})
        assert ok is True


# ── 4. Mocked Sync Calls ─────────────────────────────────────


class TestSyncCalls:
    def _call(self, method_name, return_json, *args, **kwargs):
        feed = _make_feed()
        with patch.object(feed, "http_request", return_value=return_json):
            method = getattr(feed, method_name)
            result = method(*args, **kwargs)
            assert isinstance(result, RequestData)
            return result

    def test_get_tick(self):
        self._call("get_tick", SAMPLE_TICKER, "BTC/USDT")

    def test_get_depth(self):
        self._call("get_depth", SAMPLE_ORDERBOOK, "BTC/USDT", count=10)

    def test_get_kline(self):
        self._call("get_kline", SAMPLE_KLINES, "BTC/USDT", period="1h", count=100)

    def test_get_trade_history(self):
        self._call("get_trade_history", SAMPLE_TRADES, "BTC/USDT")

    def test_get_server_time(self):
        self._call("get_server_time", SAMPLE_SERVER_TIME)

    def test_get_exchange_info(self):
        self._call("get_exchange_info", SAMPLE_MARKETS)

    def test_get_balance(self):
        self._call("get_balance", SAMPLE_BALANCE)

    def test_get_account(self):
        self._call("get_account", SAMPLE_BALANCE)

    def test_get_open_orders(self):
        self._call("get_open_orders", [SAMPLE_ORDER], symbol="BTC/USDT")

    def test_get_deals(self):
        self._call("get_deals", SAMPLE_TRADES, "BTC/USDT")


# ── 5. Auth / Signature ──────────────────────────────────────


class TestAuth:
    def test_signature_not_empty(self):
        feed = _make_feed()
        sig = feed.signature(1704067200000, "GET", "/orders", "test_secret")
        assert isinstance(sig, str) and len(sig) > 0

    def test_header_keys(self):
        feed = _make_feed()
        sig = feed.signature(1704067200000, "GET", "/orders", "test_secret")
        headers = feed.get_header("test_key", sig, 1704067200000)
        assert headers["key"] == "test_key"
        assert "signature" in headers
        assert "signTimestamp" in headers

    def test_private_endpoint_requires_keys(self):
        feed = _make_feed(public_key=None, private_key=None)
        with pytest.raises(ValueError, match="API keys required"):
            with patch.object(feed, "http_request"):
                feed.get_balance()


# ── 6. Registry ──────────────────────────────────────────────


class TestRegistry:
    def test_poloniex_spot_registered(self):
        from bt_api_py.registry import ExchangeRegistry
        import bt_api_py.exchange_registers.register_poloniex  # noqa: F401
        assert ExchangeRegistry.has_exchange("POLONIEX___SPOT")
        assert ExchangeRegistry._feed_classes["POLONIEX___SPOT"] is PoloniexRequestDataSpot

    def test_exchange_data_registered(self):
        from bt_api_py.registry import ExchangeRegistry
        assert ExchangeRegistry._exchange_data_classes["POLONIEX___SPOT"] is PoloniexExchangeDataSpot

    def test_balance_handler_registered(self):
        from bt_api_py.registry import ExchangeRegistry
        handler = ExchangeRegistry.get_balance_handler("POLONIEX___SPOT")
        assert callable(handler)


# ── 7. Method Existence ──────────────────────────────────────


class TestMethodExistence:
    METHODS = [
        "get_tick", "async_get_tick",
        "get_ticker", "async_get_ticker",
        "get_depth", "async_get_depth",
        "get_kline", "async_get_kline",
        "get_trade_history", "async_get_trade_history",
        "get_trades", "async_get_trades",
        "make_order", "async_make_order",
        "cancel_order", "async_cancel_order",
        "query_order", "async_query_order",
        "get_open_orders", "async_get_open_orders",
        "get_account", "async_get_account",
        "get_balance", "async_get_balance",
        "get_server_time", "async_get_server_time",
        "get_exchange_info", "async_get_exchange_info",
        "get_deals",
    ]

    @pytest.mark.parametrize("method_name", METHODS, ids=METHODS)
    def test_method_exists(self, method_name):
        feed = _make_feed()
        assert hasattr(feed, method_name) and callable(getattr(feed, method_name))


# ── 8. Feed Init ─────────────────────────────────────────────


class TestFeedInit:
    def test_default_exchange_name(self):
        feed = _make_feed()
        assert feed.exchange_name == "POLONIEX___SPOT"

    def test_default_asset_type(self):
        feed = _make_feed()
        assert feed.asset_type == "SPOT"

    def test_api_keys(self):
        feed = _make_feed(public_key="k1", private_key="s1")
        assert feed.api_key == "k1"
        assert feed.api_secret == "s1"

    def test_api_key_alias(self):
        q = queue.Queue()
        feed = PoloniexRequestDataSpot(q, api_key="k2", api_secret="s2")
        assert feed.api_key == "k2"
        assert feed.api_secret == "s2"

    def test_capabilities(self):
        from bt_api_py.feeds.capability import Capability
        caps = PoloniexRequestDataSpot._capabilities()
        assert Capability.GET_TICK in caps
        assert Capability.GET_DEPTH in caps
        assert Capability.GET_KLINE in caps
        assert Capability.MAKE_ORDER in caps

    def test_push_data_to_queue(self):
        q = queue.Queue()
        feed = _make_feed(data_queue=q)
        feed.push_data_to_queue("test_data")
        assert q.get_nowait() == "test_data"


# ── 9. Integration (skipped by default) ──────────────────────


class TestIntegration:
    @pytest.mark.skip(reason="Requires network")
    def test_live_get_tick(self):
        feed = _make_feed()
        result = feed.get_tick("BTC_USDT")
        assert isinstance(result, RequestData)

    @pytest.mark.skip(reason="Requires network")
    def test_live_get_depth(self):
        feed = _make_feed()
        result = feed.get_depth("BTC_USDT")
        assert isinstance(result, RequestData)

    @pytest.mark.skip(reason="Requires network")
    def test_live_get_kline(self):
        feed = _make_feed()
        result = feed.get_kline("BTC_USDT", period="1h")
        assert isinstance(result, RequestData)

    @pytest.mark.skip(reason="Requires network")
    def test_live_get_exchange_info(self):
        feed = _make_feed()
        result = feed.get_exchange_info()
        assert isinstance(result, RequestData)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
