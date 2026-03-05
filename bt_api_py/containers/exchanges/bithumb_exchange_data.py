"""
Bithumb Exchange Data Configuration
"""

import os
from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("bithumb_exchange_data")

_bithumb_config = None
_bithumb_config_loaded = False


def _get_bithumb_config():
    """Load Bithumb YAML configuration."""
    global _bithumb_config, _bithumb_config_loaded
    if _bithumb_config_loaded:
        return _bithumb_config
    try:
        from bt_api_py.config_loader import load_exchange_config
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "bithumb.yaml",
        )
        if os.path.exists(config_path):
            _bithumb_config = load_exchange_config(config_path)
        _bithumb_config_loaded = True
    except Exception as e:
        logger.warn(f"Failed to load bithumb.yaml config: {e}")
    return _bithumb_config


class BithumbExchangeData(ExchangeData):
    """Base class for Bithumb exchange."""

    def __init__(self):
        super().__init__()
        self.exchange_name = "bithumb"
        self.rest_url = "https://global-openapi.bithumb.pro/openapi/v1"
        self.wss_url = "wss://global-api.bithumb.pro/message/realtime"
        self.kline_periods = {
            "1m": "m1",
            "3m": "m3",
            "5m": "m5",
            "15m": "m15",
            "30m": "m30",
            "1h": "h1",
            "2h": "h2",
            "4h": "h4",
            "6h": "h6",
            "8h": "h8",
            "12h": "h12",
            "1d": "d1",
            "3d": "d3",
            "1w": "w1",
            "1M": "M1",
        }
        self.legal_currency = ["USDT", "USD", "BTC", "ETH", "KRW"]

    def _load_from_config(self, asset_type):
        """Load from YAML config."""
        config = _get_bithumb_config()
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


class BithumbExchangeDataSpot(BithumbExchangeData):
    """Bithumb Spot exchange configuration."""

    def __init__(self):
        super().__init__()
        self.asset_type = "spot"
        self.rest_paths = {}
        self.wss_paths = {}
        self._load_from_config("spot")

    def get_period(self, period: str) -> str:
        """Get kline period mapping for API.

        Args:
            period: Period key (e.g., '1m', '1h', '1d')

        Returns:
            API period string (e.g., 'm1', 'h1', 'd1')
        """
        if period not in self.kline_periods:
            logger.warn(f"Unknown period '{period}', returning as-is")
            return period
        return self.kline_periods[period]
