"""
Uniswap Pool Data Container

Standardized container for Uniswap pool information and liquidity data.
"""

from dataclasses import dataclass
from typing import List, Optional, Dict, Any
from decimal import Decimal


@dataclass
class UniswapPoolToken:
    """Container for token information within a pool."""

    address: str
    symbol: str
    name: str
    decimals: int
    reserve: Optional[Decimal] = None
    reserve_usd: Optional[Decimal] = None
    weight: Optional[Decimal] = None
    price_usd: Optional[Decimal] = None
    price_in_pool: Optional[Decimal] = None


@dataclass
class UniswapPoolFee:
    """Container for pool fee information."""

    swap_fee: Optional[Decimal] = None
    protocol_fee: Optional[Decimal] = None
    total_fee: Optional[Decimal] = None


@dataclass
class UniswapPoolStats:
    """Container for pool statistics."""

    total_value_locked_usd: Optional[Decimal] = None
    volume_usd_24h: Optional[Decimal] = None
    volume_usd_week: Optional[Decimal] = None
    fees_usd_24h: Optional[Decimal] = None
    liquidity_provider_count: int = 0
    swap_count_24h: int = 0
    apr: Optional[Decimal] = None
    apr_breakdown: Optional[Dict[str, Decimal]] = None


@dataclass
class UniswapPoolRoute:
    """Container for pool route information."""

    pool_address: str
    token_in: str
    token_out: str
    pool_fee: Optional[Decimal] = None
    hop_count: int = 1


@dataclass
class UniswapPool:
    """Container for Uniswap pool data."""

    # Basic pool information
    pool_id: str
    address: str
    name: str
    symbol: str
    pool_type: str  # V2, V3, V4, etc.

    # Tokens in the pool
    token0: Optional[UniswapPoolToken] = None
    token1: Optional[UniswapPoolToken] = None
    tokens: List[UniswapPoolToken] = None

    # Pool fees
    fees: Optional[UniswapPoolFee] = None

    # Statistics
    stats: Optional[UniswapPoolStats] = None

    # Price information
    token0_price: Optional[Decimal] = None
    token1_price: Optional[Decimal] = None
    sqrt_price: Optional[Decimal] = None

    # Liquidity
    liquidity: Optional[Decimal] = None
    tick_spacing: Optional[int] = None

    # Fee tier
    fee_tier: Optional[Decimal] = None

    # Timestamps
    created_at: Optional[float] = None
    last_updated: Optional[float] = None

    # Validation flags
    is_valid: bool = False
    has_liquidity: bool = False
    has_fees: bool = False

    def __post_init__(self):
        """Post-initialization validation and calculations."""
        if self.tokens is None:
            self.tokens = []

        # Set creation timestamp if not provided
        if self.created_at is None:
            import time
            self.created_at = time.time()
            self.last_updated = self.created_at

        # Validate pool data
        self._validate_pool()

        # Calculate derived values
        self._calculate_derived_values()

    def _validate_pool(self) -> None:
        """Validate pool data."""
        # Check if pool has at least one token
        if self.token0 is not None:
            self.is_valid = True

        # Check if pool has liquidity
        if (self.stats and
            self.stats.total_value_locked_usd and
            self.stats.total_value_locked_usd > Decimal('0')):
            self.has_liquidity = True

        # Check if pool has fees
        if self.fees and self.fees.swap_fee:
            self.has_fees = True

    def _calculate_derived_values(self) -> None:
        """Calculate derived values from pool data."""
        if self.token0 and self.token1 and self.token0_price:
            # Calculate token1 price from token0 price
            if self.token0_price > 0:
                self.token1_price = Decimal('1') / self.token0_price

    @classmethod
    def from_graphql_data(cls, data: dict) -> 'UniswapPool':
        """Create pool from GraphQL response data.

        Args:
            data: Raw GraphQL response data for pool

        Returns:
            UniswapPool instance
        """
        pool_data = data.get('pool', {}) or {}

        # Basic pool info
        pool_id = pool_data.get('id', '')
        address = pool_data.get('address', '')
        name = pool_data.get('name', '')
        symbol = pool_data.get('symbol', '')
        pool_type = pool_data.get('type', 'V3')

        # Create tokens
        token0_data = pool_data.get('token0', {}) or {}
        token1_data = pool_data.get('token1', {}) or {}

        token0 = UniswapPoolToken(
            address=token0_data.get('address', ''),
            symbol=token0_data.get('symbol', ''),
            name=token0_data.get('name', ''),
            decimals=token0_data.get('decimals', 0),
            price_usd=token0_data.get('priceUSD'),
        )

        token1 = UniswapPoolToken(
            address=token1_data.get('address', ''),
            symbol=token1_data.get('symbol', ''),
            name=token1_data.get('name', ''),
            decimals=token1_data.get('decimals', 0),
            price_usd=token1_data.get('priceUSD'),
        )

        # Liquidity reserves
        reserve0 = pool_data.get('reserve0')
        reserve1 = pool_data.get('reserve1')
        if reserve0 is not None:
            token0.reserve = Decimal(str(reserve0))
        if reserve1 is not None:
            token1.reserve = Decimal(str(reserve1))

        # Prices in pool
        token0_price = pool_data.get('token0Price')
        token1_price = pool_data.get('token1Price')
        if token0_price is not None:
            token0.price_in_pool = Decimal(str(token0_price))
        if token1_price is not None:
            token1.price_in_pool = Decimal(str(token1_price))

        # Pool fees
        fees = UniswapPoolFee(
            swap_fee=pool_data.get('swapFee'),
            protocol_fee=pool_data.get('protocolFee'),
        )

        # Statistics
        stats_data = pool_data.get('dynamicData', {}) or {}
        stats = UniswapPoolStats(
            total_value_locked_usd=Decimal(str(stats_data.get('totalValueLockedUSD', 0))) if stats_data.get('totalValueLockedUSD') else None,
            volume_usd_24h=Decimal(str(stats_data.get('volumeUSD', 0))) if stats_data.get('volumeUSD') else None,
            volume_usd_week=Decimal(str(stats_data.get('volumeUSDDay', 0))) if stats_data.get('volumeUSDDay') else None,
            fees_usd_24h=Decimal(str(stats_data.get('feesUSD', 0))) if stats_data.get('feesUSD') else None,
            liquidity_provider_count=pool_data.get('liquidityProviderCount', 0),
        )

        # Extract APR data if available
        apr_items = stats_data.get('aprItems', [])
        if apr_items:
            apr_breakdown = {}
            for item in apr_items:
                apr_breakdown[item.get('title', '')] = Decimal(str(item.get('apr', 0)))
            stats.apr_breakdown = apr_breakdown
            # Use total APR if available, otherwise calculate from breakdown
            total_apr = sum(apr_breakdown.values())
            stats.apr = total_apr

        return cls(
            pool_id=pool_id,
            address=address,
            name=name,
            symbol=symbol,
            pool_type=pool_type,
            token0=token0,
            token1=token1,
            tokens=[token0, token1],
            fees=fees,
            stats=stats,
            token0_price=token0.price_in_pool,
            token1_price=token1.price_in_pool,
        )

    def to_dict(self) -> dict:
        """Convert pool to dictionary.

        Returns:
            Dictionary representation of pool
        """
        return {
            'pool_id': self.pool_id,
            'address': self.address,
            'name': self.name,
            'symbol': self.symbol,
            'pool_type': self.pool_type,
            'token0': self.token0.to_dict() if self.token0 else None,
            'token1': self.token1.to_dict() if self.token1 else None,
            'tokens': [token.to_dict() for token in self.tokens] if self.tokens else [],
            'fees': {
                'swap_fee': str(self.fees.swap_fee) if self.fees else None,
                'protocol_fee': str(self.fees.protocol_fee) if self.fees else None,
                'total_fee': str(self.fees.total_fee) if self.fees else None,
            } if self.fees else None,
            'stats': {
                'total_value_locked_usd': str(self.stats.total_value_locked_usd) if self.stats else None,
                'volume_usd_24h': str(self.stats.volume_usd_24h) if self.stats else None,
                'volume_usd_week': str(self.stats.volume_usd_week) if self.stats else None,
                'fees_usd_24h': str(self.stats.fees_usd_24h) if self.stats else None,
                'liquidity_provider_count': self.stats.liquidity_provider_count if self.stats else 0,
                'swap_count_24h': self.stats.swap_count_24h if self.stats else 0,
                'apr': str(self.stats.apr) if self.stats else None,
                'apr_breakdown': {k: str(v) for k, v in self.stats.apr_breakdown.items()} if self.stats and self.stats.apr_breakdown else None,
            } if self.stats else None,
            'token0_price': str(self.token0_price) if self.token0_price else None,
            'token1_price': str(self.token1_price) if self.token1_price else None,
            'sqrt_price': str(self.sqrt_price) if self.sqrt_price else None,
            'liquidity': str(self.liquidity) if self.liquidity else None,
            'tick_spacing': self.tick_spacing,
            'fee_tier': str(self.fee_tier) if self.fee_tier else None,
            'created_at': self.created_at,
            'last_updated': self.last_updated,
            'is_valid': self.is_valid,
            'has_liquidity': self.has_liquidity,
            'has_fees': self.has_fees,
        }

    def get_token_balance(self, token_address: str) -> Optional[Decimal]:
        """Get balance of a specific token in the pool.

        Args:
            token_address: Address of the token

        Returns:
            Token balance in the pool
        """
        for token in self.tokens:
            if token.address.lower() == token_address.lower():
                return token.reserve
        return None

    def get_token_price(self, token_address: str) -> Optional[Decimal]:
        """Get price of a specific token in the pool.

        Args:
            token_address: Address of the token

        Returns:
            Token price in USD
        """
        for token in self.tokens:
            if token.address.lower() == token_address.lower():
                return token.price_usd
        return None

    def get_pool_value_usd(self) -> Optional[Decimal]:
        """Get total pool value in USD.

        Returns:
            Total pool value in USD
        """
        if self.stats and self.stats.total_value_locked_usd:
            return self.stats.total_value_locked_usd
        return None

    def is_liquid(self, min_tvl_usd: Decimal = Decimal('10000')) -> bool:
        """Check if pool is liquid.

        Args:
            min_tvl_usd: Minimum TVL in USD to consider liquid

        Returns:
            True if pool is liquid
        """
        return (
            self.has_liquidity and
            self.get_pool_value_usd() and
            self.get_pool_value_usd() >= min_tvl_usd
        )

    def get_fee_rate(self) -> Optional[Decimal]:
        """Get pool fee rate.

        Returns:
            Fee rate as percentage (e.g., 0.003 for 0.3%)
        """
        if self.fees and self.fees.swap_fee:
            return self.fees.swap_fee
        return None