"""Tests for GateioTickerData container."""


from bt_api_py.containers.tickers.gateio_ticker import GateioTickerData


class TestGateioTickerData:
    """Tests for GateioTickerData."""

    def test_init(self):
        """Test initialization."""
        ticker = GateioTickerData({}, symbol_name="BTC_USDT", asset_type="SPOT")

        assert ticker.exchange_name == "GATEIO"
        assert ticker.symbol_name == "BTC_USDT"
        assert ticker.asset_type == "SPOT"
        assert ticker.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with ticker info."""
        data = {
            "currency_pair": "BTC_USDT",
            "last": "50000.0",
            "highest_bid": "49990.0",
            "lowest_ask": "50010.0",
            "high_24h": "51000.0",
            "low_24h": "49000.0",
        }
        ticker = GateioTickerData(
            data, symbol_name="BTC_USDT", asset_type="SPOT", has_been_json_encoded=True
        )
        ticker.init_data()

        assert ticker.ticker_symbol_name == "BTC_USDT"
        assert ticker.last_price == 50000.0

    def test_get_all_data(self):
        """Test get_all_data."""
        ticker = GateioTickerData(
            {}, symbol_name="BTC_USDT", asset_type="SPOT", has_been_json_encoded=True
        )
        result = ticker.get_all_data()

        assert result["exchange_name"] == "GATEIO"
        assert result["symbol_name"] == "BTC_USDT"

    def test_str_representation(self):
        """Test __str__ method."""
        ticker = GateioTickerData(
            {}, symbol_name="BTC_USDT", asset_type="SPOT", has_been_json_encoded=True
        )
        result = str(ticker)

        assert "GATEIO" in result
