"""Tests for GeminiRequestTickerData container."""

from __future__ import annotations

from bt_api_py.containers.tickers.gemini_ticker import GeminiRequestTickerData


class TestGeminiRequestTickerData:
    """Tests for GeminiRequestTickerData."""

    def test_init(self):
        """Test initialization."""
        ticker = GeminiRequestTickerData({})

        assert ticker.exchange_name == "GEMINI"

    def test_parse_data(self):
        """Test parsing data."""
        data = {
            "last": "50000.0",
            "bid": "49990.0",
            "ask": "50010.0",
            "high": "51000.0",
            "low": "49000.0",
            "volume": "1000.0",
        }
        ticker = GeminiRequestTickerData(data, symbol="BTCUSD", asset_type="SPOT")

        assert ticker.last_price == 50000.0

    def test_to_dict(self):
        """Test to_dict."""
        ticker = GeminiRequestTickerData({})
        result = ticker.to_dict()

        assert result is not None

    def test_str_representation(self):
        """Test __str__ method."""
        ticker = GeminiRequestTickerData({})
        result = str(ticker)

        assert result is not None
