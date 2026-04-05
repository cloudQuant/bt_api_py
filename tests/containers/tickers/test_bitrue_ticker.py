"""Tests for BitrueRequestTickerData container."""

from bt_api_py.containers.tickers.bitrue_ticker import BitrueRequestTickerData


class TestBitrueRequestTickerData:
    """Tests for BitrueRequestTickerData."""

    def test_init(self):
        """Test initialization."""
        ticker = BitrueRequestTickerData({}, symbol_name="BTCUSDT", asset_type="SPOT")

        assert ticker.exchange_name == "BITRUE"
        assert ticker.symbol_name == "BTCUSDT"
        assert ticker.asset_type == "SPOT"
        assert ticker.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with ticker info."""
        data = {"lastPrice": "50000.0", "bidPrice": "49990.0", "askPrice": "50010.0"}
        ticker = BitrueRequestTickerData(
            data, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True
        )
        ticker.init_data()

        assert ticker.has_been_init_data is True

    def test_get_all_data(self):
        """Test get_all_data."""
        ticker = BitrueRequestTickerData(
            {}, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True
        )
        result = ticker.get_all_data()

        assert result["exchange_name"] == "BITRUE"
        assert result["symbol_name"] == "BTCUSDT"

    def test_str_representation(self):
        """Test __str__ method."""
        ticker = BitrueRequestTickerData(
            {}, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True
        )
        result = str(ticker)

        assert "BITRUE" in result
