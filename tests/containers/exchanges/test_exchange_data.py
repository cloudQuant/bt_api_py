"""Tests for ExchangeData base class."""

import pytest

from bt_api_py.containers.exchanges.exchange_data import ExchangeData


class TestExchangeData:
    """Tests for ExchangeData base class."""

    def test_init_default_values(self):
        """Test initialization with default values."""
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
        assert exchange.rest_url == ""
        assert exchange.acct_wss_url == ""
        assert exchange.wss_url == ""
        assert exchange.um_rest_url == ""
        assert exchange.um_wss_Url == ""
        assert exchange.rest_paths == {}
        assert exchange.wss_paths == {}
        assert exchange.kline_periods == {}
        assert exchange.reverse_kline_periods == {}
        assert exchange.status_dict == {}
        assert exchange.legal_currency == []
        assert exchange.api_key == ""
        assert exchange.api_secret == ""
        assert exchange.passphrase == ""

    def test_init_with_config(self):
        """Test initialization with config dict."""
        config = {
            "exchange_name": "test_exchange",
            "rest_url": "https://api.test.com",
            "wss_url": "wss://ws.test.com",
        }
        exchange = ExchangeData(config)

        # Config is not used in __init__, values remain default
        assert exchange.exchange_name == ""

    def test_get_wss_url(self):
        """Test get_wss_url method."""
        exchange = ExchangeData()
        exchange.wss_url = "wss://stream.test.com"

        assert exchange.get_wss_url() == "wss://stream.test.com"

    def test_raise_path_error(self):
        """Test raise_path_error method."""
        exchange = ExchangeData()

        with pytest.raises(NotImplementedError) as exc_info:
            exchange.raise_path_error("binance", "depth")

        assert "wbfAPI还未封装" in str(exc_info.value)
        assert "binance" in str(exc_info.value)
        assert "depth" in str(exc_info.value)

    def test_raise_timeout(self):
        """Test raise_timeout method."""
        exchange = ExchangeData()

        with pytest.raises(TimeoutError) as exc_info:
            exchange.raise_timeout(30, "test_endpoint")

        assert "rest请求超时" in str(exc_info.value)
        assert "30" in str(exc_info.value)

    def test_raise400(self):
        """Test raise400 method."""
        exchange = ExchangeData()

        with pytest.raises(RuntimeError) as exc_info:
            exchange.raise400("test_endpoint")

        assert "400" in str(exc_info.value)

    def test_raise_proxy_error(self):
        """Test raise_proxy_error method."""
        exchange = ExchangeData()

        with pytest.raises(ConnectionError) as exc_info:
            exchange.raise_proxy_error("test_proxy")

        assert "网络代理错误" in str(exc_info.value)

    def test_update_info_static_method(self):
        """Test update_info static method."""
        exchange_info = {
            "exchange_name": "binance",
            "rest_url": "https://api.binance.com",
            "server_time": 1700000000000.0,
            "timezone": "UTC",
        }

        exchange = ExchangeData.update_info(exchange_info)

        assert exchange.exchange_name == "binance"
        assert exchange.rest_url == "https://api.binance.com"
        assert exchange.server_time == 1700000000000.0
        assert exchange.timezone == "UTC"

    def test_to_dict(self):
        """Test to_dict method."""
        exchange = ExchangeData()
        exchange.exchange_name = "test"
        exchange.rest_url = "https://test.com"

        result = exchange.to_dict()

        assert isinstance(result, dict)
        assert result["exchange_name"] == "test"
        assert result["rest_url"] == "https://test.com"
        # Verify no private methods in output
        assert "__init__" not in result
        assert "update_info" not in result
        assert "to_dict" not in result

    def test_to_dict_excludes_methods(self):
        """Test to_dict excludes update_* and to_dict methods."""
        exchange = ExchangeData()
        exchange.exchange_name = "test_exchange"

        result = exchange.to_dict()

        # Methods should not be in the dict
        for key in result:
            assert not key.startswith("__")
            assert not key.startswith("update")
            assert not key.startswith("to_dict")

    def test_update_info_accepts_custom_fields(self):
        exchange = ExchangeData.update_info({"custom_field": "value", "limit": 1200})

        assert exchange.custom_field == "value"
        assert exchange.limit == 1200

    def test_to_dict_includes_runtime_mutable_fields(self):
        exchange = ExchangeData()
        exchange.rest_paths = {"get_tick": "/ticker"}
        exchange.wss_paths = {"ticker": "stream"}

        result = exchange.to_dict()

        assert result["rest_paths"] == {"get_tick": "/ticker"}
        assert result["wss_paths"] == {"ticker": "stream"}
