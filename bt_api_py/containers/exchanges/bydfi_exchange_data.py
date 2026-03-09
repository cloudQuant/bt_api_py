from typing import Any

"""BYDFi Exchange Data Configuration."""

import os

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("bydfi_exchange_data")

_bydfi_config = None
_bydfi_config_loaded = False


def _get_bydfi_config() -> Any | None:
    """Load BYDFi YAML configuration."""
    global _bydfi_config, _bydfi_config_loaded
    if _bydfi_config_loaded:
        return _bydfi_config
    try:
        from bt_api_py.config_loader import load_exchange_config

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "bydfi.yaml",
        )
        if os.path.exists(config_path):
            _bydfi_config = load_exchange_config(config_path)
        _bydfi_config_loaded = True
    except Exception as e:
        logger.warn(f"Failed to load bydfi.yaml config: {e}")
    return _bydfi_config


class BYDFiExchangeData(ExchangeData):
    """Base class for BYDFi exchange."""

    def __init__(self) -> None:
        super().__init__()
        self.exchange_name = "bydfi"
        self.rest_url = "https://api.bydfi.com"
        self.wss_url = "wss://stream.bydfi.com"
        self.kline_periods = {
            "1m": "1m",
            "5m": "5m",
            "15m": "15m",
            "30m": "30m",
            "1h": "1h",
            "4h": "4h",
            "1d": "1d",
            "1w": "1w",
        }
        self.reverse_kline_periods = {v: k for k, v in self.kline_periods.items()}
        self.legal_currency = ["USDT", "USD", "BTC", "ETH", "USDC"]
        # API credentials (for authenticated requests)
        self.api_key = None
        self.api_secret = None

    def _load_from_config(self, asset_type) -> bool:
        """Load from YAML config."""
        config = _get_bydfi_config()
        if config is None:
            return False
        asset_cfg = config.asset_types.get(asset_type)
        if asset_cfg is None:
            return False

        if asset_cfg.exchange_name:
            self.exchange_name = asset_cfg.exchange_name
        if config.base_urls and config.base_urls.rest:
            # Extract URL for the specific asset type (spot), fallback to default
            self.rest_url = config.base_urls.rest.get(
                asset_type, config.base_urls.rest.get("default", self.rest_url)
            )
        if config.base_urls and config.base_urls.wss:
            # Extract URL for the specific asset type (spot), fallback to default
            self.wss_url = config.base_urls.wss.get(
                asset_type, config.base_urls.wss.get("default", self.wss_url)
            )
        if asset_cfg.rest_paths:
            self.rest_paths.update(asset_cfg.rest_paths)
        if asset_cfg.wss_paths:
            self.wss_paths.update(asset_cfg.wss_paths)

        # kline_periods - load from YAML, prefer asset-specific config
        kp = asset_cfg.kline_periods or (config.kline_periods if config.kline_periods else None)
        if kp:
            self.kline_periods = dict(kp)

        # legal_currency - load from YAML
        lc = asset_cfg.legal_currency or (config.legal_currency if config.legal_currency else None)
        if lc:
            self.legal_currency = list(lc)

        return True

    def get_symbol(self, symbol: str) -> str:
        """Convert symbol format for BYDFi API.

        BYDFi uses dash separator (BTC-USDT).
        Accepts both BTC/USDT and BTCUSDT formats.
        """
        return symbol.replace("/", "-")

    def get_period(self, key: str) -> str:
        """Get kline period for API."""
        return self.kline_periods.get(key, key)

    def get_rest_path(self, key: str, **kwargs) -> str:
        """Get REST API path by key."""
        if key not in self.rest_paths or self.rest_paths[key] == "":
            self.raise_path_error(self.exchange_name, key)
        return self.rest_paths[key]


class BYDFiExchangeDataSpot(BYDFiExchangeData):
    """BYDFi Spot exchange configuration."""

    def __init__(self) -> None:
        super().__init__()
        self.asset_type = "spot"
        self.rest_paths = {}
        self.wss_paths = {}
        self._load_from_config("spot")
