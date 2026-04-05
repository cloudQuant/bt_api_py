"""Tests for CoinExRequestTickerData container."""


from bt_api_py.containers.tickers.coinex_ticker import CoinExRequestTickerData


class TestCoinExRequestTickerData:
    """Tests for CoinExRequestTickerData."""

    def test_init(self):
        """Test initialization."""
        ticker = CoinExRequestTickerData({}, symbol_name="BTCUSDT", asset_type="SPOT")

        assert ticker.exchange_name == "COINEX"
        assert ticker.symbol_name == "BTCUSDT"
        assert ticker.asset_type == "SPOT"
        assert ticker.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with ticker info."""
        data = {"last": "50000.0", "buy": "49990.0", "sell": "50010.0"}
        ticker = CoinExRequestTickerData(
            data, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True
        )
        ticker.init_data()

        assert ticker.has_been_init_data is True

    def test_get_all_data(self):
        """Test get_all_data."""
        ticker = CoinExRequestTickerData(
            {}, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True
        )
        result = ticker.get_all_data()

        assert result["exchange_name"] == "COINEX"
        assert result["symbol_name"] == "BTCUSDT"

    def test_str_representation(self):
        """Test __str__ method."""
        ticker = CoinExRequestTickerData(
            {}, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True
        )
        result = str(ticker)

        assert "COINEX" in result
