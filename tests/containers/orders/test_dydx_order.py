"""Tests for DydxOrderData container."""

from bt_api_py.containers.orders.dydx_order import DydxOrderData


class TestDydxOrderData:
    """Tests for DydxOrderData."""

    def test_init(self):
        """Test initialization."""
        order = DydxOrderData({}, symbol_name="BTC-USD", asset_type="SWAP")

        assert order.exchange_name == "DYDX"
        assert order.symbol_name == "BTC-USD"
        assert order.asset_type == "SWAP"
        assert order.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with order info."""
        data = {
            "id": "123456",
            "clientId": "abc123",
            "market": "BTC-USD",
            "side": "BUY",
            "type": "LIMIT",
            "size": "1.0",
            "price": "50000.0",
            "status": "OPEN",
            "filledSize": "0.5",
            "remainingSize": "0.5",
        }
        order = DydxOrderData(
            data, symbol_name="BTC-USD", asset_type="SWAP", has_been_json_encoded=True
        )
        order.init_data()

        assert order.order_id == "123456"
        assert order.side == "BUY"
        assert order.price == 50000.0

    def test_get_all_data(self):
        """Test get_all_data."""
        order = DydxOrderData(
            {}, symbol_name="BTC-USD", asset_type="SWAP", has_been_json_encoded=True
        )
        result = order.get_all_data()

        assert result["exchange_name"] == "DYDX"
        assert result["symbol_name"] == "BTC-USD"

    def test_str_representation(self):
        """Test __str__ method."""
        order = DydxOrderData(
            {}, symbol_name="BTC-USD", asset_type="SWAP", has_been_json_encoded=True
        )
        result = str(order)

        assert "DYDX" in result
