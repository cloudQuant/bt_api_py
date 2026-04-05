"""Balancer Exchange Data Configuration.

Defines API endpoints, chain enums, and path configurations for Balancer DEX.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bt_api_py._compat import StrEnum

# ── 配置加载缓存 ──────────────────────────────────────────────
_balancer_config = None
_balancer_config_loaded = False
_balancer_config_raw = None


def _get_balancer_config() -> Any | None:
    """Load and cache Balancer YAML configuration with lazy loading.

    Returns:
        The loaded Balancer configuration object or None if loading failed.

    """
    global _balancer_config, _balancer_config_loaded, _balancer_config_raw
    if _balancer_config_loaded:
        return _balancer_config
    try:
        import yaml

        from bt_api_py.config_loader import load_exchange_config

        config_path = Path(__file__).resolve().parent.parent.parent / "configs" / "balancer.yaml"
        if config_path.exists():
            # Load raw YAML first for custom fields
            with config_path.open(encoding="utf-8") as f:
                _balancer_config_raw = yaml.safe_load(f)

            # Then load with pydantic
            _balancer_config = load_exchange_config(str(config_path))

            # Store raw config for custom fields
            _balancer_config._raw_config = _balancer_config_raw
        _balancer_config_loaded = True
    except (OSError, ValueError, KeyError, ImportError) as e:
        # Import logger here to avoid circular imports
        from bt_api_py.logging_factory import get_logger

        logger = get_logger("balancer_exchange_data")
        logger.warning(f"Failed to load balancer.yaml config: {e}")
    return _balancer_config


class GqlChain(StrEnum):
    """Balancer supported chains for GraphQL queries."""

    MAINNET = "MAINNET"
    POLYGON = "POLYGON"
    ARBITRUM = "ARBITRUM"
    OPTIMISM = "OPTIMISM"
    GNOSIS = "GNOSIS"
    AVALANCHE = "AVALANCHE"
    BASE = "BASE"
    SEPOLIA = "SEPOLIA"


class BalancerExchangeData:
    """Balancer exchange configuration data.

    Balancer uses GraphQL API instead of traditional REST endpoints.
    """

    # Default chain for queries
    DEFAULT_CHAIN = GqlChain.MAINNET

    # GraphQL API endpoints (fallback if config loading fails)
    GRAPHQL_API_URL = "https://api-v3.balancer.fi"

    # Subgraph endpoints (per chain) - fallback if config loading fails
    SUBGRAPH_URLS = {
        GqlChain.MAINNET: "https://api.thegraph.com/subgraphs/name/balancer-labs/balancer-v2",
        GqlChain.POLYGON: "https://api.thegraph.com/subgraphs/name/balancer-labs/balancer-v2-polygon",
        GqlChain.ARBITRUM: "https://api.thegraph.com/subgraphs/name/balancer-labs/balancer-v2-arbitrum",
        GqlChain.OPTIMISM: "https://api.thegraph.com/subgraphs/name/balancer-labs/balancer-v2-optimism",
        GqlChain.GNOSIS: "https://api.thegraph.com/subgraphs/name/balancer-labs/balancer-v2-gnosis",
        GqlChain.AVALANCHE: "https://api.thegraph.com/subgraphs/name/balancer-labs/balancer-v2-avalanche",
        GqlChain.BASE: "https://api.thegraph.com/subgraphs/name/balancer-labs/balancer-v2-base",
        GqlChain.SEPOLIA: "https://api.thegraph.com/subgraphs/name/balancer-labs/balancer-v2-sepolia",
    }

    # Smart contract addresses (Ethereum Mainnet)
    VAULT_ADDRESS = "0xBA12222222228d8Ba445958a75a0704d566BF2C8"
    BALANCER_QUERIES_ADDRESS = "0xE39B5e3B6D74016b2F6A9673D7d7493B6DF549d5"
    BAL_TOKEN_ADDRESS = "0xba100000625a3754423978a60c9317c58a424e3d"

    def __init__(self, chain: GqlChain = DEFAULT_CHAIN, asset_type: str | None = None) -> None:
        """Initialize Balancer exchange data.

        Args:
            chain: The blockchain to query
            asset_type: Asset type (e.g., 'mainnet', 'polygon') to load config from balancer.yaml

        """
        self.chain = chain
        self.asset_type = asset_type
        self.config = _get_balancer_config()

        # Load from config if available and asset_type is provided
        if self.config and asset_type:
            self._load_from_config(asset_type)
        else:
            # Use defaults
            self.rest_url = self.GRAPHQL_API_URL
            self.subgraph_urls = self.SUBGRAPH_URLS
            self.chains_supported = [chain.value]
            self.graphql_queries = {}

    def _load_from_config(self, asset_type: str) -> bool:
        """从 YAML 配置文件加载 Balancer 参数.

        Args:
            asset_type: 资产类型 key, 如 'mainnet', 'polygon' 等.

        Returns:
            是否加载成功.

        """
        if not self.config:
            return False

        asset_cfg = self.config.asset_types.get(asset_type)
        if not asset_cfg:
            return False

        # Load GraphQL API URL
        if hasattr(self.config, "_raw_config") and self.config._raw_config:
            raw_base_urls = self.config._raw_config.get("base_urls", {})
            if raw_base_urls and raw_base_urls.get("graphql_api"):
                self.rest_url = raw_base_urls["graphql_api"]
        else:
            self.rest_url = self.GRAPHQL_API_URL

        # Load subgraph URLs
        # Use raw config data since AssetTypeConfig doesn't have graphql_endpoints
        if hasattr(self.config, "_raw_config") and self.config._raw_config:
            raw_asset_types = self.config._raw_config.get("asset_types", {})
            raw_asset_config = raw_asset_types.get(asset_type, {})
            if raw_asset_config.get("graphql_endpoints"):
                subgraph_url = raw_asset_config["graphql_endpoints"].get("subgraph")
                if subgraph_url:
                    chain_name = self.chain.value
                    self.subgraph_urls = {chain_name: subgraph_url}
        else:
            self.subgraph_urls = self.SUBGRAPH_URLS

        # Load supported chains
        asset_config_dict = asset_cfg.__dict__ if hasattr(asset_cfg, "__dict__") else {}
        if "chains_supported" in asset_config_dict and asset_config_dict["chains_supported"]:
            self.chains_supported = list(asset_config_dict["chains_supported"])
        else:
            self.chains_supported = [self.chain.value]

        # Load GraphQL queries from config
        if hasattr(self.config, "_raw_config") and self.config._raw_config:
            self.graphql_queries = self.config._raw_config.get("graphql_queries", {})
        else:
            self.graphql_queries = {}

        return True

    def get_rest_path(self, request_type: str) -> str:
        """Get REST/GraphQL path for request type. Base returns standard GraphQL endpoint."""
        return f"POST {self.rest_url}/graphql"

    def get_rest_url(self) -> str:
        """Get the GraphQL API URL.

        Returns:
            The GraphQL API URL string.

        """
        return self.rest_url

    def get_subgraph_url(self) -> str:
        """Get the subgraph URL for the configured chain.

        Returns:
            The subgraph URL for the current chain.

        """
        if hasattr(self, "subgraph_urls") and self.chain.value in self.subgraph_urls:
            return self.subgraph_urls[self.chain.value]
        # Fallback to predefined URLs
        return self.SUBGRAPH_URLS.get(self.chain, self.SUBGRAPH_URLS[GqlChain.MAINNET])

    def get_symbol(self, symbol: str) -> str:
        """Normalize symbol for Balancer API.

        Balancer uses token addresses as symbols.

        Args:
            symbol: Token address or symbol (e.g., "0xC02...WETH")

        Returns:
            Normalized token address (checksummed)

        """
        # Return as-is if it looks like an address
        if symbol.startswith("0x") and len(symbol) == 42:
            return symbol
        # Could add symbol -> address mapping here
        return symbol

    def get_pool_id(self, pool_id: str) -> str:
        """Get normalized pool ID.

        Args:
            pool_id: Pool ID (bytes32 as hex string)

        Returns:
            Pool ID string

        """
        return pool_id

    def get_chain_value(self) -> str:
        """Get the chain enum value for GraphQL queries.

        Returns:
            The chain enum value as a string.

        """
        return self.chain.value

    def get_graphql_query(self, query_name: str) -> str | None:
        """Get GraphQL query by name from configuration.

        Args:
            query_name: Name of the query (e.g., "get_pools", "get_pool")

        Returns:
            GraphQL query string or None if not found

        """
        if hasattr(self, "graphql_queries") and self.graphql_queries:
            return self.graphql_queries.get(query_name)
        return None

    def get_graphql_path(self, path_name: str) -> str | None:
        """Get GraphQL path by name from asset type configuration.

        Args:
            path_name: Name of the path (e.g., "query_pools", "get_token_prices")

        Returns:
            GraphQL path string or None if not found

        """
        if hasattr(self, "graphql_paths") and self.graphql_paths:
            return self.graphql_paths.get(path_name)
        return None


class BalancerExchangeDataSpot(BalancerExchangeData):
    """Balancer Spot exchange configuration.

    Inherits from base BalancerExchangeData with spot-specific settings.
    """

    # Kline periods supported by Balancer (from pool snapshots)
    kline_periods = {
        "1m": "1m",
        "5m": "5m",
        "15m": "15m",
        "1h": "1h",
        "4h": "4h",
        "1d": "1d",
        "1w": "1w",
    }

    # Legal currencies for Balancer DEX
    legal_currency = [
        "USDT",
        "USDC",
        "DAI",
        "ETH",
        "WBTC",
        "WETH",
    ]

    def __init__(
        self,
        chain: GqlChain | str = BalancerExchangeData.DEFAULT_CHAIN,
        asset_type: str | None = None,
    ) -> None:
        """Initialize Balancer Spot exchange data.

        Args:
            chain: The blockchain to query (can be GqlChain enum or string).
            asset_type: Asset type to load configuration for.

        """
        # Convert string to enum if needed
        if isinstance(chain, str):
            try:
                chain = GqlChain(chain)
            except ValueError:
                chain = BalancerExchangeData.DEFAULT_CHAIN

        # Map chain to asset_type if not provided
        if asset_type is None:
            asset_type = chain.value.lower()

        super().__init__(chain, asset_type)

        # Load specific spot configuration
        self._load_spot_config(asset_type)

    def _load_spot_config(self, asset_type: str) -> bool:
        """Load spot-specific configuration from config."""
        if not self.config:
            return False

        # Use raw config data to get graphql_paths
        if hasattr(self.config, "_raw_config") and self.config._raw_config:
            raw_asset_types = self.config._raw_config.get("asset_types", {})
            asset_config = raw_asset_types.get(asset_type, {})

            # Load GraphQL paths if available
            self.graphql_paths = asset_config.get("graphql_paths", {})

            # Load special operations if available
            self.special_operations = asset_config.get("special_operations", {})
        else:
            self.graphql_paths = {}
            self.special_operations = {}

        return True

    def get_rest_path(self, request_type: str) -> str:
        """Get the REST path for a given request type.

        For Balancer, this returns the HTTP method and GraphQL endpoint.
        Can load custom GraphQL paths from config if available.

        Args:
            request_type: Type of request (e.g., "get_tick", "get_pools", "get_swap_path")

        Returns:
            String in format "POST /graphql" or "GRAPHQL {query}"

        """
        # If config has rest_paths and this request_type is defined
        if self.config and hasattr(self, "asset_type") and self.asset_type:
            asset_cfg = self.config.asset_types.get(self.asset_type)
            if asset_cfg and asset_cfg.rest_paths:
                path = asset_cfg.rest_paths.get(request_type)
                if path:
                    return path

        # Fallback to standard GraphQL endpoint
        # All Balancer queries use POST to the GraphQL endpoint
        return f"POST {self.rest_url}/graphql"
