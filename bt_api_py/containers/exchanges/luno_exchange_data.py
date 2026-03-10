"""Luno Exchange Data Configuration."""

import os
from typing import Any

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("luno_exchange_data")

_luno_config = None
_luno_config_loaded = False


def _get_luno_config() -> Any | None:
    """Load Luno YAML configuration."""
    global _luno_config, _luno_config_loaded
    if _luno_config_loaded:
        return _luno_config
    try:
        from bt_api_py.config_loader import load_exchange_config

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "luno.yaml",
        )
        if os.path.exists(config_path):
            _luno_config = load_exchange_config(config_path)
        _luno_config_loaded = True
    except Exception as e:
        logger.warn("Failed to load luno.yaml config: %s", e)
    return _luno_config


class LunoExchangeData(ExchangeData):
    """Base class for Luno exchange."""

    def __init__(self) -> None:
        super().__init__()
        self.exchange_name = "LUNO___SPOT"
        self.rest_url = "https://api.luno.com/api/1"
        self.rest_exchange_url = "https://api.luno.com/api/exchange/1"
        self.wss_url = "wss://api.luno.com/api/1"
        self.kline_periods = {
            "1m": "60",
            "5m": "300",
            "15m": "900",
            "30m": "1800",
            "1h": "3600",
            "3h": "10800",
            "4h": "14400",
            "1d": "86400",
            "3d": "259200",
            "1w": "604800",
        }
        self.reverse_kline_periods = {v: k for k, v in self.kline_periods.items()}
        self.legal_currency = ["ZAR", "NGN", "MYR", "IDR", "EUR", "GBP", "UGX", "AUD", "ZMW"]
        # API credentials (for authenticated requests)
        self.api_key = os.getenv("LUNO_API_KEY", "")
        self.api_secret = os.getenv("LUNO_API_SECRET", "")

    def _load_from_config(self, asset_type) -> bool:
        """Load from YAML config."""
        config = _get_luno_config()
        if config is None:
            return False
        asset_cfg = config.asset_types.get(asset_type)
        if asset_cfg is None:
            return False

        if asset_cfg.exchange_name:
            self.exchange_name = asset_cfg.exchange_name
        if config.base_urls and config.base_urls.rest:
            # Get spot-specific URL if available, otherwise use default
            self.rest_url = config.base_urls.rest.get(
                asset_type, config.base_urls.rest.get("default", self.rest_url)
            )
        if config.base_urls and hasattr(config.base_urls, "rest_exchange"):
            self.rest_exchange_url = config.base_urls.rest_exchange
        if config.base_urls and config.base_urls.wss:
            self.wss_url = config.base_urls.wss.get(
                asset_type, config.base_urls.wss.get("default", self.wss_url)
            )
        if asset_cfg.rest_paths:
            self.rest_paths.update(asset_cfg.rest_paths)

        # kline_periods - load from YAML, prefer asset-specific config
        kp = asset_cfg.kline_periods or (config.kline_periods if config.kline_periods else None)
        if kp:
            self.kline_periods = dict(kp)
            self.reverse_kline_periods = {v: k for k, v in self.kline_periods.items()}

        # legal_currency - load from YAML
        lc = asset_cfg.legal_currency or (config.legal_currency if config.legal_currency else None)
        if lc:
            self.legal_currency = list(lc)

        return True

    # noinspection PyMethodMayBeStatic
    def get_period(self, key, default=None):
        """Get kline period value from key.

        Args:
            key: The period key (e.g., "1h", "1d")
            default: Default value to return if key not found

        Returns:
            The period value or default if not found

        """
        if key not in self.kline_periods:
            return default if default is not None else key
        return self.kline_periods[key]


class LunoExchangeDataSpot(LunoExchangeData):
    """Luno Spot exchange configuration."""

    def __init__(self) -> None:
        super().__init__()
        self.asset_type = "spot"
        self.rest_paths = {}
        self.wss_paths = {}
        self._load_from_config("spot")
