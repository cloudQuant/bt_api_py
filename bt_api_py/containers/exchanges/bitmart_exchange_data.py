"""
BitMart Exchange Data Configuration
"""

import os
from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.functions.log_message import SpdLogManager

logger = SpdLogManager(
    file_name="bitmart_exchange_data.log", logger_name="bitmart_data", print_info=False
).create_logger()

_bitmart_config = None
_bitmart_config_loaded = False


def _get_bitmart_config():
    """Load BitMart YAML configuration."""
    global _bitmart_config, _bitmart_config_loaded
    if _bitmart_config_loaded:
        return _bitmart_config
    try:
        from bt_api_py.config_loader import load_exchange_config
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "bitmart.yaml",
        )
        if os.path.exists(config_path):
            _bitmart_config = load_exchange_config(config_path)
        _bitmart_config_loaded = True
    except Exception as e:
        logger.warn(f"Failed to load bitmart.yaml config: {e}")
    return _bitmart_config


class BitmartExchangeData(ExchangeData):
    """Base class for BitMart exchange."""

    def __init__(self):
        super().__init__()
        self.exchange_name = "bitmart"
        self.rest_url = "https://api-cloud.bitmart.com"
        self.wss_url = "wss://ws-manager-compress.bitmart.com/api?protocol=1.1"
        self.rest_paths = {}
        self.wss_paths = {}
        self.kline_periods = {
            "1m": "1",
            "3m": "3",
            "5m": "5",
            "15m": "15",
            "30m": "30",
            "45m": "45",
            "1h": "60",
            "2h": "120",
            "3h": "180",
            "4h": "240",
            "1d": "1440",
            "1w": "10080",
            "1M": "43200",
        }
        self.legal_currency = ["USDT", "USD", "BTC", "ETH", "USDC"]

    def get_symbol(self, symbol):
        """Convert symbol to BitMart format.

        BitMart uses underscore separator: BTC_USDT
        Converts:
        - BTC/USDT -> BTC_USDT
        - BTC-USDT -> BTC_USDT
        """
        if "/" in symbol:
            return symbol.replace("/", "_")
        elif "-" in symbol:
            return symbol.replace("-", "_")
        return symbol

    def get_rest_path(self, key):
        """Get REST API path for given key."""
        if key not in self.rest_paths or self.rest_paths[key] == "":
            self.raise_path_error(self.exchange_name, key)
        return self.rest_paths[key]

    def _load_from_config(self, asset_type):
        """Load from YAML config."""
        config = _get_bitmart_config()
        if config is None:
            return False
        asset_cfg = config.asset_types.get(asset_type)
        if asset_cfg is None:
            return False

        if asset_cfg.exchange_name:
            self.exchange_name = asset_cfg.exchange_name

        # Handle rest_url - try asset_type specific, then default
        if config.base_urls and config.base_urls.rest:
            rest_urls = config.base_urls.rest
            if isinstance(rest_urls, dict):
                # Try asset_type specific, then default
                self.rest_url = rest_urls.get(asset_type) or rest_urls.get("default", self.rest_url)
            else:
                self.rest_url = rest_urls

        # Handle wss_url - try asset_type specific, then default
        if config.base_urls and config.base_urls.wss:
            wss_urls = config.base_urls.wss
            if isinstance(wss_urls, dict):
                # Try asset_type specific, then default
                self.wss_url = wss_urls.get(asset_type) or wss_urls.get("default", self.wss_url)
            else:
                self.wss_url = wss_urls

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


class BitmartExchangeDataSpot(BitmartExchangeData):
    """BitMart Spot exchange configuration."""

    def __init__(self):
        super().__init__()
        self.asset_type = "SPOT"
        self.rest_paths = {}
        self.wss_paths = {}
        # API credentials (for private endpoints)
        self.api_key = None
        self.api_secret = None
        self.api_memo = None
        self._load_from_config("spot")

    def get_period(self, period):
        """Convert period string to BitMart format (minutes).

        Args:
            period: Period string like '1m', '5m', '1h', '1d'

        Returns:
            str: Period in minutes format
        """
        return self.kline_periods.get(period, period)
