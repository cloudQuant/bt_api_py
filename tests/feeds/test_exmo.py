"""
Test EXMO exchange integration.

Run tests:
    pytest tests/feeds/test_exmo.py -v
"""

import json
import queue
from unittest.mock import Mock

import pytest

from bt_api_py.containers.exchanges.exmo_exchange_data import ExmoExchangeDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.containers.tickers.exmo_ticker import ExmoRequestTickerData
from bt_api_py.feeds.live_exmo.spot import ExmoRequestDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.registry import ExchangeRegistry

# Import registration to auto-register EXMO
import bt_api_py.exchange_registers.register_exmo  # noqa: F401


@pytest.fixture
def mock_feed():
    """Create an EXMO feed instance with mocked request."""
    data_queue = queue.Queue()
    feed = ExmoRequestDataSpot(data_queue, exchange_name="EXMO___SPOT")
    feed.request = Mock(return_value=Mock(spec=RequestData))
    return feed


class TestExmoExchangeData:
    """Test EXMO exchange data configuration."""

    def test_exchange_data_spot_creation(self):
        exchange_data = ExmoExchangeDataSpot()
        assert exchange_data.asset_type == "spot"
        assert exchange_data.rest_url
        assert exchange_data.wss_url

    def test_kline_periods(self):
        exchange_data = ExmoExchangeDataSpot()
        assert "1m" in exchange_data.kline_periods
        assert "5m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods

    def test_legal_currency(self):
        exchange_data = ExmoExchangeDataSpot()
        assert "USDT" in exchange_data.legal_currency
        assert "USD" in exchange_data.legal_currency
        assert "EUR" in exchange_data.legal_currency
        assert "BTC" in exchange_data.legal_currency


class TestExmoRequestDataSpot:
    """Test EXMO REST API request methods."""

    def test_request_data_creation(self):
        data_queue = queue.Queue()
        feed = ExmoRequestDataSpot(data_queue, exchange_name="EXMO___SPOT")
        assert feed.exchange_name == "EXMO___SPOT"
        assert feed.asset_type == "SPOT"

    def test_capabilities(self):
        caps = ExmoRequestDataSpot._capabilities()
        assert Capability.GET_TICK in caps
        assert Capability.GET_DEPTH in caps
        assert Capability.GET_KLINE in caps
        assert Capability.GET_EXCHANGE_INFO in caps
        assert Capability.GET_SERVER_TIME in caps
        assert Capability.GET_BALANCE in caps
        assert Capability.GET_ACCOUNT in caps
        assert Capability.MAKE_ORDER in caps
        assert Capability.CANCEL_ORDER in caps

    def test_get_tick_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_tick("BTC/USDT")
        assert path == "GET /ticker"
        assert extra_data["request_type"] == "get_tick"
        assert extra_data["symbol_name"] == "BTC/USDT"

    def test_get_tick_calls_request(self, mock_feed):
        mock_feed.get_tick("BTC/USDT")
        assert mock_feed.request.called

    def test_get_depth_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_depth("BTC/USDT", count=20)
        assert path == "GET /order_book"
        assert extra_data["request_type"] == "get_depth"
        assert params["pair"] == "BTC_USDT"

    def test_get_depth_calls_request(self, mock_feed):
        mock_feed.get_depth("BTC/USDT")
        assert mock_feed.request.called

    def test_get_kline_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_kline("BTC/USDT", "1h")
        assert path == "GET /candles_history"
        assert extra_data["request_type"] == "get_kline"
        assert params["symbol"] == "BTC_USDT"

    def test_get_kline_calls_request(self, mock_feed):
        mock_feed.get_kline("BTC/USDT", "1h")
        assert mock_feed.request.called

    def test_get_exchange_info_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_exchange_info()
        assert extra_data["request_type"] == "get_exchange_info"

    def test_get_server_time_tuple(self):
        data_queue = queue.Queue()
        feed = ExmoRequestDataSpot(data_queue, exchange_name="EXMO___SPOT")
        path, params, extra_data = feed._get_server_time()
        assert extra_data["request_type"] == "get_server_time"

    def test_get_server_time_returns_data(self):
        data_queue = queue.Queue()
        feed = ExmoRequestDataSpot(data_queue, exchange_name="EXMO___SPOT")
        result = feed.get_server_time()
        assert result is not None


class TestExmoStandardInterfaces:
    """Test standard Feed interface methods for EXMO."""

    @pytest.fixture
    def feed(self):
        data_queue = queue.Queue()
        feed = ExmoRequestDataSpot(data_queue, exchange_name="EXMO___SPOT")
        feed.request = Mock(return_value=Mock(spec=RequestData))
        return feed

    def test_make_order_calls_request(self, feed):
        feed.make_order("BTC/USDT", 0.01, 50000, "limit", offset="BUY")
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


class TestExmoBaseCapabilities:
    """Test capabilities on the base class."""

    def test_base_capabilities(self):
        from bt_api_py.feeds.live_exmo.request_base import ExmoRequestData
        caps = ExmoRequestData._capabilities()
        assert Capability.GET_TICK in caps
        assert Capability.MAKE_ORDER in caps
        assert Capability.CANCEL_ORDER in caps


class TestExmoNormalizeFunctions:
    """Test normalize functions edge cases."""

    def test_tick_normalize_with_none(self):
        result, status = ExmoRequestDataSpot._get_tick_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_tick_normalize_symbol_match(self):
        input_data = {"BTC_USDT": {"buy_price": "50000.0", "last_trade": "50050.0"}}
        extra_data = {"symbol_name": "BTC/USDT"}
        result, status = ExmoRequestDataSpot._get_tick_normalize_function(input_data, extra_data)
        assert status is True
        assert len(result) == 1
        assert result[0]["symbol"] == "BTC/USDT"

    def test_tick_normalize_no_match(self):
        input_data = {"ETH_USDT": {"buy_price": "3000.0"}}
        extra_data = {"symbol_name": "BTC/USDT"}
        result, status = ExmoRequestDataSpot._get_tick_normalize_function(input_data, extra_data)
        assert result == []
        assert status is True  # API succeeded, just symbol not found

    def test_depth_normalize_with_none(self):
        result, status = ExmoRequestDataSpot._get_depth_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_depth_normalize_symbol_match(self):
        input_data = {"BTC_USDT": {"ask_quantity": "1.0", "bid_quantity": "1.0"}}
        extra_data = {"symbol_name": "BTC/USDT"}
        result, status = ExmoRequestDataSpot._get_depth_normalize_function(input_data, extra_data)
        assert status is True
        assert len(result) == 1

    def test_kline_normalize_with_none(self):
        result, status = ExmoRequestDataSpot._get_kline_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_kline_normalize_list(self):
        input_data = [{"t": 1000, "o": "50000"}]
        result, status = ExmoRequestDataSpot._get_kline_normalize_function(input_data, None)
        assert status is True

    def test_exchange_info_normalize_with_none(self):
        result, status = ExmoRequestDataSpot._get_exchange_info_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_account_normalize_with_none(self):
        result, status = ExmoRequestDataSpot._get_account_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_balance_normalize_with_none(self):
        result, status = ExmoRequestDataSpot._get_balance_normalize_function(None, None)
        assert result == []
        assert status is False


class TestExmoDataContainers:
    """Test EXMO data containers."""

    def test_ticker_container(self):
        ticker_info = json.dumps({
            "buy_price": "50000.0",
            "sell_price": "50100.0",
            "last_trade": "50050.0",
            "high": "51000.0",
            "low": "48000.0",
            "vol": "1234.56",
            "avg": "50000.0",
        })
        ticker = ExmoRequestTickerData(ticker_info, "BTC/USDT", "SPOT", False)
        ticker.init_data()
        assert ticker.get_exchange_name() == "EXMO"
        assert ticker.get_symbol_name() == "BTC/USDT"
        assert ticker.get_last_price() > 0


class TestExmoRegistry:
    """Test EXMO registration."""

    def test_exmo_registered(self):
        assert "EXMO___SPOT" in ExchangeRegistry._feed_classes
        assert ExchangeRegistry._feed_classes["EXMO___SPOT"] == ExmoRequestDataSpot

    def test_exmo_exchange_data_registered(self):
        assert "EXMO___SPOT" in ExchangeRegistry._exchange_data_classes
        assert ExchangeRegistry._exchange_data_classes["EXMO___SPOT"] == ExmoExchangeDataSpot

    def test_exmo_create_feed(self):
        data_queue = queue.Queue()
        feed = ExchangeRegistry.create_feed("EXMO___SPOT", data_queue)
        assert isinstance(feed, ExmoRequestDataSpot)

    def test_exmo_create_exchange_data(self):
        exchange_data = ExchangeRegistry.create_exchange_data("EXMO___SPOT")
        assert isinstance(exchange_data, ExmoExchangeDataSpot)


class TestExmoLiveAPI:
    """Live API tests - require network, marked as integration."""

    @pytest.mark.integration
    def test_exmo_req_tick_data(self):
        data_queue = queue.Queue()
        feed = ExmoRequestDataSpot(data_queue, exchange_name="EXMO___SPOT")
        result = feed.get_tick("BTC/USDT")
        assert isinstance(result, RequestData)

    @pytest.mark.integration
    def test_exmo_req_depth_data(self):
        data_queue = queue.Queue()
        feed = ExmoRequestDataSpot(data_queue, exchange_name="EXMO___SPOT")
        result = feed.get_depth("BTC/USDT", count=20)
        assert isinstance(result, RequestData)

    @pytest.mark.integration
    def test_exmo_req_kline_data(self):
        data_queue = queue.Queue()
        feed = ExmoRequestDataSpot(data_queue, exchange_name="EXMO___SPOT")
        result = feed.get_kline("BTC/USDT", "1h", count=10)
        assert isinstance(result, RequestData)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
