"""Uniswap Quote Data Container.

Standardized container for Uniswap swap quotes and routing information.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass
class UniswapQuoteToken:
    """Container for token information in a quote."""

    address: str
    symbol: str
    name: str


@dataclass
class UniswapQuoteRoute:
    """Container for route information in a quote."""

    pool_address: str
    token_in: str
    token_out: str
    fee_tier: Decimal | None = None
    hop_count: int = 1
    input_amount: Decimal | None = None
    output_amount: Decimal | None = None


@dataclass
class UniswapQuote:
    """Container for Uniswap swap quote data."""

    # Basic quote information
    quote_id: str
    token_in: UniswapQuoteToken
    token_out: UniswapQuoteToken
    swap_type: str  # EXACT_IN or EXACT_OUT

    # Amounts
    amount_in: Decimal
    amount_out: Decimal
    estimated_gas: int | None = None

    # Price impact
    price_impact: Decimal | None = None
    price_impact_percentage: Decimal | None = None

    # Routes
    routes: list[UniswapQuoteRoute] | None = None
    best_route: UniswapQuoteRoute | None = None

    # Timestamps
    created_at: float | None = None
    expires_at: float | None = None

    # Quote status
    is_valid: bool = True
    is_expired: bool = False
    has_price_impact_warning: bool = False
    price_impact_threshold: Decimal = Decimal("0.5")  # 0.5%

    def __post_init__(self):
        """Post-initialization validation and calculations."""
        if self.routes is None:
            self.routes = []

        # Set creation timestamp
        if self.created_at is None:
            import time

            self.created_at = time.time()
            # Quote expires in 60 seconds by default
            self.expires_at = self.created_at + 60

        # Calculate price impact percentage
        if self.price_impact is not None and self.amount_in > 0:
            self.price_impact_percentage = Decimal(
                str((abs(float(self.price_impact)) / float(self.amount_in)) * 100)
            )

        # Check price impact warning
        if (
            self.price_impact_threshold
            and self.price_impact_percentage
            and abs(self.price_impact_percentage) > self.price_impact_threshold
        ):
            self.has_price_impact_warning = True

        # Check if quote is expired
        self._check_expiry()

    def _check_expiry(self) -> None:
        """Check if quote has expired."""
        if self.expires_at:
            import time

            if time.time() > self.expires_at:
                self.is_expired = True
                self.is_valid = False

    @classmethod
    def from_graphql_data(cls, data: dict) -> UniswapQuote:
        """Create quote from GraphQL response data.

        Args:
            data: Raw GraphQL response data for quote

        Returns:
            UniswapQuote instance

        """
        quote_data = data.get("quote", {}) or {}

        # Token information
        token_in_data = quote_data.get("tokenIn", {}) or {}
        token_out_data = quote_data.get("tokenOut", {}) or {}

        token_in = UniswapQuoteToken(
            address=token_in_data.get("address", ""),
            symbol=token_in_data.get("symbol", ""),
            name=token_in_data.get("name", ""),
        )

        token_out = UniswapQuoteToken(
            address=token_out_data.get("address", ""),
            symbol=token_out_data.get("symbol", ""),
            name=token_out_data.get("name", ""),
        )

        # Amounts
        amount_in = Decimal(str(quote_data.get("amountIn", 0)))
        amount_out = Decimal(str(quote_data.get("amountOut", 0)))
        estimated_gas = (
            int(quote_data.get("estimatedGas", 0)) if quote_data.get("estimatedGas") else None
        )

        # Price impact
        price_impact = (
            Decimal(str(quote_data.get("priceImpact", 0)))
            if quote_data.get("priceImpact")
            else None
        )

        # Routes
        routes = []
        route_data_list = quote_data.get("route", {}).get("segments", [])
        for segment_data in route_data_list:
            pool_data = segment_data.get("pool", {}) or {}
            route = UniswapQuoteRoute(
                pool_address=pool_data.get("address", ""),
                token_in=segment_data.get("tokenIn", ""),
                token_out=segment_data.get("tokenOut", ""),
            )
            routes.append(route)

        import time

        return cls(
            quote_id=f"quote_{time.time()}",
            token_in=token_in,
            token_out=token_out,
            swap_type="EXACT_IN",  # Default, can be determined from context
            amount_in=Decimal(str(amount_in)),
            amount_out=Decimal(str(amount_out)),
            estimated_gas=estimated_gas,
            price_impact=Decimal(str(price_impact)) if price_impact else None,
            routes=routes,
            best_route=routes[0] if routes else None,
        )

    def to_dict(self) -> dict:
        """Convert quote to dictionary.

        Returns:
            Dictionary representation of quote

        """
        return {
            "quote_id": self.quote_id,
            "token_in": {
                "address": self.token_in.address,
                "symbol": self.token_in.symbol,
                "name": self.token_in.name,
            },
            "token_out": {
                "address": self.token_out.address,
                "symbol": self.token_out.symbol,
                "name": self.token_out.name,
            },
            "swap_type": self.swap_type,
            "amount_in": str(self.amount_in),
            "amount_out": str(self.amount_out),
            "estimated_gas": self.estimated_gas,
            "price_impact": str(self.price_impact) if self.price_impact else None,
            "price_impact_percentage": str(self.price_impact_percentage)
            if self.price_impact_percentage
            else None,
            "routes": [
                {
                    "pool_address": route.pool_address,
                    "token_in": route.token_in,
                    "token_out": route.token_out,
                    "fee_tier": str(route.fee_tier) if route.fee_tier else None,
                    "hop_count": route.hop_count,
                    "input_amount": str(route.input_amount) if route.input_amount else None,
                    "output_amount": str(route.output_amount) if route.output_amount else None,
                }
                for route in (self.routes or [])
            ],
            "best_route": {
                "pool_address": self.best_route.pool_address,
                "token_in": self.best_route.token_in,
                "token_out": self.best_route.token_out,
                "fee_tier": str(self.best_route.fee_tier) if self.best_route.fee_tier else None,
                "hop_count": self.best_route.hop_count,
            }
            if self.best_route
            else None,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "is_valid": self.is_valid,
            "is_expired": self.is_expired,
            "has_price_impact_warning": self.has_price_impact_warning,
            "price_impact_threshold": str(self.price_impact_threshold),
        }

    def get_output_amount(self, input_amount: Decimal) -> Decimal | None:
        """Get output amount for given input amount using current quote.

        Args:
            input_amount: Input amount

        Returns:
            Output amount based on current price impact and fees

        """
        if not self.is_valid or self.is_expired:
            return None

        # Simple proportional calculation
        # In reality, this should account for price impact and fees more accurately
        ratio = self.amount_out / self.amount_in if self.amount_in > 0 else Decimal("0")
        return input_amount * ratio

    def get_effective_rate(self) -> Decimal | None:
        """Get effective exchange rate (output/input).

        Returns:
            Exchange rate as decimal

        """
        if self.amount_in > 0:
            return self.amount_out / self.amount_in
        return None

    def get_slippage_adjusted_amount_out(self, slippage_tolerance: Decimal) -> Decimal | None:
        """Get output amount adjusted for slippage tolerance.

        Args:
            slippage_tolerance: Slippage tolerance as percentage (e.g., 0.5 for 0.5%)

        Returns:
            Output amount adjusted for slippage

        """
        if not self.is_valid or self.is_expired:
            return None

        slippage_factor = Decimal("1") - (slippage_tolerance / Decimal("100"))
        return self.amount_out * slippage_factor

    def get_total_cost(self, gas_price_gwei: Decimal = Decimal("20")) -> Decimal | None:
        """Get total cost including gas fees.

        Args:
            gas_price_gwei: Gas price in Gwei

        Returns:
            Total cost in input token amount

        """
        if not self.estimated_gas or not gas_price_gwei:
            return None

        # Convert gas price from Gwei to Wei (1 ETH = 1e18 wei, 1 Gwei = 1e9 wei)
        gas_cost_wei = self.estimated_gas * (gas_price_gwei * Decimal("1e9"))

        # Convert to decimal for calculation
        # Note: This assumes input token is ETH or has similar decimals
        # In practice, you'd need to convert gas cost to input token
        return Decimal(str(gas_cost_wei))

    def is_good_price(self, max_slippage: Decimal = Decimal("1")) -> bool:
        """Check if quote has acceptable price.

        Args:
            max_slippage: Maximum acceptable slippage percentage

        Returns:
            True if quote price is acceptable

        """
        if self.price_impact_percentage:
            return abs(self.price_impact_percentage) <= max_slippage
        return True

    def refresh_expiry(self, expiry_seconds: int = 60) -> None:
        """Refresh quote expiry time.

        Args:
            expiry_seconds: Number of seconds until expiry

        """
        import time

        self.created_at = time.time()
        self.expires_at = self.created_at + expiry_seconds
        self.is_expired = False

    def add_route(self, route: UniswapQuoteRoute) -> None:
        """Add a route to the quote.

        Args:
            route: Route to add

        """
        if self.routes is None:
            self.routes = []
        self.routes.append(route)
        # Update best route if this is better (fewer hops, better price)
        if self.best_route is None or route.hop_count < self.best_route.hop_count:
            self.best_route = route
