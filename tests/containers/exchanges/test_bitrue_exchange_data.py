"""Tests for BitrueExchangeData container."""

from __future__ import annotations

import pytest

from bt_api_py.containers.exchanges.bitrue_exchange_data import (
    BitrueExchangeData,
    BitrueExchangeDataSpot,
    BitrueSpotExchangeData,
)


class TestBitrueExchangeData:
    """Tests for BitrueExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = BitrueExchangeData()

        assert exchange.exchange_name == "BITRUE___SPOT"
        assert "bitrue.com" in exchange.rest_url
        assert "bitrue.com" in exchange.wss_url
        assert exchange.rest_paths == {}
        assert exchange.wss_paths == {}

    def test_kline_periods(self):
        """Test kline periods mapping."""
        exchange = BitrueExchangeData()

        assert exchange.get_period("1m") == "1m"
        assert exchange.get_period("5m") == "5m"
        assert exchange.get_period("1h") == "1h"
        assert exchange.get_period("1d") == "1d"
        assert exchange.get_period("unknown") == "unknown"

    def test_get_symbol(self):
        """Test symbol conversion."""
        exchange = BitrueExchangeData()

        assert exchange.get_symbol("BTC/USDT") == "BTCUSDT"
        assert exchange.get_symbol("btc-usdt") == "BTCUSDT"
        assert exchange.get_symbol("BTC_USDT") == "BTCUSDT"
        assert exchange.get_symbol("btcusdt") == "BTCUSDT"

    def test_legal_currency(self):
        """Test legal currency list."""
        exchange = BitrueExchangeData()

        assert "USDT" in exchange.legal_currency
        assert "BTC" in exchange.legal_currency
        assert "XRP" in exchange.legal_currency


class TestBitrueExchangeDataSpot:
    """Tests for BitrueExchangeDataSpot."""

    def test_init(self):
        """Test spot exchange initialization."""
        exchange = BitrueExchangeDataSpot()

        assert exchange.exchange_name == "BITRUE___SPOT"
        assert exchange.asset_type == "SPOT"
        assert exchange.rest_paths["ping"] == "GET /api/v1/ping"
        assert exchange.rest_paths["get_tick"] == "GET /api/v1/ticker/24hr"
        assert exchange.rest_paths["make_order"] == "POST /api/v1/order"

    def test_get_rest_path(self):
        """Test getting REST path."""
        exchange = BitrueExchangeDataSpot()

        assert exchange.get_rest_path("get_tick") == "GET /api/v1/ticker/24hr"
        assert exchange.get_rest_path("get_depth") == "GET /api/v1/depth"

    def test_get_rest_path_unknown_raises(self):
        """Test that unknown REST path raises ValueError."""
        exchange = BitrueExchangeDataSpot()

        with pytest.raises(ValueError, match="Unknown rest path"):
            exchange.get_rest_path("unknown_path")

    def test_get_wss_path(self):
        """Test getting WSS path with symbol substitution."""
        exchange = BitrueExchangeDataSpot()
        exchange.wss_paths = {"ticker": "/ticker/{symbol}"}

        result = exchange.get_wss_path("ticker", symbol="BTC/USDT")
        assert result == "/ticker/btcusdt"

    def test_get_wss_path_no_symbol(self):
        """Test getting WSS path without symbol."""
        exchange = BitrueExchangeDataSpot()
        exchange.wss_paths = {"depth": "/depth"}

        result = exchange.get_wss_path("depth")
        assert result == "/depth"

    def test_backward_compatibility_alias(self):
        """Test that BitrueSpotExchangeData is an alias."""
        assert BitrueSpotExchangeData is BitrueExchangeDataSpot
