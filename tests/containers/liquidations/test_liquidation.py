"""Tests for LiquidationData base class."""

import pytest

from bt_api_py.containers.liquidations.liquidation import LiquidationData


class TestLiquidationData:
    """Tests for LiquidationData base class."""

    def test_init(self):
        """Test initialization."""
        liquidation = LiquidationData({})

        assert liquidation.event == "LiquidationWarningEvent"
        assert liquidation.liquidation_info == {}
        assert liquidation.has_been_json_encoded is False
        assert liquidation.exchange_name is None
        assert liquidation.symbol_name is None
        assert liquidation.asset_type is None

    def test_init_with_json_encoded(self):
        """Test initialization with has_been_json_encoded."""
        data = {"test": "data"}
        liquidation = LiquidationData(data, has_been_json_encoded=True)

        assert liquidation.has_been_json_encoded is True
        assert liquidation.liquidation_data == data

    def test_get_event(self):
        """Test get_event."""
        liquidation = LiquidationData({})
        assert liquidation.get_event() == "LiquidationWarningEvent"

    def test_init_data_raises_not_implemented(self):
        """Test init_data raises NotImplementedError."""
        liquidation = LiquidationData({})

        with pytest.raises(NotImplementedError):
            liquidation.init_data()

    def test_get_all_data(self):
        """Test get_all_data."""
        liquidation = LiquidationData({})
        liquidation.exchange_name = "OKX"
        liquidation.symbol_name = "BTC-USDT"
        liquidation.asset_type = "SWAP"
        liquidation.server_time = 1712217600.0

        result = liquidation.get_all_data()

        assert result["exchange_name"] == "OKX"
        assert result["symbol_name"] == "BTC-USDT"
        assert result["asset_type"] == "SWAP"
        assert result["server_time"] == 1712217600.0

    def test_get_exchange_name_raises_not_implemented(self):
        """Test get_exchange_name raises NotImplementedError."""
        liquidation = LiquidationData({})

        with pytest.raises(NotImplementedError):
            liquidation.get_exchange_name()

    def test_get_asset_type_raises_not_implemented(self):
        """Test get_asset_type raises NotImplementedError."""
        liquidation = LiquidationData({})

        with pytest.raises(NotImplementedError):
            liquidation.get_asset_type()

    def test_get_symbol_name_raises_not_implemented(self):
        """Test get_symbol_name raises NotImplementedError."""
        liquidation = LiquidationData({})

        with pytest.raises(NotImplementedError):
            liquidation.get_symbol_name()

    def test_get_server_time_raises_not_implemented(self):
        """Test get_server_time raises NotImplementedError."""
        liquidation = LiquidationData({})

        with pytest.raises(NotImplementedError):
            liquidation.get_server_time()

    def test_get_local_update_time_raises_not_implemented(self):
        """Test get_local_update_time raises NotImplementedError."""
        liquidation = LiquidationData({})

        with pytest.raises(NotImplementedError):
            liquidation.get_local_update_time()

    def test_str_raises_not_implemented(self):
        """Test __str__ raises NotImplementedError."""
        liquidation = LiquidationData({})

        with pytest.raises(NotImplementedError):
            str(liquidation)

    def test_repr_calls_str(self):
        """Test __repr__ calls __str__."""
        liquidation = LiquidationData({})

        with pytest.raises(NotImplementedError):
            repr(liquidation)
