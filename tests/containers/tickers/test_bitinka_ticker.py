"""Tests for BitinkaRequestTickerData container."""

from __future__ import annotations

import pytest

from bt_api_py.containers.tickers.bitinka_ticker import BitinkaRequestTickerData


class TestBitinkaRequestTickerData:
    """Tests for BitinkaRequestTickerData."""

    def test_init(self):
        """Test initialization."""
        ticker = BitinkaRequestTickerData({}, symbol_name="BTCUSD", asset_type="SPOT")

        assert ticker.exchange_name == "BITINKA"
        assert ticker.symbol_name == "BTCUSD"
        assert ticker.asset_type == "SPOT"
        assert ticker.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with ticker info."""
        data = {"last": "50000.0", "bid": "49990.0", "ask": "50010.0"}
        ticker = BitinkaRequestTickerData(
            data, symbol_name="BTCUSD", asset_type="SPOT", has_been_json_encoded=True
        )
        ticker.init_data()

        assert ticker.has_been_init_data is True

    def test_get_all_data(self):
        """Test get_all_data - base class raises NotImplementedError."""
        ticker = BitinkaRequestTickerData(
            {}, symbol_name="BTCUSD", asset_type="SPOT", has_been_json_encoded=True
        )
        with pytest.raises(NotImplementedError):
            ticker.get_all_data()

    def test_str_representation(self):
        """Test __str__ method - base class raises NotImplementedError."""
        ticker = BitinkaRequestTickerData(
            {}, symbol_name="BTCUSD", asset_type="SPOT", has_been_json_encoded=True
        )
        with pytest.raises(NotImplementedError):
            str(ticker)
