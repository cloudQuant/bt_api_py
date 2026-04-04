"""Tests for CryptoComTicker container."""

import pytest

from bt_api_py.containers.tickers.cryptocom_ticker import CryptoComTicker


class TestCryptoComTicker:
    """Tests for CryptoComTicker."""

    def test_init(self):
        """Test initialization."""
        ticker = CryptoComTicker({}, symbol_name="BTC-USDT", asset_type="SPOT")

        assert ticker.exchange_name == "CRYPTOCOM"
        assert ticker.symbol_name == "BTC-USDT"
        assert ticker.asset_type == "SPOT"
        assert ticker.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with ticker info."""
        data = {"a": "50000.0", "b": "49990.0", "k": "50010.0", "h": "51000.0", "l": "49000.0", "v": "1000.0"}
        ticker = CryptoComTicker(data, symbol_name="BTC-USDT", asset_type="SPOT", has_been_json_encoded=True)
        ticker.init_data()

        assert ticker.last_price == 50000.0

    def test_get_all_data(self):
        """Test get_all_data."""
        ticker = CryptoComTicker({}, symbol_name="BTC-USDT", asset_type="SPOT", has_been_json_encoded=True)
        result = ticker.get_all_data()

        assert result["exchange_name"] == "CRYPTOCOM"
        assert result["symbol_name"] == "BTC-USDT"

    def test_str_representation(self):
        """Test __str__ method."""
        ticker = CryptoComTicker({}, symbol_name="BTC-USDT", asset_type="SPOT", has_been_json_encoded=True)
        result = str(ticker)

        assert "CRYPTOCOM" in result
