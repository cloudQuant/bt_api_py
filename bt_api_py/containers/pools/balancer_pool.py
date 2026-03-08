"""
Balancer Pool Data Container.

Handles pool information data from Balancer's GraphQL API.
"""

import json
import time
from typing import Any


class BalancerPoolData:
    """Balancer pool data container.

    Parses pool data from Balancer's poolGetPool query.
    """

    def __init__(self, pool_info, has_been_json_encoded=False):
        self.pool_info = pool_info
        self.has_been_json_encoded = has_been_json_encoded
        self.exchange_name = "BALANCER"
        self.local_update_time = time.time()
        self.pool_data = pool_info if has_been_json_encoded else None
        self.has_been_init_data = False

        # Pool fields
        self.pool_id = None
        self.pool_address = None
        self.pool_name = None
        self.pool_symbol = None
        self.pool_type = None
        self.pool_version = None

        # Token fields
        self.tokens = []
        self.token_addresses = []
        self.token_symbols = []

        # Liquidity fields
        self.total_liquidity = None
        self.total_shares = None
        self.volume_24h = None
        self.fees_24h = None

        # APR fields
        self.apr_items = []
        self.total_apr = None

    def init_data(self):
        """Parse Balancer pool response from GraphQL API."""
        if not self.has_been_json_encoded:
            self.pool_data = json.loads(self.pool_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        # Extract nested data if present
        if isinstance(self.pool_data, dict):
            if "data" in self.pool_data:
                inner = self.pool_data.get("data", {}).get("poolGetPool")
                if inner:
                    self.pool_data = inner

        # Parse basic pool info
        self.pool_id = self.pool_data.get("id")
        self.pool_address = self.pool_data.get("address")
        self.pool_name = self.pool_data.get("name")
        self.pool_symbol = self.pool_data.get("symbol")
        self.pool_type = self.pool_data.get("type")
        self.pool_version = self.pool_data.get("version")

        # Parse tokens
        all_tokens = self.pool_data.get("allTokens", [])
        self.tokens = all_tokens
        self.token_addresses = [t.get("address") for t in all_tokens if t.get("address")]
        self.token_symbols = [t.get("symbol") for t in all_tokens if t.get("symbol")]

        # Parse pool tokens with balances
        pool_tokens = self.pool_data.get("poolTokens", [])
        self.pool_tokens = pool_tokens

        # Parse dynamic data
        dynamic_data = self.pool_data.get("dynamicData", {})
        if dynamic_data:
            self.total_liquidity = self._parse_float(dynamic_data.get("totalLiquidity"))
            self.total_shares = self._parse_float(dynamic_data.get("totalShares"))
            self.volume_24h = self._parse_float(dynamic_data.get("volume24h"))
            self.fees_24h = self._parse_float(dynamic_data.get("fees24h"))

            # Parse APR items
            apr_items = dynamic_data.get("aprItems", [])
            self.apr_items = [
                {
                    "title": item.get("title"),
                    "type": item.get("type"),
                    "apr": self._parse_float(item.get("apr")),
                }
                for item in apr_items
            ]
            self.total_apr = sum(item.get("apr", 0) for item in self.apr_items if item.get("apr"))

        self.has_been_init_data = True
        return self

    @staticmethod
    def _parse_float(value: Any) -> float | None:
        """Safely parse a float value."""
        if value is None:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None

    def get_pool_id(self) -> str | None:
        return self.pool_id

    def get_pool_address(self) -> str | None:
        return self.pool_address

    def get_pool_name(self) -> str | None:
        return self.pool_name

    def get_pool_symbol(self) -> str | None:
        return self.pool_symbol

    def get_pool_type(self) -> str | None:
        return self.pool_type

    def get_total_liquidity(self) -> float | None:
        return self.total_liquidity

    def get_volume_24h(self) -> float | None:
        return self.volume_24h

    def get_fees_24h(self) -> float | None:
        return self.fees_24h

    def get_total_apr(self) -> float | None:
        return self.total_apr

    def get_apr_breakdown(self) -> list[dict]:
        return self.apr_items

    def get_tokens(self) -> list[dict]:
        return self.tokens

    def get_token_addresses(self) -> list[str]:
        return self.token_addresses

    def get_token_symbols(self) -> list[str]:
        return self.token_symbols

    def get_pool_tokens_with_balances(self) -> list[dict]:
        return self.pool_tokens


class BalancerWssPoolData(BalancerPoolData):
    """Balancer WebSocket pool data container.

    Note: Balancer doesn't have native WebSocket support.
    This class is provided for future compatibility.
    """

    def init_data(self):
        """Parse Balancer WebSocket pool response."""
        if not self.has_been_json_encoded:
            self.pool_data = json.loads(self.pool_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        # Use same parsing logic as REST
        super().init_data()
        return self
