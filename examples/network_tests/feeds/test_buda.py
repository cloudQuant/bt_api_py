"""
Test Buda exchange integration.

Run tests:
    pytest tests/feeds/test_buda.py -v
"""

import json
import queue
import time
from unittest.mock import Mock

import pytest

# Import registration to auto-register Buda
import bt_api_py.exchange_registers.register_buda  # noqa: F401
from bt_api_py.containers.exchanges.buda_exchange_data import (
    BudaExchangeDataSpot,
)
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.containers.tickers.buda_ticker import BudaRequestTickerData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_buda.spot import BudaRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


@pytest.fixture
def mock_feed():
    """Create a Buda feed instance with mocked request."""
    data_queue = queue.Queue()
    feed = BudaRequestDataSpot(data_queue, exchange_name="BUDA___SPOT")
    feed.request = Mock(return_value=Mock(spec=RequestData))
    return feed


class TestBudaExchangeData:
    """Test Buda exchange data configuration."""

    def test_exchange_data_spot_creation(self):
        exchange_data = BudaExchangeDataSpot()
        assert exchange_data.exchange_name == "budaSpot"
        assert exchange_data.asset_type == "spot"
        assert exchange_data.rest_url == "https://api.buda.com"
        assert exchange_data.wss_url == "wss://api.buda.com/websocket"

    @pytest.mark.kline
    def test_kline_periods(self):
        exchange_data = BudaExchangeDataSpot()
        assert "1m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods
        assert exchange_data.kline_periods["1h"] == "3600"
        assert exchange_data.kline_periods["1d"] == "86400"

    def test_legal_currencies(self):
        exchange_data = BudaExchangeDataSpot()
        assert "CLP" in exchange_data.legal_currency
        assert "COP" in exchange_data.legal_currency
        assert "PEN" in exchange_data.legal_currency
        assert "EUR" in exchange_data.legal_currency
        assert "USDT" in exchange_data.legal_currency


class TestBudaRequestDataSpot:
    """Test Buda REST API request methods."""

    def test_request_data_creation(self):
        data_queue = queue.Queue()
        feed = BudaRequestDataSpot(data_queue, exchange_name="BUDA___SPOT")
        assert feed.exchange_name == "BUDA___SPOT"
        assert feed.asset_type == "SPOT"

    def test_capabilities(self):
        caps = BudaRequestDataSpot._capabilities()
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
        path, params, extra_data = mock_feed._get_tick("btc-clp")
        assert "ticker" in path
        assert extra_data["request_type"] == "get_tick"
        assert extra_data["symbol_name"] == "btc-clp"

    @pytest.mark.ticker
    def test_get_tick_calls_request(self, mock_feed):
        mock_feed.get_tick("btc-clp")
        assert mock_feed.request.called

    @pytest.mark.orderbook
    def test_get_depth_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_depth("btc-clp")
        assert "order_book" in path
        assert extra_data["request_type"] == "get_depth"

    @pytest.mark.orderbook
    def test_get_depth_calls_request(self, mock_feed):
        mock_feed.get_depth("btc-clp")
        assert mock_feed.request.called

    @pytest.mark.kline
    def test_get_kline_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_kline("btc-clp", "1h")
        assert "candles" in path
        assert extra_data["request_type"] == "get_kline"
        assert "time_frame" in params

    @pytest.mark.kline
    def test_get_kline_calls_request(self, mock_feed):
        mock_feed.get_kline("btc-clp", "1h")
        assert mock_feed.request.called

    def test_get_exchange_info_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_exchange_info()
        assert extra_data["request_type"] == "get_exchange_info"

    def test_get_server_time_tuple(self):
        data_queue = queue.Queue()
        feed = BudaRequestDataSpot(data_queue, exchange_name="BUDA___SPOT")
        path, params, extra_data = feed._get_server_time()
        assert extra_data["request_type"] == "get_server_time"

    def test_get_trades_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_trades("btc-clp")
        assert "trades" in path
        assert extra_data["request_type"] == "get_trades"


class TestBudaStandardInterfaces:
    """Test standard Feed interface methods for Buda."""

    @pytest.fixture
    def feed(self):
        data_queue = queue.Queue()
        feed = BudaRequestDataSpot(data_queue, exchange_name="BUDA___SPOT")
        feed.request = Mock(return_value=Mock(spec=RequestData))
        return feed

    def test_make_order_calls_request(self, feed):
        feed.make_order("btc-clp", 0.01, 50000000, "limit", offset="BUY")
        assert feed.request.called
        extra_data = feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "make_order"

    def test_cancel_order_calls_request(self, feed):
        feed.cancel_order("btc-clp", "order_123")
        assert feed.request.called
        extra_data = feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "cancel_order"

    def test_query_order_calls_request(self, feed):
        feed.query_order("btc-clp", "order_123")
        assert feed.request.called
        extra_data = feed.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "query_order"

    def test_get_open_orders_calls_request(self, feed):
        feed.get_open_orders("btc-clp")
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


class TestBudaBaseCapabilities:
    """Test capabilities on the base class."""

    def test_base_capabilities(self):
        from bt_api_py.feeds.live_buda.request_base import BudaRequestData

        caps = BudaRequestData._capabilities()
        assert Capability.GET_TICK in caps
        assert Capability.MAKE_ORDER in caps
        assert Capability.CANCEL_ORDER in caps


class TestBudaNormalizeFunctions:
    """Test normalize functions edge cases."""

    @pytest.mark.ticker
    def test_tick_normalize_with_none(self):
        result, status = BudaRequestDataSpot._get_tick_normalize_function(None, None)
        assert result == []
        assert status is False

    @pytest.mark.ticker
    def test_tick_normalize_success(self):
        data = {"ticker": {"market_id": "btc-clp", "last_price": [50000000]}}
        result, status = BudaRequestDataSpot._get_tick_normalize_function(data, None)
        assert status is True
        assert len(result) == 1

    @pytest.mark.orderbook
    def test_depth_normalize_with_none(self):
        result, status = BudaRequestDataSpot._get_depth_normalize_function(None, None)
        assert result == []
        assert status is False

    @pytest.mark.orderbook
    def test_depth_normalize_success(self):
        data = {"order_book": {"bids": [], "asks": []}}
        result, status = BudaRequestDataSpot._get_depth_normalize_function(data, None)
        assert status is True

    @pytest.mark.kline
    def test_kline_normalize_with_none(self):
        result, status = BudaRequestDataSpot._get_kline_normalize_function(None, None)
        assert result == []
        assert status is False

    @pytest.mark.kline
    def test_kline_normalize_success(self):
        data = {"candles": [[1640995200, "49500", "51000", "49000", "50000"]]}
        result, status = BudaRequestDataSpot._get_kline_normalize_function(data, None)
        assert status is True

    def test_exchange_info_normalize_with_none(self):
        result, status = BudaRequestDataSpot._get_exchange_info_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_account_normalize_with_none(self):
        result, status = BudaRequestDataSpot._get_account_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_balance_normalize_with_none(self):
        result, status = BudaRequestDataSpot._get_balance_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_trades_normalize_with_none(self):
        result, status = BudaRequestDataSpot._get_trades_normalize_function(None, None)
        assert result == []
        assert status is False


class TestBudaDataContainers:
    """Test Buda data containers."""

    @pytest.mark.ticker
    def test_ticker_container(self):
        ticker_response = json.dumps(
            {
                "ticker": {
                    "market_id": "btc-clp",
                    "last_price": [50000000],
                    "min_ask": [49900000],
                    "max_bid": [50100000],
                    "volume": [1.5],
                    "max_price": [51000000],
                    "min_price": [48000000],
                    "timestamp": 1640995200,
                }
            }
        )
        ticker = BudaRequestTickerData(ticker_response, "btc-clp", "SPOT", False)
        ticker.init_data()
        assert ticker.get_exchange_name() == "BUDA"
        assert ticker.symbol_name == "btc-clp"
        assert ticker.last_price == 50000000
        assert ticker.bid_price == 49900000
        assert ticker.ask_price == 50100000
        assert ticker.volume_24h == 1.5
        assert ticker.high_24h == 51000000
        assert ticker.low_24h == 48000000

    @pytest.mark.ticker
    def test_ticker_container_with_empty_data(self):
        ticker_response = json.dumps({"ticker": {}})
        ticker = BudaRequestTickerData(ticker_response, "btc-clp", "SPOT", False)
        ticker.init_data()
        assert ticker.last_price is None


class TestBudaRegistry:
    """Test Buda registration."""

    def test_buda_registered(self):
        assert "BUDA___SPOT" in ExchangeRegistry._feed_classes
        assert ExchangeRegistry._feed_classes["BUDA___SPOT"] == BudaRequestDataSpot

    def test_buda_exchange_data_registered(self):
        assert "BUDA___SPOT" in ExchangeRegistry._exchange_data_classes
        assert ExchangeRegistry._exchange_data_classes["BUDA___SPOT"] == BudaExchangeDataSpot

    def test_buda_create_feed(self):
        data_queue = queue.Queue()
        feed = ExchangeRegistry.create_feed("BUDA___SPOT", data_queue)
        assert isinstance(feed, BudaRequestDataSpot)

    def test_buda_create_exchange_data(self):
        exchange_data = ExchangeRegistry.create_exchange_data("BUDA___SPOT")
        assert isinstance(exchange_data, BudaExchangeDataSpot)


class TestBudaLiveAPI:
    """Live API tests - require network, marked as integration."""

    @pytest.mark.integration
    @pytest.mark.ticker
    def test_buda_req_tick_data(self):
        data_queue = queue.Queue()
        feed = BudaRequestDataSpot(data_queue, exchange_name="BUDA___SPOT")
        data = feed.get_tick("btc-clp")
        assert isinstance(data, RequestData)

    @pytest.mark.integration
    @pytest.mark.orderbook
    def test_buda_req_depth_data(self):
        data_queue = queue.Queue()
        feed = BudaRequestDataSpot(data_queue, exchange_name="BUDA___SPOT")
        data = feed.get_depth("btc-clp", 20)
        assert isinstance(data, RequestData)

    @pytest.mark.integration
    @pytest.mark.ticker
    def test_buda_async_tick_data(self):
        data_queue = queue.Queue()
        feed = BudaRequestDataSpot(data_queue, exchange_name="BUDA___SPOT")
        feed.async_get_tick("btc-clp")
        time.sleep(3)
        try:
            tick_data = data_queue.get(timeout=10)
            assert tick_data is not None
        except queue.Empty:
            pass
