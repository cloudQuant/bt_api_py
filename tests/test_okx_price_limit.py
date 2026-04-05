"""Tests for OKX price limit module."""

from __future__ import annotations

import pytest

from bt_api_py.containers.pricelimits.okx_price_limit import OkxPriceLimitData


class TestOkxPriceLimitData:
    """Tests for OkxPriceLimitData class."""

    def test_init(self):
        """Test initialization."""
        price_limit = OkxPriceLimitData(
            {"instId": "BTC-USDT-SWAP", "buyLmt": "50000", "sellLmt": "51000"},
            "BTC-USDT-SWAP",
            "SWAP",
            has_been_json_encoded=True,
        )

        assert price_limit.event == "PriceLimitEvent"
        assert price_limit.price_limit_info == {
            "instId": "BTC-USDT-SWAP",
            "buyLmt": "50000",
            "sellLmt": "51000",
        }
        assert price_limit.has_been_json_encoded is True
        assert price_limit.exchange_name == "OKX"
        assert price_limit.symbol_name == "BTC-USDT-SWAP"

    def test_init_without_json_encoded(self):
        """Test initialization without json encoded."""
        price_limit = OkxPriceLimitData(
            '{"instId": "BTC-USDT-SWAP", "buyLmt": "50000", "sellLmt": "51000"}',
            "BTC-USDT-SWAP",
            "SWAP",
            has_been_json_encoded=False,
        )

        assert price_limit.event == "PriceLimitEvent"
        assert price_limit.has_been_json_encoded is False
        assert price_limit.price_limit_data is None

    def test_init_data(self):
        """Test init_data method."""
        price_limit = OkxPriceLimitData(
            {
                "instId": "BTC-USDT-SWAP",
                "buyLmt": "50000",
                "sellLmt": "51000",
                "ts": "1705315800000",
            },
            "BTC-USDT-SWAP",
            "SWAP",
            has_been_json_encoded=True,
        )
        price_limit.init_data()

        assert price_limit.price_limit_symbol_name == "BTC-USDT-SWAP"
        assert price_limit.buy_limit == 50000.0
        assert price_limit.sell_limit == 51000.0

    def test_get_event(self):
        """Test get_event method."""
        price_limit = OkxPriceLimitData({}, "BTC-USDT-SWAP", "SWAP", has_been_json_encoded=True)

        assert price_limit.event == "PriceLimitEvent"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
