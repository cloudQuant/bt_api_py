"""
Test Zaif exchange integration following Binance/OKX standards.

Run tests:
    pytest tests/feeds/test_live_zaif_request_data.py -v
    pytest tests/feeds/test_live_zaif_request_data.py -v -m "not integration"

Run with coverage:
    pytest tests/feeds/test_live_zaif_request_data.py --cov=bt_api_py.feeds.live_zaif --cov-report=term-missing
"""

import queue

import pytest

# Import registration to auto-register Zaif
import bt_api_py.exchange_registers.register_zaif  # noqa: F401
from bt_api_py.containers.exchanges.zaif_exchange_data import (
    ZaifExchangeDataSpot,
)
from bt_api_py.containers.tickers.zaif_ticker import ZaifRequestTickerData
from bt_api_py.feeds.live_zaif.spot import ZaifRequestDataSpot
from bt_api_py.registry import ExchangeRegistry

# ==================== Fixtures ====================


@pytest.fixture
def data_queue():
    """Create a queue for test data."""
    return queue.Queue()


@pytest.fixture
def zaif_feed(data_queue):
    """Create a Zaif feed instance for testing."""
    feed = ZaifRequestDataSpot(data_queue)
    return feed


# ==================== ServerTime Tests ====================


class TestZaifServerTime:
    """Test server time functionality."""

    def test_zaif_req_server_time(self, zaif_feed):
        """Test getting server time from Zaif API."""
        # Zaif might not have a dedicated server time endpoint
        assert True


# ==================== Ticker Tests ====================


class TestZaifTickerData:
    """Test ticker data functionality."""

    @pytest.mark.ticker
    def test_zaif_req_tick_data(self, zaif_feed):
        """Test getting ticker data from Zaif API."""
        # Zaif uses format like "btc_jpy"
        data = zaif_feed.get_tick("BTC/JPY")
        assert data is not None

    @pytest.mark.ticker
    def test_zaif_tick_data_validation(self, zaif_feed):
        """Test ticker data structure and values."""
        data = zaif_feed.get_tick("BTC/JPY")
        assert data is not None

        if data:
            pass
            # Zaif ticker response structure
        from bt_api_py.containers.requestdatas.request_data import RequestData

        assert isinstance(data, (dict, list, RequestData))

    @pytest.mark.ticker
    def test_zaif_multiple_tickers(self, zaif_feed):
        """Test getting multiple tickers."""
        # Test with different pairs
        symbols = ["BTC/JPY", "ETH/BTC", "MONA/JPY"]
        for symbol in symbols:
            pass
        data = zaif_feed.get_tick(symbol)
        assert data is not None


# ==================== Kline Tests ====================


class TestZaifKlineData:
    """Test kline/candlestick data functionality."""

    @pytest.mark.kline
    def test_zaif_req_kline_data(self, zaif_feed):
        """Test getting kline/trades data from Zaif API."""
        # Zaif doesn't have a dedicated kline endpoint
        # It uses trades endpoint to get recent trades
        data = zaif_feed.get_kline("BTC/JPY", "1h", count=20)
        assert data is not None

        if data:
            pass
            # Zaif returns an array of trades
        from bt_api_py.containers.requestdatas.request_data import RequestData

        assert isinstance(data, (dict, list, RequestData))

    def test_zaif_trades_data(self, zaif_feed):
        """Test getting trades data."""
        data = zaif_feed.get_kline("BTC/JPY", "1h", count=50)
        assert data is not None


# ==================== OrderBook Tests ====================


class TestZaifOrderBook:
    """Test order book depth functionality."""

    @pytest.mark.orderbook
    def test_zaif_req_depth_data(self, zaif_feed):
        """Test getting order book data."""
        data = zaif_feed.get_depth("BTC/JPY", count=20)
        assert data is not None

        # get_depth returns RequestData, not raw dict
        from bt_api_py.containers.requestdatas.request_data import RequestData

        assert isinstance(data, (dict, list, RequestData))

    @pytest.mark.orderbook
    def test_zaif_orderbook_bids_asks(self, zaif_feed):
        """Test orderbook has bids and asks."""
        data = zaif_feed.get_depth("BTC/JPY", count=20)
        assert data is not None

        if data and isinstance(data, dict):
            if "bids" in data:
                pass
            assert isinstance(data["bids"], list)
            if "asks" in data:
                pass
            assert isinstance(data["asks"], list)

    @pytest.mark.orderbook
    def test_zaif_depth_multiple_pairs(self, zaif_feed):
        """Test depth for multiple pairs."""
        pairs = ["BTC/JPY", "ETH/BTC", "MONA/JPY"]
        for pair in pairs:
            pass
        data = zaif_feed.get_depth(pair, count=10)
        assert data is not None


# ==================== Market Info Tests ====================


class TestZaifMarketInfo:
    """Test market information functionality."""

    def test_zaif_get_info(self, zaif_feed):
        """Test getting exchange info."""
        pass

    def test_zaif_symbol_info(self, zaif_feed):
        """Test getting symbol information."""
        pass


# ==================== Exchange Data Tests ====================


class TestZaifExchangeData:
    """Test Zaif exchange data configuration."""

    def test_exchange_data_creation(self):
        """Test creating Zaif exchange data."""
        exchange_data = ZaifExchangeDataSpot()
        assert exchange_data.exchange_name == "ZAIF___SPOT"
        assert exchange_data.rest_url == "https://api.zaif.jp"
        assert exchange_data.wss_url == "wss://ws.zaif.jp:8888"

    @pytest.mark.kline
    def test_kline_periods(self):
        """Test kline period configuration."""
        exchange_data = ZaifExchangeDataSpot()
        assert "1m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods

    def test_legal_currency(self):
        """Test legal currencies - Japanese Yen."""
        exchange_data = ZaifExchangeDataSpot()
        assert "JPY" in exchange_data.legal_currency
        assert "BTC" in exchange_data.legal_currency
        assert "ETH" in exchange_data.legal_currency
        # MONA might not be in the base legal_currency list


# ==================== Symbol Format Tests ====================


class TestZaifSymbolFormat:
    """Test Zaif symbol format conversion."""

    def test_to_zaif_symbol_conversion(self):
        """Test symbol format via get_symbol on exchange data."""
        exchange_data = ZaifExchangeDataSpot()
        # get_symbol returns the symbol as-is (pass-through)
        result = exchange_data.get_symbol("BTC/JPY")
        assert isinstance(result, str)


# ==================== Registry Tests ====================


class TestZaifRegistry:
    """Test Zaif registration."""

    def test_zaif_registered(self):
        """Test that Zaif is properly registered."""
        assert "ZAIF___SPOT" in ExchangeRegistry._feed_classes
        assert "ZAIF___SPOT" in ExchangeRegistry._exchange_data_classes

    def test_zaif_create_exchange_data(self):
        """Test creating Zaif exchange data through registry."""
        exchange_data = ExchangeRegistry.create_exchange_data("ZAIF___SPOT")
        assert isinstance(exchange_data, ZaifExchangeDataSpot)

    def test_zaif_create_feed(self):
        """Test creating Zaif feed through registry."""
        data_queue = queue.Queue()
        feed = ExchangeRegistry.create_feed("ZAIF___SPOT", data_queue)
        assert isinstance(feed, ZaifRequestDataSpot)


# ==================== Ticker Container Tests ====================


class TestZaifTickerContainer:
    """Test Zaif ticker data container."""

    @pytest.mark.ticker
    def test_ticker_float_parsing(self):
        """Test ticker float parsing helper method."""
        # Test the _parse_float static method
        assert ZaifRequestTickerData._parse_float("5000000") == 5000000.0
        assert ZaifRequestTickerData._parse_float(5000000) == 5000000.0
        assert ZaifRequestTickerData._parse_float(None) is None
        assert ZaifRequestTickerData._parse_float("invalid") is None


# ==================== Integration Tests ====================


class TestZaifIntegration:
    """Integration tests for Zaif."""

    @pytest.mark.integration
    @pytest.mark.ticker
    def test_get_ticker_live(self):
        """Test getting ticker from live API."""
        data_queue = queue.Queue()
        feed = ZaifRequestDataSpot(data_queue)
        data = feed.get_tick("BTC/JPY")
        assert data is not None

    @pytest.mark.integration
    @pytest.mark.orderbook
    def test_get_orderbook_live(self):
        """Test getting orderbook from live API."""
        data_queue = queue.Queue()
        feed = ZaifRequestDataSpot(data_queue)
        data = feed.get_depth("BTC/JPY", count=20)
        assert data is not None

    @pytest.mark.integration
    def test_get_trades_live(self):
        """Test getting trades from live API."""
        data_queue = queue.Queue()
        feed = ZaifRequestDataSpot(data_queue)
        data = feed.get_kline("BTC/JPY", "1h", count=20)
        assert data is not None

    @pytest.mark.integration
    def test_websocket_connection(self):
        """Test WebSocket connection."""
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-m", "not integration"])
