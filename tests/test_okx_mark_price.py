"""Tests for OKX mark price module."""

import pytest

from bt_api_py.containers.markprices.okx_mark_price import OkxMarkPriceData


class TestOkxMarkPriceData:
    """Tests for OkxMarkPriceData class."""

    def test_init(self):
        """Test initialization."""
        mark_price = OkxMarkPriceData(
            {"arg": {"instId": "BTC-USDT-SWAP"}, "data": [{"markPx": "40000.0"}]},
            symbol_name="BTC-USDT-SWAP",
            asset_type="FUTURE",
            has_been_json_encoded=True,
        )

        assert mark_price.exchange_name == "OKX"
        assert mark_price.symbol_name == "BTC-USDT-SWAP"
        assert mark_price.asset_type == "FUTURE"

    def test_init_data(self):
        """Test init_data method."""
        mark_price_info = {
            "arg": {"instId": "BTC-USDT-SWAP"},
            "data": [{"ts": "1705315800000", "markPx": "40000.0"}],
        }
        mark_price = OkxMarkPriceData(
            mark_price_info,
            symbol_name="BTC-USDT-SWAP",
            asset_type="FUTURE",
            has_been_json_encoded=False,
        )
        mark_price.init_data()

        assert mark_price.mark_price_symbol_name == "BTC-USDT-SWAP"
        assert mark_price.server_time == 1705315800000.0
        assert mark_price.mark_price == 40000.0

    def test_mark_price_data_inheritance(self):
        """Test that OkxMarkPriceData inherits from MarkPriceData."""
        mark_price = OkxMarkPriceData(
            {}, symbol_name="BTC-USDT-SWAP", asset_type="FUTURE", has_been_json_encoded=True
        )

        assert hasattr(mark_price, "mark_price_info")
        assert hasattr(mark_price, "event")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
