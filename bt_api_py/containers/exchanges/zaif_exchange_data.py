"""
Zaif Exchange Data Configuration
"""

import os
from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.functions.log_message import SpdLogManager

logger = SpdLogManager(
    file_name="zaif_exchange_data.log", logger_name="zaif_data", print_info=False
).create_logger()

_zaif_config = None
_zaif_config_loaded = False


def _get_zaif_config():
    """Load Zaif YAML configuration."""
    global _zaif_config, _zaif_config_loaded
    if _zaif_config_loaded:
        return _zaif_config
    try:
        from bt_api_py.config_loader import load_exchange_config
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "zaif.yaml",
        )
        if os.path.exists(config_path):
            _zaif_config = load_exchange_config(config_path)
        _zaif_config_loaded = True
    except Exception as e:
        logger.warn(f"Failed to load zaif.yaml config: {e}")
    return _zaif_config


class ZaifExchangeData(ExchangeData):
    """Base class for Zaif exchange."""

    def __init__(self):
        super().__init__()
        self.exchange_name = "zaif"
        self.rest_url = "https://api.zaif.jp"
        self.wss_url = "wss://ws.zaif.jp:8888"
        self.kline_periods = {
            "1m": "1min",
            "5m": "5min",
            "15m": "15min",
            "30m": "30min",
            "1h": "1hour",
            "4h": "4hour",
            "1d": "1day",
            "1w": "1week",
        }
        self.legal_currency = ["JPY", "BTC", "ETH"]

    def _load_from_config(self, asset_type):
        """Load from YAML config."""
        config = _get_zaif_config()
        if config is None:
            return False
        asset_cfg = config.asset_types.get(asset_type)
        if asset_cfg is None:
            return False

        if asset_cfg.exchange_name:
            self.exchange_name = asset_cfg.exchange_name
        if config.base_urls and config.base_urls.rest:
            rest = config.base_urls.rest
            self.rest_url = rest.get(asset_type, rest.get("default", self.rest_url)) if isinstance(rest, dict) else rest
        if config.base_urls and config.base_urls.wss:
            wss = config.base_urls.wss
            self.wss_url = wss.get(asset_type, wss.get("default", self.wss_url)) if isinstance(wss, dict) else wss
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


class ZaifExchangeDataSpot(ZaifExchangeData):
    """Zaif Spot exchange configuration."""

    def __init__(self):
        super().__init__()
        self.asset_type = "spot"
        self.rest_paths = {}
        self.wss_paths = {}
        self.api_key = os.getenv("ZAIF_API_KEY", "")
        self.api_secret = os.getenv("ZAIF_API_SECRET", "")
        self._load_from_config("spot")
