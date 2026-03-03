"""
Bitstamp Exchange Data Configuration
"""

import os
from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.functions.log_message import SpdLogManager

logger = SpdLogManager(
    file_name="bitstamp_exchange_data.log", logger_name="bitstamp_data", print_info=False
).create_logger()

_bitstamp_config = None
_bitstamp_config_loaded = False


def _get_bitstamp_config():
    """Load Bitstamp YAML configuration."""
    global _bitstamp_config, _bitstamp_config_loaded
    if _bitstamp_config_loaded:
        return _bitstamp_config
    try:
        from bt_api_py.config_loader import load_exchange_config
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "bitstamp.yaml",
        )
        if os.path.exists(config_path):
            _bitstamp_config = load_exchange_config(config_path)
        _bitstamp_config_loaded = True
    except Exception as e:
        logger.warn(f"Failed to load bitstamp.yaml config: {e}")
    return _bitstamp_config


class BitstampExchangeData(ExchangeData):
    """Base class for Bitstamp exchange."""

    def __init__(self):
        super().__init__()
        self.exchange_name = "bitstamp"
        self.rest_url = "https://www.bitstamp.net/api/v2"
        self.wss_url = "wss://ws.bitstamp.net"
        self.rest_paths = {}
        self.wss_paths = {}
        self.kline_periods = {
            "1m": "60",
            "3m": "180",
            "5m": "300",
            "15m": "900",
            "30m": "1800",
            "1h": "3600",
            "2h": "7200",
            "4h": "14400",
            "6h": "21600",
            "12h": "43200",
            "1d": "86400",
            "3d": "259200",
        }
        self.legal_currency = ["USD", "EUR", "GBP", "USDC"]

    def _load_from_config(self, asset_type):
        """Load from YAML config."""
        config = _get_bitstamp_config()
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


class BitstampExchangeDataSpot(BitstampExchangeData):
    """Bitstamp Spot exchange configuration."""

    def __init__(self):
        super().__init__()
        self.asset_type = "spot"
        self._load_from_config("spot")

    def get_symbol(self, symbol):
        """Convert symbol to Bitstamp format (e.g., BTC-USD -> btcusd)."""
        # Bitstamp uses lowercase without separator
        return symbol.replace("-", "").lower()

    def get_period(self, key):
        """Get kline period for API (in seconds)."""
        return self.kline_periods.get(key, key)

    def get_rest_path(self, key):
        if key not in self.rest_paths or self.rest_paths[key] == "":
            self.raise_path_error(self.exchange_name, key)
        return self.rest_paths[key]
