"""
Test Bitvavo exchange integration.

Run tests:
    pytest tests/feeds/test_bitvavo.py -v

Run with coverage:
    pytest tests/feeds/test_bitvavo.py --cov=bt_api_py.feeds.live_bitvavo --cov-report=term-missing
"""

import json
import queue
from unittest.mock import Mock, patch
from unittest.mock import MagicMock

import pytest

from bt_api_py.containers.exchanges.bitvavo_exchange_data import (
    BitvavoExchangeData,
    BitvavoExchangeDataSpot,
)
from bt_api_py.feeds.live_bitvavo.spot import BitvavoRequestDataSpot
from bt_api_py.registry import ExchangeRegistry

# Import registration to auto-register Bitvavo
import bt_api_py.feeds.register_bitvavo  # noqa: F401


@pytest.fixture
def mock_logger():
    """Mock the logger to avoid AttributeError with warning method."""
    with patch("bt_api_py.containers.exchanges.bitvavo_exchange_data.logger") as mock_log:
        mock_logger.warning = MagicMock()
        yield mock_log


class TestBitvavoExchangeData:
    """Test Bitvavo exchange data configuration."""

    def test_exchange_data_spot_creation(self, mock_logger):
        """Test creating Bitvavo spot exchange data."""
        exchange_data = BitvavoExchangeDataSpot()
        assert exchange_data.exchange_name == "bitvavo"
        assert exchange_data.asset_type == "spot"
        # rest_url may be set via config or have a default
        assert hasattr(exchange_data, "rest_url")

    def test_kline_periods(self, mock_logger):
        """Test kline periods are defined."""
        exchange_data = BitvavoExchangeDataSpot()
        assert "1m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods

    def test_legal_currencies(self, mock_logger):
        """Test legal currencies are defined."""
        exchange_data = BitvavoExchangeDataSpot()
        assert "EUR" in exchange_data.legal_currency
        assert "USDT" in exchange_data.legal_currency

    def test_wss_url(self, mock_logger):
        """Test WebSocket URL is configured."""
        exchange_data = BitvavoExchangeDataSpot()
        assert exchange_data.wss_url != ""
        assert "ws" in exchange_data.wss_url.lower()


class TestBitvavoRequestDataSpot:
    """Test Bitvavo REST API request methods."""

    def test_request_data_creation(self):
        """Test creating Bitvavo request data."""
        data_queue = queue.Queue()
        request_data = BitvavoRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
            exchange_name="BITVAVO___SPOT",
        )
        assert request_data.exchange_name == "BITVAVO___SPOT"
        assert request_data.asset_type == "SPOT"

    def test_normalize_functions_exist(self):
        """Test that normalization functions exist."""
        # Test ticker normalize function exists
        input_data = {
            "market": "BTC-EUR",
            "last": "50000",
            "bid": "49990",
            "ask": "50010",
        }
        extra_data = {"symbol_name": "BTC-EUR"}

        result, success = BitvavoRequestDataSpot._get_tick_normalize_function(
            input_data, extra_data
        )
        assert success is True

    def test_depth_normalize_function(self):
        """Test depth normalization function."""
        input_data = {
            "bids": [
            {"price": "49990", "size": "1.0"},
            {"price": "49980", "size": "2.0"},
            ],
            "asks": [
            {"price": "50010", "size": "1.0"},
            {"price": "50020", "size": "2.0"},
            ],
        }
        extra_data = {"symbol_name": "BTC-EUR"}

        result, success = BitvavoRequestDataSpot._get_depth_normalize_function(
            input_data, extra_data
        )

        assert success is True
        assert "bids" in result[0]

    def test_kline_normalize_function(self):
        """Test kline normalization function."""
        input_data = [
            [1640995200000, "49500", "51000", "49000", "50000", "1234.56"]
        ]
        extra_data = {"symbol_name": "BTC-EUR"}

        result, success = BitvavoRequestDataSpot._get_kline_normalize_function(
            input_data, extra_data
        )

        assert success is True
        assert isinstance(result[0], list)


class TestBitvavoRegistration:
    """Test Bitvavo registration."""

    def test_bitvavo_registered(self):
        """Test that Bitvavo is properly registered."""
        # Check if feed is registered in _feed_classes
        assert "BITVAVO___SPOT" in ExchangeRegistry._feed_classes
        assert ExchangeRegistry._feed_classes["BITVAVO___SPOT"] == BitvavoRequestDataSpot

        # Check if exchange data is registered
        assert "BITVAVO___SPOT" in ExchangeRegistry._exchange_data_classes
        assert ExchangeRegistry._exchange_data_classes["BITVAVO___SPOT"] == BitvavoExchangeDataSpot

        # Check if balance handler is registered
        assert "BITVAVO___SPOT" in ExchangeRegistry._balance_handlers
        assert ExchangeRegistry._balance_handlers["BITVAVO___SPOT"] is not None

    def test_bitvavo_create_exchange_data(self):
        """Test creating Bitvavo exchange data through registry."""
        exchange_data = ExchangeRegistry.create_exchange_data("BITVAVO___SPOT")
        assert isinstance(exchange_data, BitvavoExchangeDataSpot)


class TestBitvavoIntegration:
    """Integration tests for Bitvavo."""

    def test_market_data_api(self):
        """Test market data API calls (requires network)."""
        pass

    def test_trading_api(self):
        """Test trading API calls (requires API keys)."""
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
