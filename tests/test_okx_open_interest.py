"""Tests for OKX open interest module."""

import pytest

from bt_api_py.containers.openinterests.okx_open_interest import OkxOpenInterestData


class TestOkxOpenInterestData:
    """Tests for OkxOpenInterestData class."""

    def test_init(self):
        """Test initialization."""
        open_interest = OkxOpenInterestData(
            {"instId": "BTC-USDT-SWAP", "oi": "1000000"},
            "BTC-USDT-SWAP",
            "SWAP",
            has_been_json_encoded=True,
        )

        assert open_interest.event == "OpenInterestEvent"
        assert open_interest.open_interest_info == {"instId": "BTC-USDT-SWAP", "oi": "1000000"}
        assert open_interest.has_been_json_encoded is True
        assert open_interest.exchange_name == "OKX"
        assert open_interest.symbol_name == "BTC-USDT-SWAP"

    def test_init_without_json_encoded(self):
        """Test initialization without json encoded."""
        open_interest = OkxOpenInterestData(
            '{"instId": "BTC-USDT-SWAP", "oi": "1000000"}',
            "BTC-USDT-SWAP",
            "SWAP",
            has_been_json_encoded=False,
        )

        assert open_interest.event == "OpenInterestEvent"
        assert open_interest.has_been_json_encoded is False
        assert open_interest.open_interest_data is None

    def test_init_data(self):
        """Test init_data method."""
        open_interest = OkxOpenInterestData(
            {"instId": "BTC-USDT-SWAP", "oi": "1000000", "ts": "1705315800000"},
            "BTC-USDT-SWAP",
            "SWAP",
            has_been_json_encoded=True,
        )
        open_interest.init_data()

        assert open_interest.open_interest_symbol_name == "BTC-USDT-SWAP"
        assert open_interest.open_interest == 1000000.0
        assert open_interest.server_time == 1705315800000.0

    def test_get_event(self):
        """Test get_event method."""
        open_interest = OkxOpenInterestData({}, "BTC-USDT-SWAP", "SWAP", has_been_json_encoded=True)

        assert open_interest.event == "OpenInterestEvent"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
