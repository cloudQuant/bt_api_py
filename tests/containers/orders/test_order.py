"""Tests for OrderData base class and OrderStatus enum."""

from __future__ import annotations

import pytest

from bt_api_py.containers.orders.order import OrderData, OrderStatus


class TestOrderStatus:
    """Tests for OrderStatus enum."""

    def test_str_representation(self):
        """Test __str__ returns value."""
        assert str(OrderStatus.ACCEPTED) == "new"
        assert str(OrderStatus.COMPLETED) == "filled"
        assert str(OrderStatus.CANCELED) == "canceled"

    def test_get_static_dict(self):
        """Test get_static_dict returns mapping."""
        static_dict = OrderStatus.get_static_dict()

        assert static_dict["NEW"] == OrderStatus.ACCEPTED
        assert static_dict["new"] == OrderStatus.ACCEPTED
        assert static_dict["filled"] == OrderStatus.COMPLETED
        assert static_dict["canceled"] == OrderStatus.CANCELED

    def test_from_value_valid(self):
        """Test from_value with valid values."""
        assert OrderStatus.from_value("NEW") == OrderStatus.ACCEPTED
        assert OrderStatus.from_value("new") == OrderStatus.ACCEPTED
        assert OrderStatus.from_value("filled") == OrderStatus.COMPLETED
        assert OrderStatus.from_value("canceled") == OrderStatus.CANCELED

    def test_from_value_none_returns_rejected(self):
        """Test from_value with None returns REJECTED."""
        assert OrderStatus.from_value(None) == OrderStatus.REJECTED

    def test_from_value_invalid_raises_value_error(self):
        """Test from_value with invalid value raises ValueError."""
        with pytest.raises(ValueError, match="Invalid order status value"):
            OrderStatus.from_value("invalid_status")

    def test_aliases(self):
        """Test enum aliases."""
        assert OrderStatus.LIVE == OrderStatus.ACCEPTED
        assert OrderStatus.PARTIALLY_FILLED == OrderStatus.PARTIAL
        assert OrderStatus.FILLED == OrderStatus.COMPLETED


class TestOrderData:
    """Tests for OrderData base class."""

    def test_init(self):
        """Test initialization."""
        order = OrderData({})

        assert order.event == "OrderEvent"
        assert order.order_info == {}
        assert order.has_been_json_encoded is False
        assert order.exchange_name is None
        assert order.symbol_name is None
        assert order.asset_type is None

    def test_init_with_json_encoded(self):
        """Test initialization with has_been_json_encoded."""
        data = {"test": "data"}
        order = OrderData(data, has_been_json_encoded=True)

        assert order.has_been_json_encoded is True
        assert order.order_data == data

    def test_get_event(self):
        """Test get_event."""
        order = OrderData({})
        assert order.get_event() == "OrderEvent"

    def test_init_data_raises_not_implemented(self):
        """Test init_data raises NotImplementedError."""
        order = OrderData({})

        with pytest.raises(NotImplementedError):
            order.init_data()

    def test_get_all_data(self):
        """Test get_all_data."""
        order = OrderData({})
        # Set _initialized to prevent AutoInitMixin from calling init_data
        order._initialized = True
        order.exchange_name = "BINANCE"
        order.symbol_name = "BTCUSDT"
        order.order_id = "12345"
        order.order_size = 1.0
        order.order_price = 50000.0
        order.order_status = OrderStatus.ACCEPTED

        result = order.get_all_data()

        assert result["exchange_name"] == "BINANCE"
        assert result["symbol_name"] == "BTCUSDT"
        assert result["order_id"] == "12345"
        assert result["order_size"] == 1.0
        assert result["order_price"] == 50000.0
        assert result["order_status"] == "new"

    def test_get_all_data_order_status_string(self):
        """Test get_all_data handles string order_status."""
        order = OrderData({})
        order._initialized = True
        order.order_status = "custom_status"

        result = order.get_all_data()

        assert result["order_status"] == "custom_status"

    def test_get_exchange_name_raises_not_implemented(self):
        """Test get_exchange_name raises NotImplementedError."""
        order = OrderData({})

        with pytest.raises(NotImplementedError):
            order.get_exchange_name()

    def test_get_symbol_name_raises_not_implemented(self):
        """Test get_symbol_name raises NotImplementedError."""
        order = OrderData({})

        with pytest.raises(NotImplementedError):
            order.get_symbol_name()

    def test_get_order_id_raises_not_implemented(self):
        """Test get_order_id raises NotImplementedError."""
        order = OrderData({})

        with pytest.raises(NotImplementedError):
            order.get_order_id()

    def test_get_order_size_raises_not_implemented(self):
        """Test get_order_size raises NotImplementedError."""
        order = OrderData({})

        with pytest.raises(NotImplementedError):
            order.get_order_size()

    def test_get_order_price_raises_not_implemented(self):
        """Test get_order_price raises NotImplementedError."""
        order = OrderData({})

        with pytest.raises(NotImplementedError):
            order.get_order_price()

    def test_get_order_side_raises_not_implemented(self):
        """Test get_order_side raises NotImplementedError."""
        order = OrderData({})

        with pytest.raises(NotImplementedError):
            order.get_order_side()

    def test_get_order_status_raises_not_implemented(self):
        """Test get_order_status raises NotImplementedError."""
        order = OrderData({})

        with pytest.raises(NotImplementedError):
            order.get_order_status()

    def test_get_order_offset_returns_none(self):
        """Test get_order_offset returns None by default."""
        order = OrderData({})
        # Set _initialized to prevent AutoInitMixin from calling init_data
        order._initialized = True

        # get_order_offset has a default implementation that returns None
        assert order.get_order_offset() is None

    def test_get_order_exchange_id_returns_none(self):
        """Test get_order_exchange_id returns None by default."""
        order = OrderData({})
        # Set _initialized to prevent AutoInitMixin from calling init_data
        order._initialized = True

        # get_order_exchange_id has a default implementation that returns None
        assert order.get_order_exchange_id() is None

    def test_str_raises_not_implemented(self):
        """Test __str__ raises NotImplementedError."""
        order = OrderData({})

        with pytest.raises(NotImplementedError):
            str(order)

    def test_repr_raises_not_implemented(self):
        """Test __repr__ raises NotImplementedError."""
        order = OrderData({})

        with pytest.raises(NotImplementedError):
            repr(order)
