"""Tests for PhemexRequestTickerData container."""

import pytest

from bt_api_py.containers.tickers.phemex_ticker import PhemexRequestTickerData


class TestPhemexRequestTickerData:
    """Tests for PhemexRequestTickerData."""

    def test_init(self):
        """Test initialization."""
        ticker = PhemexRequestTickerData({}, symbol="BTCUSDT", asset_type="SPOT")

        assert ticker.symbol == "BTCUSDT"
        assert ticker.asset_type == "SPOT"
        assert ticker.last_price is None

    def test_init_data(self):
        """Test data parsing in constructor."""
        data = {"data": {"lastEp": 50000000000, "bidEp": 49990000000, "askEp": 50010000000}}
        ticker = PhemexRequestTickerData(data, symbol="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True)

        # Phemex parses data in constructor
        assert ticker.last_price is not None

    def test_get_all_data(self):
        """Test get_all_data - base class raises NotImplementedError."""
        ticker = PhemexRequestTickerData({}, symbol="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True)
        with pytest.raises(NotImplementedError):
            ticker.get_all_data()

    def test_str_representation(self):
        """Test __str__ method."""
        ticker = PhemexRequestTickerData({}, symbol="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True)
        result = str(ticker)

        assert "Phemex" in result
