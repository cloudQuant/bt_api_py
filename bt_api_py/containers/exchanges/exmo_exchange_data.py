"""EXMO Exchange Data Configuration."""

import os
from typing import Any

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("exmo_exchange_data")

_exmo_config = None
_exmo_config_loaded = False


def _get_exmo_config() -> Any | None:
    """Load EXMO YAML configuration."""
    global _exmo_config, _exmo_config_loaded
    if _exmo_config_loaded:
        return _exmo_config
    try:
        from bt_api_py.config_loader import load_exchange_config

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "exmo.yaml",
        )
        if os.path.exists(config_path):
            _exmo_config = load_exchange_config(config_path)
        _exmo_config_loaded = True
    except Exception as e:
        logger.warn("Failed to load exmo.yaml config: %s", e)
    return _exmo_config


class ExmoExchangeData(ExchangeData):
    """Base class for EXMO exchange."""

    def __init__(self) -> None:
        super().__init__()
        self.exchange_name = "exmo"
        self.rest_url = "https://api.exmo.com/v1.1"
        self.wss_url = "wss://ws.exmo.me/v1.1"
        self.kline_periods = {
            "1m": "1",
            "5m": "5",
            "15m": "15",
            "30m": "30",
            "1h": "60",
            "2h": "120",
            "4h": "240",
            "1d": "D",
            "1w": "W",
            "1M": "M",
        }
        self.legal_currency = [
            "USDT",
            "USD",
            "EUR",
            "UAH",
            "RUB",
            "GBP",
            "PLN",
            "TRY",
            "BTC",
            "ETH",
        ]

    def _load_from_config(self, asset_type) -> bool:
        """Load from YAML config."""
        config = _get_exmo_config()
        if config is None:
            return False
        asset_cfg = config.asset_types.get(asset_type)
        if asset_cfg is None:
            return False

        if asset_cfg.exchange_name:
            self.exchange_name = asset_cfg.exchange_name
        if config.base_urls:
            if config.base_urls.rest:
                self.rest_url = config.base_urls.rest.get(asset_type, self.rest_url)
            if config.base_urls.wss:
                self.wss_url = config.base_urls.wss.get(asset_type, self.wss_url)
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


class ExmoExchangeDataSpot(ExmoExchangeData):
    """EXMO Spot exchange configuration."""

    def __init__(self) -> None:
        super().__init__()
        self.asset_type = "spot"
        self.rest_paths = {}
        self.wss_paths = {}
        self._load_from_config("spot")
