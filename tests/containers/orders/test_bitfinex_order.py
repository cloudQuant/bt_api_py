"""Tests for BitfinexOrderData container."""


from bt_api_py.containers.orders.bitfinex_order import BitfinexOrderData


class TestBitfinexOrderData:
    """Tests for BitfinexOrderData."""

    def test_init(self):
        """Test initialization."""
        order = BitfinexOrderData({}, symbol_name="BTCUSD", asset_type="SPOT")

        assert order.exchange_name == "BITFINEX"
        assert order.symbol_name == "BTCUSD"
        assert order.asset_type == "SPOT"
        assert order.has_been_init_data is False

    def test_init_data_dict_format(self):
        """Test init_data with dict format."""
        # Bitfinex order data can be a dict
        data = {
            "id": 123456,
            "gid": 1,
            "cid": 100,
            "symbol": "tBTCUSD",
            "mts_create": 1234567890000,
            "mts_update": 1234567890000,
            "amount": 1.0,
            "amount_orig": 1.0,
            "type": "LIMIT",
            "status": "ACTIVE",
            "price": 50000.0,
            "price_avg": 49500.0,
        }
        order = BitfinexOrderData(
            data, symbol_name="BTCUSD", asset_type="SPOT", has_been_json_encoded=True
        )
        order.init_data()

        # Verify init_data was called
        assert order.has_been_init_data is True

    def test_get_all_data(self):
        """Test get_all_data."""
        order = BitfinexOrderData(
            {}, symbol_name="BTCUSD", asset_type="SPOT", has_been_json_encoded=True
        )
        result = order.get_all_data()

        assert result["exchange_name"] == "BITFINEX"
        assert result["symbol_name"] == "BTCUSD"

    def test_str_representation(self):
        """Test __str__ method."""
        order = BitfinexOrderData(
            {}, symbol_name="BTCUSD", asset_type="SPOT", has_been_json_encoded=True
        )
        result = str(order)

        assert "BITFINEX" in result
