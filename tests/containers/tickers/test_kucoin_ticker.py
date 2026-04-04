"""Tests for KuCoinTickerData container."""

import pytest

from bt_api_py.containers.tickers.kucoin_ticker import KuCoinTickerData


class TestKuCoinTickerData:
    """Tests for KuCoinTickerData."""

    def test_init(self):
        """Test initialization."""
        ticker = KuCoinTickerData({}, symbol_name="BTC-USDT", asset_type="SPOT")

        assert ticker.exchange_name == "KUCOIN"
        assert ticker.symbol_name == "BTC-USDT"
        assert ticker.asset_type == "SPOT"
        assert ticker.has_been_init_data is False

    def test_init_data_raises_not_implemented(self):
        """Test init_data raises NotImplementedError."""
        ticker = KuCoinTickerData({}, symbol_name="BTC-USDT", asset_type="SPOT")

        with pytest.raises(NotImplementedError):
            ticker.init_data()

    def test_get_all_data(self):
        """Test get_all_data."""
        ticker = KuCoinTickerData({}, symbol_name="BTC-USDT", asset_type="SPOT", has_been_json_encoded=True)
        # Set _initialized to prevent AutoInitMixin from calling init_data
        ticker._initialized = True
        result = ticker.get_all_data()

        assert result["exchange_name"] == "KUCOIN"
        assert result["symbol_name"] == "BTC-USDT"

    def test_str_representation(self):
        """Test __str__ method - skip since init_data raises NotImplementedError."""
        # KuCoinTickerData.__str__ calls init_data() which raises NotImplementedError
        # This is expected behavior for abstract base class
        ticker = KuCoinTickerData({}, symbol_name="BTC-USDT", asset_type="SPOT", has_been_json_encoded=True)
        with pytest.raises(NotImplementedError):
            str(ticker)
