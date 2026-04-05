"""Tests for BitstampRequestTickerData container."""

from __future__ import annotations

from bt_api_py.containers.tickers.bitstamp_ticker import BitstampRequestTickerData


class TestBitstampRequestTickerData:
    """Tests for BitstampRequestTickerData."""

    def test_init(self):
        """Test initialization."""
        ticker = BitstampRequestTickerData({}, symbol_name="btcusd", asset_type="SPOT")

        assert ticker.exchange_name == "BITSTAMP"
        assert ticker.symbol_name == "btcusd"
        assert ticker.asset_type == "SPOT"
        assert ticker.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with ticker info."""
        data = {"last": "50000.0", "bid": "49990.0", "ask": "50010.0"}
        ticker = BitstampRequestTickerData(
            data, symbol_name="btcusd", asset_type="SPOT", has_been_json_encoded=True
        )
        ticker.init_data()

        assert ticker.last_price == 50000.0

    def test_get_all_data(self):
        """Test get_all_data."""
        ticker = BitstampRequestTickerData(
            {}, symbol_name="btcusd", asset_type="SPOT", has_been_json_encoded=True
        )
        result = ticker.get_all_data()

        assert result["exchange_name"] == "BITSTAMP"
        assert result["symbol_name"] == "btcusd"

    def test_str_representation(self):
        """Test __str__ method."""
        ticker = BitstampRequestTickerData(
            {}, symbol_name="btcusd", asset_type="SPOT", has_been_json_encoded=True
        )
        result = str(ticker)

        assert "BITSTAMP" in result
