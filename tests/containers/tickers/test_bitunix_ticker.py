"""Tests for BitunixRequestTickerData container."""

import pytest

from bt_api_py.containers.tickers.bitunix_ticker import BitunixRequestTickerData


class TestBitunixRequestTickerData:
    """Tests for BitunixRequestTickerData."""

    def test_init(self):
        """Test initialization."""
        ticker = BitunixRequestTickerData({}, symbol_name="BTCUSDT", asset_type="SPOT")

        assert ticker.exchange_name == "BITUNIX"
        assert ticker.symbol_name == "BTCUSDT"
        assert ticker.asset_type == "SPOT"
        assert ticker.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with ticker info."""
        data = {"lastPrice": "50000.0", "bidPrice": "49990.0", "askPrice": "50010.0"}
        ticker = BitunixRequestTickerData(data, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True)
        ticker.init_data()

        assert ticker.has_been_init_data is True

    def test_get_all_data(self):
        """Test get_all_data - base class raises NotImplementedError."""
        ticker = BitunixRequestTickerData({}, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True)
        with pytest.raises(NotImplementedError):
            ticker.get_all_data()

    def test_str_representation(self):
        """Test __str__ method - base class raises NotImplementedError."""
        ticker = BitunixRequestTickerData({}, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True)
        with pytest.raises(NotImplementedError):
            str(ticker)
