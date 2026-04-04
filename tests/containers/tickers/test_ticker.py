"""Tests for TickerData base container."""

import pytest

from bt_api_py.containers.tickers.ticker import TickerData


class TestTickerData:
    """Tests for TickerData base class."""

    def test_init(self):
        """Test initialization."""
        ticker = TickerData({})

        assert ticker.event == "TickerEvent"

    def test_init_data_raises_not_implemented(self):
        """Test init_data raises NotImplementedError."""
        ticker = TickerData({})

        with pytest.raises(NotImplementedError):
            ticker.init_data()

    def test_get_all_data_raises_not_implemented(self):
        """Test get_all_data raises NotImplementedError."""
        ticker = TickerData({})

        with pytest.raises(NotImplementedError):
            ticker.get_all_data()

    def test_get_event(self):
        """Test get_event method."""
        ticker = TickerData({})

        assert ticker.get_event() == "TickerEvent"

    def test_get_exchange_name_raises_not_implemented(self):
        """Test get_exchange_name raises NotImplementedError."""
        ticker = TickerData({})

        with pytest.raises(NotImplementedError):
            ticker.get_exchange_name()
