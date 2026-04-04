"""Tests for BitgetOrderData container."""

import pytest

from bt_api_py.containers.orders.bitget_order import BitgetOrderData


class TestBitgetOrderData:
    """Tests for BitgetOrderData."""

    def test_init(self):
        """Test initialization."""
        order = BitgetOrderData({}, symbol_name="BTCUSDT", asset_type="SPOT")

        assert order.exchange_name == "BITGET"
        assert order.symbol_name == "BTCUSDT"
        assert order.asset_type == "SPOT"
        assert order.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with order info."""
        data = {
            "orderId": "123456",
            "clientOId": "abc123",
            "symbol": "BTCUSDT",
            "side": "buy",
            "orderType": "limit",
            "status": "filled",
            "size": "1.0",
            "filledSize": "1.0",
            "price": "50000.0",
            "avgPrice": "49500.0",
        }
        order = BitgetOrderData(data, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True)
        order.init_data()

        assert order.order_id == "123456"
        assert order.side == "buy"
        assert order.price == 50000.0

    def test_get_all_data(self):
        """Test get_all_data."""
        order = BitgetOrderData({}, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True)
        result = order.get_all_data()

        assert result["exchange_name"] == "BITGET"
        assert result["symbol_name"] == "BTCUSDT"

    def test_str_representation(self):
        """Test __str__ method."""
        order = BitgetOrderData({}, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True)
        result = str(order)

        assert "BITGET" in result
