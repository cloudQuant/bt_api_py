"""Tests for BigONEExchangeData container."""

from __future__ import annotations

import pytest

from bt_api_py.containers.exchanges.bigone_exchange_data import (
    BigONEExchangeData,
    BigONEExchangeDataSpot,
    BigONESpotExchangeData,
)


class TestBigONEExchangeData:
    """Tests for BigONEExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = BigONEExchangeData()

        assert exchange.exchange_name == "BIGONE___SPOT"
        assert "big.one" in exchange.rest_url
        assert "big.one" in exchange.wss_url
        assert exchange.rest_paths == {}
        assert exchange.wss_paths == {}

    def test_kline_periods(self):
        """Test kline periods mapping."""
        exchange = BigONEExchangeData()

        assert exchange.get_period("1m") == "min1"
        assert exchange.get_period("5m") == "min5"
        assert exchange.get_period("1h") == "hour1"
        assert exchange.get_period("1d") == "day1"
        assert exchange.get_period("unknown") == "unknown"

    def test_get_symbol(self):
        """Test symbol conversion."""
        exchange = BigONEExchangeData()

        assert exchange.get_symbol("BTC/USDT") == "BTC-USDT"
        assert exchange.get_symbol("btc_usdt") == "BTC-USDT"
        assert exchange.get_symbol("BTCUSDT") == "BTCUSDT"
        assert exchange.get_symbol("btc-usdt") == "BTC-USDT"

    def test_legal_currency(self):
        """Test legal currency list."""
        exchange = BigONEExchangeData()

        assert "USDT" in exchange.legal_currency
        assert "BTC" in exchange.legal_currency
        assert "ETH" in exchange.legal_currency


class TestBigONEExchangeDataSpot:
    """Tests for BigONEExchangeDataSpot."""

    def test_init(self):
        """Test spot exchange initialization."""
        exchange = BigONEExchangeDataSpot()

        assert exchange.exchange_name == "BIGONE___SPOT"
        assert exchange.asset_type == "spot"
        assert exchange.rest_paths["get_server_time"] == "GET /ping"
        assert exchange.rest_paths["get_tick"] == "GET /asset_pairs/{symbol}/ticker"
        assert exchange.rest_paths["make_order"] == "POST /viewer/orders"

    def test_get_rest_path(self):
        """Test getting REST path."""
        exchange = BigONEExchangeDataSpot()

        assert exchange.get_rest_path("get_tick") == "GET /asset_pairs/{symbol}/ticker"
        assert exchange.get_rest_path("get_depth") == "GET /asset_pairs/{symbol}/depth"

    def test_get_rest_path_unknown_raises(self):
        """Test that unknown REST path raises ValueError."""
        exchange = BigONEExchangeDataSpot()

        with pytest.raises(ValueError, match="Unknown rest path"):
            exchange.get_rest_path("unknown_path")

    def test_get_wss_path(self):
        """Test getting WSS path with symbol substitution."""
        exchange = BigONEExchangeDataSpot()
        exchange.wss_paths = {"ticker": "/ticker/{symbol}"}

        result = exchange.get_wss_path("ticker", symbol="BTC/USDT")
        assert result == "/ticker/BTC-USDT"

    def test_get_wss_path_no_symbol(self):
        """Test getting WSS path without symbol."""
        exchange = BigONEExchangeDataSpot()
        exchange.wss_paths = {"depth": "/depth"}

        result = exchange.get_wss_path("depth")
        assert result == "/depth"

    def test_backward_compatibility_alias(self):
        """Test that BigONESpotExchangeData is an alias."""
        assert BigONESpotExchangeData is BigONEExchangeDataSpot
