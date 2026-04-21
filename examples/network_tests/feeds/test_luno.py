"""
Test Luno exchange integration.

Run tests:
    pytest tests/feeds/test_luno.py -v
"""

import json
import queue
from unittest.mock import Mock

import pytest

# Import registration to auto-register Luno
import bt_api_py.exchange_registers.register_luno  # noqa: F401
from bt_api_py.containers.tickers.luno_ticker import LunoRequestTickerData
from bt_api_base.feeds.capability import Capability
from bt_api_py.feeds.live_luno.spot import LunoRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


@pytest.fixture
def mock_feed():
    """Create a Luno feed instance with mocked request."""
    data_queue = queue.Queue()
    feed = LunoRequestDataSpot(
        data_queue,
        exchange_name="LUNO___SPOT",
    )
    feed.request = Mock(return_value=Mock())
    return feed


class TestLunoRequestDataSpot:
    """Test Luno spot REST API request class."""

    def test_request_data_creation(self):
        data_queue = queue.Queue()
        feed = LunoRequestDataSpot(
            data_queue,
            exchange_name="LUNO___SPOT",
        )
        assert feed.exchange_name == "LUNO___SPOT"
        assert feed.asset_type == "SPOT"

    def test_capabilities(self):
        caps = LunoRequestDataSpot._capabilities()
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
        path, params, extra_data = mock_feed._get_tick("XBTZAR")
        assert path is not None
        assert params["pair"] == "XBTZAR"
        assert extra_data["request_type"] == "get_tick"
        assert extra_data["symbol_name"] == "XBTZAR"
        assert extra_data["exchange_name"] == "LUNO___SPOT"
        assert extra_data["asset_type"] == "SPOT"

    @pytest.mark.orderbook
    def test_get_depth_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_depth("XBTZAR")
        assert path is not None
        assert params["pair"] == "XBTZAR"
        assert extra_data["request_type"] == "get_depth"

    @pytest.mark.kline
    def test_get_kline_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_kline("XBTZAR", period="1h")
        assert path is not None
        assert params["pair"] == "XBTZAR"
        assert "duration" in params
        assert extra_data["request_type"] == "get_kline"

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
            symbol="XBTZAR", volume="0.001", price="50000", order_type="buy-limit"
        )
        assert path is not None
        assert params["type"] == "BID"
        assert extra_data["request_type"] == "make_order"

    def test_cancel_order_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._cancel_order("XBTZAR", "order_123")
        assert path is not None
        assert extra_data["request_type"] == "cancel_order"
        assert extra_data["order_id"] == "order_123"

    def test_get_server_time_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_server_time()
        assert path is not None
        assert extra_data["request_type"] == "get_server_time"

    def test_get_exchange_info_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_exchange_info()
        assert path is not None
        assert extra_data["request_type"] == "get_exchange_info"


class TestLunoStandardInterfaces:
    """Test standard Feed interface methods invoke request()."""

    @pytest.fixture
    def feed(self):
        data_queue = queue.Queue()
        feed = LunoRequestDataSpot(data_queue, exchange_name="LUNO___SPOT")
        feed.request = Mock(return_value=Mock())
        return feed

    @pytest.mark.ticker
    def test_get_tick_calls_request(self, feed):
        feed.get_tick("XBTZAR")
        assert feed.request.called
        call_kwargs = feed.request.call_args[1]
        extra_data = call_kwargs.get("extra_data")
        if extra_data:
            assert extra_data["request_type"] == "get_tick"

    @pytest.mark.orderbook
    def test_get_depth_calls_request(self, feed):
        feed.get_depth("XBTZAR")
        assert feed.request.called

    @pytest.mark.kline
    def test_get_kline_calls_request(self, feed):
        feed.get_kline("XBTZAR", "1h")
        assert feed.request.called

    def test_get_balance_calls_request(self, feed):
        feed.get_balance()
        assert feed.request.called

    def test_get_account_calls_request(self, feed):
        feed.get_account()
        assert feed.request.called

    def test_make_order_calls_request(self, feed):
        feed.make_order("XBTZAR", "0.001", "50000", "buy-limit")
        assert feed.request.called

    def test_cancel_order_calls_request(self, feed):
        feed.cancel_order("XBTZAR", "order_123")
        assert feed.request.called

    def test_get_exchange_info_calls_request(self, feed):
        feed.get_exchange_info()
        assert feed.request.called


class TestLunoNormalizeFunctions:
    """Test normalize functions edge cases."""

    @pytest.mark.ticker
    def test_tick_normalize_with_none(self):
        result, status = LunoRequestDataSpot._get_tick_normalize_function(None, None)
        assert result == []
        assert status is False

    @pytest.mark.ticker
    def test_tick_normalize_with_data(self):
        input_data = {"pair": "XBTZAR", "last_trade": "95000000"}
        result, status = LunoRequestDataSpot._get_tick_normalize_function(input_data, {})
        assert status is True
        assert len(result) == 1

    @pytest.mark.orderbook
    def test_depth_normalize_with_none(self):
        result, status = LunoRequestDataSpot._get_depth_normalize_function(None, None)
        assert result == []
        assert status is False

    @pytest.mark.orderbook
    def test_depth_normalize_with_data(self):
        input_data = {"bids": [], "asks": []}
        result, status = LunoRequestDataSpot._get_depth_normalize_function(input_data, {})
        assert status is True

    @pytest.mark.kline
    def test_kline_normalize_with_none(self):
        result, status = LunoRequestDataSpot._get_kline_normalize_function(None, None)
        assert result == []
        assert status is False

    @pytest.mark.kline
    def test_kline_normalize_with_data(self):
        input_data = {"candles": [{"open": 100}]}
        result, status = LunoRequestDataSpot._get_kline_normalize_function(input_data, {})
        assert status is True

    def test_balance_normalize_with_none(self):
        result, status = LunoRequestDataSpot._get_balance_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_account_normalize_with_none(self):
        result, status = LunoRequestDataSpot._get_account_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_make_order_normalize_with_none(self):
        result, status = LunoRequestDataSpot._make_order_normalize_function(None, {})
        assert result == []
        assert status is False

    def test_exchange_info_normalize_with_none(self):
        result, status = LunoRequestDataSpot._get_exchange_info_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_server_time_normalize_with_none(self):
        result, status = LunoRequestDataSpot._get_server_time_normalize_function(None, None)
        assert result is None
        assert status is False

    def test_server_time_normalize_with_data(self):
        input_data = {"timestamp": 1678901234000}
        result, status = LunoRequestDataSpot._get_server_time_normalize_function(input_data, {})
        assert status is True
        assert result == 1678901234000


class TestLunoDataContainers:
    """Test Luno data containers."""

    @pytest.mark.ticker
    def test_ticker_container(self):
        ticker_info = json.dumps(
            {
                "pair": "XBTZAR",
                "last_trade": "95000000",
                "bid": "94990000",
                "ask": "95000000",
                "rolling_24_hour_volume": "123.45",
                "rolling_24_hour_high": "95800000",
                "rolling_24_hour_low": "93500000",
                "timestamp": 1678901234000,
            }
        )
        ticker = LunoRequestTickerData(ticker_info, "XBTZAR", "SPOT", False)
        ticker.init_data()
        assert ticker.exchange_name == "LUNO"
        assert ticker.ticker_symbol_name == "XBTZAR"
        assert ticker.last_price == 95000000.0
        assert ticker.bid_price == 94990000.0
        assert ticker.ask_price == 95000000.0
        assert ticker.volume_24h == 123.45
        assert ticker.high_24h == 95800000.0
        assert ticker.low_24h == 93500000.0


class TestLunoRegistry:
    """Test Luno registration."""

    def test_luno_registered(self):
        assert "LUNO___SPOT" in ExchangeRegistry._feed_classes
        assert ExchangeRegistry._feed_classes["LUNO___SPOT"] == LunoRequestDataSpot

    def test_luno_create_feed(self):
        data_queue = queue.Queue()
        feed = ExchangeRegistry.create_feed("LUNO___SPOT", data_queue)
        assert isinstance(feed, LunoRequestDataSpot)


class TestLunoLiveAPI:
    """Live API tests - require network, marked as integration."""

    @pytest.mark.integration
    @pytest.mark.ticker
    def test_luno_req_tick_data(self):
        data_queue = queue.Queue()
        feed = LunoRequestDataSpot(data_queue, exchange_name="LUNO___SPOT")
        result = feed.get_tick("XBTZAR")
        assert result is not None

    @pytest.mark.integration
    @pytest.mark.orderbook
    def test_luno_req_depth_data(self):
        data_queue = queue.Queue()
        feed = LunoRequestDataSpot(data_queue, exchange_name="LUNO___SPOT")
        result = feed.get_depth("XBTZAR")
        assert result is not None

    @pytest.mark.integration
    @pytest.mark.kline
    def test_luno_req_kline_data(self):
        data_queue = queue.Queue()
        feed = LunoRequestDataSpot(data_queue, exchange_name="LUNO___SPOT")
        result = feed.get_kline("XBTZAR", "1h")
        assert result is not None

    @pytest.mark.integration
    def test_luno_server_time(self):
        data_queue = queue.Queue()
        feed = LunoRequestDataSpot(data_queue, exchange_name="LUNO___SPOT")
        result = feed.get_server_time()
        assert result is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
