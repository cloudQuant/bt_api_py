"""Tests for exchange_data module."""

from __future__ import annotations

import pytest

from bt_api_py.containers.exchanges.exchange_data import ExchangeData


class TestExchangeData:
    """Tests for ExchangeData class."""

    def test_init(self):
        """Test initialization."""
        exchange = ExchangeData()

        assert exchange.rate_limit_type == ""
        assert exchange.interval == ""
        assert exchange.interval_num == 0
        assert exchange.limit == 0
        assert exchange.server_time == 0.0
        assert exchange.local_update_time == 0.0
        assert exchange.timezone == ""
        assert exchange.rate_limits == []
        assert exchange.exchange_filters == []
        assert exchange.symbols == []
        assert exchange.exchange_name == ""

    def test_init_with_config(self):
        """Test initialization with config."""
        config = {
            "exchange_name": "BINANCE",
            "rest_url": "https://api.binance.com",
            "wss_url": "wss://stream.binance.com",
        }
        exchange = ExchangeData(config)

        assert exchange.exchange_name == ""
        # Note: ExchangeData doesn't use config in __init__

    def test_get_wss_url(self):
        """Test get_wss_url method."""
        exchange = ExchangeData()
        exchange.wss_url = "wss://stream.binance.com"

        assert exchange.get_wss_url() == "wss://stream.binance.com"

    def test_raise_path_error(self):
        """Test raise_path_error method."""
        exchange = ExchangeData()

        with pytest.raises(NotImplementedError, match="wbfAPI还未封装"):
            exchange.raise_path_error("test_path")

    def test_raise_timeout(self):
        """Test raise_timeout method."""
        exchange = ExchangeData()

        with pytest.raises(TimeoutError, match="rest请求超时"):
            exchange.raise_timeout(30, "test")

    def test_raise400(self):
        """Test raise400 method."""
        exchange = ExchangeData()

        with pytest.raises(RuntimeError, match="rest请求返回<400>"):
            exchange.raise400("test")

    def test_raise_proxy_error(self):
        """Test raise_proxy_error method."""
        exchange = ExchangeData()

        with pytest.raises(ConnectionError, match="网络代理错误"):
            exchange.raise_proxy_error("test")

    def test_update_info(self):
        """Test update_info static method."""
        exchange_info = {
            "exchange_name": "BINANCE",
            "server_time": 1705315800000.0,
        }
        exchange = ExchangeData.update_info(exchange_info)

        assert exchange.exchange_name == "BINANCE"
        assert exchange.server_time == 1705315800000.0

    def test_to_dict(self):
        """Test to_dict method."""
        exchange = ExchangeData()
        exchange.exchange_name = "BINANCE"
        exchange.server_time = 1705315800000.0

        result = exchange.to_dict()

        assert "exchange_name" in result
        assert result["exchange_name"] == "BINANCE"
        assert "server_time" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
