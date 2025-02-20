import pytest
from bt_api_py.containers.orders.order import OrderStatus  # Import your OrderStatus class here

def test_from_value_valid_status():
    # Test valid status values
    assert OrderStatus.from_value('submitted') == OrderStatus.SUBMITTED
    assert OrderStatus.from_value('NEW') == OrderStatus.ACCEPTED
    assert OrderStatus.from_value('margin') == OrderStatus.MARGIN
    assert OrderStatus.from_value('partially_filled') == OrderStatus.PARTIAL
    assert OrderStatus.from_value('filled') == OrderStatus.COMPLETED
    assert OrderStatus.from_value('canceled') == OrderStatus.CANCELED
    assert OrderStatus.from_value('REJECTED') == OrderStatus.REJECTED
    assert OrderStatus.from_value('EXPIRED') == OrderStatus.EXPIRED
    assert OrderStatus.from_value('mmp_canceled') == OrderStatus.MMP_CANCELED

def test_from_value_invalid_status():
    # Test invalid status values, should raise ValueError
    with pytest.raises(ValueError):
        OrderStatus.from_value('invalid_status')

    with pytest.raises(ValueError):
        OrderStatus.from_value('non_existent_status')

def test_from_value_live_mapped_to_new():
    # Test 'live' being correctly mapped to 'NEW'
    assert OrderStatus.from_value('live') == OrderStatus.ACCEPTED