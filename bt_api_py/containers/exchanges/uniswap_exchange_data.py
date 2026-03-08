"""
Uniswap Exchange Data Configuration

Defines API endpoints, chain enums, and path configurations for Uniswap DEX.
"""

import os
from enum import Enum

# ── 配置加载缓存 ──────────────────────────────────────────────
_uniswap_config = None
_uniswap_config_loaded = False
_uniswap_config_raw = None


def _get_uniswap_config():
    """延迟加载并缓存 Uniswap YAML 配置"""
    global _uniswap_config, _uniswap_config_loaded, _uniswap_config_raw
    if _uniswap_config_loaded:
        return _uniswap_config
    try:
        import yaml

        from bt_api_py.config_loader import load_exchange_config

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "..",
            "configs",
            "uniswap.yaml",
        )
        if os.path.exists(config_path):
            # Load raw YAML first for custom fields
            with open(config_path, encoding="utf-8") as f:
                _uniswap_config_raw = yaml.safe_load(f)

            # Then load with pydantic
            _uniswap_config = load_exchange_config(config_path)

            # Store raw config for custom fields
            _uniswap_config._raw_config = _uniswap_config_raw
        _uniswap_config_loaded = True
    except Exception as e:
        # Import logger here to avoid circular imports
        from bt_api_py.logging_factory import get_logger

        logger = get_logger("uniswap_exchange_data")
        logger.warn(f"Failed to load uniswap.yaml config: {e}")
    return _uniswap_config


class UniswapChain(str, Enum):
    """Uniswap supported chains for GraphQL queries."""

    ETHEREUM = "ETHEREUM"
    ARBITRUM = "ARBITRUM"
    OPTIMISM = "OPTIMISM"
    POLYGON = "POLYGON"
    BSC = "BSC"
    AVALANCHE = "AVALANCHE"
    BASE = "BASE"


class UniswapExchangeData:
    """Uniswap exchange configuration data.

    Uniswap uses GraphQL API instead of traditional REST endpoints.
    """

    # Default chain for queries
    DEFAULT_CHAIN = UniswapChain.ETHEREUM

    # Trading API endpoints (fallback if config loading fails)
    TRADING_API_URL = "https://trade-api.gateway.uniswap.org/v1"

    # Subgraph endpoints (per chain) - fallback if config loading fails
    SUBGRAPH_URLS = {
        UniswapChain.ETHEREUM: "https://api.thegraph.com/subgraphs/name/uniswap/uniswap-v3",
        UniswapChain.ARBITRUM: "https://api.thegraph.com/subgraphs/name/uniswap/uniswap-v3-arbitrum",
        UniswapChain.OPTIMISM: "https://api.thegraph.com/subgraphs/name/uniswap/uniswap-v3-optimism",
        UniswapChain.POLYGON: "https://api.thegraph.com/subgraphs/name/uniswap/uniswap-v3-polygon",
        UniswapChain.BSC: "https://api.thegraph.com/subgraphs/name/uniswap/uniswap-v3-bsc",
        UniswapChain.AVALANCHE: "https://api.thegraph.com/subgraphs/name/uniswap/uniswap-v3-avalanche",
        UniswapChain.BASE: "https://api.thegraph.com/subgraphs/name/uniswap/uniswap-v3-base",
    }

    # Router addresses (Ethereum Mainnet)
    ROUTER_ADDRESS = "0x68b3465833fb129eeae6d7305a3f870a770d97c5"

    def __init__(self, chain: UniswapChain = DEFAULT_CHAIN, asset_type: str | None = None):
        """Initialize Uniswap exchange data.

        Args:
            chain: The blockchain to query
            asset_type: Asset type (e.g., 'ethereum', 'arbitrum') to load config from uniswap.yaml
        """
        self.chain = chain
        self.asset_type = asset_type
        self.config = _get_uniswap_config()

        # Load from config if available and asset_type is provided
        if self.config and asset_type:
            self._load_from_config(asset_type)
        else:
            # Use defaults
            self.rest_url = self.TRADING_API_URL
            self.subgraph_urls = self.SUBGRAPH_URLS
            self.chains_supported = [chain.value]
            self.router_address = self.ROUTER_ADDRESS

    def _load_from_config(self, asset_type: str) -> bool:
        """从 YAML 配置文件加载 Uniswap 参数

        Args:
            asset_type: 资产类型 key, 如 'ethereum', 'arbitrum' 等
        Returns:
            bool: 是否加载成功
        """
        if not self.config:
            return False

        asset_cfg = self.config.asset_types.get(asset_type)
        if not asset_cfg:
            return False

        # Load Trading API URL
        if hasattr(self.config, "_raw_config") and self.config._raw_config:
            raw_base_urls = self.config._raw_config.get("base_urls", {})
            if raw_base_urls and raw_base_urls.get("trading_api"):
                self.rest_url = raw_base_urls["trading_api"]
        else:
            self.rest_url = self.TRADING_API_URL

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

        # Load router address from config if available
        if hasattr(self.config, "_raw_config") and self.config._raw_config:
            router_addresses = self.config._raw_config.get("router_address", {})
            if router_addresses and self.chain.value in router_addresses:
                self.router_address = router_addresses[self.chain.value]

        # Load supported chains
        asset_config_dict = asset_cfg.__dict__ if hasattr(asset_cfg, "__dict__") else {}
        if "chains_supported" in asset_config_dict and asset_config_dict["chains_supported"]:
            self.chains_supported = list(asset_config_dict["chains_supported"])
        else:
            self.chains_supported = [self.chain.value]

        return True

    def get_rest_url(self) -> str:
        """Get the Trading API URL."""
        return self.rest_url

    def get_subgraph_url(self) -> str:
        """Get the subgraph URL for the configured chain."""
        if hasattr(self, "subgraph_urls") and self.chain.value in self.subgraph_urls:
            return self.subgraph_urls[self.chain.value]
        # Fallback to predefined URLs
        return self.SUBGRAPH_URLS.get(self.chain, self.SUBGRAPH_URLS[UniswapChain.ETHEREUM])

    def get_symbol(self, symbol: str) -> str:
        """Normalize symbol for Uniswap API.

        Uniswap uses token addresses as symbols.

        Args:
            symbol: Token address or symbol (e.g., "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2" for WETH)

        Returns:
            Normalized token address (checksummed)
        """
        # Return as-is if it looks like an address
        if symbol.startswith("0x") and len(symbol) == 42:
            return symbol
        # Could add symbol -> address mapping here
        return symbol

    def get_api_key(self) -> str:
        """Get API key for Uniswap Trading API.

        Returns:
            API key string
        """
        # API key should be set via environment variable
        api_key = os.getenv("UNISWAP_API_KEY")
        if not api_key:
            raise ValueError("UNISWAP_API_KEY environment variable not set")
        return api_key

    def get_chain_value(self) -> str:
        """Get the chain enum value for GraphQL queries."""
        return self.chain.value

    def get_graphql_query(self, query_name: str) -> str | None:
        """Get GraphQL query by name from configuration.

        Args:
            query_name: Name of the query (e.g., "get_pools", "get_token_prices")

        Returns:
            GraphQL query string or None if not found
        """
        if hasattr(self, "graphql_queries") and self.graphql_queries:
            return self.graphql_queries.get(query_name)
        return None

    def get_rest_path(self, request_type: str) -> str:
        """Get the REST path for a given request type.

        For Uniswap, this returns the HTTP method and Trading API endpoint.
        Can load custom REST paths from config if available.

        Args:
            request_type: Type of request (e.g., "get_tick", "get_pools", "get_swap_quote")

        Returns:
            String in format "POST /endpoint" or "GRAPHQL {query}"
        """
        # If config has rest_paths and this request_type is defined
        if self.config and hasattr(self, "asset_type") and self.asset_type:
            asset_cfg = self.config.asset_types.get(self.asset_type)
            if asset_cfg and asset_cfg.rest_paths:
                path = asset_cfg.rest_paths.get(request_type)
                if path:
                    return path

        # Fallback to standard Trading API endpoint
        # All Uniswap Trading API queries use POST
        return f"POST {self.rest_url}/graphql"


class UniswapExchangeDataSpot(UniswapExchangeData):
    """Uniswap Spot exchange configuration.

    Inherits from base UniswapExchangeData with spot-specific settings.
    """

    def __init__(
        self,
        chain: UniswapChain | str = UniswapExchangeData.DEFAULT_CHAIN,
        asset_type: str | None = None,
    ):
        # Convert string to enum if needed
        if isinstance(chain, str):
            try:
                chain = UniswapChain(chain)
            except ValueError:
                chain = UniswapExchangeData.DEFAULT_CHAIN

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
        if hasattr(self, "_raw_config") and self._raw_config:
            raw_asset_types = self._raw_config.get("asset_types", {})
            asset_config = raw_asset_types.get(asset_type, {})

            # Load GraphQL paths if available
            self.graphql_paths = asset_config.get("rest_paths", {})

            # Load special operations if available
            self.special_operations = asset_config.get("special_operations", {})
        else:
            self.graphql_paths = {}
            self.special_operations = {}

        return True
