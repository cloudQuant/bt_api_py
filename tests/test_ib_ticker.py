"""Tests for IB ticker module."""

import pytest

from bt_api_py.containers.ib.ib_ticker import IbTickerData


class TestIbTickerData:
    """Tests for IbTickerData class."""

    def test_init(self):
        """Test initialization."""
        ticker = IbTickerData(
            {"symbol": "AAPL"}, symbol_name="AAPL", asset_type="STK", has_been_json_encoded=True
        )

        assert ticker.exchange_name == "IB"
        assert ticker.symbol_name == "AAPL"
        assert ticker.asset_type == "STK"

    def test_init_data(self):
        """Test init_data method."""
        ticker_info = {
            "symbol": "AAPL",
            "bid": 150.0,
            "ask": 150.5,
            "bidSize": 100,
            "askSize": 200,
            "last": 150.25,
            "lastSize": 50,
            "volume": 1000000,
            "high": 151.0,
            "low": 149.0,
            "close": 150.0,
            "time": "2025-01-01 09:30:00",
        }
        ticker = IbTickerData(
            ticker_info, symbol_name="AAPL", asset_type="STK", has_been_json_encoded=True
        )
        ticker.init_data()

        assert ticker.contract_symbol == "AAPL"
        assert ticker.bid_val == 150.0
        assert ticker.ask_val == 150.5
        assert ticker.bid_size_val == 100.0
        assert ticker.ask_size_val == 200.0
        assert ticker.last_val == 150.25
        assert ticker.volume_val == 1000000
        assert ticker.high_val == 151.0
        assert ticker.low_val == 149.0
        assert ticker.close_val == 150.0

    def test_get_exchange_name(self):
        """Test get_exchange_name method."""
        ticker = IbTickerData({}, has_been_json_encoded=True)

        assert ticker.get_exchange_name() == "IB"

    def test_ticker_data_inheritance(self):
        """Test that IbTickerData inherits from TickerData."""
        ticker = IbTickerData({}, has_been_json_encoded=True)

        assert hasattr(ticker, "ticker_info")
        assert hasattr(ticker, "event")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
