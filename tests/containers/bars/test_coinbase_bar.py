"""Tests for CoinbaseBarData container."""

import pytest

from bt_api_py.containers.bars.coinbase_bar import CoinbaseBarData


class TestCoinbaseBarData:
    """Tests for CoinbaseBarData."""

    def test_init(self):
        """Test initialization."""
        bar = CoinbaseBarData({}, symbol_name="BTC-USD", asset_type="SPOT")

        assert bar.exchange_name == "COINBASE"
        assert bar.symbol_name == "BTC-USD"
        assert bar.asset_type == "SPOT"
        assert bar.has_been_init_data is False

    def test_init_data_raises_not_implemented(self):
        """Test init_data raises NotImplementedError."""
        bar = CoinbaseBarData({}, symbol_name="BTC-USD", asset_type="SPOT", has_been_json_encoded=True)

        with pytest.raises(NotImplementedError):
            bar.init_data()

    def test_get_all_data(self):
        """Test get_all_data method - raises NotImplementedError via init_data."""
        bar = CoinbaseBarData({}, symbol_name="BTC-USD", asset_type="SPOT", has_been_json_encoded=True)

        with pytest.raises(NotImplementedError):
            bar.get_all_data()
