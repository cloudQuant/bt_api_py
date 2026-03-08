"""
SatoshiTango Exchange Integration Tests

Run tests:
    pytest tests/feeds/test_satoshitango.py -v
"""

import queue
from unittest.mock import Mock

import pytest

# Import registration to auto-register SatoshiTango
import bt_api_py.exchange_registers.register_satoshitango  # noqa: F401
from bt_api_py.containers.exchanges.satoshitango_exchange_data import (
    SatoshiTangoExchangeData,
    SatoshiTangoExchangeDataSpot,
)
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.containers.tickers.satoshitango_ticker import SatoshiTangoRequestTickerData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_satoshitango.spot import SatoshiTangoRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


@pytest.fixture
def mock_feed():
    """Create a SatoshiTango feed with mocked request."""
    data_queue = queue.Queue()
    feed = SatoshiTangoRequestDataSpot(data_queue)
    feed.request = Mock(return_value=Mock())
    return feed


class TestSatoshiTangoExchangeData:
    """Test SatoshiTango exchange data configuration."""

    def test_exchange_data_base_initialization(self):
        data = SatoshiTangoExchangeData()
        assert data.exchange_name == "satoshitango"
        assert data.rest_url == "https://api.satoshitango.com"
        assert data.wss_url == ""
        assert isinstance(data.kline_periods, dict)
        assert "1h" in data.kline_periods
        assert "ARS" in data.legal_currency

    def test_kline_periods(self):
        data = SatoshiTangoExchangeData()
        assert "1m" in data.kline_periods
        assert "1h" in data.kline_periods
        assert "1d" in data.kline_periods
        assert "1w" in data.kline_periods

    def test_legal_currencies(self):
        data = SatoshiTangoExchangeData()
        assert "ARS" in data.legal_currency
        assert "USD" in data.legal_currency


class TestSatoshiTangoRequestDataSpot:
    """Test SatoshiTango spot request feed."""

    def test_request_data_initialization(self):
        data_queue = queue.Queue()
        feed = SatoshiTangoRequestDataSpot(data_queue)
        assert feed.exchange_name == "SATOSHITANGO___SPOT"
        assert feed.asset_type == "SPOT"

    def test_capabilities(self):
        caps = SatoshiTangoRequestDataSpot._capabilities()
        assert Capability.GET_TICK in caps
        assert Capability.GET_DEPTH in caps
        assert Capability.GET_KLINE in caps
        assert Capability.GET_EXCHANGE_INFO in caps
        assert Capability.GET_BALANCE in caps
        assert Capability.GET_ACCOUNT in caps
        assert Capability.MAKE_ORDER in caps
        assert Capability.CANCEL_ORDER in caps

    def test_get_tick_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_tick("BTC/ARS")
        assert "ticker" in path.lower()
        assert extra_data["request_type"] == "get_tick"
        assert extra_data["symbol_name"] == "BTC/ARS"
        assert extra_data["exchange_name"] == "SATOSHITANGO___SPOT"
        assert extra_data["asset_type"] == "SPOT"

    def test_get_depth_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_depth("BTC/ARS", 20)
        assert "orderbook" in path.lower()
        assert params["depth"] == 20
        assert extra_data["request_type"] == "get_depth"
        assert extra_data["exchange_name"] == "SATOSHITANGO___SPOT"

    def test_get_kline_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_kline("BTC/ARS", "1h", 10)
        assert "kline" in path.lower()
        assert extra_data["request_type"] == "get_kline"
        assert extra_data["exchange_name"] == "SATOSHITANGO___SPOT"

    def test_get_exchange_info_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_exchange_info()
        assert path is not None
        assert extra_data["request_type"] == "get_exchange_info"

    def test_get_balance_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_balance()
        assert path is not None
        assert extra_data["request_type"] == "get_balance"

    def test_get_account_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_account()
        assert path is not None
        assert extra_data["request_type"] == "get_account"

    def test_make_order_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._make_order("BTC/ARS", "0.001", "9500000", "buy-limit")
        assert path is not None
        assert extra_data["request_type"] == "make_order"

    def test_cancel_order_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._cancel_order("BTC/ARS", "order_123")
        assert path is not None
        assert extra_data["request_type"] == "cancel_order"
        assert extra_data["order_id"] == "order_123"

    def test_get_server_time_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_server_time()
        assert path is not None
        assert extra_data["request_type"] == "get_server_time"


class TestSatoshiTangoStandardInterfaces:
    """Test standard Feed interface methods invoke request()."""

    @pytest.fixture
    def feed(self):
        data_queue = queue.Queue()
        feed = SatoshiTangoRequestDataSpot(data_queue)
        feed.request = Mock(return_value=Mock())
        return feed

    def test_get_tick_calls_request(self, feed):
        feed.get_tick("BTC/ARS")
        assert feed.request.called

    def test_get_depth_calls_request(self, feed):
        feed.get_depth("BTC/ARS")
        assert feed.request.called

    def test_get_kline_calls_request(self, feed):
        feed.get_kline("BTC/ARS", "1h")
        assert feed.request.called

    def test_get_exchange_info_calls_request(self, feed):
        feed.get_exchange_info()
        assert feed.request.called

    def test_get_balance_calls_request(self, feed):
        feed.get_balance()
        assert feed.request.called

    def test_get_account_calls_request(self, feed):
        feed.get_account()
        assert feed.request.called

    def test_make_order_calls_request(self, feed):
        feed.make_order("BTC/ARS", "0.001", "9500000", "buy-limit")
        assert feed.request.called

    def test_cancel_order_calls_request(self, feed):
        feed.cancel_order("BTC/ARS", "order_123")
        assert feed.request.called

    def test_get_server_time_calls_request(self, feed):
        feed.get_server_time()
        assert feed.request.called


class TestSatoshiTangoNormalizeFunctions:
    """Test normalize functions edge cases."""

    def test_tick_normalize_with_none(self):
        result, status = SatoshiTangoRequestDataSpot._get_tick_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_tick_normalize_with_data(self):
        input_data = {"last": "9500000", "bid": "9490000"}
        result, status = SatoshiTangoRequestDataSpot._get_tick_normalize_function(input_data, {})
        assert status is True
        assert result[0]["last"] == "9500000"

    def test_depth_normalize_with_none(self):
        result, status = SatoshiTangoRequestDataSpot._get_depth_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_depth_normalize_with_data(self):
        input_data = {"bids": [], "asks": []}
        result, status = SatoshiTangoRequestDataSpot._get_depth_normalize_function(input_data, {})
        assert status is True

    def test_kline_normalize_with_none(self):
        result, status = SatoshiTangoRequestDataSpot._get_kline_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_kline_normalize_with_data(self):
        input_data = [[1672531200, "9500000", "9600000"]]
        result, status = SatoshiTangoRequestDataSpot._get_kline_normalize_function(input_data, {})
        assert status is True

    def test_exchange_info_normalize_with_none(self):
        result, status = SatoshiTangoRequestDataSpot._get_exchange_info_normalize_function(
            None, None
        )
        assert result == []
        assert status is False

    def test_exchange_info_normalize_with_data(self):
        input_data = {"BTCARS": {}, "ETHARS": {}}
        result, status = SatoshiTangoRequestDataSpot._get_exchange_info_normalize_function(
            input_data, {}
        )
        assert status is True

    def test_balance_normalize_with_none(self):
        result, status = SatoshiTangoRequestDataSpot._get_balance_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_account_normalize_with_none(self):
        result, status = SatoshiTangoRequestDataSpot._get_account_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_make_order_normalize_with_none(self):
        result, status = SatoshiTangoRequestDataSpot._make_order_normalize_function(None, {})
        assert result == []
        assert status is False

    def test_server_time_normalize_with_none(self):
        result, status = SatoshiTangoRequestDataSpot._get_server_time_normalize_function(None, None)
        assert result is None
        assert status is False

    def test_server_time_normalize_with_data(self):
        input_data = {"timestamp": 1678901234}
        result, status = SatoshiTangoRequestDataSpot._get_server_time_normalize_function(
            input_data, {}
        )
        assert status is True


class TestSatoshiTangoDataContainers:
    """Test SatoshiTango data containers."""

    def test_ticker_container(self):
        ticker_response = {
            "symbol": "BTCARS",
            "last": "9500000",
            "high": "9600000",
            "low": "9400000",
            "bid": "9490000",
            "ask": "9510000",
            "volume": "1.5",
        }
        ticker = SatoshiTangoRequestTickerData(ticker_response, "BTC/ARS", "SPOT")
        ticker.init_data()
        assert ticker.ticker_symbol_name == "BTCARS"
        assert ticker.last_price == 9500000.0
        assert ticker.bid_price == 9490000.0
        assert ticker.ask_price == 9510000.0


class TestSatoshiTangoRegistration:
    """Test SatoshiTango registration module."""

    def test_registration(self):
        assert ExchangeRegistry.has_exchange("SATOSHITANGO___SPOT")
        feed_class = ExchangeRegistry._feed_classes.get("SATOSHITANGO___SPOT")
        assert feed_class == SatoshiTangoRequestDataSpot
        data_class = ExchangeRegistry._exchange_data_classes.get("SATOSHITANGO___SPOT")
        assert data_class == SatoshiTangoExchangeDataSpot

    def test_create_feed(self):
        data_queue = queue.Queue()
        feed = ExchangeRegistry.create_feed("SATOSHITANGO___SPOT", data_queue)
        assert isinstance(feed, SatoshiTangoRequestDataSpot)

    def test_create_exchange_data(self):
        data = ExchangeRegistry.create_exchange_data("SATOSHITANGO___SPOT")
        assert isinstance(data, SatoshiTangoExchangeDataSpot)


class TestSatoshiTangoLiveAPI:
    """Live API tests - require network, marked as integration."""

    @pytest.mark.integration
    def test_satoshitango_req_tick_data(self):
        data_queue = queue.Queue()
        feed = SatoshiTangoRequestDataSpot(data_queue)
        data = feed.get_tick("BTC/ARS")
        assert isinstance(data, RequestData)

    @pytest.mark.integration
    def test_satoshitango_req_depth_data(self):
        data_queue = queue.Queue()
        feed = SatoshiTangoRequestDataSpot(data_queue)
        data = feed.get_depth("BTC/ARS")
        assert isinstance(data, RequestData)

    @pytest.mark.integration
    def test_satoshitango_req_kline_data(self):
        data_queue = queue.Queue()
        feed = SatoshiTangoRequestDataSpot(data_queue)
        data = feed.get_kline("BTC/ARS", "1h")
        assert isinstance(data, RequestData)

    @pytest.mark.integration
    def test_satoshitango_server_time(self):
        data_queue = queue.Queue()
        feed = SatoshiTangoRequestDataSpot(data_queue)
        data = feed.get_server_time()
        assert isinstance(data, RequestData)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
