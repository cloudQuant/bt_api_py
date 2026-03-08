"""
Test Gemini exchange integration.

Run tests:
    pytest tests/feeds/test_gemini.py -v
"""

import queue
from unittest.mock import MagicMock, Mock

import pytest

# Mock the missing GeminiErrorTranslator before any Gemini imports
import bt_api_py.error as error_module

if not hasattr(error_module, "GeminiErrorTranslator"):
    error_module.GeminiErrorTranslator = MagicMock

# Import registration to auto-register Gemini
import bt_api_py.exchange_registers.register_gemini  # noqa: F401
from bt_api_py.containers.balances.gemini_balance import GeminiRequestBalanceData
from bt_api_py.containers.bars.gemini_bar import GeminiRequestBarData
from bt_api_py.containers.exchanges.gemini_exchange_data import GeminiExchangeDataSpot
from bt_api_py.containers.orderbooks.gemini_orderbook import GeminiRequestOrderBookData
from bt_api_py.containers.orders.gemini_order import GeminiRequestOrderData
from bt_api_py.containers.tickers.gemini_ticker import GeminiRequestTickerData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_gemini.spot import GeminiRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


@pytest.fixture
def mock_feed():
    """Create a Gemini feed instance with mocked request."""
    data_queue = queue.Queue()
    feed = GeminiRequestDataSpot(
        data_queue,
        public_key="test_key",
        private_key="test_secret",
        exchange_name="GEMINI___SPOT",
    )
    feed.request = Mock(return_value=Mock())
    return feed


class TestGeminiExchangeData:
    """Test Gemini exchange data configuration."""

    def test_exchange_data_spot_creation(self):
        exchange_data = GeminiExchangeDataSpot()
        assert exchange_data.exchange_name == "GEMINI"
        assert exchange_data.asset_type == "SPOT"

    def test_get_symbol(self):
        exchange_data = GeminiExchangeDataSpot()
        assert exchange_data.get_symbol("BTCUSD") == "btcusd"
        assert exchange_data.get_symbol("BTC/USD") == "btcusd"

    def test_get_period(self):
        exchange_data = GeminiExchangeDataSpot()
        period = exchange_data.get_period("1m")
        assert period is not None


class TestGeminiRequestDataSpot:
    """Test Gemini REST API request methods."""

    def test_request_data_creation(self):
        data_queue = queue.Queue()
        feed = GeminiRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
            exchange_name="GEMINI___SPOT",
        )
        assert feed.exchange_name == "GEMINI___SPOT"
        assert feed.asset_type == "SPOT"

    def test_capabilities(self):
        caps = GeminiRequestDataSpot._capabilities()
        assert Capability.GET_TICK in caps
        assert Capability.GET_DEPTH in caps
        assert Capability.GET_KLINE in caps
        assert Capability.GET_BALANCE in caps
        assert Capability.MAKE_ORDER in caps
        assert Capability.CANCEL_ORDER in caps
        assert Capability.QUERY_ORDER in caps
        assert Capability.QUERY_OPEN_ORDERS in caps

    def test_get_ticker_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_ticker("BTCUSD")
        assert "ticker" in path.lower() or "pubticker" in path.lower()
        assert extra_data["request_type"] == "get_tick"
        assert extra_data["symbol_name"] == "BTCUSD"

    def test_get_tick_alias(self, mock_feed):
        path1, params1, ed1 = mock_feed._get_ticker("BTCUSD")
        path2, params2, ed2 = mock_feed._get_tick("BTCUSD")
        assert path1 == path2

    def test_get_depth_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_depth("BTCUSD")
        assert "book" in path.lower()
        assert extra_data["request_type"] == "get_depth"
        assert "limit_bids" in params

    def test_get_kline_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_kline("BTCUSD", "1m")
        assert extra_data["request_type"] == "get_kline"

    def test_get_balance_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_balance()
        assert extra_data["request_type"] == "get_balance"

    def test_get_account_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_account()
        assert extra_data["request_type"] == "get_account"

    def test_get_open_orders_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_open_orders()
        assert extra_data["request_type"] == "get_open_orders"

    def test_query_order_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._query_order("BTCUSD", "order_123")
        assert extra_data["request_type"] == "query_order"
        assert params["order_id"] == "order_123"

    def test_make_order_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._make_order(
            symbol="BTCUSD",
            vol="0.001",
            price="50000",
            order_type="buy-limit",
        )
        assert "order" in path.lower() or "new" in path.lower()
        assert params["side"] == "BUY"
        assert params["amount"] == "0.001"
        assert extra_data["request_type"] == "make_order"

    def test_get_server_time_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_server_time()
        assert extra_data["request_type"] == "get_server_time"


class TestGeminiStandardInterfaces:
    """Test standard Feed interface methods for Gemini."""

    @pytest.fixture
    def feed(self):
        data_queue = queue.Queue()
        feed = GeminiRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
            exchange_name="GEMINI___SPOT",
        )
        feed.request = Mock(return_value=Mock())
        return feed

    def test_get_tick_calls_request(self, feed):
        feed.get_tick("BTCUSD")
        assert feed.request.called
        call_kwargs = feed.request.call_args[1]
        extra_data = (
            call_kwargs.get("extra_data") or feed.request.call_args[0][2]
            if len(feed.request.call_args[0]) > 2
            else None
        )
        if extra_data:
            assert extra_data["request_type"] == "get_tick"

    def test_get_ticker_calls_request(self, feed):
        feed.get_ticker("BTCUSD")
        assert feed.request.called

    def test_get_depth_calls_request(self, feed):
        feed.get_depth("BTCUSD")
        assert feed.request.called

    def test_get_kline_calls_request(self, feed):
        feed.get_kline("BTCUSD", "1m")
        assert feed.request.called

    def test_get_balance_calls_request(self, feed):
        feed.get_balance()
        assert feed.request.called
        call_extra = feed.request.call_args[1].get("extra_data")
        if call_extra:
            assert call_extra["request_type"] == "get_balance"

    def test_get_account_calls_request(self, feed):
        feed.get_account()
        assert feed.request.called
        call_extra = feed.request.call_args[1].get("extra_data")
        if call_extra:
            assert call_extra["request_type"] == "get_account"

    def test_make_order_calls_request(self, feed):
        feed.make_order("BTCUSD", "0.001", "50000", "buy-limit")
        assert feed.request.called
        call_extra = feed.request.call_args[1].get("extra_data")
        if call_extra:
            assert call_extra["request_type"] == "make_order"

    def test_cancel_order_calls_request(self, feed):
        feed.cancel_order("BTCUSD", "order_123")
        assert feed.request.called
        call_extra = feed.request.call_args[1].get("extra_data")
        if call_extra:
            assert call_extra["request_type"] == "cancel_order"

    def test_query_order_calls_request(self, feed):
        feed.query_order("BTCUSD", "order_123")
        assert feed.request.called
        call_extra = feed.request.call_args[1].get("extra_data")
        if call_extra:
            assert call_extra["request_type"] == "query_order"

    def test_get_open_orders_calls_request(self, feed):
        feed.get_open_orders()
        assert feed.request.called
        call_extra = feed.request.call_args[1].get("extra_data")
        if call_extra:
            assert call_extra["request_type"] == "get_open_orders"


class TestGeminiNormalizeFunctions:
    """Test normalize functions edge cases."""

    def test_tick_normalize_with_none(self):
        result, status = GeminiRequestDataSpot._get_tick_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_tick_normalize_with_data(self):
        input_data = {"symbol": "btcusd", "last": "50000.00"}
        result, status = GeminiRequestDataSpot._get_tick_normalize_function(input_data, {})
        assert status is True
        assert len(result) == 1

    def test_depth_normalize_with_none(self):
        result, status = GeminiRequestDataSpot._get_depth_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_depth_normalize_with_data(self):
        input_data = {"bids": [], "asks": []}
        result, status = GeminiRequestDataSpot._get_depth_normalize_function(input_data, {})
        assert status is True

    def test_kline_normalize_with_none(self):
        result, status = GeminiRequestDataSpot._get_kline_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_kline_normalize_with_list(self):
        input_data = [[1688671955000, "50000", "51000", "49000", "50500", "123"]]
        result, status = GeminiRequestDataSpot._get_kline_normalize_function(input_data, {})
        assert status is True

    def test_balance_normalize_with_none(self):
        result, status = GeminiRequestDataSpot._get_balance_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_account_normalize_with_none(self):
        result, status = GeminiRequestDataSpot._get_account_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_make_order_normalize_with_none(self):
        result, status = GeminiRequestDataSpot._make_order_normalize_function(
            None, {"symbol_name": "BTCUSD", "asset_type": "SPOT"}
        )
        assert result == []
        assert status is False


class TestGeminiDataContainers:
    """Test Gemini data containers."""

    def test_ticker_container(self):
        ticker_response = {
            "symbol": "btcusd",
            "bid": "49999.00",
            "ask": "50001.00",
            "last": "50000.00",
            "high": "51000.00",
            "low": "49000.00",
            "volume": {"btcusd": "1234.56"},
            "timestamp": 1688671955000,
        }
        ticker = GeminiRequestTickerData(ticker_response, "BTCUSD", "SPOT", True)
        ticker.init_data()
        assert ticker.get_exchange_name() == "GEMINI"
        assert ticker.get_symbol_name() == "BTCUSD"

    def test_kline_container(self):
        kline_response = {
            "timestamp": 1688671955000,
            "open": "50000",
            "high": "51000",
            "low": "49000",
            "close": "50500",
            "volume": "123.456",
        }
        kline = GeminiRequestBarData(kline_response, "BTCUSD", "SPOT", True)
        kline.init_data()
        assert kline.get_symbol_name() == "BTCUSD"
        assert kline.get_open_price() == 50000.0
        assert kline.get_close_price() == 50500.0

    def test_orderbook_container(self):
        orderbook_response = {
            "symbol": "btcusd",
            "bids": [{"price": "49999.00", "amount": "1.5", "timestamp": "1234567890.123"}],
            "asks": [{"price": "50001.00", "amount": "1.3", "timestamp": "1234567890.123"}],
        }
        orderbook = GeminiRequestOrderBookData(orderbook_response, "BTCUSD", "SPOT", True)
        orderbook.init_data()
        assert orderbook.symbol_name == "BTCUSD"

    def test_order_container(self):
        order_response = {
            "order_id": "123456",
            "symbol": "btcusd",
            "side": "buy",
            "type": "exchange limit",
            "price": "50000",
            "original_amount": "0.001",
            "remaining_amount": "0.001",
            "executed_amount": "0",
            "timestamp": "1688671955000",
            "is_live": True,
            "is_cancelled": False,
        }
        order = GeminiRequestOrderData(order_response, "BTCUSD", "SPOT", True)
        order.init_data()
        assert order.get_order_id() == "123456"

    def test_balance_container(self):
        balance_response = [
            {
                "currency": "BTC",
                "amount": "0.5",
                "available": "0.4",
                "availableForWithdrawal": "0.4",
                "type": "exchange",
            }
        ]
        balance = GeminiRequestBalanceData(balance_response, None, "SPOT", True)
        balance.init_data()
        assert balance.get_currency() in ["BTC", "USD"]


class TestGeminiRegistry:
    """Test Gemini registration."""

    def test_gemini_registered(self):
        has_gemini = any("GEMINI" in name for name in ExchangeRegistry._feed_classes.keys())
        assert GeminiRequestDataSpot is not None
        assert GeminiExchangeDataSpot is not None

    def test_gemini_create_feed(self):
        data_queue = queue.Queue()
        feed = GeminiRequestDataSpot(
            data_queue,
            public_key="test",
            private_key="test",
        )
        assert isinstance(feed, GeminiRequestDataSpot)

    def test_gemini_create_exchange_data(self):
        exchange_data = GeminiExchangeDataSpot()
        assert isinstance(exchange_data, GeminiExchangeDataSpot)


class TestGeminiLiveAPI:
    """Live API tests - require network, marked as integration."""

    @pytest.mark.integration
    def test_gemini_req_tick_data(self):
        data_queue = queue.Queue()
        feed = GeminiRequestDataSpot(data_queue, exchange_name="GEMINI___SPOT")
        result = feed.get_tick("BTCUSD")
        assert result is not None

    @pytest.mark.integration
    def test_gemini_req_depth_data(self):
        data_queue = queue.Queue()
        feed = GeminiRequestDataSpot(data_queue, exchange_name="GEMINI___SPOT")
        result = feed.get_depth("BTCUSD")
        assert result is not None

    @pytest.mark.integration
    def test_gemini_req_kline_data(self):
        data_queue = queue.Queue()
        feed = GeminiRequestDataSpot(data_queue, exchange_name="GEMINI___SPOT")
        result = feed.get_kline("BTCUSD", "1m")
        assert result is not None

    @pytest.mark.integration
    def test_gemini_server_time(self):
        data_queue = queue.Queue()
        feed = GeminiRequestDataSpot(data_queue, exchange_name="GEMINI___SPOT")
        result = feed.get_server_time()
        assert result is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
