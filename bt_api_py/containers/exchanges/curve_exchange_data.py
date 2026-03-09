"""Curve Exchange Data Configuration.

Defines API endpoints, chain enums, and path configurations for Curve DEX.
"""

import os
from enum import Enum
from typing import Any

# Config loading cache
_curve_config = None
_curve_config_loaded = False
_curve_config_raw = None


def _get_curve_config() -> Any | None:
    """延迟加载并缓存 Curve YAML 配置."""
    global _curve_config, _curve_config_loaded, _curve_config_raw
    if _curve_config_loaded:
        return _curve_config
    try:
        import yaml

        from bt_api_py.config_loader import load_exchange_config

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "..",
            "configs",
            "curve.yaml",
        )
        if os.path.exists(config_path):
            # Load raw YAML first for custom fields
            with open(config_path, encoding="utf-8") as f:
                _curve_config_raw = yaml.safe_load(f)

            # Then load with pydantic
            _curve_config = load_exchange_config(config_path)

            # Store raw config for custom fields
            _curve_config._raw_config = _curve_config_raw
        _curve_config_loaded = True
    except Exception as e:
        # Import logger here to avoid circular imports
        from bt_api_py.logging_factory import get_logger

        logger = get_logger("curve_exchange_data")
        logger.warn(f"Failed to load curve.yaml config: {e}")
    return _curve_config


class CurveChain(str, Enum):
    """Curve supported chains for API queries."""

    ETHEREUM = "ETHEREUM"
    ARBITRUM = "ARBITRUM"
    OPTIMISM = "OPTIMISM"
    POLYGON = "POLYGON"
    AVALANCHE = "AVALANCHE"
    BASE = "BASE"
    BSC = "BSC"
    FANTOM = "FANTOM"
    CELO = "CELO"
    GNOSIS = "GNOSIS"
    MOONBEAM = "MOONBEAM"
    KAVA = "KAVA"
    AURORA = "AURORA"
    FRAXTAL = "FRAXTAL"
    XAYER = "XAYER"


class CurveExchangeData:
    """Curve exchange configuration data.

    Curve uses REST API for querying pool data.
    On-chain trading is done through smart contracts.
    """

    # Default chain for queries
    DEFAULT_CHAIN = CurveChain.ETHEREUM

    # REST API endpoint (fallback if config loading fails)
    REST_API_URL = "https://api.curve.finance"

    # Factory addresses per chain
    FACTORY_ADDRESSES = {
        CurveChain.ETHEREUM: "0xF1e1A5D75807Fa1Ba018AFB412048C9352F827A8",
        CurveChain.ARBITRUM: "0x24F32A697FEC4d56f0f41961D74a578eF90975b9",
        CurveChain.OPTIMISM: "0x43507493AC2e62D324A5C5CEA3F7Fedd93130599",
        CurveChain.POLYGON: "0x2e8Da1e171d8Cd5C74F75a1bf8152FE63f2B8B43",
        CurveChain.AVALANCHE: "0x2c843758D0Bc7F8a9DeA49898615968AbC74d870",
        CurveChain.BASE: "0x82A8F6FbC44897655779AaDB1Cc3193F62CD2B10",
        CurveChain.BSC: "0x4e39530563291880468f9A9F60EF94267210a541",
        CurveChain.FANTOM: "0x2e3228b28834AbE263238F884A2B8A66F5439878",
        CurveChain.CELO: "0x3C0342B5af598407582aA958145beF7e87F26193",
        CurveChain.GNOSIS: "0x57C7a96BC578FC397103697A96876276A476F8E7",
        CurveChain.MOONBEAM: "0x3B657391A45dE089713c28079A9E714a2B13eAf0",
        CurveChain.KAVA: "0x5D2BFC9Af56a9F3a56B0964f76A6FA7D5095B73b",
        CurveChain.AURORA: "0x0A51b7c4E37824Abbd539c0C9586F9f933E09519",
        CurveChain.FRAXTAL: "0x45209b5F83E21C7FEB2a2D6b6823A191D1c8d7Db",
        CurveChain.XAYER: "0x1BF9C9381F37F3aFB3Fa25E1bFa2E91581718475",
    }

    def __init__(self, chain: CurveChain = DEFAULT_CHAIN, asset_type: str | None = None) -> None:
        """Initialize Curve exchange data.

        Args:
            chain: The blockchain to query
            asset_type: Asset type (e.g., 'ethereum', 'arbitrum') to load config from curve.yaml

        """
        self.chain = chain
        self.asset_type = asset_type
        self.config = _get_curve_config()

        # Load from config if available and asset_type is provided
        if self.config and asset_type:
            self._load_from_config(asset_type)
        else:
            # Use defaults
            self.rest_url = self.REST_API_URL
            self.chains_supported = [chain.value]
            self.factory_address = self.FACTORY_ADDRESSES.get(chain)

    def _load_from_config(self, asset_type: str) -> bool:
        """从 YAML 配置文件加载 Curve 参数.

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

        # Load REST API URL
        if hasattr(self.config, "_raw_config") and self.config._raw_config:
            raw_base_urls = self.config._raw_config.get("base_urls", {})
            if raw_base_urls and raw_base_urls.get("rest"):
                self.rest_url = raw_base_urls["rest"]
        else:
            self.rest_url = self.REST_API_URL

        # Load factory address
        if hasattr(self.config, "_raw_config") and self.config._raw_config:
            factory_addresses = self.config._raw_config.get("factory_address", {})
            if factory_addresses and self.chain.value in factory_addresses:
                self.factory_address = factory_addresses[self.chain.value]
            else:
                self.factory_address = self.FACTORY_ADDRESSES.get(self.chain)

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

    def get_chain_value(self) -> str:
        """Get the chain enum value for API queries."""
        return self.chain.value.lower()

    def get_chain_name(self) -> str:
        """Get the chain name for API paths."""
        return self.chain.value.lower()

    def get_rest_path(self, request_type: str) -> str:
        """Get the REST path for a given request type.

        Args:
            request_type: Type of request (e.g., "get_pools", "get_volumes", "get_tvl")

        Returns:
            String in format "GET /endpoint"

        """
        # If config has rest_paths and this request_type is defined
        if self.config and hasattr(self, "asset_type") and self.asset_type:
            asset_cfg = self.config.asset_types.get(self.asset_type)
            if asset_cfg and asset_cfg.rest_paths:
                path = asset_cfg.rest_paths.get(request_type)
                if path:
                    return path

        # Fallback to standard endpoint pattern
        chain_name = self.get_chain_name()
        endpoints = {
            "get_pools": f"GET /v1/getPools/{chain_name}/main",
            "get_volumes": f"GET /v1/getVolumes/{chain_name}",
            "get_tvl": f"GET /v1/getTVL/{chain_name}",
            "get_apys": f"GET /v1/getAPYs/{chain_name}",
            "get_gauges": f"GET /v1/getGauges/{chain_name}",
        }
        return endpoints.get(request_type, f"GET /v1/{request_type}/{chain_name}")


class CurveExchangeDataSpot(CurveExchangeData):
    """Curve Spot exchange configuration.

    Inherits from base CurveExchangeData with spot-specific settings.
    """

    def __init__(
        self,
        chain: CurveChain | str = CurveExchangeData.DEFAULT_CHAIN,
        asset_type: str | None = None,
    ) -> None:
        # Convert string to enum if needed
        if isinstance(chain, str):
            try:
                chain = CurveChain(chain)
            except ValueError:
                chain = CurveExchangeData.DEFAULT_CHAIN

        # Map chain to asset_type if not provided
        if asset_type is None:
            asset_type = chain.value.lower()

        super().__init__(chain, asset_type)

        # Initialize kline_periods and legal_currency
        self.kline_periods = {
            "1m": "1minute",
            "5m": "5minutes",
            "15m": "15minutes",
            "1h": "1hour",
            "4h": "4hours",
            "1d": "1day",
            "1w": "1week",
        }
        self.legal_currency = [
            "USDT",
            "USDC",
            "ETH",
            "BTC",
            "DAI",
        ]

        # Load specific spot configuration
        self._load_spot_config(asset_type)

    def _load_spot_config(self, asset_type: str) -> bool:
        """Load spot-specific configuration from config."""
        if not self.config:
            return False

        # Use raw config data to get rest_paths
        if hasattr(self, "_raw_config") and self._raw_config:
            raw_asset_types = self._raw_config.get("asset_types", {})
            asset_config = raw_asset_types.get(asset_type, {})

            # Load rest_paths if available
            self.rest_paths = asset_config.get("rest_paths", {})
        else:
            self.rest_paths = {}

        return True
