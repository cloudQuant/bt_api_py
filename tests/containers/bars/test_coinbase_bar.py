"""Tests for CoinbaseBarData container."""

import pytest

from bt_api_py.containers.bars.coinbase_bar import CoinbaseBarData, CoinbaseRequestBarData


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
        bar = CoinbaseBarData(
            {}, symbol_name="BTC-USD", asset_type="SPOT", has_been_json_encoded=True
        )

        with pytest.raises(NotImplementedError):
            bar.init_data()

    def test_get_all_data(self):
        """Test get_all_data method - raises NotImplementedError via init_data."""
        bar = CoinbaseBarData(
            {}, symbol_name="BTC-USD", asset_type="SPOT", has_been_json_encoded=True
        )

        with pytest.raises(NotImplementedError):
            bar.get_all_data()


class TestCoinbaseRequestBarData:
    def test_parse_dict_payload(self):
        bar = CoinbaseRequestBarData(
            {
                "start": "1688671800",
                "open": "50000",
                "high": "50500",
                "low": "49500",
                "close": "50200",
                "volume": "1000",
            },
            symbol_name="BTC-USD",
            asset_type="SPOT",
            has_been_json_encoded=True,
        )
        bar.init_data()

        assert bar.get_exchange_name() == "COINBASE"
        assert bar.get_symbol_name() == "BTC-USD"
        assert bar.get_open_price() == 50000.0
        assert bar.get_high_price() == 50500.0
        assert bar.get_low_price() == 49500.0
        assert bar.get_close_price() == 50200.0
        assert bar.get_volume() == 1000.0

    def test_parse_list_payload(self):
        bar = CoinbaseRequestBarData(
            [1688671800, 50000, 50500, 49500, 50200, 1000],
            symbol_name="BTC-USD",
            asset_type="SPOT",
            has_been_json_encoded=True,
        )
        result = bar.get_all_data()

        assert result["server_time"] == 1688671800.0
        assert result["open"] == 50000.0
        assert result["close"] == 50200.0

    def test_str_representation_contains_exchange(self):
        bar = CoinbaseRequestBarData(
            {
                "start": "1688671800",
                "open": "50000",
                "high": "50500",
                "low": "49500",
                "close": "50200",
                "volume": "1000",
            },
            symbol_name="BTC-USD",
            asset_type="SPOT",
            has_been_json_encoded=True,
        )

        assert "COINBASE" in str(bar)
