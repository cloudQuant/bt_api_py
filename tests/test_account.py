"""Tests for account module."""

import pytest

from bt_api_py.containers.accounts.account import AccountData


class TestAccountData:
    """Tests for AccountData class."""

    def test_init(self):
        """Test initialization."""
        account = AccountData({"account_id": "12345"}, has_been_json_encoded=True)

        assert account.event == "AccountEvent"
        assert account.account_info == {"account_id": "12345"}
        assert account.has_been_json_encoded is True
        assert account.exchange_name is None
        assert account.symbol_name is None

    def test_init_without_json_encoded(self):
        """Test initialization without json encoded."""
        account = AccountData('{"account_id": "12345"}', has_been_json_encoded=False)

        assert account.event == "AccountEvent"
        assert account.account_info == '{"account_id": "12345"}'
        assert account.has_been_json_encoded is False
        assert account.account_data is None

    def test_get_event(self):
        """Test get_event method."""
        account = AccountData({}, has_been_json_encoded=True)

        assert account.get_event() == "AccountEvent"

    def test_default_values(self):
        """Test default values."""
        account = AccountData({}, has_been_json_encoded=True)

        assert account.balances == []
        assert account.positions == []
        assert account.can_deposit is None
        assert account.can_trade is None
        assert account.can_withdraw is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
