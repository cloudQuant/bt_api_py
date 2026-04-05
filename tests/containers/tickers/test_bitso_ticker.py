"""Tests for BitsoRequestTickerData container."""

from __future__ import annotations

from bt_api_py.containers.tickers.bitso_ticker import BitsoRequestTickerData


class TestBitsoRequestTickerData:
    """Tests for BitsoRequestTickerData."""

    def test_init(self):
        """Test initialization."""
        ticker = BitsoRequestTickerData({}, symbol_name="btc_mxn", asset_type="SPOT")

        assert ticker.exchange_name == "BITSO"
        assert ticker.symbol_name == "btc_mxn"
        assert ticker.asset_type == "SPOT"
        assert ticker.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with ticker info."""
        data = {"last": "50000.0", "bid": "49990.0", "ask": "50010.0"}
        ticker = BitsoRequestTickerData(
            data, symbol_name="btc_mxn", asset_type="SPOT", has_been_json_encoded=True
        )
        ticker.init_data()

        assert ticker.has_been_init_data is True

    def test_get_all_data(self):
        """Test get_all_data."""
        ticker = BitsoRequestTickerData(
            {}, symbol_name="btc_mxn", asset_type="SPOT", has_been_json_encoded=True
        )
        # Set attributes that would normally be set by init_data with payload
        ticker.last_price = None
        ticker.bid_price = None
        ticker.ask_price = None
        ticker.volume_24h = None
        ticker.high_24h = None
        ticker.low_24h = None
        result = ticker.get_all_data()

        assert result["exchange_name"] == "BITSO"
        assert result["symbol_name"] == "btc_mxn"

    def test_str_representation(self):
        """Test __str__ method."""
        ticker = BitsoRequestTickerData(
            {}, symbol_name="btc_mxn", asset_type="SPOT", has_been_json_encoded=True
        )
        # Set attributes that would normally be set by init_data with payload
        ticker.last_price = None
        ticker.bid_price = None
        ticker.ask_price = None
        ticker.volume_24h = None
        ticker.high_24h = None
        ticker.low_24h = None
        result = str(ticker)

        assert "BITSO" in result
