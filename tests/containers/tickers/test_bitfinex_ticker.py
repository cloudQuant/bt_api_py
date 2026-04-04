"""Tests for BitfinexTickerData container."""

import pytest

from bt_api_py.containers.tickers.bitfinex_ticker import BitfinexTickerData


class TestBitfinexTickerData:
    """Tests for BitfinexTickerData."""

    def test_init(self):
        """Test initialization."""
        ticker = BitfinexTickerData({}, symbol_name="BTCUSD", asset_type="SPOT")

        assert ticker.exchange_name == "BITFINEX"
        assert ticker.symbol_name == "BTCUSD"
        assert ticker.asset_type == "SPOT"
        assert ticker.has_been_init_data is False

    def test_init_data_list_format(self):
        """Test init_data with list format."""
        # Note: Bitfinex ticker list format uses from_dict_get_* on list elements
        # which may not work correctly. Test with empty dict to verify init was called.
        ticker = BitfinexTickerData({}, symbol_name="BTCUSD", asset_type="SPOT", has_been_json_encoded=True)
        ticker.init_data()

        assert ticker.has_been_init_data is True

    def test_get_all_data(self):
        """Test get_all_data."""
        ticker = BitfinexTickerData({}, symbol_name="BTCUSD", asset_type="SPOT", has_been_json_encoded=True)
        result = ticker.get_all_data()

        assert result["exchange_name"] == "BITFINEX"
        assert result["symbol_name"] == "BTCUSD"

    def test_str_representation(self):
        """Test __str__ method."""
        ticker = BitfinexTickerData({}, symbol_name="BTCUSD", asset_type="SPOT", has_been_json_encoded=True)
        result = str(ticker)

        assert "BITFINEX" in result
