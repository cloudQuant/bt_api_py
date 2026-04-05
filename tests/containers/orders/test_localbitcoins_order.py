"""Tests for LocalBitcoinsRequestOrderData container."""

from bt_api_py.containers.orders.localbitcoins_order import LocalBitcoinsRequestOrderData


class TestLocalBitcoinsRequestOrderData:
    """Tests for LocalBitcoinsRequestOrderData."""

    def test_init(self):
        order_info = {"id": "3"}
        order = LocalBitcoinsRequestOrderData(order_info, symbol_name="BTCUSD", asset_type="SPOT")

        assert order.order_info == order_info
        assert order.exchange_name == "LOCALBITCOINS"
        assert order.symbol_name == "BTCUSD"
        assert order.asset_type == "SPOT"
        assert isinstance(order.local_update_time, float)
        assert order.order_data is None

    def test_init_with_json_encoded_and_get_all_data(self):
        order_info = {"id": "3", "status": "canceled"}
        order = LocalBitcoinsRequestOrderData(
            order_info,
            symbol_name="BTCUSD",
            asset_type="SPOT",
            has_been_json_encoded=True,
        )
        order._initialized = True
        order.order_status = "canceled"

        result = order.get_all_data()

        assert order.order_data == order_info
        assert result["exchange_name"] == "LOCALBITCOINS"
        assert result["symbol_name"] == "BTCUSD"
        assert result["asset_type"] == "SPOT"
        assert result["order_status"] == "canceled"
        assert result["local_update_time"] == order.local_update_time
