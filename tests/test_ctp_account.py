"""Tests for CTP account module."""

from __future__ import annotations

import pytest

from bt_api_py.containers.ctp.ctp_account import CtpAccountData


class TestCtpAccountData:
    """Tests for CtpAccountData class."""

    def test_init(self):
        """Test initialization."""
        account = CtpAccountData(
            {"AccountID": "123456"},
            symbol_name="IF2506",
            asset_type="FUTURE",
            has_been_json_encoded=True,
        )

        assert account.exchange_name == "CTP"
        assert account.symbol_name == "IF2506"
        assert account.asset_type == "FUTURE"

    def test_init_data(self):
        """Test init_data method."""
        account_info = {
            "BrokerID": "9999",
            "AccountID": "123456",
            "PreBalance": 1000000.0,
            "Balance": 1100000.0,
            "Available": 800000.0,
            "Commission": 500.0,
            "FrozenMargin": 200000.0,
            "CurrMargin": 100000.0,
            "CloseProfit": 50000.0,
            "PositionProfit": 50000.0,
            "Withdraw": 0.0,
            "Deposit": 0.0,
        }
        account = CtpAccountData(
            account_info, symbol_name="IF2506", asset_type="FUTURE", has_been_json_encoded=True
        )
        account.init_data()

        assert account.broker_id == "9999"
        assert account.account_id == "123456"
        assert account.pre_balance == 1000000.0
        assert account.balance == 1100000.0
        assert account.available == 800000.0
        assert account.commission == 500.0
        assert account.frozen_margin == 200000.0
        assert account.curr_margin == 100000.0
        assert account.close_profit == 50000.0
        assert account.position_profit == 50000.0
        assert account.risk_degree is not None

    def test_get_exchange_name(self):
        """Test get_exchange_name method."""
        account = CtpAccountData({}, has_been_json_encoded=True)

        assert account.exchange_name == "CTP"

    def test_account_data_inheritance(self):
        """Test that CtpAccountData inherits from AccountData."""
        account = CtpAccountData({}, has_been_json_encoded=True)

        assert hasattr(account, "account_info")
        assert hasattr(account, "event")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
