"""Tests for MercadoBitcoinRequestTickerData container."""

import pytest

from bt_api_py.containers.tickers.mercado_bitcoin_ticker import MercadoBitcoinRequestTickerData


class TestMercadoBitcoinRequestTickerData:
    """Tests for MercadoBitcoinRequestTickerData."""

    def test_init(self):
        """Test initialization."""
        ticker = MercadoBitcoinRequestTickerData({}, symbol_name="BTCBRL", asset_type="SPOT")

        assert ticker.exchange_name == "MERCADO_BITCOIN"
        assert ticker.symbol_name == "BTCBRL"
        assert ticker.asset_type == "SPOT"
        assert ticker.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with ticker info."""
        data = {"last": "500000.0", "buy": "499900.0", "sell": "500100.0"}
        ticker = MercadoBitcoinRequestTickerData(data, symbol_name="BTCBRL", asset_type="SPOT", has_been_json_encoded=True)
        ticker.init_data()

        assert ticker.has_been_init_data is True

    def test_get_all_data(self):
        """Test get_all_data - base class raises NotImplementedError."""
        ticker = MercadoBitcoinRequestTickerData({}, symbol_name="BTCBRL", asset_type="SPOT", has_been_json_encoded=True)
        with pytest.raises(NotImplementedError):
            ticker.get_all_data()

    def test_str_representation(self):
        """Test __str__ method - base class raises NotImplementedError."""
        ticker = MercadoBitcoinRequestTickerData({}, symbol_name="BTCBRL", asset_type="SPOT", has_been_json_encoded=True)
        with pytest.raises(NotImplementedError):
            str(ticker)
