"""Tests for Balancer Pool container."""

import json
import pytest

from bt_api_py.containers.pools import BalancerPoolData, BalancerWssPoolData


class TestBalancerPoolData:
    """Tests for BalancerPoolData container."""

    def test_init_with_dict(self):
        """Test initialization with dict data."""
        data = {
            "id": "pool-123",
            "address": "0xabc123",
            "name": "BTC-ETH Pool",
            "symbol": "BTC-ETH",
            "type": "WEIGHTED",
            "version": 1,
        }
        pool = BalancerPoolData(data, has_been_json_encoded=True)

        assert pool.exchange_name == "BALANCER"
        assert pool.has_been_json_encoded is True

    def test_init_with_json_string(self):
        """Test initialization with JSON string."""
        data = {
            "id": "pool-456",
            "address": "0xdef456",
            "name": "USDC-USDT Pool",
            "symbol": "USDC-USDT",
        }
        json_str = json.dumps(data)
        pool = BalancerPoolData(json_str, has_been_json_encoded=False)

        pool.init_data()

        assert pool.pool_id == "pool-456"
        assert pool.pool_address == "0xdef456"
        assert pool.pool_name == "USDC-USDT Pool"

    def test_init_data_basic_fields(self):
        """Test init_data method for basic fields."""
        data = {
            "id": "pool-789",
            "address": "0xghi789",
            "name": "Test Pool",
            "symbol": "TEST",
            "type": "STABLE",
            "version": 2,
        }
        pool = BalancerPoolData(data, has_been_json_encoded=True)

        pool.init_data()

        assert pool.pool_id == "pool-789"
        assert pool.pool_address == "0xghi789"
        assert pool.pool_name == "Test Pool"
        assert pool.pool_symbol == "TEST"
        assert pool.pool_type == "STABLE"
        assert pool.pool_version == 2

    def test_init_data_with_tokens(self):
        """Test init_data with token information."""
        data = {
            "id": "pool-token",
            "address": "0xtoken",
            "allTokens": [
                {"address": "0xtoken0", "symbol": "TKN0"},
                {"address": "0xtoken1", "symbol": "TKN1"},
            ],
        }
        pool = BalancerPoolData(data, has_been_json_encoded=True)

        pool.init_data()

        assert len(pool.tokens) == 2
        assert pool.token_addresses == ["0xtoken0", "0xtoken1"]
        assert pool.token_symbols == ["TKN0", "TKN1"]

    def test_init_data_with_dynamic_data(self):
        """Test init_data with dynamic liquidity data."""
        data = {
            "id": "pool-dynamic",
            "address": "0xdynamic",
            "dynamicData": {
                "totalLiquidity": "1000000",
                "totalShares": "500000",
                "volume24h": "50000",
                "fees24h": "100",
                "aprItems": [
                    {"title": "Trading Fees", "type": "swap", "apr": 5.5},
                    {"title": "Yield", "type": "yield", "apr": 2.5},
                ],
            },
        }
        pool = BalancerPoolData(data, has_been_json_encoded=True)

        pool.init_data()

        assert pool.total_liquidity == 1000000.0
        assert pool.total_shares == 500000.0
        assert pool.volume_24h == 50000.0
        assert pool.fees_24h == 100.0
        assert pool.total_apr == 8.0  # 5.5 + 2.5
        assert len(pool.apr_items) == 2

    def test_init_data_with_graphql_wrapper(self):
        """Test init_data with GraphQL response wrapper."""
        data = {
            "data": {
                "poolGetPool": {
                    "id": "pool-graphql",
                    "address": "0xgraphql",
                    "name": "GraphQL Pool",
                }
            }
        }
        pool = BalancerPoolData(data, has_been_json_encoded=True)

        pool.init_data()

        assert pool.pool_id == "pool-graphql"
        assert pool.pool_address == "0xgraphql"
        assert pool.pool_name == "GraphQL Pool"

    def test_init_data_idempotent(self):
        """Test that init_data is idempotent."""
        data = {"id": "pool-idem", "address": "0xidem"}
        pool = BalancerPoolData(data, has_been_json_encoded=True)

        pool.init_data()
        first_id = pool.pool_id

        pool.init_data()
        assert pool.pool_id == first_id

    def test_getter_methods(self):
        """Test all getter methods."""
        data = {
            "id": "pool-getters",
            "address": "0xgetters",
            "name": "Getters Pool",
            "symbol": "GET",
            "type": "WEIGHTED",
            "dynamicData": {
                "totalLiquidity": "100000",
                "volume24h": "10000",
                "fees24h": "50",
                "aprItems": [{"title": "APR", "type": "swap", "apr": 10.0}],
            },
            "allTokens": [{"address": "0xt1", "symbol": "T1"}],
            "poolTokens": [{"id": "pt1", "balance": "100"}],
        }
        pool = BalancerPoolData(data, has_been_json_encoded=True)
        pool.init_data()

        assert pool.get_pool_id() == "pool-getters"
        assert pool.get_pool_address() == "0xgetters"
        assert pool.get_pool_name() == "Getters Pool"
        assert pool.get_pool_symbol() == "GET"
        assert pool.get_pool_type() == "WEIGHTED"
        assert pool.get_total_liquidity() == 100000.0
        assert pool.get_volume_24h() == 10000.0
        assert pool.get_fees_24h() == 50.0
        assert pool.get_total_apr() == 10.0
        assert pool.get_apr_breakdown() == [{"title": "APR", "type": "swap", "apr": 10.0}]
        assert pool.get_tokens() == [{"address": "0xt1", "symbol": "T1"}]
        assert pool.get_token_addresses() == ["0xt1"]
        assert pool.get_token_symbols() == ["T1"]
        assert pool.get_pool_tokens_with_balances() == [{"id": "pt1", "balance": "100"}]

    def test_parse_float_static_method(self):
        """Test _parse_float static method."""
        assert BalancerPoolData._parse_float("123.45") == 123.45
        assert BalancerPoolData._parse_float(100) == 100.0
        assert BalancerPoolData._parse_float(None) is None
        assert BalancerPoolData._parse_float("invalid") is None


class TestBalancerWssPoolData:
    """Tests for BalancerWssPoolData container."""

    def test_init_data(self):
        """Test init_data method for WebSocket data."""
        data = {
            "id": "wss-pool",
            "address": "0xwss",
            "name": "WebSocket Pool",
            "symbol": "WSS",
        }
        wss_pool = BalancerWssPoolData(data, has_been_json_encoded=True)

        wss_pool.init_data()

        assert wss_pool.pool_id == "wss-pool"
        assert wss_pool.pool_address == "0xwss"
        assert wss_pool.pool_name == "WebSocket Pool"

    def test_inherits_from_balancer_pool_data(self):
        """Test that BalancerWssPoolData inherits from BalancerPoolData."""
        data = {"id": "inherit-test", "address": "0xinherit"}
        wss_pool = BalancerWssPoolData(data, has_been_json_encoded=True)

        assert isinstance(wss_pool, BalancerPoolData)
