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
        assert Capability.GET_BALANCE in capabilities
        assert Capability.GET_ACCOUNT in capabilities
        assert Capability.MAKE_ORDER in capabilities
        assert Capability.CANCEL_ORDER in capabilities

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


class TestBalancerStandardInterfaces:
    """Test standard Feed interface methods for Balancer."""

    @pytest.fixture
    def balancer_spot(self):
        """Create BalancerRequestDataSpot instance with mocked request."""
        with patch('bt_api_py.feeds.live_balancer.request_base.HttpClient', return_value=MagicMock()):
            instance = BalancerRequestDataSpot(Mock(), chain=MockGqlChain.MAINNET)
            instance.request = Mock(return_value=Mock(spec=RequestData))
            return instance

    # ── get_tick via request() ────────────────────────────────

    def test_get_tick_calls_request(self, balancer_spot):
        """Test get_tick calls self.request with correct extra_data."""
        token = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
        result = balancer_spot.get_tick(token)
        assert balancer_spot.request.called
        call_args = balancer_spot.request.call_args
        extra_data = call_args[1].get("extra_data") or call_args[0][2] if len(call_args[0]) > 2 else call_args[1].get("extra_data")
        assert extra_data["request_type"] == "get_tick"
        assert extra_data["symbol_name"] == token

    # ── get_depth via request() ───────────────────────────────

    def test_get_depth_calls_request(self, balancer_spot):
        """Test get_depth calls self.request."""
        pool_id = "0x7f2b3b7fbd3226c5be438cde49a519f442ca2eda00020000000000000000067d"
        result = balancer_spot.get_depth(pool_id)
        assert balancer_spot.request.called
        call_args = balancer_spot.request.call_args
        extra_data = call_args[1].get("extra_data") or call_args[0][2] if len(call_args[0]) > 2 else call_args[1].get("extra_data")
        assert extra_data["request_type"] == "get_depth"
        assert extra_data["symbol_name"] == pool_id

    # ── get_kline via request() ───────────────────────────────

    def test_get_kline_calls_request(self, balancer_spot):
        """Test get_kline calls self.request."""
        result = balancer_spot.get_kline("0xpool", "1h", 100)
        assert balancer_spot.request.called
        call_args = balancer_spot.request.call_args
        extra_data = call_args[1].get("extra_data") or call_args[0][2] if len(call_args[0]) > 2 else call_args[1].get("extra_data")
        assert extra_data["request_type"] == "get_kline"
        assert extra_data["period"] == "1h"

    # ── get_server_time ───────────────────────────────────────

    def test_get_server_time(self, balancer_spot):
        """Test get_server_time returns RequestData with server_time."""
        # get_server_time builds its own RequestData, don't mock request
        with patch('bt_api_py.feeds.live_balancer.request_base.HttpClient', return_value=MagicMock()):
            inst = BalancerRequestDataSpot(Mock(), chain=MockGqlChain.MAINNET)
            result = inst.get_server_time()
            assert isinstance(result, RequestData)

    def test_get_server_time_extra_data(self, balancer_spot):
        """Test _get_server_time populates extra_data correctly."""
        with patch('bt_api_py.feeds.live_balancer.request_base.HttpClient', return_value=MagicMock()):
            inst = BalancerRequestDataSpot(Mock(), chain=MockGqlChain.MAINNET)
            path, params, extra_data = inst._get_server_time()
            assert extra_data["request_type"] == "get_server_time"
            assert extra_data["exchange_name"] == "BALANCER___DEX"
            assert "server_time" in extra_data

    # ── make_order (DEX swap) ─────────────────────────────────

    def test_make_order_calls_request(self, balancer_spot):
        """Test make_order calls self.request with swap params."""
        result = balancer_spot.make_order(
            "WETH-USDC", 1.0, 3000, "LIMIT",
            token_in="0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
            token_out="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        )
        assert balancer_spot.request.called
        call_args = balancer_spot.request.call_args
        extra_data = call_args[1].get("extra_data")
        assert extra_data["request_type"] == "make_order"
        assert extra_data["symbol_name"] == "WETH-USDC"

    def test_make_order_parses_symbol(self, balancer_spot):
        """Test _make_order parses tokenIn-tokenOut from symbol."""
        path, body, extra_data = balancer_spot._make_order(
            "0xTokenA-0xTokenB", 1.0, 100, "LIMIT"
        )
        assert extra_data["token_in"] == "0xTokenA"
        assert extra_data["token_out"] == "0xTokenB"
        assert body["amount"] == "1.0"

    # ── cancel_order ──────────────────────────────────────────

    def test_cancel_order_calls_request(self, balancer_spot):
        """Test cancel_order calls self.request."""
        result = balancer_spot.cancel_order("WETH-USDC", "tx_hash_123")
        assert balancer_spot.request.called
        call_args = balancer_spot.request.call_args
        extra_data = call_args[1].get("extra_data") or call_args[0][2] if len(call_args[0]) > 2 else call_args[1].get("extra_data")
        assert extra_data["request_type"] == "cancel_order"
        assert extra_data["order_id"] == "tx_hash_123"

    # ── query_order ───────────────────────────────────────────

    def test_query_order_calls_request(self, balancer_spot):
        """Test query_order calls self.request."""
        result = balancer_spot.query_order("WETH-USDC", "tx_hash_123")
        assert balancer_spot.request.called
        call_args = balancer_spot.request.call_args
        extra_data = call_args[1].get("extra_data") or call_args[0][2] if len(call_args[0]) > 2 else call_args[1].get("extra_data")
        assert extra_data["request_type"] == "query_order"
        assert extra_data["order_id"] == "tx_hash_123"

    # ── get_open_orders ───────────────────────────────────────

    def test_get_open_orders_calls_request(self, balancer_spot):
        """Test get_open_orders calls self.request."""
        result = balancer_spot.get_open_orders("WETH-USDC")
        assert balancer_spot.request.called
        call_args = balancer_spot.request.call_args
        extra_data = call_args[1].get("extra_data") or call_args[0][2] if len(call_args[0]) > 2 else call_args[1].get("extra_data")
        assert extra_data["request_type"] == "get_open_orders"

    # ── get_account ───────────────────────────────────────────

    def test_get_account_calls_request(self, balancer_spot):
        """Test get_account calls self.request."""
        result = balancer_spot.get_account("WETH")
        assert balancer_spot.request.called
        call_args = balancer_spot.request.call_args
        extra_data = call_args[1].get("extra_data") or call_args[0][2] if len(call_args[0]) > 2 else call_args[1].get("extra_data")
        assert extra_data["request_type"] == "get_account"
        assert extra_data["chain"] == "MAINNET"

    # ── get_balance ───────────────────────────────────────────

    def test_get_balance_calls_request(self, balancer_spot):
        """Test get_balance calls self.request."""
        result = balancer_spot.get_balance("WETH")
        assert balancer_spot.request.called
        call_args = balancer_spot.request.call_args
        extra_data = call_args[1].get("extra_data") or call_args[0][2] if len(call_args[0]) > 2 else call_args[1].get("extra_data")
        assert extra_data["request_type"] == "get_balance"
        assert extra_data["chain"] == "MAINNET"


class TestBalancerRequestMethod:
    """Test that request() properly handles GraphQL delegation."""

    def test_request_with_graphql_query(self):
        """Test request() delegates to _execute_graphql_query when _graphql_query present."""
        with patch('bt_api_py.feeds.live_balancer.request_base.HttpClient', return_value=MagicMock()):
            inst = BalancerRequestDataSpot(Mock(), chain=MockGqlChain.MAINNET)
            inst._execute_graphql_query = Mock(return_value=Mock(spec=RequestData))
            extra_data = {
                "_graphql_query": "query { test }",
                "_graphql_variables": {"var1": "val1"},
                "request_type": "test",
            }
            result = inst.request("POST /graphql", extra_data=extra_data)
            inst._execute_graphql_query.assert_called_once()

    def test_request_without_graphql_returns_request_data(self):
        """Test request() returns RequestData when no _graphql_query."""
        with patch('bt_api_py.feeds.live_balancer.request_base.HttpClient', return_value=MagicMock()):
            inst = BalancerRequestDataSpot(Mock(), chain=MockGqlChain.MAINNET)
            result = inst.request("GET /test", params={"key": "val"}, extra_data={"request_type": "test"})
            assert isinstance(result, RequestData)


class TestBalancerBaseCapabilities:
    """Test capabilities on the base class."""

    def test_base_capabilities(self):
        """Test that BalancerRequestData base class declares correct capabilities."""
        from bt_api_py.feeds.capability import Capability
        from bt_api_py.feeds.live_balancer.request_base import BalancerRequestData

        caps = BalancerRequestData._capabilities()
        assert Capability.GET_TICK in caps
        assert Capability.GET_DEPTH in caps
        assert Capability.GET_KLINE in caps
        assert Capability.GET_EXCHANGE_INFO in caps
        assert Capability.GET_BALANCE in caps
        assert Capability.GET_ACCOUNT in caps
        assert Capability.MAKE_ORDER in caps
        assert Capability.CANCEL_ORDER in caps


class TestBalancerDataContainers:
    """Test Balancer data containers init_data() returns self."""

    def test_ticker_init_data_returns_self(self):
        """Test BalancerRequestTickerData.init_data() returns self."""
        from bt_api_py.containers.tickers.balancer_ticker import BalancerRequestTickerData

        ticker_data = {
            "data": {
                "tokenGetTokenDynamicData": {
                    "price": "3000.50",
                    "priceChange24h": "50.00",
                    "marketCap": "100000000",
                    "volume24h": "50000",
                }
            }
        }
        ticker = BalancerRequestTickerData(ticker_data, "WETH", "DEX", has_been_json_encoded=True)
        result = ticker.init_data()
        assert result is ticker
        assert ticker.get_symbol_name() == "WETH"
        assert ticker.get_last_price() == 3000.50

    def test_ticker_init_data_idempotent(self):
        """Test that calling init_data() twice returns self both times."""
        from bt_api_py.containers.tickers.balancer_ticker import BalancerRequestTickerData

        ticker_data = {"price": "100.0", "priceChange24h": "1.0"}
        ticker = BalancerRequestTickerData(ticker_data, "WETH", "DEX", has_been_json_encoded=True)
        r1 = ticker.init_data()
        r2 = ticker.init_data()
        assert r1 is ticker
        assert r2 is ticker

    def test_wss_ticker_init_data_returns_self(self):
        """Test BalancerWssTickerData.init_data() returns self."""
        from bt_api_py.containers.tickers.balancer_ticker import BalancerWssTickerData

        ticker_data = {"price": "2500.0", "priceChange24h": "10.0"}
        ticker = BalancerWssTickerData(ticker_data, "WETH", "DEX", has_been_json_encoded=True)
        result = ticker.init_data()
        assert result is ticker

    def test_pool_init_data_returns_self(self):
        """Test BalancerPoolData.init_data() returns self."""
        from bt_api_py.containers.pools.balancer_pool import BalancerPoolData

        pool_data = {
            "id": "pool123",
            "address": "0xabc",
            "name": "Test Pool",
            "symbol": "TP",
            "type": "WEIGHTED",
            "version": 2,
            "allTokens": [
                {"address": "0xtoken1", "symbol": "TK1", "name": "Token1", "decimals": 18},
            ],
            "poolTokens": [
                {"address": "0xtoken1", "symbol": "TK1", "balance": "1000", "balanceUsd": "3000"},
            ],
            "dynamicData": {
                "totalLiquidity": "1000000",
                "totalShares": "500000",
                "volume24h": "50000",
                "fees24h": "150",
                "aprItems": [
                    {"title": "Swap APR", "type": "SWAP", "apr": "0.05"},
                ],
            },
        }
        pool = BalancerPoolData(pool_data, has_been_json_encoded=True)
        result = pool.init_data()
        assert result is pool
        assert pool.get_pool_id() == "pool123"
        assert pool.get_pool_name() == "Test Pool"
        assert pool.get_total_liquidity() == 1000000.0
        assert pool.get_volume_24h() == 50000.0

    def test_pool_init_data_idempotent(self):
        """Test that calling pool init_data() twice returns self both times."""
        from bt_api_py.containers.pools.balancer_pool import BalancerPoolData

        pool_data = {"id": "p1", "name": "P1"}
        pool = BalancerPoolData(pool_data, has_been_json_encoded=True)
        r1 = pool.init_data()
        r2 = pool.init_data()
        assert r1 is pool
        assert r2 is pool

    def test_wss_pool_init_data_returns_self(self):
        """Test BalancerWssPoolData.init_data() returns self."""
        from bt_api_py.containers.pools.balancer_pool import BalancerWssPoolData

        pool_data = {"id": "p1", "name": "Pool 1"}
        pool = BalancerWssPoolData(pool_data, has_been_json_encoded=True)
        result = pool.init_data()
        assert result is pool


class TestBalancerNormalizeFunctions:
    """Test normalize functions edge cases."""

    def test_tick_normalize_with_none_input(self):
        """Test tick normalize returns empty for None input."""
        result, status = BalancerRequestDataSpot._get_tick_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_tick_normalize_with_missing_data(self):
        """Test tick normalize returns empty when tokenGetTokenDynamicData is missing."""
        input_data = {"data": {}}
        result, status = BalancerRequestDataSpot._get_tick_normalize_function(input_data, None)
        assert result == []
        assert status is False

    def test_pool_normalize_with_none_input(self):
        """Test pool normalize returns empty for None input."""
        result, status = BalancerRequestDataSpot._get_pool_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_pools_normalize_with_empty_list(self):
        """Test pools normalize returns empty list for empty pools."""
        input_data = {"data": {"poolGetPools": []}}
        result, status = BalancerRequestDataSpot._get_pools_normalize_function(input_data, None)
        assert result == []
        assert status is True

    def test_swap_path_normalize_with_none_input(self):
        """Test swap path normalize returns empty for None input."""
        result, status = BalancerRequestDataSpot._get_swap_path_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_pool_events_normalize_with_empty_events(self):
        """Test pool events normalize returns empty for no events."""
        input_data = {"data": {"poolGetEvents": []}}
        result, status = BalancerRequestDataSpot._get_pool_events_normalize_function(input_data, None)
        assert result == []
        assert status is True

    def test_depth_normalize_with_none_input(self):
        """Test depth normalize returns empty for None input."""
        result, status = BalancerRequestDataSpot._get_depth_normalize_function(None, None)
        assert result == []
        assert status is False

    def test_kline_normalize_always_returns_empty(self):
        """Test kline normalize always returns empty (not supported)."""
        result, status = BalancerRequestDataSpot._get_kline_normalize_function({"data": {}}, None)
        assert result == []
        assert status is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
