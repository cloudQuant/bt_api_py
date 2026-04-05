"""Tests for OKX exchange data module."""

from __future__ import annotations

import pytest

from bt_api_py.containers.exchanges.okx_exchange_data import OkxExchangeData


class TestOkxExchangeData:
    """Tests for OkxExchangeData class."""

    def test_init(self):
        """Test initialization."""
        exchange = OkxExchangeData()

        assert exchange.exchange_name == "OkxSwap"
        assert exchange.rest_url == "https://www.okx.com"
        assert exchange.wss_url == "wss://ws.okx.com:8443/ws/v5/public"

    def test_symbol_leverage_dict(self):
        """Test symbol_leverage_dict attribute."""
        exchange = OkxExchangeData()

        assert "BTC-USDT" in exchange.symbol_leverage_dict
        assert "ETH-USDT" in exchange.symbol_leverage_dict

    def test_exchange_data_inheritance(self):
        """Test that OkxExchangeData inherits from ExchangeData."""
        exchange = OkxExchangeData()

        assert hasattr(exchange, "exchange_name")
        assert hasattr(exchange, "rest_url")
        assert hasattr(exchange, "wss_url")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
