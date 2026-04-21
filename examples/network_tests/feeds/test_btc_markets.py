"""
Test BTC Markets exchange integration.

Run tests:
    pytest tests/feeds/test_btc_markets.py -v
"""

import json
import queue
import time
from unittest.mock import Mock

import pytest

# Import registration to auto-register BTC Markets
import bt_api_py.exchange_registers.register_btc_markets  # noqa: F401
from bt_api_py.containers.exchanges.btc_markets_exchange_data import (
    BtcMarketsExchangeDataSpot,
)
from bt_api_base.containers.requestdatas.request_data import RequestData
from bt_api_py.containers.tickers.btc_markets_ticker import BtcMarketsRequestTickerData
from bt_api_base.feeds.capability import Capability
from bt_api_py.feeds.live_btc_markets.spot import BtcMarketsRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


@pytest.fixture
def mock_feed():
    """Create a BTC Markets feed instance with mocked request."""
    data_queue = queue.Queue()
    feed = BtcMarketsRequestDataSpot(data_queue, exchange_name="BTC_MARKETS___SPOT")
    feed.request = Mock(return_value=Mock(spec=RequestData))
    return feed


class TestBtcMarketsExchangeData:
    """Test BTC Markets exchange data configuration."""

    def test_exchange_data_spot_creation(self):
        exchange_data = BtcMarketsExchangeDataSpot()
        assert exchange_data.exchange_name == "btc_markets"
        assert exchange_data.asset_type == "spot"
        assert exchange_data.rest_url == "https://api.btcmarkets.net"
        assert exchange_data.wss_url == "wss://socket.btcmarkets.net/v3"

    @pytest.mark.kline
    def test_kline_periods(self):
        exchange_data = BtcMarketsExchangeDataSpot()
        assert "1m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods
        assert exchange_data.kline_periods["1m"] == "1m"
        assert exchange_data.kline_periods["1h"] == "1h"
        assert exchange_data.kline_periods["1d"] == "1d"

    def test_legal_currencies(self):
        exchange_data = BtcMarketsExchangeDataSpot()
        assert "AUD" in exchange_data.legal_currency


class TestBtcMarketsRequestDataSpot:
    """Test BTC Markets REST API request methods."""

    def test_request_data_creation(self):
        data_queue = queue.Queue()
        feed = BtcMarketsRequestDataSpot(data_queue, exchange_name="BTC_MARKETS___SPOT")
        assert feed.exchange_name == "BTC_MARKETS___SPOT"
        assert feed.asset_type == "SPOT"

    def test_capabilities(self):
        caps = BtcMarketsRequestDataSpot._capabilities()
        assert Capability.GET_TICK in caps
        assert Capability.GET_DEPTH in caps
        assert Capability.GET_KLINE in caps
        assert Capability.GET_EXCHANGE_INFO in caps
        assert Capability.GET_BALANCE in caps
        assert Capability.GET_ACCOUNT in caps
        assert Capability.MAKE_ORDER in caps
        assert Capability.CANCEL_ORDER in caps

    @pytest.mark.ticker
    def test_get_tick_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_tick("BTC-AUD")
        assert "GET" in path
        assert "ticker" in path
        assert "BTC-AUD" in path
        assert extra_data["request_type"] == "get_tick"
        assert extra_data["symbol_name"] == "BTC-AUD"

    @pytest.mark.ticker
    def test_get_tick_calls_request(self, mock_feed):
        mock_feed.get_tick("BTC-AUD")
        assert mock_feed.request.called

    @pytest.mark.orderbook
    def test_get_depth_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_depth("BTC-AUD")
        assert "orderbook" in path
        assert extra_data["request_type"] == "get_depth"

    @pytest.mark.orderbook
    def test_get_depth_calls_request(self, mock_feed):
        mock_feed.get_depth("BTC-AUD")
        assert mock_feed.request.called

    @pytest.mark.kline
    def test_get_kline_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_kline("BTC-AUD", "1h")
        assert "candles" in path
        assert extra_data["request_type"] == "get_kline"

    @pytest.mark.kline
    def test_get_kline_calls_request(self, mock_feed):
        mock_feed.get_kline("BTC-AUD", "1h")
        assert mock_feed.request.called

    def test_get_exchange_info_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_exchange_info()
        assert extra_data["request_type"] == "get_exchange_info"

    def test_get_server_time_tuple(self):
        data_queue = queue.Queue()
        feed = BtcMarketsRequestDataSpot(data_queue, exchange_name="BTC_MARKETS___SPOT")
        path, params, extra_data = feed._get_server_time()
        assert extra_data["request_type"] == "get_server_time"


class TestBtcMarketsStandardInterfaces:
    """Test standard Feed interface methods for BTC Markets."""

    @pytest.fixture
    def feed(self):
        data_queue = queue.Queue()
        feed = BtcMarketsRequestDataSpot(data_queue, exchange_name="BTC_MARKETS___SPOT")
        feed.request = Mock(return_value=Mock(spec=RequestData))
        return feed

    def test_make_order_calls_request(self, feed):
        feed.make_order("BTC-AUD", 0.01, 50000, "Limit", offset="BUY")
        assert feed.request.called
        extra_data = feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "make_order"

    def test_cancel_order_calls_request(self, feed):
        feed.cancel_order("BTC-AUD", "order_123")
        assert feed.request.called
        extra_data = feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "cancel_order"

    def test_query_order_calls_request(self, feed):
        feed.query_order("BTC-AUD", "order_123")
        assert feed.request.called
        extra_data = feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "query_order"

    def test_get_open_orders_calls_request(self, feed):
        feed.get_open_orders("BTC-AUD")
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


class TestBtcMarketsBaseCapabilities:
    """Test capabilities on the base class."""

    def test_base_capabilities(self):
        from bt_api_py.feeds.live_btc_markets.request_base import BtcMarketsRequestData

        caps = BtcMarketsRequestData._capabilities()
        assert Capability.GET_TICK in caps
        assert Capability.MAKE_ORDER in caps
        assert Capability.CANCEL_ORDER in caps


class TestBtcMarketsNormalizeFunctions:
    """Test normalize functions edge cases."""

    @pytest.mark.ticker
    def test_tick_normalize_with_none(self):
        result, status = BtcMarketsRequestDataSpot._get_tick_normalize_function(None, None)
        assert result == []
        assert status is False

    @pytest.mark.ticker
    def test_tick_normalize_success(self):
        data = {"marketId": "BTC-AUD", "lastPrice": "50000.00"}
        result, status = BtcMarketsRequestDataSpot._get_tick_normalize_function(data, None)
        assert status is True
        assert len(result) == 1

    @pytest.mark.orderbook
    def test_depth_normalize_with_none(self):
        result, status = BtcMarketsRequestDataSpot._get_depth_normalize_function(None, None)
        assert result == []
        assert status is False

    @pytest.mark.orderbook
    def test_depth_normalize_success(self):
        data = {"bids": [["49990", "1.0"]], "asks": [["50010", "1.0"]]}
        result, status = BtcMarketsRequestDataSpot._get_depth_normalize_function(data, None)
        assert status is True
        assert "bids" in result[0]

    @pytest.mark.kline
    def test_kline_normalize_with_none(self):
        result, status = BtcMarketsRequestDataSpot._get_kline_normalize_function(None, None)
        assert result == []
        assert status is False

    @pytest.mark.kline
    def test_kline_normalize_success(self):
        data = [[1640995200000, "49500", "51000", "49000", "50000", "1234.56"]]
        result, status = BtcMarketsRequestDataSpot._get_kline_normalize_function(data, None)
        assert status is True

    def test_exchange_info_normalize_with_none(self):
        result, status = BtcMarketsRequestDataSpot._get_exchange_info_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_account_normalize_with_none(self):
        result, status = BtcMarketsRequestDataSpot._get_account_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_balance_normalize_with_none(self):
        result, status = BtcMarketsRequestDataSpot._get_balance_normalize_function(None, None)
        assert result == []
        assert status is False


class TestBtcMarketsDataContainers:
    """Test BTC Markets data containers."""

    @pytest.mark.ticker
    def test_ticker_container(self):
        ticker_response = json.dumps(
            {
                "marketId": "BTC-AUD",
                "lastPrice": "50000.00",
                "bestBid": "49900.00",
                "bestAsk": "50100.00",
                "volume24h": "100.50",
                "high24h": "51000.00",
                "low24h": "48000.00",
            }
        )
        ticker = BtcMarketsRequestTickerData(ticker_response, "BTC-AUD", "SPOT", False)
        ticker.init_data()
        assert ticker.get_exchange_name() == "BTC_MARKETS"
        assert ticker.symbol_name == "BTC-AUD"
        assert ticker.last_price == 50000.00
        assert ticker.bid_price == 49900.00
        assert ticker.ask_price == 50100.00
        assert ticker.volume_24h == 100.50
        assert ticker.high_24h == 51000.00
        assert ticker.low_24h == 48000.00

    @pytest.mark.ticker
    def test_ticker_container_with_json_string(self):
        ticker_data = {
            "marketId": "ETH-AUD",
            "lastPrice": "3000.00",
            "bestBid": "2990.00",
            "bestAsk": "3010.00",
        }
        ticker_response = json.dumps(ticker_data)
        ticker = BtcMarketsRequestTickerData(ticker_response, "ETH-AUD", "SPOT", False)
        ticker.init_data()
        assert ticker.last_price == 3000.00
        assert ticker.bid_price == 2990.00
        assert ticker.ask_price == 3010.00


class TestBtcMarketsRegistry:
    """Test BTC Markets registration."""

    def test_btc_markets_registered(self):
        assert "BTC_MARKETS___SPOT" in ExchangeRegistry._feed_classes
        assert ExchangeRegistry._feed_classes["BTC_MARKETS___SPOT"] == BtcMarketsRequestDataSpot

    def test_btc_markets_exchange_data_registered(self):
        assert "BTC_MARKETS___SPOT" in ExchangeRegistry._exchange_data_classes
        assert (
            ExchangeRegistry._exchange_data_classes["BTC_MARKETS___SPOT"]
            == BtcMarketsExchangeDataSpot
        )

    def test_btc_markets_create_feed(self):
        data_queue = queue.Queue()
        feed = ExchangeRegistry.create_feed("BTC_MARKETS___SPOT", data_queue)
        assert isinstance(feed, BtcMarketsRequestDataSpot)

    def test_btc_markets_create_exchange_data(self):
        exchange_data = ExchangeRegistry.create_exchange_data("BTC_MARKETS___SPOT")
        assert isinstance(exchange_data, BtcMarketsExchangeDataSpot)


class TestBtcMarketsLiveAPI:
    """Live API tests - require network, marked as integration."""

    @pytest.mark.integration
    @pytest.mark.ticker
    def test_btc_markets_req_tick_data(self):
        data_queue = queue.Queue()
        feed = BtcMarketsRequestDataSpot(data_queue, exchange_name="BTC_MARKETS___SPOT")
        data = feed.get_tick("BTC-AUD")
        assert isinstance(data, RequestData)

    @pytest.mark.integration
    @pytest.mark.orderbook
    def test_btc_markets_req_depth_data(self):
        data_queue = queue.Queue()
        feed = BtcMarketsRequestDataSpot(data_queue, exchange_name="BTC_MARKETS___SPOT")
        data = feed.get_depth("BTC-AUD", 20)
        assert isinstance(data, RequestData)

    @pytest.mark.integration
    @pytest.mark.ticker
    def test_btc_markets_async_tick_data(self):
        data_queue = queue.Queue()
        feed = BtcMarketsRequestDataSpot(data_queue, exchange_name="BTC_MARKETS___SPOT")
        feed.async_get_tick("BTC-AUD")
        time.sleep(3)
        try:
            tick_data = data_queue.get(timeout=10)
            assert tick_data is not None
        except queue.Empty:
            pass
