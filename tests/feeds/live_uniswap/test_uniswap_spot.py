"""
Tests for Uniswap Spot Feed implementation.
"""

import os
from decimal import Decimal
from unittest.mock import MagicMock, Mock, patch

import pytest

from bt_api_py.containers.exchanges.uniswap_pool import UniswapPool
from bt_api_py.containers.exchanges.uniswap_quote import UniswapQuote
from bt_api_py.containers.exchanges.uniswap_ticker import UniswapTicker
from bt_api_py.feeds.live_uniswap.spot import UniswapRequestDataSpot

# Set a dummy API key for tests
os.environ["UNISWAP_API_KEY"] = "test_api_key"


class TestUniswapRequestDataSpot:
    """Test cases for UniswapRequestDataSpot."""

    @pytest.fixture
    def mock_data_queue(self):
        """Mock data queue."""
        return Mock()

    @pytest.fixture
    def mock_http_client(self):
        """Mock HTTP client."""
        client = MagicMock()
        return client

    @pytest.fixture
    def uniswap_spot(self, mock_data_queue, mock_http_client):
        """Create UniswapRequestDataSpot instance with mocked HTTP client."""
        with patch("bt_api_py.feeds.http_client.HttpClient", return_value=mock_http_client):
            instance = UniswapRequestDataSpot(mock_data_queue, chain="ETHEREUM")
            # Replace the http_client with our mock
            instance._http_client = mock_http_client
            return instance

    def test_init(self, uniswap_spot):
        """Test initialization."""
        assert uniswap_spot.exchange_name == "UNISWAP___DEX"
        assert uniswap_spot.chain.value == "ETHEREUM"
        assert uniswap_spot.asset_type == "ethereum"
        assert uniswap_spot.logger_name == "uniswap_dex_feed.log"

    def test_capabilities(self, uniswap_spot):
        """Test declared capabilities."""
        from bt_api_py.feeds.capability import Capability

        capabilities = uniswap_spot._capabilities()
        assert Capability.GET_TICK in capabilities
        assert Capability.GET_DEPTH in capabilities
        assert Capability.GET_EXCHANGE_INFO in capabilities
        assert Capability.GET_KLINE in capabilities
        assert Capability.GET_BALANCE in capabilities
        assert Capability.GET_ACCOUNT in capabilities
        assert Capability.MAKE_ORDER in capabilities
        assert Capability.CANCEL_ORDER in capabilities

    def test_get_server_time(self, uniswap_spot):
        """Test get_server_time returns RequestData."""
        from bt_api_py.containers.requestdatas.request_data import RequestData

        result = uniswap_spot.get_server_time()
        assert isinstance(result, RequestData)

    def test_get_server_time_tuple(self, uniswap_spot):
        """Test _get_server_time returns tuple."""
        path, params, extra_data = uniswap_spot._get_server_time()
        assert extra_data["request_type"] == "get_server_time"
        assert "server_time" in extra_data

    @pytest.mark.ticker
    def test_get_tick(self, uniswap_spot, mock_http_client):
        """Test get_tick method."""
        # Mock response
        mock_response = {
            "data": {
                "token": {
                    "id": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
                    "symbol": "WETH",
                    "name": "Wrapped Ether",
                    "decimals": 18,
                    "price": {"USD": "3000.0"},
                    "priceChange24h": {"USD": "50.0"},
                    "volume": {"USD": "1000000.0"},
                    "marketCap": {"USD": "360000000000"},
                }
            }
        }
        mock_http_client.request.return_value = mock_response

        # Call method
        result = uniswap_spot.get_tick("0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2")

        # Verify result
        assert result.input_data == mock_response
        assert result.extra_data is not None
        assert result.extra_data["symbol_name"] == "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"

    def test_get_pool(self, uniswap_spot, mock_http_client):
        """Test get_pool method."""
        # Mock response
        mock_response = {
            "data": {
                "pool": {
                    "id": "0x8ad599c3a0ff1de082011efddc58f1908eb6e6d8",
                    "address": "0x8ad599c3a0ff1de082011efddc58f1908eb6e6d8",
                    "name": "WETH/USDC",
                    "symbol": "WETH/USDC 0.3%",
                    "type": "V3",
                    "swapFee": "0.003",
                    "totalValueLockedUSD": "10000000",
                    "volumeUSD": "5000000",
                    "volumeUSDDay": "100000",
                    "feesUSD": "15000",
                    "token0": {
                        "id": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
                        "symbol": "WETH",
                        "name": "Wrapped Ether",
                        "decimals": 18,
                        "priceUSD": "3000.0",
                    },
                    "token1": {
                        "id": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
                        "symbol": "USDC",
                        "name": "USD Coin",
                        "decimals": 6,
                        "priceUSD": "1.0",
                    },
                    "reserve0": "1000",
                    "reserve1": "3000000",
                    "token0Price": "3000.0",
                    "token1Price": "0.0003333333333333333",
                    "liquidityProviderCount": 100,
                }
            }
        }
        mock_http_client.request.return_value = mock_response

        # Call method
        result = uniswap_spot.get_pool("0x8ad599c3a0ff1de082011efddc58f1908eb6e6d8")

        # Verify result
        assert result.input_data == mock_response
        assert result.extra_data is not None
        assert result.extra_data["pool_id"] == "0x8ad599c3a0ff1de082011efddc58f1908eb6e6d8"

    def test_get_swap_quote(self, uniswap_spot, mock_http_client):
        """Test get_swap_quote method."""
        # Mock response
        mock_response = {
            "data": {
                "quote": {
                    "tokenIn": {
                        "address": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
                        "symbol": "WETH",
                        "name": "Wrapped Ether",
                    },
                    "tokenOut": {
                        "address": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
                        "symbol": "USDC",
                        "name": "USD Coin",
                    },
                    "amountIn": "1",
                    "amountOut": "2985.05",
                    "priceImpact": "0.5",
                    "estimatedGas": "45000",
                    "route": {
                        "id": "route_1",
                        "segments": [
                            {
                                "pool": {
                                    "id": "0x8ad599c3a0ff1de082011efddc58f1908eb6e6d8",
                                    "address": "0x8ad599c3a0ff1de082011efddc58f1908eb6e6d8",
                                },
                                "tokenIn": {
                                    "address": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
                                    "symbol": "WETH",
                                },
                                "tokenOut": {
                                    "address": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
                                    "symbol": "USDC",
                                },
                            }
                        ],
                    },
                }
            }
        }
        mock_http_client.request.return_value = mock_response

        # Call method
        result = uniswap_spot.get_swap_quote(
            token_in="0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
            token_out="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
            amount="1",
        )

        # Verify result
        assert result.input_data == mock_response
        assert result.extra_data is not None
        assert result.extra_data["token_in"] == "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
        assert result.extra_data["token_out"] == "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"

    def test_get_swappable_tokens(self, uniswap_spot, mock_http_client):
        """Test get_swappable_tokens method."""
        # Mock response
        mock_response = {
            "data": {
                "swappableTokens": [
                    {
                        "id": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
                        "address": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
                        "symbol": "WETH",
                        "name": "Wrapped Ether",
                        "decimals": 18,
                        "priceUSD": "3000.0",
                        "volumeUSD": "1000000",
                        "marketCapUSD": "360000000000",
                        "totalLiquidityUSD": "10000000",
                    },
                    {
                        "id": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
                        "address": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
                        "symbol": "USDC",
                        "name": "USD Coin",
                        "decimals": 6,
                        "priceUSD": "1.0",
                        "volumeUSD": "5000000",
                        "marketCapUSD": "50000000000",
                        "totalLiquidityUSD": "50000000",
                    },
                ]
            }
        }
        mock_http_client.request.return_value = mock_response

        # Call method
        result = uniswap_spot.get_swappable_tokens()

        # Verify result
        assert result.input_data == mock_response
        assert len(result.input_data["data"]["swappableTokens"]) == 2

    @pytest.mark.orderbook
    def test_get_depth(self, uniswap_spot, mock_http_client):
        """Test get_depth method."""
        # Mock response for pool
        mock_response = {
            "data": {
                "pool": {
                    "id": "0x8ad599c3a0ff1de082011efddc58f1908eb6e6d8",
                    "address": "0x8ad599c3a0ff1de082011efddc58f1908eb6e6d8",
                    "name": "WETH/USDC",
                    "symbol": "WETH/USDC 0.3%",
                    "type": "V3",
                    "totalValueLockedUSD": "10000000",
                }
            }
        }
        mock_http_client.request.return_value = mock_response

        # Call method with pool address (starts with 0x)
        result = uniswap_spot.get_depth("0x8ad599c3a0ff1de082011efddc58f1908eb6e6d8")

        # Verify result - should return pool data
        assert result.input_data == mock_response
        assert result.extra_data is not None

    @pytest.mark.kline
    def test_get_kline(self, uniswap_spot, mock_http_client):
        """Test get_kline method."""
        # Call method - kline returns a message without making HTTP request
        result = uniswap_spot.get_kline("0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", "1d")

        # Verify result - should return message about klines not being available
        assert result.input_data == {
            "message": "Klines not available via Uniswap Trading API. Use pool events or external sources."
        }
        assert result.extra_data is not None
        assert result.extra_data.get("request_type") == "get_kline"

    def test_get_exchange_info(self, uniswap_spot, mock_http_client):
        """Test get_exchange_info method."""
        # Mock response
        mock_response = {
            "data": {
                "service": {
                    "apiKey": "test_key",
                    "version": "1.0",
                    "supportedChains": ["ETHEREUM", "ARBITRUM"],
                    "features": ["swapping", "pool_management"],
                },
                "tokens": {"totalCount": 1000},
                "pools": {"totalCount": 500},
            }
        }
        mock_http_client.request.return_value = mock_response

        # Call method
        result = uniswap_spot.get_exchange_info()

        # Verify result
        assert result.input_data == mock_response
        assert result.extra_data is not None


class TestUniswapTicker:
    """Test cases for UniswapTicker."""

    def test_from_graphql_data(self):
        """Test creating ticker from GraphQL data."""
        data = {
            "token": {
                "id": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
                "symbol": "WETH",
                "name": "Wrapped Ether",
                "decimals": 18,
                "price": {"USD": "3000.0"},
                "priceChange24h": {"USD": "50.0"},
                "volume": {"USD": "1000000.0"},
                "marketCap": {"USD": "360000000000"},
            }
        }

        ticker = UniswapTicker.from_graphql_data(data)

        assert ticker.symbol == "WETH"
        assert ticker.name == "Wrapped Ether"
        assert ticker.address == "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
        assert ticker.decimals == 18
        assert ticker.price == 3000.0
        assert ticker.price_change_24h == 50.0
        assert ticker.volume_24h == 1000000.0
        assert ticker.market_cap == 360000000000.0

    def test_price_percentage_calculation(self):
        """Test price change percentage calculation."""
        ticker = UniswapTicker(
            symbol="WETH",
            name="Wrapped Ether",
            address="0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
            decimals=18,
            price=3000.0,
            price_change_24h=150.0,  # 5% of 3000
        )

        assert ticker.price_change_percentage_24h == 5.0

    def test_validation_flags(self):
        """Test validation flags."""
        # Valid ticker
        ticker = UniswapTicker(
            symbol="WETH",
            name="Wrapped Ether",
            address="0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
            decimals=18,
            price=3000.0,
            volume_24h=1000000.0,
            total_liquidity_usd=10000000.0,
        )

        assert ticker.is_valid_price == True
        assert ticker.is_valid_volume == True
        assert ticker.has_liquidity == True

        # Invalid ticker
        invalid_ticker = UniswapTicker(
            symbol="TEST",
            name="Test Token",
            address="0x1234567890123456789012345678901234567890",
            decimals=18,
            price=0.0,
            volume_24h=0.0,
            total_liquidity_usd=0.0,
        )

        assert invalid_ticker.is_valid_price == False
        assert invalid_ticker.is_valid_volume == False
        assert invalid_ticker.has_liquidity == False


class TestUniswapPool:
    """Test cases for UniswapPool."""

    def test_from_graphql_data(self):
        """Test creating pool from GraphQL data."""
        data = {
            "pool": {
                "id": "0x8ad599c3a0ff1de082011efddc58f1908eb6e6d8",
                "address": "0x8ad599c3a0ff1de082011efddc58f1908eb6e6d8",
                "name": "WETH/USDC",
                "symbol": "WETH/USDC 0.3%",
                "type": "V3",
                "swapFee": "0.003",
                "totalValueLockedUSD": "10000000",
                "volumeUSD": "5000000",
                "volumeUSDDay": "100000",
                "feesUSD": "15000",
                "token0": {
                    "id": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
                    "symbol": "WETH",
                    "name": "Wrapped Ether",
                    "decimals": 18,
                    "priceUSD": "3000.0",
                },
                "token1": {
                    "id": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
                    "symbol": "USDC",
                    "name": "USD Coin",
                    "decimals": 6,
                    "priceUSD": "1.0",
                },
                "reserve0": "1000",
                "reserve1": "3000000",
                "token0Price": "3000.0",
                "token1Price": "0.0003333333333333333",
                "liquidityProviderCount": 100,
                "dynamicData": {
                    "totalValueLockedUSD": "10000000",
                    "volumeUSD": "5000000",
                    "volumeUSDDay": "100000",
                    "feesUSD": "15000",
                    "aprItems": [{"title": "Trading Fees", "type": "swap", "apr": "0.15"}],
                },
            }
        }

        pool = UniswapPool.from_graphql_data(data)

        assert pool.pool_id == "0x8ad599c3a0ff1de082011efddc58f1908eb6e6d8"
        assert pool.address == "0x8ad599c3a0ff1de082011efddc58f1908eb6e6d8"
        assert pool.name == "WETH/USDC"
        assert pool.symbol == "WETH/USDC 0.3%"
        assert pool.pool_type == "V3"
        assert pool.token0.symbol == "WETH"
        assert pool.token1.symbol == "USDC"
        assert pool.token0.reserve == Decimal("1000")
        assert pool.token1.reserve == Decimal("3000000")
        assert pool.token0_price == Decimal("3000.0")
        # token1_price is recalculated as 1/token0_price in __post_init__
        assert pool.token1_price == Decimal("1") / Decimal("3000.0")
        assert pool.stats.total_value_locked_usd == Decimal("10000000")
        assert pool.stats.apr == Decimal("0.15")
        assert pool.is_valid == True
        assert pool.has_liquidity == True

    def test_get_token_balance(self):
        """Test getting token balance from pool."""
        from bt_api_py.containers.exchanges.uniswap_pool import UniswapPoolToken

        pool = UniswapPool(
            pool_id="test",
            address="0x123",
            name="Test Pool",
            symbol="TEST",
            pool_type="V3",
            token0=None,
            token1=None,
            tokens=[
                UniswapPoolToken(
                    address="0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
                    symbol="WETH",
                    name="Wrapped Ether",
                    decimals=18,
                    reserve=Decimal("1000"),
                ),
                UniswapPoolToken(
                    address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
                    symbol="USDC",
                    name="USD Coin",
                    decimals=6,
                    reserve=Decimal("3000000"),
                ),
            ],
        )

        # Get WETH balance
        weth_balance = pool.get_token_balance("0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2")
        assert weth_balance == Decimal("1000")

        # Get USDC balance
        usdc_balance = pool.get_token_balance("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48")
        assert usdc_balance == Decimal("3000000")

        # Get non-existent token
        non_existent = pool.get_token_balance("0x1111111111111111111111111111111111111111")
        assert non_existent is None


class TestUniswapQuote:
    """Test cases for UniswapQuote."""

    def test_from_graphql_data(self):
        """Test creating quote from GraphQL data."""
        data = {
            "quote": {
                "tokenIn": {
                    "address": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
                    "symbol": "WETH",
                    "name": "Wrapped Ether",
                },
                "tokenOut": {
                    "address": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
                    "symbol": "USDC",
                    "name": "USD Coin",
                },
                "amountIn": "1",
                "amountOut": "2985.05",
                "priceImpact": "0.005",  # 0.5% price impact
                "estimatedGas": "45000",
                "route": {
                    "id": "route_1",
                    "segments": [
                        {
                            "pool": {
                                "id": "0x8ad599c3a0ff1de082011efddc58f1908eb6e6d8",
                                "address": "0x8ad599c3a0ff1de082011efddc58f1908eb6e6d8",
                            },
                            "tokenIn": {
                                "address": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
                                "symbol": "WETH",
                            },
                            "tokenOut": {
                                "address": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
                                "symbol": "USDC",
                            },
                        }
                    ],
                },
            }
        }

        quote = UniswapQuote.from_graphql_data(data)

        assert quote.token_in.symbol == "WETH"
        assert quote.token_out.symbol == "USDC"
        assert quote.amount_in == Decimal("1")
        assert quote.amount_out == Decimal("2985.05")
        assert quote.price_impact == Decimal("0.005")
        assert quote.estimated_gas == 45000
        assert len(quote.routes) == 1
        assert quote.is_valid == True
        # price_impact_percentage is calculated as abs(0.005) / 1 * 100 = 0.5
        # has_price_impact_warning is True when percentage > 0.5 (threshold)
        # Since 0.5 is NOT > 0.5, warning should be False
        assert quote.price_impact_percentage == 0.5
        assert quote.has_price_impact_warning == False  # 0.5% is not > 0.5% threshold

    def test_price_impact_calculation(self):
        """Test price impact percentage calculation."""
        quote = UniswapQuote(
            quote_id="test",
            token_in=Mock(address="0x1", symbol="WETH", name="Wrapped Ether"),
            token_out=Mock(address="0x2", symbol="USDC", name="USD Coin"),
            swap_type="EXACT_IN",
            amount_in=Decimal("100"),
            amount_out=Decimal("90"),
            price_impact=Decimal("-10"),  # -10 amount impact
        )

        # price_impact_percentage is calculated as abs(-10) / 100 * 100 = 10.0
        # Note: __post_init__ calculates this as a float, not Decimal
        assert quote.price_impact_percentage == 10.0
        assert quote.has_price_impact_warning == True  # 10% > 0.5% threshold

    def test_effective_rate(self):
        """Test effective exchange rate calculation."""
        quote = UniswapQuote(
            quote_id="test",
            token_in=Mock(address="0x1", symbol="WETH", name="Wrapped Ether"),
            token_out=Mock(address="0x2", symbol="USDC", name="USD Coin"),
            swap_type="EXACT_IN",
            amount_in=Decimal("100"),
            amount_out=Decimal("90"),
        )

        assert quote.get_effective_rate() == Decimal("0.9")

    def test_slippage_adjusted_amount(self):
        """Test slippage adjusted amount calculation."""
        quote = UniswapQuote(
            quote_id="test",
            token_in=Mock(address="0x1", symbol="WETH", name="Wrapped Ether"),
            token_out=Mock(address="0x2", symbol="USDC", name="USD Coin"),
            swap_type="EXACT_IN",
            amount_in=Decimal("100"),
            amount_out=Decimal("90"),
        )

        # With 1% slippage tolerance
        adjusted = quote.get_slippage_adjusted_amount_out(Decimal("1"))
        assert adjusted == Decimal("89.1")  # 90 * 0.99


class TestUniswapStandardInterfaces:
    """Test standard Feed interface methods for Uniswap."""

    @pytest.fixture
    def uniswap_spot(self):
        with patch("bt_api_py.feeds.http_client.HttpClient", return_value=MagicMock()):
            instance = UniswapRequestDataSpot(Mock(), chain="ETHEREUM")
            instance.request = Mock(return_value=Mock())
            return instance

    def test_make_order_calls_request(self, uniswap_spot):
        result = uniswap_spot.make_order("WETH/USDC", 1.0, 3000, "LIMIT")
        assert uniswap_spot.request.called
        extra_data = uniswap_spot.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "make_order"

    def test_cancel_order_calls_request(self, uniswap_spot):
        result = uniswap_spot.cancel_order("WETH/USDC", "order_123")
        assert uniswap_spot.request.called
        extra_data = uniswap_spot.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "cancel_order"

    def test_query_order_calls_request(self, uniswap_spot):
        result = uniswap_spot.query_order("WETH/USDC", "order_123")
        assert uniswap_spot.request.called
        extra_data = uniswap_spot.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "query_order"

    def test_get_open_orders_calls_request(self, uniswap_spot):
        result = uniswap_spot.get_open_orders("WETH/USDC")
        assert uniswap_spot.request.called
        extra_data = uniswap_spot.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "get_open_orders"

    def test_get_account_calls_request(self, uniswap_spot):
        result = uniswap_spot.get_account("WETH")
        assert uniswap_spot.request.called
        extra_data = uniswap_spot.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "get_account"

    def test_get_balance_calls_request(self, uniswap_spot):
        result = uniswap_spot.get_balance("WETH")
        assert uniswap_spot.request.called
        extra_data = uniswap_spot.request.call_args[1].get("extra_data")
        assert extra_data["request_type"] == "get_balance"


class TestUniswapBaseCapabilities:
    """Test capabilities on the base class."""

    def test_base_capabilities(self):
        from bt_api_py.feeds.capability import Capability
        from bt_api_py.feeds.live_uniswap.request_base import UniswapRequestData

        caps = UniswapRequestData._capabilities()
        assert Capability.GET_TICK in caps
        assert Capability.GET_DEPTH in caps
        assert Capability.GET_EXCHANGE_INFO in caps
        assert Capability.MAKE_ORDER in caps
        assert Capability.CANCEL_ORDER in caps


class TestUniswapNormalizeFunctions:
    """Test normalize functions edge cases."""

    @pytest.mark.ticker
    def test_tick_normalize_with_none(self):
        result, status = UniswapRequestDataSpot._get_tick_normalize_function(None, None)
        assert result == []
        assert status is False

    @pytest.mark.orderbook
    def test_depth_normalize_with_none(self):
        result, status = UniswapRequestDataSpot._get_depth_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_pool_normalize_with_none(self):
        result, status = UniswapRequestDataSpot._get_pool_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_swap_quote_normalize_with_none(self):
        result, status = UniswapRequestDataSpot._get_swap_quote_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_swappable_tokens_normalize_with_none(self):
        result, status = UniswapRequestDataSpot._get_swappable_tokens_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_exchange_info_normalize_with_none(self):
        result, status = UniswapRequestDataSpot._get_exchange_info_normalize_function(None, None)
        assert result == []
        assert status is False


if __name__ == "__main__":
    pytest.main([__file__])
