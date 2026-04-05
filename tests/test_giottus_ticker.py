"""Tests for Giottus ticker data container."""

from __future__ import annotations

import pytest

from bt_api_py.containers.tickers.giottus_ticker import GiottusRequestTickerData


class TestGiottusRequestTickerData:
    """Tests for GiottusRequestTickerData class."""

    def test_init_with_dict(self):
        """Test initialization with dict data."""
        ticker_info = {
            "symbol": "BTCINR",
            "lastPrice": 5000000.0,
            "bidPrice": 4999999.0,
            "askPrice": 5000001.0,
        }
        ticker = GiottusRequestTickerData(ticker_info, "BTCINR", "SPOT", has_been_json_encoded=True)

        assert ticker.symbol_name == "BTCINR"
        assert ticker.exchange_name == "GIOTTUS"

    def test_init_data(self):
        """Test init_data method."""
        ticker_info = {
            "symbol": "BTCINR",
            "lastPrice": 5000000.0,
            "bidPrice": 4999999.0,
            "askPrice": 5000001.0,
        }
        ticker = GiottusRequestTickerData(ticker_info, "BTCINR", "SPOT", has_been_json_encoded=True)
        ticker.init_data()

        assert ticker.last_price == 5000000.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
