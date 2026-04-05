"""Tests for CoinSwitchExchangeData container."""

import pytest

from bt_api_py.containers.exchanges.coinswitch_exchange_data import (
    CoinSwitchExchangeData,
    CoinSwitchExchangeDataSpot,
)


class TestCoinSwitchExchangeData:
    """Tests for CoinSwitchExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = CoinSwitchExchangeData(
            {
                "exchange_name": "COINSWITCH___SPOT",
                "rest_url": "https://api.coinswitch.co",
                "wss_url": "wss://ws.coinswitch.co",
                "kline_periods": {"1m": "1"},
                "rest_paths": {"get_tick": "/ticker"},
                "wss_paths": {"ticker": "ticker"},
                "legal_currency": ["INR"],
                "symbols": {"BTCINR": {}},
            }
        )

        assert exchange.exchange_name == "COINSWITCH___SPOT"

    def test_symbol_period_and_rest_path_helpers(self):
        exchange = CoinSwitchExchangeData(
            {
                "exchange_name": "COINSWITCH___SPOT",
                "rest_paths": {"get_tick": "/ticker"},
                "kline_periods": {"1m": "1"},
            }
        )

        assert exchange.get_symbol("BTCINR") == "BTCINR"
        assert exchange.get_period("1m") == "1"
        assert exchange.get_period("5m") == "5m"
        assert exchange.get_rest_path("get_tick") == "/ticker"

    def test_get_rest_path_raises_when_missing(self):
        exchange = CoinSwitchExchangeData({"exchange_name": "COINSWITCH___SPOT", "rest_paths": {}})
        with pytest.raises(ValueError):
            exchange.get_rest_path("missing")

    def test_init_raises_when_config_missing(self, monkeypatch):
        monkeypatch.setattr(
            "bt_api_py.containers.exchanges.coinswitch_exchange_data._get_coinswitch_config",
            lambda: None,
        )
        with pytest.raises(ValueError):
            CoinSwitchExchangeData()


class TestCoinSwitchExchangeDataSpot:
    def test_spot_subclass_sets_asset_type(self):
        exchange = CoinSwitchExchangeDataSpot({"exchange_name": "COINSWITCH___SPOT"})
        assert exchange.asset_type == "SPOT"
