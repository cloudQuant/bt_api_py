"""Tests for IB account module."""

import pytest

from bt_api_py.containers.ib.ib_account import IbAccountData


class TestIbAccountData:
    """Tests for IbAccountData class."""

    def test_init(self):
        """Test initialization."""
        account = IbAccountData({"AccountID": "U123456"}, has_been_json_encoded=True)

        assert account.exchange_name == "IB"
        assert account.asset_type == "STK"
        assert account.account_id is None

    def test_init_data(self):
        """Test init_data method."""
        account_info = {
            "AccountID": "U123456",
            "NetLiquidation": "100000",
            "TotalCashValue": "50000",
            "BuyingPower": "100000",
            "GrossPositionValue": "50000",
            "MaintMarginReq": "10000",
            "AvailableFunds": "40000",
            "UnrealizedPnL": "1000",
            "RealizedPnL": "2000",
            "Currency": "USD",
        }
        account = IbAccountData(account_info, has_been_json_encoded=True)
        account.init_data()

        assert account.account_id == "U123456"
        assert account.net_liquidation == 100000.0
        assert account.total_cash_value == 50000.0
        assert account.buying_power == 100000.0
        assert account.currency == "USD"

    def test_get_exchange_name(self):
        """Test get_exchange_name method."""
        account = IbAccountData({}, has_been_json_encoded=True)

        assert account.get_exchange_name() == "IB"

    def test_get_asset_type(self):
        """Test get_asset_type method."""
        account = IbAccountData({}, asset_type="FUT", has_been_json_encoded=True)

        assert account.get_asset_type() == "FUT"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
