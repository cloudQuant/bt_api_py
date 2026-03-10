"""
PancakeSwap Pool Data Container

Provides standardized pool data structure for PancakeSwap
"""

from dataclasses import dataclass


@dataclass
class PancakeSwapLiquidityData:
    """PancakeSwap Liquidity Data"""

    token0_reserve: float
    token1_reserve: float
    token0_symbol: str
    token1_symbol: str
    token0_address: str
    token1_address: str
    token0_decimals: int
    token1_decimals: int

    @property
    def reserve0_usd(self) -> float:
        """Token0 reserve in USD (requires price oracle)"""
        # Placeholder - would need price data
        return 0.0

    @property
    def reserve1_usd(self) -> float:
        """Token1 reserve in USD (requires price oracle)"""
        # Placeholder - would need price data
        return 0.0

    @property
    def total_liquidity_usd(self) -> float:
        """Total liquidity in USD"""
        return self.reserve0_usd + self.reserve1_usd

    @property
    def price(self) -> float:
        """Price of token1 in terms of token0"""
        if self.token0_reserve > 0:
            return self.token1_reserve / self.token0_reserve
        return 0.0

    @property
    def price_inverted(self) -> float:
        """Price of token0 in terms of token1"""
        if self.token1_reserve > 0:
            return self.token0_reserve / self.token1_reserve
        return 0.0


@dataclass
class PancakeSwapPoolData:
    """PancakeSwap Pool Data"""

    pool_address: str
    symbol: str
    liquidity_data: PancakeSwapLiquidityData
    volume_24h: float
    volume_24h_usd: float
    tvl: float  # Total Value Locked in USD
    apy: float | None = None  # Annual Percentage Yield
    fee_tier: float = 0.0025  # 0.25% default fee
    token0_price: float | None = None
    token1_price: float | None = None
    transactions_24h: int | None = None
    created_at: int | None = None
    is_v3: bool = False

    def __post_init__(self):
        """Validate and normalize data after initialization"""
        if self.volume_24h < 0:
            self.volume_24h = 0.0
        if self.volume_24h_usd < 0:
            self.volume_24h_usd = 0.0
        if self.tvl < 0:
            self.tvl = 0.0
        if self.apy is not None and self.apy < 0:
            self.apy = 0.0
        if self.fee_tier < 0:
            self.fee_tier = 0.0
        if self.token0_price is not None and self.token0_price < 0:
            self.token0_price = 0.0
        if self.token1_price is not None and self.token1_price < 0:
            self.token1_price = 0.0
        if self.transactions_24h is not None and self.transactions_24h < 0:
            self.transactions_24h = 0

    @property
    def daily_volume_token0(self) -> float:
        """Daily volume in token0 units"""
        # Placeholder calculation
        return self.volume_24h / 2  # Simplified

    @property
    def daily_volume_token1(self) -> float:
        """Daily volume in token1 units"""
        # Placeholder calculation
        return self.volume_24h / 2  # Simplified

    @property
    def liquidity_ratio_token0(self) -> float:
        """Ratio of token0 in total liquidity"""
        if self.liquidity_data.token0_reserve + self.liquidity_data.token1_reserve > 0:
            return self.liquidity_data.token0_reserve / (
                self.liquidity_data.token0_reserve + self.liquidity_data.token1_reserve
            )
        return 0.0

    @property
    def liquidity_ratio_token1(self) -> float:
        """Ratio of token1 in total liquidity"""
        if self.liquidity_data.token0_reserve + self.liquidity_data.token1_reserve > 0:
            return self.liquidity_data.token1_reserve / (
                self.liquidity_data.token0_reserve + self.liquidity_data.token1_reserve
            )
        return 0.0

    @property
    def fee_24h_usd(self) -> float:
        """Fees collected in last 24 hours (USD)"""
        if self.volume_24h_usd > 0:
            return self.volume_24h_usd * self.fee_tier
        return 0.0

    @property
    def fee_24h_token0(self) -> float:
        """Fees collected in last 24 hours (token0)"""
        if self.volume_24h > 0:
            return self.volume_24h * self.fee_tier * self.liquidity_ratio_token0
        return 0.0

    @property
    def fee_24h_token1(self) -> float:
        """Fees collected in last 24 hours (token1)"""
        if self.volume_24h > 0:
            return self.volume_24h * self.fee_tier * self.liquidity_ratio_token1
        return 0.0

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization"""
        return {
            "pool_address": self.pool_address,
            "symbol": self.symbol,
            "liquidity_data": {
                "token0_reserve": self.liquidity_data.token0_reserve,
                "token1_reserve": self.liquidity_data.token1_reserve,
                "token0_symbol": self.liquidity_data.token0_symbol,
                "token1_symbol": self.liquidity_data.token1_symbol,
                "token0_address": self.liquidity_data.token0_address,
                "token1_address": self.liquidity_data.token1_address,
                "token0_decimals": self.liquidity_data.token0_decimals,
                "token1_decimals": self.liquidity_data.token1_decimals,
            },
            "volume_24h": self.volume_24h,
            "volume_24h_usd": self.volume_24h_usd,
            "tvl": self.tvl,
            "apy": self.apy,
            "fee_tier": self.fee_tier,
            "token0_price": self.token0_price,
            "token1_price": self.token1_price,
            "transactions_24h": self.transactions_24h,
            "created_at": self.created_at,
            "is_v3": self.is_v3,
            "metrics": {
                "daily_volume_token0": self.daily_volume_token0,
                "daily_volume_token1": self.daily_volume_token1,
                "liquidity_ratio_token0": self.liquidity_ratio_token0,
                "liquidity_ratio_token1": self.liquidity_ratio_token1,
                "fee_24h_usd": self.fee_24h_usd,
                "fee_24h_token0": self.fee_24h_token0,
                "fee_24h_token1": self.fee_24h_token1,
            },
        }

    def to_json(self) -> str:
        """Convert to JSON string"""
        import json

        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict) -> "PancakeSwapPoolData":
        """Create from dictionary"""
        liquidity_data = PancakeSwapLiquidityData(**data["liquidity_data"])
        return cls(
            pool_address=data["pool_address"],
            symbol=data["symbol"],
            liquidity_data=liquidity_data,
            volume_24h=data["volume_24h"],
            volume_24h_usd=data["volume_24h_usd"],
            tvl=data["tvl"],
            apy=data.get("apy"),
            fee_tier=data.get("fee_tier", 0.0025),
            token0_price=data.get("token0_price"),
            token1_price=data.get("token1_price"),
            transactions_24h=data.get("transactions_24h"),
            created_at=data.get("created_at"),
            is_v3=data.get("is_v3", False),
        )

    @classmethod
    def from_json(cls, json_str: str) -> "PancakeSwapPoolData":
        """Create from JSON string"""
        import json

        data = json.loads(json_str)
        return cls.from_dict(data)


@dataclass
class PancakeSwapPoolList:
    """List of PancakeSwap Pools"""

    pools: list[PancakeSwapPoolData]
    total_pools: int
    total_liquidity_usd: float
    total_volume_24h_usd: float

    def __post_init__(self):
        """Calculate totals after initialization"""
        self.total_pools = len(self.pools)

        if self.pools:
            self.total_liquidity_usd = sum(pool.tvl for pool in self.pools)
            self.total_volume_24h_usd = sum(pool.volume_24h_usd for pool in self.pools)
        else:
            self.total_liquidity_usd = 0.0
            self.total_volume_24h_usd = 0.0

    def filter_by_tvl(self, min_tvl: float, max_tvl: float | None = None) -> "PancakeSwapPoolList":
        """Filter pools by TVL range"""
        filtered = []
        for pool in self.pools:
            if pool.tvl >= min_tvl and (max_tvl is None or pool.tvl <= max_tvl):
                filtered.append(pool)

        return PancakeSwapPoolList(
            pools=filtered,
            total_pools=len(filtered),
            total_liquidity_usd=sum(p.tvl for p in filtered),
            total_volume_24h_usd=sum(p.volume_24h_usd for p in filtered),
        )

    def filter_by_volume(
        self, min_volume: float, max_volume: float | None = None
    ) -> "PancakeSwapPoolList":
        """Filter pools by 24h volume range"""
        filtered = []
        for pool in self.pools:
            if pool.volume_24h_usd >= min_volume and (
                max_volume is None or pool.volume_24h_usd <= max_volume
            ):
                filtered.append(pool)

        return PancakeSwapPoolList(
            pools=filtered,
            total_pools=len(filtered),
            total_liquidity_usd=sum(p.tvl for p in filtered),
            total_volume_24h_usd=sum(p.volume_24h_usd for p in filtered),
        )

    def filter_by_symbol(self, symbol: str) -> "PancakeSwapPoolList":
        """Filter pools by symbol"""
        filtered = [pool for pool in self.pools if pool.symbol == symbol]
        return PancakeSwapPoolList(
            pools=filtered,
            total_pools=len(filtered),
            total_liquidity_usd=sum(p.tvl for p in filtered),
            total_volume_24h_usd=sum(p.volume_24h_usd for p in filtered),
        )

    def sort_by_tvl(self, descending: bool = True) -> "PancakeSwapPoolList":
        """Sort pools by TVL"""
        sorted_pools = sorted(self.pools, key=lambda p: p.tvl, reverse=descending)
        return PancakeSwapPoolList(
            pools=sorted_pools,
            total_pools=len(sorted_pools),
            total_liquidity_usd=sum(p.tvl for p in sorted_pools),
            total_volume_24h_usd=sum(p.volume_24h_usd for p in sorted_pools),
        )

    def sort_by_volume(self, descending: bool = True) -> "PancakeSwapPoolList":
        """Sort pools by 24h volume"""
        sorted_pools = sorted(self.pools, key=lambda p: p.volume_24h_usd, reverse=descending)
        return PancakeSwapPoolList(
            pools=sorted_pools,
            total_pools=len(sorted_pools),
            total_liquidity_usd=sum(p.tvl for p in sorted_pools),
            total_volume_24h_usd=sum(p.volume_24h_usd for p in sorted_pools),
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization"""
        return {
            "pools": [pool.to_dict() for pool in self.pools],
            "total_pools": self.total_pools,
            "total_liquidity_usd": self.total_liquidity_usd,
            "total_volume_24h_usd": self.total_volume_24h_usd,
        }

    def to_json(self) -> str:
        """Convert to JSON string"""
        import json

        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict) -> "PancakeSwapPoolList":
        """Create from dictionary"""
        pools = [PancakeSwapPoolData.from_dict(pool_data) for pool_data in data["pools"]]
        return cls(
            pools=pools,
            total_pools=data["total_pools"],
            total_liquidity_usd=data["total_liquidity_usd"],
            total_volume_24h_usd=data["total_volume_24h_usd"],
        )

    @classmethod
    def from_json(cls, json_str: str) -> "PancakeSwapPoolList":
        """Create from JSON string"""
        import json

        data = json.loads(json_str)
        return cls.from_dict(data)
