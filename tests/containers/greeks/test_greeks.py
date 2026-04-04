"""Tests for GreeksData base container."""

import pytest

from bt_api_py.containers.greeks.greeks import GreeksData


class TestGreeksData:
    """Tests for GreeksData base class."""

    def test_init(self):
        """Test initialization."""
        greeks = GreeksData({})

        assert greeks.event == "AccountGreeksEvent"

    def test_init_data_raises_not_implemented(self):
        """Test init_data raises NotImplementedError."""
        greeks = GreeksData({})

        with pytest.raises(NotImplementedError):
            greeks.init_data()

    def test_get_event(self):
        """Test get_event method."""
        greeks = GreeksData({})

        assert greeks.get_event() == "AccountGreeksEvent"

    def test_get_all_data(self):
        """Test get_all_data method."""
        greeks = GreeksData({})
        result = greeks.get_all_data()

        assert result["exchange_name"] is None
        assert result["symbol_name"] is None
