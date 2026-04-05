"""Tests for HtxRequestBarData container."""

from __future__ import annotations

from bt_api_py.containers.bars.htx_bar import HtxRequestBarData


class TestHtxRequestBarData:
    """Tests for HtxRequestBarData."""

    def test_init(self):
        """Test initialization."""
        bar = HtxRequestBarData({}, symbol_name="btcusdt", asset_type="SPOT")

        assert bar.exchange_name == "HTX"
        assert bar.symbol_name == "btcusdt"
        assert bar.asset_type == "SPOT"
        assert bar.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with bar info."""
        data = {"data": [[1688671955, 50000.0, 51000.0, 49000.0, 50500.0, 1000.0]]}
        bar = HtxRequestBarData(
            data, symbol_name="btcusdt", asset_type="SPOT", has_been_json_encoded=True
        )
        bar.init_data()

        assert bar.has_been_init_data is True

    def test_get_all_data(self):
        """Test get_all_data method."""
        bar = HtxRequestBarData(
            {}, symbol_name="btcusdt", asset_type="SPOT", has_been_json_encoded=True
        )
        result = bar.get_all_data()

        assert result["exchange_name"] == "HTX"
        assert result["symbol_name"] == "btcusdt"
