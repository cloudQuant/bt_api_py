"""Tests for OKX funding rate module."""

import pytest

from bt_api_py.containers.fundingrates.okx_funding_rate import OkxFundingRateData


class TestOkxFundingRateData:
    """Tests for OkxFundingRateData class."""

    def test_init(self):
        """Test initialization."""
        funding_rate = OkxFundingRateData(
            {"instId": "BTC-USDT-SWAP"},
            symbol_name="BTC-USDT-SWAP",
            asset_type="FUTURE",
            has_been_json_encoded=True,
        )

        assert funding_rate.exchange_name == "OKX"
        assert funding_rate.symbol_name == "BTC-USDT-SWAP"
        assert funding_rate.asset_type == "FUTURE"

    def test_init_data(self):
        """Test init_data method."""
        funding_rate_info = {
            "ts": "1705315800000",
            "nextFundingRate": "0.0001",
            "nextFundingTime": "1705344000000",
            "maxFundingRate": "0.001",
            "minFundingRate": "-0.001",
            "fundingRate": "0.0002",
            "fundingTime": "1705314000000",
            "settFundingRate": "0.0001",
            "settState": "settled",
            "method": "next_period",
        }
        funding_rate = OkxFundingRateData(
            funding_rate_info,
            symbol_name="BTC-USDT-SWAP",
            asset_type="FUTURE",
            has_been_json_encoded=True,
        )
        funding_rate.init_data()

        assert funding_rate.server_time == 1705315800000.0
        assert funding_rate.next_funding_rate == 0.0001
        assert funding_rate.next_funding_time == 1705344000000.0
        assert funding_rate.max_funding_rate == 0.001
        assert funding_rate.min_funding_rate == -0.001
        assert funding_rate.current_funding_rate == 0.0002
        assert funding_rate.current_funding_time == 1705314000000.0
        assert funding_rate.settlement_funding_rate == 0.0001
        assert funding_rate.settlement_status == "settled"
        assert funding_rate.method == "next_period"

    def test_funding_rate_data_inheritance(self):
        """Test that OkxFundingRateData inherits from FundingRateData."""
        funding_rate = OkxFundingRateData(
            {}, symbol_name="BTC-USDT-SWAP", asset_type="FUTURE", has_been_json_encoded=True
        )

        assert hasattr(funding_rate, "funding_rate_info")
        assert hasattr(funding_rate, "event")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
