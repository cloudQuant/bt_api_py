"""Tests for PositionData base class."""

from __future__ import annotations

import pytest

from bt_api_py.containers.positions.position import PositionData


class TestPositionData:
    """Tests for PositionData base class."""

    def test_init(self):
        """Test initialization."""
        position = PositionData({})

        assert position.event == "PositionEvent"
        assert position.position_info == {}
        assert position.has_been_json_encoded is False
        assert position.exchange_name is None
        assert position.symbol_name is None
        assert position.asset_type is None

    def test_init_with_json_encoded(self):
        """Test initialization with has_been_json_encoded."""
        data = {"test": "data"}
        position = PositionData(data, has_been_json_encoded=True)

        assert position.has_been_json_encoded is True
        assert position.position_data == data

    def test_get_event(self):
        """Test get_event."""
        position = PositionData({})
        assert position.get_event() == "PositionEvent"

    def test_init_data_raises_not_implemented(self):
        """Test init_data raises NotImplementedError."""
        position = PositionData({})

        with pytest.raises(NotImplementedError):
            position.init_data()

    def test_get_all_data(self):
        """Test get_all_data."""
        position = PositionData({})
        # Set _initialized to prevent AutoInitMixin from calling init_data
        position._initialized = True
        position.exchange_name = "TEST"
        position.symbol_name = "BTCUSDT"
        position.position_volume = 1.0
        position.avg_price = 50000.0

        result = position.get_all_data()

        assert result["exchange_name"] == "TEST"
        assert result["symbol_name"] == "BTCUSDT"
        assert result["position_volume"] == 1.0
        assert result["avg_price"] == 50000.0

    def test_get_exchange_name_raises_not_implemented(self):
        """Test get_exchange_name raises NotImplementedError."""
        position = PositionData({})

        with pytest.raises(NotImplementedError):
            position.get_exchange_name()

    def test_get_asset_type_raises_not_implemented(self):
        """Test get_asset_type raises NotImplementedError."""
        position = PositionData({})

        with pytest.raises(NotImplementedError):
            position.get_asset_type()

    def test_get_server_time_raises_not_implemented(self):
        """Test get_server_time raises NotImplementedError."""
        position = PositionData({})

        with pytest.raises(NotImplementedError):
            position.get_server_time()

    def test_get_account_id_raises_not_implemented(self):
        """Test get_account_id raises NotImplementedError."""
        position = PositionData({})

        with pytest.raises(NotImplementedError):
            position.get_account_id()

    def test_get_position_id_raises_not_implemented(self):
        """Test get_position_id raises NotImplementedError."""
        position = PositionData({})

        with pytest.raises(NotImplementedError):
            position.get_position_id()

    def test_get_leverage_raises_not_implemented(self):
        """Test get_leverage raises NotImplementedError."""
        position = PositionData({})

        with pytest.raises(NotImplementedError):
            position.get_leverage()

    def test_get_position_volume_raises_not_implemented(self):
        """Test get_position_volume raises NotImplementedError."""
        position = PositionData({})

        with pytest.raises(NotImplementedError):
            position.get_position_volume()

    def test_get_position_side_raises_not_implemented(self):
        """Test get_position_side raises NotImplementedError."""
        position = PositionData({})

        with pytest.raises(NotImplementedError):
            position.get_position_side()

    def test_get_avg_price_raises_not_implemented(self):
        """Test get_avg_price raises NotImplementedError."""
        position = PositionData({})

        with pytest.raises(NotImplementedError):
            position.get_avg_price()

    def test_get_position_unrealized_pnl_raises_not_implemented(self):
        """Test get_position_unrealized_pnl raises NotImplementedError."""
        position = PositionData({})

        with pytest.raises(NotImplementedError):
            position.get_position_unrealized_pnl()

    def test_str_raises_not_implemented(self):
        """Test __str__ raises NotImplementedError."""
        position = PositionData({})

        with pytest.raises(NotImplementedError):
            str(position)

    def test_repr_raises_not_implemented(self):
        """Test __repr__ raises NotImplementedError."""
        position = PositionData({})

        with pytest.raises(NotImplementedError):
            repr(position)
