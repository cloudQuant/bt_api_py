"""Tests for Binance order data container."""

import pytest

from bt_api_py.containers.orders.binance_order import BinanceForceOrderData


class TestBinanceForceOrderData:
    """Tests for BinanceForceOrderData class."""

    def test_init_with_dict(self):
        """Test initialization with dict data."""
        order_info = {
            "E": 1705315800000.0,
            "o": {
                "s": "BTCUSDT",
                "S": "BUY",
                "o": "LIMIT",
                "f": "GTC",
                "p": 50000.0,
                "q": 0.1,
                "ap": 50000.0,
                "X": "FILLED",
                "T": 1705315800000.0,
                "l": 0.1,
                "z": 0.1,
            }
        }
        order = BinanceForceOrderData(
            order_info, "BTCUSDT", "SPOT", has_been_json_encoded=True
        )

        assert order.symbol_name == "BTCUSDT"
        assert order.exchange_name == "BINANCE"
        assert order.asset_type == "SPOT"

    def test_init_data(self):
        """Test init_data method."""
        order_info = {
            "E": 1705315800000.0,
            "o": {
                "s": "BTCUSDT",
                "S": "BUY",
                "o": "LIMIT",
                "f": "GTC",
                "p": 50000.0,
                "q": 0.1,
                "ap": 50000.0,
                "X": "FILLED",
                "T": 1705315800000.0,
                "l": 0.1,
                "z": 0.1,
            }
        }
        order = BinanceForceOrderData(
            order_info, "BTCUSDT", "SPOT", has_been_json_encoded=True
        )
        order.init_data()

        assert order.order_symbol_name == "BTCUSDT"
        assert order.order_side == "BUY"
        assert order.order_type == "LIMIT"
        assert order.order_price == 50000.0
        assert order.order_qty == 0.1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
