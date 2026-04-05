"""Tests for BinanceForceOrderData container."""

from __future__ import annotations

from bt_api_py.containers.orders.binance_order import BinanceForceOrderData
from bt_api_py.containers.orders.order import OrderStatus


class TestBinanceForceOrderData:
    """Tests for BinanceForceOrderData."""

    def test_init(self):
        """Test initialization."""
        order = BinanceForceOrderData({}, symbol_name="BTCUSDT", asset_type="SPOT")

        assert order.exchange_name == "BINANCE"
        assert order.symbol_name == "BTCUSDT"
        assert order.asset_type == "SPOT"
        assert order.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with order info."""
        data = {
            "E": 1234567890000,
            "o": {
                "s": "BTCUSDT",
                "S": "BUY",
                "o": "LIMIT",
                "f": "GTC",
                "p": "50000.0",
                "q": "1.0",
                "ap": "49500.0",
                "X": "FILLED",
                "T": 1234567890000,
                "l": "1.0",
                "z": "1.0",
            },
        }
        order = BinanceForceOrderData(
            data, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True
        )
        order.init_data()

        assert order.order_symbol_name == "BTCUSDT"
        assert order.order_side == "BUY"
        assert order.order_price == 50000.0
        assert order.order_status == OrderStatus.FILLED

    def test_get_all_data(self):
        """Test get_all_data."""
        data = {"E": 1234567890000, "o": {"s": "BTCUSDT", "S": "BUY"}}
        order = BinanceForceOrderData(
            data, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True
        )
        result = order.get_all_data()

        assert result["exchange_name"] == "BINANCE"
        assert result["symbol_name"] == "BTCUSDT"

    def test_str_representation(self):
        """Test __str__ method."""
        data = {"E": 1234567890000, "o": {"s": "BTCUSDT"}}
        order = BinanceForceOrderData(
            data, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True
        )
        result = str(order)

        assert "BINANCE" in result
