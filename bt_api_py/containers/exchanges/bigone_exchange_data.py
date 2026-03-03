"""
BigONE Exchange Data Configuration
"""

import os
from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.functions.log_message import SpdLogManager

logger = SpdLogManager(
    file_name="bigone_exchange_data.log", logger_name="bigone_data", print_info=False
).create_logger()

_bigone_config = None
_bigone_config_loaded = False


def _get_bigone_config():
    """Load BigONE YAML configuration."""
    global _bigone_config, _bigone_config_loaded
    if _bigone_config_loaded:
        return _bigone_config
    try:
        from bt_api_py.config_loader import load_exchange_config
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "bigone.yaml",
        )
        if os.path.exists(config_path):
            _bigone_config = load_exchange_config(config_path)
        _bigone_config_loaded = True
    except Exception as e:
        logger.warn(f"Failed to load bigone.yaml config: {e}")
    return _bigone_config


class BigONEExchangeData(ExchangeData):
    """Base class for BigONE exchange."""

    def __init__(self):
        super().__init__()
        self.exchange_name = "bigone"
        self.rest_url = "https://big.one/api/v3"
        self.wss_url = "wss://big.one/ws/v2"
        self.kline_periods = {
            "1m": "min1",
            "5m": "min5",
            "15m": "min15",
            "30m": "min30",
            "1h": "hour1",
            "4h": "hour4",
            "12h": "hour12",
            "1d": "day1",
            "1w": "week1",
            "1M": "month1",
        }
        self.legal_currency = ["USDT", "USD", "BTC", "ETH", "EUR"]
        # API credentials (for authenticated requests)
        self.api_key = os.getenv("BIGONE_API_KEY", "")
        self.api_secret = os.getenv("BIGONE_API_SECRET", "")

    def _load_from_config(self, asset_type):
        """Load from YAML config."""
        config = _get_bigone_config()
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


class BigONEExchangeDataSpot(BigONEExchangeData):
    """BigONE Spot exchange configuration."""

    def __init__(self):
        super().__init__()
        self.asset_type = "spot"
        self.rest_paths = {}
        self.wss_paths = {}
        self._load_from_config("spot")
