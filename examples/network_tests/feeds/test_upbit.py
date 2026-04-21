"""
Tests for Upbit exchange – Feed pattern.

Run:  pytest tests/feeds/test_upbit.py -v
"""

import queue
from unittest.mock import patch

import pytest

import bt_api_py.exchange_registers.register_upbit  # noqa: F401
from bt_api_py.containers.exchanges.upbit_exchange_data import UpbitExchangeDataSpot
from bt_api_base.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_upbit.request_base import UpbitRequestData
from bt_api_py.feeds.live_upbit.spot import UpbitRequestDataSpot
from bt_api_py.registry import ExchangeRegistry

# ── sample response fixtures ──────────────────────────────────

SAMPLE_TICK = [{"market": "KRW-BTC", "trade_price": 50000000, "timestamp": 1700000000000}]

SAMPLE_DEPTH = [
    {
        "market": "KRW-BTC",
        "orderbook_units": [
            {"ask_price": 50010000, "bid_price": 49990000, "ask_size": 1.0, "bid_size": 0.5}
        ],
        "total_ask_size": 10.0,
        "total_bid_size": 5.0,
    }
]

SAMPLE_KLINE = [
    {
        "market": "KRW-BTC",
        "candle_date_time_utc": "2024-01-01T00:00:00",
        "opening_price": 49000000,
        "high_price": 51000000,
        "low_price": 48000000,
        "trade_price": 50000000,
        "candle_acc_trade_volume": 100,
    },
    {
        "market": "KRW-BTC",
        "candle_date_time_utc": "2024-01-01T01:00:00",
        "opening_price": 50000000,
        "high_price": 52000000,
        "low_price": 49000000,
        "trade_price": 51000000,
        "candle_acc_trade_volume": 120,
    },
]

SAMPLE_TRADES = [
    {
        "market": "KRW-BTC",
        "trade_price": 50000000,
        "trade_volume": 0.01,
        "ask_bid": "BID",
        "timestamp": 1700000000000,
    }
]

SAMPLE_EXCHANGE_INFO = [
    {"market": "KRW-BTC", "korean_name": "비트코인", "english_name": "Bitcoin"},
    {"market": "KRW-ETH", "korean_name": "이더리움", "english_name": "Ethereum"},
]

SAMPLE_ACCOUNT = [
    {"currency": "BTC", "balance": "1.5", "locked": "0.0", "avg_buy_price": "49000000"},
    {"currency": "KRW", "balance": "10000000", "locked": "0.0", "avg_buy_price": "0"},
]

SAMPLE_ORDER = {
    "uuid": "abc-123",
    "side": "bid",
    "ord_type": "limit",
    "price": "50000000",
    "state": "done",
    "market": "KRW-BTC",
    "volume": "0.01",
}

SAMPLE_ERROR = {"error": {"name": "invalid_parameter", "message": "invalid market"}}


# ── helpers ───────────────────────────────────────────────────


@pytest.fixture
def feed():
    q = queue.Queue()
    return UpbitRequestDataSpot(q)


@pytest.fixture
def exdata():
    return UpbitExchangeDataSpot()


# ═══════════════════════════════════════════════════════════════
# 1) ExchangeData
# ═══════════════════════════════════════════════════════════════


class TestExchangeData:
    def test_exchange_name(self, exdata):
        assert exdata.exchange_name == "UPBIT___SPOT"

    def test_rest_url(self, exdata):
        assert "api.upbit.com" in exdata.rest_url

    def test_wss_url(self, exdata):
        assert "api.upbit.com" in exdata.wss_url

    def test_get_symbol_passthrough(self, exdata):
        assert exdata.get_symbol("KRW-BTC") == "KRW-BTC"
        assert exdata.get_symbol("USDT-ETH") == "USDT-ETH"

    def test_get_period(self, exdata):
        assert exdata.get_period("1m") == "1"
        assert exdata.get_period("1h") == "60"
        assert exdata.get_period("1d") == "D"
        assert exdata.get_period("1w") == "W"
        assert exdata.get_period("1M") == "M"

    @pytest.mark.kline
    def test_kline_periods(self, exdata):
        for k in ("1m", "5m", "1h", "4h", "1d", "1w", "1M"):
            assert k in exdata.kline_periods

    def test_legal_currency(self, exdata):
        for c in ("KRW", "USDT", "BTC", "ETH"):
            assert c in exdata.legal_currency

    def test_get_rest_path_ok(self, exdata):
        p = exdata.get_rest_path("get_tick")
        assert "/v1/ticker" in p

    def test_get_rest_path_missing(self, exdata):
        with pytest.raises(ValueError):
            exdata.get_rest_path("nonexistent_endpoint")

    def test_rest_paths_keys(self, exdata):
        for key in (
            "get_tick",
            "get_depth",
            "get_exchange_info",
            "get_trades",
            "get_kline_minutes",
            "get_kline_days",
            "get_account",
            "make_order",
            "cancel_order",
            "query_order",
            "get_open_orders",
        ):
            assert key in exdata.rest_paths


# ═══════════════════════════════════════════════════════════════
# 2) Parameter generation (_get_xxx)
# ═══════════════════════════════════════════════════════════════


class TestParamGeneration:
    @pytest.mark.ticker
    def test_get_tick_params(self, feed):
        path, params, extra = feed._get_tick("KRW-BTC")
        assert "GET" in path
        assert params["markets"] == "KRW-BTC"
        assert extra["request_type"] == "get_tick"

    @pytest.mark.orderbook
    def test_get_depth_params(self, feed):
        path, params, extra = feed._get_depth("KRW-BTC")
        assert "GET" in path
        assert params["markets"] == "KRW-BTC"

    @pytest.mark.kline
    def test_get_kline_minutes(self, feed):
        path, params, extra = feed._get_kline("KRW-BTC", "1h", 100)
        assert "GET" in path
        assert "/candles/minutes/60" in path
        assert params["market"] == "KRW-BTC"
        assert params["count"] == 100

    @pytest.mark.kline
    def test_get_kline_days(self, feed):
        path, params, extra = feed._get_kline("KRW-BTC", "1d", 50)
        assert "/candles/days" in path

    @pytest.mark.kline
    def test_get_kline_weeks(self, feed):
        path, params, extra = feed._get_kline("KRW-BTC", "1w", 10)
        assert "/candles/weeks" in path

    @pytest.mark.kline
    def test_get_kline_months(self, feed):
        path, params, extra = feed._get_kline("KRW-BTC", "1M", 10)
        assert "/candles/months" in path

    def test_get_trade_history_params(self, feed):
        path, params, extra = feed._get_trade_history("KRW-BTC", count=20)
        assert params["market"] == "KRW-BTC"
        assert params["count"] == 20

    def test_get_exchange_info_params(self, feed):
        path, params, extra = feed._get_exchange_info()
        assert "GET" in path
        assert params["isDetails"] == "true"

    def test_make_order_params(self, feed):
        path, body, extra = feed._make_order(
            "KRW-BTC", 0.01, price=50000000, order_type="bid-limit"
        )
        assert "POST" in path
        assert body["market"] == "KRW-BTC"
        assert body["side"] == "bid"
        assert body["ord_type"] == "limit"
        assert body["volume"] == "0.01"

    def test_cancel_order_params(self, feed):
        path, params, extra = feed._cancel_order(order_id="abc-123")
        assert "DELETE" in path
        assert params["uuid"] == "abc-123"

    def test_query_order_params(self, feed):
        path, params, extra = feed._query_order(order_id="abc-123")
        assert "GET" in path
        assert params["uuid"] == "abc-123"

    def test_get_open_orders_params(self, feed):
        path, params, extra = feed._get_open_orders(symbol="KRW-BTC")
        assert params["state"] == "wait"
        assert params["market"] == "KRW-BTC"

    def test_get_deals_params(self, feed):
        path, params, extra = feed._get_deals(symbol="KRW-BTC")
        assert params["state"] == "done"

    def test_get_account_params(self, feed):
        path, params, extra = feed._get_account()
        assert "GET" in path
        assert extra["request_type"] == "get_account"

    def test_get_balance_params(self, feed):
        path, params, extra = feed._get_balance()
        assert "GET" in path
        assert extra["request_type"] == "get_balance"


# ═══════════════════════════════════════════════════════════════
# 3) Normalization functions
# ═══════════════════════════════════════════════════════════════


class TestNormalization:
    @pytest.mark.ticker
    def test_tick_ok(self):
        result, ok = UpbitRequestData._get_tick_normalize_function(SAMPLE_TICK, {})
        assert ok is True
        assert result[0]["market"] == "KRW-BTC"

    @pytest.mark.ticker
    def test_tick_error(self):
        result, ok = UpbitRequestData._get_tick_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    @pytest.mark.ticker
    def test_tick_empty(self):
        result, ok = UpbitRequestData._get_tick_normalize_function([], {})
        assert ok is False

    @pytest.mark.orderbook
    def test_depth_ok(self):
        result, ok = UpbitRequestData._get_depth_normalize_function(SAMPLE_DEPTH, {})
        assert ok is True
        assert "orderbook_units" in result[0]

    @pytest.mark.orderbook
    def test_depth_error(self):
        result, ok = UpbitRequestData._get_depth_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    @pytest.mark.kline
    def test_kline_ok(self):
        result, ok = UpbitRequestData._get_kline_normalize_function(SAMPLE_KLINE, {})
        assert ok is True
        assert len(result) == 2

    @pytest.mark.kline
    def test_kline_empty(self):
        result, ok = UpbitRequestData._get_kline_normalize_function([], {})
        assert ok is False

    def test_trades_ok(self):
        result, ok = UpbitRequestData._get_trade_history_normalize_function(SAMPLE_TRADES, {})
        assert ok is True

    def test_trades_empty(self):
        result, ok = UpbitRequestData._get_trade_history_normalize_function([], {})
        assert ok is False

    def test_exchange_info_ok(self):
        result, ok = UpbitRequestData._get_exchange_info_normalize_function(
            SAMPLE_EXCHANGE_INFO, {}
        )
        assert ok is True
        assert "markets" in result[0]

    def test_exchange_info_error(self):
        result, ok = UpbitRequestData._get_exchange_info_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_make_order_ok(self):
        result, ok = UpbitRequestData._make_order_normalize_function(SAMPLE_ORDER, {})
        assert ok is True
        assert result[0]["uuid"] == "abc-123"

    def test_make_order_error(self):
        result, ok = UpbitRequestData._make_order_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_cancel_order_ok(self):
        result, ok = UpbitRequestData._cancel_order_normalize_function(SAMPLE_ORDER, {})
        assert ok is True

    def test_query_order_ok(self):
        result, ok = UpbitRequestData._query_order_normalize_function(SAMPLE_ORDER, {})
        assert ok is True

    def test_query_order_error(self):
        result, ok = UpbitRequestData._query_order_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_open_orders_ok(self):
        result, ok = UpbitRequestData._get_open_orders_normalize_function([SAMPLE_ORDER], {})
        assert ok is True
        assert len(result) == 1

    def test_open_orders_empty(self):
        result, ok = UpbitRequestData._get_open_orders_normalize_function([], {})
        assert ok is True

    def test_open_orders_error(self):
        result, ok = UpbitRequestData._get_open_orders_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_deals_ok(self):
        result, ok = UpbitRequestData._get_deals_normalize_function([SAMPLE_ORDER], {})
        assert ok is True

    def test_deals_empty(self):
        result, ok = UpbitRequestData._get_deals_normalize_function([], {})
        assert ok is True

    def test_account_ok(self):
        result, ok = UpbitRequestData._get_account_normalize_function(SAMPLE_ACCOUNT, {})
        assert ok is True
        assert len(result) == 2

    def test_account_error(self):
        result, ok = UpbitRequestData._get_account_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_balance_ok(self):
        result, ok = UpbitRequestData._get_balance_normalize_function(SAMPLE_ACCOUNT, {})
        assert ok is True

    def test_balance_error(self):
        result, ok = UpbitRequestData._get_balance_normalize_function(SAMPLE_ERROR, {})
        assert ok is False

    def test_is_error_none(self):
        assert UpbitRequestData._is_error(None) is True

    def test_is_error_dict_with_error(self):
        assert UpbitRequestData._is_error(SAMPLE_ERROR) is True

    def test_is_error_ok(self):
        assert UpbitRequestData._is_error(SAMPLE_TICK) is False


# ═══════════════════════════════════════════════════════════════
# 4) Mocked sync calls
# ═══════════════════════════════════════════════════════════════


class TestSyncCalls:
    @patch.object(UpbitRequestData, "http_request", return_value=SAMPLE_TICK)
    @pytest.mark.ticker
    def test_get_tick(self, mock_http, feed):
        rd = feed.get_tick("KRW-BTC")
        assert isinstance(rd, RequestData)
        mock_http.assert_called_once()

    @patch.object(UpbitRequestData, "http_request", return_value=SAMPLE_DEPTH)
    @pytest.mark.orderbook
    def test_get_depth(self, mock_http, feed):
        rd = feed.get_depth("KRW-BTC")
        assert isinstance(rd, RequestData)

    @patch.object(UpbitRequestData, "http_request", return_value=SAMPLE_KLINE)
    @pytest.mark.kline
    def test_get_kline(self, mock_http, feed):
        rd = feed.get_kline("KRW-BTC", "1h", 10)
        assert isinstance(rd, RequestData)

    @patch.object(UpbitRequestData, "http_request", return_value=SAMPLE_TRADES)
    def test_get_trade_history(self, mock_http, feed):
        rd = feed.get_trade_history("KRW-BTC", count=5)
        assert isinstance(rd, RequestData)

    @patch.object(UpbitRequestData, "http_request", return_value=SAMPLE_EXCHANGE_INFO)
    def test_get_exchange_info(self, mock_http, feed):
        rd = feed.get_exchange_info()
        assert isinstance(rd, RequestData)

    @patch.object(UpbitRequestData, "http_request", return_value=SAMPLE_ACCOUNT)
    def test_get_account(self, mock_http, feed):
        rd = feed.get_account()
        assert isinstance(rd, RequestData)

    @patch.object(UpbitRequestData, "http_request", return_value=[SAMPLE_ORDER])
    def test_get_open_orders(self, mock_http, feed):
        rd = feed.get_open_orders()
        assert isinstance(rd, RequestData)

    @patch.object(UpbitRequestData, "http_request", return_value=SAMPLE_ORDER)
    def test_make_order(self, mock_http, feed):
        rd = feed.make_order("KRW-BTC", 0.01, price=50000000)
        assert isinstance(rd, RequestData)

    @patch.object(UpbitRequestData, "http_request", return_value=SAMPLE_ORDER)
    def test_cancel_order(self, mock_http, feed):
        rd = feed.cancel_order(order_id="abc-123")
        assert isinstance(rd, RequestData)

    @patch.object(UpbitRequestData, "http_request", return_value=SAMPLE_ORDER)
    def test_query_order(self, mock_http, feed):
        rd = feed.query_order(order_id="abc-123")
        assert isinstance(rd, RequestData)


# ═══════════════════════════════════════════════════════════════
# 5) Auth
# ═══════════════════════════════════════════════════════════════


class TestAuth:
    def test_no_auth_without_keys(self):
        q = queue.Queue()
        feed = UpbitRequestDataSpot(q)
        headers = feed._generate_auth_headers()
        assert headers == {}

    def test_api_key_property(self, feed):
        assert feed.api_key == ""
        assert feed.api_secret == ""

    def test_auth_with_keys(self):
        q = queue.Queue()
        try:
            import jwt  # noqa: F401  # availability check

            feed = UpbitRequestDataSpot(q, access_key="mykey", secret_key="mysecret")
            headers = feed._generate_auth_headers()
            assert "Authorization" in headers
            assert headers["Authorization"].startswith("Bearer ")
        except ImportError:
            pytest.skip("PyJWT not installed")

    def test_jwt_token_with_params(self):
        q = queue.Queue()
        try:
            import jwt  # availability check

            feed = UpbitRequestDataSpot(q, access_key="mykey", secret_key="mysecret")
            token = feed._generate_jwt_token({"market": "KRW-BTC"})
            assert token is not None
            decoded = jwt.decode(token, "mysecret", algorithms=["HS256"])
            assert decoded["access_key"] == "mykey"
            assert "query_hash" in decoded
        except ImportError:
            pytest.skip("PyJWT not installed")


# ═══════════════════════════════════════════════════════════════
# 6) Registry
# ═══════════════════════════════════════════════════════════════


class TestRegistry:
    def test_upbit_spot_registered(self):
        assert "UPBIT___SPOT" in ExchangeRegistry._feed_classes

    def test_exchange_data_registered(self):
        assert "UPBIT___SPOT" in ExchangeRegistry._exchange_data_classes

    def test_balance_handler_registered(self):
        assert "UPBIT___SPOT" in ExchangeRegistry._balance_handlers

    def test_create_exchange_data(self):
        ed = ExchangeRegistry.create_exchange_data("UPBIT___SPOT")
        assert isinstance(ed, UpbitExchangeDataSpot)


# ═══════════════════════════════════════════════════════════════
# 7) Method existence
# ═══════════════════════════════════════════════════════════════


_EXPECTED_METHODS = [
    "get_tick",
    "async_get_tick",
    "get_ticker",
    "async_get_ticker",
    "get_depth",
    "async_get_depth",
    "get_kline",
    "async_get_kline",
    "get_trade_history",
    "async_get_trade_history",
    "get_trades",
    "async_get_trades",
    "make_order",
    "async_make_order",
    "cancel_order",
    "async_cancel_order",
    "query_order",
    "async_query_order",
    "get_open_orders",
    "async_get_open_orders",
    "get_deals",
    "async_get_deals",
    "get_account",
    "async_get_account",
    "get_balance",
    "async_get_balance",
    "get_exchange_info",
    "async_get_exchange_info",
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
        assert feed.exchange_name == "UPBIT___SPOT"

    def test_default_asset_type(self, feed):
        assert feed.asset_type == "SPOT"

    def test_api_keys(self):
        q = queue.Queue()
        feed = UpbitRequestDataSpot(q, access_key="ak", secret_key="sk")
        assert feed.api_key == "ak"
        assert feed.api_secret == "sk"

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
    @pytest.mark.skip(reason="Requires network access")
    @pytest.mark.ticker
    def test_live_get_tick(self):
        q = queue.Queue()
        feed = UpbitRequestDataSpot(q)
        rd = feed.get_tick("KRW-BTC")
        assert isinstance(rd, RequestData)

    @pytest.mark.skip(reason="Requires network access")
    @pytest.mark.orderbook
    def test_live_get_depth(self):
        q = queue.Queue()
        feed = UpbitRequestDataSpot(q)
        rd = feed.get_depth("KRW-BTC")
        assert isinstance(rd, RequestData)

    @pytest.mark.skip(reason="Requires network access")
    @pytest.mark.kline
    def test_live_get_kline(self):
        q = queue.Queue()
        feed = UpbitRequestDataSpot(q)
        rd = feed.get_kline("KRW-BTC", "1h", 5)
        assert isinstance(rd, RequestData)

    @pytest.mark.skip(reason="Requires network access")
    def test_live_get_exchange_info(self):
        q = queue.Queue()
        feed = UpbitRequestDataSpot(q)
        rd = feed.get_exchange_info()
        assert isinstance(rd, RequestData)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
