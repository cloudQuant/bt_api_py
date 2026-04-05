"""Tests for BitfinexFundingRateData container."""

from __future__ import annotations

from bt_api_py.containers.fundingrates.bitfinex_funding_rate import (
    BitfinexFundingRateData,
    BitfinexRequestFundingRateData,
)


class TestBitfinexFundingRateData:
    """Tests for BitfinexFundingRateData."""

    def test_init(self):
        """Test initialization."""
        rate = BitfinexFundingRateData({}, symbol_name="BTCUSD", asset_type="FUTURE")

        assert rate.exchange_name == "BITFINEX"
        assert rate.symbol_name == "BTCUSD"
        assert rate.asset_type == "FUTURE"


class TestBitfinexRequestFundingRateData:
    """Tests for BitfinexRequestFundingRateData."""

    def test_init(self):
        """Test initialization."""
        rate = BitfinexRequestFundingRateData({}, symbol_name="BTCUSD", asset_type="FUTURE")

        assert rate.exchange_name == "BITFINEX"
        assert rate.symbol_name == "BTCUSD"
        assert rate.asset_type == "FUTURE"
