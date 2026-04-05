"""Tests for KorbitTickerData container."""

from __future__ import annotations

import json

from bt_api_py.containers.tickers.korbit_ticker import (
    KorbitRequestTickerData,
    KorbitTickerData,
    KorbitWssTickerData,
)


class TestKorbitTickerData:
    """Tests for KorbitTickerData."""

    def test_init(self):
        """Test initialization."""
        ticker = KorbitTickerData({}, symbol_name="btc_krw", asset_type="SPOT")

        assert ticker.exchange_name == "KORBIT"
        assert ticker.symbol_name == "btc_krw"
        assert ticker.asset_type == "SPOT"
        assert ticker.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with ticker info."""
        data = {"last": "50000000", "bid": "49990000", "ask": "50010000"}
        ticker = KorbitTickerData(
            data, symbol_name="btc_krw", asset_type="SPOT", has_been_json_encoded=True
        )
        ticker.init_data()

        assert ticker.has_been_init_data is True

    def test_init_data_parses_fields_and_getters(self):
        data = {
            "last": "50000000",
            "bid": "49990000",
            "ask": "50010000",
            "high": "51000000",
            "low": "49000000",
            "volume": "123.45",
            "change": "100000",
            "changePercent": "0.2",
            "timestamp": 1678901234000,
        }
        ticker = KorbitTickerData(
            data, symbol_name="btc_krw", asset_type="SPOT", has_been_json_encoded=True
        )
        ticker.init_data()

        assert ticker.get_ticker_symbol_name() == "btc_krw"
        assert ticker.get_last_price() == 50000000.0
        assert ticker.get_bid_price() == 49990000.0
        assert ticker.get_ask_price() == 50010000.0
        assert ticker.get_high() == 51000000.0
        assert ticker.get_low() == 49000000.0
        assert ticker.get_volume() == 123.45
        assert ticker.get_daily_change() == 100000.0
        assert ticker.get_daily_change_percentage() == 0.2
        assert ticker.get_server_time() == 1678901234.0

    def test_get_all_data(self):
        """Test get_all_data."""
        ticker = KorbitTickerData(
            {}, symbol_name="btc_krw", asset_type="SPOT", has_been_json_encoded=True
        )
        result = ticker.get_all_data()

        assert result["exchange_name"] == "KORBIT"
        assert result["symbol_name"] == "btc_krw"

    def test_str_representation(self):
        """Test __str__ method."""
        ticker = KorbitTickerData(
            {}, symbol_name="btc_krw", asset_type="SPOT", has_been_json_encoded=True
        )
        result = str(ticker)

        assert "KORBIT" in result

    def test_request_and_wss_subclasses_parse_json_and_fallback_timestamp(self, monkeypatch):
        monkeypatch.setattr("bt_api_py.containers.tickers.korbit_ticker.time.time", lambda: 321.0)
        request = KorbitRequestTickerData(
            json.dumps({"last": "1", "bid": "0.9", "ask": "1.1"}),
            symbol_name="btc_krw",
            asset_type="SPOT",
        )
        request.init_data()

        wss = KorbitWssTickerData(
            {"last": "2", "bid": "1.9", "ask": "2.1", "timestamp": 1700000000000},
            symbol_name="btc_krw",
            asset_type="SPOT",
            has_been_json_encoded=True,
        )
        wss.init_data()

        assert request.get_server_time() == 321.0
        assert request.get_last_price() == 1.0
        assert wss.get_server_time() == 1700000000.0
        assert wss.get_last_price() == 2.0
        assert "KORBIT" in repr(wss)
