"""Tests for BalanceData base class."""

import pytest

from bt_api_py.containers.balances.balance import BalanceData


class TestBalanceData:
    """Tests for BalanceData base class."""

    def test_init(self):
        """Test initialization."""
        balance = BalanceData({})

        assert balance.event == "BalanceEvent"
        assert balance.balance_info == {}
        assert balance.has_been_json_encoded is False

    def test_init_with_json_encoded(self):
        """Test initialization with has_been_json_encoded."""
        data = {"test": "data"}
        balance = BalanceData(data, has_been_json_encoded=True)

        assert balance.has_been_json_encoded is True

    def test_get_event(self):
        """Test get_event."""
        balance = BalanceData({})
        assert balance.get_event() == "BalanceEvent"

    def test_init_data_raises_not_implemented(self):
        """Test init_data raises NotImplementedError."""
        balance = BalanceData({})

        with pytest.raises(NotImplementedError):
            balance.init_data()

    def test_get_all_data_raises_not_implemented(self):
        """Test get_all_data raises NotImplementedError."""
        balance = BalanceData({})

        with pytest.raises(NotImplementedError):
            balance.get_all_data()

    def test_get_exchange_name_raises_not_implemented(self):
        """Test get_exchange_name raises NotImplementedError."""
        balance = BalanceData({})

        with pytest.raises(NotImplementedError):
            balance.get_exchange_name()

    def test_get_asset_type_raises_not_implemented(self):
        """Test get_asset_type raises NotImplementedError."""
        balance = BalanceData({})

        with pytest.raises(NotImplementedError):
            balance.get_asset_type()

    def test_get_server_time_raises_not_implemented(self):
        """Test get_server_time raises NotImplementedError."""
        balance = BalanceData({})

        with pytest.raises(NotImplementedError):
            balance.get_server_time()

    def test_get_local_update_time_raises_not_implemented(self):
        """Test get_local_update_time raises NotImplementedError."""
        balance = BalanceData({})

        with pytest.raises(NotImplementedError):
            balance.get_local_update_time()

    def test_get_account_id_raises_not_implemented(self):
        """Test get_account_id raises NotImplementedError."""
        balance = BalanceData({})

        with pytest.raises(NotImplementedError):
            balance.get_account_id()

    def test_get_margin_raises_not_implemented(self):
        """Test get_margin raises NotImplementedError."""
        balance = BalanceData({})

        with pytest.raises(NotImplementedError):
            balance.get_margin()

    def test_get_used_margin_raises_not_implemented(self):
        """Test get_used_margin raises NotImplementedError."""
        balance = BalanceData({})

        with pytest.raises(NotImplementedError):
            balance.get_used_margin()

    def test_get_available_margin_raises_not_implemented(self):
        """Test get_available_margin raises NotImplementedError."""
        balance = BalanceData({})

        with pytest.raises(NotImplementedError):
            balance.get_available_margin()

    def test_get_unrealized_profit_raises_not_implemented(self):
        """Test get_unrealized_profit raises NotImplementedError."""
        balance = BalanceData({})

        with pytest.raises(NotImplementedError):
            balance.get_unrealized_profit()

    def test_str_raises_not_implemented(self):
        """Test __str__ raises NotImplementedError."""
        balance = BalanceData({})

        with pytest.raises(NotImplementedError):
            str(balance)

    def test_repr_raises_not_implemented(self):
        """Test __repr__ raises NotImplementedError."""
        balance = BalanceData({})

        with pytest.raises(NotImplementedError):
            repr(balance)
