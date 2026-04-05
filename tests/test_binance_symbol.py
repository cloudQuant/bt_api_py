"""Tests for Binance symbol module."""

from __future__ import annotations

import pytest

from bt_api_py.containers.symbols.binance_symbol import BinanceSwapSymbolData


class TestBinanceSwapSymbolData:
    """Tests for BinanceSwapSymbolData class."""

    def test_init(self):
        """Test initialization."""
        symbol = BinanceSwapSymbolData({"symbol": "BTCUSDT"}, has_been_json_encoded=True)

        assert symbol.exchange_name == "BINANCE"
        assert symbol.event == "BinanceSymbolEvent"

    def test_init_data(self):
        """Test init_data method."""
        symbol_info = {
            "symbol": "BTCUSDT",
            "contractType": "PERPETUAL",
            "maintMarginPercent": "2.5",
            "requiredMarginPercent": "5.0",
            "baseAsset": "BTC",
            "quoteAsset": "USDT",
            "pricePrecision": 2,
            "quantityPrecision": 3,
            "orderTypes": ["LIMIT", "MARKET"],
            "timeInForce": ["GTC", "IOC"],
            "filters": [
                {"minPrice": "0.01", "tickSize": "0.01"},
                {"minQty": "0.001", "stepSize": "0.001"},
                {},
                {},
                {},
                {"notional": "10"},
            ],
        }
        symbol = BinanceSwapSymbolData(symbol_info, has_been_json_encoded=True)
        symbol.init_data()

        assert symbol.symbol_name == "BTCUSDT"
        assert symbol.asset_type == "PERPETUAL"
        assert symbol.maintain_margin_percent == 2.5
        assert symbol.required_margin_percent == 5.0
        assert symbol.base_asset == "BTC"
        assert symbol.quote_asset == "USDT"

    def test_symbol_data_inheritance(self):
        """Test that BinanceSwapSymbolData inherits from SymbolData."""
        symbol = BinanceSwapSymbolData({}, has_been_json_encoded=True)

        assert hasattr(symbol, "symbol_info")
        assert hasattr(symbol, "event")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
