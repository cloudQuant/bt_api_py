"""Tests for OKX price limit module."""

from __future__ import annotations

import pytest

from bt_api_py.containers.pricelimits.okx_price_limit import OkxPriceLimitData


class TestOkxPriceLimitData:
    """Tests for OkxPriceLimitData class."""

    def test_init(self):
        """Test initialization."""
        price_limit = OkxPriceLimitData(
            {"instId": "BTC-USDT-SWAP"},
            symbol_name="BTC-USDT-SWAP",
            asset_type="FUTURE",
            has_been_json_encoded=True,
        )

        assert price_limit.exchange_name == "OKX"
        assert price_limit.symbol_name == "BTC-USDT-SWAP"
        assert price_limit.asset_type == "FUTURE"
        assert price_limit.event == "PriceLimitEvent"

    def test_init_data(self):
        """Test init_data method."""
        price_limit_info = {
            "instId": "BTC-USDT-SWAP",
            "ts": "1705315800000",
            "buyLmt": "40000",
            "sellLmt": "42000",
        }
        price_limit = OkxPriceLimitData(
            price_limit_info,
            symbol_name="BTC-USDT-SWAP",
            asset_type="FUTURE",
            has_been_json_encoded=True,
        )
        price_limit.init_data()

        assert price_limit.price_limit_symbol_name == "BTC-USDT-SWAP"
        assert price_limit.server_time == 1705315800000.0
        assert price_limit.buy_limit == 40000.0
        assert price_limit.sell_limit == 42000.0

    def test_event_attribute(self):
        """Test event attribute."""
        price_limit = OkxPriceLimitData(
            {}, symbol_name="BTC-USDT-SWAP", asset_type="FUTURE", has_been_json_encoded=True
        )

        assert price_limit.event == "PriceLimitEvent"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
