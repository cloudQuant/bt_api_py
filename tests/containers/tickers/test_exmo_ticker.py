"""Tests for ExmoRequestTickerData container."""

from __future__ import annotations

from bt_api_py.containers.tickers.exmo_ticker import ExmoRequestTickerData


class TestExmoRequestTickerData:
    """Tests for ExmoRequestTickerData."""

    def test_init(self):
        """Test initialization."""
        ticker = ExmoRequestTickerData({}, symbol_name="BTC_USD", asset_type="SPOT")

        assert ticker.exchange_name == "EXMO"
        assert ticker.symbol_name == "BTC_USD"
        assert ticker.asset_type == "SPOT"
        assert ticker.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with ticker info."""
        data = {"last": "50000.0", "buy_price": "49990.0", "sell_price": "50010.0"}
        ticker = ExmoRequestTickerData(
            data, symbol_name="BTC_USD", asset_type="SPOT", has_been_json_encoded=True
        )
        ticker.init_data()

        assert ticker.has_been_init_data is True

    def test_get_all_data(self):
        """Test get_all_data."""
        ticker = ExmoRequestTickerData(
            {}, symbol_name="BTC_USD", asset_type="SPOT", has_been_json_encoded=True
        )
        result = ticker.get_all_data()

        assert result["exchange_name"] == "EXMO"
        assert result["symbol_name"] == "BTC_USD"

    def test_str_representation(self):
        """Test __str__ method."""
        ticker = ExmoRequestTickerData(
            {}, symbol_name="BTC_USD", asset_type="SPOT", has_been_json_encoded=True
        )
        result = str(ticker)

        assert "EXMO" in result
