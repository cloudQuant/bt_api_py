"""Tests for UpbitBalanceData container."""

import pytest

from bt_api_py.containers.balances.upbit_balance import UpbitBalanceData


class TestUpbitBalanceData:
    """Tests for UpbitBalanceData."""

    def test_init(self):
        """Test initialization."""
        balance = UpbitBalanceData({}, currency="BTC", asset_type="SPOT")

        assert balance.exchange_name == "UPBIT"
        assert balance.currency == "BTC"
        assert balance.asset_type == "SPOT"
        assert balance.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with balance info."""
        data = {
            "balance": "1.5",
            "locked": "0.5",
            "avg_buy_price": "50000.0",
            "currency_name": "Bitcoin",
            "unit_currency": "KRW",
        }
        balance = UpbitBalanceData(data, currency="BTC", asset_type="SPOT", has_been_json_encoded=True)
        balance.init_data()

        assert balance.balance == 1.5
        assert balance.locked == 0.5
        assert balance.available == 1.0

    def test_get_all_data(self):
        """Test get_all_data."""
        data = {"balance": "1.5", "locked": "0.5"}
        balance = UpbitBalanceData(data, currency="BTC", asset_type="SPOT", has_been_json_encoded=True)
        result = balance.get_all_data()

        assert result["exchange_name"] == "UPBIT"
        assert result["currency"] == "BTC"

    def test_str_representation(self):
        """Test __str__ method."""
        data = {"balance": "1.5", "locked": "0.5"}
        balance = UpbitBalanceData(data, currency="BTC", asset_type="SPOT", has_been_json_encoded=True)
        result = str(balance)

        assert "UpbitBalance" in result
        assert "BTC" in result
