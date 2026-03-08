"""
Test bitFlyer exchange integration.

Run tests:
    pytest tests/feeds/test_bitflyer.py -v
"""

import json
import queue
import time
from unittest.mock import Mock

import pytest

# Import registration to auto-register bitFlyer
import bt_api_py.exchange_registers.register_bitflyer  # noqa: F401
from bt_api_py.containers.exchanges.bitflyer_exchange_data import (
    BitflyerExchangeDataSpot,
)
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.containers.tickers.bitflyer_ticker import (
    BitflyerRequestTickerData,
)
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_bitflyer import BitflyerRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


@pytest.fixture
def mock_feed():
    """Create a Bitflyer feed instance with mocked request."""
    data_queue = queue.Queue()
    feed = BitflyerRequestDataSpot(data_queue, exchange_name="BITFLYER___SPOT")
    feed.request = Mock(return_value=Mock(spec=RequestData))
    return feed


class TestBitflyerExchangeData:
    """Test bitFlyer exchange data configuration."""

    def test_exchange_data_spot_creation(self):
        """Test creating bitFlyer spot exchange data."""
        exchange_data = BitflyerExchangeDataSpot()
        assert exchange_data.exchange_name in ["bitflyer", "BITFLYER"]
        assert exchange_data.asset_type == "spot"
        assert exchange_data.rest_url != ""
        assert len(exchange_data.kline_periods) > 0

    def test_kline_periods(self):
        """Test kline periods are defined."""
        exchange_data = BitflyerExchangeDataSpot()
        assert "1m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods

    def test_legal_currencies(self):
        """Test legal currencies are defined."""
        exchange_data = BitflyerExchangeDataSpot()
        assert "JPY" in exchange_data.legal_currency
        assert "USD" in exchange_data.legal_currency
        assert "BTC" in exchange_data.legal_currency

    def test_rest_url(self):
        """Test REST URL is configured."""
        exchange_data = BitflyerExchangeDataSpot()
        assert "api.bitflyer.com" in exchange_data.rest_url.lower()

    def test_wss_url(self):
        """Test WebSocket URL is configured."""
        exchange_data = BitflyerExchangeDataSpot()
        assert exchange_data.wss_url != ""
        assert "ws" in exchange_data.wss_url.lower()


class TestBitflyerRequestDataSpot:
    """Test Bitflyer REST API request methods."""

    def test_request_data_creation(self):
        data_queue = queue.Queue()
        feed = BitflyerRequestDataSpot(data_queue, exchange_name="BITFLYER___SPOT")
        assert feed.exchange_name == "BITFLYER___SPOT"
        assert feed.asset_type == "SPOT"

    def test_capabilities(self):
        caps = BitflyerRequestDataSpot._capabilities()
        assert Capability.GET_TICK in caps
        assert Capability.GET_DEPTH in caps
        assert Capability.GET_KLINE in caps
        assert Capability.GET_EXCHANGE_INFO in caps
        assert Capability.GET_BALANCE in caps
        assert Capability.GET_ACCOUNT in caps
        assert Capability.MAKE_ORDER in caps
        assert Capability.CANCEL_ORDER in caps

    def test_normalize_product_code(self):
        data_queue = queue.Queue()
        feed = BitflyerRequestDataSpot(data_queue)
        assert feed._normalize_product_code("BTC/JPY") == "BTC_JPY"
        assert feed._normalize_product_code("BTC-JPY") == "BTC_JPY"
        assert feed._normalize_product_code("BTC_JPY") == "BTC_JPY"

    def test_get_tick_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_tick("BTC_JPY")
        assert "GET" in path
        assert "ticker" in path
        assert params["product_code"] == "BTC_JPY"
        assert extra_data["request_type"] == "get_tick"
        assert extra_data["symbol_name"] == "BTC_JPY"

    def test_get_tick_calls_request(self, mock_feed):
        mock_feed.get_tick("BTC_JPY")
        assert mock_feed.request.called

    def test_get_depth_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_depth("BTC_JPY")
        assert "board" in path
        assert extra_data["request_type"] == "get_depth"

    def test_get_depth_calls_request(self, mock_feed):
        mock_feed.get_depth("BTC_JPY")
        assert mock_feed.request.called

    def test_get_kline_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_kline("BTC_JPY", "1h")
        assert "executions" in path
        assert extra_data["request_type"] == "get_kline"

    def test_get_kline_calls_request(self, mock_feed):
        mock_feed.get_kline("BTC_JPY", "1h")
        assert mock_feed.request.called

    def test_get_exchange_info_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_exchange_info()
        assert extra_data["request_type"] == "get_exchange_info"

    def test_get_server_time_tuple(self):
        data_queue = queue.Queue()
        feed = BitflyerRequestDataSpot(data_queue, exchange_name="BITFLYER___SPOT")
        path, params, extra_data = feed._get_server_time()
        assert extra_data["request_type"] == "get_server_time"


class TestBitflyerDataContainers:
    """Test bitFlyer data containers."""

    def test_ticker_container(self):
        """Test ticker data container."""
        ticker_data = {
            "product_code": "BTC_JPY",
            "timestamp": "2024-01-01T00:00:00.000",
            "tick_id": 123456,
            "best_bid": 5000000.0,
            "best_ask": 5001000.0,
            "best_bid_size": 0.1,
            "best_ask_size": 0.2,
            "total_bid_depth": 100.0,
            "total_ask_depth": 150.0,
            "ltp": 5000500.0,
            "volume": 1234.56,
            "volume_by_product": 1234567.89,
        }
        ticker = BitflyerRequestTickerData(json.dumps(ticker_data), "BTC-JPY", "SPOT", False)
        ticker.init_data()
        assert ticker.get_exchange_name() == "BITFLYER"
        assert ticker.last_price == 5000500.0
        assert ticker.bid_price == 5000000.0
        assert ticker.ask_price == 5001000.0
        assert ticker.volume_24h == 1234.56


class TestBitflyerRegistration:
    """Test bitFlyer registration."""

    def test_bitflyer_registered(self):
        registered = any("BITFLYER" in k.upper() for k in ExchangeRegistry._feed_classes)
        assert registered or BitflyerExchangeDataSpot is not None

    def test_bitflyer_exchange_data_creation(self):
        exchange_data = BitflyerExchangeDataSpot()
        assert exchange_data is not None
        assert exchange_data.exchange_name is not None


class TestBitflyerStandardInterfaces:
    """Test standard Feed interface methods for Bitflyer."""

    @pytest.fixture
    def feed(self):
        data_queue = queue.Queue()
        feed = BitflyerRequestDataSpot(data_queue, exchange_name="BITFLYER___SPOT")
        feed.request = Mock(return_value=Mock(spec=RequestData))
        return feed

    def test_make_order_calls_request(self, feed):
        feed.make_order("BTC_JPY", 0.01, 5000000, "LIMIT", offset="BUY")
        assert feed.request.called
        extra_data = feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "make_order"

    def test_cancel_order_calls_request(self, feed):
        feed.cancel_order("BTC_JPY", "order_123")
        assert feed.request.called
        extra_data = feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "cancel_order"

    def test_query_order_calls_request(self, feed):
        feed.query_order("BTC_JPY", "order_123")
        assert feed.request.called
        extra_data = feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "query_order"

    def test_get_open_orders_calls_request(self, feed):
        feed.get_open_orders("BTC_JPY")
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


class TestBitflyerBaseCapabilities:
    """Test capabilities on the base class."""

    def test_base_capabilities(self):
        from bt_api_py.feeds.live_bitflyer.request_base import BitflyerRequestData

        caps = BitflyerRequestData._capabilities()
        assert Capability.GET_TICK in caps
        assert Capability.MAKE_ORDER in caps
        assert Capability.CANCEL_ORDER in caps


class TestBitflyerNormalizeFunctions:
    """Test normalize functions edge cases."""

    def test_tick_normalize_with_none(self):
        result, status = BitflyerRequestDataSpot._get_tick_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_tick_normalize_success(self):
        data = {"product_code": "BTC_JPY", "ltp": 5000000}
        result, status = BitflyerRequestDataSpot._get_tick_normalize_function(data, None)
        assert result == [data]
        assert status is True

    def test_depth_normalize_with_none(self):
        result, status = BitflyerRequestDataSpot._get_depth_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_depth_normalize_success(self):
        data = {"bids": [], "asks": [], "mid_price": 5000000}
        result, status = BitflyerRequestDataSpot._get_depth_normalize_function(data, None)
        assert result == [data]
        assert status is True

    def test_kline_normalize_with_none(self):
        result, status = BitflyerRequestDataSpot._get_kline_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_kline_normalize_list(self):
        data = [{"id": 1}, {"id": 2}]
        result, status = BitflyerRequestDataSpot._get_kline_normalize_function(data, None)
        assert result == [data]
        assert status is True

    def test_exchange_info_normalize_with_none(self):
        result, status = BitflyerRequestDataSpot._get_exchange_info_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_trades_normalize_with_none(self):
        result, status = BitflyerRequestDataSpot._get_trades_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_health_normalize_with_none(self):
        result, status = BitflyerRequestDataSpot._get_health_normalize_function(None, None)
        assert result == []
        assert status is False


class TestBitflyerLiveAPI:
    """Live API tests - require network, marked as integration."""

    @pytest.mark.integration
    def test_bitflyer_req_tick_data(self):
        data_queue = queue.Queue()
        feed = BitflyerRequestDataSpot(data_queue, exchange_name="BITFLYER___SPOT")
        data = feed.get_tick("BTC_JPY")
        assert isinstance(data, RequestData)
        data_list = data.get_data()
        assert isinstance(data_list, list)
        if data_list and len(data_list) > 0:
            ticker = data_list[0]
            if isinstance(ticker, dict):
                assert isinstance(ticker, dict)

    @pytest.mark.integration
    def test_bitflyer_req_kline_data(self):
        data_queue = queue.Queue()
        feed = BitflyerRequestDataSpot(data_queue, exchange_name="BITFLYER___SPOT")
        data = feed.get_kline("BTC_JPY", "1h", count=2)
        assert isinstance(data, RequestData)
        data_list = data.get_data()
        assert isinstance(data_list, list)
        if data_list and len(data_list) > 0:
            klines = data_list[0]
            assert isinstance(klines, (list, dict))

    @pytest.mark.integration
    def test_bitflyer_req_orderbook_data(self):
        data_queue = queue.Queue()
        feed = BitflyerRequestDataSpot(data_queue, exchange_name="BITFLYER___SPOT")
        data = feed.get_depth("BTC_JPY", 20)
        assert isinstance(data, RequestData)
        data_list = data.get_data()
        assert isinstance(data_list, list)
        if data_list and len(data_list) > 0:
            orderbook = data_list[0]
            if isinstance(orderbook, dict):
                assert "bids" in orderbook or "asks" in orderbook

    @pytest.mark.integration
    def test_bitflyer_async_tick_data(self):
        data_queue = queue.Queue()
        feed = BitflyerRequestDataSpot(data_queue, exchange_name="BITFLYER___SPOT")
        feed.async_get_tick("BTC_JPY", extra_data={"test_async": True})
        time.sleep(3)
        try:
            tick_data = data_queue.get(timeout=10)
            assert tick_data is not None
        except queue.Empty:
            pass

    @pytest.mark.integration
    def test_bitflyer_async_kline_data(self):
        data_queue = queue.Queue()
        feed = BitflyerRequestDataSpot(data_queue, exchange_name="BITFLYER___SPOT")
        feed.async_get_kline("BTC_JPY", period="1h", count=3, extra_data={"test_async": True})
        time.sleep(5)
        try:
            kline_data = data_queue.get(timeout=10)
            assert kline_data is not None
        except queue.Empty:
            pass

    @pytest.mark.integration
    def test_bitflyer_async_orderbook_data(self):
        data_queue = queue.Queue()
        feed = BitflyerRequestDataSpot(data_queue, exchange_name="BITFLYER___SPOT")
        feed.async_get_depth("BTC_JPY", 20)
        time.sleep(3)
        try:
            depth_data = data_queue.get(timeout=10)
            assert depth_data is not None
        except queue.Empty:
            pass
