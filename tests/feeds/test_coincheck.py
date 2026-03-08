"""
Test Coincheck exchange integration.

Run tests:
    pytest tests/feeds/test_coincheck.py -v
"""

import json
import queue
from unittest.mock import Mock

import pytest

# Import registration to auto-register Coincheck
import bt_api_py.exchange_registers.register_coincheck  # noqa: F401
from bt_api_py.containers.exchanges.coincheck_exchange_data import (
    CoincheckExchangeDataSpot,
)
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.containers.tickers.coincheck_ticker import CoincheckRequestTickerData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_coincheck.spot import CoincheckRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


@pytest.fixture
def mock_feed():
    """Create a Coincheck feed instance with mocked request."""
    data_queue = queue.Queue()
    feed = CoincheckRequestDataSpot(data_queue, exchange_name="COINCHECK___SPOT")
    feed.request = Mock(return_value=Mock(spec=RequestData))
    return feed


class TestCoincheckExchangeData:
    """Test Coincheck exchange data configuration."""

    def test_exchange_data_spot_creation(self):
        exchange_data = CoincheckExchangeDataSpot()
        assert exchange_data.exchange_name == "coincheckSpot"
        assert exchange_data.asset_type == "spot"
        assert exchange_data.rest_url == "https://coincheck.com"
        assert exchange_data.wss_url == "wss://ws-api.coincheck.com"

    def test_kline_periods(self):
        exchange_data = CoincheckExchangeDataSpot()
        assert "1m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods

    def test_legal_currencies(self):
        exchange_data = CoincheckExchangeDataSpot()
        assert "JPY" in exchange_data.legal_currency


class TestCoincheckRequestDataSpot:
    """Test Coincheck REST API request methods."""

    def test_request_data_creation(self):
        data_queue = queue.Queue()
        feed = CoincheckRequestDataSpot(data_queue, exchange_name="COINCHECK___SPOT")
        assert feed.exchange_name == "COINCHECK___SPOT"
        assert feed.asset_type == "SPOT"

    def test_capabilities(self):
        caps = CoincheckRequestDataSpot._capabilities()
        assert Capability.GET_TICK in caps
        assert Capability.GET_DEPTH in caps
        assert Capability.GET_KLINE in caps
        assert Capability.GET_EXCHANGE_INFO in caps
        assert Capability.GET_BALANCE in caps
        assert Capability.GET_ACCOUNT in caps
        assert Capability.MAKE_ORDER in caps
        assert Capability.CANCEL_ORDER in caps

    def test_get_tick_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_tick("btc_jpy")
        assert path == "GET /api/ticker"
        assert extra_data["request_type"] == "get_tick"
        assert extra_data["symbol_name"] == "btc_jpy"

    def test_get_tick_calls_request(self, mock_feed):
        mock_feed.get_tick("btc_jpy")
        assert mock_feed.request.called

    def test_get_depth_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_depth("btc_jpy")
        assert path == "GET /api/order_books"
        assert extra_data["request_type"] == "get_depth"

    def test_get_depth_calls_request(self, mock_feed):
        mock_feed.get_depth("btc_jpy")
        assert mock_feed.request.called

    def test_get_kline_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_kline("btc_jpy", "1h")
        assert extra_data["request_type"] == "get_kline"
        assert "pair" in params

    def test_get_exchange_info_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_exchange_info()
        assert extra_data["request_type"] == "get_exchange_info"

    def test_get_server_time_tuple(self):
        data_queue = queue.Queue()
        feed = CoincheckRequestDataSpot(data_queue, exchange_name="COINCHECK___SPOT")
        path, params, extra_data = feed._get_server_time()
        assert extra_data["request_type"] == "get_server_time"

    def test_get_trades_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_trades("btc_jpy")
        assert extra_data["request_type"] == "get_trades"


class TestCoincheckStandardInterfaces:
    """Test standard Feed interface methods for Coincheck."""

    @pytest.fixture
    def feed(self):
        data_queue = queue.Queue()
        feed = CoincheckRequestDataSpot(data_queue, exchange_name="COINCHECK___SPOT")
        feed.request = Mock(return_value=Mock(spec=RequestData))
        return feed

    def test_make_order_calls_request(self, feed):
        feed.make_order("btc_jpy", 0.01, 5000000, "limit", offset="BUY")
        assert feed.request.called
        extra_data = feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "make_order"

    def test_cancel_order_calls_request(self, feed):
        feed.cancel_order("btc_jpy", "order_123")
        assert feed.request.called
        extra_data = feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "cancel_order"

    def test_query_order_calls_request(self, feed):
        feed.query_order("btc_jpy", "order_123")
        assert feed.request.called
        extra_data = feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "query_order"

    def test_get_open_orders_calls_request(self, feed):
        feed.get_open_orders("btc_jpy")
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


class TestCoincheckBaseCapabilities:
    """Test capabilities on the base class."""

    def test_base_capabilities(self):
        from bt_api_py.feeds.live_coincheck.request_base import CoincheckRequestData

        caps = CoincheckRequestData._capabilities()
        assert Capability.GET_TICK in caps
        assert Capability.MAKE_ORDER in caps
        assert Capability.CANCEL_ORDER in caps


class TestCoincheckNormalizeFunctions:
    """Test normalize functions edge cases."""

    def test_tick_normalize_with_none(self):
        result, status = CoincheckRequestDataSpot._get_tick_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_tick_normalize_success(self):
        data = {"last": "5000000", "bid": "4990000"}
        result, status = CoincheckRequestDataSpot._get_tick_normalize_function(data, None)
        assert status is True
        assert len(result) == 1

    def test_depth_normalize_with_none(self):
        result, status = CoincheckRequestDataSpot._get_depth_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_kline_normalize_with_none(self):
        result, status = CoincheckRequestDataSpot._get_kline_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_exchange_info_normalize_with_none(self):
        result, status = CoincheckRequestDataSpot._get_exchange_info_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_account_normalize_with_none(self):
        result, status = CoincheckRequestDataSpot._get_account_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_balance_normalize_with_none(self):
        result, status = CoincheckRequestDataSpot._get_balance_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_trades_normalize_with_none(self):
        result, status = CoincheckRequestDataSpot._get_trades_normalize_function(None, None)
        assert result == []
        assert status is False


class TestCoincheckDataContainers:
    """Test Coincheck data containers."""

    def test_ticker_container(self):
        ticker_response = json.dumps(
            {
                "last": "5000000",
                "bid": "4990000",
                "ask": "5010000",
                "volume": "100.50",
                "high": "5100000",
                "low": "4800000",
            }
        )
        ticker = CoincheckRequestTickerData(ticker_response, "btc_jpy", "SPOT", False)
        ticker.init_data()
        assert ticker.get_exchange_name() == "COINCHECK"
        assert ticker.symbol_name == "btc_jpy"
        assert ticker.last_price == 5000000

    def test_ticker_container_with_json_string(self):
        ticker_data = {
            "last": "3000000",
            "bid": "2990000",
            "ask": "3010000",
        }
        ticker_response = json.dumps(ticker_data)
        ticker = CoincheckRequestTickerData(ticker_response, "eth_jpy", "SPOT", False)
        ticker.init_data()
        assert ticker.last_price == 3000000


class TestCoincheckRegistry:
    """Test Coincheck registration."""

    def test_coincheck_registered(self):
        assert "COINCHECK___SPOT" in ExchangeRegistry._feed_classes
        assert ExchangeRegistry._feed_classes["COINCHECK___SPOT"] == CoincheckRequestDataSpot

    def test_coincheck_exchange_data_registered(self):
        assert "COINCHECK___SPOT" in ExchangeRegistry._exchange_data_classes
        assert (
            ExchangeRegistry._exchange_data_classes["COINCHECK___SPOT"] == CoincheckExchangeDataSpot
        )

    def test_coincheck_create_feed(self):
        data_queue = queue.Queue()
        feed = ExchangeRegistry.create_feed("COINCHECK___SPOT", data_queue)
        assert isinstance(feed, CoincheckRequestDataSpot)

    def test_coincheck_create_exchange_data(self):
        exchange_data = ExchangeRegistry.create_exchange_data("COINCHECK___SPOT")
        assert isinstance(exchange_data, CoincheckExchangeDataSpot)


class TestCoincheckLiveAPI:
    """Live API tests - require network, marked as integration."""

    @pytest.mark.integration
    def test_coincheck_req_tick_data(self):
        data_queue = queue.Queue()
        feed = CoincheckRequestDataSpot(data_queue, exchange_name="COINCHECK___SPOT")
        data = feed.get_tick("btc_jpy")
        assert isinstance(data, RequestData)

    @pytest.mark.integration
    def test_coincheck_req_depth_data(self):
        data_queue = queue.Queue()
        feed = CoincheckRequestDataSpot(data_queue, exchange_name="COINCHECK___SPOT")
        data = feed.get_depth("btc_jpy")
        assert isinstance(data, RequestData)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
