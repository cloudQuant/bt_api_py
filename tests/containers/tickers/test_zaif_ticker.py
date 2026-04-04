"""Tests for ZaifRequestTickerData container."""

import pytest

from bt_api_py.containers.tickers.zaif_ticker import ZaifRequestTickerData


class TestZaifRequestTickerData:
    """Tests for ZaifRequestTickerData."""

    def test_init(self):
        """Test initialization."""
        ticker = ZaifRequestTickerData({}, symbol_name="btc_jpy", asset_type="SPOT")

        assert ticker.exchange_name == "ZAIF"
        assert ticker.symbol_name == "btc_jpy"
        assert ticker.asset_type == "SPOT"
        assert ticker.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with ticker info."""
        data = {"last": "5000000.0", "bid": "4999000.0", "ask": "5001000.0"}
        ticker = ZaifRequestTickerData(data, symbol_name="btc_jpy", asset_type="SPOT", has_been_json_encoded=True)
        ticker.init_data()

        assert ticker.has_been_init_data is True

    def test_get_all_data(self):
        """Test get_all_data - base class raises NotImplementedError."""
        ticker = ZaifRequestTickerData({}, symbol_name="btc_jpy", asset_type="SPOT", has_been_json_encoded=True)
        with pytest.raises(NotImplementedError):
            ticker.get_all_data()

    def test_str_representation(self):
        """Test __str__ method - base class raises NotImplementedError."""
        ticker = ZaifRequestTickerData({}, symbol_name="btc_jpy", asset_type="SPOT", has_been_json_encoded=True)
        with pytest.raises(NotImplementedError):
            str(ticker)
