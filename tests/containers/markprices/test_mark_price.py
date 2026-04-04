"""Tests for MarkPriceData base container."""

import pytest

from bt_api_py.containers.markprices.mark_price import MarkPriceData


class TestMarkPriceData:
    """Tests for MarkPriceData base class."""

    def test_init(self):
        """Test initialization."""
        price = MarkPriceData({})

        assert price.event == "MarkPriceEvent"

    def test_init_data_raises_not_implemented(self):
        """Test init_data raises NotImplementedError."""
        price = MarkPriceData({})

        with pytest.raises(NotImplementedError):
            price.init_data()

    def test_get_event(self):
        """Test get_event method."""
        price = MarkPriceData({})

        assert price.get_event() == "MarkPriceEvent"

    def test_get_all_data(self):
        """Test get_all_data method - raises NotImplementedError via init_data."""
        price = MarkPriceData({})

        with pytest.raises(NotImplementedError):
            price.get_all_data()
