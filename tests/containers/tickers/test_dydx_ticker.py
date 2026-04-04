"""Tests for DydxTickerData container."""

import pytest

from bt_api_py.containers.tickers.dydx_ticker import DydxTickerData


class TestDydxTickerData:
    """Tests for DydxTickerData."""

    def test_init(self):
        """Test initialization."""
        ticker = DydxTickerData({}, symbol_name="BTC-USD", asset_type="SWAP")

        assert ticker.exchange_name == "DYDX"
        assert ticker.symbol_name == "BTC-USD"
        assert ticker.asset_type == "SWAP"
        assert ticker.has_been_init_data is False

    def test_init_data_raises_not_implemented(self):
        """Test init_data raises NotImplementedError."""
        ticker = DydxTickerData({}, symbol_name="BTC-USD", asset_type="SWAP")

        with pytest.raises(NotImplementedError):
            ticker.init_data()

    def test_get_all_data(self):
        """Test get_all_data."""
        ticker = DydxTickerData({}, symbol_name="BTC-USD", asset_type="SWAP", has_been_json_encoded=True)
        # Set _initialized to prevent AutoInitMixin from calling init_data
        ticker._initialized = True
        result = ticker.get_all_data()

        assert result["exchange_name"] == "DYDX"
        assert result["symbol_name"] == "BTC-USD"

    def test_str_representation(self):
        """Test __str__ method - skip since init_data raises NotImplementedError."""
        # DydxTickerData.__str__ calls init_data() which raises NotImplementedError
        # This is expected behavior for abstract base class
        ticker = DydxTickerData({}, symbol_name="BTC-USD", asset_type="SWAP", has_been_json_encoded=True)
        with pytest.raises(NotImplementedError):
            str(ticker)
