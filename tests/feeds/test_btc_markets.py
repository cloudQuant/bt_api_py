"""
Test BTC Markets exchange integration.

Run tests:
    pytest tests/feeds/test_btc_markets.py -v

Run with coverage:
    pytest tests/feeds/test_btc_markets.py --cov=bt_api_py.feeds.live_btc_markets --cov-report=term-missing
"""

import json
import queue

import pytest

from bt_api_py.containers.exchanges.btc_markets_exchange_data import (
    BtcMarketsExchangeDataSpot,
)
from bt_api_py.containers.tickers.btc_markets_ticker import BtcMarketsRequestTickerData
from bt_api_py.feeds.live_btc_markets.spot import BtcMarketsRequestDataSpot
from bt_api_py.registry import ExchangeRegistry

# Import registration to auto-register BTC Markets
import bt_api_py.feeds.register_btc_markets  # noqa: F401


class TestBtcMarketsExchangeData:
    """Test BTC Markets exchange data configuration."""

    def test_exchange_data_spot_creation(self):
        """Test creating BTC Markets spot exchange data."""
        exchange_data = BtcMarketsExchangeDataSpot()
        assert exchange_data.exchange_name == "btc_markets"
        assert exchange_data.asset_type == "spot"
        assert exchange_data.rest_url == "https://api.btcmarkets.net"
        assert exchange_data.wss_url == "wss://socket.btcmarkets.net/v3"

    def test_kline_periods(self):
        """Test kline period conversion."""
        exchange_data = BtcMarketsExchangeDataSpot()
        assert "1m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods
        assert exchange_data.kline_periods["1m"] == "1m"
        assert exchange_data.kline_periods["1h"] == "1h"
        assert exchange_data.kline_periods["1d"] == "1d"

    def test_legal_currencies(self):
        """Test legal currencies."""
        exchange_data = BtcMarketsExchangeDataSpot()
        assert "AUD" in exchange_data.legal_currency


class TestBtcMarketsRequestData:
    """Test BTC Markets REST API request base class."""

    def test_request_data_creation(self):
        """Test creating BTC Markets request data."""
        data_queue = queue.Queue()
        request_data = BtcMarketsRequestDataSpot(
            data_queue,
            exchange_name="BTC_MARKETS___SPOT",
        )
        assert request_data.exchange_name == "BTC_MARKETS___SPOT"


class TestBtcMarketsDataContainers:
    """Test BTC Markets data containers."""

    def test_ticker_container(self):
        """Test ticker data container."""
        ticker_response = json.dumps({
            "marketId": "BTC-AUD",
            "lastPrice": "50000.00",
            "bestBid": "49900.00",
            "bestAsk": "50100.00",
            "volume24h": "100.50",
            "high24h": "51000.00",
            "low24h": "48000.00",
        })

        ticker = BtcMarketsRequestTickerData(
            ticker_response, "BTC-AUD", "SPOT", False
        )
        ticker.init_data()

        assert ticker.get_exchange_name() == "BTC_MARKETS"
        assert ticker.symbol_name == "BTC-AUD"
        assert ticker.last_price == 50000.00
        assert ticker.bid_price == 49900.00
        assert ticker.ask_price == 50100.00
        assert ticker.volume_24h == 100.50
        assert ticker.high_24h == 51000.00
        assert ticker.low_24h == 48000.00

    def test_ticker_container_with_json_string(self):
        """Test ticker data container with JSON string input."""
        ticker_data = {
            "marketId": "ETH-AUD",
            "lastPrice": "3000.00",
            "bestBid": "2990.00",
            "bestAsk": "3010.00",
        }
        ticker_response = json.dumps(ticker_data)

        ticker = BtcMarketsRequestTickerData(
            ticker_response, "ETH-AUD", "SPOT", False
        )
        ticker.init_data()

        assert ticker.last_price == 3000.00
        assert ticker.bid_price == 2990.00
        assert ticker.ask_price == 3010.00


class TestBtcMarketsRegistry:
    """Test BTC Markets registration."""

    def test_btc_markets_registered(self):
        """Test that BTC Markets is properly registered."""
        # Check if feed is registered in _feed_classes
        assert "BTC_MARKETS___SPOT" in ExchangeRegistry._feed_classes
        assert ExchangeRegistry._feed_classes["BTC_MARKETS___SPOT"] == BtcMarketsRequestDataSpot

        # Check if exchange data is registered
        assert "BTC_MARKETS___SPOT" in ExchangeRegistry._exchange_data_classes
        assert ExchangeRegistry._exchange_data_classes["BTC_MARKETS___SPOT"] == BtcMarketsExchangeDataSpot

        # Check if balance handler is registered
        assert "BTC_MARKETS___SPOT" in ExchangeRegistry._balance_handlers
        assert ExchangeRegistry._balance_handlers["BTC_MARKETS___SPOT"] is not None

    def test_btc_markets_create_feed(self):
        """Test creating BTC Markets feed through registry."""
        data_queue = queue.Queue()
        feed = ExchangeRegistry.create_feed(
            "BTC_MARKETS___SPOT",
            data_queue,
        )
        assert isinstance(feed, BtcMarketsRequestDataSpot)

    def test_btc_markets_create_exchange_data(self):
        """Test creating BTC Markets exchange data through registry."""
        exchange_data = ExchangeRegistry.create_exchange_data("BTC_MARKETS___SPOT")
        assert isinstance(exchange_data, BtcMarketsExchangeDataSpot)


class TestBtcMarketsIntegration:
    """Integration tests for BTC Markets."""

    def test_market_data_api(self):
        """Test market data API calls (requires network)."""
        data_queue = queue.Queue()
        feed = BtcMarketsRequestDataSpot(data_queue)

        # Test ticker
        ticker = feed.get_tick("BTC-AUD")
        ticker.init_data()  # Initialize data to process the response
        assert ticker.status is True

    def test_trading_api(self):
        """Test trading API calls (requires API keys)."""
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
