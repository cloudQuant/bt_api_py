"""Tests for order module."""

import pytest

from bt_api_py.containers.orders.order import OrderStatus, OrderData


class TestOrderStatus:
    """Tests for OrderStatus enum."""

    def test_str(self):
        """Test __str__ method."""
        assert str(OrderStatus.SUBMITTED) == "submitted"
        assert str(OrderStatus.ACCEPTED) == "new"
        assert str(OrderStatus.COMPLETED) == "filled"

    def test_get_static_dict(self):
        """Test get_static_dict method."""
        static_dict = OrderStatus.get_static_dict()

        assert static_dict["submitted"] == OrderStatus.SUBMITTED
        assert static_dict["new"] == OrderStatus.ACCEPTED
        assert static_dict["filled"] == OrderStatus.COMPLETED
        assert static_dict["canceled"] == OrderStatus.CANCELED

    def test_from_value_valid(self):
        """Test from_value with valid values."""
        assert OrderStatus.from_value("submitted") == OrderStatus.SUBMITTED
        assert OrderStatus.from_value("new") == OrderStatus.ACCEPTED
        assert OrderStatus.from_value("filled") == OrderStatus.COMPLETED
        assert OrderStatus.from_value("canceled") == OrderStatus.CANCELED

    def test_from_value_none(self):
        """Test from_value with None."""
        assert OrderStatus.from_value(None) == OrderStatus.REJECTED

    def test_from_value_invalid(self):
        """Test from_value with invalid value."""
        with pytest.raises(ValueError, match="Invalid order status value"):
            OrderStatus.from_value("invalid_status")

    def test_aliases(self):
        """Test enum aliases."""
        assert OrderStatus.LIVE == OrderStatus.ACCEPTED
        assert OrderStatus.PARTIALLY_FILLED == OrderStatus.PARTIAL
        assert OrderStatus.FILLED == OrderStatus.COMPLETED


class TestOrderData:
    """Tests for OrderData class."""

    def test_init(self):
        """Test initialization."""
        order = OrderData({"order_id": "12345"}, has_been_json_encoded=True)

        assert order.event == "OrderEvent"
        assert order.order_info == {"order_id": "12345"}
        assert order.has_been_json_encoded is True
        assert order.exchange_name is None
        assert order.symbol_name is None

    def test_init_without_json_encoded(self):
        """Test initialization without json encoded."""
        order = OrderData('{"order_id": "12345"}', has_been_json_encoded=False)

        assert order.event == "OrderEvent"
        assert order.order_info == '{"order_id": "12345"}'
        assert order.has_been_json_encoded is False
        assert order.order_data is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
