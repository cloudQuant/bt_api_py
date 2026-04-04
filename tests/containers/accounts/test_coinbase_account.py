"""Tests for CoinbaseAccountData container."""

import pytest

from bt_api_py.containers.accounts.coinbase_account import CoinbaseAccountData


class TestCoinbaseAccountData:
    """Tests for CoinbaseAccountData."""

    def test_init(self):
        """Test initialization."""
        account = CoinbaseAccountData({}, symbol_name="BTC-USD", asset_type="SPOT")

        assert account.exchange_name == "COINBASE"
        assert account.symbol_name == "BTC-USD"
        assert account.asset_type == "SPOT"
        assert account.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with account info."""
        data = {"id": "test-account", "currency": "BTC", "balance": "1.5", "available": "1.0", "hold": "0.5"}
        account = CoinbaseAccountData(data, symbol_name="BTC-USD", asset_type="SPOT", has_been_json_encoded=True)
        account.init_data()

        assert account.has_been_init_data is True

    def test_get_all_data(self):
        """Test get_all_data method."""
        account = CoinbaseAccountData({}, symbol_name="BTC-USD", asset_type="SPOT", has_been_json_encoded=True)
        result = account.get_all_data()

        assert result["exchange_name"] == "COINBASE"
        assert result["symbol_name"] == "BTC-USD"
