"""Tests for LocalBitcoinsTickerData container."""

import pytest

from bt_api_py.containers.tickers.localbitcoins_ticker import LocalBitcoinsTickerData


class TestLocalBitcoinsTickerData:
    """Tests for LocalBitcoinsTickerData."""

    def test_init(self):
        """Test initialization."""
        ticker = LocalBitcoinsTickerData({}, symbol_name="BTC-USD", asset_type="SPOT")

        assert ticker.exchange_name == "LOCALBITCOINS"
        assert ticker.symbol_name == "BTC-USD"
        assert ticker.asset_type == "SPOT"
        assert ticker.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with ticker info."""
        data = {"avg_1h": "50000.0", "avg_6h": "49990.0"}
        ticker = LocalBitcoinsTickerData(data, symbol_name="BTC-USD", asset_type="SPOT", has_been_json_encoded=True)
        ticker.init_data()

        assert ticker.has_been_init_data is True

    def test_get_all_data(self):
        """Test get_all_data."""
        ticker = LocalBitcoinsTickerData({}, symbol_name="BTC-USD", asset_type="SPOT", has_been_json_encoded=True)
        result = ticker.get_all_data()

        assert result["exchange_name"] == "LOCALBITCOINS"
        assert result["symbol_name"] == "BTC-USD"

    def test_str_representation(self):
        """Test __str__ method."""
        ticker = LocalBitcoinsTickerData({}, symbol_name="BTC-USD", asset_type="SPOT", has_been_json_encoded=True)
        result = str(ticker)

        assert "LOCALBITCOINS" in result
