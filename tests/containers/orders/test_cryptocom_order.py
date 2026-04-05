"""Tests for CryptoComOrder container."""

from bt_api_py.containers.orders.cryptocom_order import CryptoComOrder


class TestCryptoComOrder:
    """Tests for CryptoComOrder."""

    def test_init(self):
        """Test initialization."""
        order = CryptoComOrder({}, symbol_name="BTC-USDT", asset_type="SPOT")

        assert order.exchange_name == "CRYPTOCOM"
        assert order.symbol_name == "BTC-USDT"
        assert order.asset_type == "SPOT"
        assert order.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with order info."""
        data = {
            "order_id": "123456",
            "client_oid": "abc123",
            "side": "BUY",
            "type": "LIMIT",
            "quantity": "1.0",
            "price": "50000.0",
            "status": "FILLED",
            "filled_quantity": "1.0",
            "remaining_quantity": "0.0",
        }
        order = CryptoComOrder(
            data, symbol_name="BTC-USDT", asset_type="SPOT", has_been_json_encoded=True
        )
        order.init_data()

        assert order.order_id == "123456"
        assert order.side == "BUY"
        assert order.price == 50000.0

    def test_get_all_data(self):
        """Test get_all_data."""
        order = CryptoComOrder(
            {}, symbol_name="BTC-USDT", asset_type="SPOT", has_been_json_encoded=True
        )
        result = order.get_all_data()

        assert result["exchange_name"] == "CRYPTOCOM"
        assert result["symbol_name"] == "BTC-USDT"

    def test_str_representation(self):
        """Test __str__ method."""
        order = CryptoComOrder(
            {}, symbol_name="BTC-USDT", asset_type="SPOT", has_been_json_encoded=True
        )
        result = str(order)

        assert "CRYPTOCOM" in result
