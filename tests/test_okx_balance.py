"""Tests for OKX balance module."""

import pytest

from bt_api_py.containers.balances.okx_balance import OkxBalanceData


class TestOkxBalanceData:
    """Tests for OkxBalanceData class."""

    def test_init(self):
        """Test initialization."""
        balance = OkxBalanceData(
            {"ccy": "USDT"}, symbol_name="USDT", asset_type="SPOT", has_been_json_encoded=True
        )

        assert balance.exchange_name == "OKX"
        assert balance.symbol_name == "USDT"
        assert balance.asset_type == "SPOT"

    def test_init_data(self):
        """Test init_data method."""
        balance_info = {
            "ccy": "USDT",
            "uTime": "1705315800000",
            "eq": "10000.0",
            "frozenBal": "500.0",
            "availBal": "9500.0",
            "upl": "100.0",
            "interest": "10.0",
        }
        balance = OkxBalanceData(
            balance_info, symbol_name="USDT", asset_type="SPOT", has_been_json_encoded=True
        )
        balance.init_data()

        assert balance.symbol_name == "USDT"
        assert balance.server_time == 1705315800000.0
        assert balance.margin == 10000.0
        assert balance.used_margin == 500.0
        assert balance.available_margin == 9500.0
        assert balance.unrealized_profit == 100.0
        assert balance.interest == 10.0

    def test_get_all_data(self):
        """Test get_all_data method."""
        balance_info = {
            "ccy": "USDT",
            "uTime": "1705315800000",
            "eq": "10000.0",
            "frozenBal": "500.0",
            "availBal": "9500.0",
            "upl": "100.0",
        }
        balance = OkxBalanceData(
            balance_info, symbol_name="USDT", asset_type="SPOT", has_been_json_encoded=True
        )
        balance.init_data()
        result = balance.get_all_data()

        assert result["exchange_name"] == "OKX"
        assert result["symbol_name"] == "USDT"
        assert result["margin"] == 10000.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
