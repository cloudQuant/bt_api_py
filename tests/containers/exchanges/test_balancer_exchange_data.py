"""Tests for BalancerExchangeData container."""

from __future__ import annotations

from bt_api_py.containers.exchanges.balancer_exchange_data import BalancerExchangeData, GqlChain


class TestBalancerExchangeData:
    """Tests for BalancerExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = BalancerExchangeData()

        assert exchange.get_rest_url() == "https://api-v3.balancer.fi"
        assert exchange.get_chain_value() == GqlChain.MAINNET.value

    def test_default_rest_path_and_subgraph(self):
        exchange = BalancerExchangeData(chain=GqlChain.POLYGON)

        assert exchange.get_rest_path("get_pools") == "POST https://api-v3.balancer.fi/graphql"
        assert "polygon" in exchange.get_subgraph_url().lower()

    def test_symbol_and_pool_passthrough(self):
        exchange = BalancerExchangeData()
        symbol = "0xBA12222222228d8Ba445958a75a0704d566BF2C8"

        assert exchange.get_symbol(symbol) == symbol
        assert exchange.get_pool_id("pool-id") == "pool-id"

    def test_graphql_query_and_path_accessors(self):
        exchange = BalancerExchangeData()
        exchange.graphql_queries = {"get_pools": "query { pools { id } }"}
        exchange.graphql_paths = {"query_pools": "/graphql"}

        assert exchange.get_graphql_query("get_pools") == "query { pools { id } }"
        assert exchange.get_graphql_query("missing") is None
        assert exchange.get_graphql_path("query_pools") == "/graphql"
        assert exchange.get_graphql_path("missing") is None
