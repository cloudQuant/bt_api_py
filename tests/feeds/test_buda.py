"""
Test Buda exchange integration.

Run tests:
    pytest tests/feeds/test_buda.py -v

Run with coverage:
    pytest tests/feeds/test_buda.py --cov=bt_api_py.feeds.live_buda --cov-report=term-missing

Run unit tests only (exclude integration tests):
    pytest tests/feeds/test_buda.py -v -m "not integration"
"""

import json
import queue

import pytest

from bt_api_py.containers.exchanges.buda_exchange_data import (
    BudaExchangeDataSpot,
)
from bt_api_py.containers.tickers.buda_ticker import BudaRequestTickerData
from bt_api_py.error_framework import AuthenticationError
from bt_api_py.feeds.live_buda.spot import BudaRequestDataSpot
from bt_api_py.registry import ExchangeRegistry

# Import registration to auto-register Buda
import bt_api_py.feeds.register_buda  # noqa: F401


class TestBudaExchangeData:
    """Test Buda exchange data configuration."""

    def test_exchange_data_spot_creation(self):
        """Test creating Buda spot exchange data."""
        exchange_data = BudaExchangeDataSpot()
        assert exchange_data.exchange_name == "budaSpot"
        assert exchange_data.asset_type == "spot"
        assert exchange_data.rest_url == "https://api.buda.com"
        assert exchange_data.wss_url == "wss://api.buda.com/websocket"

    def test_kline_periods(self):
        """Test kline period conversion."""
        exchange_data = BudaExchangeDataSpot()
        assert "1m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods
        assert exchange_data.kline_periods["1h"] == "3600"
        assert exchange_data.kline_periods["1d"] == "86400"

    def test_legal_currencies(self):
        """Test legal currencies."""
        exchange_data = BudaExchangeDataSpot()
        assert "CLP" in exchange_data.legal_currency
        assert "COP" in exchange_data.legal_currency
        assert "PEN" in exchange_data.legal_currency
        assert "EUR" in exchange_data.legal_currency
        assert "USDT" in exchange_data.legal_currency


class TestBudaRequestData:
    """Test Buda REST API request base class."""

    def test_request_data_creation(self):
        """Test creating Buda request data."""
        data_queue = queue.Queue()
        request_data = BudaRequestDataSpot(
            data_queue,
            exchange_name="BUDA___SPOT",
        )
        assert request_data.exchange_name == "BUDA___SPOT"


class TestBudaDataContainers:
    """Test Buda data containers."""

    def test_ticker_container(self):
        """Test ticker data container."""
        ticker_response = json.dumps({
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
        })

        ticker = BudaRequestTickerData(
            ticker_response, "btc-clp", "SPOT", False
        )
        ticker.init_data()

        assert ticker.get_exchange_name() == "BUDA"
        assert ticker.symbol_name == "btc-clp"
        assert ticker.last_price == 50000000
        assert ticker.bid_price == 49900000
        assert ticker.ask_price == 50100000
        assert ticker.volume_24h == 1.5
        assert ticker.high_24h == 51000000
        assert ticker.low_24h == 48000000

    def test_ticker_container_with_empty_data(self):
        """Test ticker data container with empty data."""
        ticker_response = json.dumps({"ticker": {}})

        ticker = BudaRequestTickerData(
            ticker_response, "btc-clp", "SPOT", False
        )
        ticker.init_data()

        # Should not raise error, just have None values
        assert ticker.last_price is None


class TestBudaRegistry:
    """Test Buda registration."""

    def test_buda_registered(self):
        """Test that Buda is properly registered."""
        # Check if feed is registered in _feed_classes
        assert "BUDA___SPOT" in ExchangeRegistry._feed_classes
        assert ExchangeRegistry._feed_classes["BUDA___SPOT"] == BudaRequestDataSpot

        # Check if exchange data is registered
        assert "BUDA___SPOT" in ExchangeRegistry._exchange_data_classes
        assert ExchangeRegistry._exchange_data_classes["BUDA___SPOT"] == BudaExchangeDataSpot

        # Check if balance handler is registered
        assert "BUDA___SPOT" in ExchangeRegistry._balance_handlers
        assert ExchangeRegistry._balance_handlers["BUDA___SPOT"] is not None

    def test_buda_create_feed(self):
        """Test creating Buda feed through registry."""
        data_queue = queue.Queue()
        feed = ExchangeRegistry.create_feed(
            "BUDA___SPOT",
            data_queue,
        )
        assert isinstance(feed, BudaRequestDataSpot)

    def test_buda_create_exchange_data(self):
        """Test creating Buda exchange data through registry."""
        exchange_data = ExchangeRegistry.create_exchange_data("BUDA___SPOT")
        assert isinstance(exchange_data, BudaExchangeDataSpot)


class TestBudaIntegration:
    """Integration tests for Buda."""

    @pytest.mark.integration
    def test_market_data_api(self):
        """Test market data API calls (requires network).

        Note: Buda API is protected by Cloudflare and may return 403 for automated requests.
        This test requires proper network access and may fail if Cloudflare blocks the request.
        """
        data_queue = queue.Queue()
        feed = BudaRequestDataSpot(data_queue)

        try:
            # Test ticker
            ticker = feed.get_tick("btc-clp")
            assert ticker.status is True
        except AuthenticationError as e:
            # Buda API is protected by Cloudflare and may reject automated requests
            # This is an expected behavior when running in CI/CD or automated environments
            pytest.skip(f"Buda API returned authentication error (likely Cloudflare protection): {e}")

    @pytest.mark.integration
    def test_trading_api(self):
        """Test trading API calls (requires API keys)."""
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
