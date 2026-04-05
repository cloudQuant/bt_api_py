"""Raydium Exchange Data Configuration."""

from __future__ import annotations

import os
from enum import Enum
from typing import Any

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("raydium_exchange_data")
_raydium_config = None
_raydium_config_loaded = False


def _get_raydium_config() -> Any | None:
    """Lazy load and cache Raydium YAML configuration.

    Returns:
        Configuration dictionary or None if not found
    """
    global _raydium_config, _raydium_config_loaded
    if _raydium_config_loaded:
        return _raydium_config
    try:
        from bt_api_py.config_loader import load_exchange_config

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "raydium.yaml",
        )
        if os.path.exists(config_path):
            _raydium_config = load_exchange_config(config_path)
            _raydium_config_loaded = True
            return _raydium_config
    except (OSError, ValueError, KeyError, ImportError) as e:
        logger.warning(f"Failed to load raydium.yaml config: {e}")
        _raydium_config_loaded = True
        return None
    return None


class RaydiumChain(Enum):
    """Supported blockchain networks for Raydium."""

    SOLANA = "SOLANA"


class RaydiumExchangeData(ExchangeData):
    """Base class for Raydium DEX.

    Raydium is a Solana-based DEX that uses AMM pools.
    This class provides REST API access to pool data, prices, and liquidity.
    """

    def __init__(self, chain: RaydiumChain = RaydiumChain.SOLANA) -> None:
        """Initialize Raydium exchange data.

        Args:
            chain: Blockchain network to use
        """
        super().__init__()
        self.exchange_name = "raydium"
        self.chain = chain
        self.rest_url = "https://api-v3.raydium.io"
        self.rest_paths: dict[str, str] = {}
        self.wss_paths: dict[str, Any] = {}

        self.kline_periods = {
            "1m": "60",
            "5m": "300",
            "15m": "900",
            "30m": "1800",
            "1h": "3600",
            "2h": "7200",
            "4h": "14400",
            "6h": "21600",
            "12h": "43200",
            "1d": "86400",
            "1w": "604800",
            "1M": "2592000",
        }

        self.legal_currency = ["SOL", "USDC", "USDT"]

        self._load_from_config("solana")

    def _load_from_config(self, asset_type: str) -> bool:
        """Load exchange parameters from YAML configuration file.

        Args:
            asset_type: Asset type key, e.g., 'solana'

        Returns:
            bool: Whether loading was successful
        """
        config = _get_raydium_config()
        if config is None:
            return False
        asset_cfg = config.asset_types.get(asset_type)
        if asset_cfg is None:
            return False

        if asset_cfg.exchange_name:
            self.exchange_name = asset_cfg.exchange_name

        if config.base_urls:
            self.rest_url = config.base_urls.rest.get(
                asset_type, config.base_urls.rest.get("default", self.rest_url)
            )

        if hasattr(asset_cfg, "rest_paths") and asset_cfg.rest_paths:
            self.rest_paths = dict(asset_cfg.rest_paths)

        return True

    def get_chain_value(self) -> str:
        """Get the blockchain network value.

        Returns:
            str: Chain name (e.g., 'SOLANA')
        """
        return self.chain.value

    def get_symbol(self, symbol: str) -> str:
        """Convert symbol format to standard format.

        Args:
            symbol: Symbol in any format (e.g., 'SOL-USDC', 'sol/usdc')

        Returns:
            str: Symbol in standard format (e.g., 'SOL/USDC')
        """
        return symbol.upper().replace("-", "/")

    def get_period(self, period: str) -> str:
        """Convert period to seconds string.

        Args:
            period: Period name

        Returns:
            str: Period in seconds as string
        """
        return str(self.kline_periods.get(period, period))

    def get_rest_path(self, request_type: str, **kwargs: Any) -> str:
        """Get REST API path.

        Args:
            request_type: Request type

        Returns:
            str: REST API path
        """
        if request_type not in self.rest_paths or self.rest_paths[request_type] == "":
            self.raise_path_error(self.exchange_name, request_type)
        return self.rest_paths[request_type]

    def get_wss_path(self, **kwargs: Any) -> str:
        """Get WebSocket path.

        Args:
            **kwargs: Path parameters

        Returns:
            str: WebSocket path
        """
        topic = kwargs.get("topic", "")
        if topic not in self.wss_paths or self.wss_paths[topic] == "":
            self.raise_path_error(self.exchange_name, topic)
        return str(self.wss_paths[topic])

    def get_pool_id(self, base_token: str, quote_token: str) -> str:
        """Get pool ID for a token pair.

        For Raydium, pools are identified by their pool address.
        This method would need to query the API to get the pool ID.

        Args:
            base_token: Base token address or symbol
            quote_token: Quote token address or symbol

        Returns:
            str: Pool ID
        """
        return f"{base_token}-{quote_token}"


class RaydiumExchangeDataSpot(RaydiumExchangeData):
    """Raydium Spot Exchange Configuration."""

    def __init__(self) -> None:
        """Initialize Raydium spot exchange data."""
        super().__init__()
        self.asset_type = "SPOT"
