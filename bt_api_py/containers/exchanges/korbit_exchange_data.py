"""
Korbit Exchange Data Configuration.

API doc: https://docs.korbit.co.kr
Auth: OAuth2 Bearer token (Authorization: Bearer {token})
Symbol: {base}_{quote} lowercase (e.g. btc_krw)
"""

import os
from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("korbit_exchange_data")

_korbit_config = None
_korbit_config_loaded = False


def _get_korbit_config():
    """Load and cache Korbit YAML configuration."""
    global _korbit_config, _korbit_config_loaded
    if _korbit_config_loaded:
        return _korbit_config
    try:
        from bt_api_py.config_loader import load_exchange_config
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "korbit.yaml",
        )
        if os.path.exists(config_path):
            _korbit_config = load_exchange_config(config_path)
        _korbit_config_loaded = True
    except Exception as e:
        logger.warn(f"Failed to load korbit.yaml config: {e}")
    return _korbit_config


# ── fallback rest_paths ─────────────────────────────────────
_FALLBACK_REST_PATHS = {
    "get_tick": "GET /v1/ticker/detailed",
    "get_depth": "GET /v1/orderbook",
    "get_deals": "GET /v1/transactions",
    "get_exchange_info": "GET /v1/constants",
    "get_kline": "GET /v1/chart/units",
    "make_order": "POST /v1/user/orders/buy",
    "make_order_sell": "POST /v1/user/orders/sell",
    "cancel_order": "POST /v1/user/orders/cancel",
    "get_open_orders": "GET /v1/user/orders/open",
    "get_balance": "GET /v1/user/balances",
    "get_account": "GET /v1/user/balances",
}


class KorbitExchangeData(ExchangeData):
    """Base class for Korbit exchange."""

    def __init__(self):
        super().__init__()
        self.exchange_name = "KORBIT___SPOT"
        self.rest_url = "https://api.korbit.co.kr"
        self.wss_url = "wss://ws-api.korbit.co.kr/v2/public"
        self.rest_paths = dict(_FALLBACK_REST_PATHS)
        self.wss_paths = {}
        self.kline_periods = {
            "1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m",
            "30m": "30m", "1h": "1h", "4h": "4h", "1d": "1d",
        }
        self.reverse_kline_periods = {v: k for k, v in self.kline_periods.items()}
        self.legal_currency = ["KRW", "BTC", "ETH"]

    def _load_from_config(self, asset_type):
        """Load from YAML config."""
        config = _get_korbit_config()
        if config is None:
            return False
        asset_cfg = config.asset_types.get(asset_type)
        if asset_cfg is None:
            return False
        if asset_cfg.exchange_name:
            self.exchange_name = asset_cfg.exchange_name
        if hasattr(asset_cfg, "rest_url") and asset_cfg.rest_url:
            self.rest_url = asset_cfg.rest_url
        if hasattr(asset_cfg, "wss_url") and asset_cfg.wss_url is not None:
            self.wss_url = asset_cfg.wss_url
        if asset_cfg.rest_paths:
            self.rest_paths.update(asset_cfg.rest_paths)
        if asset_cfg.wss_paths:
            self.wss_paths.update(asset_cfg.wss_paths)
        kp = asset_cfg.kline_periods or (config.kline_periods if config.kline_periods else None)
        if kp:
            self.kline_periods = dict(kp)
            self.reverse_kline_periods = {v: k for k, v in self.kline_periods.items()}
        lc = asset_cfg.legal_currency or (config.legal_currency if config.legal_currency else None)
        if lc:
            self.legal_currency = list(lc)
        return True


class KorbitExchangeDataSpot(KorbitExchangeData):
    """Korbit Spot exchange configuration."""

    def __init__(self):
        super().__init__()
        self.asset_type = "SPOT"
        self._load_from_config("spot")

    @staticmethod
    def get_symbol(symbol):
        """Convert 'BTC/KRW' or 'BTC-KRW' → 'btc_krw'."""
        return symbol.lower().replace("/", "_").replace("-", "_")

    @staticmethod
    def get_reverse_symbol(symbol):
        """Convert 'btc_krw' → 'BTC-KRW'."""
        return symbol.upper().replace("_", "-")

    def get_period(self, key):
        """Get kline period value."""
        return self.kline_periods.get(key, key)

    def get_reverse_period(self, key):
        """Reverse kline period lookup."""
        return self.reverse_kline_periods.get(key, key)

    def get_rest_path(self, key):
        """Get REST path for *key*. Raises ValueError if missing."""
        if key not in self.rest_paths or self.rest_paths[key] == "":
            raise ValueError(f"[{self.exchange_name}] REST path not found: {key}")
        return self.rest_paths[key]
