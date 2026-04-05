"""Tests for KorbitRequestOrderData container."""

from bt_api_py.containers.orders.korbit_order import KorbitRequestOrderData


class TestKorbitRequestOrderData:
    """Tests for KorbitRequestOrderData."""

    def test_init(self):
        order_info = {"id": "1"}
        order = KorbitRequestOrderData(order_info, symbol_name="BTCKRW", asset_type="SPOT")

        assert order.order_info == order_info
        assert order.exchange_name == "KORBIT"
        assert order.symbol_name == "BTCKRW"
        assert order.asset_type == "SPOT"
        assert isinstance(order.local_update_time, float)
        assert order.order_data is None

    def test_init_with_json_encoded_and_get_all_data(self):
        order_info = {"id": "1", "status": "accepted"}
        order = KorbitRequestOrderData(
            order_info,
            symbol_name="BTCKRW",
            asset_type="SPOT",
            has_been_json_encoded=True,
        )
        order._initialized = True
        order.order_status = "accepted"

        result = order.get_all_data()

        assert order.order_data == order_info
        assert result["exchange_name"] == "KORBIT"
        assert result["symbol_name"] == "BTCKRW"
        assert result["asset_type"] == "SPOT"
        assert result["order_status"] == "accepted"
        assert result["local_update_time"] == order.local_update_time
