"""Tests for Binance balance module."""

import pytest

from bt_api_py.containers.balances.binance_balance import (
    BinanceSpotRequestBalanceData,
    BinanceWssBalanceData,
)


class TestBinanceWssBalanceData:
    """Tests for BinanceWssBalanceData class."""

    def test_init(self):
        """Test initialization."""
        balance = BinanceWssBalanceData(
            {"a": {"B": [{"a": "USDT", "f": "1000", "l": "500"}]}},
            asset_type="SPOT",
            has_been_json_encoded=True,
        )

        assert balance.asset_type == "SPOT"
        assert balance.accounts == []

    def test_init_balance_data(self):
        """Test init_balance_data method."""
        balance_info = {
            "a": {
                "B": [
                    {"a": "USDT", "f": "1000", "l": "500"},
                    {"a": "BTC", "f": "0.5", "l": "0.1"},
                ]
            }
        }
        balance = BinanceWssBalanceData(balance_info, asset_type="SPOT", has_been_json_encoded=True)
        balance.init_balance_data()

        assert len(balance.accounts) == 2
        assert balance.accounts[0]["asset"] == "USDT"
        assert balance.accounts[0]["free"] == 1000.0
        assert balance.accounts[0]["locked"] == 500.0

    def test_get_data(self):
        """Test get_data method."""
        balance = BinanceWssBalanceData(
            {"a": {"B": []}}, asset_type="SPOT", has_been_json_encoded=True
        )
        balance.init_balance_data()

        assert balance.get_data() == []


class TestBinanceSpotRequestBalanceData:
    """Tests for BinanceSpotRequestBalanceData class."""

    def test_init(self):
        """Test initialization."""
        balance = BinanceSpotRequestBalanceData(
            {"asset": "USDT", "free": "1000", "locked": "500"},
            symbol_name="USDT",
            asset_type="SPOT",
            has_been_json_encoded=True,
        )

        assert balance.exchange_name == "BINANCE"
        assert balance.account_type == "SPOT"
        assert balance.symbol_name == "USDT"

    def test_init_data(self):
        """Test init_data method."""
        balance_info = {
            "asset": "USDT",
            "free": "1000.0",
            "locked": "500.0",
        }
        balance = BinanceSpotRequestBalanceData(
            balance_info, symbol_name="USDT", asset_type="SPOT", has_been_json_encoded=True
        )
        balance.init_data()

        assert balance.balance_data == balance_info


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
