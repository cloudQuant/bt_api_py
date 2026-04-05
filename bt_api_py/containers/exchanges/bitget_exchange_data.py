"""Bitget Exchange Data Configuration."""

from __future__ import annotations

import os
from typing import Any

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("bitget_exchange_data")

_bitget_config = None
_bitget_config_loaded = False


def _get_bitget_config() -> Any | None:
    """Lazy load and cache Bitget YAML configuration.

    Returns:
        Configuration dictionary or None if not found
    """
    global _bitget_config, _bitget_config_loaded
    if _bitget_config_loaded:
        return _bitget_config
    try:
        from bt_api_py.config_loader import load_exchange_config

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "bitget.yaml",
        )
        if os.path.exists(config_path):
            _bitget_config = load_exchange_config(config_path)
        _bitget_config_loaded = True
        return _bitget_config
    except (OSError, ValueError, KeyError, ImportError) as e:
        logger.error(f"Failed to load bitget.yaml config: {e}")
        _bitget_config_loaded = True
        return None
    return None


class BitgetExchangeData(ExchangeData):
    """Bitget Exchange Data Configuration."""

    def __init__(self) -> None:
        """Initialize Bitget exchange data with default configuration."""
        super().__init__()
        self.exchange_name = "BITGET"
        self.rest_url = "https://api.bitget.com"
        self.wss_url = "wss://ws.bitget.com/spot/v1/stream"
        self.api_version = "v2"

        self.rest_paths: dict[str, str] = {}
        self.wss_paths: dict[str, Any] = {}
        self.kline_periods: dict[str, str] = {}
        self.reverse_kline_periods: dict[str, str] = {}
        self.exchange_info: dict[str, Any] = {}

        self._load_from_config("spot")

    def _load_from_config(self, asset_type: str) -> bool:
        """Load exchange parameters from YAML configuration file.

        Args:
            asset_type: Asset type key, e.g., 'spot', 'swap', 'margin'

        Returns:
            bool: Whether loading was successful
        """
        config = _get_bitget_config()
        if config is None:
            return False
        asset_cfg = config.asset_types.get(asset_type)
        if asset_cfg is None:
            return False

        if asset_cfg.exchange_name:
            self.exchange_name = asset_cfg.exchange_name

        if config.base_urls:
            self.rest_url = config.base_urls.rest.get(asset_type, self.rest_url)
            self.wss_url = config.base_urls.wss.get(asset_type, self.wss_url)

        if hasattr(asset_cfg, "rest_paths") and asset_cfg.rest_paths:
            self.rest_paths = dict(asset_cfg.rest_paths)

        if hasattr(asset_cfg, "wss_paths") and asset_cfg.wss_paths:
            self.wss_paths = dict(asset_cfg.wss_paths)

        kp = asset_cfg.kline_periods or (config.kline_periods if config.kline_periods else None)
        if kp:
            self.kline_periods = dict(kp)
            self.reverse_kline_periods = {v: k for k, v in self.kline_periods.items()}

        lc = asset_cfg.legal_currency or (config.legal_currency if config.legal_currency else None)
        if lc:
            self.legal_currency = list(lc)

        return True

    def get_symbol(self, symbol: str) -> str:
        """Format trading pair name for Bitget.

        Args:
            symbol: Raw trading pair name (e.g., 'BTC-USDT')

        Returns:
            str: Formatted trading pair name (e.g., 'BTCUSDT')
        """
        return symbol.replace("-", "").replace("/", "").upper()

    def get_period(self, period: str) -> str:
        """Convert period name.

        Args:
            period: Period name (e.g., '1m', '1h')

        Returns:
            str: Converted period name
        """
        return self.kline_periods.get(period, period)

    def get_rest_path(self, request_type: str, **kwargs: Any) -> str:
        """Get REST API endpoint path.

        Args:
            request_type: Request type key

        Returns:
            str: REST API path
        """
        if request_type not in self.rest_paths or self.rest_paths[request_type] == "":
            self.raise_path_error(self.exchange_name, request_type)
        return self.rest_paths[request_type]


class BitgetExchangeDataSpot(BitgetExchangeData):
    """Bitget Spot Exchange Configuration."""

    def __init__(self) -> None:
        """Initialize Bitget spot exchange data."""
        super().__init__()
        self.asset_type = "spot"


class BitgetExchangeDataSwap(BitgetExchangeData):
    """Bitget Swap Exchange Configuration."""

    def __init__(self) -> None:
        """Initialize Bitget swap exchange data."""
        super().__init__()
        self._load_from_config("swap")
        self.asset_type = "swap"
