"""Tests for BitflyerRequestTickerData container."""

import pytest

from bt_api_py.containers.tickers.bitflyer_ticker import BitflyerRequestTickerData


class TestBitflyerRequestTickerData:
    """Tests for BitflyerRequestTickerData."""

    def test_init(self):
        """Test initialization."""
        ticker = BitflyerRequestTickerData({}, symbol_name="BTC_JPY", asset_type="SPOT")

        assert ticker.exchange_name == "BITFLYER"
        assert ticker.symbol_name == "BTC_JPY"
        assert ticker.asset_type == "SPOT"
        assert ticker.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with ticker info."""
        data = {"ltp": 5000000, "best_bid": 4999000, "best_ask": 5001000}
        ticker = BitflyerRequestTickerData(data, symbol_name="BTC_JPY", asset_type="SPOT", has_been_json_encoded=True)
        ticker.init_data()

        assert ticker.has_been_init_data is True

    def test_get_all_data(self):
        """Test get_all_data - base class raises NotImplementedError."""
        ticker = BitflyerRequestTickerData({}, symbol_name="BTC_JPY", asset_type="SPOT", has_been_json_encoded=True)
        with pytest.raises(NotImplementedError):
            ticker.get_all_data()

    def test_str_representation(self):
        """Test __str__ method - base class raises NotImplementedError."""
        ticker = BitflyerRequestTickerData({}, symbol_name="BTC_JPY", asset_type="SPOT", has_been_json_encoded=True)
        with pytest.raises(NotImplementedError):
            str(ticker)
