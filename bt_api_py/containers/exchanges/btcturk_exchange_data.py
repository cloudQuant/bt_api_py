"""
BTCTurk Exchange Data Configuration
"""

import os
from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("btcturk_exchange_data")

_btcturk_config = None
_btcturk_config_loaded = False


def _get_btcturk_config():
    """Load BTCTurk YAML configuration."""
    global _btcturk_config, _btcturk_config_loaded
    if _btcturk_config_loaded:
        return _btcturk_config
    try:
        from bt_api_py.config_loader import load_exchange_config
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "btcturk.yaml",
        )
        if os.path.exists(config_path):
            _btcturk_config = load_exchange_config(config_path)
        _btcturk_config_loaded = True
    except Exception as e:
        logger.warn(f"Failed to load btcturk.yaml config: {e}")
    return _btcturk_config


class BTCTurkExchangeData(ExchangeData):
    """Base class for BTCTurk exchange."""

    def __init__(self):
        super().__init__()
        self.exchange_name = "btcturk"
        self.rest_url = "https://api.btcturk.com"
        self.wss_url = "wss://ws-feed-pro.btcturk.com"
        self.kline_periods = {
            "1m": "1",
            "5m": "5",
            "15m": "15",
            "30m": "30",
            "1h": "60",
            "4h": "240",
            "1d": "1440",
            "1w": "10080",
        }
        self.legal_currency = ["USDT", "TRY", "BTC", "ETH", "USDC"]
        # API credentials (for authenticated requests)
        self.api_key = None
        self.api_secret = None

    def _load_from_config(self, asset_type):
        """Load from YAML config."""
        config = _get_btcturk_config()
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


class BTCTurkExchangeDataSpot(BTCTurkExchangeData):
    """BTCTurk Spot exchange configuration."""

    def __init__(self):
        super().__init__()
        self.asset_type = "spot"
        self.rest_paths = {}
        self.wss_paths = {}
        self._load_from_config("spot")
