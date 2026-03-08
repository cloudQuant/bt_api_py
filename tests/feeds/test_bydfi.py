"""
Test BYDFi exchange integration.

Run tests:
    pytest tests/feeds/test_bydfi.py -v
"""

import queue
import time
from unittest.mock import Mock

import pytest

# Import registration to auto-register BYDFi
import bt_api_py.exchange_registers.register_bydfi  # noqa: F401
from bt_api_py.containers.exchanges.bydfi_exchange_data import BYDFiExchangeDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.containers.tickers.bydfi_ticker import BYDFiRequestTickerData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_bydfi.spot import BYDFiRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


@pytest.fixture
def mock_feed():
    """Create a BYDFi feed instance with mocked request."""
    data_queue = queue.Queue()
    feed = BYDFiRequestDataSpot(data_queue, exchange_name="BYDFI___SPOT")
    feed.request = Mock(return_value=Mock(spec=RequestData))
    return feed


class TestBYDFiExchangeData:
    """Test BYDFi exchange data configuration."""

    def test_exchange_data_spot_creation(self):
        exchange_data = BYDFiExchangeDataSpot()
        assert exchange_data.asset_type == "spot"

    def test_get_symbol(self):
        exchange_data = BYDFiExchangeDataSpot()
        assert exchange_data.get_symbol("BTC/USDT") == "BTC-USDT"
        assert exchange_data.get_symbol("BTC-USDT") == "BTC-USDT"

    def test_get_rest_path(self):
        exchange_data = BYDFiExchangeDataSpot()
        path = exchange_data.get_rest_path("get_ticker")
        assert "ticker" in path.lower() or "tick" in path.lower()

    def test_kline_periods(self):
        exchange_data = BYDFiExchangeDataSpot()
        assert "1m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods


class TestBYDFiRequestDataSpot:
    """Test BYDFi REST API request methods."""

    def test_request_data_creation(self):
        data_queue = queue.Queue()
        feed = BYDFiRequestDataSpot(data_queue, exchange_name="BYDFI___SPOT")
        assert feed.exchange_name == "BYDFI___SPOT"
        assert feed.asset_type == "SPOT"

    def test_capabilities(self):
        caps = BYDFiRequestDataSpot._capabilities()
        assert Capability.GET_TICK in caps
        assert Capability.GET_DEPTH in caps
        assert Capability.GET_KLINE in caps
        assert Capability.GET_EXCHANGE_INFO in caps
        assert Capability.GET_BALANCE in caps
        assert Capability.GET_ACCOUNT in caps
        assert Capability.MAKE_ORDER in caps
        assert Capability.CANCEL_ORDER in caps

    def test_get_tick_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_tick("BTC-USDT")
        assert extra_data["request_type"] == "get_tick"
        assert extra_data["symbol_name"] == "BTC-USDT"
        assert "symbol" in params

    def test_get_tick_calls_request(self, mock_feed):
        mock_feed.get_tick("BTC-USDT")
        assert mock_feed.request.called

    def test_get_depth_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_depth("BTC-USDT")
        assert extra_data["request_type"] == "get_depth"
        assert "limit" in params

    def test_get_depth_calls_request(self, mock_feed):
        mock_feed.get_depth("BTC-USDT")
        assert mock_feed.request.called

    def test_get_kline_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_kline("BTC-USDT", "1h")
        assert extra_data["request_type"] == "get_kline"
        assert "interval" in params

    def test_get_kline_calls_request(self, mock_feed):
        mock_feed.get_kline("BTC-USDT", "1h")
        assert mock_feed.request.called

    def test_get_exchange_info_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_exchange_info()
        assert extra_data["request_type"] == "get_exchange_info"

    def test_get_server_time_tuple(self):
        data_queue = queue.Queue()
        feed = BYDFiRequestDataSpot(data_queue, exchange_name="BYDFI___SPOT")
        path, params, extra_data = feed._get_server_time()
        assert extra_data["request_type"] == "get_server_time"

    def test_get_trades_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_trades("BTC-USDT")
        assert extra_data["request_type"] == "get_trades"


class TestBYDFiStandardInterfaces:
    """Test standard Feed interface methods for BYDFi."""

    @pytest.fixture
    def feed(self):
        data_queue = queue.Queue()
        feed = BYDFiRequestDataSpot(data_queue, exchange_name="BYDFI___SPOT")
        feed.request = Mock(return_value=Mock(spec=RequestData))
        return feed

    def test_make_order_calls_request(self, feed):
        feed.make_order("BTC-USDT", 0.01, 50000, "limit", offset="BUY")
        assert feed.request.called
        extra_data = feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "make_order"

    def test_cancel_order_calls_request(self, feed):
        feed.cancel_order("BTC-USDT", "order_123")
        assert feed.request.called
        extra_data = feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "cancel_order"

    def test_query_order_calls_request(self, feed):
        feed.query_order("BTC-USDT", "order_123")
        assert feed.request.called
        extra_data = feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "query_order"

    def test_get_open_orders_calls_request(self, feed):
        feed.get_open_orders("BTC-USDT")
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


class TestBYDFiBaseCapabilities:
    """Test capabilities on the base class."""

    def test_base_capabilities(self):
        from bt_api_py.feeds.live_bydfi.request_base import BYDFiRequestData

        caps = BYDFiRequestData._capabilities()
        assert Capability.GET_TICK in caps
        assert Capability.MAKE_ORDER in caps
        assert Capability.CANCEL_ORDER in caps


class TestBYDFiNormalizeFunctions:
    """Test normalize functions edge cases."""

    def test_tick_normalize_with_none(self):
        result, status = BYDFiRequestDataSpot._get_tick_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_tick_normalize_success(self):
        data = {"data": {"symbol": "BTC-USDT", "price": "50000"}}
        result, status = BYDFiRequestDataSpot._get_tick_normalize_function(data, None)
        assert status is True
        assert len(result) == 1

    def test_depth_normalize_with_none(self):
        result, status = BYDFiRequestDataSpot._get_depth_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_kline_normalize_with_none(self):
        result, status = BYDFiRequestDataSpot._get_kline_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_exchange_info_normalize_with_none(self):
        result, status = BYDFiRequestDataSpot._get_exchange_info_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_account_normalize_with_none(self):
        result, status = BYDFiRequestDataSpot._get_account_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_balance_normalize_with_none(self):
        result, status = BYDFiRequestDataSpot._get_balance_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_trades_normalize_with_none(self):
        result, status = BYDFiRequestDataSpot._get_trades_normalize_function(None, None)
        assert result == []
        assert status is False


class TestBYDFiDataContainers:
    """Test BYDFi data containers."""

    def test_ticker_container(self):
        ticker_response = {
            "code": 0,
            "msg": "success",
            "data": {
                "symbol": "BTC-USDT",
                "price": "50000",
                "bid": "49999",
                "ask": "50001",
                "high": "51000",
                "low": "49000",
                "volume": "1234.56",
                "timestamp": 1234567890,
            },
        }
        ticker = BYDFiRequestTickerData(
            ticker_response, "BTC-USDT", "SPOT", has_been_json_encoded=True
        )
        assert ticker.get_symbol_name() == "BTC-USDT"


class TestBYDFiRegistry:
    """Test BYDFi registration."""

    def test_bydfi_registered(self):
        assert "BYDFI___SPOT" in ExchangeRegistry._feed_classes
        assert ExchangeRegistry._feed_classes["BYDFI___SPOT"] == BYDFiRequestDataSpot

    def test_bydfi_exchange_data_registered(self):
        assert "BYDFI___SPOT" in ExchangeRegistry._exchange_data_classes
        assert ExchangeRegistry._exchange_data_classes["BYDFI___SPOT"] == BYDFiExchangeDataSpot

    def test_bydfi_create_feed(self):
        data_queue = queue.Queue()
        feed = ExchangeRegistry.create_feed("BYDFI___SPOT", data_queue)
        assert isinstance(feed, BYDFiRequestDataSpot)

    def test_bydfi_create_exchange_data(self):
        exchange_data = ExchangeRegistry.create_exchange_data("BYDFI___SPOT")
        assert isinstance(exchange_data, BYDFiExchangeDataSpot)


class TestBYDFiLiveAPI:
    """Live API tests - require network, marked as integration."""

    @pytest.mark.skip(reason="BYDFI API endpoint temporarily unavailable (404)")
    @pytest.mark.integration
    def test_bydfi_req_tick_data(self):
        data_queue = queue.Queue()
        feed = BYDFiRequestDataSpot(data_queue, exchange_name="BYDFI___SPOT")
        data = feed.get_tick("BTC-USDT")
        assert isinstance(data, RequestData)

    @pytest.mark.skip(reason="BYDFI API endpoint temporarily unavailable (404)")
    @pytest.mark.integration
    def test_bydfi_async_tick_data(self):
        data_queue = queue.Queue()
        feed = BYDFiRequestDataSpot(data_queue, exchange_name="BYDFI___SPOT")
        feed.async_get_tick("BTC-USDT")
        time.sleep(3)
        try:
            tick_data = data_queue.get(timeout=10)
            assert tick_data is not None
        except queue.Empty:
            pass
