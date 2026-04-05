"""Tests for BybitTickerData container."""

from bt_api_py.containers.tickers.bybit_ticker import BybitTickerData


class TestBybitTickerData:
    """Tests for BybitTickerData."""

    def test_init(self):
        """Test initialization."""
        ticker = BybitTickerData({}, symbol_name="BTCUSDT", asset_type="SPOT")

        assert ticker.exchange_name == "BYBIT"
        assert ticker.symbol_name == "BTCUSDT"
        assert ticker.asset_type == "SPOT"
        assert ticker.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with ticker info."""
        data = {
            "result": {
                "list": [
                    {
                        "symbol": "BTCUSDT",
                        "lastPrice": "50000.0",
                        "bid1Price": "49990.0",
                        "ask1Price": "50010.0",
                        "highPrice24h": "51000.0",
                        "lowPrice24h": "49000.0",
                    }
                ]
            }
        }
        ticker = BybitTickerData(
            data, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True
        )
        ticker.init_data()

        assert ticker.ticker_symbol_name == "BTCUSDT"
        assert ticker.last_price == 50000.0

    def test_get_all_data(self):
        """Test get_all_data."""
        ticker = BybitTickerData(
            {}, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True
        )
        result = ticker.get_all_data()

        assert result["exchange_name"] == "BYBIT"
        assert result["symbol_name"] == "BTCUSDT"

    def test_str_representation(self):
        """Test __str__ method."""
        ticker = BybitTickerData(
            {}, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True
        )
        result = str(ticker)

        assert "Bybit" in result
