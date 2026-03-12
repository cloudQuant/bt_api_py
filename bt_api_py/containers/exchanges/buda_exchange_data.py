"""Buda Exchange Data Configuration."""

import os
from typing import Any

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("buda_exchange_data")

_buda_config = None
_buda_config_loaded = False


def _get_buda_config() -> Any | None:
    """Load Buda YAML configuration."""
    global _buda_config, _buda_config_loaded
    if _buda_config_loaded:
        return _buda_config
    try:
        from bt_api_py.config_loader import load_exchange_config

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "buda.yaml",
        )
        if os.path.exists(config_path):
            _buda_config = load_exchange_config(config_path)
        _buda_config_loaded = True
    except Exception as e:
        logger.warning(f"Failed to load buda.yaml config: {e}")
    return _buda_config


class BudaExchangeData(ExchangeData):
    """Base class for Buda exchange."""

    def __init__(self) -> None:
        super().__init__()
        self.exchange_name = "buda"
        self.rest_url = "https://api.buda.com"
        self.wss_url = "wss://api.buda.com/websocket"
        self.kline_periods = {
            "1m": "60",
            "5m": "300",
            "15m": "900",
            "30m": "1800",
            "1h": "3600",
            "4h": "14400",
            "1d": "86400",
            "1w": "604800",
        }
        self.legal_currency = ["CLP", "COP", "PEN", "EUR", "USDT"]

    def _load_from_config(self, asset_type) -> bool:
        """Load from YAML config."""
        config = _get_buda_config()
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


class BudaExchangeDataSpot(BudaExchangeData):
    """Buda Spot exchange configuration."""

    def __init__(self) -> None:
        super().__init__()
        self.asset_type = "spot"
        self.rest_paths = {}
        self.wss_paths = {}
        self.api_key = None
        self.api_secret = None
        self._load_from_config("spot")
