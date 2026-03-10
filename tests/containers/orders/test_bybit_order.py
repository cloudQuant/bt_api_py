"""Tests for Bybit order data containers."""

import pytest

from bt_api_py.containers.orders.bybit_order import BybitOrderData
from bt_api_py.containers.orders.order import OrderStatus


def test_bybit_order_data_spot():
    """Test Bybit spot order data parsing."""
    data = {
        "retCode": 0,
        "retMsg": "OK",
        "result": {
            "list": [
                {
                    "orderId": "1234567890",
                    "orderLinkId": "test_order_001",
                    "orderStatus": "New",
                    "side": "Buy",
                    "orderType": "Limit",
                    "price": "50000.00",
                    "qty": "0.001",
                    "cumExecQty": "0.0005",
                    "leavesQty": "0.0005",
                    "cumExecValue": "25.00",
                    "avgPrice": "50000.00",
                    "createdTime": "1609459200000",
                    "updatedTime": "1609459201000",
                    "timeInForce": "GTC",
                }
            ]
        },
    }

    order = BybitOrderData(data, "BTCUSDT", "SPOT", has_been_json_encoded=True)
    order.init_data()

    assert order.get_order_id() == "1234567890"
    assert order.get_client_order_id() == "test_order_001"
    assert order.get_order_status() == OrderStatus.ACCEPTED
    assert order.get_order_side() == "BUY"
    assert order.get_order_type() == "LIMIT"
    assert order.get_order_price() == 50000.0
    assert order.get_order_size() == 0.001
    assert order.get_executed_qty() == 0.0005
    assert order.get_order_avg_price() == 50000.0
    assert order.get_order_time_in_force() == "GTC"


def test_bybit_order_data_filled():
    """Test Bybit filled order data parsing."""
    data = {
        "retCode": 0,
        "retMsg": "OK",
        "result": {
            "list": [
                {
                    "orderId": "9876543210",
                    "orderLinkId": "filled_order",
                    "orderStatus": "Filled",
                    "side": "Sell",
                    "orderType": "Market",
                    "price": "0",
                    "qty": "0.01",
                    "cumExecQty": "0.01",
                    "leavesQty": "0",
                    "cumExecValue": "500.00",
                    "avgPrice": "50000.00",
                    "createdTime": "1609459200000",
                    "updatedTime": "1609459202000",
                    "timeInForce": "IOC",
                }
            ]
        },
    }

    order = BybitOrderData(data, "BTCUSDT", "SPOT", has_been_json_encoded=True)
    order.init_data()

    assert order.get_order_status() == OrderStatus.COMPLETED
    assert order.get_order_side() == "SELL"
    assert order.get_order_type() == "MARKET"
    assert order.get_executed_qty() == 0.01
    assert order.get_remaining_qty() == 0.0


def test_bybit_order_data_cancelled():
    """Test Bybit cancelled order data parsing."""
    data = {
        "retCode": 0,
        "retMsg": "OK",
        "result": {
            "list": [
                {
                    "orderId": "5555555555",
                    "orderLinkId": "cancelled_order",
                    "orderStatus": "Cancelled",
                    "side": "Buy",
                    "orderType": "Limit",
                    "price": "45000.00",
                    "qty": "0.02",
                    "cumExecQty": "0",
                    "leavesQty": "0.02",
                    "cumExecValue": "0",
                    "avgPrice": "0",
                    "createdTime": "1609459200000",
                    "updatedTime": "1609459203000",
                    "canceledTime": "1609459203500",
                    "timeInForce": "GTC",
                }
            ]
        },
    }

    order = BybitOrderData(data, "BTCUSDT", "SPOT", has_been_json_encoded=True)
    order.init_data()

    assert order.get_order_status() == OrderStatus.CANCELED
    assert order.get_executed_qty() == 0.0
    assert order.get_remaining_qty() == 0.02


def test_bybit_order_data_empty_list():
    """Test Bybit order data with empty order list."""
    data = {"retCode": 0, "retMsg": "OK", "result": {"list": []}}

    order = BybitOrderData(data, "BTCUSDT", "SPOT", has_been_json_encoded=True)
    order.init_data()

    # Should handle empty list gracefully
    assert order.get_order_id() is None
    assert order.get_order_status() is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
