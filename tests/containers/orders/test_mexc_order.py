"""Tests for MexcOrderData container."""

import pytest

from bt_api_py.containers.orders.mexc_order import MexcOrderData


class TestMexcOrderData:
    """Tests for MexcOrderData."""

    def test_init(self):
        """Test initialization."""
        order = MexcOrderData({}, symbol_name="BTCUSDT", asset_type="SPOT")

        assert order.exchange_name == "MEXC"
        assert order.symbol_name == "BTCUSDT"
        assert order.asset_type == "SPOT"
        assert order.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with order info."""
        data = {
            "orderId": "123456",
            "clientOrderId": "abc123",
            "symbol": "BTCUSDT",
            "status": "FILLED",
            "side": "BUY",
            "type": "LIMIT",
            "origQty": "1.0",
            "executedQty": "1.0",
            "price": "50000.0",
        }
        order = MexcOrderData(data, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True)
        order.init_data()

        assert order.order_id == 123456
        assert order.side == "BUY"
        assert order.price == 50000.0

    def test_get_all_data(self):
        """Test get_all_data."""
        order = MexcOrderData({}, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True)
        result = order.get_all_data()

        assert result["exchange_name"] == "MEXC"
        assert result["symbol_name"] == "BTCUSDT"

    def test_str_representation(self):
        """Test __str__ method."""
        order = MexcOrderData({}, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True)
        result = str(order)

        assert "MEXC" in result
