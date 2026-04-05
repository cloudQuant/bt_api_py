"""Tests for UpbitTickerData container."""


from bt_api_py.containers.tickers.upbit_ticker import UpbitTickerData


class TestUpbitTickerData:
    """Tests for UpbitTickerData."""

    def test_init(self):
        """Test initialization."""
        ticker = UpbitTickerData({}, symbol_name="KRW-BTC", asset_type="SPOT")

        assert ticker.exchange_name == "UPBIT"
        assert ticker.symbol_name == "KRW-BTC"
        assert ticker.asset_type == "SPOT"
        assert ticker.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with ticker info."""
        data = {
            "market": "KRW-BTC",
            "trade_price": 50000000,
            "high_price": 51000000,
            "low_price": 49000000,
        }
        ticker = UpbitTickerData(
            data, symbol_name="KRW-BTC", asset_type="SPOT", has_been_json_encoded=True
        )
        ticker.init_data()

        assert ticker.last_price == 50000000

    def test_get_all_data(self):
        """Test get_all_data."""
        ticker = UpbitTickerData(
            {}, symbol_name="KRW-BTC", asset_type="SPOT", has_been_json_encoded=True
        )
        result = ticker.get_all_data()

        assert result["exchange_name"] == "UPBIT"
        assert result["symbol_name"] == "KRW-BTC"

    def test_str_representation(self):
        """Test __str__ method."""
        ticker = UpbitTickerData(
            {}, symbol_name="KRW-BTC", asset_type="SPOT", has_been_json_encoded=True
        )
        result = str(ticker)

        assert "Upbit" in result
