"""Tests for OKX open interest module."""

import pytest

from bt_api_py.containers.openinterests.okx_open_interest import OkxOpenInterestData


class TestOkxOpenInterestData:
    """Tests for OkxOpenInterestData class."""

    def test_init(self):
        """Test initialization."""
        open_interest = OkxOpenInterestData(
            {"instId": "BTC-USDT-SWAP"},
            symbol_name="BTC-USDT-SWAP",
            asset_type="FUTURE",
            has_been_json_encoded=True
        )

        assert open_interest.exchange_name == "OKX"
        assert open_interest.symbol_name == "BTC-USDT-SWAP"
        assert open_interest.asset_type == "FUTURE"
        assert open_interest.event == "OpenInterestEvent"

    def test_init_data(self):
        """Test init_data method."""
        open_interest_info = {
            "instId": "BTC-USDT-SWAP",
            "ts": "1705315800000",
            "oi": "100000",
            "oiCcy": "BTC",
        }
        open_interest = OkxOpenInterestData(
            open_interest_info,
            symbol_name="BTC-USDT-SWAP",
            asset_type="FUTURE",
            has_been_json_encoded=True
        )
        open_interest.init_data()

        assert open_interest.open_interest_symbol_name == "BTC-USDT-SWAP"
        assert open_interest.server_time == 1705315800000.0
        assert open_interest.open_interest == 100000.0
        assert open_interest.open_interest_ccy == "BTC"

    def test_event_attribute(self):
        """Test event attribute."""
        open_interest = OkxOpenInterestData(
            {},
            symbol_name="BTC-USDT-SWAP",
            asset_type="FUTURE",
            has_been_json_encoded=True
        )

        assert open_interest.event == "OpenInterestEvent"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
