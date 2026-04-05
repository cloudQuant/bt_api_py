"""Tests for IncomeData base class."""

from __future__ import annotations

import pytest

from bt_api_py.containers.incomes.income import IncomeData


class TestIncomeData:
    """Tests for IncomeData base class."""

    def test_init(self):
        """Test initialization."""
        income = IncomeData({}, has_been_json_encoded=False)

        assert income.event == "IncomeEvent"
        assert income.income_info == {}
        assert income.has_been_json_encoded is False
        assert income.exchange_name is None
        assert income.symbol_name is None
        assert income.asset_type is None

    def test_init_with_json_encoded(self):
        """Test initialization with has_been_json_encoded."""
        data = {"test": "data"}
        income = IncomeData(data, has_been_json_encoded=True)

        assert income.has_been_json_encoded is True
        assert income.income_data == data

    def test_get_event(self):
        """Test get_event."""
        income = IncomeData({}, has_been_json_encoded=False)
        assert income.get_event() == "IncomeEvent"

    def test_get_event_type(self):
        """Test get_event_type."""
        income = IncomeData({}, has_been_json_encoded=False)
        assert income.get_event_type() == "IncomeEvent"

    def test_init_data_raises_not_implemented(self):
        """Test init_data raises NotImplementedError."""
        income = IncomeData({}, has_been_json_encoded=False)

        with pytest.raises(NotImplementedError):
            income.init_data()

    def test_get_all_data(self):
        """Test get_all_data."""
        income = IncomeData({}, has_been_json_encoded=False)
        # Set _initialized to prevent AutoInitMixin from calling init_data
        income._initialized = True
        income.exchange_name = "BINANCE"
        income.income_type = "COMMISSION"
        income.income_value = 100.0
        income.income_asset = "USDT"

        result = income.get_all_data()

        assert result["exchange_name"] == "BINANCE"
        assert result["income_type"] == "COMMISSION"
        assert result["income_value"] == 100.0
        assert result["income_asset"] == "USDT"

    def test_get_exchange_name_raises_not_implemented(self):
        """Test get_exchange_name raises NotImplementedError."""
        income = IncomeData({}, has_been_json_encoded=False)

        with pytest.raises(NotImplementedError):
            income.get_exchange_name()

    def test_get_server_time_raises_not_implemented(self):
        """Test get_server_time raises NotImplementedError."""
        income = IncomeData({}, has_been_json_encoded=False)

        with pytest.raises(NotImplementedError):
            income.get_server_time()

    def test_get_symbol_name_raises_not_implemented(self):
        """Test get_symbol_name raises NotImplementedError."""
        income = IncomeData({}, has_been_json_encoded=False)

        with pytest.raises(NotImplementedError):
            income.get_symbol_name()

    def test_get_income_type_raises_not_implemented(self):
        """Test get_income_type raises NotImplementedError."""
        income = IncomeData({}, has_been_json_encoded=False)

        with pytest.raises(NotImplementedError):
            income.get_income_type()

    def test_get_income_value_raises_not_implemented(self):
        """Test get_income_value raises NotImplementedError."""
        income = IncomeData({}, has_been_json_encoded=False)

        with pytest.raises(NotImplementedError):
            income.get_income_value()

    def test_get_income_asset_raises_not_implemented(self):
        """Test get_income_asset raises NotImplementedError."""
        income = IncomeData({}, has_been_json_encoded=False)

        with pytest.raises(NotImplementedError):
            income.get_income_asset()

    def test_str_raises_not_implemented(self):
        """Test __str__ raises NotImplementedError."""
        income = IncomeData({}, has_been_json_encoded=False)

        with pytest.raises(NotImplementedError):
            str(income)

    def test_repr_raises_not_implemented(self):
        """Test __repr__ raises NotImplementedError."""
        income = IncomeData({}, has_been_json_encoded=False)

        with pytest.raises(NotImplementedError):
            repr(income)
