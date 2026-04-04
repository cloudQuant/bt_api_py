"""Tests for BarData base container."""

import pytest

from bt_api_py.containers.bars.bar import BarData


class TestBarData:
    """Tests for BarData base class."""

    def test_init(self):
        """Test initialization."""
        bar = BarData({})

        assert bar.event == "BarEvent"

    def test_init_data_raises_not_implemented(self):
        """Test init_data raises NotImplementedError."""
        bar = BarData({})

        with pytest.raises(NotImplementedError):
            bar.init_data()

    def test_get_event(self):
        """Test get_event method."""
        bar = BarData({})

        assert bar.get_event() == "BarEvent"

    def test_get_exchange_name_raises_not_implemented(self):
        """Test get_exchange_name raises NotImplementedError."""
        bar = BarData({})

        with pytest.raises(NotImplementedError):
            bar.get_exchange_name()
