"""Tests for OkxOrderData container."""


from bt_api_py.containers.orders.okx_order import OkxOrderData


class TestOkxOrderData:
    """Tests for OkxOrderData."""

    def test_init(self):
        """Test initialization."""
        order = OkxOrderData({}, symbol_name="BTC-USDT", asset_type="SPOT")

        assert order.exchange_name == "OKX"
        assert order.symbol_name == "BTC-USDT"
        assert order.asset_type == "SPOT"
        assert order.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with order info."""
        # OKX order_data is set directly from order_info when has_been_json_encoded=True
        data = {"ordId": "123456", "instId": "BTC-USDT", "side": "buy", "state": "filled"}
        order = OkxOrderData(
            data, symbol_name="BTC-USDT", asset_type="SPOT", has_been_json_encoded=True
        )
        order.init_data()

        assert order.order_id == "123456"
        assert order.order_symbol_name == "BTC-USDT"

    def test_get_all_data(self):
        """Test get_all_data."""
        order = OkxOrderData(
            {}, symbol_name="BTC-USDT", asset_type="SPOT", has_been_json_encoded=True
        )
        result = order.get_all_data()

        assert result["exchange_name"] == "OKX"
        assert result["symbol_name"] == "BTC-USDT"

    def test_str_representation(self):
        """Test __str__ method."""
        order = OkxOrderData(
            {}, symbol_name="BTC-USDT", asset_type="SPOT", has_been_json_encoded=True
        )
        result = str(order)

        assert "OKX" in result
