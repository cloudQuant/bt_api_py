"""Tests for IB order module."""

import pytest

from bt_api_py.containers.ib.ib_order import IbOrderData, IB_ORDER_STATUS_MAP


class TestIbOrderData:
    """Tests for IbOrderData class."""

    def test_init(self):
        """Test initialization."""
        order = IbOrderData(
            {"orderId": 12345},
            symbol_name="AAPL",
            asset_type="STK",
            has_been_json_encoded=True
        )

        assert order.exchange_name == "IB"
        assert order.symbol_name == "AAPL"
        assert order.asset_type == "STK"

    def test_ib_order_status_map(self):
        """Test IB order status map."""
        assert IB_ORDER_STATUS_MAP["PendingSubmit"] is not None
        assert IB_ORDER_STATUS_MAP["Cancelled"] is not None
        assert IB_ORDER_STATUS_MAP["Filled"] is not None

    def test_init_data(self):
        """Test init_data method."""
        order_info = {
            "orderId": 12345,
            "permId": 99999,
            "clientId": 1,
            "action": "BUY",
            "totalQuantity": 100,
            "orderType": "LMT",
            "lmtPrice": 150.0,
            "tif": "DAY",
            "status": "Submitted",
            "filled": 50,
            "remaining": 50,
            "avgFillPrice": 150.5,
        }
        order = IbOrderData(
            order_info,
            symbol_name="AAPL",
            asset_type="STK",
            has_been_json_encoded=True
        )
        order.init_data()

        assert order.order_id_val == 12345
        assert order.perm_id == 99999
        assert order.action == "BUY"
        assert order.total_quantity == 100
        assert order.order_type_val == "LMT"
        assert order.lmt_price == 150.0
        assert order.tif == "DAY"
        assert order.filled == 50

    def test_order_data_inheritance(self):
        """Test that IbOrderData inherits from OrderData."""
        order = IbOrderData({}, has_been_json_encoded=True)

        assert hasattr(order, "order_info")
        assert hasattr(order, "event")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
