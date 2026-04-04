"""Tests for MexcTickerData container."""

import pytest

from bt_api_py.containers.tickers.mexc_ticker import MexcTickerData


class TestMexcTickerData:
    """Tests for MexcTickerData."""

    def test_init(self):
        """Test initialization."""
        ticker = MexcTickerData({}, symbol_name="BTCUSDT", asset_type="SPOT")

        assert ticker.exchange_name == "MEXC"
        assert ticker.symbol_name == "BTCUSDT"
        assert ticker.asset_type == "SPOT"
        assert ticker.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with ticker info."""
        data = {
            "symbol": "BTCUSDT",
            "lastPrice": "50000.0",
            "bidPrice": "49990.0",
            "askPrice": "50010.0",
        }
        ticker = MexcTickerData(data, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True)
        ticker.init_data()

        assert ticker.ticker_symbol_name == "BTCUSDT"
        assert ticker.last_price == 50000.0

    def test_get_all_data(self):
        """Test get_all_data."""
        ticker = MexcTickerData({}, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True)
        result = ticker.get_all_data()

        assert result["exchange_name"] == "MEXC"
        assert result["symbol_name"] == "BTCUSDT"

    def test_str_representation(self):
        """Test __str__ method."""
        ticker = MexcTickerData({}, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True)
        result = str(ticker)

        assert "MEXC" in result
