"""Tests for Binance mark price module."""

import pytest

from bt_api_py.containers.markprices.binance_mark_price import BinanceMarkPrice


class TestBinanceMarkPrice:
    """Tests for BinanceMarkPrice class."""

    def test_init(self):
        """Test initialization."""
        mark_price = BinanceMarkPrice(
            {"symbol": "BTCUSDT"},
            symbol_name="BTCUSDT",
            asset_type="FUTURE",
            has_been_json_encoded=True
        )

        assert mark_price.exchange_name == "BINANCE"
        assert mark_price.symbol_name == "BTCUSDT"
        assert mark_price.asset_type == "FUTURE"

    def test_init_data_raises_not_implemented(self):
        """Test that init_data raises NotImplementedError."""
        mark_price = BinanceMarkPrice(
            {"symbol": "BTCUSDT"},
            symbol_name="BTCUSDT",
            asset_type="FUTURE",
            has_been_json_encoded=True
        )

        with pytest.raises(NotImplementedError):
            mark_price.init_data()

    def test_mark_price_data_inheritance(self):
        """Test that BinanceMarkPrice inherits from MarkPriceData."""
        mark_price = BinanceMarkPrice(
            {},
            symbol_name="BTCUSDT",
            asset_type="FUTURE",
            has_been_json_encoded=True
        )

        assert hasattr(mark_price, "mark_price_info")
        assert hasattr(mark_price, "event")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
