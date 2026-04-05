"""Tests for LocalBitcoinsTickerData container."""

import json

from bt_api_py.containers.tickers.localbitcoins_ticker import (
    LocalBitcoinsRequestTickerData,
    LocalBitcoinsTickerData,
    LocalBitcoinsWssTickerData,
)


class TestLocalBitcoinsTickerData:
    """Tests for LocalBitcoinsTickerData."""

    def test_init(self):
        """Test initialization."""
        ticker = LocalBitcoinsTickerData({}, symbol_name="BTC-USD", asset_type="SPOT")

        assert ticker.exchange_name == "LOCALBITCOINS"
        assert ticker.symbol_name == "BTC-USD"
        assert ticker.asset_type == "SPOT"
        assert ticker.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with ticker info."""
        data = {"avg_1h": "50000.0", "avg_6h": "49990.0"}
        ticker = LocalBitcoinsTickerData(
            data, symbol_name="BTC-USD", asset_type="SPOT", has_been_json_encoded=True
        )
        ticker.init_data()

        assert ticker.has_been_init_data is True

    def test_init_data_parses_nested_symbol_payload_and_getters(self, monkeypatch):
        monkeypatch.setattr(
            "bt_api_py.containers.tickers.localbitcoins_ticker.time.time", lambda: 789.0
        )
        data = {
            "btc_usd": {
                "avg": 50000.5,
                "bid": 49900.0,
                "ask": 50100.0,
                "volume_btc": 12.34,
            }
        }
        ticker = LocalBitcoinsTickerData(
            data,
            symbol_name="BTC-USD",
            asset_type="SPOT",
            has_been_json_encoded=True,
        )
        ticker.init_data()

        assert ticker.get_ticker_symbol_name() == "BTC-USD"
        assert ticker.get_last_price() == 50000.5
        assert ticker.get_bid_price() == 49900.0
        assert ticker.get_ask_price() == 50100.0
        assert ticker.get_volume() == 12.34
        assert ticker.get_high() is None
        assert ticker.get_low() is None
        assert ticker.get_server_time() == 789.0

    def test_get_all_data(self):
        """Test get_all_data."""
        ticker = LocalBitcoinsTickerData(
            {}, symbol_name="BTC-USD", asset_type="SPOT", has_been_json_encoded=True
        )
        result = ticker.get_all_data()

        assert result["exchange_name"] == "LOCALBITCOINS"
        assert result["symbol_name"] == "BTC-USD"

    def test_str_representation(self):
        """Test __str__ method."""
        ticker = LocalBitcoinsTickerData(
            {}, symbol_name="BTC-USD", asset_type="SPOT", has_been_json_encoded=True
        )
        result = str(ticker)

        assert "LOCALBITCOINS" in result

    def test_request_and_wss_subclasses_parse_string_and_dict_payloads(self, monkeypatch):
        monkeypatch.setattr(
            "bt_api_py.containers.tickers.localbitcoins_ticker.time.time", lambda: 654.0
        )
        request = LocalBitcoinsRequestTickerData(
            json.dumps({"btc_usd": {"avg": 1.0, "bid": 0.9, "ask": 1.1, "volume_btc": 2.5}}),
            symbol_name="BTC-USD",
            asset_type="SPOT",
        )
        request.init_data()

        wss = LocalBitcoinsWssTickerData(
            {"btc_usd": {"avg": 2.0, "bid": 1.9, "ask": 2.1, "volume_btc": 3.5}},
            symbol_name="BTC-USD",
            asset_type="SPOT",
            has_been_json_encoded=True,
        )
        wss.init_data()

        assert request.get_server_time() == 654.0
        assert request.get_last_price() == 1.0
        assert wss.get_last_price() == 2.0
        assert wss.get_volume() == 3.5
        assert "LOCALBITCOINS" in repr(wss)
