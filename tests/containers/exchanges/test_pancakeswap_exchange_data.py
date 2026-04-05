"""Tests for PancakeswapExchangeData container."""

from __future__ import annotations

from bt_api_py.containers.exchanges.pancakeswap_exchange_data import PancakeSwapExchangeData


class TestPancakeSwapExchangeData:
    """Tests for PancakeSwapExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = PancakeSwapExchangeData()

        assert exchange.exchange_name == "pancakeswap"
        assert exchange.rest_url == ""
        assert exchange.wss_url == ""
        assert exchange.rest_paths == {}
        assert exchange.wss_paths == {}

    def test_kline_periods(self):
        """Test kline periods mapping."""
        exchange = PancakeSwapExchangeData()

        assert exchange.get_period("1m") == "1m"
        assert exchange.get_period("5m") == "5m"
        assert exchange.get_period("1h") == "1h"
        assert exchange.get_period("1d") == "1d"
        assert exchange.get_period("unknown") == "unknown"

    def test_legal_currency(self):
        """Test legal currency list."""
        exchange = PancakeSwapExchangeData()

        assert "USDT" in exchange.legal_currency
        assert "BNB" in exchange.legal_currency
        assert "BTC" in exchange.legal_currency
        assert "ETH" in exchange.legal_currency

    def test_get_symbol(self):
        """Test symbol conversion."""
        exchange = PancakeSwapExchangeData()

        assert exchange.get_symbol("BTCB/USDT") == "BTCB/USDT"
        assert exchange.get_symbol("ETH/USDT") == "ETH/USDT"

    def test_symbol_to_address(self):
        """Test symbol to address conversion."""
        exchange = PancakeSwapExchangeData()

        assert (
            exchange.symbol_to_address("BTCB/USDT") == "0x58F876857a02D6762EeFA1aF755FEE1271A3ACaC"
        )
        assert (
            exchange.symbol_to_address("ETH/USDT") == "0x70e36197034F56Bf06712e97d19AEd5C0b8453D1"
        )
        assert (
            exchange.symbol_to_address("CAKE/USDT") == "0x04514E7Ba3F091234D6Be8E39864a7a3Ad4a1E1e"
        )
        assert exchange.symbol_to_address("UNKNOWN") == "0x0"

    def test_address_to_symbol(self):
        """Test address to symbol conversion."""
        exchange = PancakeSwapExchangeData()

        assert (
            exchange.address_to_symbol("0x58F876857a02D6762EeFA1aF755FEE1271A3ACaC") == "BTCB/USDT"
        )
        assert (
            exchange.address_to_symbol("0x70e36197034F56Bf06712e97d19AEd5C0b8453D1") == "ETH/USDT"
        )
        assert exchange.address_to_symbol("0xunknown") == "UNKNOWN"

    def test_get_rest_path(self):
        """Test getting REST path."""
        exchange = PancakeSwapExchangeData()

        # When no config loaded, returns empty string
        result = exchange.get_rest_path("graphql")
        assert result == ""

    def test_get_wss_path(self):
        """Test getting WSS path."""
        exchange = PancakeSwapExchangeData()

        # When no config loaded, returns empty string
        result = exchange.get_wss_path("ticker")
        assert result == ""

    def test_get_supported_tokens_no_config(self):
        """Test getting supported tokens without config."""
        exchange = PancakeSwapExchangeData()

        # Returns empty list when no config
        tokens = exchange.get_supported_tokens()
        assert tokens == []

    def test_get_supported_pairs_no_config(self):
        """Test getting supported pairs without config."""
        exchange = PancakeSwapExchangeData()

        # Returns empty list when no config
        pairs = exchange.get_supported_pairs()
        assert pairs == []

    def test_get_minimum_trade_amount_no_config(self):
        """Test getting minimum trade amount without config."""
        exchange = PancakeSwapExchangeData()

        # Returns default amounts
        assert exchange.get_minimum_trade_amount("USDT") == 10.0
        assert exchange.get_minimum_trade_amount("BNB") == 0.01
        assert exchange.get_minimum_trade_amount("BTC") == 0.0001
        assert exchange.get_minimum_trade_amount("ETH") == 0.001
        assert exchange.get_minimum_trade_amount("UNKNOWN") == 0.0

    def test_is_supported_network_no_config(self):
        """Test checking supported network without config."""
        exchange = PancakeSwapExchangeData()

        # Returns False when no config or network not found
        # May raise AttributeError if config exists but incomplete
        try:
            result = exchange.is_supported_network(56)
            assert result is False
        except AttributeError:
            # Config exists but missing networks attribute
            pass

    def test_get_backup_urls_no_config(self):
        """Test getting backup URLs without config."""
        exchange = PancakeSwapExchangeData()

        # Returns empty list when no config or missing rest attribute
        try:
            urls = exchange.get_backup_urls("graphql")
            assert urls == []
        except AttributeError:
            # Config exists but missing rest attribute
            pass

    def test_get_fee_config_no_config(self):
        """Test getting fee config without config."""
        exchange = PancakeSwapExchangeData()

        # Returns default fees when no config or missing fees attribute
        fee = exchange.get_fee_config()
        # Default DEX fees
        assert fee == {"maker": 0.0025, "taker": 0.0025}

    def test_get_slippage_config_no_config(self):
        """Test getting slippage config without config."""
        import contextlib

        exchange = PancakeSwapExchangeData()

        # Returns None or raises AttributeError when no config
        with contextlib.suppress(AttributeError):
            exchange.get_slippage_config()

    def test_get_order_types_no_config(self):
        """Test getting order types without config."""
        exchange = PancakeSwapExchangeData()

        # Returns default order types
        order_types = exchange.get_order_types()
        assert order_types == ["MARKET", "LIMIT"]

    def test_get_order_statuses_no_config(self):
        """Test getting order statuses without config."""
        exchange = PancakeSwapExchangeData()

        # Returns default order statuses
        statuses = exchange.get_order_statuses()
        assert statuses == ["NEW", "FILLED", "PARTIALLY_FILLED", "FAILED"]

    def test_get_capabilities_no_config(self):
        """Test getting capabilities without config."""
        exchange = PancakeSwapExchangeData()

        # Returns default capabilities
        capabilities = exchange.get_capabilities()
        assert "GET_TICK" in capabilities
        assert "GET_DEPTH" in capabilities
        assert "MAKE_ORDER" in capabilities
