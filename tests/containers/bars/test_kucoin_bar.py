"""Tests for KuCoinBarData container."""

import pytest

from bt_api_py.containers.bars.kucoin_bar import KuCoinBarData, KuCoinRequestBarData, KuCoinWssBarData


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


class TestKuCoinRequestBarData:
    def test_parse_direct_array(self):
        bar = KuCoinRequestBarData(
            ["1688671800", "50000", "50200", "50500", "49500", "1000.12345678", "50100000"],
            symbol_name="BTC-USDT",
            asset_type="SPOT",
            has_been_json_encoded=True,
        )
        bar.init_data()

        assert bar.get_exchange_name() == "KUCOIN"
        assert bar.get_symbol_name() == "BTC-USDT"
        assert bar.get_server_time() == 1688671800000.0
        assert bar.get_open_price() == 50000.0
        assert bar.get_close_price() == 50200.0
        assert bar.get_high_price() == 50500.0
        assert bar.get_low_price() == 49500.0
        assert bar.get_volume() == 1000.12345678
        assert bar.get_turnover() == 50100000.0

    def test_parse_wrapped_response(self):
        bar = KuCoinRequestBarData(
            {
                "data": [
                    ["1688671800", "50000", "50200", "50500", "49500", "1000.12345678", "50100000"]
                ]
            },
            symbol_name="BTC-USDT",
            asset_type="SPOT",
            has_been_json_encoded=True,
        )
        result = bar.get_all_data()

        assert result["bar_symbol_name"] == "BTC-USDT"
        assert result["server_time"] == 1688671800000.0


class TestKuCoinWssBarData:
    def test_parse_wss_payload(self):
        bar = KuCoinWssBarData(
            {
                "data": {
                    "symbol": "BTC-USDT",
                    "candles": ["1688671800", "50000", "50200", "50500", "49500", "1000", "50100000"],
                }
            },
            symbol_name="BTC-USDT",
            asset_type="SPOT",
            has_been_json_encoded=True,
        )
        bar.init_data()

        assert bar.get_bar_symbol_name() == "BTC-USDT"
        assert bar.get_server_time() == 1688671800000.0
        assert bar.get_open_price() == 50000.0
        assert bar.get_close_price() == 50200.0
