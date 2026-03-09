from typing import Any

"""CoinDCX Exchange Data Configuration."""

import os

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("coindcx_exchange_data")

_coindcx_config = None
_coindcx_config_loaded = False


def _get_coindcx_config() -> Any | None:
    """Load CoinDCX YAML configuration."""
    global _coindcx_config, _coindcx_config_loaded
    if _coindcx_config_loaded:
        return _coindcx_config
    try:
        from bt_api_py.config_loader import load_exchange_config

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "coindcx.yaml",
        )
        if os.path.exists(config_path):
            _coindcx_config = load_exchange_config(config_path)
        _coindcx_config_loaded = True
    except Exception:
        pass  # Silently ignore config loading errors
    return _coindcx_config


class CoinDCXExchangeData(ExchangeData):
    """Base class for CoinDCX exchange."""

    def __init__(self) -> None:
        super().__init__()
        self.exchange_name = "coindcx"
        self.rest_url = "https://api.coindcx.com"
        self.wss_url = "wss://stream.coindcx.com"
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
        self.legal_currency = ["INR", "USDT", "BTC", "ETH"]

    def get_period(self, period: str) -> str:
        """Get kline period for API request.

        Args:
            period: Period string like '1m', '5m', '1h', '1d'

        Returns:
            str: API period value

        """
        return self.kline_periods.get(period, period)

    def _load_from_config(self, asset_type) -> bool:
        """Load from YAML config."""
        config = _get_coindcx_config()
        if config is None:
            return False
        asset_cfg = config.asset_types.get(asset_type)
        if asset_cfg is None:
            return False

        if asset_cfg.exchange_name:
            self.exchange_name = asset_cfg.exchange_name
        if config.base_urls and config.base_urls.rest:
            self.rest_url = config.base_urls.rest.get(
                asset_type, config.base_urls.rest.get("default", self.rest_url)
            )
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

        # legal_currency - load from YAML
        lc = asset_cfg.legal_currency or (config.legal_currency if config.legal_currency else None)
        if lc:
            self.legal_currency = list(lc)

        return True


class CoinDCXExchangeDataSpot(CoinDCXExchangeData):
    """CoinDCX Spot exchange configuration."""

    def __init__(self) -> None:
        super().__init__()
        self.asset_type = "spot"
        self.rest_paths = {}
        self.wss_paths = {}
        self._load_from_config("spot")
