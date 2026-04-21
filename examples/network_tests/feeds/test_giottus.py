"""
Test Giottus exchange integration.

Run tests:
    pytest tests/feeds/test_giottus.py -v
"""

import queue
from unittest.mock import Mock

import pytest

# Import registration to auto-register Giottus
import bt_api_py.exchange_registers.register_giottus  # noqa: F401
from bt_api_py.containers.exchanges.giottus_exchange_data import GiottusExchangeDataSpot
from bt_api_py.containers.tickers.giottus_ticker import GiottusRequestTickerData
from bt_api_base.feeds.capability import Capability
from bt_api_py.feeds.live_giottus.spot import GiottusRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


@pytest.fixture
def mock_feed():
    """Create a Giottus feed instance with mocked request."""
    data_queue = queue.Queue()
    feed = GiottusRequestDataSpot(
        data_queue,
        public_key="test_key",
        private_key="test_secret",
        exchange_name="GIOTTUS___SPOT",
    )
    feed.request = Mock(return_value=Mock())
    return feed


class TestGiottusExchangeData:
    """Test Giottus exchange data configuration."""

    def test_exchange_data_spot_creation(self):
        exchange_data = GiottusExchangeDataSpot()
        assert exchange_data.rest_url
        assert exchange_data.asset_type == "spot"

    def test_rest_url(self):
        exchange_data = GiottusExchangeDataSpot()
        assert "giottus.com" in exchange_data.rest_url

    def test_wss_url(self):
        exchange_data = GiottusExchangeDataSpot()
        assert exchange_data.wss_url
        assert "wss://" in exchange_data.wss_url

    @pytest.mark.kline
    def test_kline_periods(self):
        exchange_data = GiottusExchangeDataSpot()
        assert "1m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods

    def test_legal_currency(self):
        exchange_data = GiottusExchangeDataSpot()
        assert "INR" in exchange_data.legal_currency
        assert "USDT" in exchange_data.legal_currency
        assert "BTC" in exchange_data.legal_currency


class TestGiottusRequestDataSpot:
    """Test Giottus spot REST API request class."""

    def test_request_data_creation(self):
        data_queue = queue.Queue()
        feed = GiottusRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
            exchange_name="GIOTTUS___SPOT",
        )
        assert feed.exchange_name == "GIOTTUS___SPOT"
        assert feed.asset_type == "SPOT"

    def test_capabilities(self):
        caps = GiottusRequestDataSpot._capabilities()
        assert Capability.GET_TICK in caps
        assert Capability.GET_DEPTH in caps
        assert Capability.GET_KLINE in caps
        assert Capability.GET_EXCHANGE_INFO in caps
        assert Capability.GET_BALANCE in caps
        assert Capability.GET_ACCOUNT in caps
        assert Capability.MAKE_ORDER in caps
        assert Capability.CANCEL_ORDER in caps

    @pytest.mark.ticker
    def test_get_tick_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_tick("BTC-INR")
        assert path is not None
        assert "symbol" in params
        assert extra_data["request_type"] == "get_tick"
        assert extra_data["symbol_name"] == "BTC-INR"
        assert extra_data["exchange_name"] == "GIOTTUS___SPOT"
        assert extra_data["asset_type"] == "SPOT"

    @pytest.mark.orderbook
    def test_get_depth_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_depth("BTC-INR", count=20)
        assert path is not None
        assert "symbol" in params
        assert "limit" in params
        assert extra_data["request_type"] == "get_depth"
        assert extra_data["symbol_name"] == "BTC-INR"

    @pytest.mark.kline
    def test_get_kline_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_kline("BTC-INR", period="1h", count=20)
        assert path is not None
        assert "symbol" in params
        assert "interval" in params
        assert extra_data["request_type"] == "get_kline"

    def test_get_balance_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_balance()
        assert path is not None
        assert extra_data["request_type"] == "get_balance"

    def test_get_account_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_account()
        assert path is not None
        assert extra_data["request_type"] == "get_account"

    def test_make_order_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._make_order(
            symbol="BTC-INR", volume="0.001", price="50000", order_type="buy-limit"
        )
        assert path is not None
        assert params["side"] == "BUY"
        assert extra_data["request_type"] == "make_order"

    def test_cancel_order_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._cancel_order("BTC-INR", "order_123")
        assert path is not None
        assert extra_data["request_type"] == "cancel_order"
        assert extra_data["order_id"] == "order_123"

    def test_get_server_time_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_server_time()
        assert path is not None
        assert extra_data["request_type"] == "get_server_time"

    def test_get_exchange_info_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_exchange_info()
        assert path is not None
        assert extra_data["request_type"] == "get_exchange_info"


class TestGiottusStandardInterfaces:
    """Test standard Feed interface methods invoke request()."""

    @pytest.fixture
    def feed(self):
        data_queue = queue.Queue()
        feed = GiottusRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
            exchange_name="GIOTTUS___SPOT",
        )
        feed.request = Mock(return_value=Mock())
        return feed

    @pytest.mark.ticker
    def test_get_tick_calls_request(self, feed):
        feed.get_tick("BTC-INR")
        assert feed.request.called
        call_kwargs = feed.request.call_args[1]
        extra_data = call_kwargs.get("extra_data")
        if extra_data:
            assert extra_data["request_type"] == "get_tick"

    @pytest.mark.orderbook
    def test_get_depth_calls_request(self, feed):
        feed.get_depth("BTC-INR")
        assert feed.request.called
        call_kwargs = feed.request.call_args[1]
        extra_data = call_kwargs.get("extra_data")
        if extra_data:
            assert extra_data["request_type"] == "get_depth"

    @pytest.mark.kline
    def test_get_kline_calls_request(self, feed):
        feed.get_kline("BTC-INR", "1h")
        assert feed.request.called
        call_kwargs = feed.request.call_args[1]
        extra_data = call_kwargs.get("extra_data")
        if extra_data:
            assert extra_data["request_type"] == "get_kline"

    def test_get_balance_calls_request(self, feed):
        feed.get_balance()
        assert feed.request.called
        call_kwargs = feed.request.call_args[1]
        extra_data = call_kwargs.get("extra_data")
        if extra_data:
            assert extra_data["request_type"] == "get_balance"

    def test_get_account_calls_request(self, feed):
        feed.get_account()
        assert feed.request.called
        call_kwargs = feed.request.call_args[1]
        extra_data = call_kwargs.get("extra_data")
        if extra_data:
            assert extra_data["request_type"] == "get_account"

    def test_make_order_calls_request(self, feed):
        feed.make_order("BTC-INR", "0.001", "50000", "buy-limit")
        assert feed.request.called
        call_kwargs = feed.request.call_args[1]
        extra_data = call_kwargs.get("extra_data")
        if extra_data:
            assert extra_data["request_type"] == "make_order"

    def test_cancel_order_calls_request(self, feed):
        feed.cancel_order("BTC-INR", "order_123")
        assert feed.request.called
        call_kwargs = feed.request.call_args[1]
        extra_data = call_kwargs.get("extra_data")
        if extra_data:
            assert extra_data["request_type"] == "cancel_order"

    def test_get_exchange_info_calls_request(self, feed):
        feed.get_exchange_info()
        assert feed.request.called


class TestGiottusNormalizeFunctions:
    """Test normalize functions edge cases."""

    @pytest.mark.ticker
    def test_tick_normalize_with_none(self):
        result, status = GiottusRequestDataSpot._get_tick_normalize_function(None, None)
        assert result == []
        assert status is False

    @pytest.mark.ticker
    def test_tick_normalize_with_data(self):
        input_data = {"symbol": "BTCINR", "last": "50000"}
        result, status = GiottusRequestDataSpot._get_tick_normalize_function(input_data, {})
        assert status is True
        assert len(result) == 1

    @pytest.mark.orderbook
    def test_depth_normalize_with_none(self):
        result, status = GiottusRequestDataSpot._get_depth_normalize_function(None, None)
        assert result == []
        assert status is False

    @pytest.mark.orderbook
    def test_depth_normalize_with_data(self):
        input_data = {"bids": [], "asks": []}
        result, status = GiottusRequestDataSpot._get_depth_normalize_function(input_data, {})
        assert status is True

    @pytest.mark.kline
    def test_kline_normalize_with_none(self):
        result, status = GiottusRequestDataSpot._get_kline_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_balance_normalize_with_none(self):
        result, status = GiottusRequestDataSpot._get_balance_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_balance_normalize_with_data(self):
        input_data = [{"currency": "BTC", "available": "0.5"}]
        result, status = GiottusRequestDataSpot._get_balance_normalize_function(input_data, {})
        assert status is True

    def test_account_normalize_with_none(self):
        result, status = GiottusRequestDataSpot._get_account_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_make_order_normalize_with_none(self):
        result, status = GiottusRequestDataSpot._make_order_normalize_function(None, {})
        assert result == []
        assert status is False

    def test_exchange_info_normalize_with_none(self):
        result, status = GiottusRequestDataSpot._get_exchange_info_normalize_function(None, None)
        assert result == []
        assert status is False


class TestGiottusDataContainers:
    """Test Giottus data containers."""

    @pytest.mark.ticker
    def test_ticker_container(self):
        ticker_response = {
            "success": True,
            "data": {
                "symbol": "BTCINR",
                "last": "50000",
                "bid": "49999",
                "ask": "50001",
                "volume": "1000",
                "high": "51000",
                "low": "49000",
            },
        }
        ticker = GiottusRequestTickerData(
            ticker_response, symbol_name="BTC-INR", asset_type="SPOT", has_been_json_encoded=True
        )
        ticker.init_data()
        assert ticker.get_exchange_name() == "GIOTTUS"
        assert ticker.get_symbol_name() == "BTC-INR"


class TestGiottusRegistry:
    """Test Giottus registration."""

    def test_giottus_registered(self):
        assert "GIOTTUS___SPOT" in ExchangeRegistry._feed_classes
        assert "GIOTTUS___SPOT" in ExchangeRegistry._exchange_data_classes

    def test_giottus_create_feed(self):
        data_queue = queue.Queue()
        feed = ExchangeRegistry.create_feed(
            "GIOTTUS___SPOT",
            data_queue,
            public_key="test",
            private_key="test",
        )
        assert isinstance(feed, GiottusRequestDataSpot)

    def test_giottus_create_exchange_data(self):
        exchange_data = ExchangeRegistry.create_exchange_data("GIOTTUS___SPOT")
        assert isinstance(exchange_data, GiottusExchangeDataSpot)


class TestGiottusLiveAPI:
    """Live API tests - require network, marked as integration."""

    @pytest.mark.integration
    @pytest.mark.ticker
    def test_giottus_req_tick_data(self):
        data_queue = queue.Queue()
        feed = GiottusRequestDataSpot(data_queue, exchange_name="GIOTTUS___SPOT")
        result = feed.get_tick("BTC-INR")
        assert result is not None

    @pytest.mark.integration
    @pytest.mark.orderbook
    def test_giottus_req_depth_data(self):
        data_queue = queue.Queue()
        feed = GiottusRequestDataSpot(data_queue, exchange_name="GIOTTUS___SPOT")
        result = feed.get_depth("BTC-INR", count=20)
        assert result is not None

    @pytest.mark.integration
    @pytest.mark.kline
    def test_giottus_req_kline_data(self):
        data_queue = queue.Queue()
        feed = GiottusRequestDataSpot(data_queue, exchange_name="GIOTTUS___SPOT")
        result = feed.get_kline("BTC-INR", "1h", count=10)
        assert result is not None

    @pytest.mark.integration
    def test_giottus_server_time(self):
        data_queue = queue.Queue()
        feed = GiottusRequestDataSpot(data_queue, exchange_name="GIOTTUS___SPOT")
        result = feed.get_server_time()
        assert result is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
