"""Tests for KuCoinTickerData container."""

import json

import pytest

from bt_api_py.containers.tickers.kucoin_ticker import (
    KuCoinRequestTickerData,
    KuCoinStatsTickerData,
    KuCoinTickerData,
    KuCoinWssTickerData,
)


class TestKuCoinTickerData:
    """Tests for KuCoinTickerData."""

    def test_init(self):
        """Test initialization."""
        ticker = KuCoinTickerData({}, symbol_name="BTC-USDT", asset_type="SPOT")

        assert ticker.exchange_name == "KUCOIN"
        assert ticker.symbol_name == "BTC-USDT"
        assert ticker.asset_type == "SPOT"
        assert ticker.has_been_init_data is False

    def test_init_data_raises_not_implemented(self):
        """Test init_data raises NotImplementedError."""
        ticker = KuCoinTickerData({}, symbol_name="BTC-USDT", asset_type="SPOT")

        with pytest.raises(NotImplementedError):
            ticker.init_data()

    def test_get_all_data(self):
        """Test get_all_data."""
        ticker = KuCoinTickerData(
            {}, symbol_name="BTC-USDT", asset_type="SPOT", has_been_json_encoded=True
        )
        # Set _initialized to prevent AutoInitMixin from calling init_data
        ticker._initialized = True
        result = ticker.get_all_data()

        assert result["exchange_name"] == "KUCOIN"
        assert result["symbol_name"] == "BTC-USDT"

    def test_str_representation(self):
        """Test __str__ method - skip since init_data raises NotImplementedError."""
        # KuCoinTickerData.__str__ calls init_data() which raises NotImplementedError
        # This is expected behavior for abstract base class
        ticker = KuCoinTickerData(
            {}, symbol_name="BTC-USDT", asset_type="SPOT", has_been_json_encoded=True
        )
        with pytest.raises(NotImplementedError):
            str(ticker)

    def test_request_and_wss_subclasses_parse_payloads(self):
        request = KuCoinRequestTickerData(
            json.dumps(
                {
                    "code": "200000",
                    "data": {
                        "time": 1688671955000,
                        "price": "50000",
                        "size": "0.001",
                        "bestBid": "49999",
                        "bestBidSize": "1.5",
                        "bestAsk": "50001",
                        "bestAskSize": "2.3",
                    },
                }
            ),
            symbol_name="BTC-USDT",
            asset_type="SPOT",
        )
        request.init_data()

        wss = KuCoinWssTickerData(
            {
                "data": {
                    "time": 1688671956000,
                    "price": "50010",
                    "size": "0.002",
                    "bestBid": "50009",
                    "bestBidSize": "1.7",
                    "bestAsk": "50011",
                    "bestAskSize": "2.5",
                }
            },
            symbol_name="BTC-USDT",
            asset_type="SPOT",
            has_been_json_encoded=True,
        )
        wss.init_data()

        assert request.get_ticker_symbol_name() == "BTC-USDT"
        assert request.get_server_time() == 1688671955000.0
        assert request.get_last_price() == 50000.0
        assert request.get_last_volume() == 0.001
        assert request.get_bid_price() == 49999.0
        assert request.get_bid_volume() == 1.5
        assert request.get_ask_price() == 50001.0
        assert request.get_ask_volume() == 2.3
        assert wss.get_last_price() == 50010.0
        assert wss.get_last_volume() == 0.002
        assert wss.get_bid_price() == 50009.0
        assert wss.get_ask_price() == 50011.0
        assert "KUCOIN" in repr(wss)

    def test_stats_subclass_parses_statistics_payload(self):
        stats = KuCoinStatsTickerData(
            json.dumps(
                {
                    "code": "200000",
                    "data": {
                        "time": 1688671955000,
                        "symbol": "BTC-USDT",
                        "buy": "50000",
                        "sell": "50001",
                        "vol": "1234.56789",
                        "last": "50000",
                    },
                }
            ),
            symbol_name="BTC-USDT",
            asset_type="SPOT",
        )
        stats.init_data()

        assert stats.get_ticker_symbol_name() == "BTC-USDT"
        assert stats.get_server_time() == 1688671955000.0
        assert stats.get_last_price() == 50000.0
        assert stats.get_last_volume() == 1234.56789
        assert stats.get_bid_price() == 50000.0
        assert stats.get_ask_price() == 50001.0
        assert stats.get_bid_volume() is None
        assert stats.get_ask_volume() is None
