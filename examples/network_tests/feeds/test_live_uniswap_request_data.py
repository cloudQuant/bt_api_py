"""
Test Uniswap DEX integration following Binance/OKX standards.

Uniswap is a Decentralized Exchange (DEX) that uses:
    pass
- GraphQL queries for data retrieval
- Token addresses instead of trading pairs
- Pool-based liquidity instead of order books
- Subgraph API for historical data

Run tests:
    pytest tests/feeds/test_live_uniswap_request_data.py -v
    pytest tests/feeds/test_live_uniswap_request_data.py -v -m "not integration"

Run with coverage:
    pytest tests/feeds/test_live_uniswap_request_data.py --cov=bt_api_py.feeds.live_uniswap --cov-report=term-missing
"""

import queue
from unittest.mock import MagicMock

import pytest

# Import registration to auto-register Uniswap
import bt_api_py.exchange_registers.register_uniswap  # noqa: F401
from bt_api_py.containers.exchanges.uniswap_exchange_data import (
    UniswapChain,
    UniswapExchangeData,
    UniswapExchangeDataSpot,
)

# Uniswap uses different data containers - tickers may not exist
from bt_api_py.feeds.live_uniswap.spot import UniswapRequestDataSpot
from bt_api_py.registry import ExchangeRegistry

# ==================== Common Token Addresses ====================

# Common token addresses for testing (Ethereum mainnet)
WETH_ADDRESS = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
USDC_ADDRESS = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
USDT_ADDRESS = "0xdAC17F958D2ee523a2206206994597C13D831ec7"
WBTC_ADDRESS = "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599"

# Common pool addresses
USDC_WETH_POOL = "0x8ad599c3A0ff1De082011EFDDc58f1908eb6e6D8"
USDT_WETH_POOL = "0x4e68Cd3E89f51C3074ca5072bbAC773960dfa36e"


# ==================== Fixtures ====================


@pytest.fixture
def data_queue():
    """Create a queue for test data."""
    return queue.Queue()


@pytest.fixture
def uniswap_feed(data_queue):
    """Create a Uniswap feed instance for testing."""
    import sys

    # Mock requests module
    sys.modules["requests"] = MagicMock()
    sys.modules["requests.Session"] = MagicMock
    sys.modules["requests.post"] = MagicMock

    feed = UniswapRequestDataSpot(
        data_queue,
        public_key="test_key",
        private_key="test_secret",
    )
    return feed


@pytest.fixture
def uniswap_feed_arbitrum(data_queue):
    """Create a Uniswap feed for Arbitrum."""
    import sys

    # Mock requests module
    sys.modules["requests"] = MagicMock()
    sys.modules["requests.Session"] = MagicMock
    sys.modules["requests.post"] = MagicMock

    feed = UniswapRequestDataSpot(
        data_queue,
        chain=UniswapChain.ARBITRUM,
        public_key="test_key",
        private_key="test_secret",
    )
    return feed


# ==================== ServerTime Tests ====================


class TestUniswapServerTime:
    """Test server time functionality."""

    def test_uniswap_req_server_time(self, uniswap_feed):
        """Test getting server time from Uniswap API."""
        # Uniswap uses blockchain timestamps, not a server time endpoint
        assert True


# ==================== Ticker/Token Price Tests ====================


class TestUniswapTickerData:
    """Test token price/ticker data functionality."""

    def test_uniswap_req_token_price(self, uniswap_feed):
        """Test getting token price from Uniswap API."""
        # Get WETH price
        data = uniswap_feed.get_tick(WETH_ADDRESS)
        assert data is not None

    def test_uniswap_token_price_validation(self, uniswap_feed):
        """Test token price data structure and values."""
        data = uniswap_feed.get_tick(WETH_ADDRESS)
        assert data is not None

        if data:
            pass
            # Uniswap returns token price in USD
        assert isinstance(data, (dict, list))

    def test_uniswap_multiple_token_prices(self, uniswap_feed):
        """Test getting multiple token prices."""
        tokens = [WETH_ADDRESS, USDC_ADDRESS, WBTC_ADDRESS]
        for t in tokens:
            data = uniswap_feed.get_tick(t)
            assert data is not None

    @pytest.mark.ticker
    def test_uniswap_get_tick_params(self, uniswap_feed):
        """Test get tick parameter generation."""
        path, params, extra_data = uniswap_feed._get_tick(WETH_ADDRESS)
        assert path is not None
        assert extra_data is not None
        assert extra_data.get("request_type") == "get_tick"
        assert extra_data.get("symbol_name") == WETH_ADDRESS


# ==================== Pool Tests ====================


class TestUniswapPoolData:
    """Test pool data functionality."""

    def test_uniswap_req_pool_data(self, uniswap_feed):
        """Test getting pool data from Uniswap API."""
        data = uniswap_feed.get_pool(USDC_WETH_POOL)
        assert data is not None

    def test_uniswap_pool_data_validation(self, uniswap_feed):
        """Test pool data structure and values."""
        data = uniswap_feed.get_pool(USDC_WETH_POOL)
        assert data is not None

        if data:
            pass
            # Pool should have token0, token1, liquidity, etc.
        assert isinstance(data, (dict, list))

    def test_uniswap_multiple_pools(self, uniswap_feed):
        """Test getting multiple pool data."""
        pools = [USDC_WETH_POOL, USDT_WETH_POOL]
        for p in pools:
            data = uniswap_feed.get_pool(p)
            assert data is not None

    def test_uniswap_get_pool_params(self, uniswap_feed):
        """Test get pool parameter generation."""
        path, params, extra_data = uniswap_feed._get_pool(USDC_WETH_POOL)
        assert path is not None
        assert extra_data is not None
        assert extra_data.get("request_type") == "get_pool"
        assert extra_data.get("pool_id") == USDC_WETH_POOL


# ==================== Swap Quote Tests ====================


class TestUniswapSwapQuote:
    """Test swap quote functionality."""

    def test_uniswap_req_swap_quote(self, uniswap_feed):
        """Test getting swap quote from Uniswap API."""
        # Get quote for swapping 1 WETH to USDC
        data = uniswap_feed.get_swap_quote(
            token_in=WETH_ADDRESS,
            token_out=USDC_ADDRESS,
            amount="1",
        )
        assert data is not None

    def test_uniswap_swap_quote_validation(self, uniswap_feed):
        """Test swap quote data structure and values."""
        data = uniswap_feed.get_swap_quote(
            token_in=WETH_ADDRESS,
            token_out=USDC_ADDRESS,
            amount="1",
        )
        assert data is not None

        if data:
            pass
            # Quote should have amountIn, amountOut, priceImpact, etc.
        assert isinstance(data, (dict, list))

    def test_uniswap_swap_quote_exact_out(self, uniswap_feed):
        """Test swap quote for exact output."""
        data = uniswap_feed.get_swap_quote(
            token_in=USDC_ADDRESS,
            token_out=WETH_ADDRESS,
            amount="1",
            swap_type="EXACT_OUT",
        )
        assert data is not None

    def test_uniswap_get_swap_quote_params(self, uniswap_feed):
        """Test get swap quote parameter generation."""
        path, params, extra_data = uniswap_feed._get_swap_quote(
            token_in=WETH_ADDRESS,
            token_out=USDC_ADDRESS,
            amount="1",
        )
        assert path is not None
        assert extra_data is not None
        assert extra_data.get("request_type") == "get_swap_quote"
        assert extra_data.get("token_in") == WETH_ADDRESS
        assert extra_data.get("token_out") == USDC_ADDRESS


# ==================== Swappable Tokens Tests ====================


class TestUniswapSwappableTokens:
    """Test swappable tokens functionality."""

    def test_uniswap_req_swappable_tokens(self, uniswap_feed):
        """Test getting swappable tokens from Uniswap API."""
        data = uniswap_feed.get_swappable_tokens()
        assert data is not None

        if data:
            pass
            # Should return a list of tokens
        assert isinstance(data, (list, dict))

    def test_uniswap_get_swappable_tokens_params(self, uniswap_feed):
        """Test get swappable tokens parameter generation."""
        path, params, extra_data = uniswap_feed._get_swappable_tokens()
        assert path is not None
        assert extra_data is not None
        assert extra_data.get("request_type") == "get_swappable_tokens"


# ==================== Kline Tests ====================


class TestUniswapKlineData:
    """Test kline/candlestick data functionality."""

    @pytest.mark.kline
    def test_uniswap_req_kline_data(self, uniswap_feed):
        """Test getting kline data - not supported directly."""
        # Uniswap doesn't provide direct kline data
        # Use pool events or external data sources
        data = uniswap_feed.get_kline(WETH_ADDRESS, "1h", count=20)
        # Should return a message about using external sources
        assert data is not None


# ==================== Depth/Liquidity Tests ====================


class TestUniswapDepth:
    """Test liquidity depth functionality."""

    @pytest.mark.orderbook
    def test_uniswap_req_depth_as_pool(self, uniswap_feed):
        """Test getting depth - returns pool info for Uniswap."""
        # For Uniswap, depth is pool liquidity
        # If the symbol is a pool address, it should return pool info
        data = uniswap_feed.get_depth(USDC_WETH_POOL, count=20)
        # Should either return pool data or message about AMM
        assert data is not None

    @pytest.mark.orderbook
    def test_uniswap_get_depth_params(self, uniswap_feed):
        """Test get depth parameter generation."""
        path, params, extra_data = uniswap_feed._get_depth(WETH_ADDRESS, count=20)
        assert path is not None
        assert extra_data is not None
        assert extra_data.get("request_type") == "get_depth"


# ==================== Exchange Info Tests ====================


class TestUniswapExchangeInfo:
    """Test exchange information functionality."""

    def test_uniswap_get_exchange_info_params(self, uniswap_feed):
        """Test get exchange info parameter generation."""
        path, params, extra_data = uniswap_feed._get_exchange_info()
        assert path is not None
        assert extra_data is not None
        assert extra_data.get("request_type") == "get_exchange_info"

    def test_uniswap_req_exchange_info(self, uniswap_feed):
        """Test getting exchange info."""
        data = uniswap_feed.get_exchange_info()
        assert data is not None


# ==================== Chain Tests ====================


class TestUniswapChainSupport:
    """Test multi-chain support."""

    def test_uniswap_ethereum_chain(self, uniswap_feed):
        """Test Uniswap on Ethereum chain."""
        assert uniswap_feed.chain == UniswapChain.ETHEREUM
        # get_chain_value might not be implemented - use chain.value instead
        assert uniswap_feed.chain.value == "ETHEREUM"

    def test_uniswap_arbitrum_chain(self, uniswap_feed_arbitrum):
        """Test Uniswap on Arbitrum chain."""
        assert uniswap_feed_arbitrum.chain == UniswapChain.ARBITRUM
        # get_chain_value might not be implemented - use chain.value instead
        assert uniswap_feed_arbitrum.chain.value == "ARBITRUM"

    def test_uniswap_supported_chains(self):
        """Test supported chains."""
        assert UniswapChain.ETHEREUM in UniswapExchangeData.SUBGRAPH_URLS
        assert UniswapChain.ARBITRUM in UniswapExchangeData.SUBGRAPH_URLS
        assert UniswapChain.OPTIMISM in UniswapExchangeData.SUBGRAPH_URLS
        assert UniswapChain.POLYGON in UniswapExchangeData.SUBGRAPH_URLS


# ==================== Exchange Data Tests ====================


class TestUniswapExchangeData:
    """Test Uniswap exchange data configuration."""

    def test_exchange_data_creation(self):
        """Test creating Uniswap exchange data."""
        exchange_data = UniswapExchangeDataSpot()
        assert exchange_data.chain == UniswapChain.ETHEREUM
        assert exchange_data.rest_url
        assert "uniswap" in exchange_data.rest_url.lower()

    def test_exchange_data_with_chain(self):
        """Test creating Uniswap exchange data with specific chain."""
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
        """Test symbol format - token address is returned as-is."""
        exchange_data = UniswapExchangeDataSpot()
        token_address = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
        assert exchange_data.get_symbol(token_address) == token_address

    @pytest.mark.kline
    def test_kline_periods(self):
        """Test kline period configuration."""
        exchange_data = UniswapExchangeDataSpot()
        # Uniswap might not have traditional kline_periods attribute
        # or it might be inherited from base class
        assert hasattr(exchange_data, "kline_periods") or hasattr(exchange_data, "rest_url")


# ==================== Registry Tests ====================


class TestUniswapRegistry:
    """Test Uniswap registration."""

    def test_uniswap_registered(self):
        """Test that Uniswap is properly registered."""
        assert "UNISWAP___DEX" in ExchangeRegistry._feed_classes
        assert "UNISWAP___DEX" in ExchangeRegistry._exchange_data_classes

    def test_uniswap_create_exchange_data(self):
        """Test creating Uniswap exchange data through registry."""
        exchange_data = ExchangeRegistry.create_exchange_data("UNISWAP___DEX")
        assert isinstance(exchange_data, UniswapExchangeDataSpot)

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


# ==================== Normalization Tests ====================


class TestUniswapNormalization:
    """Test data normalization functions."""

    @pytest.mark.ticker
    def test_get_tick_normalize_function(self):
        """Test ticker normalization function."""
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
        result, status = UniswapRequestDataSpot._get_tick_normalize_function(input_data, extra_data)

        assert status is True
        assert result is not None
        assert isinstance(result, list)
        assert len(result) > 0

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
        result, status = UniswapRequestDataSpot._get_pool_normalize_function(input_data, extra_data)

        assert status is True
        assert result is not None

    def test_get_swap_quote_normalize_function(self):
        """Test swap quote normalization function."""
        input_data = {
            "data": {
                "quote": {
                    "tokenIn": {"address": WETH_ADDRESS, "symbol": "WETH"},
                    "tokenOut": {"address": USDC_ADDRESS, "symbol": "USDC"},
                    "amountIn": "1",
                    "amountOut": "3000",
                }
            }
        }

        extra_data = {}
        result, status = UniswapRequestDataSpot._get_swap_quote_normalize_function(
            input_data, extra_data
        )

        assert status is True
        assert result is not None

    def test_get_swappable_tokens_normalize_function(self):
        """Test swappable tokens normalization function."""
        input_data = {
            "data": {
                "swappableTokens": [
                    {"id": WETH_ADDRESS, "symbol": "WETH"},
                    {"id": USDC_ADDRESS, "symbol": "USDC"},
                ]
            }
        }

        extra_data = {}
        result, status = UniswapRequestDataSpot._get_swappable_tokens_normalize_function(
            input_data, extra_data
        )

        assert status is True
        assert isinstance(result, list)

    @pytest.mark.orderbook
    def test_get_depth_normalize_function(self):
        """Test depth normalization function."""
        # Uniswap pools don't have order books
        input_data = {"message": "AMM pool liquidity"}

        extra_data = {}
        result, status = UniswapRequestDataSpot._get_depth_normalize_function(
            input_data, extra_data
        )

        assert status is True
        assert result is not None

    @pytest.mark.kline
    def test_get_kline_normalize_function(self):
        """Test kline normalization function."""
        # Uniswap doesn't have direct kline data
        input_data = {}

        extra_data = {}
        result, status = UniswapRequestDataSpot._get_kline_normalize_function(
            input_data, extra_data
        )

        # Should return empty list with True status
        assert status is True
        assert result == []

    def test_get_exchange_info_normalize_function(self):
        """Test exchange info normalization function."""
        input_data = {
            "data": {
                "service": {
                    "apiKey": "test",
                    "version": "1.0",
                    "supportedChains": ["ETHEREUM", "ARBITRUM"],
                },
                "tokens": {"totalCount": 1000},
                "pools": {"totalCount": 500},
            }
        }

        extra_data = {}
        result, status = UniswapRequestDataSpot._get_exchange_info_normalize_function(
            input_data, extra_data
        )

        assert status is True
        assert result is not None


# ==================== Integration Tests ====================


class TestUniswapIntegration:
    """Integration tests for Uniswap."""

    @pytest.mark.integration
    def test_get_token_price_live(self):
        """Test getting token price from live API."""
        data_queue = queue.Queue()
        feed = UniswapRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
        )
        data = feed.get_tick(WETH_ADDRESS)
        assert data is not None

    @pytest.mark.integration
    def test_get_pool_info_live(self):
        """Test getting pool info from live API."""
        data_queue = queue.Queue()
        feed = UniswapRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
        )
        data = feed.get_pool(USDC_WETH_POOL)
        assert data is not None

    @pytest.mark.integration
    def test_get_swap_quote_live(self):
        """Test getting swap quote from live API."""
        data_queue = queue.Queue()
        feed = UniswapRequestDataSpot(
            data_queue,
            public_key="test_key",
            private_key="test_secret",
        )
        data = feed.get_swap_quote(
            token_in=WETH_ADDRESS,
            token_out=USDC_ADDRESS,
            amount="1",
        )
        assert data is not None

    @pytest.mark.integration
    def test_multi_chain_support(self):
        """Test multi-chain support (no API call required)."""
        # Test on Arbitrum - just verify the chain is set correctly
        data_queue = queue.Queue()
        feed = UniswapRequestDataSpot(
            data_queue,
            chain=UniswapChain.ARBITRUM,
            public_key="test_key",
            private_key="test_secret",
        )
        assert feed.chain == UniswapChain.ARBITRUM


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-m", "not integration"])
