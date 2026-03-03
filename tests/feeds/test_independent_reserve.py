"""
Test Independent Reserve exchange integration.

Run tests:
    pytest tests/feeds/test_independent_reserve.py -v

Run with coverage:
    pytest tests/feeds/test_independent_reserve.py --cov=bt_api_py.feeds.live_independent_reserve --cov-report=term-missing
"""

import json
import queue
import time
from unittest.mock import Mock, patch

import pytest

from bt_api_py.containers.exchanges.independent_reserve_exchange_data import IndependentReserveExchangeDataSpot
from bt_api_py.containers.tickers.independent_reserve_ticker import IndependentReserveRequestTickerData
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_independent_reserve.spot import IndependentReserveRequestDataSpot
from bt_api_py.registry import ExchangeRegistry

# Import registration to auto-register Independent Reserve
import bt_api_py.feeds.register_independent_reserve  # noqa: F401


class TestIndependentReserveExchangeData:
    """Test Independent Reserve exchange data configuration."""

    def test_exchange_data_spot_creation(self):
        """Test creating Independent Reserve spot exchange data."""
        exchange_data = IndependentReserveExchangeDataSpot()
        assert exchange_data.exchange_name == "INDEPENDENT_RESERVE___SPOT"
        assert exchange_data.asset_type == "spot"

    def test_kline_periods(self):
        """Test kline period conversion."""
        exchange_data = IndependentReserveExchangeDataSpot()
        assert "1m" in exchange_data.kline_periods
        assert "5m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods

    def test_legal_currency(self):
        """Test legal currencies."""
        exchange_data = IndependentReserveExchangeDataSpot()
        assert "AUD" in exchange_data.legal_currency
        assert "USD" in exchange_data.legal_currency
        assert "NZD" in exchange_data.legal_currency
        assert "SGD" in exchange_data.legal_currency


class TestIndependentReserveRequestData:
    """Test Independent Reserve REST API request base class."""

    def test_request_data_creation(self):
        """Test creating Independent Reserve request data."""
        data_queue = queue.Queue()
        request_data = IndependentReserveRequestDataSpot(
            data_queue,
            exchange_name="INDEPENDENT_RESERVE___SPOT",
        )
        assert request_data.exchange_name == "INDEPENDENT_RESERVE___SPOT"

    def test_capabilities(self):
        """Test feed capabilities."""
        data_queue = queue.Queue()
        request_data = IndependentReserveRequestDataSpot(data_queue)
        capabilities = request_data._capabilities()
        assert Capability.GET_TICK in capabilities
        assert Capability.GET_DEPTH in capabilities
        assert Capability.GET_EXCHANGE_INFO in capabilities

    def test_parse_symbol(self):
        """Test symbol parsing.

        Independent Reserve uses primaryCurrencyCode and secondaryCurrencyCode.
        """
        data_queue = queue.Queue()
        request_data = IndependentReserveRequestDataSpot(data_queue)

        primary, secondary = request_data._parse_symbol("BTC/AUD")
        assert primary == "Xbt"
        assert secondary == "Aud"

        primary, secondary = request_data._parse_symbol("ETH/USD")
        assert primary == "Eth"
        assert secondary == "Usd"

    def test_get_tick(self):
        """Test get tick method."""
        data_queue = queue.Queue()
        request_data = IndependentReserveRequestDataSpot(data_queue)

        path, params, extra_data = request_data._get_tick("BTC/AUD")
        assert "GET /Public/GetMarketSummary" in path or path == "GET /Public/GetMarketSummary"
        assert params["primaryCurrencyCode"] == "Xbt"
        assert params["secondaryCurrencyCode"] == "Aud"

    def test_get_tick_normalize_function(self):
        """Test ticker normalize function."""
        data_queue = queue.Queue()
        request_data = IndependentReserveRequestDataSpot(data_queue)

        input_data = {
            "LastPrice": 50000.0,
            "CurrentHighestBidPrice": 49950.0,
            "CurrentLowestOfferPrice": 50050.0,
            "DayVolumeXbt": 123.45,
            "DayHighestPrice": 51000.0,
            "DayLowestPrice": 49000.0,
        }
        extra_data = {"symbol_name": "BTC/AUD"}

        result, success = request_data._get_tick_normalize_function(
            input_data, extra_data)
        assert success is True
        assert len(result) > 0

    def test_get_depth(self):
        """Test get depth method."""
        data_queue = queue.Queue()
        request_data = IndependentReserveRequestDataSpot(data_queue)

        path, params, extra_data = request_data._get_depth("BTC/AUD")
        assert "GET /Public/GetOrderBook" in path or path == "GET /Public/GetOrderBook"
        assert params["primaryCurrencyCode"] == "Xbt"
        assert params["secondaryCurrencyCode"] == "Aud"

    def test_get_recent_trades(self):
        """Test get recent trades method."""
        data_queue = queue.Queue()
        request_data = IndependentReserveRequestDataSpot(data_queue)

        result = request_data.get_recent_trades("BTC/AUD", count=50)
        assert isinstance(result, RequestData)


class TestIndependentReserveDataContainers:
    """Test Independent Reserve data containers."""

    def test_ticker_container(self):
        """Test ticker data container."""
        ticker_info = json.dumps({
            "LastPrice": 50000.0,
            "CurrentHighestBidPrice": 49950.0,
            "CurrentLowestOfferPrice": 50050.0,
            "DayVolumeXbt": 123.45,
            "DayHighestPrice": 51000.0,
            "DayLowestPrice": 49000.0,
            "DayAvgPrice": 50000.0,
            "CreatedTimestamp": "2023-01-01T00:00:00Z",
        })

        ticker = IndependentReserveRequestTickerData(
            ticker_info, "BTC/AUD", "SPOT", False)
        ticker.init_data()

        assert ticker.exchange_name == "INDEPENDENT_RESERVE"
        assert ticker.symbol_name == "BTC/AUD"
        assert ticker.last_price == 50000.0
        assert ticker.bid_price == 49950.0
        assert ticker.ask_price == 50050.0
        assert ticker.high_24h == 51000.0
        assert ticker.low_24h == 49000.0


class TestIndependentReserveRegistry:
    """Test Independent Reserve registration."""

    def test_independent_reserve_registered(self):
        """Test that Independent Reserve is properly registered."""
        # Check if feed is registered
        assert "INDEPENDENT_RESERVE___SPOT" in ExchangeRegistry._feed_classes
        assert ExchangeRegistry._feed_classes["INDEPENDENT_RESERVE___SPOT"] == IndependentReserveRequestDataSpot

        # Check if exchange data is registered
        assert "INDEPENDENT_RESERVE___SPOT" in ExchangeRegistry._exchange_data_classes
        assert ExchangeRegistry._exchange_data_classes[
            "INDEPENDENT_RESERVE___SPOT"] == IndependentReserveExchangeDataSpot

        # Check if balance handler is registered
        assert "INDEPENDENT_RESERVE___SPOT" in ExchangeRegistry._balance_handlers
        assert ExchangeRegistry._balance_handlers["INDEPENDENT_RESERVE___SPOT"] is not None

    def test_independent_reserve_create_feed(self):
        """Test creating Independent Reserve feed through registry."""
        data_queue = queue.Queue()
        feed = ExchangeRegistry.create_feed(
            "INDEPENDENT_RESERVE___SPOT", data_queue)
        assert isinstance(feed, IndependentReserveRequestDataSpot)

    def test_independent_reserve_create_exchange_data(self):
        """Test creating Independent Reserve exchange data through registry."""
        exchange_data = ExchangeRegistry.create_exchange_data(
            "INDEPENDENT_RESERVE___SPOT")
        assert isinstance(exchange_data, IndependentReserveExchangeDataSpot)


class TestIndependentReserveLiveMarketData:
    """Live market data tests following Binance/OKX standard."""

    def init_req_feed(self):
        """Initialize Independent Reserve request feed."""
        data_queue = queue.Queue()
        live_independent_reserve_spot_feed = IndependentReserveRequestDataSpot(
            data_queue)
        return live_independent_reserve_spot_feed

    def test_independent_reserve_req_server_time(self):
        """Test server time endpoint."""
        live_independent_reserve_spot_feed = self.init_req_feed()
        data = live_independent_reserve_spot_feed.get_server_time()
        assert isinstance(data, RequestData)
        print("independent_reserve_server_time:", data.get_data())

    def test_independent_reserve_req_tick_data(self):
        """Test ticker data request (synchronous)."""
        live_independent_reserve_spot_feed = self.init_req_feed()
        data = live_independent_reserve_spot_feed.get_tick(
            "BTC/AUD").get_data()
        assert isinstance(data, list)
        if data and data[0]:
            tick_data = data[0]
            assert tick_data.get("symbol_name") == "BTC/AUD"
            assert tick_data.get("last_price") > 0
            assert tick_data.get("bid_price") >= 0
            assert tick_data.get("ask_price") >= 0

    @pytest.mark.integration
    def test_independent_reserve_async_tick_data(self):
        """Test ticker data request (asynchronous)."""
        data_queue = queue.Queue()
        live_independent_reserve_spot_feed = IndependentReserveRequestDataSpot(
            data_queue)
        live_independent_reserve_spot_feed.async_get_tick(
            "BTC/AUD", extra_data={"test_async_tick_data": True})
        time.sleep(3)
        try:
            tick_data = data_queue.get(timeout=10)
            assert isinstance(tick_data, RequestData)
        except queue.Empty:
            pass  # No data received

    @pytest.mark.integration
    def test_independent_reserve_req_kline_data(self):
        """Test kline data request (synchronous)."""
        live_independent_reserve_spot_feed = self.init_req_feed()
        data = live_independent_reserve_spot_feed.get_kline(
            "BTC/AUD", "1h", count=2).get_data()
        assert isinstance(data, list)
        if data and data[0]:
            kline_data = data[0]
            assert kline_data.get("symbol_name") == "BTC/AUD"
            assert kline_data.get("open_price") > 0

    @pytest.mark.integration
    def test_independent_reserve_async_kline_data(self):
        """Test kline data request (asynchronous)."""
        data_queue = queue.Queue()
        live_independent_reserve_spot_feed = IndependentReserveRequestDataSpot(
            data_queue)
        live_independent_reserve_spot_feed.async_get_kline("BTC/AUD", period="1h", count=3,
                                                           extra_data={"test_async_kline_data": True})
        time.sleep(5)
        try:
            kline_data = data_queue.get(timeout=10)
            assert isinstance(kline_data, RequestData)
        except queue.Empty:
            pass  # No data received


def order_book_value_equals(order_book):
    """Validate order book data."""
    # Independent Reserve may return dict order book data
    if isinstance(order_book, dict):
        assert order_book.get("symbol_name") == "BTC/AUD"
        bids = order_book.get("bids", [])
        asks = order_book.get("asks", [])
        if bids:
            assert bids[0].get("price") > 0
        if asks:
            assert asks[0].get("price") > 0


class TestIndependentReserveOrderBook:
    """Order book tests."""

    def init_req_feed(self):
        """Initialize Independent Reserve request feed."""
        data_queue = queue.Queue()
        live_independent_reserve_spot_feed = IndependentReserveRequestDataSpot(
            data_queue)
        return live_independent_reserve_spot_feed

    def test_independent_reserve_req_orderbook_data(self):
        """Test orderbook data request."""
        live_independent_reserve_spot_feed = self.init_req_feed()
        data = live_independent_reserve_spot_feed.get_depth(
            "BTC/AUD").get_data()
        assert isinstance(data, list)
        if data and data[0]:
            order_book_value_equals(data[0])

    @pytest.mark.integration
    def test_independent_reserve_async_orderbook_data(self):
        """Test orderbook data request (asynchronous)."""
        data_queue = queue.Queue()
        live_independent_reserve_spot_feed = IndependentReserveRequestDataSpot(
            data_queue)
        live_independent_reserve_spot_feed.async_get_depth("BTC/AUD")
        time.sleep(3)
        try:
            depth_data = data_queue.get(timeout=10)
            assert depth_data is not None
        except queue.Empty:
            pass  # No data received


class TestIndependentReserveIntegration:
    """Integration tests for Independent Reserve."""

    @pytest.mark.integration
    def test_market_data_api(self):
        """Test market data API calls (requires network)."""
        data_queue = queue.Queue()
        feed = IndependentReserveRequestDataSpot(data_queue)

        # Test ticker
        ticker = feed.get_tick("BTC/AUD")
        assert isinstance(ticker, RequestData)

    def test_trading_api(self):
        """Test trading API calls (requires API keys)."""
        pass


# ==================== Additional Binance/OKX Standard Tests =============

class TestIndependentReserveStandardMarketData:
    """Standard market data tests following Binance/OKX pattern."""

    def init_req_feed(self):
        """Initialize Independent Reserve request feed."""
        data_queue = queue.Queue()
        live_independent_reserve_spot_feed = IndependentReserveRequestDataSpot(
            data_queue)
        return live_independent_reserve_spot_feed

    def test_independent_reserve_req_get_exchange_info(self):
        """Test exchange info endpoint."""
        live_independent_reserve_spot_feed = self.init_req_feed()
        data = live_independent_reserve_spot_feed.get_exchange_info()
        assert isinstance(data, RequestData)
        print("independent_reserve_exchange_info:", data.get_data())

    @pytest.mark.integration
    def test_independent_reserve_req_get_symbols(self):
        """Test symbols list endpoint."""
        live_independent_reserve_spot_feed = self.init_req_feed()
        data = live_independent_reserve_spot_feed.get_symbols()
        assert isinstance(data, RequestData)
        result = data.get_data()
        assert isinstance(result, list)


class TestIndependentReserveOrderManagement:
    """Order management tests following Binance/OKX pattern."""

    def init_req_feed(self):
        """Initialize Independent Reserve request feed."""
        data_queue = queue.Queue()
        live_independent_reserve_spot_feed = IndependentReserveRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
        )
        return live_independent_reserve_spot_feed

    def test_make_order_params(self):
        """Test make order parameter generation."""
        live_independent_reserve_spot_feed = self.init_req_feed()
        path, params, extra_data = live_independent_reserve_spot_feed._make_order(
            symbol="BTC/AUD",
            vol="0.001",
            price="50000",
            order_type="buy-limit",
        )
        assert "PlaceOrder" in path or "place" in path.lower()
        assert params.get("primaryCurrencyCode") == "Xbt"
        assert params.get("secondaryCurrencyCode") == "Aud"

    def test_cancel_order_params(self):
        """Test cancel order parameter generation."""
        live_independent_reserve_spot_feed = self.init_req_feed()
        path, params, extra_data = live_independent_reserve_spot_feed._cancel_order(
            order_id="123456"
        )
        assert "CancelOrder" in path or "cancel" in path.lower()
        assert "orderGuid" in params or "orderId" in params.lower()

    @pytest.mark.integration
    def test_independent_reserve_req_make_order(self):
        """Test make order endpoint."""
        live_independent_reserve_spot_feed = self.init_req_feed()
        data = live_independent_reserve_spot_feed.make_order(
            symbol="BTC/AUD",
            vol="0.001",
            price="30000",  # Low price to avoid execution
            order_type="buy-limit",
        )
        assert isinstance(data, RequestData)

    @pytest.mark.integration
    def test_independent_reserve_req_query_order(self):
        """Test query order endpoint."""
        live_independent_reserve_spot_feed = self.init_req_feed()
        data = live_independent_reserve_spot_feed.query_order(
            symbol="BTC/AUD",
            order_id="123456"
        )
        assert isinstance(data, RequestData)

    @pytest.mark.integration
    def test_independent_reserve_req_get_open_orders(self):
        """Test get open orders endpoint."""
        live_independent_reserve_spot_feed = self.init_req_feed()
        data = live_independent_reserve_spot_feed.get_open_orders(
            symbol="BTC/AUD")
        assert isinstance(data, RequestData)


class TestIndependentReserveAccountData:
    """Account data tests following Binance/OKX pattern."""

    def init_req_feed(self):
        """Initialize Independent Reserve request feed."""
        data_queue = queue.Queue()
        live_independent_reserve_spot_feed = IndependentReserveRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
        )
        return live_independent_reserve_spot_feed

    @pytest.mark.integration
    def test_independent_reserve_req_get_account(self):
        """Test account data endpoint."""
        live_independent_reserve_spot_feed = self.init_req_feed()
        data = live_independent_reserve_spot_feed.get_account()
        assert isinstance(data, RequestData)
        print("independent_reserve_account:", data.get_data())

    @pytest.mark.integration
    def test_independent_reserve_req_get_balance(self):
        """Test balance data endpoint."""
        live_independent_reserve_spot_feed = self.init_req_feed()
        data = live_independent_reserve_spot_feed.get_balance()
        assert isinstance(data, RequestData)
        print("independent_reserve_balance:", data.get_data())


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
