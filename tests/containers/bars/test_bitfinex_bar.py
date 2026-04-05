"""Tests for BitfinexRequestBarData container."""

from __future__ import annotations

from bt_api_py.containers.bars.bitfinex_bar import BitfinexRequestBarData


class TestBitfinexRequestBarData:
    """Tests for BitfinexRequestBarData."""

    def test_init(self):
        """Test initialization."""
        bar = BitfinexRequestBarData({}, symbol_name="BTCUSD", asset_type="SPOT")

        assert bar.exchange_name == "BITFINEX"
        assert bar.symbol_name == "BTCUSD"
        assert bar.asset_type == "SPOT"
        assert bar.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with bar info."""
        data = [
            {"open": 50000.0, "high": 51000.0, "low": 49000.0, "close": 50500.0, "volume": 1000.0}
        ]
        bar = BitfinexRequestBarData(
            data, symbol_name="BTCUSD", asset_type="SPOT", has_been_json_encoded=True
        )
        bar.init_data()

        assert bar.has_been_init_data is True

    def test_get_all_data(self):
        """Test get_all_data method."""
        bar = BitfinexRequestBarData(
            {}, symbol_name="BTCUSD", asset_type="SPOT", has_been_json_encoded=True
        )
        result = bar.get_all_data()

        assert result["exchange_name"] == "BITFINEX"
        assert result["symbol_name"] == "BTCUSD"
