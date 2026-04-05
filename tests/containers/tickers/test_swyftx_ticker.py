"""Tests for SwyftxRequestTickerData container."""

import pytest

from bt_api_py.containers.tickers.swyftx_ticker import SwyftxRequestTickerData


class TestSwyftxRequestTickerData:
    """Tests for SwyftxRequestTickerData."""

    def test_init(self):
        """Test initialization."""
        ticker = SwyftxRequestTickerData({}, symbol_name="BTCAUD", asset_type="SPOT")

        assert ticker.exchange_name == "SWYFTX"
        assert ticker.symbol_name == "BTCAUD"
        assert ticker.asset_type == "SPOT"
        assert ticker.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with ticker info."""
        data = {"last": "50000.0", "bid": "49990.0", "ask": "50010.0"}
        ticker = SwyftxRequestTickerData(
            data, symbol_name="BTCAUD", asset_type="SPOT", has_been_json_encoded=True
        )
        ticker.init_data()

        assert ticker.has_been_init_data is True

    def test_get_all_data(self):
        """Test get_all_data - base class raises NotImplementedError."""
        ticker = SwyftxRequestTickerData(
            {}, symbol_name="BTCAUD", asset_type="SPOT", has_been_json_encoded=True
        )
        with pytest.raises(NotImplementedError):
            ticker.get_all_data()

    def test_str_representation(self):
        """Test __str__ method - base class raises NotImplementedError."""
        ticker = SwyftxRequestTickerData(
            {}, symbol_name="BTCAUD", asset_type="SPOT", has_been_json_encoded=True
        )
        with pytest.raises(NotImplementedError):
            str(ticker)
