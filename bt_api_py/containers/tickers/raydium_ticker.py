"""Raydium Ticker Data Container
Provides standardized ticker data structure for Raydium DEX.
"""

from __future__ import annotations

import time
from typing import Any

from bt_api_py.containers.tickers.ticker import TickerData
from bt_api_py.logging_factory import get_logger


class RaydiumRequestTickerData(TickerData):
    """Raydium Request Ticker Data Container.

    Raydium is a DEX, so ticker data comes from pool information.
    """

    def __init__(self, data: dict[str, Any], symbol: str, asset_type: str) -> None:
        """Initialize Raydium ticker data.

        Args:
            data: Raw ticker/pool data from Raydium API
            symbol: Trading pair symbol (e.g., 'SOL/USDC')
            asset_type: Asset type (e.g., 'DEX')

        """
        super().__init__(data, has_been_json_encoded=True)
        self.symbol_name = symbol
        self.asset_type = asset_type
        self.symbol: str = symbol
        self.logger = get_logger("raydium_ticker")
        self._parse_data(data)

    def _parse_data(self, data: dict[str, Any]) -> None:
        """Parse Raydium pool data as ticker.

        Raydium pool response format:
        {
            "success": true,
            "data": [
                {
                    "id": "pool_id",
                    "name": "SOL-USDC",
                    "type": "Standard",
                    "lpMint": "lp_token_address",
                    "baseMint": "base_token_address",
                    "quoteMint": "quote_token_address",
                    "lpDecimals": 9,
                    "version": 4,
                    "programId": "program_id",
                    "authority": "authority_address",
                    "poolLpAmount": 1000000,
                    "poolBaseTokenAmount": 1000000,
                    "poolQuoteTokenAmount": 1000000,
                    "swapBaseInAmount": 1000000,
                    "swapQuoteInAmount": 1000000,
                    "swapBaseOutAmount": 1000000,
                    "swapQuoteOutAmount": 1000000,
                    "tradeVolumeToken": 1000000,
                    "tradeVolumeQuote": 1000000,
                    "liquidity": 1000000,
                    "tvl": 1000000,
                    "day": {
                        "volume": 1000000,
                        "volumeUsd": 1000000,
                        "volumeChange": 0.1
                    },
                    "price": 100.0
                }
            ]
        }
        """
        try:
            # Raydium returns data in an array
            pool_data = data.get("data", [])
            if isinstance(pool_data, list) and len(pool_data) > 0:
                result = pool_data[0]
            elif isinstance(pool_data, dict):
                result = pool_data
            else:
                result = data

            # Basic information
            self.symbol = result.get("name", self.symbol) or self.symbol
            self.exchange = "raydium"
            self.timestamp = time.time()
            self.datetime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.timestamp))

            # Pool information
            self.pool_id = result.get("id")
            self.pool_type = result.get("type", "Standard")

            # Calculate price from pool amounts
            base_amount = result.get("poolBaseTokenAmount", 0)
            quote_amount = result.get("poolQuoteTokenAmount", 0)

            if base_amount and quote_amount:
                # Price = quote / base
                self.last_price = quote_amount / base_amount
            else:
                self.last_price = result.get("price")

            # TVL and liquidity
            self.tvl = result.get("tvl")
            self.liquidity = result.get("liquidity")

            # Volume data
            day_data = result.get("day", {})
            self.volume_24h = day_data.get("volume")
            self.volume_24h_usd = day_data.get("volumeUsd")
            self.volume_change_24h = day_data.get("volumeChange")

            # Token amounts
            self.pool_base_amount = base_amount
            self.pool_quote_amount = quote_amount
            self.lp_amount = result.get("poolLpAmount")

            # Token addresses
            self.base_token_address = result.get("baseMint")
            self.quote_token_address = result.get("quoteMint")
            self.lp_token_address = result.get("lpMint")

            # For DEX, bid/ask are estimated from pool reserves with slippage
            if self.last_price:
                slippage_0_01_percent = 0.0001
                self.ask_price = self.last_price * (1 + slippage_0_01_percent)
                self.bid_price = self.last_price * (1 - slippage_0_01_percent)
                self.spread = self.ask_price - self.bid_price
            else:
                self.ask_price = None
                self.bid_price = None
                self.spread = None

            self.price_change = None  # Not directly available
            self.price_change_percentage = day_data.get("volumeChange")  # Volume change as proxy

        except Exception as e:
            self.logger.error(f"Error parsing Raydium ticker data: {e}")
            self.logger.error(f"Raw data: {data}")
            raise

    def to_dict(self) -> dict[str, Any]:
        """Convert ticker data to dictionary."""
        return {
            "symbol": self.symbol,
            "exchange": self.exchange,
            "timestamp": self.timestamp,
            "datetime": self.datetime,
            "last_price": self.last_price,
            "bid_price": self.bid_price,
            "ask_price": self.ask_price,
            "spread": self.spread,
            "volume_24h": self.volume_24h,
            "volume_24h_usd": self.volume_24h_usd,
            "tvl": self.tvl,
            "liquidity": self.liquidity,
            "pool_id": self.pool_id,
            "pool_type": self.pool_type,
            "base_token_address": self.base_token_address,
            "quote_token_address": self.quote_token_address,
            "lp_token_address": self.lp_token_address,
            "asset_type": self.asset_type,
        }

    def validate(self) -> bool:
        """Validate ticker data integrity."""
        if not self.symbol:
            return False

        # Check required price fields
        if not self.last_price or self.last_price <= 0:
            return False

        # Validate TVL
        return not (self.tvl is not None and self.tvl < 0)

    def get_reserves(self) -> tuple:
        """Get pool reserves.

        Returns:
            tuple: (base_amount, quote_amount)

        """
        return (self.pool_base_amount, self.pool_quote_amount)

    def calculate_slippage(
        self, input_amount: float, input_token: str = "base"
    ) -> dict[str, float | None]:
        """Calculate price impact for a swap.

        This is a simplified calculation. Actual DEX swaps use
        constant product formula: x * y = k

        Args:
            input_amount: Amount to swap
            input_token: 'base' or 'quote'

        Returns:
            Dict with output amount and price impact

        """
        if not self.pool_base_amount or not self.pool_quote_amount:
            return {"output_amount": None, "price_impact": None}

        if input_token == "base":
            # Swap base for quote
            # output = (input * quote_reserve) / (base_reserve + input)
            output = (input_amount * self.pool_quote_amount) / (
                self.pool_base_amount + input_amount
            )
            price_impact = (1 - (self.last_price * input_amount / output)) if output else None
        else:
            # Swap quote for base
            # output = (input * base_reserve) / (quote_reserve + input)
            output = (input_amount * self.pool_base_amount) / (
                self.pool_quote_amount + input_amount
            )
            price_impact = (1 - (output / (input_amount / self.last_price))) if output else None

        return {
            "output_amount": output,
            "price_impact": price_impact if price_impact is not None else None,
        }

    def __str__(self) -> str:
        """String representation of ticker."""
        return (
            f"RaydiumTicker({self.symbol}: {self.last_price} "
            f"TVL:{self.tvl} Vol24h:{self.volume_24h})"
        )

    def __repr__(self) -> str:
        """Detailed string representation."""
        return (
            f"RaydiumRequestTickerData(symbol='{self.symbol}', "
            f"last_price={self.last_price}, "
            f"tvl={self.tvl}, "
            f"pool_id='{self.pool_id}', "
            f"timestamp={self.timestamp})"
        )
