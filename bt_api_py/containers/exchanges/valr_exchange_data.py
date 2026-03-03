"""
VALR Exchange Data Configuration
"""

import os
from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.functions.log_message import SpdLogManager

logger = SpdLogManager(
    file_name="valr_exchange_data.log", logger_name="valr_data", print_info=False
).create_logger()

_valr_config = None
_valr_config_loaded = False


def _get_valr_config():
    """Load VALR YAML configuration."""
    global _valr_config, _valr_config_loaded
    if _valr_config_loaded:
        return _valr_config
    try:
        from bt_api_py.config_loader import load_exchange_config
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "valr.yaml",
        )
        if os.path.exists(config_path):
            _valr_config = load_exchange_config(config_path)
        _valr_config_loaded = True
    except Exception as e:
        logger.warn(f"Failed to load valr.yaml config: {e}")
    return _valr_config


class ValrExchangeData(ExchangeData):
    """Base class for VALR exchange."""

    def __init__(self):
        super().__init__()
        self.exchange_name = "valr"
        self.rest_url = "https://api.valr.com"
        self.wss_url = "wss://api.valr.com/ws"
        self.kline_periods = {
            "1m": "1m",
            "3m": "3m",
            "5m": "5m",
            "15m": "15m",
            "30m": "30m",
            "1h": "1h",
            "2h": "2h",
            "4h": "4h",
            "6h": "6h",
            "12h": "12h",
            "1d": "1d",
            "1w": "1w",
            "1M": "1M",
        }
        self.legal_currency = ["USDC", "ZAR", "BTC", "ETH"]

    def _load_from_config(self, asset_type):
        """Load from YAML config."""
        config = _get_valr_config()
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


class ValrExchangeDataSpot(ValrExchangeData):
    """VALR Spot exchange configuration."""

    def __init__(self):
        super().__init__()
        self.asset_type = "spot"
        self.rest_paths = {}
        self.wss_paths = {}
        self._load_from_config("spot")
