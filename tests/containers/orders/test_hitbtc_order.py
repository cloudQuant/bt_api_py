"""Tests for HitBtcRequestOrderData container."""

import pytest

from bt_api_py.containers.orders.hitbtc_order import HitBtcRequestOrderData


class TestHitBtcRequestOrderData:
    """Tests for HitBtcRequestOrderData."""

    def test_init(self):
        """Test initialization."""
        order = HitBtcRequestOrderData({}, symbol_name="BTCUSD", asset_type="SPOT")

        assert order.exchange_name == "HITBTC"
        assert order.symbol_name == "BTCUSD"
        assert order.asset_type == "SPOT"
        assert order.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with order info."""
        data = {
            "id": "123456",
            "client_order_id": "abc123",
            "symbol": "BTCUSD",
            "side": "buy",
            "type": "limit",
            "status": "filled",
            "quantity": "1.0",
            "quantity_filled": "1.0",
            "price": "50000.0",
        }
        order = HitBtcRequestOrderData(data, symbol_name="BTCUSD", asset_type="SPOT", has_been_json_encoded=True)
        order.init_data()

        assert order.order_id == "123456"
        assert order.side == "buy"
        assert order.price == 50000.0

    def test_get_all_data(self):
        """Test get_all_data."""
        order = HitBtcRequestOrderData({}, symbol_name="BTCUSD", asset_type="SPOT", has_been_json_encoded=True)
        result = order.get_all_data()

        assert result["exchange_name"] == "HITBTC"
        assert result["symbol_name"] == "BTCUSD"

    def test_str_representation(self):
        """Test __str__ method."""
        order = HitBtcRequestOrderData({}, symbol_name="BTCUSD", asset_type="SPOT", has_been_json_encoded=True)
        result = str(order)

        assert "HITBTC" in result
