"""Tests for LatokenRequestOrderData container."""

from bt_api_py.containers.orders.latoken_order import LatokenRequestOrderData


class TestLatokenRequestOrderData:
    """Tests for LatokenRequestOrderData."""

    def test_init(self):
        order_info = {"id": "2"}
        order = LatokenRequestOrderData(order_info, symbol_name="BTCUSDT", asset_type="SPOT")

        assert order.order_info == order_info
        assert order.exchange_name == "LATOKEN"
        assert order.symbol_name == "BTCUSDT"
        assert order.asset_type == "SPOT"
        assert isinstance(order.local_update_time, float)
        assert order.order_data is None

    def test_init_with_json_encoded_and_get_all_data(self):
        order_info = {"id": "2", "status": "filled"}
        order = LatokenRequestOrderData(
            order_info,
            symbol_name="BTCUSDT",
            asset_type="SPOT",
            has_been_json_encoded=True,
        )
        order._initialized = True
        order.order_status = "filled"

        result = order.get_all_data()

        assert order.order_data == order_info
        assert result["exchange_name"] == "LATOKEN"
        assert result["symbol_name"] == "BTCUSDT"
        assert result["asset_type"] == "SPOT"
        assert result["order_status"] == "filled"
        assert result["local_update_time"] == order.local_update_time
