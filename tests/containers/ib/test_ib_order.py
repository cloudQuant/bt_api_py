"""Tests for IB order container."""

from bt_api_py.containers.ib.ib_order import IB_ORDER_STATUS_MAP, IbOrderData
from bt_api_py.containers.orders.order import OrderStatus


class TestIbOrderData:
    """Tests for IbOrderData."""

    def test_init(self):
        """Test initialization."""
        order = IbOrderData({}, symbol_name="AAPL", asset_type="STK")

        assert order.exchange_name == "IB"
        assert order.symbol_name == "AAPL"
        assert order.asset_type == "STK"

    def test_init_data(self):
        """Test init_data with order info."""
        data = {
            "orderId": 123,
            "permId": 456,
            "clientId": 1,
            "action": "BUY",
            "totalQuantity": 100,
            "orderType": "LMT",
            "lmtPrice": 150.0,
            "auxPrice": 0.0,
            "tif": "DAY",
            "ocaGroup": "group1",
            "status": "Submitted",
            "filled": 50,
            "remaining": 50,
            "avgFillPrice": 150.0,
            "lastFillTime": "20250404 14:30:00",
        }
        order = IbOrderData(data, symbol_name="AAPL", asset_type="STK")
        order.init_data()

        assert order.order_id_val == 123
        assert order.perm_id == 456
        assert order.client_id == 1
        assert order.action == "BUY"
        assert order.total_quantity == 100
        assert order.order_type_val == "LMT"
        assert order.lmt_price == 150.0
        assert order.aux_price == 0.0
        assert order.tif == "DAY"
        assert order.oca_group == "group1"
        assert order.status_val == OrderStatus.ACCEPTED
        assert order.filled == 50
        assert order.remaining == 50
        assert order.avg_fill_price == 150.0
        assert order.last_fill_time == "20250404 14:30:00"

    def test_init_data_idempotent(self):
        """Test init_data is idempotent."""
        data = {
            "orderId": 123,
            "action": "BUY",
        }
        order = IbOrderData(data)
        order.init_data()
        first_action = order.action

        order.init_data()
        assert order.action == first_action

    def test_status_filled(self):
        """Test Filled status mapping."""
        data = {"status": "Filled"}
        order = IbOrderData(data)
        order.init_data()

        assert order.status_val == OrderStatus.COMPLETED

    def test_status_cancelled(self):
        """Test Cancelled status mapping."""
        data = {"status": "Cancelled"}
        order = IbOrderData(data)
        order.init_data()

        assert order.status_val == OrderStatus.CANCELED

    def test_status_inactive(self):
        """Test Inactive status mapping."""
        data = {"status": "Inactive"}
        order = IbOrderData(data)
        order.init_data()

        assert order.status_val == OrderStatus.REJECTED

    def test_get_exchange_name(self):
        """Test get_exchange_name."""
        order = IbOrderData({})
        assert order.get_exchange_name() == "IB"

    def test_get_asset_type(self):
        """Test get_asset_type."""
        order = IbOrderData({}, asset_type="STK")
        assert order.get_asset_type() == "STK"

    def test_get_symbol_name(self):
        """Test get_symbol_name."""
        order = IbOrderData({}, symbol_name="AAPL")
        assert order.get_symbol_name() == "AAPL"

    def test_get_server_time(self):
        """Test get_server_time."""
        data = {"lastFillTime": "20250404 14:30:00"}
        order = IbOrderData(data)
        order.init_data()

        assert order.get_server_time() == "20250404 14:30:00"

    def test_get_local_update_time(self):
        """Test get_local_update_time."""
        data = {"lastFillTime": "20250404 14:30:00"}
        order = IbOrderData(data)
        order.init_data()

        assert order.get_local_update_time() == "20250404 14:30:00"

    def test_get_order_id(self):
        """Test get_order_id."""
        data = {"orderId": 123}
        order = IbOrderData(data)
        order.init_data()

        assert order.get_order_id() == 123

    def test_get_client_order_id(self):
        """Test get_client_order_id."""
        data = {"permId": 456}
        order = IbOrderData(data)
        order.init_data()

        assert order.get_client_order_id() == 456

    def test_get_order_size(self):
        """Test get_order_size."""
        data = {"totalQuantity": 100}
        order = IbOrderData(data)
        order.init_data()

        assert order.get_order_size() == 100

    def test_get_order_price(self):
        """Test get_order_price."""
        data = {"lmtPrice": 150.0}
        order = IbOrderData(data)
        order.init_data()

        assert order.get_order_price() == 150.0

    def test_get_order_side_buy(self):
        """Test get_order_side for BUY."""
        data = {"action": "BUY"}
        order = IbOrderData(data)
        order.init_data()

        assert order.get_order_side() == "buy"

    def test_get_order_side_sell(self):
        """Test get_order_side for SELL."""
        data = {"action": "SELL"}
        order = IbOrderData(data)
        order.init_data()

        assert order.get_order_side() == "sell"

    def test_get_order_status(self):
        """Test get_order_status."""
        data = {"status": "Submitted"}
        order = IbOrderData(data)
        order.init_data()

        assert order.get_order_status() == OrderStatus.ACCEPTED

    def test_get_executed_qty(self):
        """Test get_executed_qty."""
        data = {"filled": 50}
        order = IbOrderData(data)
        order.init_data()

        assert order.get_executed_qty() == 50

    def test_get_order_symbol_name(self):
        """Test get_order_symbol_name."""
        order = IbOrderData({}, symbol_name="AAPL")
        assert order.get_order_symbol_name() == "AAPL"

    def test_get_order_type(self):
        """Test get_order_type."""
        data = {"orderType": "LMT"}
        order = IbOrderData(data)
        order.init_data()

        assert order.get_order_type() == "LMT"

    def test_get_order_avg_price(self):
        """Test get_order_avg_price."""
        data = {"avgFillPrice": 150.0}
        order = IbOrderData(data)
        order.init_data()

        assert order.get_order_avg_price() == 150.0

    def test_get_order_time_in_force(self):
        """Test get_order_time_in_force."""
        data = {"tif": "GTC"}
        order = IbOrderData(data)
        order.init_data()

        assert order.get_order_time_in_force() == "GTC"

    def test_get_order_exchange_id(self):
        """Test get_order_exchange_id always returns SMART."""
        order = IbOrderData({})
        assert order.get_order_exchange_id() == "SMART"

    def test_str_representation(self):
        """Test __str__ method."""
        data = {
            "action": "BUY",
            "orderType": "LMT",
            "lmtPrice": 150.0,
            "totalQuantity": 100,
            "status": "Submitted",
        }
        order = IbOrderData(data, symbol_name="AAPL")
        order.init_data()

        result = str(order)

        assert "AAPL" in result
        assert "BUY" in result
        assert "LMT" in result
        assert "150" in result

    def test_repr_representation(self):
        """Test __repr__ method."""
        order = IbOrderData({}, symbol_name="AAPL")
        result = repr(order)
        assert result == str(order)


class TestIbOrderStatusMap:
    """Tests for IB_ORDER_STATUS_MAP."""

    def test_map_keys(self):
        """Test IB_ORDER_STATUS_MAP has expected keys."""
        assert "PendingSubmit" in IB_ORDER_STATUS_MAP
        assert "Submitted" in IB_ORDER_STATUS_MAP
        assert "Filled" in IB_ORDER_STATUS_MAP
        assert "Cancelled" in IB_ORDER_STATUS_MAP

    def test_map_values(self):
        """Test IB_ORDER_STATUS_MAP values."""
        assert IB_ORDER_STATUS_MAP["PendingSubmit"] == OrderStatus.SUBMITTED
        assert IB_ORDER_STATUS_MAP["Submitted"] == OrderStatus.ACCEPTED
        assert IB_ORDER_STATUS_MAP["Filled"] == OrderStatus.COMPLETED
        assert IB_ORDER_STATUS_MAP["Cancelled"] == OrderStatus.CANCELED
        assert IB_ORDER_STATUS_MAP["Inactive"] == OrderStatus.REJECTED
