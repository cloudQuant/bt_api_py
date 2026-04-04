"""Tests for Coinbase balance containers."""

import pytest

from bt_api_py.containers.balances.coinbase_balance import (
    CoinbaseBalanceData,
    CoinbaseWssBalanceData,
    CoinbaseRequestBalanceData,
)


class TestCoinbaseBalanceData:
    """Tests for CoinbaseBalanceData."""

    def test_init(self):
        """Test initialization."""
        balance = CoinbaseBalanceData({}, asset_type="SPOT")

        assert balance.exchange_name == "COINBASE"
        assert balance.asset_type == "SPOT"
        assert balance.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with balance info."""
        data = {
            "currency": "BTC",
            "available_balance": {"value": "1.5"},
            "hold": {"value": "0.5"},
            "total": {"value": "2.0"},
        }
        balance = CoinbaseBalanceData(data, asset_type="SPOT", has_been_json_encoded=True)
        balance.init_data()

        assert balance.currency == "BTC"
        assert balance.available == 1.5
        assert balance.hold == 0.5
        assert balance.total == 2.0

    def test_init_data_simple_format(self):
        """Test init_data with simple format."""
        data = {
            "currency": "BTC",
            "available": "1.5",
            "hold": "0.5",
            "total": "2.0",
        }
        balance = CoinbaseBalanceData(data, asset_type="SPOT", has_been_json_encoded=True)
        balance.init_data()

        assert balance.currency == "BTC"
        assert balance.available == 1.5

    def test_get_exchange_name(self):
        """Test get_exchange_name."""
        balance = CoinbaseBalanceData({}, asset_type="SPOT")
        assert balance.get_exchange_name() == "COINBASE"

    def test_get_asset_type(self):
        """Test get_asset_type."""
        balance = CoinbaseBalanceData({}, asset_type="SPOT")
        assert balance.get_asset_type() == "SPOT"

    def test_get_currency(self):
        """Test get_currency."""
        data = {"currency": "BTC"}
        balance = CoinbaseBalanceData(data, asset_type="SPOT", has_been_json_encoded=True)

        assert balance.get_currency() == "BTC"

    def test_get_available(self):
        """Test get_available."""
        data = {"currency": "BTC", "available": "1.5"}
        balance = CoinbaseBalanceData(data, asset_type="SPOT", has_been_json_encoded=True)

        assert balance.get_available() == 1.5

    def test_get_all_data(self):
        """Test get_all_data."""
        data = {"currency": "BTC", "available": "1.5"}
        balance = CoinbaseBalanceData(data, asset_type="SPOT", has_been_json_encoded=True)
        result = balance.get_all_data()

        assert result["exchange_name"] == "COINBASE"
        assert result["currency"] == "BTC"

    def test_str_representation(self):
        """Test __str__ method."""
        data = {"currency": "BTC"}
        balance = CoinbaseBalanceData(data, asset_type="SPOT", has_been_json_encoded=True)
        result = str(balance)

        assert "COINBASE" in result
        assert "BTC" in result


class TestCoinbaseWssBalanceData:
    """Tests for CoinbaseWssBalanceData."""

    def test_init_data(self):
        """Test init_data with WebSocket format."""
        data = {
            "currency": "BTC",
            "available": "1.5",
            "hold": "0.5",
            "total": "2.0",
        }
        balance = CoinbaseWssBalanceData(data, asset_type="SPOT", has_been_json_encoded=True)
        balance.init_data()

        assert balance.currency == "BTC"
        assert balance.available == 1.5


class TestCoinbaseRequestBalanceData:
    """Tests for CoinbaseRequestBalanceData."""

    def test_init_data(self):
        """Test init_data with REST API format."""
        data = {
            "currency": "BTC",
            "available": "1.5",
            "hold": "0.5",
            "total": "2.0",
        }
        balance = CoinbaseRequestBalanceData(data, asset_type="SPOT", has_been_json_encoded=True)
        balance.init_data()

        assert balance.currency == "BTC"
        assert balance.available == 1.5
