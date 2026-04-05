"""Tests for greeks module."""

from __future__ import annotations

import pytest

from bt_api_py.containers.greeks.greeks import GreeksData


class TestGreeksData:
    """Tests for GreeksData class."""

    def test_init(self):
        """Test initialization."""
        greeks = GreeksData({"delta": 0.5}, has_been_json_encoded=True)

        assert greeks.event == "AccountGreeksEvent"
        assert greeks.greeks_info == {"delta": 0.5}
        assert greeks.has_been_json_encoded is True
        assert greeks.exchange_name is None
        assert greeks.symbol_name is None

    def test_init_without_json_encoded(self):
        """Test initialization without json encoded."""
        greeks = GreeksData('{"delta": 0.5}', has_been_json_encoded=False)

        assert greeks.event == "AccountGreeksEvent"
        assert greeks.greeks_info == '{"delta": 0.5}'
        assert greeks.has_been_json_encoded is False
        assert greeks.greeks_data is None

    def test_get_event(self):
        """Test get_event method."""
        greeks = GreeksData({}, has_been_json_encoded=True)

        assert greeks.get_event() == "AccountGreeksEvent"

    def test_init_data_not_implemented(self):
        """Test init_data raises NotImplementedError."""
        greeks = GreeksData({}, has_been_json_encoded=True)

        with pytest.raises(NotImplementedError):
            greeks.init_data()

    def test_get_exchange_name_not_implemented(self):
        """Test get_exchange_name raises NotImplementedError."""
        greeks = GreeksData({}, has_been_json_encoded=True)

        with pytest.raises(NotImplementedError):
            greeks.get_exchange_name()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
