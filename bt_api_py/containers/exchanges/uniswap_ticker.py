"""Uniswap Ticker Data Container.

Standardized container for Uniswap token price and market data.
"""

from dataclasses import dataclass


@dataclass
class UniswapTicker:
    """Container for Uniswap token ticker data."""

    # Basic token information
    symbol: str
    name: str
    address: str
    decimals: int

    # Price data
    price: float | None = None
    price_change_24h: float | None = None
    price_change_percentage_24h: float | None = None

    # Volume data
    volume_24h: float | None = None
    volume_24h_usd: float | None = None

    # Market cap
    market_cap: float | None = None

    # Liquidity
    total_liquidity_usd: float | None = None

    # Timestamps
    timestamp: float | None = None
    last_updated: float | None = None

    # Trading information
    is_trading: bool = True
    trading_pairs_count: int = 0

    # Validation flags
    is_valid_price: bool = False
    is_valid_volume: bool = False
    has_liquidity: bool = False

    def __post_init__(self):
        """Post-initialization validation and calculations."""
        # Validate price
        if self.price is not None and self.price > 0:
            self.is_valid_price = True

        # Validate volume
        if self.volume_24h is not None and self.volume_24h > 0:
            self.is_valid_volume = True
            if self.price is not None:
                self.volume_24h_usd = self.volume_24h * self.price

        # Check liquidity
        if self.total_liquidity_usd is not None and self.total_liquidity_usd > 0:
            self.has_liquidity = True

        # Calculate percentage change if we have both price and change
        if self.price is not None and self.price_change_24h is not None:
            if self.price > 0:
                self.price_change_percentage_24h = (self.price_change_24h / self.price) * 100

        # Set timestamp if not provided
        if self.timestamp is None:
            import time

            self.timestamp = time.time()
            self.last_updated = self.timestamp

    @classmethod
    def from_graphql_data(cls, data: dict) -> "UniswapTicker":
        """Create ticker from GraphQL response data.

        Args:
            data: Raw GraphQL response data for token

        Returns:
            UniswapTicker instance

        """
        # Extract token basic info
        token_info = data.get("token", {}) or {}
        symbol = token_info.get("symbol", "")
        name = token_info.get("name", "")
        address = token_info.get("id", "")  # GraphQL 'id' is the address
        decimals = token_info.get("decimals", 0)

        # Extract price data
        price_data = token_info.get("price", {}) or {}
        price = float(price_data.get("USD", 0)) if price_data.get("USD") else None
        price_change_24h = (
            float(token_info.get("priceChange24h", {}).get("USD", 0))
            if token_info.get("priceChange24h", {}).get("USD")
            else None
        )

        # Extract volume data
        volume_data = token_info.get("volume", {}) or {}
        volume_24h = float(volume_data.get("USD", 0)) if volume_data.get("USD") else None

        # Extract market cap
        market_cap_data = token_info.get("marketCap", {}) or {}
        market_cap = float(market_cap_data.get("USD", 0)) if market_cap_data.get("USD") else None

        return cls(
            symbol=symbol,
            name=name,
            address=address,
            decimals=decimals,
            price=price,
            price_change_24h=price_change_24h,
            volume_24h=volume_24h,
            market_cap=market_cap,
        )

    def to_dict(self) -> dict:
        """Convert ticker to dictionary.

        Returns:
            Dictionary representation of ticker

        """
        return {
            "symbol": self.symbol,
            "name": self.name,
            "address": self.address,
            "decimals": self.decimals,
            "price": self.price,
            "price_change_24h": self.price_change_24h,
            "price_change_percentage_24h": self.price_change_percentage_24h,
            "volume_24h": self.volume_24h,
            "volume_24h_usd": self.volume_24h_usd,
            "market_cap": self.market_cap,
            "total_liquidity_usd": self.total_liquidity_usd,
            "timestamp": self.timestamp,
            "last_updated": self.last_updated,
            "is_trading": self.is_trading,
            "trading_pairs_count": self.trading_pairs_count,
            "is_valid_price": self.is_valid_price,
            "is_valid_volume": self.is_valid_volume,
            "has_liquidity": self.has_liquidity,
        }

    def is_price_valid(self) -> bool:
        """Check if price data is valid.

        Returns:
            True if price is valid

        """
        return self.is_valid_price and self.price is not None and self.price > 0

    def is_volume_valid(self) -> bool:
        """Check if volume data is valid.

        Returns:
            True if volume is valid

        """
        return self.is_valid_volume and self.volume_24h is not None and self.volume_24h > 0

    def update_price(self, new_price: float) -> None:
        """Update price and related fields.

        Args:
            new_price: New price value

        """
        if new_price > 0:
            old_price = self.price
            self.price = new_price
            self.is_valid_price = True

            # Update price change if we have old price
            if old_price is not None and old_price > 0:
                self.price_change_24h = new_price - old_price
                self.price_change_percentage_24h = ((new_price - old_price) / old_price) * 100

        self.last_updated = None
        import time

        self.timestamp = time.time()
        self.last_updated = self.timestamp
