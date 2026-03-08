"""
Bitbank Exchange Data Configuration
"""

import os

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("bitbank_exchange_data")

_bitbank_config = None
_bitbank_config_loaded = False


def _get_bitbank_config():
    """Load Bitbank YAML configuration."""
    global _bitbank_config, _bitbank_config_loaded
    if _bitbank_config_loaded:
        return _bitbank_config
    try:
        from bt_api_py.config_loader import load_exchange_config

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "bitbank.yaml",
        )
        if os.path.exists(config_path):
            _bitbank_config = load_exchange_config(config_path)
        _bitbank_config_loaded = True
    except Exception as e:
        logger.warn(f"Failed to load bitbank.yaml config: {e}")
    return _bitbank_config


class BitbankExchangeData(ExchangeData):
    """Base class for Bitbank exchange."""

    def __init__(self):
        super().__init__()
        self.exchange_name = "bitbank"
        self.rest_url = "https://public.bitbank.cc"
        self.rest_private_url = "https://api.bitbank.cc/v1"
        self.wss_url = "wss://stream.bitbank.cc"
        self.kline_periods = {
            "1m": "1min",
            "5m": "5min",
            "15m": "15min",
            "30m": "30min",
            "1h": "1hour",
            "4h": "4hour",
            "8h": "8hour",
            "12h": "12hour",
            "1d": "1day",
            "1w": "1week",
            "1month": "1month",
        }
        self.legal_currency = ["JPY", "BTC", "ETH"]

    def _load_from_config(self, asset_type):
        """Load from YAML config."""
        config = _get_bitbank_config()
        if config is None:
            return False
        asset_cfg = config.asset_types.get(asset_type)
        if asset_cfg is None:
            return False

        if asset_cfg.exchange_name:
            self.exchange_name = asset_cfg.exchange_name
        if config.base_urls:
            if config.base_urls.rest:
                rest = config.base_urls.rest
                self.rest_url = (
                    rest.get(asset_type, rest.get("default", self.rest_url))
                    if isinstance(rest, dict)
                    else rest
                )
            if hasattr(config.base_urls, "rest_private") and config.base_urls.rest_private:
                rest_private = config.base_urls.rest_private
                self.rest_private_url = (
                    rest_private.get(asset_type, rest_private.get("default", self.rest_private_url))
                    if isinstance(rest_private, dict)
                    else rest_private
                )
            if config.base_urls.wss:
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


class BitbankExchangeDataSpot(BitbankExchangeData):
    """Bitbank Spot exchange configuration."""

    def __init__(self):
        super().__init__()
        self.asset_type = "spot"
        self.rest_paths = {}
        self.wss_paths = {}
        self._load_from_config("spot")
