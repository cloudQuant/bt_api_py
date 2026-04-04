"""Tests for CoinbaseOrderData container."""

import pytest

from bt_api_py.containers.orders.coinbase_order import CoinbaseOrderData


class TestCoinbaseOrderData:
    """Tests for CoinbaseOrderData."""

    def test_init(self):
        """Test initialization."""
        order = CoinbaseOrderData({}, symbol_name="BTC-USD", asset_type="SPOT")

        assert order.exchange_name == "COINBASE"
        assert order.symbol_name == "BTC-USD"
        assert order.asset_type == "SPOT"
        assert order.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with order info."""
        data = {
            "order_id": "123456",
            "client_order_id": "abc123",
            "product_id": "BTC-USD",
            "side": "buy",
            "status": "filled",
            "price": "50000.0",
            "size": "1.0",
            "filled_size": "1.0",
        }
        order = CoinbaseOrderData(data, symbol_name="BTC-USD", asset_type="SPOT", has_been_json_encoded=True)
        order.init_data()

        assert order.order_id == "123456"
        assert order.side == "buy"
        assert order.price == 50000.0

    def test_get_all_data(self):
        """Test get_all_data."""
        order = CoinbaseOrderData({}, symbol_name="BTC-USD", asset_type="SPOT", has_been_json_encoded=True)
        result = order.get_all_data()

        assert result["exchange_name"] == "COINBASE"
        assert result["symbol_name"] == "BTC-USD"

    def test_str_representation(self):
        """Test __str__ method."""
        order = CoinbaseOrderData({}, symbol_name="BTC-USD", asset_type="SPOT", has_been_json_encoded=True)
        result = str(order)

        assert "COINBASE" in result
