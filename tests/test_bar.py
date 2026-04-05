"""Tests for bar module."""

from __future__ import annotations

import pytest

from bt_api_py.containers.bars.bar import BarData


class TestBarData:
    """Tests for BarData class."""

    def test_init(self):
        """Test initialization."""
        bar = BarData({"open": 50000.0}, has_been_json_encoded=True)

        assert bar.event == "BarEvent"
        assert bar.bar_info == {"open": 50000.0}
        assert bar.has_been_json_encoded is True

    def test_init_without_json_encoded(self):
        """Test initialization without json encoded."""
        bar = BarData('{"open": 50000.0}', has_been_json_encoded=False)

        assert bar.event == "BarEvent"
        assert bar.bar_info == '{"open": 50000.0}'
        assert bar.has_been_json_encoded is False

    def test_get_event(self):
        """Test get_event method."""
        bar = BarData({}, has_been_json_encoded=True)

        assert bar.get_event() == "BarEvent"

    def test_init_data_not_implemented(self):
        """Test init_data raises NotImplementedError."""
        bar = BarData({}, has_been_json_encoded=True)

        with pytest.raises(NotImplementedError):
            bar.init_data()

    def test_get_exchange_name_not_implemented(self):
        """Test get_exchange_name raises NotImplementedError."""
        bar = BarData({}, has_been_json_encoded=True)

        with pytest.raises(NotImplementedError):
            bar.get_exchange_name()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
