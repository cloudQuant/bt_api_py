"""Tests for UpbitOrderData container."""


from bt_api_py.containers.orders.upbit_order import UpbitOrderData


class TestUpbitOrderData:
    """Tests for UpbitOrderData."""

    def test_init(self):
        """Test initialization."""
        order = UpbitOrderData({}, symbol_name="KRW-BTC", asset_type="SPOT")

        assert order.exchange_name == "UPBIT"
        assert order.symbol_name == "KRW-BTC"
        assert order.asset_type == "SPOT"
        assert order.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with order info."""
        data = {
            "uuid": "123456",
            "identifier": "abc123",
            "state": "done",
            "side": "bid",
            "ord_type": "limit",
            "price": "50000.0",
            "volume": "1.0",
            "executed_volume": "1.0",
            "remaining_volume": "0.0",
            "market": "KRW-BTC",
        }
        order = UpbitOrderData(
            data, symbol_name="KRW-BTC", asset_type="SPOT", has_been_json_encoded=True
        )
        order.init_data()

        assert order.order_uuid == "123456"
        assert order.side == "bid"
        assert order.price == 50000.0

    def test_get_all_data(self):
        """Test get_all_data."""
        order = UpbitOrderData(
            {}, symbol_name="KRW-BTC", asset_type="SPOT", has_been_json_encoded=True
        )
        result = order.get_all_data()

        assert result["exchange_name"] == "UPBIT"
        assert result["symbol_name"] == "KRW-BTC"

    def test_str_representation(self):
        """Test __str__ method."""
        order = UpbitOrderData(
            {}, symbol_name="KRW-BTC", asset_type="SPOT", has_been_json_encoded=True
        )
        result = str(order)

        assert "Upbit" in result
