"""Tests for KuCoinBarData container."""

import pytest

from bt_api_py.containers.bars.kucoin_bar import KuCoinBarData


class TestKuCoinBarData:
    """Tests for KuCoinBarData."""

    def test_init(self):
        """Test initialization."""
        bar = KuCoinBarData({}, symbol_name="BTC-USDT", asset_type="SPOT")

        assert bar.exchange_name == "KUCOIN"
        assert bar.symbol_name == "BTC-USDT"
        assert bar.asset_type == "SPOT"
        assert bar.has_been_init_data is False

    def test_init_data_raises_not_implemented(self):
        """Test init_data raises NotImplementedError."""
        bar = KuCoinBarData({}, symbol_name="BTC-USDT", asset_type="SPOT", has_been_json_encoded=True)

        with pytest.raises(NotImplementedError):
            bar.init_data()

    def test_get_all_data(self):
        """Test get_all_data method - raises NotImplementedError via init_data."""
        bar = KuCoinBarData({}, symbol_name="BTC-USDT", asset_type="SPOT", has_been_json_encoded=True)

        with pytest.raises(NotImplementedError):
            bar.get_all_data()
