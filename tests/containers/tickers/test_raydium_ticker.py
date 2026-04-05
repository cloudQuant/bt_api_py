"""Tests for RaydiumRequestTickerData container."""

from __future__ import annotations

import pytest

from bt_api_py.containers.tickers.raydium_ticker import RaydiumRequestTickerData


class TestRaydiumRequestTickerData:
    """Tests for RaydiumRequestTickerData."""

    def test_init(self):
        """Test initialization."""
        ticker = RaydiumRequestTickerData({}, symbol="SOL-USDC", asset_type="DEX")

        assert ticker.symbol_name == "SOL-USDC"
        assert ticker.asset_type == "DEX"

    def test_get_all_data(self):
        """Test get_all_data - base class raises NotImplementedError."""
        ticker = RaydiumRequestTickerData({}, symbol="SOL-USDC", asset_type="DEX")
        with pytest.raises(NotImplementedError):
            ticker.get_all_data()

    def test_str_representation(self):
        """Test __str__ method."""
        ticker = RaydiumRequestTickerData({}, symbol="SOL-USDC", asset_type="DEX")
        result = str(ticker)

        assert "Raydium" in result or "raydium" in result
