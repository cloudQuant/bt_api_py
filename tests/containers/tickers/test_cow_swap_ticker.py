"""Tests for CowSwapRequestTickerData container."""

import pytest

from bt_api_py.containers.tickers.cow_swap_ticker import CowSwapRequestTickerData


class TestCowSwapRequestTickerData:
    """Tests for CowSwapRequestTickerData."""

    def test_init(self):
        """Test initialization."""
        ticker = CowSwapRequestTickerData({}, symbol_name="WETH-USDC", asset_type="SPOT")

        assert ticker.exchange_name == "COW_SWAP"
        assert ticker.asset_type == "SPOT"
        assert ticker.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with ticker info."""
        data = {"price": "50000.0"}
        ticker = CowSwapRequestTickerData(
            data, symbol_name="WETH-USDC", asset_type="SPOT", has_been_json_encoded=True
        )
        ticker.init_data()

        assert ticker.has_been_init_data is True

    def test_get_all_data(self):
        """Test get_all_data."""
        ticker = CowSwapRequestTickerData(
            {}, symbol_name="WETH-USDC", asset_type="SPOT", has_been_json_encoded=True
        )
        result = ticker.get_all_data()

        assert result == {}

    def test_str_representation(self):
        """Test __str__ method - base class raises NotImplementedError."""
        ticker = CowSwapRequestTickerData(
            {}, symbol_name="WETH-USDC", asset_type="SPOT", has_been_json_encoded=True
        )
        with pytest.raises(NotImplementedError):
            str(ticker)
