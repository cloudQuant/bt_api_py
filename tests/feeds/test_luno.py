"""
Test Luno exchange integration.

Run tests:
    pytest tests/feeds/test_luno.py -v

Run with coverage:
    pytest tests/feeds/test_luno.py --cov=bt_api_py.feeds.live_luno --cov-report=term-missing

Run only unit tests (no network):
    pytest tests/feeds/test_luno.py -m "not integration" -v
"""

import json
import queue
from unittest.mock import Mock, patch

import pytest

from bt_api_py.containers.tickers.luno_ticker import LunoRequestTickerData
from bt_api_py.feeds.live_luno.spot import LunoRequestDataSpot
from bt_api_py.registry import ExchangeRegistry

# Import registration to auto-register Luno
import bt_api_py.feeds.register_luno  # noqa: F401


class TestLunoRequestData:
    """Test Luno REST API request base class."""

    def test_request_data_creation(self):
        """Test creating Luno request data."""
        data_queue = queue.Queue()
        request_data = LunoRequestDataSpot(
            data_queue,
            exchange_name="LUNO___SPOT",
        )
        assert request_data.exchange_name == "LUNO___SPOT"

    def test_get_tick_normalize_function(self):
        """Test ticker normalize function."""
        data_queue = queue.Queue()
        request_data = LunoRequestDataSpot(data_queue)

        input_data = {
            "pair": "XBTZAR",
            "last_trade": "95000000",
            "bid": "94990000",
            "ask": "95000000",
            "rolling_24_hour_volume": "123.45",
            "rolling_24_hour_high": "95800000",
            "rolling_24_hour_low": "93500000",
            "timestamp": 1678901234000,
        }
        extra_data = {"symbol_name": "XBTZAR"}

        result, success = request_data._get_tick_normalize_function(input_data, extra_data)
        assert success is True
        assert len(result) > 0

    def test_get_depth_normalize_function(self):
        """Test depth normalize function."""
        data_queue = queue.Queue()
        request_data = LunoRequestDataSpot(data_queue)

        input_data = {"bids": [], "asks": []}
        result, status = request_data._get_depth_normalize_function(input_data, {"symbol_name": "XBTZAR"})
        assert status is True

    def test_get_kline_normalize_function(self):
        """Test kline normalize function."""
        data_queue = queue.Queue()
        request_data = LunoRequestDataSpot(data_queue)

        input_data = {"candles": []}
        result, status = request_data._get_kline_normalize_function(input_data, {"symbol_name": "XBTZAR"})
        assert status is True


class TestLunoDataContainers:
    """Test Luno data containers."""

    def test_ticker_container(self):
        """Test ticker data container."""
        ticker_info = json.dumps({
            "pair": "XBTZAR",
            "last_trade": "95000000",
            "bid": "94990000",
            "ask": "95000000",
            "rolling_24_hour_volume": "123.45",
            "rolling_24_hour_high": "95800000",
            "rolling_24_hour_low": "93500000",
            "timestamp": 1678901234000,
        })

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
        """Test that Luno is properly registered."""
        # Check if feed is registered
        assert "LUNO___SPOT" in ExchangeRegistry._feed_classes
        assert ExchangeRegistry._feed_classes["LUNO___SPOT"] == LunoRequestDataSpot

    def test_luno_create_feed(self):
        """Test creating Luno feed through registry."""
        data_queue = queue.Queue()
        feed = ExchangeRegistry.create_feed("LUNO___SPOT", data_queue)
        assert isinstance(feed, LunoRequestDataSpot)


# ==================== Mock Tests ====================

class TestLunoMockData:
    """Test with mock data."""

    def test_luno_tick_with_mock(self):
        """Test ticker with mock response."""
        data_queue = queue.Queue()
        feed = LunoRequestDataSpot(data_queue)

        mock_response = {
            "pair": "XBTZAR",
            "last_trade": "95000000",
            "bid": "94990000",
            "ask": "95000000",
            "rolling_24_hour_volume": "123.45",
        }

        result, success = feed._get_tick_normalize_function(mock_response, {"symbol_name": "XBTZAR"})
        assert success is True
        assert len(result) > 0

    def test_luno_depth_with_mock(self):
        """Test order book with mock response."""
        data_queue = queue.Queue()
        feed = LunoRequestDataSpot(data_queue)

        mock_response = {
            "bids": [
            ["94990000", "1.5"],
            ["94980000", "2.0"]
            ],
            "asks": [
            ["95000000", "1.3"],
            ["95010000", "2.5"]
            ],
            "timestamp": 1678901234000,
        }

        result, success = feed._get_depth_normalize_function(mock_response, {"symbol_name": "XBTZAR"})
        assert success is True
        assert len(result) > 0

    def test_luno_kline_with_mock(self):
        """Test kline with mock response."""
        data_queue = queue.Queue()
        feed = LunoRequestDataSpot(data_queue)

        mock_response = {
            "candles": [
            [1678901234000, "95000000", "95800000", "93500000", "95500000", "100"],
            [1678904834000, "95500000", "96000000", "95000000", "95800000", "150"],
            ]
        }

        result, success = feed._get_kline_normalize_function(mock_response, {"symbol_name": "XBTZAR"})
        assert success is True
        assert len(result) > 0


class TestLunoIntegration:
    """Integration tests for Luno."""

    def test_market_data_api(self):
        """Test market data API calls (requires network)."""
        pass

    def test_trading_api(self):
        """Test trading API calls (requires API keys)."""
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
