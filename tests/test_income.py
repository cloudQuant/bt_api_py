"""Tests for income module."""

from __future__ import annotations

import pytest

from bt_api_py.containers.incomes.income import IncomeData


class TestIncomeData:
    """Tests for IncomeData class."""

    def test_init(self):
        """Test initialization."""
        income = IncomeData({"income": "0.001"}, has_been_json_encoded=True)

        assert income.event == "IncomeEvent"
        assert income.income_info == {"income": "0.001"}
        assert income.has_been_json_encoded is True
        assert income.exchange_name is None
        assert income.symbol_name is None

    def test_init_without_json_encoded(self):
        """Test initialization without json encoded."""
        income = IncomeData('{"income": "0.001"}', has_been_json_encoded=False)

        assert income.event == "IncomeEvent"
        assert income.income_info == '{"income": "0.001"}'
        assert income.has_been_json_encoded is False
        assert income.income_data is None

    def test_get_event(self):
        """Test get_event method."""
        income = IncomeData({}, has_been_json_encoded=True)

        assert income.get_event() == "IncomeEvent"

    def test_get_event_type(self):
        """Test get_event_type method."""
        income = IncomeData({}, has_been_json_encoded=True)

        assert income.get_event_type() == "IncomeEvent"

    def test_init_data_not_implemented(self):
        """Test init_data raises NotImplementedError."""
        income = IncomeData({}, has_been_json_encoded=True)

        with pytest.raises(NotImplementedError):
            income.init_data()

    def test_get_exchange_name_not_implemented(self):
        """Test get_exchange_name raises NotImplementedError."""
        income = IncomeData({}, has_been_json_encoded=True)

        with pytest.raises(NotImplementedError):
            income.get_exchange_name()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
