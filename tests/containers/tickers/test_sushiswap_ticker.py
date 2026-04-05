"""Tests for SushiSwapRequestTickerData container."""

from __future__ import annotations

import pytest

from bt_api_py.containers.tickers.sushiswap_ticker import SushiSwapRequestTickerData


class TestSushiSwapRequestTickerData:
    """Tests for SushiSwapRequestTickerData."""

    def test_init(self):
        """Test initialization."""
        ticker = SushiSwapRequestTickerData({}, symbol_name="SUSHI-ETH", asset_type="DEX")

        assert ticker.exchange_name == "SUSHISWAP"
        assert ticker.symbol_name == "SUSHI-ETH"
        assert ticker.asset_type == "DEX"
        assert ticker.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with ticker info."""
        data = {"price": "50000.0", "bid": "49990.0", "ask": "50010.0"}
        ticker = SushiSwapRequestTickerData(
            data, symbol_name="SUSHI-ETH", asset_type="DEX", has_been_json_encoded=True
        )
        ticker.init_data()

        assert ticker.has_been_init_data is True

    def test_get_all_data(self):
        """Test get_all_data - base class raises NotImplementedError."""
        ticker = SushiSwapRequestTickerData(
            {}, symbol_name="SUSHI-ETH", asset_type="DEX", has_been_json_encoded=True
        )
        with pytest.raises(NotImplementedError):
            ticker.get_all_data()

    def test_str_representation(self):
        """Test __str__ method - base class raises NotImplementedError."""
        ticker = SushiSwapRequestTickerData(
            {}, symbol_name="SUSHI-ETH", asset_type="DEX", has_been_json_encoded=True
        )
        with pytest.raises(NotImplementedError):
            str(ticker)
