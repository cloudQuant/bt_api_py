"""Tests for liquidation module."""

import pytest

from bt_api_py.containers.liquidations.liquidation import LiquidationData


class TestLiquidationData:
    """Tests for LiquidationData class."""

    def test_init(self):
        """Test initialization."""
        liquidation = LiquidationData({"price": 50000.0}, has_been_json_encoded=True)

        assert liquidation.event == "LiquidationWarningEvent"
        assert liquidation.liquidation_info == {"price": 50000.0}
        assert liquidation.has_been_json_encoded is True
        assert liquidation.exchange_name is None
        assert liquidation.symbol_name is None

    def test_init_without_json_encoded(self):
        """Test initialization without json encoded."""
        liquidation = LiquidationData('{"price": 50000.0}', has_been_json_encoded=False)

        assert liquidation.event == "LiquidationWarningEvent"
        assert liquidation.liquidation_info == '{"price": 50000.0}'
        assert liquidation.has_been_json_encoded is False
        assert liquidation.liquidation_data is None

    def test_get_event(self):
        """Test get_event method."""
        liquidation = LiquidationData({}, has_been_json_encoded=True)

        assert liquidation.get_event() == "LiquidationWarningEvent"

    def test_init_data_not_implemented(self):
        """Test init_data raises NotImplementedError."""
        liquidation = LiquidationData({}, has_been_json_encoded=True)

        with pytest.raises(NotImplementedError):
            liquidation.init_data()

    def test_get_exchange_name_not_implemented(self):
        """Test get_exchange_name raises NotImplementedError."""
        liquidation = LiquidationData({}, has_been_json_encoded=True)

        with pytest.raises(NotImplementedError):
            liquidation.get_exchange_name()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
