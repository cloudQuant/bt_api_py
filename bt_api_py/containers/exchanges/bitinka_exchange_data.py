"""Bitinka Exchange Data Configuration."""

import os
from typing import Any

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("bitinka_exchange_data")

_bitinka_config = None
_bitinka_config_loaded = False


def _get_bitinka_config() -> Any | None:
    """Load Bitinka YAML configuration."""
    global _bitinka_config, _bitinka_config_loaded
    if _bitinka_config_loaded:
        return _bitinka_config
    try:
        from bt_api_py.config_loader import load_exchange_config

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "bitinka.yaml",
        )
        if os.path.exists(config_path):
            _bitinka_config = load_exchange_config(config_path)
        _bitinka_config_loaded = True
    except (OSError, ValueError, KeyError, ImportError) as e:
        logger.warning(f"Failed to load bitinka.yaml config: {e}")
    return _bitinka_config


class BitinkaExchangeData(ExchangeData):
    """Base class for Bitinka exchange."""

    def __init__(self) -> None:
        super().__init__()
        self.exchange_name = "bitinka"
        self.rest_url = "https://www.bitinka.com/api"
        self.wss_url = ""
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
        self.legal_currency = ["USDT", "USD", "EUR", "ARS", "BRL", "CLP", "COP", "PEN"]

    def _load_from_config(self, asset_type) -> bool:
        """Load from YAML config."""
        config = _get_bitinka_config()
        if config is None:
            return False
        asset_cfg = config.asset_types.get(asset_type)
        if asset_cfg is None:
            return False

        if asset_cfg.exchange_name:
            self.exchange_name = asset_cfg.exchange_name
        if config.base_urls and config.base_urls.rest:
            rest = config.base_urls.rest
            self.rest_url = (
                rest.get(asset_type, rest.get("default", self.rest_url))
                if isinstance(rest, dict)
                else rest
            )
        if config.base_urls and config.base_urls.wss:
            wss = config.base_urls.wss
            self.wss_url = (
                wss.get(asset_type, wss.get("default", self.wss_url))
                if isinstance(wss, dict)
                else wss
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


class BitinkaExchangeDataSpot(BitinkaExchangeData):
    """Bitinka Spot exchange configuration."""

    def __init__(self) -> None:
        super().__init__()
        self.asset_type = "spot"
        self.rest_paths = {}
        self.wss_paths = {}
        self.api_key = None  # Will be set if provided
        self.api_secret = None  # Will be set if provided
        self._load_from_config("spot")
