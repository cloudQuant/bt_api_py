"""
GMX Exchange Data Configuration

GMX is a decentralized perpetual exchange that supports multiple blockchains:
- Arbitrum
- Avalanche
- Botanix
"""

import os
from enum import Enum
from typing import Any

from bt_api_py.functions.log_message import SpdLogManager

logger = SpdLogManager(
    file_name="gmx_exchange_data.log", logger_name="gmx_data", print_info=False
).create_logger()

_gmx_config = None
_gmx_config_loaded = False
_gmx_config_raw = None


def _get_gmx_config():
    """Load GMX YAML configuration."""
    global _gmx_config, _gmx_config_loaded, _gmx_config_raw
    if _gmx_config_loaded:
        return _gmx_config
    try:
        from bt_api_py.config_loader import load_exchange_config
        import yaml

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "gmx.yaml",
        )
        if os.path.exists(config_path):
            # Load raw YAML first for custom fields
            with open(config_path, encoding="utf-8") as f:
                _gmx_config_raw = yaml.safe_load(f)

            # Then load with pydantic
            _gmx_config = load_exchange_config(config_path)

            # Store raw config for custom fields
            _gmx_config._raw_config = _gmx_config_raw
        _gmx_config_loaded = True
    except Exception as e:
        logger.warn(f"Failed to load gmx.yaml config: {e}")
    return _gmx_config


class GmxChain(str, Enum):
    """GMX supported chains."""

    ARBITRUM = "arbitrum"
    AVALANCHE = "avalanche"
    BOTANIX = "botanix"


class GmxExchangeData:
    """GMX exchange configuration data.

    GMX uses REST API for oracle/pricing data instead of GraphQL.
    """

    # Default chain for queries
    DEFAULT_CHAIN = GmxChain.ARBITRUM

    # API endpoints per chain (fallback if config loading fails)
    API_URLS = {
        GmxChain.ARBITRUM: "https://arbitrum-api.gmxinfra.io",
        GmxChain.AVALANCHE: "https://avalanche-api.gmxinfra.io",
        GmxChain.BOTANIX: "https://botanix-api.gmxinfra.io",
    }

    def __init__(self, chain: GmxChain = DEFAULT_CHAIN, asset_type: str | None = None):
        """Initialize GMX exchange data.

        Args:
            chain: The blockchain to query
            asset_type: Asset type (e.g., 'spot', 'perpetual')
        """
        self.chain = chain
        self.asset_type = asset_type or "spot"
        self.config = _get_gmx_config()

        # Load from config if available
        if self.config and asset_type:
            self._load_from_config(asset_type)
        else:
            # Use defaults
            self.rest_url = self.API_URLS[self.chain]
            self.rest_paths = {}
            self.wss_paths = {}

    def _load_from_config(self, asset_type: str) -> bool:
        """Load from YAML config file.

        Args:
            asset_type: Asset type key (e.g., 'spot', 'perpetual')
        Returns:
            bool: Whether loading was successful
        """
        if not self.config:
            return False

        asset_cfg = self.config.asset_types.get(asset_type)
        if not asset_cfg:
            return False

        # Load REST URL from config based on chain
        if hasattr(self.config, "_raw_config") and self.config._raw_config:
            raw_base_urls = self.config._raw_config.get("base_urls", {})
            if raw_base_urls and raw_base_urls.get("rest"):
                chain_url = raw_base_urls["rest"].get(self.chain.value)
                if chain_url:
                    self.rest_url = chain_url
                else:
                    self.rest_url = list(raw_base_urls["rest"].values())[0]
            else:
                self.rest_url = self.API_URLS[self.chain]
        else:
            self.rest_url = self.API_URLS[self.chain]

        # Load REST paths
        if asset_cfg.rest_paths:
            self.rest_paths = dict(asset_cfg.rest_paths)
        else:
            self.rest_paths = {}

        # Load WSS paths
        if hasattr(asset_cfg, "wss_paths") and asset_cfg.wss_paths:
            self.wss_paths = dict(asset_cfg.wss_paths)
        else:
            self.wss_paths = {}

        return True

    def get_rest_url(self) -> str:
        """Get the REST API URL for the configured chain."""
        return self.rest_url

    def get_chain_value(self) -> str:
        """Get the chain value."""
        return self.chain.value

    def get_rest_path(self, request_type: str) -> str:
        """Get the REST path for a given request type.

        Args:
            request_type: Type of request (e.g., "get_tick", "get_candles")

        Returns:
            String in format "GET /endpoint"
        """
        if self.rest_paths and request_type in self.rest_paths:
            return self.rest_paths[request_type]

        # Fallback to standard endpoints
        standard_paths = {
            "get_server_time": "GET /ping",
            "get_tick": "GET /prices/tickers",
            "get_candles": "GET /prices/candles",
            "get_tokens": "GET /tokens",
            "get_markets": "GET /markets",
        }
        return standard_paths.get(request_type, f"GET /{request_type}")


class GmxExchangeDataSpot(GmxExchangeData):
    """GMX Spot exchange configuration."""

    # Default kline periods (can be overridden by config)
    kline_periods = {
        "1m": "1m",
        "5m": "5m",
        "15m": "15m",
        "1h": "1h",
        "4h": "4h",
        "1d": "1d",
    }

    # Default legal currencies (can be overridden by config)
    legal_currency = [
        "USDC",
        "USDT",
        "USD",
        "BTC",
        "ETH",
        "AVAX",
    ]

    # Default supported symbols (can be overridden by config)
    supported_symbols = [
        "BTC",
        "ETH",
        "USDC",
        "USDT",
        "AVAX",
        "ARB",
        "UNI",
        "LINK",
    ]

    def __init__(self, chain: GmxChain | str = GmxExchangeData.DEFAULT_CHAIN):
        # Convert string to enum if needed
        if isinstance(chain, str):
            try:
                chain = GmxChain(chain)
            except ValueError:
                chain = GmxExchangeData.DEFAULT_CHAIN

        super().__init__(chain, "spot")

        # Override with config values if available
        if self.config and hasattr(self.config, "_raw_config") and self.config._raw_config:
            raw_config = self.config._raw_config

            # Load kline_periods from config
            if "kline_periods" in raw_config:
                self.kline_periods = dict(raw_config["kline_periods"])

            # Load legal_currency from config
            if "legal_currency" in raw_config:
                self.legal_currency = list(raw_config["legal_currency"])

            # Load supported symbols from asset config
            asset_cfg = raw_config.get("asset_types", {}).get("spot", {})
            if "legal_currency" in asset_cfg:
                self.legal_currency = list(asset_cfg["legal_currency"])

            # Supported symbols is derived from legal currency for GMX
            self.supported_symbols = self.legal_currency.copy()
