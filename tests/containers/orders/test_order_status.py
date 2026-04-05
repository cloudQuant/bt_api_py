from __future__ import annotations

import pytest

from bt_api_py.containers.orders.order import OrderStatus  # Import your OrderStatus class here


class TestOrderStatus:
    """Tests for OrderStatus enum."""

    def test_str_representation(self):
        """Test __str__ method returns value."""
        assert str(OrderStatus.SUBMITTED) == "submitted"
        assert str(OrderStatus.ACCEPTED) == "new"
        assert str(OrderStatus.PARTIAL) == "partially_filled"
        assert str(OrderStatus.COMPLETED) == "filled"
        assert str(OrderStatus.CANCELED) == "canceled"
        assert str(OrderStatus.REJECTED) == "rejected"
        assert str(OrderStatus.MARGIN) == "margin"
        assert str(OrderStatus.EXPIRED) == "expired"
        assert str(OrderStatus.MMP_CANCELED) == "mmp_canceled"
        assert str(OrderStatus.EXPIRED_IN_MATCH) == "expired_in_match"
        assert str(OrderStatus.UNKNOWN) == "unknown"

    def test_get_static_dict(self):
        """Test get_static_dict class method."""
        static_dict = OrderStatus.get_static_dict()

        assert isinstance(static_dict, dict)
        assert static_dict["submitted"] == OrderStatus.SUBMITTED
        assert static_dict["NEW"] == OrderStatus.ACCEPTED
        assert static_dict["new"] == OrderStatus.ACCEPTED
        assert static_dict["live"] == OrderStatus.ACCEPTED
        assert static_dict["PARTIALLY_FILLED"] == OrderStatus.PARTIAL
        assert static_dict["filled"] == OrderStatus.COMPLETED
        assert static_dict["CANCELED"] == OrderStatus.CANCELED
        assert static_dict["EXPIRED"] == OrderStatus.EXPIRED

    def test_from_value_valid_status(self):
        """Test from_value with valid status values."""
        assert OrderStatus.from_value("submitted") == OrderStatus.SUBMITTED
        assert OrderStatus.from_value("NEW") == OrderStatus.ACCEPTED
        assert OrderStatus.from_value("margin") == OrderStatus.MARGIN
        assert OrderStatus.from_value("partially_filled") == OrderStatus.PARTIAL
        assert OrderStatus.from_value("filled") == OrderStatus.COMPLETED
        assert OrderStatus.from_value("canceled") == OrderStatus.CANCELED
        assert OrderStatus.from_value("REJECTED") == OrderStatus.REJECTED
        assert OrderStatus.from_value("EXPIRED") == OrderStatus.EXPIRED
        assert OrderStatus.from_value("mmp_canceled") == OrderStatus.MMP_CANCELED
        assert OrderStatus.from_value("EXPIRED_IN_MATCH") == OrderStatus.EXPIRED_IN_MATCH

    def test_from_value_none_returns_rejected(self):
        """Test from_value with None returns REJECTED."""
        assert OrderStatus.from_value(None) == OrderStatus.REJECTED

    def test_from_value_invalid_status(self):
        """Test from_value with invalid status raises ValueError."""
        with pytest.raises(ValueError):
            OrderStatus.from_value("invalid_status")

        with pytest.raises(ValueError):
            OrderStatus.from_value("non_existent_status")

    def test_from_value_live_mapped_to_new(self):
        """Test 'live' being correctly mapped to 'ACCEPTED'."""
        assert OrderStatus.from_value("live") == OrderStatus.ACCEPTED

    def test_aliases(self):
        """Test enum aliases."""
        assert OrderStatus.LIVE == OrderStatus.ACCEPTED
        assert OrderStatus.PARTIALLY_FILLED == OrderStatus.PARTIAL
        assert OrderStatus.FILLED == OrderStatus.COMPLETED


def test_from_value_valid_status():
    # Test valid status values
    assert OrderStatus.from_value("submitted") == OrderStatus.SUBMITTED
    assert OrderStatus.from_value("NEW") == OrderStatus.ACCEPTED
    assert OrderStatus.from_value("margin") == OrderStatus.MARGIN
    assert OrderStatus.from_value("partially_filled") == OrderStatus.PARTIAL
    assert OrderStatus.from_value("filled") == OrderStatus.COMPLETED
    assert OrderStatus.from_value("canceled") == OrderStatus.CANCELED
    assert OrderStatus.from_value("REJECTED") == OrderStatus.REJECTED
    assert OrderStatus.from_value("EXPIRED") == OrderStatus.EXPIRED
    assert OrderStatus.from_value("mmp_canceled") == OrderStatus.MMP_CANCELED


def test_from_value_invalid_status():
    # Test invalid status values, should raise ValueError
    with pytest.raises(ValueError):
        OrderStatus.from_value("invalid_status")

    with pytest.raises(ValueError):
        OrderStatus.from_value("non_existent_status")


def test_from_value_live_mapped_to_new():
    # Test 'live' being correctly mapped to 'NEW'
    assert OrderStatus.from_value("live") == OrderStatus.ACCEPTED
