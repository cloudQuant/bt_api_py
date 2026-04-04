"""Tests for PancakeSwap Pool containers."""

import json
import pytest

from bt_api_py.containers.pools.pancakeswap_pool import (
    PancakeSwapLiquidityData,
    PancakeSwapPoolData,
    PancakeSwapPoolList,
)


class TestPancakeSwapLiquidityData:
    """Tests for PancakeSwapLiquidityData dataclass."""

    def test_init(self):
        """Test initialization."""
        liquidity = PancakeSwapLiquidityData(
            token0_reserve=1000.0,
            token1_reserve=2000.0,
            token0_symbol="BTC",
            token1_symbol="ETH",
            token0_address="0xbtc",
            token1_address="0xeth",
            token0_decimals=18,
            token1_decimals=18,
        )

        assert liquidity.token0_reserve == 1000.0
        assert liquidity.token1_reserve == 2000.0
        assert liquidity.token0_symbol == "BTC"
        assert liquidity.token1_symbol == "ETH"

    def test_price_property(self):
        """Test price property calculation."""
        liquidity = PancakeSwapLiquidityData(
            token0_reserve=1000.0,
            token1_reserve=2000.0,
            token0_symbol="BTC",
            token1_symbol="ETH",
            token0_address="0xbtc",
            token1_address="0xeth",
            token0_decimals=18,
            token1_decimals=18,
        )

        assert liquidity.price == 2.0  # 2000 / 1000

    def test_price_inverted_property(self):
        """Test price_inverted property calculation."""
        liquidity = PancakeSwapLiquidityData(
            token0_reserve=1000.0,
            token1_reserve=2000.0,
            token0_symbol="BTC",
            token1_symbol="ETH",
            token0_address="0xbtc",
            token1_address="0xeth",
            token0_decimals=18,
            token1_decimals=18,
        )

        assert liquidity.price_inverted == 0.5  # 1000 / 2000

    def test_price_zero_reserve(self):
        """Test price with zero reserve."""
        liquidity = PancakeSwapLiquidityData(
            token0_reserve=0.0,
            token1_reserve=2000.0,
            token0_symbol="BTC",
            token1_symbol="ETH",
            token0_address="0xbtc",
            token1_address="0xeth",
            token0_decimals=18,
            token1_decimals=18,
        )

        assert liquidity.price == 0.0

    def test_reserve_usd_properties(self):
        """Test reserve_usd properties (placeholders)."""
        liquidity = PancakeSwapLiquidityData(
            token0_reserve=1000.0,
            token1_reserve=2000.0,
            token0_symbol="BTC",
            token1_symbol="ETH",
            token0_address="0xbtc",
            token1_address="0xeth",
            token0_decimals=18,
            token1_decimals=18,
        )

        # These are placeholders returning 0.0
        assert liquidity.reserve0_usd == 0.0
        assert liquidity.reserve1_usd == 0.0
        assert liquidity.total_liquidity_usd == 0.0


class TestPancakeSwapPoolData:
    """Tests for PancakeSwapPoolData dataclass."""

    @pytest.fixture
    def sample_liquidity(self):
        """Create sample liquidity data."""
        return PancakeSwapLiquidityData(
            token0_reserve=1000.0,
            token1_reserve=2000.0,
            token0_symbol="BTC",
            token1_symbol="ETH",
            token0_address="0xbtc",
            token1_address="0xeth",
            token0_decimals=18,
            token1_decimals=18,
        )

    def test_init(self, sample_liquidity):
        """Test initialization."""
        pool = PancakeSwapPoolData(
            pool_address="0xpool",
            symbol="BTC-ETH",
            liquidity_data=sample_liquidity,
            volume_24h=10000.0,
            volume_24h_usd=50000.0,
            tvl=100000.0,
        )

        assert pool.pool_address == "0xpool"
        assert pool.symbol == "BTC-ETH"
        assert pool.volume_24h == 10000.0
        assert pool.volume_24h_usd == 50000.0
        assert pool.tvl == 100000.0

    def test_post_init_negative_values(self, sample_liquidity):
        """Test __post_init__ normalizes negative values."""
        pool = PancakeSwapPoolData(
            pool_address="0xpool",
            symbol="BTC-ETH",
            liquidity_data=sample_liquidity,
            volume_24h=-100.0,
            volume_24h_usd=-50.0,
            tvl=-1000.0,
            apy=-5.0,
            fee_tier=-0.01,
            token0_price=-100.0,
            token1_price=-200.0,
            transactions_24h=-10,
        )

        assert pool.volume_24h == 0.0
        assert pool.volume_24h_usd == 0.0
        assert pool.tvl == 0.0
        assert pool.apy == 0.0
        assert pool.fee_tier == 0.0
        assert pool.token0_price == 0.0
        assert pool.token1_price == 0.0
        assert pool.transactions_24h == 0

    def test_liquidity_ratio_properties(self, sample_liquidity):
        """Test liquidity ratio calculations."""
        pool = PancakeSwapPoolData(
            pool_address="0xpool",
            symbol="BTC-ETH",
            liquidity_data=sample_liquidity,
            volume_24h=10000.0,
            volume_24h_usd=50000.0,
            tvl=100000.0,
        )

        # token0_reserve=1000, token1_reserve=2000, total=3000
        assert pool.liquidity_ratio_token0 == pytest.approx(1000.0 / 3000.0)
        assert pool.liquidity_ratio_token1 == pytest.approx(2000.0 / 3000.0)

    def test_fee_24h_usd(self, sample_liquidity):
        """Test fee_24h_usd calculation."""
        pool = PancakeSwapPoolData(
            pool_address="0xpool",
            symbol="BTC-ETH",
            liquidity_data=sample_liquidity,
            volume_24h=10000.0,
            volume_24h_usd=50000.0,
            tvl=100000.0,
            fee_tier=0.003,  # 0.3%
        )

        assert pool.fee_24h_usd == 50000.0 * 0.003

    def test_to_dict(self, sample_liquidity):
        """Test to_dict method."""
        pool = PancakeSwapPoolData(
            pool_address="0xpool",
            symbol="BTC-ETH",
            liquidity_data=sample_liquidity,
            volume_24h=10000.0,
            volume_24h_usd=50000.0,
            tvl=100000.0,
        )

        result = pool.to_dict()

        assert result["pool_address"] == "0xpool"
        assert result["symbol"] == "BTC-ETH"
        assert result["volume_24h"] == 10000.0
        assert result["tvl"] == 100000.0
        assert "liquidity_data" in result
        assert "metrics" in result

    def test_to_json(self, sample_liquidity):
        """Test to_json method."""
        pool = PancakeSwapPoolData(
            pool_address="0xpool",
            symbol="BTC-ETH",
            liquidity_data=sample_liquidity,
            volume_24h=10000.0,
            volume_24h_usd=50000.0,
            tvl=100000.0,
        )

        json_str = pool.to_json()
        parsed = json.loads(json_str)

        assert parsed["pool_address"] == "0xpool"

    def test_from_dict(self, sample_liquidity):
        """Test from_dict class method."""
        data = {
            "pool_address": "0xpool",
            "symbol": "BTC-ETH",
            "liquidity_data": {
                "token0_reserve": 1000.0,
                "token1_reserve": 2000.0,
                "token0_symbol": "BTC",
                "token1_symbol": "ETH",
                "token0_address": "0xbtc",
                "token1_address": "0xeth",
                "token0_decimals": 18,
                "token1_decimals": 18,
            },
            "volume_24h": 10000.0,
            "volume_24h_usd": 50000.0,
            "tvl": 100000.0,
        }

        pool = PancakeSwapPoolData.from_dict(data)

        assert pool.pool_address == "0xpool"
        assert pool.symbol == "BTC-ETH"
        assert pool.volume_24h == 10000.0

    def test_from_json(self, sample_liquidity):
        """Test from_json class method."""
        json_str = json.dumps({
            "pool_address": "0xpool",
            "symbol": "BTC-ETH",
            "liquidity_data": {
                "token0_reserve": 1000.0,
                "token1_reserve": 2000.0,
                "token0_symbol": "BTC",
                "token1_symbol": "ETH",
                "token0_address": "0xbtc",
                "token1_address": "0xeth",
                "token0_decimals": 18,
                "token1_decimals": 18,
            },
            "volume_24h": 10000.0,
            "volume_24h_usd": 50000.0,
            "tvl": 100000.0,
        })

        pool = PancakeSwapPoolData.from_json(json_str)

        assert pool.pool_address == "0xpool"


class TestPancakeSwapPoolList:
    """Tests for PancakeSwapPoolList dataclass."""

    @pytest.fixture
    def sample_pool(self):
        """Create sample pool data."""
        liquidity = PancakeSwapLiquidityData(
            token0_reserve=1000.0,
            token1_reserve=2000.0,
            token0_symbol="BTC",
            token1_symbol="ETH",
            token0_address="0xbtc",
            token1_address="0xeth",
            token0_decimals=18,
            token1_decimals=18,
        )
        return PancakeSwapPoolData(
            pool_address="0xpool1",
            symbol="BTC-ETH",
            liquidity_data=liquidity,
            volume_24h=10000.0,
            volume_24h_usd=50000.0,
            tvl=100000.0,
        )

    @pytest.fixture
    def sample_pool2(self):
        """Create second sample pool data."""
        liquidity = PancakeSwapLiquidityData(
            token0_reserve=500.0,
            token1_reserve=1000.0,
            token0_symbol="USDC",
            token1_symbol="USDT",
            token0_address="0xusdc",
            token1_address="0xusdt",
            token0_decimals=6,
            token1_decimals=6,
        )
        return PancakeSwapPoolData(
            pool_address="0xpool2",
            symbol="USDC-USDT",
            liquidity_data=liquidity,
            volume_24h=5000.0,
            volume_24h_usd=25000.0,
            tvl=50000.0,
        )

    def test_init(self, sample_pool, sample_pool2):
        """Test initialization."""
        pool_list = PancakeSwapPoolList(
            pools=[sample_pool, sample_pool2],
            total_pools=2,
            total_liquidity_usd=0.0,
            total_volume_24h_usd=0.0,
        )

        assert len(pool_list.pools) == 2
        assert pool_list.total_pools == 2
        assert pool_list.total_liquidity_usd == 150000.0  # 100000 + 50000
        assert pool_list.total_volume_24h_usd == 75000.0  # 50000 + 25000

    def test_init_empty_list(self):
        """Test initialization with empty list."""
        pool_list = PancakeSwapPoolList(
            pools=[],
            total_pools=0,
            total_liquidity_usd=0.0,
            total_volume_24h_usd=0.0,
        )

        assert pool_list.total_pools == 0
        assert pool_list.total_liquidity_usd == 0.0
        assert pool_list.total_volume_24h_usd == 0.0

    def test_filter_by_tvl(self, sample_pool, sample_pool2):
        """Test filter_by_tvl method."""
        pool_list = PancakeSwapPoolList(
            pools=[sample_pool, sample_pool2],
            total_pools=2,
            total_liquidity_usd=0.0,
            total_volume_24h_usd=0.0,
        )

        filtered = pool_list.filter_by_tvl(min_tvl=75000.0)

        assert len(filtered.pools) == 1
        assert filtered.pools[0].symbol == "BTC-ETH"

    def test_filter_by_tvl_with_max(self, sample_pool, sample_pool2):
        """Test filter_by_tvl with max_tvl."""
        pool_list = PancakeSwapPoolList(
            pools=[sample_pool, sample_pool2],
            total_pools=2,
            total_liquidity_usd=0.0,
            total_volume_24h_usd=0.0,
        )

        filtered = pool_list.filter_by_tvl(min_tvl=25000.0, max_tvl=75000.0)

        assert len(filtered.pools) == 1
        assert filtered.pools[0].symbol == "USDC-USDT"

    def test_filter_by_volume(self, sample_pool, sample_pool2):
        """Test filter_by_volume method."""
        pool_list = PancakeSwapPoolList(
            pools=[sample_pool, sample_pool2],
            total_pools=2,
            total_liquidity_usd=0.0,
            total_volume_24h_usd=0.0,
        )

        filtered = pool_list.filter_by_volume(min_volume=40000.0)

        assert len(filtered.pools) == 1
        assert filtered.pools[0].symbol == "BTC-ETH"

    def test_filter_by_symbol(self, sample_pool, sample_pool2):
        """Test filter_by_symbol method."""
        pool_list = PancakeSwapPoolList(
            pools=[sample_pool, sample_pool2],
            total_pools=2,
            total_liquidity_usd=0.0,
            total_volume_24h_usd=0.0,
        )

        filtered = pool_list.filter_by_symbol("USDC-USDT")

        assert len(filtered.pools) == 1
        assert filtered.pools[0].symbol == "USDC-USDT"

    def test_sort_by_tvl(self, sample_pool, sample_pool2):
        """Test sort_by_tvl method."""
        pool_list = PancakeSwapPoolList(
            pools=[sample_pool2, sample_pool],  # Reverse order
            total_pools=2,
            total_liquidity_usd=0.0,
            total_volume_24h_usd=0.0,
        )

        sorted_list = pool_list.sort_by_tvl(descending=True)

        assert sorted_list.pools[0].symbol == "BTC-ETH"  # Higher TVL first
        assert sorted_list.pools[1].symbol == "USDC-USDT"

    def test_sort_by_volume(self, sample_pool, sample_pool2):
        """Test sort_by_volume method."""
        pool_list = PancakeSwapPoolList(
            pools=[sample_pool2, sample_pool],  # Reverse order
            total_pools=2,
            total_liquidity_usd=0.0,
            total_volume_24h_usd=0.0,
        )

        sorted_list = pool_list.sort_by_volume(descending=True)

        assert sorted_list.pools[0].symbol == "BTC-ETH"  # Higher volume first

    def test_to_dict(self, sample_pool, sample_pool2):
        """Test to_dict method."""
        pool_list = PancakeSwapPoolList(
            pools=[sample_pool, sample_pool2],
            total_pools=2,
            total_liquidity_usd=0.0,
            total_volume_24h_usd=0.0,
        )

        result = pool_list.to_dict()

        assert result["total_pools"] == 2
        assert len(result["pools"]) == 2

    def test_to_json(self, sample_pool, sample_pool2):
        """Test to_json method."""
        pool_list = PancakeSwapPoolList(
            pools=[sample_pool, sample_pool2],
            total_pools=2,
            total_liquidity_usd=0.0,
            total_volume_24h_usd=0.0,
        )

        json_str = pool_list.to_json()
        parsed = json.loads(json_str)

        assert parsed["total_pools"] == 2

    def test_from_dict(self, sample_pool):
        """Test from_dict class method."""
        data = {
            "pools": [sample_pool.to_dict()],
            "total_pools": 1,
            "total_liquidity_usd": 100000.0,
            "total_volume_24h_usd": 50000.0,
        }

        pool_list = PancakeSwapPoolList.from_dict(data)

        assert pool_list.total_pools == 1
        assert len(pool_list.pools) == 1

    def test_from_json(self, sample_pool):
        """Test from_json class method."""
        json_str = json.dumps({
            "pools": [sample_pool.to_dict()],
            "total_pools": 1,
            "total_liquidity_usd": 100000.0,
            "total_volume_24h_usd": 50000.0,
        })

        pool_list = PancakeSwapPoolList.from_json(json_str)

        assert pool_list.total_pools == 1
