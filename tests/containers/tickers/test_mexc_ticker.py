"""Tests for MexcTickerData container."""

import json

from bt_api_py.containers.tickers.mexc_ticker import (
    MexcRequestTickerData,
    MexcTickerData,
    MexcWssTickerData,
)


class TestMexcTickerData:
    """Tests for MexcTickerData."""

    def test_init(self):
        """Test initialization."""
        ticker = MexcTickerData({}, symbol_name="BTCUSDT", asset_type="SPOT")

        assert ticker.exchange_name == "MEXC"
        assert ticker.symbol_name == "BTCUSDT"
        assert ticker.asset_type == "SPOT"
        assert ticker.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with ticker info."""
        data = {
            "symbol": "BTCUSDT",
            "lastPrice": "50000.0",
            "bidPrice": "49990.0",
            "askPrice": "50010.0",
        }
        ticker = MexcTickerData(data, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True)
        ticker.init_data()

        assert ticker.ticker_symbol_name == "BTCUSDT"
        assert ticker.last_price == 50000.0

    def test_init_data_parses_fields_and_getters(self):
        data = {
            "symbol": "BTCUSDT",
            "serverTime": 1700000000000,
            "lastPrice": "50000.0",
            "lastQty": "0.25",
            "bidPrice": "49990.0",
            "bidQty": "1.5",
            "askPrice": "50010.0",
            "askQty": "2.5",
        }
        ticker = MexcTickerData(data, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True)

        assert ticker.get_ticker_symbol_name() == "BTCUSDT"
        assert ticker.get_server_time() == 1700000000000
        assert ticker.get_last_price() == 50000.0
        assert ticker.get_last_volume() == 0.25
        assert ticker.get_bid_price() == 49990.0
        assert ticker.get_bid_volume() == 1.5
        assert ticker.get_ask_price() == 50010.0
        assert ticker.get_ask_volume() == 2.5

    def test_get_all_data(self):
        """Test get_all_data."""
        ticker = MexcTickerData({}, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True)
        result = ticker.get_all_data()

        assert result["exchange_name"] == "MEXC"
        assert result["symbol_name"] == "BTCUSDT"

    def test_str_representation(self):
        """Test __str__ method."""
        ticker = MexcTickerData({}, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True)
        result = str(ticker)

        assert "MEXC" in result

    def test_request_and_wss_subclasses_parse_payloads(self):
        request = MexcRequestTickerData(
            json.dumps(
                {
                    "symbol": "BTCUSDT",
                    "closeTime": 111,
                    "lastPrice": "1.0",
                    "lastQty": "0.1",
                    "bidPrice": "0.9",
                    "bidQty": "2.0",
                    "askPrice": "1.1",
                    "askQty": "3.0",
                }
            ),
            symbol_name="BTCUSDT",
            asset_type="SPOT",
        )
        request.init_data()

        wss = MexcWssTickerData(
            {"ts": 222, "data": {"symbol": "BTCUSDT", "buy": "1.9", "sell": "2.1", "last": "2.0"}},
            symbol_name="BTCUSDT",
            asset_type="SPOT",
            has_been_json_encoded=True,
        )
        wss.init_data()

        assert request.get_server_time() == 111
        assert request.get_last_price() == 1.0
        assert request.get_last_volume() == 0.1
        assert wss.get_server_time() == 222
        assert wss.get_last_price() == 2.0
        assert wss.get_bid_price() == 1.9
        assert wss.get_ask_price() == 2.1
        assert "MEXC" in repr(wss)
