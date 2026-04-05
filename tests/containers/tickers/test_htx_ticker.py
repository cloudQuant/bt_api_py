"""Tests for HtxRequestTickerData container."""

from bt_api_py.containers.tickers.htx_ticker import HtxRequestTickerData


class TestHtxRequestTickerData:
    """Tests for HtxRequestTickerData."""

    def test_init(self):
        """Test initialization."""
        ticker = HtxRequestTickerData({}, symbol_name="btcusdt", asset_type="SPOT")

        assert ticker.exchange_name == "HTX"
        assert ticker.symbol_name == "btcusdt"
        assert ticker.asset_type == "SPOT"
        assert ticker.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with ticker info."""
        data = {"tick": {"close": 50000, "open": 49500, "high": 51000, "low": 49000}}
        ticker = HtxRequestTickerData(
            data, symbol_name="btcusdt", asset_type="SPOT", has_been_json_encoded=True
        )
        ticker.init_data()

        assert ticker.has_been_init_data is True

    def test_get_all_data(self):
        """Test get_all_data."""
        ticker = HtxRequestTickerData(
            {}, symbol_name="btcusdt", asset_type="SPOT", has_been_json_encoded=True
        )
        result = ticker.get_all_data()

        assert result["exchange_name"] == "HTX"
        assert result["symbol_name"] == "btcusdt"

    def test_str_representation(self):
        """Test __str__ method."""
        ticker = HtxRequestTickerData(
            {}, symbol_name="btcusdt", asset_type="SPOT", has_been_json_encoded=True
        )
        result = str(ticker)

        assert "HTX" in result
