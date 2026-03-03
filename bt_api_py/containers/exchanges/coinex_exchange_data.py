"""
CoinEx Exchange Data Configuration
"""

import os
from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.functions.log_message import SpdLogManager

logger = SpdLogManager(
    file_name="coinex_exchange_data.log", logger_name="coinex_data", print_info=False
).create_logger()

_coinex_config = None
_coinex_config_loaded = False


def _get_coinex_config():
    """Load CoinEx YAML configuration."""
    global _coinex_config, _coinex_config_loaded
    if _coinex_config_loaded:
        return _coinex_config
    try:
        from bt_api_py.config_loader import load_exchange_config
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "coinex.yaml",
        )
        if os.path.exists(config_path):
            _coinex_config = load_exchange_config(config_path)
        _coinex_config_loaded = True
    except Exception as e:
        logger.warn(f"Failed to load coinex.yaml config: {e}")
    return _coinex_config


class CoinExExchangeData(ExchangeData):
    """Base class for CoinEx exchange."""

    def __init__(self):
        super().__init__()
        self.exchange_name = "coinex"
        self.rest_url = "https://api.coinex.com"
        self.wss_url = "wss://stream.coinex.com"
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
        self.legal_currency = ["USDT", "USD", "BTC", "ETH", "USDC"]

    def _load_from_config(self, asset_type):
        """Load from YAML config."""
        config = _get_coinex_config()
        if config is None:
            return False
        asset_cfg = config.asset_types.get(asset_type)
        if asset_cfg is None:
            return False

        if asset_cfg.exchange_name:
            self.exchange_name = asset_cfg.exchange_name
        if config.base_urls and config.base_urls.rest:
            self.rest_url = config.base_urls.rest.get(asset_type,
                config.base_urls.rest.get("default", self.rest_url))
        if config.base_urls and config.base_urls.wss:
            self.wss_url = config.base_urls.wss.get(asset_type,
                config.base_urls.wss.get("default", self.wss_url))
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


class CoinExExchangeDataSpot(CoinExExchangeData):
    """CoinEx Spot exchange configuration."""

    def __init__(self):
        super().__init__()
        self.asset_type = "spot"
        self.rest_paths = {}
        self.wss_paths = {}
        self.api_key = ""
        self.api_secret = ""
        self._load_from_config("spot")

    def get_period(self, key):
        """Get kline period for API request.

        Args:
            key: Standard period key (e.g., '1m', '5m', '1h', '1d')

        Returns:
            Exchange-specific period string, or original key if not found
        """
        if key not in self.kline_periods:
            return key
        return self.kline_periods[key]
