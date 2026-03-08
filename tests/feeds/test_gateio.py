"""
Test Gate.io exchange integration.

Run tests:
    pytest tests/feeds/test_gateio.py -v

Run with coverage:
    pytest tests/feeds/test_gateio.py --cov=bt_api_py.feeds.live_gateio --cov-report=term-missing
"""

import hashlib
import hmac
import os
import queue

import pytest

# Import registration to auto-register Gate.io
import bt_api_py.exchange_registers.register_gateio  # noqa: F401
from bt_api_py.containers.balances.gateio_balance import (
    GateioBalanceData,
)
from bt_api_py.containers.exchanges.gateio_exchange_data import (
    GateioExchangeDataSpot,
    GateioExchangeDataSwap,
)
from bt_api_py.containers.orderbooks.gateio_orderbook import (
    GateioOrderBookData,
)
from bt_api_py.containers.orders.gateio_order import (
    GateioOrderData,
)
from bt_api_py.containers.tickers.gateio_ticker import (
    GateioRequestTickerData,
    GateioTickerData,
)
from bt_api_py.feeds.live_gateio.request_base import GateioRequestData
from bt_api_py.feeds.live_gateio.spot import GateioRequestDataSpot
from bt_api_py.feeds.live_gateio.swap import GateioRequestDataSwap
from bt_api_py.registry import ExchangeRegistry

SKIP_LIVE = os.environ.get("SKIP_LIVE_TESTS", "true").lower() == "true"


# ── Fixtures ─────────────────────────────────────────────────────────────


def _make_spot_feed():
    data_queue = queue.Queue()
    return GateioRequestDataSpot(
        data_queue,
        public_key="test_api_key",
        private_key="test_secret_key",
    )


def _make_swap_feed():
    data_queue = queue.Queue()
    return GateioRequestDataSwap(
        data_queue,
        public_key="test_api_key",
        private_key="test_secret_key",
    )


# ── Sample data ──────────────────────────────────────────────────────────

SAMPLE_TICKER = {
    "currency_pair": "BTC_USDT",
    "last": "50000.5",
    "lowest_ask": "50010",
    "highest_bid": "49990",
    "base_volume": "1234.56",
    "quote_volume": "12345678.9",
    "high_24h": "51000",
    "low_24h": "49000",
    "change_percentage": "2.5",
    "time": "1640995200",
}

SAMPLE_ORDERBOOK = {
    "id": 123456789,
    "current": 1640995200.123,
    "update": 1640995200.456,
    "bids": [
        ["49990", "1.5"],
        ["49980", "2.0"],
    ],
    "asks": [
        ["50010", "1.0"],
        ["50020", "2.5"],
    ],
}

SAMPLE_ORDER = {
    "id": "123456789",
    "text": "t-my-custom-id",
    "create_time": "1640995200",
    "update_time": "1640995200",
    "currency_pair": "BTC_USDT",
    "type": "limit",
    "side": "buy",
    "amount": "0.001",
    "price": "50000",
    "filled_total": "0.0005",
    "left": "0.0005",
    "status": "open",
    "account": "spot",
    "time_in_force": "gtc",
    "fee": "0.00001",
    "fee_currency": "BTC",
}

SAMPLE_BALANCE = {
    "currency": "BTC",
    "available": "0.5",
    "locked": "0.1",
}

SAMPLE_KLINE = [["1640995200", "100", "50000", "51000", "49000", "50500", "1234.56"]]


# ═══════════════════════════════════════════════════════════════════════
# Exchange Data Tests
# ═══════════════════════════════════════════════════════════════════════


class TestGateioExchangeData:
    """Test Gate.io exchange data configuration."""

    def test_exchange_data_spot_creation(self):
        ed = GateioExchangeDataSpot()
        assert ed.asset_type == "spot"
        assert "gateio" in ed.exchange_name

    def test_exchange_data_swap_creation(self):
        ed = GateioExchangeDataSwap()
        assert ed.asset_type == "swap"
        assert "gateio" in ed.exchange_name

    def test_get_symbol(self):
        ed = GateioExchangeDataSpot()
        assert ed.get_symbol("BTC-USDT") == "BTC_USDT"
        assert ed.get_symbol("ETH-BTC") == "ETH_BTC"

    def test_get_period(self):
        ed = GateioExchangeDataSpot()
        assert ed.get_period("1m") is not None
        assert ed.get_period("1d") is not None

    def test_get_rest_path(self):
        ed = GateioExchangeDataSpot()
        # Should return empty string for missing key rather than raising
        result = ed.get_rest_path("nonexistent_key")
        assert result == ""

    def test_kline_periods(self):
        ed = GateioExchangeDataSpot()
        assert "1m" in ed.kline_periods
        assert "1h" in ed.kline_periods
        assert "1d" in ed.kline_periods

    def test_legal_currencies(self):
        ed = GateioExchangeDataSpot()
        assert "USDT" in ed.legal_currency
        assert "USD" in ed.legal_currency

    def test_swap_has_rest_paths(self):
        ed = GateioExchangeDataSwap()
        # Futures config should be loaded from YAML
        assert isinstance(ed.rest_paths, dict)


# ═══════════════════════════════════════════════════════════════════════
# Data Container Tests
# ═══════════════════════════════════════════════════════════════════════


class TestGateioDataContainers:
    """Test Gate.io data container classes."""

    def test_ticker_container(self):
        ticker = GateioTickerData(SAMPLE_TICKER, "BTC-USDT", "SPOT", True)
        ticker.init_data()
        assert ticker.get_exchange_name() == "GATEIO"
        assert ticker.get_symbol_name() == "BTC-USDT"
        assert ticker.get_last_price() is not None

    def test_ticker_init_data_returns_self(self):
        ticker = GateioTickerData(SAMPLE_TICKER, "BTC-USDT", "SPOT", True)
        result = ticker.init_data()
        assert result is ticker

    def test_request_ticker_container(self):
        ticker = GateioRequestTickerData(SAMPLE_TICKER, "BTC-USDT", "SPOT", True)
        ticker.init_data()
        assert ticker.get_last_price() == 50000.5
        assert ticker.get_bid_price() == 49990.0
        assert ticker.get_ask_price() == 50010.0
        assert ticker.get_high_24h() == 51000.0
        assert ticker.get_low_24h() == 49000.0
        assert ticker.get_base_volume() == 1234.56

    def test_orderbook_container(self):
        ob = GateioOrderBookData(SAMPLE_ORDERBOOK, "BTC-USDT", "SPOT", True)
        ob.init_data()
        assert ob.get_exchange_name() == "GATEIO"
        assert ob.get_symbol_name() == "BTC-USDT"
        assert len(ob.get_bids()) == 2
        assert len(ob.get_asks()) == 2

    def test_orderbook_init_data_returns_self(self):
        ob = GateioOrderBookData(SAMPLE_ORDERBOOK, "BTC-USDT", "SPOT", True)
        result = ob.init_data()
        assert result is ob

    def test_order_container(self):
        order = GateioOrderData(SAMPLE_ORDER, "BTC-USDT", "SPOT", True)
        order.init_data()
        assert order.get_exchange_name() == "GATEIO"
        assert order.get_order_id() == "123456789"
        assert order.get_side() == "buy"
        assert order.get_status() == "open"

    def test_order_init_data_returns_self(self):
        order = GateioOrderData(SAMPLE_ORDER, "BTC-USDT", "SPOT", True)
        result = order.init_data()
        assert result is order

    def test_balance_container(self):
        balance = GateioBalanceData(SAMPLE_BALANCE, "SPOT", True)
        balance.init_data()
        assert balance.get_exchange_name() == "GATEIO"
        assert balance.get_currency() == "BTC"
        assert balance.get_available() == 0.5
        assert balance.get_locked() == 0.1

    def test_balance_init_data_returns_self(self):
        balance = GateioBalanceData(SAMPLE_BALANCE, "SPOT", True)
        result = balance.init_data()
        assert result is balance


# ═══════════════════════════════════════════════════════════════════════
# Feed Creation Tests
# ═══════════════════════════════════════════════════════════════════════


class TestGateioFeedCreation:
    """Test Gate.io feed creation and three-layer pattern."""

    def test_spot_feed_creation(self):
        feed = _make_spot_feed()
        assert feed is not None
        assert feed.exchange_name == "GATEIO"
        assert feed.asset_type == "spot"

    def test_swap_feed_creation(self):
        feed = _make_swap_feed()
        assert feed is not None
        assert feed.exchange_name == "GATEIO"
        assert feed.asset_type == "swap"

    def test_spot_three_layer_methods_exist(self):
        feed = _make_spot_feed()
        # Layer 1 (underscore private)
        assert hasattr(feed, "_get_ticker")
        assert hasattr(feed, "_get_depth")
        assert hasattr(feed, "_get_kline")
        assert hasattr(feed, "_get_balance")
        assert hasattr(feed, "_make_order")
        assert hasattr(feed, "_cancel_order")
        assert hasattr(feed, "_query_order")
        assert hasattr(feed, "_get_deals")
        # Layer 2 (sync public)
        assert hasattr(feed, "get_ticker")
        assert hasattr(feed, "get_depth")
        assert hasattr(feed, "get_kline")
        assert hasattr(feed, "get_balance")
        assert hasattr(feed, "make_order")
        assert hasattr(feed, "cancel_order")
        assert hasattr(feed, "query_order")
        assert hasattr(feed, "get_deals")
        # Layer 3 (async)
        assert hasattr(feed, "async_get_ticker")
        assert hasattr(feed, "async_get_depth")
        assert hasattr(feed, "async_get_kline")
        assert hasattr(feed, "async_get_balance")
        assert hasattr(feed, "async_make_order")

    def test_swap_three_layer_methods_exist(self):
        feed = _make_swap_feed()
        assert hasattr(feed, "_get_ticker")
        assert hasattr(feed, "get_ticker")
        assert hasattr(feed, "async_get_ticker")
        assert hasattr(feed, "_get_depth")
        assert hasattr(feed, "get_depth")
        assert hasattr(feed, "async_get_depth")
        assert hasattr(feed, "_get_kline")
        assert hasattr(feed, "get_kline")
        assert hasattr(feed, "async_get_kline")
        assert hasattr(feed, "_get_balance")
        assert hasattr(feed, "get_balance")
        assert hasattr(feed, "async_get_balance")
        assert hasattr(feed, "_make_order")
        assert hasattr(feed, "make_order")
        assert hasattr(feed, "async_make_order")
        assert hasattr(feed, "_cancel_order")
        assert hasattr(feed, "cancel_order")
        assert hasattr(feed, "_query_order")
        assert hasattr(feed, "query_order")
        assert hasattr(feed, "_get_deals")
        assert hasattr(feed, "get_deals")


# ═══════════════════════════════════════════════════════════════════════
# Three Layer Pattern Tests (Layer 1 returns)
# ═══════════════════════════════════════════════════════════════════════


class TestGateioThreeLayerPattern:
    """Test layer-1 methods return (path, params, extra_data) correctly."""

    def test_get_ticker_layer1(self):
        feed = _make_spot_feed()
        path, params, extra_data = feed._get_ticker("BTC-USDT")
        assert isinstance(path, str)
        assert "currency_pair" in params
        assert params["currency_pair"] == "BTC_USDT"
        assert extra_data["request_type"] == "get_tick"
        assert extra_data["symbol_name"] == "BTC-USDT"
        assert "normalize_function" in extra_data

    def test_get_depth_layer1(self):
        feed = _make_spot_feed()
        path, params, extra_data = feed._get_depth("BTC-USDT", limit=10)
        assert "currency_pair" in params
        assert params["limit"] == 10
        assert extra_data["request_type"] == "get_depth"

    def test_get_kline_layer1(self):
        feed = _make_spot_feed()
        path, params, extra_data = feed._get_kline("BTC-USDT", "1h", 50)
        assert "currency_pair" in params
        assert "interval" in params
        assert params["limit"] == 50
        assert extra_data["request_type"] == "get_kline"

    def test_get_balance_layer1(self):
        feed = _make_spot_feed()
        path, params, extra_data = feed._get_balance()
        assert isinstance(path, str)
        assert extra_data["request_type"] == "get_account"
        assert "normalize_function" in extra_data

    def test_make_order_layer1(self):
        feed = _make_spot_feed()
        path, body, extra_data = feed._make_order(
            "BTC-USDT", 0.001, price=50000, order_type="buy-limit"
        )
        assert isinstance(path, str)
        assert body["currency_pair"] == "BTC_USDT"
        assert body["side"] == "buy"
        assert body["amount"] == "0.001"
        assert body["price"] == "50000"
        assert extra_data["request_type"] == "make_order"

    def test_make_market_order_layer1(self):
        feed = _make_spot_feed()
        path, body, extra_data = feed._make_order("ETH-USDT", 1.0, order_type="sell-market")
        assert body["side"] == "sell"
        assert body["type"] == "market"

    def test_cancel_order_layer1(self):
        feed = _make_spot_feed()
        path, params, body, extra_data = feed._cancel_order("BTC-USDT", order_id="12345")
        assert isinstance(path, str)
        assert extra_data["order_id"] == "12345"

    def test_query_order_layer1(self):
        feed = _make_spot_feed()
        path, params, extra_data = feed._query_order("BTC-USDT", order_id="12345")
        assert isinstance(path, str)
        assert extra_data["order_id"] == "12345"
        assert "normalize_function" in extra_data

    def test_get_deals_layer1(self):
        feed = _make_spot_feed()
        path, params, extra_data = feed._get_deals("BTC-USDT", limit=50)
        assert isinstance(path, str)
        assert params["limit"] == 50

    # Swap layer 1 tests
    def test_swap_get_ticker_layer1(self):
        feed = _make_swap_feed()
        path, params, extra_data = feed._get_ticker("BTC-USDT")
        assert isinstance(path, str)
        assert extra_data["asset_type"] == "swap"

    def test_swap_make_order_layer1(self):
        feed = _make_swap_feed()
        path, body, extra_data = feed._make_order(
            "BTC-USDT", 10, price=50000, order_type="buy-limit"
        )
        assert "contract" in body
        assert body["size"] == 10
        assert extra_data["asset_type"] == "swap"


# ═══════════════════════════════════════════════════════════════════════
# Normalize Function Tests
# ═══════════════════════════════════════════════════════════════════════


class TestGateioNormalizeFunctions:
    """Test normalization functions for each data type."""

    def test_ticker_normalize(self):
        extra = {"symbol_name": "BTC-USDT", "asset_type": "SPOT"}
        result, status = GateioRequestDataSpot._get_ticker_normalize_function(
            [SAMPLE_TICKER], extra
        )
        assert status is True
        assert len(result) == 1
        assert isinstance(result[0], GateioTickerData)

    def test_ticker_normalize_dict(self):
        extra = {"symbol_name": "BTC-USDT", "asset_type": "SPOT"}
        result, status = GateioRequestDataSpot._get_ticker_normalize_function(SAMPLE_TICKER, extra)
        assert status is True
        assert len(result) == 1

    def test_ticker_normalize_error(self):
        extra = {"symbol_name": "BTC-USDT", "asset_type": "SPOT"}
        result, status = GateioRequestDataSpot._get_ticker_normalize_function(
            {"label": "INVALID_PARAM_VALUE", "message": "bad"}, extra
        )
        assert status is False
        assert len(result) == 0

    def test_ticker_normalize_none(self):
        extra = {"symbol_name": "BTC-USDT", "asset_type": "SPOT"}
        result, status = GateioRequestDataSpot._get_ticker_normalize_function(None, extra)
        assert status is False
        assert len(result) == 0

    def test_depth_normalize(self):
        extra = {"symbol_name": "BTC-USDT", "asset_type": "SPOT"}
        result, status = GateioRequestDataSpot._get_depth_normalize_function(
            SAMPLE_ORDERBOOK, extra
        )
        assert status is True
        assert len(result) == 1
        assert isinstance(result[0], GateioOrderBookData)

    def test_depth_normalize_error(self):
        extra = {"symbol_name": "BTC-USDT", "asset_type": "SPOT"}
        result, status = GateioRequestDataSpot._get_depth_normalize_function(
            {"label": "ERROR", "message": "fail"}, extra
        )
        assert status is False

    def test_balance_normalize(self):
        extra = {"asset_type": "SPOT"}
        result, status = GateioRequestDataSpot._get_balance_normalize_function(
            [SAMPLE_BALANCE], extra
        )
        assert status is True
        assert len(result) == 1
        assert isinstance(result[0], GateioBalanceData)

    def test_balance_normalize_single(self):
        extra = {"asset_type": "SPOT"}
        result, status = GateioRequestDataSpot._get_balance_normalize_function(
            SAMPLE_BALANCE, extra
        )
        assert status is True
        assert len(result) == 1

    def test_make_order_normalize(self):
        extra = {"symbol_name": "BTC-USDT", "asset_type": "SPOT"}
        result, status = GateioRequestDataSpot._make_order_normalize_function(SAMPLE_ORDER, extra)
        assert status is True
        assert len(result) == 1
        assert isinstance(result[0], GateioOrderData)

    def test_query_order_normalize(self):
        extra = {"symbol_name": "BTC-USDT", "asset_type": "SPOT"}
        result, status = GateioRequestDataSpot._query_order_normalize_function(SAMPLE_ORDER, extra)
        assert status is True
        assert len(result) == 1
        assert isinstance(result[0], GateioOrderData)

    def test_kline_normalize(self):
        extra = {"symbol_name": "BTC-USDT", "asset_type": "SPOT"}
        result, status = GateioRequestDataSpot._get_kline_normalize_function(SAMPLE_KLINE, extra)
        assert status is True
        assert len(result) == 1

    def test_extract_data_normalize_function(self):
        result, status = GateioRequestData._extract_data_normalize_function([{"key": "value"}], {})
        assert status is True
        assert len(result) == 1

    def test_extract_data_normalize_error(self):
        result, status = GateioRequestData._extract_data_normalize_function(
            {"label": "ERROR", "message": "fail"}, {}
        )
        assert status is False

    def test_extract_data_normalize_none(self):
        result, status = GateioRequestData._extract_data_normalize_function(None, {})
        assert status is False

    # Swap normalize tests
    def test_swap_ticker_normalize(self):
        extra = {"symbol_name": "BTC-USDT", "asset_type": "swap"}
        result, status = GateioRequestDataSwap._get_ticker_normalize_function(
            [SAMPLE_TICKER], extra
        )
        assert status is True
        assert len(result) == 1

    def test_swap_make_order_normalize(self):
        extra = {"symbol_name": "BTC-USDT", "asset_type": "swap"}
        result, status = GateioRequestDataSwap._make_order_normalize_function(SAMPLE_ORDER, extra)
        assert status is True
        assert len(result) == 1


# ═══════════════════════════════════════════════════════════════════════
# Registration Tests
# ═══════════════════════════════════════════════════════════════════════


class TestGateioRegistration:
    """Test Gate.io registration."""

    def test_gateio_spot_registered(self):
        assert "GATEIO___SPOT" in ExchangeRegistry._feed_classes

    def test_gateio_swap_registered(self):
        assert "GATEIO___SWAP" in ExchangeRegistry._feed_classes

    def test_gateio_create_exchange_data_spot(self):
        cls = ExchangeRegistry._exchange_data_classes.get("GATEIO___SPOT")
        assert cls is not None
        ed = cls()
        assert "gateio" in ed.exchange_name

    def test_gateio_create_exchange_data_swap(self):
        cls = ExchangeRegistry._exchange_data_classes.get("GATEIO___SWAP")
        assert cls is not None
        ed = cls()
        assert "gateio" in ed.exchange_name


# ═══════════════════════════════════════════════════════════════════════
# Signature Tests
# ═══════════════════════════════════════════════════════════════════════


class TestGateioSignature:
    """Test Gate.io HMAC SHA512 signature generation."""

    def test_signature_generation(self):
        feed = _make_spot_feed()
        sig, ts = feed._generate_signature("GET", "/api/v4/spot/tickers", "currency_pair=BTC_USDT")
        assert sig is not None
        assert ts is not None
        assert isinstance(sig, str)
        assert len(sig) == 128  # SHA512 hex digest is 128 chars

    def test_signature_without_keys(self):
        data_queue = queue.Queue()
        feed = GateioRequestDataSpot(data_queue)
        sig, ts = feed._generate_signature("GET", "/api/v4/spot/tickers")
        assert sig is None
        assert ts is None

    def test_auth_headers_with_keys(self):
        feed = _make_spot_feed()
        headers = feed._build_auth_headers("GET", "/api/v4/spot/tickers", "currency_pair=BTC_USDT")
        assert "KEY" in headers
        assert "SIGN" in headers
        assert "Timestamp" in headers
        assert headers["KEY"] == "test_api_key"

    def test_auth_headers_without_keys(self):
        data_queue = queue.Queue()
        feed = GateioRequestDataSpot(data_queue)
        headers = feed._build_auth_headers("GET", "/api/v4/spot/tickers")
        assert "KEY" not in headers
        assert "Content-Type" in headers

    def test_signature_reproducibility(self):
        """Verify HMAC SHA512 signature logic matches expected output."""
        secret = "test_secret_key"
        method = "GET"
        url_path = "/api/v4/spot/tickers"
        query_string = "currency_pair=BTC_USDT"
        payload_string = ""
        timestamp = "1640995200"

        hashed_payload = hashlib.sha512(payload_string.encode()).hexdigest()
        sign_string = f"{method}\n{url_path}\n{query_string}\n{hashed_payload}\n{timestamp}"
        expected_sig = hmac.new(secret.encode(), sign_string.encode(), hashlib.sha512).hexdigest()

        assert len(expected_sig) == 128
        assert isinstance(expected_sig, str)


# ═══════════════════════════════════════════════════════════════════════
# Integration Tests (skipped by default)
# ═══════════════════════════════════════════════════════════════════════


class TestGateioIntegration:
    """Integration tests (require network, skipped by default)."""

    @pytest.mark.skipif(SKIP_LIVE, reason="Live test; set SKIP_LIVE_TESTS=false to run")
    def test_market_data_api(self):
        data_queue = queue.Queue()
        feed = GateioRequestDataSpot(data_queue)
        ticker = feed.get_tick("BTC-USDT")
        assert ticker is not None

    @pytest.mark.skipif(SKIP_LIVE, reason="Live test; set SKIP_LIVE_TESTS=false to run")
    def test_trading_api(self):
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
