"""Tests for KorbitTickerData container."""

import pytest

from bt_api_py.containers.tickers.korbit_ticker import KorbitTickerData


class TestKorbitTickerData:
    """Tests for KorbitTickerData."""

    def test_init(self):
        """Test initialization."""
        ticker = KorbitTickerData({}, symbol_name="btc_krw", asset_type="SPOT")

        assert ticker.exchange_name == "KORBIT"
        assert ticker.symbol_name == "btc_krw"
        assert ticker.asset_type == "SPOT"
        assert ticker.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with ticker info."""
        data = {"last": "50000000", "bid": "49990000", "ask": "50010000"}
        ticker = KorbitTickerData(data, symbol_name="btc_krw", asset_type="SPOT", has_been_json_encoded=True)
        ticker.init_data()

        assert ticker.has_been_init_data is True

    def test_get_all_data(self):
        """Test get_all_data."""
        ticker = KorbitTickerData({}, symbol_name="btc_krw", asset_type="SPOT", has_been_json_encoded=True)
        result = ticker.get_all_data()

        assert result["exchange_name"] == "KORBIT"
        assert result["symbol_name"] == "btc_krw"

    def test_str_representation(self):
        """Test __str__ method."""
        ticker = KorbitTickerData({}, symbol_name="btc_krw", asset_type="SPOT", has_been_json_encoded=True)
        result = str(ticker)

        assert "KORBIT" in result
