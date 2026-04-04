"""Tests for orderbook module."""

import pytest

from bt_api_py.containers.orderbooks.orderbook import OrderBookData


class TestOrderBookData:
    """Tests for OrderBookData class."""

    def test_init(self):
        """Test initialization."""
        orderbook = OrderBookData({"bids": [], "asks": []}, has_been_json_encoded=True)

        assert orderbook.event == "OrderBookEvent"
        assert orderbook.order_book_info == {"bids": [], "asks": []}
        assert orderbook.has_been_json_encoded is True
        assert orderbook.exchange_name is None
        assert orderbook.symbol_name is None

    def test_init_without_json_encoded(self):
        """Test initialization without json encoded."""
        orderbook = OrderBookData('{"bids": [], "asks": []}', has_been_json_encoded=False)

        assert orderbook.event == "OrderBookEvent"
        assert orderbook.order_book_info == '{"bids": [], "asks": []}'
        assert orderbook.has_been_json_encoded is False
        assert orderbook.order_book_data is None

    def test_get_event(self):
        """Test get_event method."""
        orderbook = OrderBookData({}, has_been_json_encoded=True)

        assert orderbook.get_event() == "OrderBookEvent"

    def test_init_data_not_implemented(self):
        """Test init_data raises NotImplementedError."""
        orderbook = OrderBookData({}, has_been_json_encoded=True)

        with pytest.raises(NotImplementedError):
            orderbook.init_data()

    def test_default_values(self):
        """Test default values."""
        orderbook = OrderBookData({}, has_been_json_encoded=True)

        assert orderbook.bid_price_list is None
        assert orderbook.ask_price_list is None
        assert orderbook.bid_volume_list is None
        assert orderbook.ask_volume_list is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
