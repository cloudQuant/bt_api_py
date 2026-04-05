"""Tests for OKX symbol module."""

from __future__ import annotations

import pytest

from bt_api_py.containers.symbols.okx_symbol import OkxSymbolData


class TestOkxSymbolData:
    """Tests for OkxSymbolData class."""

    def test_init(self):
        """Test initialization."""
        symbol = OkxSymbolData({"instId": "BTC-USDT-SWAP"}, has_been_json_encoded=True)

        assert symbol.exchange_name == "OKX"
        assert symbol.event == "OkxSymbolEvent"

    def test_init_data(self):
        """Test init_data method."""
        symbol_info = {
            "instId": "BTC-USDT-SWAP",
            "instType": "SWAP",
            "uly": "BTC-USDT",
            "category": "1",
            "baseCcy": "BTC",
            "quoteCcy": "USDT",
            "settleCcy": "USDT",
            "ctVal": "10",
            "ctMult": "1",
            "ctValCcy": "BTC",
            "tickSz": "0.1",
            "lotSz": "1",
            "minSz": "1",
            "maxSz": "10000",
            "st": "live",
            "listTime": "1548133413000",
        }
        symbol = OkxSymbolData(symbol_info, has_been_json_encoded=True)
        symbol.init_data()

        assert symbol.symbol_name == "BTC-USDT-SWAP"
        assert symbol.asset_type == "SWAP"
        assert symbol.base_asset == "BTC"
        assert symbol.quote_asset == "USDT"

    def test_symbol_data_inheritance(self):
        """Test that OkxSymbolData inherits from SymbolData."""
        symbol = OkxSymbolData({}, has_been_json_encoded=True)

        assert hasattr(symbol, "symbol_info")
        assert hasattr(symbol, "event")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
