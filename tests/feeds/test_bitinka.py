"""
Test Bitinka exchange integration.

Run tests:
    pytest tests/feeds/test_bitinka.py -v
"""

import queue
import time
from unittest.mock import Mock

import pytest

# Import registration to auto-register Bitinka
import bt_api_py.exchange_registers.register_bitinka  # noqa: F401
from bt_api_py.containers.exchanges.bitinka_exchange_data import BitinkaExchangeDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_bitinka.spot import BitinkaRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


@pytest.fixture
def mock_feed():
    """Create a Bitinka feed instance with mocked request."""
    data_queue = queue.Queue()
    feed = BitinkaRequestDataSpot(data_queue, exchange_name="BITINKA___SPOT")
    feed.request = Mock(return_value=Mock(spec=RequestData))
    return feed


class TestBitinkaExchangeData:
    """Test Bitinka exchange data configuration."""

    def test_exchange_data_spot_creation(self):
        exchange_data = BitinkaExchangeDataSpot()
        assert exchange_data.exchange_name == "bitinkaSpot"
        assert exchange_data.asset_type == "spot"
        assert hasattr(exchange_data, "rest_url")

    def test_kline_periods(self):
        exchange_data = BitinkaExchangeDataSpot()
        assert "1m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods

    def test_legal_currencies(self):
        exchange_data = BitinkaExchangeDataSpot()
        assert "USDT" in exchange_data.legal_currency
        assert "USD" in exchange_data.legal_currency
        assert "EUR" in exchange_data.legal_currency


class TestBitinkaRequestDataSpot:
    """Test Bitinka REST API request methods."""

    def test_request_data_creation(self):
        data_queue = queue.Queue()
        feed = BitinkaRequestDataSpot(data_queue, exchange_name="BITINKA___SPOT")
        assert feed.exchange_name == "BITINKA___SPOT"
        assert feed.asset_type == "SPOT"

    def test_capabilities(self):
        caps = BitinkaRequestDataSpot._capabilities()
        assert Capability.GET_TICK in caps
        assert Capability.GET_DEPTH in caps
        assert Capability.GET_EXCHANGE_INFO in caps
        assert Capability.GET_BALANCE in caps
        assert Capability.GET_ACCOUNT in caps
        assert Capability.MAKE_ORDER in caps
        assert Capability.CANCEL_ORDER in caps

    def test_convert_symbol(self):
        data_queue = queue.Queue()
        feed = BitinkaRequestDataSpot(data_queue)
        assert feed._convert_symbol("BTC-USD") == "BTC/USD"
        assert feed._convert_symbol("BTC_USDT") == "BTC/USDT"
        assert feed._convert_symbol("BTC/USD") == "BTC/USD"

    def test_get_tick_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_tick("BTC/USD")
        assert "GET" in path
        assert "ticker" in path
        assert params["market"] == "BTC/USD"
        assert extra_data["request_type"] == "get_tick"
        assert extra_data["symbol_name"] == "BTC/USD"

    def test_get_tick_calls_request(self, mock_feed):
        mock_feed.get_tick("BTC/USD")
        assert mock_feed.request.called

    def test_get_depth_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_depth("BTC/USD")
        assert "orderbook" in path
        assert extra_data["request_type"] == "get_depth"

    def test_get_depth_calls_request(self, mock_feed):
        mock_feed.get_depth("BTC/USD")
        assert mock_feed.request.called

    def test_get_trades_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_trades("BTC/USD")
        assert "trades" in path
        assert extra_data["request_type"] == "get_trades"

    def test_get_exchange_info_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_exchange_info()
        assert extra_data["request_type"] == "get_exchange_info"

    def test_get_server_time_tuple(self):
        data_queue = queue.Queue()
        feed = BitinkaRequestDataSpot(data_queue, exchange_name="BITINKA___SPOT")
        path, params, extra_data = feed._get_server_time()
        assert extra_data["request_type"] == "get_server_time"


class TestBitinkaStandardInterfaces:
    """Test standard Feed interface methods for Bitinka."""

    @pytest.fixture
    def feed(self):
        data_queue = queue.Queue()
        feed = BitinkaRequestDataSpot(data_queue, exchange_name="BITINKA___SPOT")
        feed.request = Mock(return_value=Mock(spec=RequestData))
        return feed

    def test_make_order_calls_request(self, feed):
        feed.make_order("BTC/USD", 0.01, 50000, "LIMIT", offset="BUY")
        assert feed.request.called
        extra_data = feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "make_order"

    def test_cancel_order_calls_request(self, feed):
        feed.cancel_order("BTC/USD", "order_123")
        assert feed.request.called
        extra_data = feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "cancel_order"

    def test_query_order_calls_request(self, feed):
        feed.query_order("BTC/USD", "order_123")
        assert feed.request.called
        extra_data = feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "query_order"

    def test_get_open_orders_calls_request(self, feed):
        feed.get_open_orders("BTC/USD")
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


class TestBitinkaBaseCapabilities:
    """Test capabilities on the base class."""

    def test_base_capabilities(self):
        from bt_api_py.feeds.live_bitinka.request_base import BitinkaRequestData

        caps = BitinkaRequestData._capabilities()
        assert Capability.GET_TICK in caps
        assert Capability.MAKE_ORDER in caps
        assert Capability.CANCEL_ORDER in caps


class TestBitinkaNormalizeFunctions:
    """Test normalize functions edge cases."""

    def test_tick_normalize_with_none(self):
        result, status = BitinkaRequestDataSpot._get_tick_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_tick_normalize_success(self):
        data = {"data": {"last": "50000"}}
        result, status = BitinkaRequestDataSpot._get_tick_normalize_function(data, None)
        assert status is True
        assert len(result) == 1

    def test_depth_normalize_with_none(self):
        result, status = BitinkaRequestDataSpot._get_depth_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_depth_normalize_success(self):
        data = {"data": {"bids": [], "asks": []}}
        result, status = BitinkaRequestDataSpot._get_depth_normalize_function(data, None)
        assert status is True

    def test_trades_normalize_with_none(self):
        result, status = BitinkaRequestDataSpot._get_trades_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_exchange_info_normalize_with_none(self):
        result, status = BitinkaRequestDataSpot._get_exchange_info_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_account_normalize_with_none(self):
        result, status = BitinkaRequestDataSpot._get_account_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_balance_normalize_with_none(self):
        result, status = BitinkaRequestDataSpot._get_balance_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_deals_normalize_with_none(self):
        result, status = BitinkaRequestDataSpot._get_deals_normalize_function(None, None)
        assert result == []
        assert status is False


class TestBitinkaRegistration:
    """Test Bitinka registration."""

    def test_bitinka_registered(self):
        assert "BITINKA___SPOT" in ExchangeRegistry._feed_classes
        assert ExchangeRegistry._feed_classes["BITINKA___SPOT"] == BitinkaRequestDataSpot

    def test_bitinka_exchange_data_registered(self):
        assert "BITINKA___SPOT" in ExchangeRegistry._exchange_data_classes
        assert ExchangeRegistry._exchange_data_classes["BITINKA___SPOT"] == BitinkaExchangeDataSpot

    def test_bitinka_create_exchange_data(self):
        exchange_data = ExchangeRegistry.create_exchange_data("BITINKA___SPOT")
        assert isinstance(exchange_data, BitinkaExchangeDataSpot)


class TestBitinkaLiveAPI:
    """Live API tests - require network, marked as integration."""

    @pytest.mark.integration
    def test_bitinka_req_tick_data(self):
        data_queue = queue.Queue()
        feed = BitinkaRequestDataSpot(data_queue, exchange_name="BITINKA___SPOT")
        data = feed.get_tick("BTC/USD")
        assert isinstance(data, RequestData)
        data_list = data.get_data()
        assert isinstance(data_list, list)

    @pytest.mark.integration
    def test_bitinka_req_orderbook_data(self):
        data_queue = queue.Queue()
        feed = BitinkaRequestDataSpot(data_queue, exchange_name="BITINKA___SPOT")
        data = feed.get_depth("BTC/USD", 20)
        assert isinstance(data, RequestData)
        data_list = data.get_data()
        assert isinstance(data_list, list)

    @pytest.mark.integration
    def test_bitinka_async_tick_data(self):
        data_queue = queue.Queue()
        feed = BitinkaRequestDataSpot(data_queue, exchange_name="BITINKA___SPOT")
        feed.async_get_tick("BTC/USD", extra_data={"test_async": True})
        time.sleep(3)
        try:
            tick_data = data_queue.get(timeout=10)
            assert tick_data is not None
        except queue.Empty:
            pass

    @pytest.mark.integration
    def test_bitinka_req_account_data(self):
        data_queue = queue.Queue()
        feed = BitinkaRequestDataSpot(data_queue, exchange_name="BITINKA___SPOT")
        data = feed.get_account()
        assert isinstance(data, RequestData)

    @pytest.mark.integration
    def test_bitinka_req_balance_data(self):
        data_queue = queue.Queue()
        feed = BitinkaRequestDataSpot(data_queue, exchange_name="BITINKA___SPOT")
        data = feed.get_balance("BTC")
        assert isinstance(data, RequestData)

    @pytest.mark.integration
    def test_bitinka_req_get_deals(self):
        data_queue = queue.Queue()
        feed = BitinkaRequestDataSpot(data_queue, exchange_name="BITINKA___SPOT")
        data = feed.get_deals("BTC/USD")
        assert isinstance(data, RequestData)
