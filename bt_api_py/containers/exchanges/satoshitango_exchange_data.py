from typing import Any

"""SatoshiTango Exchange Data Configuration."""

import os

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("satoshitango_exchange_data")

_satoshitango_config = None
_satoshitango_config_loaded = False


def _get_satoshitango_config() -> Any | None:
    """Load SatoshiTango YAML configuration."""
    global _satoshitango_config, _satoshitango_config_loaded
    if _satoshitango_config_loaded:
        return _satoshitango_config
    try:
        from bt_api_py.config_loader import load_exchange_config

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "satoshitango.yaml",
        )
        if os.path.exists(config_path):
            _satoshitango_config = load_exchange_config(config_path)
        _satoshitango_config_loaded = True
    except Exception as e:
        logger.warn(f"Failed to load satoshitango.yaml config: {e}")
    return _satoshitango_config


class SatoshiTangoExchangeData(ExchangeData):
    """Base class for SatoshiTango exchange."""

    def __init__(self) -> None:
        super().__init__()
        self.exchange_name = "satoshitango"
        self.rest_url = "https://api.satoshitango.com"
        self.wss_url = ""
        self.kline_periods = {
            "1m": "1",
            "5m": "5",
            "15m": "15",
            "30m": "30",
            "1h": "60",
            "4h": "240",
            "1d": "1d",
            "1w": "1w",
        }
        self.legal_currency = ["ARS", "USD", "BTC", "ETH", "USDT"]

    def get_period(self, period: str) -> str:
        """Get period format for SatoshiTango API.

        Args:
            period: Period name (e.g., '1m', '5m', '1h', '1d')

        Returns:
            str: SatoshiTango format period

        """
        return self.kline_periods.get(period, period)

    def _load_from_config(self, asset_type) -> bool:
        """Load from YAML config."""
        config = _get_satoshitango_config()
        if config is None:
            return False
        asset_cfg = config.asset_types.get(asset_type)
        if asset_cfg is None:
            return False

        if asset_cfg.exchange_name:
            self.exchange_name = asset_cfg.exchange_name
        if config.base_urls and config.base_urls.rest:
            self.rest_url = config.base_urls.rest.get(asset_type, self.rest_url)
        if config.base_urls and config.base_urls.wss:
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


class SatoshiTangoExchangeDataSpot(SatoshiTangoExchangeData):
    """SatoshiTango Spot exchange configuration."""

    def __init__(self) -> None:
        super().__init__()
        self.asset_type = "spot"
        self.rest_paths = {}
        self.wss_paths = {}
        self._load_from_config("spot")
