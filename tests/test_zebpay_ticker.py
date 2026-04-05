"""Tests for ZebPay ticker data container."""

from __future__ import annotations

import pytest

from bt_api_py.containers.tickers.zebpay_ticker import ZebpayRequestTickerData


class TestZebpayRequestTickerData:
    """Tests for ZebpayRequestTickerData class."""

    def test_init_with_dict(self):
        """Test initialization with dict data."""
        ticker_info = {
            "data": {
                "symbol": "BTCINR",
                "last": 5000000.0,
                "bid": 4999999.0,
                "ask": 5000001.0,
            }
        }
        ticker = ZebpayRequestTickerData(ticker_info, "BTCINR", "SPOT", has_been_json_encoded=True)

        assert ticker.symbol_name == "BTCINR"
        assert ticker.exchange_name == "ZEBPAY"

    def test_init_data(self):
        """Test init_data method."""
        ticker_info = {
            "data": {
                "symbol": "BTCINR",
                "last": 5000000.0,
                "bid": 4999999.0,
                "ask": 5000001.0,
            }
        }
        ticker = ZebpayRequestTickerData(ticker_info, "BTCINR", "SPOT", has_been_json_encoded=True)
        ticker.init_data()

        assert ticker.last_price == 5000000.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
