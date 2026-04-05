"""Tests for CoinbaseTickerData container."""

from __future__ import annotations

import json

from bt_api_py.containers.tickers.coinbase_ticker import (
    CoinbaseRequestTickerData,
    CoinbaseTickerData,
    CoinbaseWssTickerData,
    parse_iso_time_to_timestamp,
)


class TestParseIsoTimeToTimestamp:
    def test_parse_valid_and_invalid_values(self):
        assert parse_iso_time_to_timestamp("2024-01-02T03:04:05Z") == 1704164645.0
        assert parse_iso_time_to_timestamp("") is None
        assert parse_iso_time_to_timestamp("bad-time") is None


class TestCoinbaseTickerData:
    """Tests for CoinbaseTickerData."""

    def test_init(self):
        """Test initialization."""
        ticker = CoinbaseTickerData({}, symbol_name="BTC-USD", asset_type="SPOT")

        assert ticker.exchange_name == "COINBASE"
        assert ticker.symbol_name == "BTC-USD"
        assert ticker.asset_type == "SPOT"
        assert ticker.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with ticker info."""
        data = {
            "product_id": "BTC-USD",
            "last_trade": "50000.00",
            "best_bid": "49990.00",
            "best_ask": "50010.00",
            "volume_24h": "1000.0",
        }
        ticker = CoinbaseTickerData(
            data, symbol_name="BTC-USD", asset_type="SPOT", has_been_json_encoded=True
        )
        ticker.init_data()

        assert ticker.last_price == 50000.0

    def test_init_data_parses_fields_and_sums_depth(self):
        data = {
            "product_id": "BTC-USD",
            "time": "2024-01-02T03:04:05Z",
            "last_trade": "50000.00",
            "best_bid": "49990.00",
            "best_ask": "50010.00",
            "volume_24h": "1000.0",
            "price_percentage_change_24h": "1.5",
            "bids": [["49990", "1.2"], ["49980", "0.8"]],
            "asks": [["50010", "2.0"], ["50020", "1.0"]],
        }
        ticker = CoinbaseTickerData(
            data, symbol_name="BTC-USD", asset_type="SPOT", has_been_json_encoded=True
        )
        ticker.init_data()

        assert ticker.get_ticker_symbol_name() == "BTC-USD"
        assert ticker.get_server_time() == 1704164645.0
        assert ticker.get_bid_price() == 49990.0
        assert ticker.get_ask_price() == 50010.0
        assert ticker.get_bid_volume() == 2.0
        assert ticker.get_ask_volume() == 3.0
        assert ticker.get_last_price() == 50000.0
        assert ticker.get_price_24h_change_percent() == 1.5
        assert ticker.get_volume_24h() == 1000.0

    def test_get_all_data(self):
        """Test get_all_data."""
        ticker = CoinbaseTickerData(
            {}, symbol_name="BTC-USD", asset_type="SPOT", has_been_json_encoded=True
        )
        result = ticker.get_all_data()

        assert result["exchange_name"] == "COINBASE"
        assert result["symbol_name"] == "BTC-USD"

    def test_str_representation(self):
        """Test __str__ method."""
        ticker = CoinbaseTickerData(
            {}, symbol_name="BTC-USD", asset_type="SPOT", has_been_json_encoded=True
        )
        result = str(ticker)

        assert "COINBASE" in result

    def test_request_and_wss_subclasses_parse_payloads(self):
        request = CoinbaseRequestTickerData(
            json.dumps(
                {
                    "product_id": "BTC-USD",
                    "time": "2024-01-02T03:04:05Z",
                    "price": "1.0",
                    "best_bid": "0.9",
                    "best_ask": "1.1",
                }
            ),
            symbol_name="BTC-USD",
            asset_type="SPOT",
        )
        request.init_data()

        wss = CoinbaseWssTickerData(
            {
                "product_id": "BTC-USD",
                "time": "2024-01-02T03:04:05Z",
                "price": "2.0",
                "volume_24h": "5.0",
            },
            symbol_name="BTC-USD",
            asset_type="SPOT",
            has_been_json_encoded=True,
        )
        wss.init_data()

        assert request.get_last_price() == 1.0
        assert request.get_bid_price() == 0.9
        assert request.get_ask_price() == 1.1
        assert wss.get_last_price() == 2.0
        assert wss.get_volume_24h() == 5.0
        assert "COINBASE" in repr(wss)
