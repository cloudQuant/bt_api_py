"""Tests for GeminiRequestBarData container."""

import pytest

from bt_api_py.containers.bars.gemini_bar import GeminiRequestBarData


class TestGeminiRequestBarData:
    """Tests for GeminiRequestBarData."""

    def test_init(self):
        """Test initialization."""
        bar = GeminiRequestBarData([], symbol="BTCUSD", asset_type="SPOT")

        assert bar.symbol == "BTCUSD"
        assert bar.asset_type == "SPOT"

    def test_parse_rest_data(self):
        """Test parsing REST data."""
        data = [{"open": 50000.0, "high": 51000.0, "low": 49000.0, "close": 50500.0, "volume": 1000.0}]
        bar = GeminiRequestBarData(data, symbol="BTCUSD", asset_type="SPOT", time_frame="1h")

        assert bar.time_frame == "1h"
