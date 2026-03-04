"""
Test Uniswap exchange integration.

Run tests:
    pytest tests/feeds/test_uniswap.py -v

Run with coverage:
    pytest tests/feeds/test_uniswap.py --cov=bt_api_py.feeds.live_uniswap --cov-report=term-missing
"""

import queue

import pytest

from bt_api_py.containers.exchanges.uniswap_exchange_data import (
    UniswapChain,
    UniswapExchangeData,
    UniswapExchangeDataSpot,
)
from bt_api_py.feeds.live_uniswap.spot import UniswapRequestDataSpot
from bt_api_py.registry import ExchangeRegistry

# Import registration to auto-register Uniswap
import bt_api_py.exchange_registers.register_uniswap  # noqa: F401


class TestUniswapExchangeData:
    """Test Uniswap exchange data configuration."""

    def test_exchange_data_spot_creation(self):
        """Test creating Uniswap spot exchange data."""
        exchange_data = UniswapExchangeDataSpot()
        assert exchange_data.chain == UniswapChain.ETHEREUM
        assert exchange_data.rest_url

    def test_exchange_data_spot_with_chain(self):
        """Test creating Uniswap spot exchange data with specific chain."""
        exchange_data = UniswapExchangeDataSpot(chain=UniswapChain.ARBITRUM)
        assert exchange_data.chain == UniswapChain.ARBITRUM

    def test_get_rest_url(self):
        """Test getting REST URL."""
        exchange_data = UniswapExchangeDataSpot()
        rest_url = exchange_data.get_rest_url()
        assert rest_url
        assert "uniswap" in rest_url.lower()

    def test_get_subgraph_url(self):
        """Test getting subgraph URL."""
        exchange_data = UniswapExchangeDataSpot()
        subgraph_url = exchange_data.get_subgraph_url()
        assert subgraph_url
        assert "thegraph" in subgraph_url

    def test_get_symbol(self):
        """Test symbol format conversion."""
        exchange_data = UniswapExchangeDataSpot()
        # Token address should be returned as-is
        token_address = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
        assert exchange_data.get_symbol(token_address) == token_address

    def test_get_chain_value(self):
        """Test getting chain enum value."""
        exchange_data = UniswapExchangeDataSpot(chain=UniswapChain.OPTIMISM)
        assert exchange_data.get_chain_value() == "OPTIMISM"

    def test_supported_chains(self):
        """Test supported chains."""
        assert UniswapChain.ETHEREUM in UniswapExchangeData.SUBGRAPH_URLS
        assert UniswapChain.ARBITRUM in UniswapExchangeData.SUBGRAPH_URLS
        assert UniswapChain.OPTIMISM in UniswapExchangeData.SUBGRAPH_URLS
        assert UniswapChain.POLYGON in UniswapExchangeData.SUBGRAPH_URLS

    def test_get_rest_path(self):
        """Test getting REST path."""
        exchange_data = UniswapExchangeDataSpot()
        path = exchange_data.get_rest_path("get_tick")
        assert path


class TestUniswapRequestDataSpot:
    """Test Uniswap spot REST API request class."""

    def test_request_data_creation(self):
        """Test creating Uniswap request data."""
        data_queue = queue.Queue()
        request_data = UniswapRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
            exchange_name="UNISWAP___DEX",
        )
        assert request_data.exchange_name == "UNISWAP___DEX"
        assert request_data.chain == UniswapChain.ETHEREUM

    def test_request_data_with_chain(self):
        """Test creating Uniswap request data with specific chain."""
        data_queue = queue.Queue()
        request_data = UniswapRequestDataSpot(
            data_queue,
            chain=UniswapChain.ARBITRUM,
            exchange_name="UNISWAP___DEX",
        )
        assert request_data.chain == UniswapChain.ARBITRUM

    def test_get_tick_params(self):
        """Test get tick parameter generation."""
        data_queue = queue.Queue()
        request_data = UniswapRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
        )

        result = request_data._get_tick("0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2")
        assert result is not None

    def test_get_pool_params(self):
        """Test get pool parameter generation."""
        data_queue = queue.Queue()
        request_data = UniswapRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
        )

        pool_id = "0x8ad599c3A0ff1De082011EFDDc58f1908eb6e6D8"
        result = request_data._get_pool(pool_id)
        assert result is not None

    def test_get_swap_quote_params(self):
        """Test get swap quote parameter generation."""
        data_queue = queue.Queue()
        request_data = UniswapRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
        )

        token_in = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
        token_out = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
        result = request_data._get_swap_quote(token_in, token_out, "1")
        assert result is not None

    def test_get_swappable_tokens_params(self):
        """Test get swappable tokens parameter generation."""
        data_queue = queue.Queue()
        request_data = UniswapRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
        )

        result = request_data._get_swappable_tokens()
        assert result is not None


class TestUniswapDataContainers:
    """Test Uniswap data containers.

    Note: Uniswap uses GraphQL and has different data structures.
    This section tests the request/response normalization.
    """

    def test_get_tick_normalize_function(self):
        """Test ticker normalization function."""
        data_queue = queue.Queue()
        request_data = UniswapRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
        )

        input_data = {
            "data": {
            "token": {
                "id": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
                "symbol": "WETH",
                "name": "Wrapped Ether",
                "decimals": 18,
                "price": {"USD": "3000"},
            }
            }
        }

        extra_data = {"symbol_name": "WETH"}
        result, status = UniswapRequestDataSpot._get_tick_normalize_function(
            input_data, extra_data
        )

        assert status is True
        assert result is not None

    def test_get_pool_normalize_function(self):
        """Test pool normalization function."""
        input_data = {
            "data": {
            "pool": {
                "id": "0x8ad599c3A0ff1De082011EFDDc58f1908eb6e6D8",
                "token0": {"symbol": "USDC"},
                "token1": {"symbol": "WETH"},
                "totalValueLockedUSD": "1000000",
            }
            }
        }

        extra_data = {"pool_id": "test_pool"}
        result, status = UniswapRequestDataSpot._get_pool_normalize_function(
            input_data, extra_data
        )

        assert status is True
        assert result is not None


class TestUniswapRegistry:
    """Test Uniswap registration."""

    def test_uniswap_registered(self):
        """Test that Uniswap is properly registered."""
        assert "UNISWAP___DEX" in ExchangeRegistry._feed_classes
        assert "UNISWAP___DEX" in ExchangeRegistry._exchange_data_classes

    def test_uniswap_create_feed(self):
        """Test creating Uniswap feed through registry."""
        data_queue = queue.Queue()
        feed = ExchangeRegistry.create_feed(
            "UNISWAP___DEX",
            data_queue,
            public_key="test",
            private_key="test",
        )
        assert isinstance(feed, UniswapRequestDataSpot)

    def test_uniswap_create_exchange_data(self):
        """Test creating Uniswap exchange data through registry."""
        exchange_data = ExchangeRegistry.create_exchange_data("UNISWAP___DEX")
        assert isinstance(exchange_data, UniswapExchangeDataSpot)


class TestUniswapIntegration:
    """Integration tests for Uniswap (marked as integration)."""

    @pytest.mark.integration
    def test_get_token_price_live(self):
        """Test getting token price from live API."""
        pass

    @pytest.mark.integration
    def test_get_pool_info_live(self):
        """Test getting pool info from live API."""
        pass

    @pytest.mark.integration
    def test_get_swap_quote_live(self):
        """Test getting swap quote from live API."""
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
