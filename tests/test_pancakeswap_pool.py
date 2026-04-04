"""Tests for PancakeSwap pool module."""

import pytest

from bt_api_py.containers.pools.pancakeswap_pool import PancakeSwapLiquidityData, PancakeSwapPoolData


class TestPancakeSwapLiquidityData:
    """Tests for PancakeSwapLiquidityData class."""

    def test_init(self):
        """Test initialization."""
        liquidity = PancakeSwapLiquidityData(
            token0_reserve=1000.0,
            token1_reserve=2000.0,
            token0_symbol="WBNB",
            token1_symbol="USDT",
            token0_address="0x1234",
            token1_address="0x5678",
            token0_decimals=18,
            token1_decimals=18,
        )

        assert liquidity.token0_reserve == 1000.0
        assert liquidity.token1_reserve == 2000.0
        assert liquidity.token0_symbol == "WBNB"
        assert liquidity.token1_symbol == "USDT"

    def test_price(self):
        """Test price property."""
        liquidity = PancakeSwapLiquidityData(
            token0_reserve=1000.0,
            token1_reserve=2000.0,
            token0_symbol="WBNB",
            token1_symbol="USDT",
            token0_address="0x1234",
            token1_address="0x5678",
            token0_decimals=18,
            token1_decimals=18,
        )

        assert liquidity.price == 2.0

    def test_price_inverted(self):
        """Test price_inverted property."""
        liquidity = PancakeSwapLiquidityData(
            token0_reserve=1000.0,
            token1_reserve=2000.0,
            token0_symbol="WBNB",
            token1_symbol="USDT",
            token0_address="0x1234",
            token1_address="0x5678",
            token0_decimals=18,
            token1_decimals=18,
        )

        assert liquidity.price_inverted == 0.5


class TestPancakeSwapPoolData:
    """Tests for PancakeSwapPoolData class."""

    def test_init(self):
        """Test initialization."""
        liquidity = PancakeSwapLiquidityData(
            token0_reserve=1000.0,
            token1_reserve=2000.0,
            token0_symbol="WBNB",
            token1_symbol="USDT",
            token0_address="0x1234",
            token1_address="0x5678",
            token0_decimals=18,
            token1_decimals=18,
        )
        pool = PancakeSwapPoolData(
            pool_address="0xabcdef",
            symbol="WBNB-USDT",
            liquidity_data=liquidity,
            volume_24h=10000.0,
            volume_24h_usd=50000.0,
            tvl=100000.0,
        )

        assert pool.pool_address == "0xabcdef"
        assert pool.symbol == "WBNB-USDT"
        assert pool.volume_24h == 10000.0
        assert pool.tvl == 100000.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
