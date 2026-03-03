"""
Tests for Balancer DEX Spot Feed implementation.

Following Binance/OKX test standards with DEX-specific adaptations.
"""

import queue
import pytest
from unittest.mock import Mock, patch, MagicMock
from decimal import Decimal
from enum import Enum

from bt_api_py.feeds.live_balancer.spot import BalancerRequestDataSpot
from bt_api_py.containers.exchanges.balancer_exchange_data import (
    BalancerExchangeDataSpot,
    GqlChain,
)
from bt_api_py.containers.requestdatas.request_data import RequestData


class MockGqlChain:
    """Mock GqlChain for testing."""
    MAINNET = "MAINNET"
    ARBITRUM = "ARBITRUM"
    POLYGON = "POLYGON"


class TestBalancerRequestDataSpot:
    """Test cases for BalancerRequestDataSpot."""

    @pytest.fixture
    def mock_data_queue(self):
        """Mock data queue."""
        return Mock()

    @pytest.fixture
    def balancer_spot(self, mock_data_queue):
        """Create BalancerRequestDataSpot instance."""
        with patch('bt_api_py.feeds.live_balancer.request_base.HttpClient', return_value=MagicMock()):
            instance = BalancerRequestDataSpot(mock_data_queue, chain=MockGqlChain.MAINNET)
            return instance

    def test_init(self, balancer_spot):
        """Test initialization."""
        assert balancer_spot.exchange_name == "BALANCER___DEX"
        assert balancer_spot.chain.value == "MAINNET"
        assert balancer_spot.asset_type == "DEX"
        assert balancer_spot.logger_name == "balancer_dex_feed.log"

    def test_capabilities(self, balancer_spot):
        """Test declared capabilities."""
        from bt_api_py.feeds.capability import Capability

        capabilities = balancer_spot._capabilities()
        assert Capability.GET_TICK in capabilities
        assert Capability.GET_DEPTH in capabilities
        assert Capability.GET_EXCHANGE_INFO in capabilities
        assert Capability.GET_KLINE in capabilities

    # ==================== Pool Tests ====================

    def test_get_pool(self, balancer_spot):
        """Test get_pool method."""
        pool_id = "0x7f2b3b7fbd3226c5be438cde49a519f442ca2eda00020000000000000000067d"
        path, params, extra_data = balancer_spot._get_pool(pool_id)

        assert extra_data['request_type'] == "get_pool"
        assert extra_data['pool_id'] == pool_id
        assert extra_data['exchange_name'] == "BALANCER___DEX"

    def test_get_pool_normalize_function(self):
        """Test pool normalize function."""
        input_data = {
            "data": {
                "poolGetPool": {
                    "id": "pool123",
                    "name": "Test Pool",
                    "totalLiquidity": "1000000",
                    "volume24h": "50000"
                }
            }
        }
        result, status = BalancerRequestDataSpot._get_pool_normalize_function(input_data, None)
        assert status == True
        assert len(result) == 1
        assert result[0]['id'] == "pool123"

    def test_get_pools(self, balancer_spot):
        """Test get_pools method."""
        path, params, extra_data = balancer_spot._get_pools(first=10, min_tvl=100000)

        assert extra_data['request_type'] == "get_pools"
        assert extra_data['exchange_name'] == "BALANCER___DEX"
        assert extra_data['chain'] == "MAINNET"

    def test_get_pools_normalize_function(self):
        """Test pools normalize function."""
        input_data = {
            "data": {
                "poolGetPools": [
                    {"id": "pool1", "name": "Pool 1", "totalLiquidity": "100000"},
                    {"id": "pool2", "name": "Pool 2", "totalLiquidity": "200000"},
                ]
            }
        }
        result, status = BalancerRequestDataSpot._get_pools_normalize_function(input_data, None)
        assert status == True
        assert len(result) == 2

    # ==================== Ticker Tests ====================

    def test_get_tick(self, balancer_spot):
        """Test get_tick method."""
        token_address = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
        path, params, extra_data = balancer_spot._get_tick(token_address)

        assert extra_data['request_type'] == "get_tick"
        assert extra_data['symbol_name'] == token_address
        assert extra_data['exchange_name'] == "BALANCER___DEX"

    def test_get_tick_normalize_function(self):
        """Test tick normalize function."""
        input_data = {
            "data": {
                "tokenGetTokenDynamicData": {
                    "price": "3000.50",
                    "priceChange24h": "50.00",
                    "marketCap": "100000000"
                }
            }
        }
        result, status = BalancerRequestDataSpot._get_tick_normalize_function(input_data, None)
        assert status == True
        assert len(result) == 1

    # ==================== Depth/OrderBook Tests ====================

    def test_get_depth(self, balancer_spot):
        """Test get_depth method."""
        pool_id = "0x7f2b3b7fbd3226c5be438cde49a519f442ca2eda00020000000000000000067d"
        path, params, extra_data = balancer_spot._get_depth(pool_id)

        assert extra_data['request_type'] == "get_depth"
        assert extra_data['symbol_name'] == pool_id
        assert extra_data['exchange_name'] == "BALANCER___DEX"

    def test_get_depth_normalize_function(self):
        """Test depth normalize function."""
        input_data = {
            "data": {
                "poolGetPool": {
                    "id": "pool123",
                    "swapVolume": "50000",
                    "totalLiquidity": "1000000"
                }
            }
        }
        result, status = BalancerRequestDataSpot._get_depth_normalize_function(input_data, None)
        assert status == True
        assert len(result) == 1

    # ==================== Swap Path Tests ====================

    def test_get_swap_path(self, balancer_spot):
        """Test get_swap_path method."""
        token_in = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
        token_out = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
        amount = "1"

        path, params, extra_data = balancer_spot._get_swap_path(
            token_in, token_out, amount
        )

        assert extra_data['request_type'] == "get_swap_path"
        assert extra_data['token_in'] == token_in
        assert extra_data['token_out'] == token_out
        assert extra_data['exchange_name'] == "BALANCER___DEX"

    def test_get_swap_path_normalize_function(self):
        """Test swap path normalize function."""
        input_data = {
            "data": {
                "sorGetSwapPaths": {
                    "swapAmountRaw": "1000000000000000000",
                    "returnAmountRaw": "3000000000000000000",
                    "tokenAddresses": [
                        "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
                        "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
                    ]
                }
            }
        }
        result, status = BalancerRequestDataSpot._get_swap_path_normalize_function(input_data, None)
        assert status == True
        assert len(result) == 1

    # ==================== Pool Events Tests ====================

    def test_get_pool_events(self, balancer_spot):
        """Test get_pool_events method."""
        pool_id = "0x7f2b3b7fbd3226c5be438cde49a519f442ca2eda00020000000000000000067d"
        event_type = "SWAP"

        path, params, extra_data = balancer_spot._get_pool_events(
            pool_id, event_type=event_type
        )

        assert extra_data['request_type'] == "get_pool_events"
        assert extra_data['pool_id'] == pool_id
        assert extra_data['event_type'] == event_type

    def test_get_pool_events_normalize_function(self):
        """Test pool events normalize function."""
        input_data = {
            "data": {
                "poolGetEvents": [
                    {"id": "swap1", "timestamp": 1234567890},
                    {"id": "swap2", "timestamp": 1234567891}
                ]
            }
        }
        result, status = BalancerRequestDataSpot._get_pool_events_normalize_function(input_data, None)
        assert status == True
        assert len(result) == 2

    # ==================== Kline Tests ====================

    def test_get_kline(self, balancer_spot):
        """Test get_kline method - DEX uses pool snapshots."""
        pool_id = "0x7f2b3b7fbd3226c5be438cde49a519f442ca2eda00020000000000000000067d"
        period = "1h"
        count = 100

        path, params, extra_data = balancer_spot._get_kline(pool_id, period, count)

        assert extra_data['request_type'] == "get_kline"
        assert extra_data['symbol_name'] == pool_id
        assert extra_data['period'] == period

    def test_get_kline_normalize_function(self):
        """Test kline normalize function."""
        input_data = {
            "data": {
                "poolGetPool": {
                    "hourlySnapshots": [
                        {"timestamp": 1234567890, "volume24h": "50000"},
                        {"timestamp": 1234567900, "volume24h": "60000"}
                    ]
                }
            }
        }
        result, status = BalancerRequestDataSpot._get_kline_normalize_function(input_data, None)
        # Balancer doesn't provide direct kline data through GraphQL
        assert status == True
        # The normalize function returns empty list as klines are not directly available
        assert isinstance(result, list)

    # ==================== Exchange Info Tests ====================

    def test_get_exchange_info(self, balancer_spot):
        """Test get_exchange_info method."""
        path, params, extra_data = balancer_spot._get_exchange_info()

        assert extra_data['request_type'] == "get_pools"
        assert extra_data['exchange_name'] == "BALANCER___DEX"

    @pytest.mark.skip(reason="Requires actual API call")
    def test_integration_pool_query(self, balancer_spot):
        """Integration test for pool query - skipped."""
        pass

    @pytest.mark.skip(reason="Requires actual API call")
    def test_integration_get_tick(self, balancer_spot):
        """Integration test for get_tick - skipped."""
        pass

    @pytest.mark.skip(reason="Requires actual API call")
    def test_integration_get_depth(self, balancer_spot):
        """Integration test for get_depth - skipped."""
        pass


class TestBalancerExchangeDataSpot:
    """Test cases for BalancerExchangeDataSpot."""

    def test_init_with_chain_enum(self):
        """Test initialization with chain enum."""
        with patch('bt_api_py.containers.exchanges.balancer_exchange_data._get_balancer_config', return_value=None):
            exchange_data = BalancerExchangeDataSpot(chain=MockGqlChain.MAINNET)
            assert exchange_data.chain == MockGqlChain.MAINNET
            assert exchange_data.rest_url == "https://api-v3.balancer.fi"

    def test_init_with_chain_string(self):
        """Test initialization with chain string."""
        with patch('bt_api_py.containers.exchanges.balancer_exchange_data._get_balancer_config', return_value=None):
            exchange_data = BalancerExchangeDataSpot(chain="MAINNET")
            assert exchange_data.chain.value == "MAINNET"

    def test_get_chain_value(self):
        """Test get_chain_value method."""
        with patch('bt_api_py.containers.exchanges.balancer_exchange_data._get_balancer_config', return_value=None):
            exchange_data = BalancerExchangeDataSpot(chain=MockGqlChain.MAINNET)
            assert exchange_data.get_chain_value() == "MAINNET"

    def test_get_symbol(self):
        """Test get_symbol method."""
        with patch('bt_api_py.containers.exchanges.balancer_exchange_data._get_balancer_config', return_value=None):
            exchange_data = BalancerExchangeDataSpot(chain=MockGqlChain.MAINNET)
            # Token address
            address = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
            assert exchange_data.get_symbol(address) == address

    def test_get_pool_id(self):
        """Test get_pool_id method."""
        with patch('bt_api_py.containers.exchanges.balancer_exchange_data._get_balancer_config', return_value=None):
            exchange_data = BalancerExchangeDataSpot(chain=MockGqlChain.MAINNET)
            pool_id = "0x7f2b3b7fbd3226c5be438cde49a519f442ca2eda00020000000000000000067d"
            assert exchange_data.get_pool_id(pool_id) == pool_id

    def test_get_rest_path(self):
        """Test get_rest_path method."""
        with patch('bt_api_py.containers.exchanges.balancer_exchange_data._get_balancer_config', return_value=None):
            exchange_data = BalancerExchangeDataSpot(chain=MockGqlChain.MAINNET)
            path = exchange_data.get_rest_path("get_tick")
            assert path == "POST https://api-v3.balancer.fi/graphql"

    def test_get_rest_url(self):
        """Test get_rest_url method."""
        with patch('bt_api_py.containers.exchanges.balancer_exchange_data._get_balancer_config', return_value=None):
            exchange_data = BalancerExchangeDataSpot(chain=MockGqlChain.MAINNET)
            assert exchange_data.get_rest_url() == "https://api-v3.balancer.fi"

    def test_kline_periods(self):
        """Test kline periods are defined."""
        with patch('bt_api_py.containers.exchanges.balancer_exchange_data._get_balancer_config', return_value=None):
            exchange_data = BalancerExchangeDataSpot(chain=MockGqlChain.MAINNET)
            assert "1m" in exchange_data.kline_periods
            assert "1h" in exchange_data.kline_periods
            assert "1d" in exchange_data.kline_periods

    def test_legal_currency(self):
        """Test legal currencies."""
        with patch('bt_api_py.containers.exchanges.balancer_exchange_data._get_balancer_config', return_value=None):
            exchange_data = BalancerExchangeDataSpot(chain=MockGqlChain.MAINNET)
            assert "USDT" in exchange_data.legal_currency
            assert "USDC" in exchange_data.legal_currency
            assert "ETH" in exchange_data.legal_currency


class TestBalancerRegistration:
    """Test cases for Balancer registry registration."""

    @pytest.mark.skip(reason="Registry test requires full module import")
    def test_registry_registration(self):
        """Test that Balancer is registered in the exchange registry."""
        from bt_api_py.registry import get_exchange_class

        exchange_class = get_exchange_class("BALANCER___DEX")
        assert exchange_class is not None
        assert exchange_class.__name__ == "BalancerRequestDataSpot"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
