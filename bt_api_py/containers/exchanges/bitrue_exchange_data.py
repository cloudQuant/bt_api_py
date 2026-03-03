"""
Bitrue Exchange Data Configuration
"""

import os
from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.functions.log_message import SpdLogManager

logger = SpdLogManager(
    file_name="bitrue_exchange_data.log", logger_name="bitrue_data", print_info=False
).create_logger()

_bitrue_config = None
_bitrue_config_loaded = False


def _get_bitrue_config():
    """Load Bitrue YAML configuration."""
    global _bitrue_config, _bitrue_config_loaded
    if _bitrue_config_loaded:
        return _bitrue_config
    try:
        from bt_api_py.config_loader import load_exchange_config
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "bitrue.yaml",
        )
        if os.path.exists(config_path):
            _bitrue_config = load_exchange_config(config_path)
        _bitrue_config_loaded = True
    except Exception as e:
        logger.warn(f"Failed to load bitrue.yaml config: {e}")
    return _bitrue_config


class BitrueExchangeData(ExchangeData):
    """Base class for Bitrue exchange."""

    def __init__(self):
        super().__init__()
        self.exchange_name = "bitrue"
        self.rest_url = "https://www.bitrue.com"
        self.wss_url = "wss://ws.bitrue.com/kline-api/ws"
        self.rest_paths = {}
        self.wss_paths = {}
        self.kline_periods = {
            "1m": "1m",
            "5m": "5m",
            "15m": "15m",
            "30m": "30m",
            "1h": "1h",
            "2h": "2h",
            "4h": "4h",
            "12h": "12h",
            "1d": "1d",
            "1w": "1w",
        }
        self.legal_currency = ["USDT", "BTC", "ETH", "XRP"]

    def _load_from_config(self, asset_type):
        """Load from YAML config."""
        config = _get_bitrue_config()
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
        if asset_cfg.kline_periods:
            self.kline_periods = dict(asset_cfg.kline_periods)
        lc = asset_cfg.legal_currency or (config.legal_currency if config.legal_currency else None)
        if lc:
            self.legal_currency = list(lc)
        return True


class BitrueExchangeDataSpot(BitrueExchangeData):
    """Bitrue Spot exchange configuration."""

    def __init__(self):
        super().__init__()
        self.asset_type = "SPOT"
        self._load_from_config("spot")

    def get_symbol(self, symbol):
        """Convert symbol to Bitrue format (e.g., BTC-USDT -> BTCUSDT)."""
        return symbol.replace("-", "").upper()

    def get_period(self, key):
        """Get kline period for API."""
        return self.kline_periods.get(key, key)

    def get_rest_path(self, key):
        if key not in self.rest_paths or self.rest_paths[key] == "":
            self.raise_path_error(self.exchange_name, key)
        return self.rest_paths[key]
