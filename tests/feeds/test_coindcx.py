"""
Test CoinDCX exchange integration.

Run tests:
    pytest tests/feeds/test_coindcx.py -v
"""

import json
import queue
import time
from unittest.mock import Mock

import pytest

from bt_api_py.containers.exchanges.coindcx_exchange_data import (
    CoinDCXExchangeDataSpot,
)
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.containers.tickers.coindcx_ticker import CoinDCXRequestTickerData
from bt_api_py.feeds.live_coindcx.spot import CoinDCXRequestDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.registry import ExchangeRegistry

# Import registration to auto-register CoinDCX
import bt_api_py.exchange_registers.register_coindcx  # noqa: F401


@pytest.fixture
def mock_feed():
    """Create a CoinDCX feed instance with mocked request."""
    data_queue = queue.Queue()
    feed = CoinDCXRequestDataSpot(data_queue, exchange_name="COINDCX___SPOT")
    feed.request = Mock(return_value=Mock(spec=RequestData))
    return feed


class TestCoinDCXExchangeData:
    """Test CoinDCX exchange data configuration."""

    def test_exchange_data_spot_creation(self):
        exchange_data = CoinDCXExchangeDataSpot()
        assert exchange_data.exchange_name == "coindcx"
        assert exchange_data.asset_type == "spot"
        assert exchange_data.rest_url == "https://api.coindcx.com"
        assert exchange_data.wss_url == "wss://stream.coindcx.com"

    def test_kline_periods(self):
        exchange_data = CoinDCXExchangeDataSpot()
        assert "1m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods

    def test_legal_currencies(self):
        exchange_data = CoinDCXExchangeDataSpot()
        assert "INR" in exchange_data.legal_currency
        assert "USDT" in exchange_data.legal_currency


class TestCoinDCXRequestDataSpot:
    """Test CoinDCX REST API request methods."""

    def test_request_data_creation(self):
        data_queue = queue.Queue()
        feed = CoinDCXRequestDataSpot(data_queue, exchange_name="COINDCX___SPOT")
        assert feed.exchange_name == "COINDCX___SPOT"
        assert feed.asset_type == "SPOT"

    def test_capabilities(self):
        caps = CoinDCXRequestDataSpot._capabilities()
        assert Capability.GET_TICK in caps
        assert Capability.GET_DEPTH in caps
        assert Capability.GET_KLINE in caps
        assert Capability.GET_EXCHANGE_INFO in caps
        assert Capability.GET_BALANCE in caps
        assert Capability.GET_ACCOUNT in caps
        assert Capability.MAKE_ORDER in caps
        assert Capability.CANCEL_ORDER in caps

    def test_get_tick_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_tick("BTCINR")
        assert path == "GET /exchange/ticker"
        assert extra_data["request_type"] == "get_tick"
        assert extra_data["symbol_name"] == "BTCINR"

    def test_get_tick_calls_request(self, mock_feed):
        mock_feed.get_tick("BTCINR")
        assert mock_feed.request.called

    def test_get_depth_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_depth("BTCINR")
        assert "BTCINR" in path
        assert extra_data["request_type"] == "get_depth"

    def test_get_depth_calls_request(self, mock_feed):
        mock_feed.get_depth("BTCINR")
        assert mock_feed.request.called

    def test_get_kline_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_kline("BTCINR", "1m", 2)
        assert path == "GET /market_data/candles"
        assert params["pair"] == "BTCINR"
        assert extra_data["request_type"] == "get_kline"

    def test_get_kline_calls_request(self, mock_feed):
        mock_feed.get_kline("BTCINR", "1m")
        assert mock_feed.request.called

    def test_get_exchange_info_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_exchange_info()
        assert extra_data["request_type"] == "get_exchange_info"

    def test_get_server_time_tuple(self):
        data_queue = queue.Queue()
        feed = CoinDCXRequestDataSpot(data_queue, exchange_name="COINDCX___SPOT")
        path, params, extra_data = feed._get_server_time()
        assert extra_data["request_type"] == "get_server_time"


class TestCoinDCXStandardInterfaces:
    """Test standard Feed interface methods for CoinDCX."""

    @pytest.fixture
    def feed(self):
        data_queue = queue.Queue()
        feed = CoinDCXRequestDataSpot(data_queue, exchange_name="COINDCX___SPOT")
        feed.request = Mock(return_value=Mock(spec=RequestData))
        return feed

    def test_make_order_calls_request(self, feed):
        feed.make_order("BTCINR", 0.01, 5000000, "limit", offset="BUY")
        assert feed.request.called
        extra_data = feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "make_order"

    def test_cancel_order_calls_request(self, feed):
        feed.cancel_order("BTCINR", "order_123")
        assert feed.request.called
        extra_data = feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "cancel_order"

    def test_query_order_calls_request(self, feed):
        feed.query_order("BTCINR", "order_123")
        assert feed.request.called
        extra_data = feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "query_order"

    def test_get_open_orders_calls_request(self, feed):
        feed.get_open_orders("BTCINR")
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


class TestCoinDCXBaseCapabilities:
    """Test capabilities on the base class."""

    def test_base_capabilities(self):
        from bt_api_py.feeds.live_coindcx.request_base import CoinDCXRequestData
        caps = CoinDCXRequestData._capabilities()
        assert Capability.GET_TICK in caps
        assert Capability.MAKE_ORDER in caps
        assert Capability.CANCEL_ORDER in caps


class TestCoinDCXNormalizeFunctions:
    """Test normalize functions edge cases."""

    def test_tick_normalize_with_none(self):
        result, status = CoinDCXRequestDataSpot._get_tick_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_tick_normalize_no_match(self):
        data = [{"market": "ETHINR", "last_price": "300000"}]
        extra = {"symbol_name": "BTCINR"}
        result, status = CoinDCXRequestDataSpot._get_tick_normalize_function(data, extra)
        assert result == []
        assert status is False

    def test_tick_normalize_success(self):
        data = [{"market": "BTCINR", "last_price": "5000000"}]
        extra = {"symbol_name": "BTCINR"}
        result, status = CoinDCXRequestDataSpot._get_tick_normalize_function(data, extra)
        assert status is True
        assert len(result) == 1

    def test_depth_normalize_with_none(self):
        result, status = CoinDCXRequestDataSpot._get_depth_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_kline_normalize_with_none(self):
        result, status = CoinDCXRequestDataSpot._get_kline_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_exchange_info_normalize_with_none(self):
        result, status = CoinDCXRequestDataSpot._get_exchange_info_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_account_normalize_with_none(self):
        result, status = CoinDCXRequestDataSpot._get_account_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_balance_normalize_with_none(self):
        result, status = CoinDCXRequestDataSpot._get_balance_normalize_function(None, None)
        assert result == []
        assert status is False


class TestCoinDCXDataContainers:
    """Test CoinDCX data containers."""

    def test_ticker_container(self):
        ticker_response = json.dumps({
            "data": {
                "market": "BTCINR",
                "last_price": "5000000",
                "bid": "4990000",
                "ask": "5010000",
                "volume": "100.50",
                "high": "5100000",
                "low": "4800000",
            }
        })
        ticker = CoinDCXRequestTickerData(
            ticker_response, "BTCINR", "SPOT", False
        )
        ticker.init_data()
        assert ticker.get_exchange_name() == "COINDCX"
        assert ticker.symbol_name == "BTCINR"
        assert ticker.last_price == 5000000

    def test_ticker_container_with_json_string(self):
        ticker_data = {
            "data": {
                "market": "ETHINR",
                "last_price": "300000",
                "bid": "299000",
                "ask": "301000",
            }
        }
        ticker_response = json.dumps(ticker_data)
        ticker = CoinDCXRequestTickerData(
            ticker_response, "ETHINR", "SPOT", False
        )
        ticker.init_data()
        assert ticker.last_price == 300000


class TestCoinDCXRegistry:
    """Test CoinDCX registration."""

    def test_coindcx_registered(self):
        assert "COINDCX___SPOT" in ExchangeRegistry._feed_classes
        assert ExchangeRegistry._feed_classes["COINDCX___SPOT"] == CoinDCXRequestDataSpot

    def test_coindcx_exchange_data_registered(self):
        assert "COINDCX___SPOT" in ExchangeRegistry._exchange_data_classes
        assert ExchangeRegistry._exchange_data_classes["COINDCX___SPOT"] == CoinDCXExchangeDataSpot

    def test_coindcx_create_feed(self):
        data_queue = queue.Queue()
        feed = ExchangeRegistry.create_feed("COINDCX___SPOT", data_queue)
        assert isinstance(feed, CoinDCXRequestDataSpot)

    def test_coindcx_create_exchange_data(self):
        exchange_data = ExchangeRegistry.create_exchange_data("COINDCX___SPOT")
        assert isinstance(exchange_data, CoinDCXExchangeDataSpot)


class TestCoinDCXLiveAPI:
    """Live API tests - require network, marked as integration."""

    @pytest.mark.integration
    def test_coindcx_req_tick_data(self):
        data_queue = queue.Queue()
        feed = CoinDCXRequestDataSpot(data_queue, exchange_name="COINDCX___SPOT")
        data = feed.get_tick("BTCINR")
        assert isinstance(data, RequestData)

    @pytest.mark.integration
    def test_coindcx_req_depth_data(self):
        data_queue = queue.Queue()
        feed = CoinDCXRequestDataSpot(data_queue, exchange_name="COINDCX___SPOT")
        data = feed.get_depth("BTCINR")
        assert isinstance(data, RequestData)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
