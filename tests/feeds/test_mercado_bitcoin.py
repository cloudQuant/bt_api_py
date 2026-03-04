"""
Mercado Bitcoin Exchange Integration Tests

Run tests:
    pytest tests/feeds/test_mercado_bitcoin.py -v
"""

import json
import queue
from unittest.mock import Mock

import pytest

from bt_api_py.containers.exchanges.mercado_bitcoin_exchange_data import (
    MercadoBitcoinExchangeData,
    MercadoBitcoinExchangeDataSpot,
)
from bt_api_py.containers.tickers.mercado_bitcoin_ticker import MercadoBitcoinRequestTickerData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_mercado_bitcoin.spot import MercadoBitcoinRequestDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.registry import ExchangeRegistry

# Import registration to auto-register Mercado Bitcoin
import bt_api_py.exchange_registers.register_mercado_bitcoin  # noqa: F401


@pytest.fixture
def mock_feed():
    """Create a Mercado Bitcoin feed instance with mocked request."""
    data_queue = queue.Queue()
    feed = MercadoBitcoinRequestDataSpot(data_queue)
    feed.request = Mock(return_value=Mock())
    return feed


class TestMercadoBitcoinExchangeData:
    """Test Mercado Bitcoin exchange data configuration."""

    def test_exchange_data_base_initialization(self):
        data = MercadoBitcoinExchangeData()
        assert data.exchange_name == "mercado_bitcoin"
        assert data.rest_url == "https://www.mercadobitcoin.net/api"
        assert data.rest_private_url == "https://www.mercadobitcoin.net/tapi"
        assert data.rest_v4_url == "https://api.mercadobitcoin.net/api/v4"
        assert data.wss_url == "wss://ws.mercadobitcoin.net"
        assert isinstance(data.kline_periods, dict)
        assert "1h" in data.kline_periods
        assert "BRL" in data.legal_currency

    def test_kline_periods(self):
        data = MercadoBitcoinExchangeData()
        assert "1h" in data.kline_periods
        assert "1d" in data.kline_periods
        assert "1w" in data.kline_periods

    def test_get_symbol(self):
        data = MercadoBitcoinExchangeDataSpot()
        assert data.get_symbol("BTC-BRL") == "BTC-BRL"


class TestMercadoBitcoinRequestDataSpot:
    """Test Mercado Bitcoin spot request feed."""

    def test_request_data_creation(self):
        data_queue = queue.Queue()
        feed = MercadoBitcoinRequestDataSpot(data_queue)
        assert feed.exchange_name == "MERCADO_BITCOIN___SPOT"
        assert feed.asset_type == "SPOT"

    def test_capabilities(self):
        caps = MercadoBitcoinRequestDataSpot._capabilities()
        assert Capability.GET_TICK in caps
        assert Capability.GET_DEPTH in caps
        assert Capability.GET_KLINE in caps
        assert Capability.GET_EXCHANGE_INFO in caps
        assert Capability.GET_BALANCE in caps
        assert Capability.GET_ACCOUNT in caps
        assert Capability.MAKE_ORDER in caps
        assert Capability.CANCEL_ORDER in caps

    def test_get_tick_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_tick("BTC-BRL")
        assert "BTC" in path
        assert extra_data["request_type"] == "get_tick"
        assert extra_data["symbol_name"] == "BTC-BRL"
        assert extra_data["exchange_name"] == "MERCADO_BITCOIN___SPOT"
        assert extra_data["asset_type"] == "SPOT"

    def test_get_depth_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_depth("BTC-BRL")
        assert "BTC" in path
        assert extra_data["request_type"] == "get_depth"
        assert extra_data["exchange_name"] == "MERCADO_BITCOIN___SPOT"

    def test_get_kline_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_kline("BTC-BRL", "1h")
        assert "candles" in path
        assert extra_data["request_type"] == "get_kline"
        assert extra_data["exchange_name"] == "MERCADO_BITCOIN___SPOT"

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
            "BTC-BRL", "0.001", "50000", "buy-limit"
        )
        assert path is not None
        assert extra_data["request_type"] == "make_order"

    def test_cancel_order_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._cancel_order("BTC-BRL", "order_123")
        assert path is not None
        assert extra_data["request_type"] == "cancel_order"
        assert extra_data["order_id"] == "order_123"

    def test_get_server_time_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_server_time()
        assert path is not None
        assert extra_data["request_type"] == "get_server_time"


class TestMercadoBitcoinStandardInterfaces:
    """Test standard Feed interface methods invoke request()."""

    @pytest.fixture
    def feed(self):
        data_queue = queue.Queue()
        feed = MercadoBitcoinRequestDataSpot(data_queue)
        feed.request = Mock(return_value=Mock())
        return feed

    def test_get_tick_calls_request(self, feed):
        feed.get_tick("BTC-BRL")
        assert feed.request.called

    def test_get_depth_calls_request(self, feed):
        feed.get_depth("BTC-BRL")
        assert feed.request.called

    def test_get_kline_calls_request(self, feed):
        feed.get_kline("BTC-BRL", "1h")
        assert feed.request.called

    def test_get_balance_calls_request(self, feed):
        feed.get_balance()
        assert feed.request.called

    def test_get_account_calls_request(self, feed):
        feed.get_account()
        assert feed.request.called

    def test_make_order_calls_request(self, feed):
        feed.make_order("BTC-BRL", "0.001", "50000", "buy-limit")
        assert feed.request.called

    def test_cancel_order_calls_request(self, feed):
        feed.cancel_order("BTC-BRL", "order_123")
        assert feed.request.called

    def test_get_server_time_calls_request(self, feed):
        feed.get_server_time()
        assert feed.request.called


class TestMercadoBitcoinNormalizeFunctions:
    """Test normalize functions edge cases."""

    def test_tick_normalize_with_none(self):
        result, status = MercadoBitcoinRequestDataSpot._get_tick_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_tick_normalize_with_data(self):
        input_data = {"ticker": {"last": "50000.00", "buy": "49999.00", "sell": "50001.00"}}
        result, status = MercadoBitcoinRequestDataSpot._get_tick_normalize_function(input_data, {})
        assert status is True
        assert result[0]["last"] == "50000.00"

    def test_depth_normalize_with_none(self):
        result, status = MercadoBitcoinRequestDataSpot._get_depth_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_depth_normalize_with_data(self):
        input_data = {"bids": [["49999", "1.0"]], "asks": [["50001", "1.0"]]}
        result, status = MercadoBitcoinRequestDataSpot._get_depth_normalize_function(input_data, {})
        assert status is True

    def test_kline_normalize_with_none(self):
        result, status = MercadoBitcoinRequestDataSpot._get_kline_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_kline_normalize_with_list(self):
        input_data = [[1672531200, "50000", "51000", "49000", "50500", "100"]]
        result, status = MercadoBitcoinRequestDataSpot._get_kline_normalize_function(input_data, {})
        assert status is True

    def test_balance_normalize_with_none(self):
        result, status = MercadoBitcoinRequestDataSpot._get_balance_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_account_normalize_with_none(self):
        result, status = MercadoBitcoinRequestDataSpot._get_account_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_make_order_normalize_with_none(self):
        result, status = MercadoBitcoinRequestDataSpot._make_order_normalize_function(None, {})
        assert result == []
        assert status is False

    def test_server_time_normalize_with_none(self):
        result, status = MercadoBitcoinRequestDataSpot._get_server_time_normalize_function(None, None)
        assert result is None
        assert status is False

    def test_server_time_normalize_with_data(self):
        input_data = {"ticker": {"date": 1678901234}}
        result, status = MercadoBitcoinRequestDataSpot._get_server_time_normalize_function(input_data, {})
        assert status is True


class TestMercadoBitcoinDataContainers:
    """Test Mercado Bitcoin data containers."""

    def test_ticker_container(self):
        ticker_info = json.dumps({
            "ticker": {
                "last": "50000.00",
                "buy": "49999.00",
                "sell": "50001.00",
                "high": "51000.00",
                "low": "49000.00",
                "vol": "100.50",
            }
        })
        ticker = MercadoBitcoinRequestTickerData(ticker_info, "BTC-BRL", "SPOT", False)
        ticker.init_data()
        assert ticker.exchange_name == "MERCADO_BITCOIN"
        assert ticker.ticker_symbol_name == "BTC-BRL"
        assert ticker.last_price == 50000.0
        assert ticker.bid_price == 49999.0
        assert ticker.ask_price == 50001.0


class TestMercadoBitcoinRegistration:
    """Test Mercado Bitcoin registration module."""

    def test_registration(self):
        assert ExchangeRegistry.has_exchange("MERCADO_BITCOIN___SPOT")
        feed_class = ExchangeRegistry._feed_classes.get("MERCADO_BITCOIN___SPOT")
        assert feed_class == MercadoBitcoinRequestDataSpot
        data_class = ExchangeRegistry._exchange_data_classes.get("MERCADO_BITCOIN___SPOT")
        assert data_class == MercadoBitcoinExchangeDataSpot

    def test_create_feed(self):
        data_queue = queue.Queue()
        feed = ExchangeRegistry.create_feed("MERCADO_BITCOIN___SPOT", data_queue)
        assert isinstance(feed, MercadoBitcoinRequestDataSpot)

    def test_create_exchange_data(self):
        exchange_data = ExchangeRegistry.create_exchange_data("MERCADO_BITCOIN___SPOT")
        assert isinstance(exchange_data, MercadoBitcoinExchangeDataSpot)


class TestMercadoBitcoinLiveAPI:
    """Live API tests - require network, marked as integration."""

    @pytest.mark.integration
    def test_mercado_bitcoin_req_tick_data(self):
        data_queue = queue.Queue()
        feed = MercadoBitcoinRequestDataSpot(data_queue)
        data = feed.get_tick("BTC-BRL")
        assert isinstance(data, RequestData)

    @pytest.mark.integration
    def test_mercado_bitcoin_req_depth_data(self):
        data_queue = queue.Queue()
        feed = MercadoBitcoinRequestDataSpot(data_queue)
        data = feed.get_depth("BTC-BRL")
        assert isinstance(data, RequestData)

    @pytest.mark.integration
    def test_mercado_bitcoin_req_kline_data(self):
        data_queue = queue.Queue()
        feed = MercadoBitcoinRequestDataSpot(data_queue)
        data = feed.get_kline("BTC-BRL", "1h")
        assert isinstance(data, RequestData)

    @pytest.mark.integration
    def test_mercado_bitcoin_server_time(self):
        data_queue = queue.Queue()
        feed = MercadoBitcoinRequestDataSpot(data_queue)
        data = feed.get_server_time()
        assert isinstance(data, RequestData)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
