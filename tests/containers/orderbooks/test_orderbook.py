"""Tests for OrderBookData base container."""

from __future__ import annotations

import pytest

from bt_api_py.containers.orderbooks.orderbook import OrderBookData


class TestOrderBookData:
    """Tests for OrderBookData base class."""

    def test_init(self):
        """Test initialization."""
        orderbook = OrderBookData({})

        assert orderbook.event == "OrderBookEvent"
        assert orderbook.exchange_name is None
        assert orderbook.symbol_name is None
        assert orderbook.asset_type is None

    def test_init_data_raises_not_implemented(self):
        """Test init_data raises NotImplementedError."""
        orderbook = OrderBookData({})

        with pytest.raises(NotImplementedError):
            orderbook.init_data()

    def test_get_event(self):
        """Test get_event method."""
        orderbook = OrderBookData({})

        assert orderbook.get_event() == "OrderBookEvent"

    def test_get_all_data(self):
        """Test get_all_data method - base class raises NotImplementedError via init_data."""
        orderbook = OrderBookData({})

        with pytest.raises(NotImplementedError):
            orderbook.get_all_data()
