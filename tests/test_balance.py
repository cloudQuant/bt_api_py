"""Tests for balance module."""

from __future__ import annotations

import pytest

from bt_api_py.containers.balances.balance import BalanceData


class TestBalanceData:
    """Tests for BalanceData class."""

    def test_init(self):
        """Test initialization."""
        balance = BalanceData({"asset": "BTC", "free": 1.0}, has_been_json_encoded=True)

        assert balance.event == "BalanceEvent"
        assert balance.balance_info == {"asset": "BTC", "free": 1.0}
        assert balance.has_been_json_encoded is True

    def test_init_without_json_encoded(self):
        """Test initialization without json encoded."""
        balance = BalanceData('{"asset": "BTC", "free": 1.0}', has_been_json_encoded=False)

        assert balance.event == "BalanceEvent"
        assert balance.balance_info == '{"asset": "BTC", "free": 1.0}'
        assert balance.has_been_json_encoded is False

    def test_get_event(self):
        """Test get_event method."""
        balance = BalanceData({}, has_been_json_encoded=True)

        assert balance.get_event() == "BalanceEvent"

    def test_init_data_not_implemented(self):
        """Test init_data raises NotImplementedError."""
        balance = BalanceData({}, has_been_json_encoded=True)

        with pytest.raises(NotImplementedError):
            balance.init_data()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
