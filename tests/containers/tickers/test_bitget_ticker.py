"""Tests for BitgetTickerData container."""


from bt_api_py.containers.tickers.bitget_ticker import BitgetTickerData


class TestBitgetTickerData:
    """Tests for BitgetTickerData."""

    def test_init(self):
        """Test initialization."""
        ticker = BitgetTickerData({}, symbol_name="BTCUSDT", asset_type="SPOT")

        assert ticker.exchange_name == "BITGET"
        assert ticker.symbol_name == "BTCUSDT"
        assert ticker.asset_type == "SPOT"
        assert ticker.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with ticker info."""
        data = {
            "symbol": "BTCUSDT",
            "last": "50000.0",
            "bidPx": "49990.0",
            "askPx": "50010.0",
            "high24h": "51000.0",
            "low24h": "49000.0",
        }
        ticker = BitgetTickerData(
            data, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True
        )
        ticker.init_data()

        assert ticker.ticker_symbol_name == "BTCUSDT"
        assert ticker.last_price == 50000.0

    def test_get_all_data(self):
        """Test get_all_data."""
        ticker = BitgetTickerData(
            {}, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True
        )
        result = ticker.get_all_data()

        assert result["exchange_name"] == "BITGET"
        assert result["symbol_name"] == "BTCUSDT"

    def test_str_representation(self):
        """Test __str__ method."""
        ticker = BitgetTickerData(
            {}, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True
        )
        result = str(ticker)

        assert "BITGET" in result
