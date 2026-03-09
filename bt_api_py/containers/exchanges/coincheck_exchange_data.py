"""Coincheck Exchange Data Configuration."""

import os
from typing import Any

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("coincheck_exchange_data")

_coincheck_config = None
_coincheck_config_loaded = False


def _get_coincheck_config() -> Any | None:
    """Load Coincheck YAML configuration."""
    global _coincheck_config, _coincheck_config_loaded
    if _coincheck_config_loaded:
        return _coincheck_config
    try:
        from bt_api_py.config_loader import load_exchange_config

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "coincheck.yaml",
        )
        if os.path.exists(config_path):
            _coincheck_config = load_exchange_config(config_path)
        _coincheck_config_loaded = True
    except Exception as e:
        logger.warn(f"Failed to load coincheck.yaml config: {e}")
    return _coincheck_config


class CoincheckExchangeData(ExchangeData):
    """Base class for Coincheck exchange."""

    def __init__(self) -> None:
        super().__init__()
        self.exchange_name = "coincheck"
        self.rest_url = "https://coincheck.com"
        self.wss_url = "wss://ws-api.coincheck.com"
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
        self.legal_currency = ["JPY"]

    def _load_from_config(self, asset_type) -> bool:
        """Load from YAML config."""
        config = _get_coincheck_config()
        if config is None:
            return False
        asset_cfg = config.asset_types.get(asset_type)
        if asset_cfg is None:
            return False

        if asset_cfg.exchange_name:
            self.exchange_name = asset_cfg.exchange_name
        if config.base_urls and hasattr(config.base_urls, "rest"):
            rest_url = config.base_urls.rest
            # Handle both dict and string
            if isinstance(rest_url, dict):
                self.rest_url = rest_url.get(asset_type, rest_url.get("default", self.rest_url))
            else:
                self.rest_url = rest_url
        if config.base_urls and hasattr(config.base_urls, "wss"):
            wss_url = config.base_urls.wss
            # Handle both dict and string
            if isinstance(wss_url, dict):
                self.wss_url = wss_url.get(asset_type, wss_url.get("default", self.wss_url))
            else:
                self.wss_url = wss_url
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


class CoincheckExchangeDataSpot(CoincheckExchangeData):
    """Coincheck Spot exchange configuration."""

    def __init__(self) -> None:
        super().__init__()
        self.asset_type = "spot"
        self.rest_paths = {}
        self.wss_paths = {}
        self._load_from_config("spot")
