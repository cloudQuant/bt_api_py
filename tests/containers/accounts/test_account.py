"""Tests for AccountData base class."""

from __future__ import annotations

import pytest

from bt_api_py.containers.accounts.account import AccountData


class TestAccountData:
    """Tests for AccountData base class."""

    def test_init(self):
        """Test initialization."""
        account = AccountData({})

        assert account.event == "AccountEvent"
        assert account.account_info == {}
        assert account.has_been_json_encoded is False
        assert account.exchange_name is None
        assert account.balances == []
        assert account.positions == []

    def test_init_with_json_encoded(self):
        """Test initialization with has_been_json_encoded."""
        data = {"test": "data"}
        account = AccountData(data, has_been_json_encoded=True)

        assert account.has_been_json_encoded is True
        assert account.account_data == data

    def test_get_event(self):
        """Test get_event."""
        account = AccountData({})
        assert account.get_event() == "AccountEvent"

    def test_init_data_raises_not_implemented(self):
        """Test init_data raises NotImplementedError."""
        account = AccountData({})

        with pytest.raises(NotImplementedError):
            account.init_data()

    def test_get_all_data(self):
        """Test get_all_data by accessing all_data directly."""
        account = AccountData({})
        # Set _initialized to prevent AutoInitMixin from calling init_data
        account._initialized = True
        account.exchange_name = "TEST"
        account.account_id = "12345"
        account.total_wallet_balance = 10000.0
        # Set all_data to trigger caching
        account.all_data = {
            "exchange_name": "TEST",
            "account_id": "12345",
            "total_wallet_balance": 10000.0,
        }

        result = account.get_all_data()

        assert result["exchange_name"] == "TEST"
        assert result["account_id"] == "12345"
        assert result["total_wallet_balance"] == 10000.0

    def test_get_all_data_cached(self):
        """Test get_all_data is cached."""
        account = AccountData({})
        # Set _initialized to prevent AutoInitMixin from calling init_data
        account._initialized = True
        # Set all_data directly to avoid AutoInitMixin issues
        account.all_data = {"test": "data"}
        result1 = account.get_all_data()
        result2 = account.get_all_data()

        assert result1 is result2

    def test_get_exchange_name_raises_not_implemented(self):
        """Test get_exchange_name raises NotImplementedError."""
        account = AccountData({})

        with pytest.raises(NotImplementedError):
            account.get_exchange_name()

    def test_get_asset_type_raises_not_implemented(self):
        """Test get_asset_type raises NotImplementedError."""
        account = AccountData({})

        with pytest.raises(NotImplementedError):
            account.get_asset_type()

    def test_get_server_time_raises_not_implemented(self):
        """Test get_server_time raises NotImplementedError."""
        account = AccountData({})

        with pytest.raises(NotImplementedError):
            account.get_server_time()

    def test_get_account_id_raises_not_implemented(self):
        """Test get_account_id raises NotImplementedError."""
        account = AccountData({})

        with pytest.raises(NotImplementedError):
            account.get_account_id()

    def test_get_balances_raises_not_implemented(self):
        """Test get_balances raises NotImplementedError."""
        account = AccountData({})

        with pytest.raises(NotImplementedError):
            account.get_balances()

    def test_get_positions_raises_not_implemented(self):
        """Test get_positions raises NotImplementedError."""
        account = AccountData({})

        with pytest.raises(NotImplementedError):
            account.get_positions()

    def test_str_raises_not_implemented(self):
        """Test __str__ raises NotImplementedError."""
        account = AccountData({})

        with pytest.raises(NotImplementedError):
            str(account)

    def test_repr_raises_not_implemented(self):
        """Test __repr__ raises NotImplementedError."""
        account = AccountData({})

        with pytest.raises(NotImplementedError):
            repr(account)
