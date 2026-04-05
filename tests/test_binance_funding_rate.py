"""Tests for Binance funding rate module."""

from __future__ import annotations

import pytest

from bt_api_py.containers.fundingrates.binance_funding_rate import BinanceRequestFundingRateData


class TestBinanceRequestFundingRateData:
    """Tests for BinanceRequestFundingRateData class."""

    def test_init(self):
        """Test initialization."""
        funding_rate = BinanceRequestFundingRateData(
            {"symbol": "BTCUSDT"},
            symbol_name="BTCUSDT",
            asset_type="FUTURE",
            has_been_json_encoded=True,
        )

        assert funding_rate.exchange_name == "BINANCE"
        assert funding_rate.symbol_name == "BTCUSDT"
        assert funding_rate.asset_type == "FUTURE"

    def test_init_data(self):
        """Test init_data method."""
        funding_rate_info = {
            "symbol": "BTCUSDT",
            "time": 1705315800000,
            "nextFundingRate": "0.0001",
            "nextFundingTime": 1705344000000,
            "lastFundingRate": "0.0002",
        }
        funding_rate = BinanceRequestFundingRateData(
            funding_rate_info,
            symbol_name="BTCUSDT",
            asset_type="FUTURE",
            has_been_json_encoded=True,
        )
        funding_rate.init_data()

        assert funding_rate.funding_rate_symbol_name == "BTCUSDT"
        assert funding_rate.next_funding_rate == 0.0001
        assert funding_rate.next_funding_rate_time == 1705344000000.0
        assert funding_rate.current_funding_rate == 0.0002

    def test_funding_rate_data_inheritance(self):
        """Test that BinanceRequestFundingRateData inherits from FundingRateData."""
        funding_rate = BinanceRequestFundingRateData(
            {}, symbol_name="BTCUSDT", asset_type="FUTURE", has_been_json_encoded=True
        )

        assert hasattr(funding_rate, "funding_rate_info")
        assert hasattr(funding_rate, "event")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
