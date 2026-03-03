"""
Test Foxbit exchange integration.

Run tests:
    pytest tests/feeds/test_foxbit.py -v
"""

import json
import queue
from unittest.mock import Mock

import pytest

from bt_api_py.containers.exchanges.foxbit_exchange_data import FoxbitExchangeDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.containers.tickers.foxbit_ticker import FoxbitRequestTickerData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_foxbit.spot import FoxbitRequestDataSpot
from bt_api_py.registry import ExchangeRegistry

# Import registration to auto-register Foxbit
import bt_api_py.feeds.register_foxbit  # noqa: F401


@pytest.fixture
def mock_feed():
    """Create a Foxbit feed instance with mocked request."""
    data_queue = queue.Queue()
    feed = FoxbitRequestDataSpot(data_queue, exchange_name="FOXBIT___SPOT")
    feed.request = Mock(return_value=Mock(spec=RequestData))
    return feed


class TestFoxbitExchangeData:
    """Test Foxbit exchange data configuration."""

    def test_exchange_data_spot_creation(self):
        exchange_data = FoxbitExchangeDataSpot()
        assert exchange_data.asset_type == "spot"
        assert exchange_data.rest_url
        assert exchange_data.wss_url

    def test_kline_periods(self):
        exchange_data = FoxbitExchangeDataSpot()
        assert "1m" in exchange_data.kline_periods
        assert "5m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods

    def test_legal_currency(self):
        exchange_data = FoxbitExchangeDataSpot()
        assert "BRL" in exchange_data.legal_currency


class TestFoxbitRequestDataSpot:
    """Test Foxbit REST API request methods."""

    def test_request_data_creation(self):
        data_queue = queue.Queue()
        feed = FoxbitRequestDataSpot(data_queue, exchange_name="FOXBIT___SPOT")
        assert feed.exchange_name == "FOXBIT___SPOT"
        assert feed.asset_type == "SPOT"

    def test_capabilities(self):
        caps = FoxbitRequestDataSpot._capabilities()
        assert Capability.GET_TICK in caps
        assert Capability.GET_DEPTH in caps
        assert Capability.GET_KLINE in caps
        assert Capability.GET_EXCHANGE_INFO in caps
        assert Capability.GET_BALANCE in caps
        assert Capability.GET_ACCOUNT in caps
        assert Capability.MAKE_ORDER in caps
        assert Capability.CANCEL_ORDER in caps

    def test_format_market(self, mock_feed):
        assert mock_feed._format_market("BTC/BRL") == "btcbrl"
        assert mock_feed._format_market("BTC-BRL") == "btcbrl"
        assert mock_feed._format_market("btcbrl") == "btcbrl"

    def test_get_tick_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_tick("BTC/BRL")
        assert "btcbrl" in path
        assert "/ticker/24hr" in path
        assert extra_data["request_type"] == "get_tick"
        assert extra_data["symbol_name"] == "BTC/BRL"

    def test_get_tick_calls_request(self, mock_feed):
        mock_feed.get_tick("BTC/BRL")
        assert mock_feed.request.called

    def test_get_depth_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_depth("BTC/BRL", count=20)
        assert "btcbrl" in path
        assert "/orderbook" in path
        assert extra_data["request_type"] == "get_depth"

    def test_get_depth_calls_request(self, mock_feed):
        mock_feed.get_depth("BTC/BRL")
        assert mock_feed.request.called

    def test_get_kline_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_kline("BTC/BRL", "1h")
        assert "btcbrl" in path
        assert "/candlesticks" in path
        assert extra_data["request_type"] == "get_kline"
        assert params["interval"] == "1h"

    def test_get_kline_calls_request(self, mock_feed):
        mock_feed.get_kline("BTC/BRL", "1h")
        assert mock_feed.request.called

    def test_get_exchange_info_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_exchange_info()
        assert extra_data["request_type"] == "get_exchange_info"

    def test_get_server_time_tuple(self):
        data_queue = queue.Queue()
        feed = FoxbitRequestDataSpot(data_queue, exchange_name="FOXBIT___SPOT")
        path, params, extra_data = feed._get_server_time()
        assert extra_data["request_type"] == "get_server_time"


class TestFoxbitStandardInterfaces:
    """Test standard Feed interface methods for Foxbit."""

    @pytest.fixture
    def feed(self):
        data_queue = queue.Queue()
        feed = FoxbitRequestDataSpot(data_queue, exchange_name="FOXBIT___SPOT")
        feed.request = Mock(return_value=Mock(spec=RequestData))
        return feed

    def test_make_order_calls_request(self, feed):
        feed.make_order("BTC/BRL", 0.01, 250000, "limit", offset="BUY")
        assert feed.request.called
        extra_data = feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "make_order"

    def test_cancel_order_calls_request(self, feed):
        feed.cancel_order("BTC/BRL", "order_123")
        assert feed.request.called
        extra_data = feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "cancel_order"

    def test_query_order_calls_request(self, feed):
        feed.query_order("BTC/BRL", "order_123")
        assert feed.request.called
        extra_data = feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "query_order"

    def test_get_open_orders_calls_request(self, feed):
        feed.get_open_orders("BTC/BRL")
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


class TestFoxbitBaseCapabilities:
    """Test capabilities on the base class."""

    def test_base_capabilities(self):
        from bt_api_py.feeds.live_foxbit.request_base import FoxbitRequestData
        caps = FoxbitRequestData._capabilities()
        assert Capability.GET_TICK in caps
        assert Capability.MAKE_ORDER in caps
        assert Capability.CANCEL_ORDER in caps


class TestFoxbitNormalizeFunctions:
    """Test normalize functions edge cases."""

    def test_tick_normalize_with_none(self):
        result, status = FoxbitRequestDataSpot._get_tick_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_tick_normalize_with_dict(self):
        input_data = {"data": {"marketSymbol": "BTCBRL", "lastPrice": "250000.50"}}
        extra = {"symbol_name": "BTC/BRL", "asset_type": "SPOT"}
        result, status = FoxbitRequestDataSpot._get_tick_normalize_function(input_data, extra)
        assert status is True
        assert len(result) == 1

    def test_tick_normalize_with_list(self):
        input_data = {"data": [{"marketSymbol": "BTCBRL", "lastPrice": "250000.50"}]}
        extra = {"symbol_name": "BTC/BRL", "asset_type": "SPOT"}
        result, status = FoxbitRequestDataSpot._get_tick_normalize_function(input_data, extra)
        assert status is True
        assert len(result) == 1

    def test_depth_normalize_with_none(self):
        result, status = FoxbitRequestDataSpot._get_depth_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_depth_normalize_with_dict(self):
        input_data = {"data": {"bids": [], "asks": []}}
        result, status = FoxbitRequestDataSpot._get_depth_normalize_function(input_data, None)
        assert status is True

    def test_kline_normalize_with_none(self):
        result, status = FoxbitRequestDataSpot._get_kline_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_kline_normalize_with_list(self):
        input_data = {"data": [{"open": "250000", "close": "251000"}]}
        result, status = FoxbitRequestDataSpot._get_kline_normalize_function(input_data, None)
        assert status is True

    def test_exchange_info_normalize_with_none(self):
        result, status = FoxbitRequestDataSpot._get_exchange_info_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_account_normalize_with_none(self):
        result, status = FoxbitRequestDataSpot._get_account_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_balance_normalize_with_none(self):
        result, status = FoxbitRequestDataSpot._get_balance_normalize_function(None, None)
        assert result == []
        assert status is False


class TestFoxbitDataContainers:
    """Test Foxbit data containers."""

    def test_ticker_container(self):
        ticker_info = json.dumps({
            "data": {
                "marketSymbol": "BTCBRL",
                "lastPrice": "250000.50",
                "bidPrice": "249900.00",
                "askPrice": "250000.50",
                "vol": "12.345",
                "highPrice": "255000.00",
                "lowPrice": "245000.00",
                "volQuote": "3000000",
            }
        })
        ticker = FoxbitRequestTickerData(ticker_info, "BTC/BRL", "SPOT", False)
        ticker.init_data()
        assert ticker.exchange_name == "FOXBIT"
        assert ticker.symbol_name == "BTC/BRL"
        assert ticker.last_price == 250000.50


class TestFoxbitRegistry:
    """Test Foxbit registration."""

    def test_foxbit_registered(self):
        assert "FOXBIT___SPOT" in ExchangeRegistry._feed_classes
        assert ExchangeRegistry._feed_classes["FOXBIT___SPOT"] == FoxbitRequestDataSpot

    def test_foxbit_exchange_data_registered(self):
        assert "FOXBIT___SPOT" in ExchangeRegistry._exchange_data_classes
        assert ExchangeRegistry._exchange_data_classes["FOXBIT___SPOT"] == FoxbitExchangeDataSpot

    def test_foxbit_create_feed(self):
        data_queue = queue.Queue()
        feed = ExchangeRegistry.create_feed("FOXBIT___SPOT", data_queue)
        assert isinstance(feed, FoxbitRequestDataSpot)

    def test_foxbit_create_exchange_data(self):
        exchange_data = ExchangeRegistry.create_exchange_data("FOXBIT___SPOT")
        assert isinstance(exchange_data, FoxbitExchangeDataSpot)


class TestFoxbitLiveAPI:
    """Live API tests - require network, marked as integration."""

    @pytest.mark.integration
    def test_foxbit_req_tick_data(self):
        data_queue = queue.Queue()
        feed = FoxbitRequestDataSpot(data_queue, exchange_name="FOXBIT___SPOT")
        result = feed.get_tick("BTC/BRL")
        assert isinstance(result, RequestData)

    @pytest.mark.integration
    def test_foxbit_req_depth_data(self):
        data_queue = queue.Queue()
        feed = FoxbitRequestDataSpot(data_queue, exchange_name="FOXBIT___SPOT")
        result = feed.get_depth("BTC/BRL")
        assert isinstance(result, RequestData)

    @pytest.mark.integration
    def test_foxbit_req_kline_data(self):
        data_queue = queue.Queue()
        feed = FoxbitRequestDataSpot(data_queue, exchange_name="FOXBIT___SPOT")
        result = feed.get_kline("BTC/BRL", "1h", count=10)
        assert isinstance(result, RequestData)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
