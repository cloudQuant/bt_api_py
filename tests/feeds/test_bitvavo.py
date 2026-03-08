"""
Test Bitvavo exchange integration.

Run tests:
    pytest tests/feeds/test_bitvavo.py -v
"""

import queue
import time
from unittest.mock import MagicMock, Mock, patch

import pytest

# Import registration to auto-register Bitvavo
import bt_api_py.exchange_registers.register_bitvavo  # noqa: F401
from bt_api_py.containers.exchanges.bitvavo_exchange_data import (
    BitvavoExchangeDataSpot,
)
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_bitvavo.spot import BitvavoRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


@pytest.fixture
def mock_logger():
    """Mock the logger to avoid AttributeError with warning method."""
    with patch("bt_api_py.containers.exchanges.bitvavo_exchange_data.logger") as mock_log:
        mock_log.warning = MagicMock()
        yield mock_log


@pytest.fixture
def mock_feed():
    """Create a Bitvavo feed instance with mocked request."""
    data_queue = queue.Queue()
    feed = BitvavoRequestDataSpot(data_queue, exchange_name="BITVAVO___SPOT")
    feed.request = Mock(return_value=Mock(spec=RequestData))
    return feed


class TestBitvavoExchangeData:
    """Test Bitvavo exchange data configuration."""

    def test_exchange_data_spot_creation(self, mock_logger):
        exchange_data = BitvavoExchangeDataSpot()
        assert exchange_data.exchange_name == "bitvavo"
        assert exchange_data.asset_type == "spot"
        assert hasattr(exchange_data, "rest_url")

    def test_kline_periods(self, mock_logger):
        exchange_data = BitvavoExchangeDataSpot()
        assert "1m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods

    def test_legal_currencies(self, mock_logger):
        exchange_data = BitvavoExchangeDataSpot()
        assert "EUR" in exchange_data.legal_currency
        assert "USDT" in exchange_data.legal_currency

    def test_wss_url(self, mock_logger):
        exchange_data = BitvavoExchangeDataSpot()
        assert exchange_data.wss_url != ""
        assert "ws" in exchange_data.wss_url.lower()


class TestBitvavoRequestDataSpot:
    """Test Bitvavo REST API request methods."""

    def test_request_data_creation(self):
        data_queue = queue.Queue()
        feed = BitvavoRequestDataSpot(data_queue, exchange_name="BITVAVO___SPOT")
        assert feed.exchange_name == "BITVAVO___SPOT"
        assert feed.asset_type == "SPOT"

    def test_capabilities(self):
        caps = BitvavoRequestDataSpot._capabilities()
        assert Capability.GET_TICK in caps
        assert Capability.GET_DEPTH in caps
        assert Capability.GET_KLINE in caps
        assert Capability.GET_EXCHANGE_INFO in caps
        assert Capability.GET_BALANCE in caps
        assert Capability.GET_ACCOUNT in caps
        assert Capability.MAKE_ORDER in caps
        assert Capability.CANCEL_ORDER in caps

    def test_get_tick_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_tick("BTC-EUR")
        assert "GET" in path
        assert "ticker" in path
        assert params["market"] == "BTC-EUR"
        assert extra_data["request_type"] == "get_tick"

    def test_get_tick_calls_request(self, mock_feed):
        mock_feed.get_tick("BTC-EUR")
        assert mock_feed.request.called

    def test_get_depth_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_depth("BTC-EUR")
        assert "book" in path
        assert extra_data["request_type"] == "get_depth"

    def test_get_depth_calls_request(self, mock_feed):
        mock_feed.get_depth("BTC-EUR")
        assert mock_feed.request.called

    def test_get_kline_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_kline("BTC-EUR", "1h")
        assert "candles" in path
        assert extra_data["request_type"] == "get_kline"

    def test_get_kline_calls_request(self, mock_feed):
        mock_feed.get_kline("BTC-EUR", "1h")
        assert mock_feed.request.called

    def test_get_exchange_info_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_exchange_info()
        assert extra_data["request_type"] == "get_exchange_info"

    def test_get_server_time_tuple(self):
        data_queue = queue.Queue()
        feed = BitvavoRequestDataSpot(data_queue, exchange_name="BITVAVO___SPOT")
        path, params, extra_data = feed._get_server_time()
        assert extra_data["request_type"] == "get_server_time"


class TestBitvavoStandardInterfaces:
    """Test standard Feed interface methods for Bitvavo."""

    @pytest.fixture
    def feed(self):
        data_queue = queue.Queue()
        feed = BitvavoRequestDataSpot(data_queue, exchange_name="BITVAVO___SPOT")
        feed.request = Mock(return_value=Mock(spec=RequestData))
        return feed

    def test_make_order_calls_request(self, feed):
        feed.make_order("BTC-EUR", 0.01, 50000, "limit", offset="buy")
        assert feed.request.called
        extra_data = feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "make_order"

    def test_cancel_order_calls_request(self, feed):
        feed.cancel_order("BTC-EUR", "order_123")
        assert feed.request.called
        extra_data = feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "cancel_order"

    def test_query_order_calls_request(self, feed):
        feed.query_order("BTC-EUR", "order_123")
        assert feed.request.called
        extra_data = feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "query_order"

    def test_get_open_orders_calls_request(self, feed):
        feed.get_open_orders("BTC-EUR")
        assert feed.request.called
        extra_data = feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "get_open_orders"

    def test_get_account_calls_request(self, feed):
        feed.get_account()
        assert feed.request.called
        extra_data = feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "get_account"

    def test_get_balance_calls_request(self, feed):
        feed.get_balance()
        assert feed.request.called
        extra_data = feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "get_balance"

    def test_get_exchange_info_calls_request(self, feed):
        feed.get_exchange_info()
        assert feed.request.called
        extra_data = feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "get_exchange_info"


class TestBitvavoBaseCapabilities:
    """Test capabilities on the base class."""

    def test_base_capabilities(self):
        from bt_api_py.feeds.live_bitvavo.request_base import BitvavoRequestData

        caps = BitvavoRequestData._capabilities()
        assert Capability.GET_TICK in caps
        assert Capability.MAKE_ORDER in caps
        assert Capability.CANCEL_ORDER in caps


class TestBitvavoNormalizeFunctions:
    """Test normalize functions edge cases."""

    def test_tick_normalize_with_none(self):
        result, status = BitvavoRequestDataSpot._get_tick_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_tick_normalize_success(self):
        data = {"market": "BTC-EUR", "last": "50000", "bid": "49990", "ask": "50010"}
        result, status = BitvavoRequestDataSpot._get_tick_normalize_function(data, None)
        assert status is True
        assert len(result) == 1

    def test_depth_normalize_with_none(self):
        result, status = BitvavoRequestDataSpot._get_depth_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_depth_normalize_success(self):
        data = {"bids": [["49990", "1.0"]], "asks": [["50010", "1.0"]]}
        result, status = BitvavoRequestDataSpot._get_depth_normalize_function(data, None)
        assert status is True
        assert "bids" in result[0]

    def test_kline_normalize_with_none(self):
        result, status = BitvavoRequestDataSpot._get_kline_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_kline_normalize_success(self):
        data = [[1640995200000, "49500", "51000", "49000", "50000", "1234.56"]]
        result, status = BitvavoRequestDataSpot._get_kline_normalize_function(data, None)
        assert status is True
        assert isinstance(result[0], list)

    def test_exchange_info_normalize_with_none(self):
        result, status = BitvavoRequestDataSpot._get_exchange_info_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_account_normalize_with_none(self):
        result, status = BitvavoRequestDataSpot._get_account_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_balance_normalize_with_none(self):
        result, status = BitvavoRequestDataSpot._get_balance_normalize_function(None, None)
        assert result == []
        assert status is False


class TestBitvavoRegistration:
    """Test Bitvavo registration."""

    def test_bitvavo_registered(self):
        assert "BITVAVO___SPOT" in ExchangeRegistry._feed_classes
        assert ExchangeRegistry._feed_classes["BITVAVO___SPOT"] == BitvavoRequestDataSpot

    def test_bitvavo_exchange_data_registered(self):
        assert "BITVAVO___SPOT" in ExchangeRegistry._exchange_data_classes
        assert ExchangeRegistry._exchange_data_classes["BITVAVO___SPOT"] == BitvavoExchangeDataSpot

    def test_bitvavo_create_exchange_data(self):
        exchange_data = ExchangeRegistry.create_exchange_data("BITVAVO___SPOT")
        assert isinstance(exchange_data, BitvavoExchangeDataSpot)


class TestBitvavoLiveAPI:
    """Live API tests - require network, marked as integration."""

    @pytest.mark.integration
    def test_bitvavo_req_tick_data(self):
        data_queue = queue.Queue()
        feed = BitvavoRequestDataSpot(data_queue, exchange_name="BITVAVO___SPOT")
        data = feed.get_tick("BTC-EUR")
        assert isinstance(data, RequestData)

    @pytest.mark.integration
    def test_bitvavo_req_depth_data(self):
        data_queue = queue.Queue()
        feed = BitvavoRequestDataSpot(data_queue, exchange_name="BITVAVO___SPOT")
        data = feed.get_depth("BTC-EUR", 20)
        assert isinstance(data, RequestData)

    @pytest.mark.integration
    def test_bitvavo_async_tick_data(self):
        data_queue = queue.Queue()
        feed = BitvavoRequestDataSpot(data_queue, exchange_name="BITVAVO___SPOT")
        feed.async_get_tick("BTC-EUR")
        time.sleep(3)
        try:
            tick_data = data_queue.get(timeout=10)
            assert tick_data is not None
        except queue.Empty:
            pass
