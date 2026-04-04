"""Tests for BitbankRequestTickerData container."""

import pytest

from bt_api_py.containers.tickers.bitbank_ticker import BitbankRequestTickerData


class TestBitbankRequestTickerData:
    """Tests for BitbankRequestTickerData."""

    def test_init(self):
        """Test initialization."""
        ticker = BitbankRequestTickerData({}, symbol_name="btc_jpy", asset_type="SPOT")

        assert ticker.exchange_name == "BITBANK"
        assert ticker.symbol_name == "btc_jpy"
        assert ticker.asset_type == "SPOT"
        assert ticker.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with ticker info."""
        data = {"last": "5000000", "buy": "4999000", "sell": "5001000"}
        ticker = BitbankRequestTickerData(data, symbol_name="btc_jpy", asset_type="SPOT", has_been_json_encoded=True)
        ticker.init_data()

        assert ticker.has_been_init_data is True

    def test_get_all_data(self):
        """Test get_all_data - base class raises NotImplementedError."""
        ticker = BitbankRequestTickerData({}, symbol_name="btc_jpy", asset_type="SPOT", has_been_json_encoded=True)
        with pytest.raises(NotImplementedError):
            ticker.get_all_data()

    def test_str_representation(self):
        """Test __str__ method - base class raises NotImplementedError."""
        ticker = BitbankRequestTickerData({}, symbol_name="btc_jpy", asset_type="SPOT", has_been_json_encoded=True)
        with pytest.raises(NotImplementedError):
            str(ticker)
