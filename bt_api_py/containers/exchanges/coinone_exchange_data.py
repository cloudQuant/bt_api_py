"""
Coinone Exchange Data Configuration
"""

import os
from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.functions.log_message import SpdLogManager

logger = SpdLogManager(
    file_name="coinone_exchange_data.log", logger_name="coinone_data", print_info=False
).create_logger()

_coinone_config = None
_coinone_config_loaded = False


def _get_coinone_config():
    """Load Coinone YAML configuration."""
    global _coinone_config, _coinone_config_loaded
    if _coinone_config_loaded:
        return _coinone_config
    try:
        from bt_api_py.config_loader import load_exchange_config
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "coinone.yaml",
        )
        if os.path.exists(config_path):
            _coinone_config = load_exchange_config(config_path)
        _coinone_config_loaded = True
    except Exception as e:
        logger.warn(f"Failed to load coinone.yaml config: {e}")
    return _coinone_config


class CoinoneExchangeData(ExchangeData):
    """Base class for Coinone exchange."""

    def __init__(self):
        super().__init__()
        self.exchange_name = "coinone"
        self.rest_url = "https://api.coinone.co.kr"
        self.wss_url = "wss://stream.coinone.co.kr"
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
        self.legal_currency = ["KRW"]

    def _load_from_config(self, asset_type):
        """Load from YAML config."""
        config = _get_coinone_config()
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


class CoinoneExchangeDataSpot(CoinoneExchangeData):
    """Coinone Spot exchange configuration."""

    def __init__(self):
        super().__init__()
        self.asset_type = "spot"
        self.rest_paths = {}
        self.wss_paths = {}
        self._load_from_config("spot")

    def get_period(self, key):
        """Get kline period interval.

        Coinone uses the same format as the input key (e.g., '1m', '1h', '1d').
        """
        if key in self.kline_periods:
            return self.kline_periods[key]
        return key
