"""Tests for position module."""

import pytest

from bt_api_py.containers.positions.position import PositionData


class TestPositionData:
    """Tests for PositionData class."""

    def test_init(self):
        """Test initialization."""
        position = PositionData({"symbol": "BTCUSDT", "qty": 1.0}, has_been_json_encoded=True)

        assert position.event == "PositionEvent"
        assert position.position_info == {"symbol": "BTCUSDT", "qty": 1.0}
        assert position.has_been_json_encoded is True
        assert position.exchange_name is None
        assert position.symbol_name is None

    def test_init_without_json_encoded(self):
        """Test initialization without json encoded."""
        position = PositionData('{"symbol": "BTCUSDT", "qty": 1.0}', has_been_json_encoded=False)

        assert position.event == "PositionEvent"
        assert position.position_info == '{"symbol": "BTCUSDT", "qty": 1.0}'
        assert position.has_been_json_encoded is False
        assert position.position_data is None

    def test_get_event(self):
        """Test get_event method."""
        position = PositionData({}, has_been_json_encoded=True)

        assert position.get_event() == "PositionEvent"

    def test_init_data_not_implemented(self):
        """Test init_data raises NotImplementedError."""
        position = PositionData({}, has_been_json_encoded=True)

        with pytest.raises(NotImplementedError):
            position.init_data()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
