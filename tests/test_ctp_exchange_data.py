"""Tests for CTP exchange data module."""

import pytest

from bt_api_py.containers.exchanges.ctp_exchange_data import CtpExchangeData


class TestCtpExchangeData:
    """Tests for CtpExchangeData class."""

    def test_init(self):
        """Test initialization."""
        exchange = CtpExchangeData()

        assert exchange.exchange_name == "CTP"
        assert exchange.rest_url == ""
        assert exchange.wss_url == ""

    def test_kline_periods(self):
        """Test kline_periods attribute."""
        exchange = CtpExchangeData()

        assert "1m" in exchange.kline_periods
        assert "5m" in exchange.kline_periods
        assert "1h" in exchange.kline_periods
        assert "1d" in exchange.kline_periods

    def test_reverse_kline_periods(self):
        """Test reverse_kline_periods attribute."""
        exchange = CtpExchangeData()

        # Check that reverse_kline_periods is a dict
        assert isinstance(exchange.reverse_kline_periods, dict)
        # Check that it has the expected keys from kline_periods
        assert len(exchange.reverse_kline_periods) > 0

    def test_exchange_data_inheritance(self):
        """Test that CtpExchangeData inherits from ExchangeData."""
        exchange = CtpExchangeData()

        assert hasattr(exchange, "exchange_name")
        assert hasattr(exchange, "md_front")
        assert hasattr(exchange, "td_front")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
