"""Giottus Exchange Data Configuration."""

import os
from typing import Any

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("giottus_exchange_data")

_giottus_config = None
_giottus_config_loaded = False


def _get_giottus_config() -> Any | None:
    """Load Giottus YAML configuration."""
    global _giottus_config, _giottus_config_loaded
    if _giottus_config_loaded:
        return _giottus_config
    try:
        from bt_api_py.config_loader import load_exchange_config

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "giottus.yaml",
        )
        if os.path.exists(config_path):
            _giottus_config = load_exchange_config(config_path)
        _giottus_config_loaded = True
    except (OSError, ValueError, KeyError, ImportError) as e:
        logger.warning(f"Failed to load giottus.yaml config: {e}")
    return _giottus_config


class GiottusExchangeData(ExchangeData):
    """Base class for Giottus exchange."""

    def __init__(self) -> None:
        super().__init__()
        self.exchange_name = "giottus"
        self.rest_url = "https://api.giottus.com"
        self.wss_url = "wss://api.giottus.com"
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
        self.legal_currency = ["INR", "USDT", "USD", "BTC", "ETH"]

    def _load_from_config(self, asset_type) -> bool:
        """Load from YAML config."""
        config = _get_giottus_config()
        if config is None:
            return False
        asset_cfg = config.asset_types.get(asset_type)
        if asset_cfg is None:
            return False

        if asset_cfg.exchange_name:
            self.exchange_name = asset_cfg.exchange_name
        if config.base_urls and config.base_urls.rest:
            # Extract the appropriate URL for the asset type
            rest_urls = config.base_urls.rest
            if isinstance(rest_urls, dict):
                # Try asset_type first, then 'default'
                self.rest_url = rest_urls.get(asset_type) or rest_urls.get("default", self.rest_url)
            else:
                self.rest_url = rest_urls
        if config.base_urls and config.base_urls.wss:
            # Extract the appropriate URL for the asset type
            wss_urls = config.base_urls.wss
            if isinstance(wss_urls, dict):
                # Try asset_type first, then 'default'
                self.wss_url = wss_urls.get(asset_type) or wss_urls.get("default", self.wss_url)
            else:
                self.wss_url = wss_urls
        if asset_cfg.rest_paths:
            self.rest_paths.update(asset_cfg.rest_paths)

        # kline_periods - load from YAML, prefer asset-specific config
        kp = asset_cfg.kline_periods or (config.kline_periods if config.kline_periods else None)
        if kp:
            self.kline_periods = dict(kp)

        # legal_currency - load from YAML
        lc = asset_cfg.legal_currency or (config.legal_currency if config.legal_currency else None)
        if lc:
            self.legal_currency = list(lc)

        return True


class GiottusExchangeDataSpot(GiottusExchangeData):
    """Giottus Spot exchange configuration."""

    def __init__(self) -> None:
        super().__init__()
        self.asset_type = "spot"
        self.rest_paths = {}
        self.wss_paths = {}
        self._load_from_config("spot")
