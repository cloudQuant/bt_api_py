"""
CoinSpot Exchange Data Configuration
"""

import os
from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.functions.log_message import SpdLogManager

logger = SpdLogManager(
    file_name="coinspot_exchange_data.log", logger_name="coinspot_data", print_info=False
).create_logger()

_coinspot_config = None
_coinspot_config_loaded = False


def _get_coinspot_config():
    """Load CoinSpot YAML configuration."""
    global _coinspot_config, _coinspot_config_loaded
    if _coinspot_config_loaded:
        return _coinspot_config
    try:
        from bt_api_py.config_loader import load_exchange_config
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "coinspot.yaml",
        )
        if os.path.exists(config_path):
            _coinspot_config = load_exchange_config(config_path)
        _coinspot_config_loaded = True
    except Exception as e:
        logger.warn(f"Failed to load coinspot.yaml config: {e}")
    return _coinspot_config


class CoinSpotExchangeData(ExchangeData):
    """Base class for CoinSpot exchange."""

    def __init__(self):
        super().__init__()
        self.exchange_name = "coinspot"
        self.rest_url = "https://www.coinspot.com.au"
        self.wss_url = ""
        self.kline_periods = {
            "1m": "1",
            "5m": "5",
            "15m": "15",
            "30m": "30",
            "1h": "60",
            "4h": "240",
            "1d": "D",
        }
        self.legal_currency = ["AUD", "USDT", "USD", "BTC", "ETH"]
        self.api_key = ""
        self.api_secret = ""

    def _load_from_config(self, asset_type):
        """Load from YAML config."""
        config = _get_coinspot_config()
        if config is None:
            return False
        asset_cfg = config.asset_types.get(asset_type)
        if asset_cfg is None:
            return False

        if asset_cfg.exchange_name:
            self.exchange_name = asset_cfg.exchange_name
        if config.base_urls and config.base_urls.rest:
            # rest_urls can be a dict (with keys like 'default', 'spot') or a string
            if isinstance(config.base_urls.rest, dict):
                # Try to get asset_type specific URL, fallback to 'default'
                self.rest_url = config.base_urls.rest.get(asset_type, config.base_urls.rest.get('default', self.rest_url))
            else:
                self.rest_url = config.base_urls.rest
        if config.base_urls and config.base_urls.wss:
            # wss_urls can be a dict or a string
            if isinstance(config.base_urls.wss, dict):
                self.wss_url = config.base_urls.wss.get(asset_type, config.base_urls.wss.get('default', self.wss_url))
            else:
                self.wss_url = config.base_urls.wss
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


class CoinSpotExchangeDataSpot(CoinSpotExchangeData):
    """CoinSpot Spot exchange configuration."""

    def __init__(self):
        super().__init__()
        self.asset_type = "spot"
        self.rest_paths = {}
        self.wss_paths = {}
        self._load_from_config("spot")
