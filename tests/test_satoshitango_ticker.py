"""Tests for SatoshiTango ticker data container."""

from __future__ import annotations

import pytest

from bt_api_py.containers.tickers.satoshitango_ticker import SatoshiTangoRequestTickerData


class TestSatoshiTangoRequestTickerData:
    """Tests for SatoshiTangoRequestTickerData class."""

    def test_init_with_dict(self):
        """Test initialization with dict data."""
        ticker_info = {
            "symbol": "BTCARS",
            "ask": 5000001.0,
            "bid": 4999999.0,
            "price": 5000000.0,
            "volume": 10.5,
        }
        ticker = SatoshiTangoRequestTickerData(
            ticker_info, "BTCARS", "SPOT", has_been_json_encoded=True
        )

        assert ticker.symbol_name == "BTCARS"
        assert ticker.exchange_name == "SATOSHITANGO"

    def test_init_data(self):
        """Test init_data method."""
        ticker_info = {
            "symbol": "BTCARS",
            "ask": 5000001.0,
            "bid": 4999999.0,
            "price": 5000000.0,
            "volume": 10.5,
        }
        ticker = SatoshiTangoRequestTickerData(
            ticker_info, "BTCARS", "SPOT", has_been_json_encoded=True
        )
        ticker.init_data()

        assert ticker.last_price == 5000000.0
        assert ticker.bid_price == 4999999.0
        assert ticker.ask_price == 5000001.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
