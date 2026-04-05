"""Tests for HyperliquidTickerData container."""


from bt_api_py.containers.tickers.hyperliquid_ticker import HyperliquidTickerData


class TestHyperliquidTickerData:
    """Tests for HyperliquidTickerData."""

    def test_init(self):
        """Test initialization."""
        ticker = HyperliquidTickerData({}, symbol_name="BTC", asset_type="SWAP")

        assert ticker.exchange_name == "HYPERLIQUID"
        assert ticker.symbol_name == "BTC"
        assert ticker.asset_type == "SWAP"
        assert ticker.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with ticker info."""
        data = {"BTC": "50000.0"}
        ticker = HyperliquidTickerData(
            data, symbol_name="BTC", asset_type="SWAP", has_been_json_encoded=True
        )
        ticker.init_data()

        assert ticker.last_price == 50000.0

    def test_get_all_data(self):
        """Test get_all_data."""
        ticker = HyperliquidTickerData(
            {}, symbol_name="BTC", asset_type="SWAP", has_been_json_encoded=True
        )
        result = ticker.get_all_data()

        assert result["exchange_name"] == "HYPERLIQUID"
        assert result["symbol_name"] == "BTC"

    def test_str_representation(self):
        """Test __str__ method."""
        ticker = HyperliquidTickerData(
            {}, symbol_name="BTC", asset_type="SWAP", has_been_json_encoded=True
        )
        result = str(ticker)

        assert "Hyperliquid" in result
