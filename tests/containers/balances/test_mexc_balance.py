"""Tests for MexcBalanceData container."""

import pytest

from bt_api_py.containers.balances.mexc_balance import MexcBalanceData


class TestMexcBalanceData:
    """Tests for MexcBalanceData."""

    def test_init(self):
        """Test initialization."""
        balance = MexcBalanceData({}, symbol_name="BTC", asset_type="SPOT")

        assert balance.exchange_name == "MEXC"
        assert balance.symbol_name == "BTC"
        assert balance.asset_type == "SPOT"
        assert balance.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with balance info."""
        data = {
            "asset": "USDT",
            "free": "1000.0",
            "locked": "100.0",
        }
        balance = MexcBalanceData(data, symbol_name="USDT", asset_type="SPOT", has_been_json_encoded=True)
        balance.init_data()

        assert balance.asset == "USDT"
        assert balance.free == 1000.0
        assert balance.locked == 100.0

    def test_get_all_data(self):
        """Test get_all_data."""
        data = {"asset": "USDT", "free": "1000.0", "locked": "100.0"}
        balance = MexcBalanceData(data, symbol_name="USDT", asset_type="SPOT", has_been_json_encoded=True)
        result = balance.get_all_data()

        assert result["exchange_name"] == "MEXC"
        assert result["asset"] == "USDT"

    def test_str_representation(self):
        """Test __str__ method."""
        data = {"asset": "USDT", "free": "1000.0"}
        balance = MexcBalanceData(data, symbol_name="USDT", asset_type="SPOT", has_been_json_encoded=True)
        result = str(balance)

        assert "MEXC" in result
