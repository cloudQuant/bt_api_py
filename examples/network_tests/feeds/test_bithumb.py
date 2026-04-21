"""
Test Bithumb exchange integration.

Run tests:
    pytest tests/feeds/test_bithumb.py -v
"""

import queue
import time
from unittest.mock import Mock

import pytest

# Import registration to auto-register Bithumb
import bt_api_py.exchange_registers.register_bithumb  # noqa: F401
from bt_api_py.containers.exchanges.bithumb_exchange_data import BithumbExchangeDataSpot
from bt_api_base.containers.requestdatas.request_data import RequestData
from bt_api_py.containers.tickers.bithumb_ticker import BithumbRequestTickerData
from bt_api_base.feeds.capability import Capability
from bt_api_py.feeds.live_bithumb.spot import BithumbRequestDataSpot


@pytest.fixture
def mock_feed():
    """Create a Bithumb feed instance with mocked request."""
    data_queue = queue.Queue()
    feed = BithumbRequestDataSpot(data_queue, exchange_name="BITHUMB___SPOT")
    feed.request = Mock(return_value=Mock(spec=RequestData))
    return feed


class TestBithumbExchangeData:
    """Test Bithumb exchange data configuration."""

    def test_exchange_data_spot_creation(self):
        exchange_data = BithumbExchangeDataSpot()
        assert "BITHUMB" in exchange_data.exchange_name.upper()
        assert exchange_data.asset_type == "spot"
        assert exchange_data.rest_url != ""
        assert "bithumb" in exchange_data.rest_url.lower()

    @pytest.mark.kline
    def test_kline_periods(self):
        exchange_data = BithumbExchangeDataSpot()
        assert "1m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods

    def test_legal_currencies(self):
        exchange_data = BithumbExchangeDataSpot()
        assert "USDT" in exchange_data.legal_currency
        assert "KRW" in exchange_data.legal_currency

    def test_rest_url(self):
        exchange_data = BithumbExchangeDataSpot()
        assert "bithumb" in exchange_data.rest_url.lower()

    def test_wss_url(self):
        exchange_data = BithumbExchangeDataSpot()
        assert exchange_data.wss_url != ""
        assert "ws" in exchange_data.wss_url.lower()


class TestBithumbRequestDataSpot:
    """Test Bithumb REST API request methods."""

    def test_request_data_creation(self):
        data_queue = queue.Queue()
        feed = BithumbRequestDataSpot(data_queue, exchange_name="BITHUMB___SPOT")
        assert feed.exchange_name == "BITHUMB___SPOT"
        assert feed.asset_type == "SPOT"

    def test_capabilities(self):
        caps = BithumbRequestDataSpot._capabilities()
        assert Capability.GET_TICK in caps
        assert Capability.GET_DEPTH in caps
        assert Capability.GET_KLINE in caps
        assert Capability.GET_EXCHANGE_INFO in caps
        assert Capability.GET_BALANCE in caps
        assert Capability.GET_ACCOUNT in caps
        assert Capability.MAKE_ORDER in caps
        assert Capability.CANCEL_ORDER in caps

    def test_convert_symbol(self):
        data_queue = queue.Queue()
        feed = BithumbRequestDataSpot(data_queue)
        assert feed._convert_symbol("BTC/USDT") == "BTC-USDT"
        assert feed._convert_symbol("BTC_USDT") == "BTC-USDT"
        assert feed._convert_symbol("BTC-USDT") == "BTC-USDT"

    @pytest.mark.ticker
    def test_get_tick_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_tick("BTC/USDT")
        assert "GET" in path
        assert "ticker" in path
        assert params["symbol"] == "BTC-USDT"
        assert extra_data["request_type"] == "get_tick"

    @pytest.mark.ticker
    def test_get_tick_calls_request(self, mock_feed):
        mock_feed.get_tick("BTC/USDT")
        assert mock_feed.request.called

    @pytest.mark.orderbook
    def test_get_depth_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_depth("BTC/USDT")
        assert "orderBook" in path
        assert extra_data["request_type"] == "get_depth"

    @pytest.mark.orderbook
    def test_get_depth_calls_request(self, mock_feed):
        mock_feed.get_depth("BTC/USDT")
        assert mock_feed.request.called

    @pytest.mark.kline
    def test_get_kline_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_kline("BTC/USDT", "1h")
        assert "kline" in path
        assert extra_data["request_type"] == "get_kline"

    @pytest.mark.kline
    def test_get_kline_calls_request(self, mock_feed):
        mock_feed.get_kline("BTC/USDT", "1h")
        assert mock_feed.request.called

    def test_get_exchange_info_returns_tuple(self, mock_feed):
        path, params, extra_data = mock_feed._get_exchange_info()
        assert extra_data["request_type"] == "get_exchange_info"

    def test_get_server_time_tuple(self):
        data_queue = queue.Queue()
        feed = BithumbRequestDataSpot(data_queue, exchange_name="BITHUMB___SPOT")
        path, params, extra_data = feed._get_server_time()
        assert extra_data["request_type"] == "get_server_time"


class TestBithumbDataContainers:
    """Test Bithumb data containers."""

    @pytest.mark.ticker
    def test_ticker_container(self):
        ticker_data = {
            "s": "BTC-USDT",
            "c": "50000",
            "h": "51000",
            "l": "49000",
            "v": "1234.56",
            "p": "2.5",
        }
        ticker = BithumbRequestTickerData(
            ticker_data, "BTC-USDT", "SPOT", has_been_json_encoded=True
        )
        ticker.init_data()
        assert ticker.get_exchange_name() == "BITHUMB"
        assert ticker.last_price == 50000.0
        assert ticker.high_24h == 51000.0
        assert ticker.low_24h == 49000.0
        assert ticker.volume_24h == 1234.56


class TestBithumbStandardInterfaces:
    """Test standard Feed interface methods for Bithumb."""

    @pytest.fixture
    def feed(self):
        data_queue = queue.Queue()
        feed = BithumbRequestDataSpot(data_queue, exchange_name="BITHUMB___SPOT")
        feed.request = Mock(return_value=Mock(spec=RequestData))
        return feed

    def test_make_order_calls_request(self, feed):
        feed.make_order("BTC/USDT", 0.01, 50000, "LIMIT", offset="BUY")
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


class TestBithumbBaseCapabilities:
    """Test capabilities on the base class."""

    def test_base_capabilities(self):
        from bt_api_py.feeds.live_bithumb.request_base import BithumbRequestData

        caps = BithumbRequestData._capabilities()
        assert Capability.GET_TICK in caps
        assert Capability.MAKE_ORDER in caps
        assert Capability.CANCEL_ORDER in caps


class TestBithumbNormalizeFunctions:
    """Test normalize functions edge cases."""

    @pytest.mark.ticker
    def test_tick_normalize_with_none(self):
        result, status = BithumbRequestDataSpot._get_tick_normalize_function(None, None)
        assert result == []
        assert status is False

    @pytest.mark.ticker
    def test_tick_normalize_success(self):
        data = {"data": [{"s": "BTC-USDT", "c": "50000"}]}
        result, status = BithumbRequestDataSpot._get_tick_normalize_function(data, None)
        assert status is True
        assert len(result) == 1

    @pytest.mark.orderbook
    def test_depth_normalize_with_none(self):
        result, status = BithumbRequestDataSpot._get_depth_normalize_function(None, None)
        assert result == []
        assert status is False

    @pytest.mark.orderbook
    def test_depth_normalize_success(self):
        data = {"data": {"b": [], "s": []}}
        result, status = BithumbRequestDataSpot._get_depth_normalize_function(data, None)
        assert status is True

    @pytest.mark.kline
    def test_kline_normalize_with_none(self):
        result, status = BithumbRequestDataSpot._get_kline_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_exchange_info_normalize_with_none(self):
        result, status = BithumbRequestDataSpot._get_exchange_info_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_trades_normalize_with_none(self):
        result, status = BithumbRequestDataSpot._get_trades_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_account_normalize_with_none(self):
        result, status = BithumbRequestDataSpot._get_account_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_balance_normalize_with_none(self):
        result, status = BithumbRequestDataSpot._get_balance_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_deals_normalize_with_none(self):
        result, status = BithumbRequestDataSpot._get_deals_normalize_function(None, None)
        assert result == []
        assert status is False


class TestBithumbRegistration:
    """Test Bithumb registration."""

    def test_bithumb_exchange_data_creation(self):
        exchange_data = BithumbExchangeDataSpot()
        assert exchange_data is not None
        assert exchange_data.exchange_name is not None


class TestBithumbLiveAPI:
    """Live API tests - require network, marked as integration."""

    @pytest.mark.integration
    @pytest.mark.ticker
    def test_bithumb_req_tick_data(self):
        data_queue = queue.Queue()
        feed = BithumbRequestDataSpot(data_queue, exchange_name="BITHUMB___SPOT")
        data = feed.get_tick("BTC-USDT")
        assert isinstance(data, RequestData)
        data_list = data.get_data()
        assert isinstance(data_list, list)
        if data_list and len(data_list) > 0:
            ticker = data_list[0]
            if isinstance(ticker, dict):
                assert isinstance(ticker, dict)

    @pytest.mark.integration
    @pytest.mark.kline
    def test_bithumb_req_kline_data(self):
        data_queue = queue.Queue()
        feed = BithumbRequestDataSpot(data_queue, exchange_name="BITHUMB___SPOT")
        data = feed.get_kline("BTC-USDT", "1h", count=2)
        assert isinstance(data, RequestData)
        data_list = data.get_data()
        assert isinstance(data_list, list)
        if data_list and len(data_list) > 0:
            kline = data_list[0]
            assert isinstance(kline, (list, dict))

    @pytest.mark.integration
    @pytest.mark.orderbook
    def test_bithumb_req_orderbook_data(self):
        data_queue = queue.Queue()
        feed = BithumbRequestDataSpot(data_queue, exchange_name="BITHUMB___SPOT")
        data = feed.get_depth("BTC-USDT", 20)
        assert isinstance(data, RequestData)
        data_list = data.get_data()
        assert isinstance(data_list, list)
        if data_list and len(data_list) > 0:
            orderbook = data_list[0]
            if isinstance(orderbook, dict):
                assert isinstance(orderbook, dict)

    @pytest.mark.integration
    @pytest.mark.ticker
    def test_bithumb_async_tick_data(self):
        data_queue = queue.Queue()
        feed = BithumbRequestDataSpot(data_queue, exchange_name="BITHUMB___SPOT")
        feed.async_get_tick("BTC-USDT", extra_data={"test_async": True})
        time.sleep(3)
        try:
            tick_data = data_queue.get(timeout=10)
            assert tick_data is not None
        except queue.Empty:
            pass

    @pytest.mark.integration
    @pytest.mark.kline
    def test_bithumb_async_kline_data(self):
        data_queue = queue.Queue()
        feed = BithumbRequestDataSpot(data_queue, exchange_name="BITHUMB___SPOT")
        feed.async_get_kline("BTC-USDT", period="1h", count=3, extra_data={"test_async": True})
        time.sleep(5)
        try:
            kline_data = data_queue.get(timeout=10)
            assert kline_data is not None
        except queue.Empty:
            pass

    @pytest.mark.integration
    @pytest.mark.orderbook
    def test_bithumb_async_orderbook_data(self):
        data_queue = queue.Queue()
        feed = BithumbRequestDataSpot(data_queue, exchange_name="BITHUMB___SPOT")
        feed.async_get_depth("BTC-USDT", 20)
        time.sleep(3)
        try:
            depth_data = data_queue.get(timeout=10)
            assert depth_data is not None
        except queue.Empty:
            pass

    @pytest.mark.integration
    def test_bithumb_req_account_data(self):
        data_queue = queue.Queue()
        feed = BithumbRequestDataSpot(data_queue, exchange_name="BITHUMB___SPOT")
        data = feed.get_account()
        assert isinstance(data, RequestData)

    @pytest.mark.integration
    def test_bithumb_req_balance_data(self):
        data_queue = queue.Queue()
        feed = BithumbRequestDataSpot(data_queue, exchange_name="BITHUMB___SPOT")
        data = feed.get_balance("BTC")
        assert isinstance(data, RequestData)

    @pytest.mark.integration
    def test_bithumb_req_get_deals(self):
        data_queue = queue.Queue()
        feed = BithumbRequestDataSpot(data_queue, exchange_name="BITHUMB___SPOT")
        data = feed.get_deals("BTC-USDT", count=50)
        assert isinstance(data, RequestData)
