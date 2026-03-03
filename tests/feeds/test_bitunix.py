"""
Test Bitunix exchange integration.

Run tests:
    pytest tests/feeds/test_bitunix.py -v

Run with coverage:
    pytest tests/feeds/test_bitunix.py --cov=bt_api_py.feeds.live_bitunix --cov-report=term-missing
"""

import json
import queue
from unittest.mock import Mock, patch
from unittest.mock import MagicMock

import pytest

from bt_api_py.containers.exchanges.bitunix_exchange_data import (
    BitunixExchangeData,
    BitunixExchangeDataSpot,
)
from bt_api_py.feeds.live_bitunix.spot import BitunixRequestDataSpot
from bt_api_py.registry import ExchangeRegistry

# Import registration to auto-register Bitunix
import bt_api_py.feeds.register_bitunix  # noqa: F401


@pytest.fixture
def mock_logger():
    """Mock the logger to avoid AttributeError with warning method."""
    with patch("bt_api_py.containers.exchanges.bitunix_exchange_data.logger") as mock_log:
        mock_logger.warning = MagicMock()
        yield mock_log


class TestBitunixExchangeData:
    """Test Bitunix exchange data configuration."""

    def test_exchange_data_spot_creation(self, mock_logger):
        """Test creating Bitunix spot exchange data."""
        exchange_data = BitunixExchangeDataSpot()
        assert exchange_data.exchange_name == "bitunix"
        assert exchange_data.asset_type == "spot"
        # rest_url may be set via config or have a default
        assert hasattr(exchange_data, "rest_url")

    def test_kline_periods(self, mock_logger):
        """Test kline periods are defined."""
        exchange_data = BitunixExchangeDataSpot()
        assert "1m" in exchange_data.kline_periods
        assert "1h" in exchange_data.kline_periods
        assert "1d" in exchange_data.kline_periods

    def test_legal_currencies(self, mock_logger):
        """Test legal currencies are defined."""
        exchange_data = BitunixExchangeDataSpot()
        assert "USDT" in exchange_data.legal_currency
        assert "USD" in exchange_data.legal_currency

    def test_wss_url(self, mock_logger):
        """Test WebSocket URL is configured."""
        exchange_data = BitunixExchangeDataSpot()
        assert exchange_data.wss_url != ""
        assert "ws" in exchange_data.wss_url.lower()


class TestBitunixRequestDataSpot:
    """Test Bitunix REST API request methods."""

    def test_request_data_creation(self):
        """Test creating Bitunix request data."""
        data_queue = queue.Queue()
        request_data = BitunixRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
            exchange_name="BITUNIX___SPOT",
        )
        assert request_data.exchange_name == "BITUNIX___SPOT"
        assert request_data.asset_type == "SPOT"

    def test_normalize_functions_exist(self):
        """Test that normalization functions exist."""
        # Test ticker normalize function exists
        input_data = {
            "code": 0,
            "data": [
            {
                "symbol": "BTCUSDT",
                "lastPrice": "50000",
            }
            ]
        }
        extra_data = {"symbol_name": "BTCUSDT"}

        result, success = BitunixRequestDataSpot._get_tick_normalize_function(
            input_data, extra_data
        )
        assert success is True

    def test_depth_normalize_function(self):
        """Test depth normalization function."""
        input_data = {
            "code": 0,
            "data": {
            "bids": [["49990", "1.0"], ["49980", "2.0"]],
            "asks": [["50010", "1.0"], ["50020", "2.0"]],
            }
        }
        extra_data = {"symbol_name": "BTCUSDT"}

        result, success = BitunixRequestDataSpot._get_depth_normalize_function(
            input_data, extra_data
        )

        assert success is True
        assert "bids" in result[0]

    def test_kline_normalize_function(self):
        """Test kline normalization function."""
        input_data = {
            "code": 0,
            "data": [
            [1640995200, "49500", "51000", "49000", "50000", "1234.56"]
            ]
        }
        extra_data = {"symbol_name": "BTCUSDT"}

        result, success = BitunixRequestDataSpot._get_kline_normalize_function(
            input_data, extra_data
        )

        assert success is True
        assert isinstance(result[0], list)


class TestBitunixRegistration:
    """Test Bitunix registration."""

    def test_bitunix_registered(self):
        """Test that Bitunix is properly registered."""
        # Check if feed is registered in _feed_classes
        assert "BITUNIX___SPOT" in ExchangeRegistry._feed_classes
        assert ExchangeRegistry._feed_classes["BITUNIX___SPOT"] == BitunixRequestDataSpot

        # Check if exchange data is registered
        assert "BITUNIX___SPOT" in ExchangeRegistry._exchange_data_classes
        assert ExchangeRegistry._exchange_data_classes["BITUNIX___SPOT"] == BitunixExchangeDataSpot

        # Check if balance handler is registered
        assert "BITUNIX___SPOT" in ExchangeRegistry._balance_handlers
        assert ExchangeRegistry._balance_handlers["BITUNIX___SPOT"] is not None

    def test_bitunix_create_exchange_data(self):
        """Test creating Bitunix exchange data through registry."""
        exchange_data = ExchangeRegistry.create_exchange_data("BITUNIX___SPOT")
        assert isinstance(exchange_data, BitunixExchangeDataSpot)


class TestBitunixIntegration:
    """Integration tests for Bitunix."""

    def test_market_data_api(self):
        """Test market data API calls (requires network)."""
        pass

    def test_trading_api(self):
        """Test trading API calls (requires API keys)."""
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
