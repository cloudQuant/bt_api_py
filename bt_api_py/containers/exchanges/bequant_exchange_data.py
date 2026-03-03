"""
BeQuant Exchange Data Configuration
"""

import os
from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.functions.log_message import SpdLogManager

logger = SpdLogManager(
    file_name="bequant_exchange_data.log", logger_name="bequant_data", print_info=False
).create_logger()

_bequant_config = None
_bequant_config_loaded = False


def _get_bequant_config():
    """Load BeQuant YAML configuration."""
    global _bequant_config, _bequant_config_loaded
    if _bequant_config_loaded:
        return _bequant_config
    try:
        from bt_api_py.config_loader import load_exchange_config
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "bequant.yaml",
        )
        if os.path.exists(config_path):
            _bequant_config = load_exchange_config(config_path)
        _bequant_config_loaded = True
    except Exception as e:
        logger.warn(f"Failed to load bequant.yaml config: {e}")
    return _bequant_config


class BeQuantExchangeData(ExchangeData):
    """Base class for BeQuant exchange."""

    def __init__(self):
        super().__init__()
        self.exchange_name = "bequant"
        self.rest_url = "https://api.bequant.io/api/3"
        self.wss_url = "wss://api.bequant.io/api/3/ws/public"
        self.kline_periods = {
            "1m": "M1",
            "3m": "M3",
            "5m": "M5",
            "15m": "M15",
            "30m": "M30",
            "1h": "H1",
            "4h": "H4",
            "1d": "D1",
            "1w": "D7",
            "1M": "1M",
        }
        self.legal_currency = ["USDT", "USD", "BTC", "ETH", "EUR"]

    def _load_from_config(self, asset_type):
        """Load from YAML config."""
        config = _get_bequant_config()
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


class BeQuantExchangeDataSpot(BeQuantExchangeData):
    """BeQuant Spot exchange configuration."""

    def __init__(self):
        super().__init__()
        self.asset_type = "spot"
        self.rest_paths = {}
        self.wss_paths = {}
        self._load_from_config("spot")
