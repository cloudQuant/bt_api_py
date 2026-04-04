"""Tests for GmxRequestTickerData container."""

import pytest

from bt_api_py.containers.tickers.gmx_ticker import GmxRequestTickerData


class TestGmxRequestTickerData:
    """Tests for GmxRequestTickerData."""

    def test_init(self):
        """Test initialization."""
        ticker = GmxRequestTickerData({}, symbol_name="BTC-USD", asset_type="PERP")

        assert ticker.exchange_name == "GMX"
        assert ticker.asset_type == "PERP"
        assert ticker.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with ticker info."""
        data = {"price": "50000.0"}
        ticker = GmxRequestTickerData(data, symbol_name="BTC-USD", asset_type="PERP", has_been_json_encoded=True)
        ticker.init_data()

        assert ticker.has_been_init_data is True

    def test_get_all_data(self):
        """Test get_all_data."""
        ticker = GmxRequestTickerData({}, symbol_name="BTC-USD", asset_type="PERP", has_been_json_encoded=True)
        result = ticker.get_all_data()

        assert result == {}

    def test_str_representation(self):
        """Test __str__ method - base class raises NotImplementedError."""
        ticker = GmxRequestTickerData({}, symbol_name="BTC-USD", asset_type="PERP", has_been_json_encoded=True)
        with pytest.raises(NotImplementedError):
            str(ticker)
