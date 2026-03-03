"""
Test HitBTC exchange integration.

Run tests:
    pytest tests/feeds/test_hitbtc.py -v

Skip live tests (default):
    SKIP_LIVE=1 pytest tests/feeds/test_hitbtc.py -v
"""

import base64
import json
import os
import queue
from unittest.mock import MagicMock, patch

import pytest

from bt_api_py.containers.exchanges.hitbtc_exchange_data import (
    HitBtcExchangeData,
    HitBtcExchangeDataSpot,
    HitBtcSpotExchangeData,
)
from bt_api_py.containers.tickers.hitbtc_ticker import HitBtcRequestTickerData
from bt_api_py.feeds.live_hitbtc.request_base import HitBtcRequestData
from bt_api_py.feeds.live_hitbtc.spot import HitBtcSpotRequestData

# ── sample API responses ────────────────────────────────────────

SAMPLE_TICKER = {
    "symbol": "BTCUSDT",
    "last": "50000.00",
    "bid": "49999.00",
    "ask": "50001.00",
    "volume": "1234.56",
    "volumeQuote": "61728000",
    "high": "51000.00",
    "low": "49000.00",
    "open": "49500.00",
    "timestamp": 1704067200000,
}

SAMPLE_ORDERBOOK = {
    "ask": [{"price": "50001", "size": "1.0"}, {"price": "50002", "size": "2.0"}],
    "bid": [{"price": "49999", "size": "1.5"}, {"price": "49998", "size": "3.0"}],
}

SAMPLE_TRADES = [
    {"id": 1, "price": "50000", "qty": "0.01", "side": "buy", "timestamp": "2024-01-01T00:00:00.000Z"},
    {"id": 2, "price": "50001", "qty": "0.02", "side": "sell", "timestamp": "2024-01-01T00:00:01.000Z"},
]

SAMPLE_KLINES = [
    {"timestamp": "2024-01-01T00:00:00.000Z", "open": "49500", "close": "50000",
     "min": "49000", "max": "51000", "volume": "100", "volumeQuote": "5000000"},
    {"timestamp": "2024-01-01T01:00:00.000Z", "open": "50000", "close": "50500",
     "min": "49800", "max": "50800", "volume": "80", "volumeQuote": "4040000"},
]

SAMPLE_EXCHANGE_INFO = {
    "BTCUSDT": {"base_currency": "BTC", "quote_currency": "USDT", "status": "trading",
                "tick_size": "0.01", "quantity_increment": "0.00001"},
    "ETHUSDT": {"base_currency": "ETH", "quote_currency": "USDT", "status": "trading",
                "tick_size": "0.01", "quantity_increment": "0.0001"},
}

SAMPLE_BALANCE = [
    {"currency": "USDT", "available": "1000.50", "reserved": "100.00"},
    {"currency": "BTC", "available": "0.5", "reserved": "0.1"},
]

SAMPLE_ORDER = {
    "id": 123456, "client_order_id": "my_order_1", "symbol": "BTCUSDT",
    "side": "buy", "type": "limit", "price": "50000", "quantity": "0.001",
    "quantity_cumulative": "0", "status": "new", "time_in_force": "GTC",
    "created_at": "2024-01-01T00:00:00.000Z", "updated_at": "2024-01-01T00:00:00.000Z",
}

SAMPLE_OPEN_ORDERS = [SAMPLE_ORDER]

SAMPLE_ERROR = {"error": {"code": 20001, "message": "Insufficient funds", "description": "Not enough balance"}}


# ── helpers ─────────────────────────────────────────────────────

def _make_feed(**kwargs):
    """Create a HitBtcSpotRequestData with mocked queue."""
    q = queue.Queue()
    defaults = {"public_key": "test_api_key", "private_key": "test_secret_key"}
    defaults.update(kwargs)
    return HitBtcSpotRequestData(q, **defaults)


# ── 1. Exchange Data Container ──────────────────────────────────


class TestExchangeDataContainer:
    def test_spot_exchange_name(self):
        ed = HitBtcExchangeDataSpot()
        assert ed.exchange_name == "HITBTC___SPOT"

    def test_backward_compat_alias(self):
        assert HitBtcSpotExchangeData is HitBtcExchangeDataSpot

    def test_rest_url(self):
        ed = HitBtcExchangeDataSpot()
        assert ed.rest_url == "https://api.hitbtc.com/api/3"

    def test_wss_url(self):
        ed = HitBtcExchangeDataSpot()
        assert ed.wss_url == "wss://api.hitbtc.com/api/3/ws/public"

    def test_get_symbol_slash(self):
        ed = HitBtcExchangeDataSpot()
        assert ed.get_symbol("BTC/USDT") == "BTCUSDT"

    def test_get_symbol_dash(self):
        ed = HitBtcExchangeDataSpot()
        assert ed.get_symbol("BTC-USDT") == "BTCUSDT"

    def test_get_symbol_plain(self):
        ed = HitBtcExchangeDataSpot()
        assert ed.get_symbol("BTCUSDT") == "BTCUSDT"

    def test_get_period(self):
        ed = HitBtcExchangeDataSpot()
        assert ed.get_period("1m") == "M1"
        assert ed.get_period("5m") == "M5"
        assert ed.get_period("1h") == "H1"
        assert ed.get_period("4h") == "H4"
        assert ed.get_period("1d") == "D1"

    def test_rest_paths_present(self):
        ed = HitBtcExchangeDataSpot()
        required = [
            "get_server_time", "get_exchange_info", "get_tick", "get_depth",
            "get_trades", "get_kline", "make_order", "cancel_order",
            "get_open_orders", "query_order", "get_balance", "get_account",
        ]
        for key in required:
            assert key in ed.rest_paths, f"Missing REST path: {key}"

    def test_rest_paths_method_format(self):
        """Every REST path must start with 'METHOD /...'."""
        ed = HitBtcExchangeDataSpot()
        for key, path in ed.rest_paths.items():
            parts = path.split(" ", 1)
            assert len(parts) == 2, f"Path '{key}' has no METHOD prefix: {path}"
            assert parts[0] in ("GET", "POST", "PUT", "DELETE"), f"Invalid method in '{key}': {parts[0]}"

    def test_get_rest_path_raises_for_unknown(self):
        ed = HitBtcExchangeDataSpot()
        with pytest.raises(ValueError):
            ed.get_rest_path("nonexistent_key")

    def test_wss_paths_present(self):
        ed = HitBtcExchangeDataSpot()
        for key in ("ticker", "orderbook", "trades", "candles"):
            assert key in ed.wss_paths

    def test_legal_currency(self):
        ed = HitBtcExchangeDataSpot()
        assert "USDT" in ed.legal_currency

    def test_base_class_exchange_name(self):
        ed = HitBtcExchangeData()
        assert ed.exchange_name == "HITBTC"


# ── 2. Parameter Generation (_get_xxx) ─────────────────────────


class TestParameterGeneration:
    def setup_method(self):
        self.feed = _make_feed()

    def test_get_server_time_params(self):
        path, params, extra = self.feed._get_server_time()
        assert path.startswith("GET ")
        assert "/public/time" in path
        assert params == {}
        assert extra["request_type"] == "get_server_time"
        assert extra["exchange_name"] == "HITBTC___SPOT"
        assert callable(extra["normalize_function"])

    def test_get_exchange_info_params(self):
        path, params, extra = self.feed._get_exchange_info()
        assert "GET" in path
        assert "/public/symbol" in path
        assert extra["request_type"] == "get_exchange_info"

    def test_get_tick_params(self):
        path, params, extra = self.feed._get_tick("BTC/USDT")
        assert "GET" in path
        assert "/public/ticker/BTCUSDT" in path
        assert "{symbol}" not in path
        assert extra["symbol_name"] == "BTC/USDT"
        assert extra["request_type"] == "get_tick"

    def test_get_depth_params(self):
        path, params, extra = self.feed._get_depth("BTC/USDT", count=10)
        assert "/public/orderbook/BTCUSDT" in path
        assert params["depth"] == 10
        assert extra["request_type"] == "get_depth"

    def test_get_kline_params(self):
        path, params, extra = self.feed._get_kline("BTC/USDT", period="1h", count=50)
        assert "/public/candles/BTCUSDT" in path
        assert params["period"] == "H1"
        assert params["limit"] == 50
        assert extra["request_type"] == "get_kline"

    def test_get_kline_limit_capped(self):
        _, params, _ = self.feed._get_kline("BTC/USDT", count=5000)
        assert params["limit"] == 1000

    def test_get_trade_history_params(self):
        path, params, extra = self.feed._get_trade_history("BTC/USDT", count=20)
        assert "/public/trades/BTCUSDT" in path
        assert params["limit"] == 20
        assert extra["request_type"] == "get_trades"

    def test_make_order_params(self):
        path, body, extra = self.feed._make_order("BTC/USDT", 0.001, price=50000, order_type="buy-limit")
        assert path.startswith("POST ")
        assert "/spot/order" in path
        assert body["symbol"] == "BTCUSDT"
        assert body["side"] == "buy"
        assert body["type"] == "limit"
        assert body["quantity"] == "0.001"
        assert body["price"] == "50000"
        assert body["time_in_force"] == "GTC"

    def test_make_order_market(self):
        _, body, _ = self.feed._make_order("BTC/USDT", 0.01, order_type="sell-market")
        assert body["side"] == "sell"
        assert body["type"] == "market"
        assert "price" not in body

    def test_cancel_order_params(self):
        path, params, extra = self.feed._cancel_order(symbol="BTC/USDT", order_id="my_order_1")
        assert "DELETE" in path
        assert "/spot/order/my_order_1" in path
        assert extra["request_type"] == "cancel_order"

    def test_query_order_params(self):
        path, params, extra = self.feed._query_order(order_id="my_order_1")
        assert "GET" in path
        assert "/spot/order/my_order_1" in path
        assert extra["request_type"] == "query_order"

    def test_get_open_orders_params(self):
        path, params, extra = self.feed._get_open_orders(symbol="BTC/USDT")
        assert "GET" in path
        assert "/spot/order" in path
        assert params["symbol"] == "BTCUSDT"

    def test_get_open_orders_no_symbol(self):
        _, params, _ = self.feed._get_open_orders()
        assert params == {}

    def test_get_balance_params(self):
        path, params, extra = self.feed._get_balance()
        assert "/spot/balance" in path
        assert extra["request_type"] == "get_balance"

    def test_get_account_params(self):
        path, params, extra = self.feed._get_account()
        assert "/spot/balance" in path
        assert extra["request_type"] == "get_account"


# ── 3. Normalize Functions ──────────────────────────────────────


class TestNormalizeFunctions:
    def test_server_time_ok(self):
        data, ok = HitBtcRequestData._get_server_time_normalize_function(
            {"iso": "2024-01-01T00:00:00.000Z", "timestamp": 1704067200000}, {})
        assert ok is True
        assert len(data) == 1

    def test_server_time_none(self):
        data, ok = HitBtcRequestData._get_server_time_normalize_function(None, {})
        assert ok is False
        assert data == []

    def test_server_time_error(self):
        data, ok = HitBtcRequestData._get_server_time_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_exchange_info_ok(self):
        data, ok = HitBtcRequestData._get_exchange_info_normalize_function(SAMPLE_EXCHANGE_INFO, {})
        assert ok is True
        assert "symbols" in data[0]
        assert "BTCUSDT" in data[0]["symbols"]

    def test_exchange_info_error(self):
        data, ok = HitBtcRequestData._get_exchange_info_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_tick_ok(self):
        extra = {"symbol_name": "BTC/USDT", "asset_type": "SPOT"}
        data, ok = HitBtcRequestData._get_tick_normalize_function(SAMPLE_TICKER, extra)
        assert ok is True
        assert len(data) == 1
        ticker = data[0]
        assert isinstance(ticker, HitBtcRequestTickerData)
        assert ticker.get_symbol_name() == "BTC/USDT"
        assert ticker.get_last_price() == 50000.0

    def test_tick_error(self):
        data, ok = HitBtcRequestData._get_tick_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_tick_none(self):
        data, ok = HitBtcRequestData._get_tick_normalize_function(None, {})
        assert ok is False

    def test_depth_ok(self):
        data, ok = HitBtcRequestData._get_depth_normalize_function(SAMPLE_ORDERBOOK, {})
        assert ok is True
        assert "ask" in data[0]
        assert "bid" in data[0]

    def test_depth_error(self):
        data, ok = HitBtcRequestData._get_depth_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_kline_ok(self):
        data, ok = HitBtcRequestData._get_kline_normalize_function(SAMPLE_KLINES, {})
        assert ok is True
        assert len(data) == 2

    def test_kline_empty(self):
        data, ok = HitBtcRequestData._get_kline_normalize_function([], {})
        assert ok is False
        assert data == []

    def test_trade_history_ok(self):
        data, ok = HitBtcRequestData._get_trade_history_normalize_function(SAMPLE_TRADES, {})
        assert ok is True
        assert len(data) == 2

    def test_trade_history_error(self):
        data, ok = HitBtcRequestData._get_trade_history_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_make_order_ok(self):
        data, ok = HitBtcRequestData._make_order_normalize_function(SAMPLE_ORDER, {})
        assert ok is True
        assert data[0]["symbol"] == "BTCUSDT"

    def test_make_order_error(self):
        data, ok = HitBtcRequestData._make_order_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_cancel_order_ok(self):
        data, ok = HitBtcRequestData._cancel_order_normalize_function(SAMPLE_ORDER, {})
        assert ok is True

    def test_cancel_order_error(self):
        data, ok = HitBtcRequestData._cancel_order_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_query_order_ok(self):
        data, ok = HitBtcRequestData._query_order_normalize_function(SAMPLE_ORDER, {})
        assert ok is True

    def test_open_orders_ok(self):
        data, ok = HitBtcRequestData._get_open_orders_normalize_function(SAMPLE_OPEN_ORDERS, {})
        assert ok is True
        assert len(data) == 1

    def test_open_orders_empty_list(self):
        data, ok = HitBtcRequestData._get_open_orders_normalize_function([], {})
        assert ok is False

    def test_balance_ok(self):
        data, ok = HitBtcRequestData._get_balance_normalize_function(SAMPLE_BALANCE, {})
        assert ok is True
        assert len(data) == 2

    def test_balance_error(self):
        data, ok = HitBtcRequestData._get_balance_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_account_ok(self):
        data, ok = HitBtcRequestData._get_account_normalize_function(SAMPLE_BALANCE, {})
        assert ok is True
        assert len(data) == 2

    def test_is_error_helper(self):
        assert HitBtcRequestData._is_error(None) is True
        assert HitBtcRequestData._is_error(SAMPLE_ERROR) is True
        assert HitBtcRequestData._is_error(SAMPLE_TICKER) is False
        assert HitBtcRequestData._is_error(SAMPLE_TRADES) is False


# ── 4. Sync Calls with Mocked HTTP ─────────────────────────────


class TestSyncCallsMocked:
    """Test all sync wrappers with mocked http_request."""

    def setup_method(self):
        self.feed = _make_feed()

    def _mock_http(self, return_value):
        return patch.object(self.feed, "http_request", return_value=return_value)

    def test_get_server_time(self):
        with self._mock_http({"iso": "2024-01-01T00:00:00Z", "timestamp": 1704067200000}):
            rd = self.feed.get_server_time()
            assert rd is not None
            assert rd.extra_data["request_type"] == "get_server_time"

    def test_get_exchange_info(self):
        with self._mock_http(SAMPLE_EXCHANGE_INFO):
            rd = self.feed.get_exchange_info()
            assert rd is not None
            assert rd.extra_data["request_type"] == "get_exchange_info"

    def test_get_tick(self):
        with self._mock_http(SAMPLE_TICKER) as mock:
            rd = self.feed.get_tick("BTC/USDT")
            assert rd is not None
            assert rd.extra_data["symbol_name"] == "BTC/USDT"
            # Verify URL contains the symbol
            call_args = mock.call_args
            url = call_args[0][1]
            assert "/public/ticker/BTCUSDT" in url

    def test_get_depth(self):
        with self._mock_http(SAMPLE_ORDERBOOK) as mock:
            rd = self.feed.get_depth("ETH/USDT", count=5)
            url = mock.call_args[0][1]
            assert "/public/orderbook/ETHUSDT" in url
            assert "depth=5" in url

    def test_get_kline(self):
        with self._mock_http(SAMPLE_KLINES) as mock:
            rd = self.feed.get_kline("BTC/USDT", period="1h", count=24)
            url = mock.call_args[0][1]
            assert "/public/candles/BTCUSDT" in url
            assert "period=H1" in url
            assert "limit=24" in url

    def test_get_trade_history(self):
        with self._mock_http(SAMPLE_TRADES) as mock:
            rd = self.feed.get_trade_history("BTC/USDT", count=50)
            url = mock.call_args[0][1]
            assert "/public/trades/BTCUSDT" in url
            assert "limit=50" in url

    def test_make_order(self):
        with self._mock_http(SAMPLE_ORDER) as mock:
            rd = self.feed.make_order("BTC/USDT", 0.001, price=50000, order_type="buy-limit")
            # Should be POST with Authorization header
            method = mock.call_args[0][0]
            assert method == "POST"
            headers = mock.call_args[0][2]
            assert "Authorization" in headers
            assert headers["Authorization"].startswith("Basic ")

    def test_cancel_order(self):
        with self._mock_http(SAMPLE_ORDER) as mock:
            rd = self.feed.cancel_order(order_id="my_order_1")
            method = mock.call_args[0][0]
            url = mock.call_args[0][1]
            assert method == "DELETE"
            assert "/spot/order/my_order_1" in url

    def test_query_order(self):
        with self._mock_http(SAMPLE_ORDER) as mock:
            rd = self.feed.query_order(order_id="my_order_1")
            url = mock.call_args[0][1]
            assert "/spot/order/my_order_1" in url

    def test_get_open_orders(self):
        with self._mock_http(SAMPLE_OPEN_ORDERS) as mock:
            rd = self.feed.get_open_orders(symbol="BTC/USDT")
            url = mock.call_args[0][1]
            assert "/spot/order" in url
            assert "symbol=BTCUSDT" in url

    def test_get_balance(self):
        with self._mock_http(SAMPLE_BALANCE) as mock:
            rd = self.feed.get_balance()
            url = mock.call_args[0][1]
            assert "/spot/balance" in url
            headers = mock.call_args[0][2]
            assert "Authorization" in headers

    def test_get_account(self):
        with self._mock_http(SAMPLE_BALANCE) as mock:
            rd = self.feed.get_account()
            assert rd is not None


# ── 5. Basic Auth ───────────────────────────────────────────────


class TestBasicAuth:
    def test_auth_header_format(self):
        feed = _make_feed(public_key="mykey", private_key="mysecret")
        header = feed._basic_auth_header()
        assert header.startswith("Basic ")
        decoded = base64.b64decode(header.split(" ", 1)[1]).decode("ascii")
        assert decoded == "mykey:mysecret"

    def test_public_no_auth(self):
        feed = _make_feed()
        with patch.object(feed, "http_request", return_value=SAMPLE_TICKER) as mock:
            feed.get_tick("BTC/USDT")
            headers = mock.call_args[0][2]
            assert "Authorization" not in headers

    def test_private_has_auth(self):
        feed = _make_feed()
        with patch.object(feed, "http_request", return_value=SAMPLE_BALANCE) as mock:
            feed.get_balance()
            headers = mock.call_args[0][2]
            assert "Authorization" in headers

    def test_no_auth_when_keys_empty(self):
        feed = _make_feed(public_key="", private_key="")
        with patch.object(feed, "http_request", return_value=SAMPLE_BALANCE) as mock:
            feed.get_balance()
            headers = mock.call_args[0][2]
            assert "Authorization" not in headers


# ── 6. Ticker Container ────────────────────────────────────────


class TestTickerContainer:
    def test_init_data(self):
        ticker = HitBtcRequestTickerData(SAMPLE_TICKER, "BTC/USDT", "SPOT", True)
        ticker.init_data()
        assert ticker.get_exchange_name() == "HITBTC"
        assert ticker.get_symbol_name() == "BTC/USDT"
        assert ticker.get_last_price() == 50000.0
        assert ticker.get_bid_price() == 49999.0
        assert ticker.get_ask_price() == 50001.0

    def test_idempotent_init(self):
        ticker = HitBtcRequestTickerData(SAMPLE_TICKER, "BTC/USDT", "SPOT", True)
        ticker.init_data()
        ticker.init_data()
        assert ticker.get_last_price() == 50000.0

    def test_str_repr(self):
        ticker = HitBtcRequestTickerData(SAMPLE_TICKER, "BTC/USDT", "SPOT", True)
        ticker.init_data()
        assert "BTC/USDT" in str(ticker)
        assert "HitBtc" in repr(ticker)

    def test_get_all_data(self):
        ticker = HitBtcRequestTickerData(SAMPLE_TICKER, "BTC/USDT", "SPOT", True)
        ticker.init_data()
        all_data = ticker.get_all_data()
        assert isinstance(all_data, dict)
        assert all_data["exchange_name"] == "HITBTC"
        assert all_data["symbol_name"] == "BTC/USDT"


# ── 7. Registry ────────────────────────────────────────────────


class TestRegistry:
    def test_hitbtc_spot_registered(self):
        from bt_api_py.registry import ExchangeRegistry
        import bt_api_py.feeds.register_hitbtc  # noqa: F401
        assert ExchangeRegistry.has_exchange("HITBTC___SPOT")
        assert ExchangeRegistry._feed_classes["HITBTC___SPOT"] is HitBtcSpotRequestData

    def test_exchange_data_registered(self):
        from bt_api_py.registry import ExchangeRegistry
        assert ExchangeRegistry._exchange_data_classes["HITBTC___SPOT"] is HitBtcExchangeDataSpot

    def test_balance_handler_registered(self):
        from bt_api_py.registry import ExchangeRegistry
        handler = ExchangeRegistry.get_balance_handler("HITBTC___SPOT")
        assert callable(handler)


# ── 8. Method Existence ────────────────────────────────────────


class TestMethodExistence:
    """Verify HitBtcSpotRequestData exposes all required public methods."""

    @pytest.mark.parametrize("method_name", [
        "get_server_time", "async_get_server_time",
        "get_exchange_info", "async_get_exchange_info",
        "get_tick", "async_get_tick",
        "get_depth", "async_get_depth",
        "get_kline", "async_get_kline",
        "get_trade_history", "async_get_trade_history",
        "make_order", "async_make_order",
        "cancel_order", "async_cancel_order",
        "query_order", "async_query_order",
        "get_open_orders", "async_get_open_orders",
        "get_account", "async_get_account",
        "get_balance", "async_get_balance",
    ])
    def test_method_exists(self, method_name):
        feed = _make_feed()
        assert hasattr(feed, method_name)
        assert callable(getattr(feed, method_name))


# ── 9. Feed Init ───────────────────────────────────────────────


class TestFeedInit:
    def test_default_exchange_name(self):
        feed = _make_feed()
        assert feed.exchange_name == "HITBTC___SPOT"

    def test_default_asset_type(self):
        feed = _make_feed()
        assert feed.asset_type == "SPOT"

    def test_api_keys(self):
        feed = _make_feed(public_key="k1", private_key="s1")
        assert feed.api_key == "k1"
        assert feed.api_secret == "s1"

    def test_api_key_alias(self):
        q = queue.Queue()
        feed = HitBtcSpotRequestData(q, api_key="k2", api_secret="s2")
        assert feed.api_key == "k2"
        assert feed.api_secret == "s2"

    def test_capabilities(self):
        from bt_api_py.feeds.capability import Capability
        caps = HitBtcSpotRequestData._capabilities()
        assert Capability.GET_TICK in caps
        assert Capability.GET_DEPTH in caps
        assert Capability.GET_KLINE in caps
        assert Capability.MAKE_ORDER in caps
        assert Capability.CANCEL_ORDER in caps
        assert Capability.GET_BALANCE in caps

    def test_push_data_to_queue(self):
        feed = _make_feed()
        feed.push_data_to_queue("test_data")
        assert feed.data_queue.get_nowait() == "test_data"


# ── 10. Integration Tests (skipped by default) ─────────────────

SKIP_LIVE = os.environ.get("SKIP_LIVE", "1") == "1"


@pytest.mark.skipif(SKIP_LIVE, reason="Live HitBTC tests disabled (set SKIP_LIVE=0)")
class TestIntegration:
    def setup_method(self):
        self.feed = _make_feed()

    def test_live_get_tick(self):
        rd = self.feed.get_tick("BTCUSDT")
        assert rd is not None

    def test_live_get_depth(self):
        rd = self.feed.get_depth("BTCUSDT", count=5)
        assert rd is not None

    def test_live_get_kline(self):
        rd = self.feed.get_kline("BTCUSDT", period="1h", count=5)
        assert rd is not None

    def test_live_get_exchange_info(self):
        rd = self.feed.get_exchange_info()
        assert rd is not None

    def test_live_get_trade_history(self):
        rd = self.feed.get_trade_history("BTCUSDT", count=5)
        assert rd is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
