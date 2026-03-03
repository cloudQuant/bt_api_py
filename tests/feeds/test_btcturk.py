"""
Test BTCTurk exchange integration.

Run tests:
    pytest tests/feeds/test_btcturk.py -v
"""

import json
import queue
import time
from unittest.mock import Mock, MagicMock

import pytest

from bt_api_py.containers.exchanges.btcturk_exchange_data import (
    BTCTurkExchangeDataSpot,
)
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.containers.tickers.btcturk_ticker import BTCTurkRequestTickerData
from bt_api_py.feeds.live_btcturk.spot import BTCTurkRequestDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.registry import ExchangeRegistry

# Import registration to auto-register BTCTurk
import bt_api_py.feeds.register_btcturk  # noqa: F401


@pytest.fixture
def mock_feed():
    """Create a BTCTurk feed instance with mocked request."""
    data_queue = queue.Queue()
    feed = BTCTurkRequestDataSpot(data_queue, exchange_name="BTCTURK___SPOT")
    feed.request = Mock(return_value=Mock(spec=RequestData))
    return feed


class TestBTCTurkExchangeData:
    """Test BTCTurk exchange data configuration."""

    def test_exchange_data_spot_creation(self):
        exchange_data = BTCTurkExchangeDataSpot()
        assert exchange_data.exchange_name == "btcturkSpot"
        assert exchange_data.asset_type == "spot"
        assert exchange_data.rest_url == "https://api.btcturk.com"
        assert exchange_data.wss_url == "wss://ws-feed-pro.btcturk.com"

    def test_kline_periods(self):
        exchange_data = BTCTurkExchangeDataSpot()
        assert "1m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods
        assert exchange_data.kline_periods["1h"] == "60"
        assert exchange_data.kline_periods["1d"] == "1440"

    def test_legal_currencies(self):
        exchange_data = BTCTurkExchangeDataSpot()
        assert "USDT" in exchange_data.legal_currency
        assert "TRY" in exchange_data.legal_currency
        assert "BTC" in exchange_data.legal_currency


class TestBTCTurkRequestDataSpot:
    """Test BTCTurk REST API request methods."""

    def test_request_data_creation(self):
        data_queue = queue.Queue()
        feed = BTCTurkRequestDataSpot(data_queue, exchange_name="BTCTURK___SPOT")
        assert feed.exchange_name == "BTCTURK___SPOT"
        assert feed.asset_type == "SPOT"

    def test_capabilities(self):
        caps = BTCTurkRequestDataSpot._capabilities()
        assert Capability.GET_TICK in caps
        assert Capability.GET_DEPTH in caps
        assert Capability.GET_KLINE in caps
        assert Capability.GET_EXCHANGE_INFO in caps
        assert Capability.GET_BALANCE in caps
        assert Capability.GET_ACCOUNT in caps
        assert Capability.MAKE_ORDER in caps
        assert Capability.CANCEL_ORDER in caps

    def test_get_tick_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_tick("BTCUSDT")
        assert "ticker" in path
        assert params["pairSymbol"] == "BTCUSDT"
        assert extra_data["request_type"] == "get_tick"
        assert extra_data["symbol_name"] == "BTCUSDT"

    def test_get_tick_calls_request(self, mock_feed):
        mock_feed.get_tick("BTCUSDT")
        assert mock_feed.request.called

    def test_get_depth_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_depth("BTCUSDT")
        assert "orderbook" in path
        assert extra_data["request_type"] == "get_depth"

    def test_get_depth_calls_request(self, mock_feed):
        mock_feed.get_depth("BTCUSDT")
        assert mock_feed.request.called

    def test_get_kline_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_kline("BTCUSDT", "1h")
        assert "ohlcs" in path
        assert extra_data["request_type"] == "get_kline"
        assert "from" in params
        assert "to" in params

    def test_get_kline_calls_request(self, mock_feed):
        mock_feed.get_kline("BTCUSDT", "1h")
        assert mock_feed.request.called

    def test_get_exchange_info_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_exchange_info()
        assert extra_data["request_type"] == "get_exchange_info"

    def test_get_server_time_tuple(self):
        data_queue = queue.Queue()
        feed = BTCTurkRequestDataSpot(data_queue, exchange_name="BTCTURK___SPOT")
        path, params, extra_data = feed._get_server_time()
        assert extra_data["request_type"] == "get_server_time"

    def test_get_trades_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_trades("BTCUSDT")
        assert "trades" in path
        assert extra_data["request_type"] == "get_trades"


class TestBTCTurkStandardInterfaces:
    """Test standard Feed interface methods for BTCTurk."""

    @pytest.fixture
    def feed(self):
        data_queue = queue.Queue()
        feed = BTCTurkRequestDataSpot(data_queue, exchange_name="BTCTURK___SPOT")
        feed.request = Mock(return_value=Mock(spec=RequestData))
        return feed

    def test_make_order_calls_request(self, feed):
        feed.make_order("BTCUSDT", 0.01, 50000, "limit", offset="BUY")
        assert feed.request.called
        extra_data = feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "make_order"

    def test_cancel_order_calls_request(self, feed):
        feed.cancel_order("BTCUSDT", "order_123")
        assert feed.request.called
        extra_data = feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "cancel_order"

    def test_query_order_calls_request(self, feed):
        feed.query_order("BTCUSDT", "order_123")
        assert feed.request.called
        extra_data = feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "query_order"

    def test_get_open_orders_calls_request(self, feed):
        feed.get_open_orders("BTCUSDT")
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


class TestBTCTurkBaseCapabilities:
    """Test capabilities on the base class."""

    def test_base_capabilities(self):
        from bt_api_py.feeds.live_btcturk.request_base import BTCTurkRequestData
        caps = BTCTurkRequestData._capabilities()
        assert Capability.GET_TICK in caps
        assert Capability.MAKE_ORDER in caps
        assert Capability.CANCEL_ORDER in caps


class TestBTCTurkNormalizeFunctions:
    """Test normalize functions edge cases."""

    def test_tick_normalize_with_none(self):
        result, status = BTCTurkRequestDataSpot._get_tick_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_tick_normalize_success(self):
        data = {"data": [{"pairSymbol": "BTCUSDT", "last": "50000"}]}
        result, status = BTCTurkRequestDataSpot._get_tick_normalize_function(data, None)
        assert status is True
        assert len(result) == 1

    def test_depth_normalize_with_none(self):
        result, status = BTCTurkRequestDataSpot._get_depth_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_depth_normalize_success(self):
        data = {"data": {"bids": [["49990", "1.0"]], "asks": [["50010", "1.0"]]}}
        result, status = BTCTurkRequestDataSpot._get_depth_normalize_function(data, None)
        assert status is True

    def test_kline_normalize_with_none(self):
        result, status = BTCTurkRequestDataSpot._get_kline_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_kline_normalize_success(self):
        data = {"data": [[1640995200000, "49500", "51000", "49000", "50000"]]}
        result, status = BTCTurkRequestDataSpot._get_kline_normalize_function(data, None)
        assert status is True

    def test_exchange_info_normalize_with_none(self):
        result, status = BTCTurkRequestDataSpot._get_exchange_info_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_account_normalize_with_none(self):
        result, status = BTCTurkRequestDataSpot._get_account_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_balance_normalize_with_none(self):
        result, status = BTCTurkRequestDataSpot._get_balance_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_trades_normalize_with_none(self):
        result, status = BTCTurkRequestDataSpot._get_trades_normalize_function(None, None)
        assert result == []
        assert status is False


class TestBTCTurkDataContainers:
    """Test BTCTurk data containers."""

    def test_ticker_container(self):
        ticker_response = json.dumps({
            "data": [{
                "pairSymbol": "BTCUSDT",
                "last": "50000.00",
                "bid": "49900.00",
                "ask": "50100.00",
                "volume": "100.50",
                "high": "51000.00",
                "low": "48000.00",
                "timestamp": 1640995200000,
            }]
        })
        ticker = BTCTurkRequestTickerData(
            ticker_response, "BTCUSDT", "SPOT", False
        )
        ticker.init_data()
        assert ticker.get_exchange_name() == "BTCTURK"
        assert ticker.symbol_name == "BTCUSDT"
        assert ticker.last_price == 50000.00
        assert ticker.bid_price == 49900.00
        assert ticker.ask_price == 50100.00
        assert ticker.volume_24h == 100.50

    def test_ticker_container_with_dict_data(self):
        ticker_response = json.dumps({
            "data": {
                "pairSymbol": "ETHUSDT",
                "last": "3000.00",
                "bid": "2990.00",
                "ask": "3010.00",
            }
        })
        ticker = BTCTurkRequestTickerData(
            ticker_response, "ETHUSDT", "SPOT", False
        )
        ticker.init_data()
        assert ticker.last_price == 3000.00
        assert ticker.bid_price == 2990.00
        assert ticker.ask_price == 3010.00


class TestBTCTurkRegistry:
    """Test BTCTurk registration."""

    def test_btcturk_registered(self):
        assert "BTCTURK___SPOT" in ExchangeRegistry._feed_classes
        assert ExchangeRegistry._feed_classes["BTCTURK___SPOT"] == BTCTurkRequestDataSpot

    def test_btcturk_exchange_data_registered(self):
        assert "BTCTURK___SPOT" in ExchangeRegistry._exchange_data_classes
        assert ExchangeRegistry._exchange_data_classes["BTCTURK___SPOT"] == BTCTurkExchangeDataSpot

    def test_btcturk_create_feed(self):
        data_queue = queue.Queue()
        feed = ExchangeRegistry.create_feed("BTCTURK___SPOT", data_queue)
        assert isinstance(feed, BTCTurkRequestDataSpot)

    def test_btcturk_create_exchange_data(self):
        exchange_data = ExchangeRegistry.create_exchange_data("BTCTURK___SPOT")
        assert isinstance(exchange_data, BTCTurkExchangeDataSpot)


class TestBTCTurkLiveAPI:
    """Live API tests - require network, marked as integration."""

    @pytest.mark.integration
    def test_btcturk_req_tick_data(self):
        data_queue = queue.Queue()
        feed = BTCTurkRequestDataSpot(data_queue, exchange_name="BTCTURK___SPOT")
        data = feed.get_tick("BTCUSDT")
        assert isinstance(data, RequestData)

    @pytest.mark.integration
    def test_btcturk_req_depth_data(self):
        data_queue = queue.Queue()
        feed = BTCTurkRequestDataSpot(data_queue, exchange_name="BTCTURK___SPOT")
        data = feed.get_depth("BTCUSDT", 20)
        assert isinstance(data, RequestData)

    @pytest.mark.integration
    def test_btcturk_async_tick_data(self):
        data_queue = queue.Queue()
        feed = BTCTurkRequestDataSpot(data_queue, exchange_name="BTCTURK___SPOT")
        feed.async_get_tick("BTCUSDT")
        time.sleep(3)
        try:
            tick_data = data_queue.get(timeout=10)
            assert tick_data is not None
        except queue.Empty:
            pass
