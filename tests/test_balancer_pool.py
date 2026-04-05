"""Tests for balancer_pool module."""

from __future__ import annotations

import pytest

from bt_api_py.containers.pools.balancer_pool import BalancerPoolData


class TestBalancerPoolData:
    """Tests for BalancerPoolData class."""

    def test_init(self):
        """Test initialization."""
        pool = BalancerPoolData({"id": "pool123"}, has_been_json_encoded=True)

        assert pool.pool_info == {"id": "pool123"}
        assert pool.has_been_json_encoded is True
        assert pool.exchange_name == "BALANCER"
        assert pool.pool_id is None

    def test_init_without_json_encoded(self):
        """Test initialization without json encoded."""
        pool = BalancerPoolData('{"id": "pool123"}', has_been_json_encoded=False)

        assert pool.pool_info == '{"id": "pool123"}'
        assert pool.has_been_json_encoded is False
        assert pool.pool_data is None

    def test_init_data(self):
        """Test init_data method."""
        pool_info = {
            "data": {
                "poolGetPool": {
                    "id": "pool123",
                    "address": "0x123",
                    "name": "Test Pool",
                    "symbol": "TEST",
                    "poolType": "WEIGHTED",
                }
            }
        }
        pool = BalancerPoolData(pool_info, has_been_json_encoded=True)
        pool.init_data()

        assert pool.pool_id == "pool123"
        assert pool.pool_address == "0x123"
        assert pool.pool_name == "Test Pool"

    def test_default_values(self):
        """Test default values."""
        pool = BalancerPoolData({}, has_been_json_encoded=True)

        assert pool.tokens == []
        assert pool.token_addresses == []
        assert pool.token_symbols == []
        assert pool.total_liquidity is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
