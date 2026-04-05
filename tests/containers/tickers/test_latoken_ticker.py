"""Tests for LatokenTickerData container."""

import json
import pytest

from bt_api_py.containers.tickers.latoken_ticker import (
    LatokenRequestTickerData,
    LatokenTickerData,
    LatokenWssTickerData,
)


class TestLatokenTickerData:
    """Tests for LatokenTickerData."""

    def test_init(self):
        """Test initialization."""
        ticker = LatokenTickerData({}, symbol_name="BTCUSDT", asset_type="SPOT")

        assert ticker.exchange_name == "LATOKEN"
        assert ticker.symbol_name == "BTCUSDT"
        assert ticker.asset_type == "SPOT"
        assert ticker.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with ticker info."""
        data = {"lastPrice": "50000.0", "bidPrice": "49990.0", "askPrice": "50010.0"}
        ticker = LatokenTickerData(data, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True)
        ticker.init_data()

        assert ticker.has_been_init_data is True

    def test_init_data_parses_fields_and_daily_change(self):
        data = {
            "symbol": "BTCUSDT",
            "lastPrice": "50000.0",
            "bidPrice": "49990.0",
            "askPrice": "50010.0",
            "high": "51000.0",
            "low": "49000.0",
            "volume": "123.4",
            "timestamp": 1700000000000,
        }
        ticker = LatokenTickerData(data, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True)
        ticker.init_data()

        assert ticker.get_ticker_symbol_name() == "BTCUSDT"
        assert ticker.get_last_price() == 50000.0
        assert ticker.get_bid_price() == 49990.0
        assert ticker.get_ask_price() == 50010.0
        assert ticker.get_high() == 51000.0
        assert ticker.get_low() == 49000.0
        assert ticker.get_volume() == 123.4
        assert ticker.get_daily_change() == 1000.0
        assert ticker.get_server_time() == 1700000000.0

    def test_get_all_data(self):
        """Test get_all_data."""
        ticker = LatokenTickerData({}, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True)
        result = ticker.get_all_data()

        assert result["exchange_name"] == "LATOKEN"
        assert result["symbol_name"] == "BTCUSDT"

    def test_str_representation(self):
        """Test __str__ method."""
        ticker = LatokenTickerData({}, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True)
        result = str(ticker)

        assert "LATOKEN" in result

    def test_request_and_wss_subclasses_parse_string_and_dict_payloads(self, monkeypatch):
        monkeypatch.setattr("bt_api_py.containers.tickers.latoken_ticker.time.time", lambda: 456.0)
        request = LatokenRequestTickerData(
            json.dumps({"lastPrice": "1", "bidPrice": "0.9", "askPrice": "1.1"}),
            symbol_name="BTCUSDT",
            asset_type="SPOT",
        )
        request.init_data()

        wss = LatokenWssTickerData(
            {"symbol": "BTCUSDT", "lastPrice": "2", "bidPrice": "1.9", "askPrice": "2.1"},
            symbol_name="BTCUSDT",
            asset_type="SPOT",
            has_been_json_encoded=True,
        )
        wss.init_data()

        assert request.get_server_time() == 456.0
        assert request.get_last_price() == 1.0
        assert wss.get_ticker_symbol_name() == "BTCUSDT"
        assert wss.get_last_price() == 2.0
        assert "LATOKEN" in repr(wss)
