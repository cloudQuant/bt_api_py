"""
Test Upbit exchange integration.

Run tests:
    pytest tests/feeds/test_upbit.py -v

Run with coverage:
    pytest tests/feeds/test_upbit.py --cov=bt_api_py.feeds.live_upbit --cov-report=term-missing
"""

import queue

import pytest

from bt_api_py.containers.exchanges.upbit_exchange_data import UpbitExchangeDataSpot
from bt_api_py.containers.tickers.upbit_ticker import UpbitTickerData
from bt_api_py.feeds.live_upbit.spot import UpbitSpotFeed
from bt_api_py.registry import ExchangeRegistry

# Import registration to auto-register Upbit
import bt_api_py.feeds.register_upbit  # noqa: F401


class TestUpbitExchangeData:
    """Test Upbit exchange data configuration."""

    def test_exchange_data_spot_creation(self):
        """Test creating Upbit spot exchange data."""
        exchange_data = UpbitExchangeDataSpot()
        # exchange_name is loaded from YAML config
        assert exchange_data.rest_url
        # Upbit uses 'ticker' not 'get_ticker' in rest_paths
        assert "ticker" in exchange_data.rest_paths

    def test_get_symbol(self):
        """Test symbol format conversion."""
        exchange_data = UpbitExchangeDataSpot()
        assert exchange_data.get_symbol("BTC-USDT") == "BTCUSDT"
        assert exchange_data.get_symbol("KRW-BTC") == "KRWBTC"

    def test_account_wss_symbol(self):
        """Test account WebSocket symbol format conversion."""
        exchange_data = UpbitExchangeDataSpot()
        assert exchange_data.account_wss_symbol("BTCUSDT") == "btc/usdt"
        assert exchange_data.account_wss_symbol("KRWBTC") == "krw/btc"

    def test_get_period(self):
        """Test kline period conversion."""
        exchange_data = UpbitExchangeDataSpot()
        assert exchange_data.get_period("1m") == "1"
        assert exchange_data.get_period("1h") == "60"
        assert exchange_data.get_period("1d") == "D"

    def test_kline_periods(self):
        """Test kline period configuration."""
        exchange_data = UpbitExchangeDataSpot()
        assert "1m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods

    def test_legal_currency(self):
        """Test legal currencies."""
        exchange_data = UpbitExchangeDataSpot()
        assert "KRW" in exchange_data.legal_currency
        assert "USDT" in exchange_data.legal_currency
        assert "BTC" in exchange_data.legal_currency


class TestUpbitSpotFeed:
    """Test Upbit spot feed class."""

    def test_feed_creation(self):
        """Test creating Upbit spot feed."""
        feed = UpbitSpotFeed()
        assert feed.exchange_name == "UPBIT___SPOT"
        assert feed.asset_type == "spot"

    def test_exchange_data_initialization(self):
        """Test exchange data is properly initialized."""
        feed = UpbitSpotFeed()
        assert feed.exchange_data is not None
        assert isinstance(feed.exchange_data, UpbitExchangeDataSpot)

    def test_rate_limiter_initialization(self):
        """Test rate limiter is properly initialized."""
        feed = UpbitSpotFeed()
        assert feed.rate_limiter is not None


class TestUpbitDataContainers:
    """Test Upbit data containers."""

    def test_ticker_container(self):
        """Test ticker data container."""
        ticker_response = {
            "market": "KRW-BTC",
            "trade_price": 50000000,
            "bid_price": 49990000,
            "ask_price": 50010000,
            "acc_trade_volume_24h": 1000,
            "high_price": 51000000,
            "low_price": 49000000,
            "opening_price": 49500000,
        }

        # UpbitTickerData has different init signature
        ticker = UpbitTickerData(
            ticker_response,
            symbol_name="KRW-BTC",
            asset_type="spot",
        )
        ticker.init_data()

        assert ticker.get_exchange_name() == "UPBIT"
        assert ticker.get_symbol_name() == "KRW-BTC"
        assert ticker.get_last_price() == 50000000


class TestUpbitRegistry:
    """Test Upbit registration."""

    def test_upbit_registered(self):
        """Test that Upbit is properly registered."""
        assert "UPBIT___SPOT" in ExchangeRegistry._feed_classes
        assert "UPBIT___SPOT" in ExchangeRegistry._exchange_data_classes
        # Upbit does not register a balance handler in the standard way
        # It uses a custom subscribe handler

    def test_upbit_create_feed(self):
        """Test creating Upbit feed through registry."""
        data_queue = queue.Queue()
        feed = ExchangeRegistry.create_feed(
            "UPBIT___SPOT",
            data_queue,
        )
        assert isinstance(feed, UpbitSpotFeed)

    def test_upbit_create_exchange_data(self):
        """Test creating Upbit exchange data through registry."""
        exchange_data = ExchangeRegistry.create_exchange_data("UPBIT___SPOT")
        assert isinstance(exchange_data, UpbitExchangeDataSpot)


class TestUpbitIntegration:
    """Integration tests for Upbit (marked as integration)."""

    @pytest.mark.integration
    def test_get_ticker_live(self):
        """Test getting ticker from live API."""
        pass

    @pytest.mark.integration
    def test_get_orderbook_live(self):
        """Test getting orderbook from live API."""
        pass

    @pytest.mark.integration
    def test_place_order_live(self):
        """Test placing order on live API."""
        pass

    @pytest.mark.integration
    def test_websocket_connection(self):
        """Test WebSocket connection."""
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
