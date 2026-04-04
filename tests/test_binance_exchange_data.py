"""Tests for Binance exchange data module."""

import pytest

from bt_api_py.containers.exchanges.binance_exchange_data import BinanceExchangeData


class TestBinanceExchangeData:
    """Tests for BinanceExchangeData class."""

    def test_init(self):
        """Test initialization."""
        exchange = BinanceExchangeData()

        assert exchange.exchange_name == "binance"
        assert exchange.rest_url == ""
        assert exchange.wss_url == ""

    def test_kline_periods(self):
        """Test kline_periods attribute."""
        exchange = BinanceExchangeData()

        assert "1m" in exchange.kline_periods
        assert "5m" in exchange.kline_periods
        assert "1h" in exchange.kline_periods
        assert "1d" in exchange.kline_periods

    def test_exchange_data_inheritance(self):
        """Test that BinanceExchangeData inherits from ExchangeData."""
        exchange = BinanceExchangeData()

        assert hasattr(exchange, "exchange_name")
        assert hasattr(exchange, "rest_url")
        assert hasattr(exchange, "wss_url")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
