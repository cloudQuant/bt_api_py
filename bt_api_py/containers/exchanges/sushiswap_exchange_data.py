"""SushiSwap Exchange Data Configuration.

Defines API endpoints, chain enums, and path configurations for SushiSwap DEX.
"""

import os
from enum import Enum
from typing import Any

# Config loading cache
_sushiswap_config = None
_sushiswap_config_loaded = False
_sushiswap_config_raw = None


def _get_sushiswap_config() -> Any | None:
    """Load SushiSwap YAML configuration with caching."""
    global _sushiswap_config, _sushiswap_config_loaded, _sushiswap_config_raw
    if _sushiswap_config_loaded:
        return _sushiswap_config
    try:
        import yaml

        from bt_api_py.config_loader import load_exchange_config

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "sushiswap.yaml",
        )
        if os.path.exists(config_path):
            # Load raw YAML first for custom fields
            with open(config_path, encoding="utf-8") as f:
                _sushiswap_config_raw = yaml.safe_load(f)

            # Then load with pydantic
            _sushiswap_config = load_exchange_config(config_path)

            # Store raw config for custom fields
            _sushiswap_config._raw_config = _sushiswap_config_raw
        _sushiswap_config_loaded = True
    except Exception as e:
        from bt_api_py.logging_factory import get_logger

        logger = get_logger("sushiswap_exchange_data")
        logger.warn(f"Failed to load sushiswap.yaml config: {e}")
    return _sushiswap_config


class SushiSwapChain(str, Enum):
    """SushiSwap supported chains."""

    ETHEREUM = "ETHEREUM"
    ARBITRUM = "ARBITRUM"
    POLYGON = "POLYGON"
    OPTIMISM = "OPTIMISM"
    BSC = "BSC"
    AVALANCHE = "AVALANCHE"
    FANTOM = "FANTOM"
    CELO = "CELO"
    MOONBEAM = "MOONBEAM"
    MOONRIVER = "MOONRIVER"


class SushiSwapExchangeData:
    """SushiSwap exchange configuration data.

    SushiSwap uses REST API instead of traditional GraphQL endpoints.
    """

    DEFAULT_CHAIN = SushiSwapChain.ETHEREUM

    # API endpoints (fallback if config loading fails)
    API_BASE_URL = "https://api.sushi.com"

    # Chain IDs
    CHAIN_IDS = {
        SushiSwapChain.ETHEREUM: "1",
        SushiSwapChain.ARBITRUM: "42161",
        SushiSwapChain.POLYGON: "137",
        SushiSwapChain.OPTIMISM: "10",
        SushiSwapChain.BSC: "56",
        SushiSwapChain.AVALANCHE: "43114",
        SushiSwapChain.FANTOM: "250",
        SushiSwapChain.CELO: "42220",
        SushiSwapChain.MOONBEAM: "1284",
        SushiSwapChain.MOONRIVER: "1285",
    }

    def __init__(
        self, chain: SushiSwapChain = DEFAULT_CHAIN, asset_type: str | None = None
    ) -> None:
        """Initialize SushiSwap exchange data.

        Args:
            chain: The blockchain to query
            asset_type: Asset type (e.g., 'ethereum', 'arbitrum') to load config

        """
        self.chain = chain
        self.asset_type = asset_type
        self.config = _get_sushiswap_config()

        # Load from config if available and asset_type is provided
        if self.config and asset_type:
            self._load_from_config(asset_type)
        else:
            # Use defaults
            self.rest_url = self.API_BASE_URL
            self.chains_supported = [chain.value]

    def _load_from_config(self, asset_type: str) -> bool:
        """Load configuration from YAML.

        Args:
            asset_type: Asset type key, like 'ethereum', 'arbitrum', etc.

        Returns:
            bool: Whether loading succeeded

        """
        if not self.config:
            return False

        asset_cfg = self.config.asset_types.get(asset_type)
        if not asset_cfg:
            return False

        # Load API URL from raw config
        if hasattr(self.config, "_raw_config") and self.config._raw_config:
            raw_base_urls = self.config._raw_config.get("base_urls", {})
            if raw_base_urls and raw_base_urls.get("rest"):
                self.rest_url = raw_base_urls["rest"].get(asset_type, self.API_BASE_URL)
        else:
            self.rest_url = self.API_BASE_URL

        # Load supported chains
        asset_config_dict = asset_cfg.__dict__ if hasattr(asset_cfg, "__dict__") else {}
        if "chains_supported" in asset_config_dict and asset_config_dict["chains_supported"]:
            self.chains_supported = list(asset_config_dict["chains_supported"])
        else:
            self.chains_supported = [self.chain.value]

        return True

    def get_rest_url(self) -> str:
        """Get the REST API URL."""
        return self.rest_url

    def get_chain_id(self) -> str:
        """Get the chain ID for API requests."""
        return self.CHAIN_IDS.get(self.chain, self.CHAIN_IDS[SushiSwapChain.ETHEREUM])

    def get_symbol(self, symbol: str) -> str:
        """Normalize symbol for SushiSwap API.

        SushiSwap uses token addresses for most API calls.

        Args:
            symbol: Token address or symbol

        Returns:
            Normalized token address or symbol

        """
        # Return as-is if it looks like an address
        if symbol.startswith("0x") and len(symbol) == 42:
            return symbol
        return symbol

    def get_api_key(self) -> str | None:
        """Get API key for SushiSwap API (optional)."""
        return os.getenv("SUSHISWAP_API_KEY")

    def get_rest_path(self, request_type: str) -> str:
        """Get the REST path for a given request type.

        Args:
            request_type: Type of request (e.g., "get_tick", "get_pools", "get_quote")

        Returns:
            String in format "GET /endpoint" or "POST /endpoint"

        """
        # If config has rest_paths and this request_type is defined
        if self.config and hasattr(self, "asset_type") and self.asset_type:
            asset_cfg = self.config.asset_types.get(self.asset_type)
            if asset_cfg and asset_cfg.rest_paths:
                path = asset_cfg.rest_paths.get(request_type)
                if path:
                    # Replace {address} placeholder if needed
                    return path

        # Fallback to standard API endpoint construction
        chain_id = self.get_chain_id()
        if request_type == "get_tick":
            return f"GET /price/v1/{chain_id}/{{address}}"
        elif request_type == "get_pool":
            return f"GET /pool/v1/{chain_id}/{{address}}"
        elif request_type == "get_pools":
            return f"GET /pools/v1/{chain_id}"
        elif request_type == "get_quote":
            return f"GET /quote/v7/{chain_id}"
        elif request_type == "get_swap":
            return f"GET /swap/v7/{chain_id}"
        elif request_type == "get_exchange_info":
            return f"GET /tokens/v1/{chain_id}"

        return f"GET /{request_type}"


class SushiSwapExchangeDataSpot(SushiSwapExchangeData):
    """SushiSwap Spot exchange configuration."""

    # Kline periods supported
    kline_periods = ["1m", "5m", "15m", "1h", "4h", "1d"]

    # Legal currencies (stablecoins and native tokens)
    legal_currency = ["USDT", "USDC", "DAI", "ETH", "MATIC", "ARB"]

    def __init__(
        self,
        chain: SushiSwapChain | str = SushiSwapExchangeData.DEFAULT_CHAIN,
        asset_type: str | None = None,
    ) -> None:
        # Convert string to enum if needed
        if isinstance(chain, str):
            try:
                chain = SushiSwapChain(chain)
            except ValueError:
                chain = SushiSwapExchangeData.DEFAULT_CHAIN

        # Map chain to asset_type if not provided
        if asset_type is None:
            asset_type = chain.value.lower()

        super().__init__(chain, asset_type)

        # Load specific spot configuration
        self._load_spot_config(asset_type)

    def _load_spot_config(self, asset_type: str) -> bool:
        """Load spot-specific configuration."""
        if not self.config:
            return False

        # Use raw config data to get rest_paths
        if hasattr(self.config, "_raw_config") and self.config._raw_config:
            raw_asset_types = self.config._raw_config.get("asset_types", {})
            asset_config = raw_asset_types.get(asset_type, {})
            self.rest_paths = asset_config.get("rest_paths", {})
        else:
            self.rest_paths = {}

        return True
