"""Foxbit Exchange Data Configuration."""

import os
from typing import Any

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("foxbit_exchange_data")

_foxbit_config = None
_foxbit_config_loaded = False


def _get_foxbit_config() -> Any | None:
    """Load Foxbit YAML configuration."""
    global _foxbit_config, _foxbit_config_loaded
    if _foxbit_config_loaded:
        return _foxbit_config
    try:
        from bt_api_py.config_loader import load_exchange_config

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "foxbit.yaml",
        )
        if os.path.exists(config_path):
            _foxbit_config = load_exchange_config(config_path)
        _foxbit_config_loaded = True
    except (OSError, ValueError, KeyError, ImportError) as e:
        logger.warning(f"Failed to load foxbit.yaml config: {e}")
    return _foxbit_config


class FoxbitExchangeData(ExchangeData):
    """Base class for Foxbit exchange."""

    def __init__(self) -> None:
        super().__init__()
        self.exchange_name = "foxbit"
        self.rest_url = "https://api.foxbit.com.br"
        self.wss_url = "wss://ws.foxbit.com.br"
        # API credentials (for authenticated requests)
        self.api_key = None
        self.api_secret = None
        self.kline_periods = {
            "1m": "1m",
            "5m": "5m",
            "15m": "15m",
            "30m": "30m",
            "1h": "1h",
            "2h": "2h",
            "4h": "4h",
            "6h": "6h",
            "12h": "12h",
            "1d": "1d",
            "1w": "1w",
            "2w": "2w",
            "1M": "1M",
        }
        self.legal_currency = ["BRL"]

    def _load_from_config(self, asset_type) -> bool:
        """Load from YAML config."""
        config = _get_foxbit_config()
        if config is None:
            return False
        asset_cfg = config.asset_types.get(asset_type)
        if asset_cfg is None:
            return False

        if asset_cfg.exchange_name:
            self.exchange_name = asset_cfg.exchange_name
        # URLs - extract asset_type specific URL from the dict
        if config.base_urls and config.base_urls.rest:
            if isinstance(config.base_urls.rest, dict):
                # Try to get asset_type specific URL, fallback to 'default', fallback to 'spot'
                self.rest_url = config.base_urls.rest.get(
                    asset_type,
                    config.base_urls.rest.get(
                        "default", config.base_urls.rest.get("spot", self.rest_url)
                    ),
                )
            else:
                self.rest_url = config.base_urls.rest
        if config.base_urls and config.base_urls.wss:
            if isinstance(config.base_urls.wss, dict):
                # Try to get asset_type specific URL, fallback to 'default', fallback to 'spot'
                self.wss_url = config.base_urls.wss.get(
                    asset_type,
                    config.base_urls.wss.get(
                        "default", config.base_urls.wss.get("spot", self.wss_url)
                    ),
                )
            else:
                self.wss_url = config.base_urls.wss
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


class FoxbitExchangeDataSpot(FoxbitExchangeData):
    """Foxbit Spot exchange configuration."""

    def __init__(self) -> None:
        super().__init__()
        self.asset_type = "spot"
        self.rest_paths = {}
        self.wss_paths = {}
        self._load_from_config("spot")
