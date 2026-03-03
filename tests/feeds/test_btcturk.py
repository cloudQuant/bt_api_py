"""
Test BTCTurk exchange integration.

Run tests:
    pytest tests/feeds/test_btcturk.py -v

Run with coverage:
    pytest tests/feeds/test_btcturk.py --cov=bt_api_py.feeds.live_btcturk --cov-report=term-missing
"""

import json
import queue

import pytest

from bt_api_py.containers.exchanges.btcturk_exchange_data import (
    BTCTurkExchangeDataSpot,
)
from bt_api_py.containers.tickers.btcturk_ticker import BTCTurkRequestTickerData
from bt_api_py.feeds.live_btcturk.spot import BTCTurkRequestDataSpot
from bt_api_py.registry import ExchangeRegistry

# Import registration to auto-register BTCTurk
import bt_api_py.feeds.register_btcturk  # noqa: F401


class TestBTCTurkExchangeData:
    """Test BTCTurk exchange data configuration."""

    def test_exchange_data_spot_creation(self):
        """Test creating BTCTurk spot exchange data."""
        exchange_data = BTCTurkExchangeDataSpot()
        assert exchange_data.exchange_name == "btcturkSpot"
        assert exchange_data.asset_type == "spot"
        assert exchange_data.rest_url == "https://api.btcturk.com"
        assert exchange_data.wss_url == "wss://ws-feed-pro.btcturk.com"

    def test_kline_periods(self):
        """Test kline period conversion."""
        exchange_data = BTCTurkExchangeDataSpot()
        assert "1m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods
        assert exchange_data.kline_periods["1h"] == "60"
        assert exchange_data.kline_periods["1d"] == "1440"

    def test_legal_currencies(self):
        """Test legal currencies."""
        exchange_data = BTCTurkExchangeDataSpot()
        assert "USDT" in exchange_data.legal_currency
        assert "TRY" in exchange_data.legal_currency
        assert "BTC" in exchange_data.legal_currency


class TestBTCTurkRequestData:
    """Test BTCTurk REST API request base class."""

    def test_request_data_creation(self):
        """Test creating BTCTurk request data."""
        data_queue = queue.Queue()
        request_data = BTCTurkRequestDataSpot(
            data_queue,
            exchange_name="BTCTURK___SPOT",
        )
        assert request_data.exchange_name == "BTCTURK___SPOT"


class TestBTCTurkDataContainers:
    """Test BTCTurk data containers."""

    def test_ticker_container(self):
        """Test ticker data container with array format."""
        # BTCTurk returns data in array format
        ticker_response = json.dumps({
            "data": [
            {
                "pairSymbol": "BTCUSDT",
                "last": "50000.00",
                "bid": "49900.00",
                "ask": "50100.00",
                "volume": "100.50",
                "high": "51000.00",
                "low": "48000.00",
                "timestamp": 1640995200000,
            }
            ]
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
        """Test ticker data container with dict format."""
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
        """Test that BTCTurk is properly registered."""
        # Check if feed is registered in _feed_classes
        assert "BTCTURK___SPOT" in ExchangeRegistry._feed_classes
        assert ExchangeRegistry._feed_classes["BTCTURK___SPOT"] == BTCTurkRequestDataSpot

        # Check if exchange data is registered
        assert "BTCTURK___SPOT" in ExchangeRegistry._exchange_data_classes
        assert ExchangeRegistry._exchange_data_classes["BTCTURK___SPOT"] == BTCTurkExchangeDataSpot

        # Check if balance handler is registered
        assert "BTCTURK___SPOT" in ExchangeRegistry._balance_handlers
        assert ExchangeRegistry._balance_handlers["BTCTURK___SPOT"] is not None

    def test_btcturk_create_feed(self):
        """Test creating BTCTurk feed through registry."""
        data_queue = queue.Queue()
        feed = ExchangeRegistry.create_feed(
            "BTCTURK___SPOT",
            data_queue,
        )
        assert isinstance(feed, BTCTurkRequestDataSpot)

    def test_btcturk_create_exchange_data(self):
        """Test creating BTCTurk exchange data through registry."""
        exchange_data = ExchangeRegistry.create_exchange_data("BTCTURK___SPOT")
        assert isinstance(exchange_data, BTCTurkExchangeDataSpot)


class TestBTCTurkIntegration:
    """Integration tests for BTCTurk."""

    def test_market_data_api(self):
        """Test market data API calls (requires network)."""
        data_queue = queue.Queue()
        feed = BTCTurkRequestDataSpot(data_queue)

        # Test ticker
        ticker = feed.get_tick("BTCUSDT")
        assert ticker is not None
        # Use get_status() which automatically calls init_data()
        assert ticker.get_status() is True

    def test_trading_api(self):
        """Test trading API calls (requires API keys)."""
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
