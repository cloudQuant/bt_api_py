"""Tests for HtxRequestOrderData container."""

import pytest

from bt_api_py.containers.orders.htx_order import HtxRequestOrderData


class TestHtxRequestOrderData:
    """Tests for HtxRequestOrderData."""

    def test_init(self):
        """Test initialization."""
        order = HtxRequestOrderData({}, symbol_name="btcusdt", asset_type="SPOT")

        assert order.exchange_name == "HTX"
        assert order.symbol_name == "btcusdt"
        assert order.asset_type == "SPOT"
        assert order.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with order info."""
        data = {
            "id": 123456789,
            "symbol": "btcusdt",
            "type": "buy-limit",
            "price": "50000",
            "amount": "1.0",
            "field-amount": "1.0",
            "state": "filled",
        }
        order = HtxRequestOrderData(data, symbol_name="btcusdt", asset_type="SPOT", has_been_json_encoded=True)
        order.init_data()

        assert order.order_id == "123456789"
        assert order.order_symbol_name == "btcusdt"

    def test_get_all_data(self):
        """Test get_all_data."""
        order = HtxRequestOrderData({}, symbol_name="btcusdt", asset_type="SPOT", has_been_json_encoded=True)
        result = order.get_all_data()

        assert result["exchange_name"] == "HTX"
        assert result["symbol_name"] == "btcusdt"

    def test_str_representation(self):
        """Test __str__ method."""
        order = HtxRequestOrderData({}, symbol_name="btcusdt", asset_type="SPOT", has_been_json_encoded=True)
        result = str(order)

        assert "HTX" in result
