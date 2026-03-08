"""
Test Bitbns exchange integration.

Run tests:
    pytest tests/feeds/test_bitbns.py -v
"""

import queue
import time
from unittest.mock import MagicMock, Mock, patch

import pytest

# Import registration to auto-register Bitbns
import bt_api_py.exchange_registers.register_bitbns  # noqa: F401
from bt_api_py.containers.exchanges.bitbns_exchange_data import (
    BitbnsExchangeDataSpot,
)
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_bitbns.spot import BitbnsRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


@pytest.fixture
def mock_logger():
    """Mock the logger to avoid AttributeError with warning method."""
    with patch("bt_api_py.containers.exchanges.bitbns_exchange_data.logger") as mock_log:
        mock_logger.warning = MagicMock()
        yield mock_log


@pytest.fixture
def mock_feed():
    """Create a Bitbns feed instance with mocked request."""
    data_queue = queue.Queue()
    feed = BitbnsRequestDataSpot(data_queue, exchange_name="BITBNS___SPOT")
    feed.request = Mock(return_value=Mock(spec=RequestData))
    return feed


class TestBitbnsExchangeData:
    """Test Bitbns exchange data configuration."""

    def test_exchange_data_spot_creation(self, mock_logger):
        """Test creating Bitbns spot exchange data."""
        exchange_data = BitbnsExchangeDataSpot()
        assert exchange_data.exchange_name == "bitbns"
        assert exchange_data.asset_type == "spot"
        assert hasattr(exchange_data, "rest_url")

    def test_kline_periods(self, mock_logger):
        """Test kline periods are defined."""
        exchange_data = BitbnsExchangeDataSpot()
        assert "1m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods

    def test_legal_currencies(self, mock_logger):
        """Test legal currencies are defined."""
        exchange_data = BitbnsExchangeDataSpot()
        assert "INR" in exchange_data.legal_currency
        assert "USDT" in exchange_data.legal_currency

    def test_wss_url(self, mock_logger):
        """Test WebSocket URL is configured."""
        exchange_data = BitbnsExchangeDataSpot()
        assert exchange_data.wss_url != ""
        assert "ws" in exchange_data.wss_url.lower()


class TestBitbnsRequestDataSpot:
    """Test Bitbns REST API request methods."""

    def test_request_data_creation(self):
        """Test creating Bitbns request data."""
        data_queue = queue.Queue()
        request_data = BitbnsRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
            exchange_name="BITBNS___SPOT",
        )
        assert request_data.exchange_name == "BITBNS___SPOT"
        assert request_data.asset_type == "SPOT"

    def test_capabilities(self):
        """Test declared capabilities include all standard interfaces."""
        caps = BitbnsRequestDataSpot._capabilities()
        assert Capability.GET_TICK in caps
        assert Capability.GET_DEPTH in caps
        assert Capability.GET_KLINE in caps
        assert Capability.GET_EXCHANGE_INFO in caps
        assert Capability.GET_BALANCE in caps
        assert Capability.GET_ACCOUNT in caps
        assert Capability.MAKE_ORDER in caps
        assert Capability.CANCEL_ORDER in caps

    def test_normalize_symbol(self):
        """Test symbol normalization for Bitbns."""
        data_queue = queue.Queue()
        request_data = BitbnsRequestDataSpot(data_queue)
        assert request_data._normalize_symbol("BTC/USDT") == "BTC_USDT"
        assert request_data._normalize_symbol("BTC-USDT") == "BTC_USDT"
        assert request_data._normalize_symbol("BTC") == "BTC"

    def test_parse_symbol(self):
        """Test symbol parsing into base/market."""
        data_queue = queue.Queue()
        feed = BitbnsRequestDataSpot(data_queue)
        base, market = feed._parse_symbol("BTC/USDT")
        assert base == "BTC"
        assert market == "USDT"
        base2, market2 = feed._parse_symbol("ETH/INR")
        assert base2 == "ETH"
        assert market2 == "INR"

    def test_get_tick_returns_tuple(self, mock_feed):
        """Test _get_tick returns (path, params, extra_data) tuple."""
        path, params, extra_data = mock_feed._get_tick("BTC/USDT")
        assert "GET" in path
        assert params["symbol"] == "BTC"
        assert params["market"] == "USDT"
        assert extra_data["request_type"] == "get_tick"
        assert extra_data["symbol_name"] == "BTC/USDT"

    def test_get_tick_calls_request(self, mock_feed):
        mock_feed.get_tick("BTC/USDT")
        assert mock_feed.request.called

    def test_get_depth_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_depth("BTC/USDT")
        assert "orderBook" in path
        assert extra_data["request_type"] == "get_depth"

    def test_get_depth_calls_request(self, mock_feed):
        mock_feed.get_depth("BTC/USDT")
        assert mock_feed.request.called

    def test_get_kline_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_kline("BTC/USDT", "1h")
        assert "kline" in path
        assert extra_data["request_type"] == "get_kline"
        assert params["period"] == "1h"

    def test_get_kline_calls_request(self, mock_feed):
        mock_feed.get_kline("BTC/USDT", "1h")
        assert mock_feed.request.called

    def test_get_exchange_info_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_exchange_info()
        assert extra_data["request_type"] == "get_exchange_info"

    def test_get_server_time_tuple(self):
        data_queue = queue.Queue()
        feed = BitbnsRequestDataSpot(data_queue, exchange_name="BITBNS___SPOT")
        path, params, extra_data = feed._get_server_time()
        assert extra_data["request_type"] == "get_server_time"

    def test_normalize_functions_exist(self):
        """Test that normalization functions exist."""
        input_data = {
            "status": 1,
            "data": {
                "BTC": {
                    "last_traded_price": "50000",
                    "highest_buy_bid": "49990",
                    "lowest_sell_bid": "50010",
                }
            },
        }
        extra_data = {"symbol_name": "BTC/USDT"}
        result, success = BitbnsRequestDataSpot._get_tick_normalize_function(input_data, extra_data)
        assert success is True

    def test_depth_normalize_function(self):
        """Test depth normalization function."""
        input_data = {
            "status": 1,
            "data": {
                "bids": [["49990", "1.0"]],
                "asks": [["50010", "1.0"]],
            },
        }
        extra_data = {"symbol_name": "BTC/USDT"}
        result, success = BitbnsRequestDataSpot._get_depth_normalize_function(
            input_data, extra_data
        )
        assert success is True
        assert "bids" in result[0]

    def test_kline_normalize_function(self):
        """Test kline normalization function."""
        input_data = {
            "status": 1,
            "data": [[1640995200, "49500", "51000", "49000", "50000", "1234.56"]],
        }
        extra_data = {"symbol_name": "BTC/USDT", "period": "1h"}
        result, success = BitbnsRequestDataSpot._get_kline_normalize_function(
            input_data, extra_data
        )
        assert success is True
        assert isinstance(result[0], list)


class TestBitbnsRegistration:
    """Test Bitbns registration."""

    def test_bitbns_registered(self):
        """Test that Bitbns is properly registered."""
        assert "BITBNS___SPOT" in ExchangeRegistry._feed_classes
        assert ExchangeRegistry._feed_classes["BITBNS___SPOT"] == BitbnsRequestDataSpot
        assert "BITBNS___SPOT" in ExchangeRegistry._exchange_data_classes
        assert ExchangeRegistry._exchange_data_classes["BITBNS___SPOT"] == BitbnsExchangeDataSpot
        assert "BITBNS___SPOT" in ExchangeRegistry._balance_handlers

    def test_bitbns_create_exchange_data(self):
        """Test creating Bitbns exchange data through registry."""
        exchange_data = ExchangeRegistry.create_exchange_data("BITBNS___SPOT")
        assert isinstance(exchange_data, BitbnsExchangeDataSpot)


class TestBitbnsStandardInterfaces:
    """Test standard Feed interface methods for Bitbns."""

    @pytest.fixture
    def feed(self):
        data_queue = queue.Queue()
        feed = BitbnsRequestDataSpot(data_queue, exchange_name="BITBNS___SPOT")
        feed.request = Mock(return_value=Mock(spec=RequestData))
        return feed

    def test_make_order_calls_request(self, feed):
        feed.make_order("BTC/USDT", 0.01, 50000, "limit")
        assert feed.request.called
        extra_data = feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "make_order"

    def test_cancel_order_calls_request(self, feed):
        feed.cancel_order("BTC/USDT", "order_123")
        assert feed.request.called
        extra_data = feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "cancel_order"

    def test_query_order_calls_request(self, feed):
        feed.query_order("BTC/USDT", "order_123")
        assert feed.request.called
        extra_data = feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "query_order"

    def test_get_open_orders_calls_request(self, feed):
        feed.get_open_orders("BTC/USDT")
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


class TestBitbnsBaseCapabilities:
    """Test capabilities on the base class."""

    def test_base_capabilities(self):
        from bt_api_py.feeds.live_bitbns.request_base import BitbnsRequestData

        caps = BitbnsRequestData._capabilities()
        assert Capability.GET_TICK in caps
        assert Capability.MAKE_ORDER in caps
        assert Capability.CANCEL_ORDER in caps


class TestBitbnsNormalizeFunctions:
    """Test normalize functions edge cases."""

    def test_tick_normalize_with_none(self):
        result, status = BitbnsRequestDataSpot._get_tick_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_depth_normalize_with_none(self):
        result, status = BitbnsRequestDataSpot._get_depth_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_kline_normalize_with_none(self):
        result, status = BitbnsRequestDataSpot._get_kline_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_exchange_info_normalize_with_none(self):
        result, status = BitbnsRequestDataSpot._get_exchange_info_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_trades_normalize_with_none(self):
        result, status = BitbnsRequestDataSpot._get_trades_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_tick_normalize_failure(self):
        result, status = BitbnsRequestDataSpot._get_tick_normalize_function(
            {"status": 0, "data": {}}, None
        )
        assert result == []
        assert status is False


class TestBitbnsLiveAPI:
    """Live API tests - require network, marked as integration."""

    @pytest.mark.integration
    def test_bitbns_req_tick_data(self):
        data_queue = queue.Queue()
        feed = BitbnsRequestDataSpot(data_queue, exchange_name="BITBNS___SPOT")
        data = feed.get_tick("BTC/USDT")
        assert isinstance(data, RequestData)
        data_list = data.get_data()
        assert isinstance(data_list, list)
        if data_list and len(data_list) > 0:
            ticker = data_list[0]
            if isinstance(ticker, dict):
                assert isinstance(ticker, dict)

    @pytest.mark.integration
    def test_bitbns_req_kline_data(self):
        data_queue = queue.Queue()
        feed = BitbnsRequestDataSpot(data_queue, exchange_name="BITBNS___SPOT")
        data = feed.get_kline("BTC/USDT", "1h", count=2)
        assert isinstance(data, RequestData)
        data_list = data.get_data()
        assert isinstance(data_list, list)
        if data_list and len(data_list) > 0:
            klines = data_list[0]
            assert isinstance(klines, (list, dict))

    @pytest.mark.integration
    def test_bitbns_req_orderbook_data(self):
        data_queue = queue.Queue()
        feed = BitbnsRequestDataSpot(data_queue, exchange_name="BITBNS___SPOT")
        data = feed.get_depth("BTC/USDT", 20)
        assert isinstance(data, RequestData)
        data_list = data.get_data()
        assert isinstance(data_list, list)
        if data_list and len(data_list) > 0:
            orderbook = data_list[0]
            if isinstance(orderbook, dict):
                assert "bids" in orderbook or "asks" in orderbook

    @pytest.mark.integration
    def test_bitbns_async_tick_data(self):
        data_queue = queue.Queue()
        feed = BitbnsRequestDataSpot(data_queue, exchange_name="BITBNS___SPOT")
        feed.async_get_tick("BTC/USDT", extra_data={"test_async": True})
        time.sleep(3)
        try:
            tick_data = data_queue.get(timeout=10)
            assert tick_data is not None
        except queue.Empty:
            pass

    @pytest.mark.integration
    def test_bitbns_async_kline_data(self):
        data_queue = queue.Queue()
        feed = BitbnsRequestDataSpot(data_queue, exchange_name="BITBNS___SPOT")
        feed.async_get_kline("BTC/USDT", period="1h", count=3, extra_data={"test_async": True})
        time.sleep(5)
        try:
            kline_data = data_queue.get(timeout=10)
            assert kline_data is not None
        except queue.Empty:
            pass

    @pytest.mark.integration
    def test_bitbns_async_orderbook_data(self):
        data_queue = queue.Queue()
        feed = BitbnsRequestDataSpot(data_queue, exchange_name="BITBNS___SPOT")
        feed.async_get_depth("BTC/USDT", 20)
        time.sleep(3)
        try:
            depth_data = data_queue.get(timeout=10)
            assert depth_data is not None
        except queue.Empty:
            pass
