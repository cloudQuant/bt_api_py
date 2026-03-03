"""
Ripio Exchange Integration Tests

Run tests:
    pytest tests/feeds/test_ripio.py -v
"""

import json
import queue
from unittest.mock import Mock

import pytest

from bt_api_py.containers.exchanges.ripio_exchange_data import (
    RipioExchangeData,
    RipioExchangeDataSpot,
)
from bt_api_py.containers.tickers.ripio_ticker import RipioRequestTickerData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_ripio.spot import RipioRequestDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.registry import ExchangeRegistry

# Import registration to auto-register Ripio
import bt_api_py.feeds.register_ripio  # noqa: F401


@pytest.fixture
def mock_feed():
    """Create a Ripio feed instance with mocked request."""
    data_queue = queue.Queue()
    feed = RipioRequestDataSpot(data_queue)
    feed.request = Mock(return_value=Mock())
    return feed


class TestRipioExchangeData:
    """Test Ripio exchange data configuration."""

    def test_exchange_data_base_initialization(self):
        data = RipioExchangeData()
        assert data.exchange_name == "ripio"
        assert data.rest_url == "https://api.exchange.ripio.com"
        assert data.wss_url == "wss://api.exchange.ripio.com/ws"
        assert isinstance(data.kline_periods, dict)
        assert "1h" in data.kline_periods
        assert "ARS" in data.legal_currency
        assert "BRL" in data.legal_currency
        assert "USDT" in data.legal_currency

    def test_get_symbol_conversion(self):
        data = RipioExchangeDataSpot()
        assert data.get_symbol("BTC/USDT") == "BTC_USDT"
        assert data.get_symbol("ETH-USDT") == "ETH_USDT"
        assert data.get_symbol("btc_usdt") == "BTC_USDT"

    def test_get_period_conversion(self):
        data = RipioExchangeDataSpot()
        assert data.get_period("1m") == "1"
        assert data.get_period("1h") == "60"
        assert data.get_period("1d") == "1440"

    def test_legal_currencies(self):
        data = RipioExchangeData()
        assert "ARS" in data.legal_currency
        assert "BRL" in data.legal_currency
        assert "MXN" in data.legal_currency


class TestRipioRequestDataSpot:
    """Test Ripio spot request feed."""

    def test_request_data_initialization(self):
        data_queue = queue.Queue()
        feed = RipioRequestDataSpot(data_queue)
        assert feed.exchange_name == "RIPIO___SPOT"
        assert feed.asset_type == "SPOT"

    def test_capabilities(self):
        caps = RipioRequestDataSpot._capabilities()
        assert Capability.GET_TICK in caps
        assert Capability.GET_DEPTH in caps
        assert Capability.GET_KLINE in caps
        assert Capability.GET_EXCHANGE_INFO in caps
        assert Capability.GET_BALANCE in caps
        assert Capability.GET_ACCOUNT in caps
        assert Capability.MAKE_ORDER in caps
        assert Capability.CANCEL_ORDER in caps

    def test_get_tick_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_tick("BTC/USDT")
        assert "ticker" in path.lower()
        assert extra_data["request_type"] == "get_tick"
        assert extra_data["symbol_name"] == "BTC/USDT"
        assert extra_data["exchange_name"] == "RIPIO___SPOT"
        assert extra_data["asset_type"] == "SPOT"

    def test_get_depth_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_depth("BTC/USDT", 20)
        assert "orderbook" in path.lower() or "depth" in path.lower()
        assert params["limit"] == 20
        assert extra_data["request_type"] == "get_depth"
        assert extra_data["exchange_name"] == "RIPIO___SPOT"

    def test_get_kline_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_kline("BTC/USDT", "1h", 10)
        assert "candle" in path.lower() or "kline" in path.lower()
        assert params["period"] == "60"
        assert extra_data["request_type"] == "get_kline"
        assert extra_data["exchange_name"] == "RIPIO___SPOT"

    def test_get_exchange_info_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_exchange_info()
        assert path is not None
        assert extra_data["request_type"] == "get_exchange_info"

    def test_get_trades_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_trades("BTC/USDT")
        assert path is not None
        assert extra_data["request_type"] == "get_trades"

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
            "BTC/USDT", "0.001", "50000", "buy-limit"
        )
        assert path is not None
        assert extra_data["request_type"] == "make_order"

    def test_cancel_order_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._cancel_order("BTC/USDT", "order_123")
        assert path is not None
        assert extra_data["request_type"] == "cancel_order"
        assert extra_data["order_id"] == "order_123"

    def test_get_server_time_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_server_time()
        assert path is not None
        assert extra_data["request_type"] == "get_server_time"


class TestRipioStandardInterfaces:
    """Test standard Feed interface methods invoke request()."""

    @pytest.fixture
    def feed(self):
        data_queue = queue.Queue()
        feed = RipioRequestDataSpot(data_queue)
        feed.request = Mock(return_value=Mock())
        return feed

    def test_get_tick_calls_request(self, feed):
        feed.get_tick("BTC/USDT")
        assert feed.request.called

    def test_get_depth_calls_request(self, feed):
        feed.get_depth("BTC/USDT")
        assert feed.request.called

    def test_get_kline_calls_request(self, feed):
        feed.get_kline("BTC/USDT", "1h")
        assert feed.request.called

    def test_get_exchange_info_calls_request(self, feed):
        feed.get_exchange_info()
        assert feed.request.called

    def test_get_trades_calls_request(self, feed):
        feed.get_trades("BTC/USDT")
        assert feed.request.called

    def test_get_balance_calls_request(self, feed):
        feed.get_balance()
        assert feed.request.called

    def test_get_account_calls_request(self, feed):
        feed.get_account()
        assert feed.request.called

    def test_make_order_calls_request(self, feed):
        feed.make_order("BTC/USDT", "0.001", "50000", "buy-limit")
        assert feed.request.called

    def test_cancel_order_calls_request(self, feed):
        feed.cancel_order("BTC/USDT", "order_123")
        assert feed.request.called

    def test_get_server_time_calls_request(self, feed):
        feed.get_server_time()
        assert feed.request.called


class TestRipioNormalizeFunctions:
    """Test normalize functions edge cases."""

    def test_tick_normalize_with_none(self):
        result, status = RipioRequestDataSpot._get_tick_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_tick_normalize_with_data(self):
        input_data = {"success": True, "data": {"lastPrice": "50000.00"}}
        result, status = RipioRequestDataSpot._get_tick_normalize_function(input_data, {})
        assert status is True
        assert result[0]["lastPrice"] == "50000.00"

    def test_tick_normalize_failure(self):
        input_data = {"success": False, "data": {}}
        result, status = RipioRequestDataSpot._get_tick_normalize_function(input_data, {})
        assert result == []
        assert status is False

    def test_depth_normalize_with_none(self):
        result, status = RipioRequestDataSpot._get_depth_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_depth_normalize_with_data(self):
        input_data = {"success": True, "data": {"bids": [], "asks": []}}
        result, status = RipioRequestDataSpot._get_depth_normalize_function(input_data, {})
        assert status is True

    def test_kline_normalize_with_none(self):
        result, status = RipioRequestDataSpot._get_kline_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_kline_normalize_with_data(self):
        input_data = {"success": True, "data": [[1672531200, "50000", "51000"]]}
        result, status = RipioRequestDataSpot._get_kline_normalize_function(input_data, {})
        assert status is True

    def test_exchange_info_normalize_with_none(self):
        result, status = RipioRequestDataSpot._get_exchange_info_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_trades_normalize_with_none(self):
        result, status = RipioRequestDataSpot._get_trades_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_balance_normalize_with_none(self):
        result, status = RipioRequestDataSpot._get_balance_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_account_normalize_with_none(self):
        result, status = RipioRequestDataSpot._get_account_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_make_order_normalize_with_none(self):
        result, status = RipioRequestDataSpot._make_order_normalize_function(None, {})
        assert result == []
        assert status is False

    def test_server_time_normalize_with_none(self):
        result, status = RipioRequestDataSpot._get_server_time_normalize_function(None, None)
        assert result is None
        assert status is False

    def test_server_time_normalize_with_data(self):
        input_data = {"data": {"timestamp": 1678901234}}
        result, status = RipioRequestDataSpot._get_server_time_normalize_function(input_data, {})
        assert status is True


class TestRipioDataContainers:
    """Test Ripio data containers."""

    def test_ticker_container(self):
        ticker_response = {
            "success": True,
            "data": {
                "symbol": "BTC_USDT",
                "last": "50000.00",
                "high": "51000.00",
                "low": "49000.00",
                "bid": "49999.00",
                "ask": "50001.00",
                "volume": "1000.00",
            }
        }
        ticker = RipioRequestTickerData(ticker_response, "BTC/USDT", "SPOT")
        assert ticker.symbol == "BTC_USDT"
        assert ticker.last_price == 50000.0


class TestRipioRegistration:
    """Test Ripio registration module."""

    def test_registration(self):
        assert ExchangeRegistry.has_exchange("RIPIO___SPOT")
        feed_class = ExchangeRegistry._feed_classes.get("RIPIO___SPOT")
        assert feed_class == RipioRequestDataSpot
        data_class = ExchangeRegistry._exchange_data_classes.get("RIPIO___SPOT")
        assert data_class == RipioExchangeDataSpot

    def test_create_feed(self):
        data_queue = queue.Queue()
        feed = ExchangeRegistry.create_feed("RIPIO___SPOT", data_queue)
        assert isinstance(feed, RipioRequestDataSpot)

    def test_create_exchange_data(self):
        data = ExchangeRegistry.create_exchange_data("RIPIO___SPOT")
        assert isinstance(data, RipioExchangeDataSpot)


class TestRipioLiveAPI:
    """Live API tests - require network, marked as integration."""

    @pytest.mark.integration
    def test_ripio_req_tick_data(self):
        data_queue = queue.Queue()
        feed = RipioRequestDataSpot(data_queue)
        data = feed.get_tick("BTC/USDT")
        assert isinstance(data, RequestData)

    @pytest.mark.integration
    def test_ripio_req_depth_data(self):
        data_queue = queue.Queue()
        feed = RipioRequestDataSpot(data_queue)
        data = feed.get_depth("BTC/USDT")
        assert isinstance(data, RequestData)

    @pytest.mark.integration
    def test_ripio_req_kline_data(self):
        data_queue = queue.Queue()
        feed = RipioRequestDataSpot(data_queue)
        data = feed.get_kline("BTC/USDT", "1h")
        assert isinstance(data, RequestData)

    @pytest.mark.integration
    def test_ripio_server_time(self):
        data_queue = queue.Queue()
        feed = RipioRequestDataSpot(data_queue)
        data = feed.get_server_time()
        assert isinstance(data, RequestData)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
