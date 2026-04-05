"""Tests for BitmartExchangeData container."""

import pytest

from bt_api_py.containers.exchanges.bitmart_exchange_data import (
    BitmartExchangeData,
    BitmartExchangeDataSpot,
)


class TestBitmartExchangeData:
    """Tests for BitmartExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = BitmartExchangeData()

        assert exchange.exchange_name == "BITMART___SPOT"

    def test_symbol_and_period_mapping(self):
        exchange = BitmartExchangeData()

        assert exchange.get_symbol("btc/usdt") == "BTC_USDT"
        assert exchange.get_symbol("btc-usdt") == "BTC_USDT"
        assert exchange.get_period("1h") == "60"
        assert exchange.get_period("custom") == "custom"

    def test_get_rest_path_raises_for_unknown_key(self):
        exchange = BitmartExchangeData()
        with pytest.raises(ValueError):
            exchange.get_rest_path("unknown")

    def test_get_wss_path_substitutes_symbol(self):
        exchange = BitmartExchangeData()
        exchange.wss_paths = {"ticker": "spot/ticker:{symbol}"}

        assert exchange.get_wss_path("ticker", symbol="btc/usdt") == "spot/ticker:BTC_USDT"


class TestBitmartExchangeDataSpot:
    def test_spot_fallback_defaults(self, monkeypatch):
        monkeypatch.setattr(BitmartExchangeDataSpot, "_load_from_config", lambda self, asset_type: False)
        exchange = BitmartExchangeDataSpot()

        assert exchange.asset_type == "SPOT"
        assert exchange.exchange_name == "BITMART___SPOT"
        assert exchange.get_rest_path("get_tick") == "GET /spot/quotation/v3/ticker"
