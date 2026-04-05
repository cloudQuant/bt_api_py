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

    def test_parse_dict_payload(self):
        data = {
            "open": "50000",
            "high": "51000",
            "low": "49000",
            "close": "50500",
            "volume": "1000",
            "timestamp": 1688671800,
        }
        bar = GeminiRequestBarData(data, symbol="BTCUSD", asset_type="SPOT")

        assert bar.get_exchange_name() == "GEMINI"
        assert bar.get_symbol_name() == "BTCUSD"
        assert bar.get_asset_type() == "SPOT"
        assert bar.get_server_time() == 1688671800
        assert bar.get_open_price() == 50000.0
        assert bar.get_close_price() == 50500.0
        assert bar.get_volume() == 1000.0

    def test_parse_list_payload_uses_latest_bar(self):
        data = [
            [1688671700, "49000", "50000", "48000", "49500", "800"],
            [1688671800, "50000", "51000", "49000", "50500", "1000"],
        ]
        bar = GeminiRequestBarData(data, symbol="BTCUSD", asset_type="SPOT")

        assert bar.get_server_time() == 1688671800
        assert bar.get_open_price() == 50000.0
        assert bar.get_high_price() == 51000.0
        assert bar.get_low_price() == 49000.0
        assert bar.get_close_price() == 50500.0

    def test_parse_single_bar_returns_none_for_short_input(self):
        bar = GeminiRequestBarData([], symbol="BTCUSD", asset_type="SPOT")
        assert bar._parse_single_bar([1, 2, 3]) is None

    def test_non_rest_uses_same_parse_path(self):
        data = {"open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10, "timestamp": 123456}
        bar = GeminiRequestBarData(data, symbol="ETHUSD", asset_type="SPOT", is_rest=False)

        assert bar.get_symbol_name() == "ETHUSD"
        assert bar.get_open_price() == 1.0
