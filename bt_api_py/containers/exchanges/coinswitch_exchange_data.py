"""
CoinSwitch Exchange Data Configuration.

API: REST V2  (https://docs.coinswitch.co/)
Auth: API key in header x-api-key
Response: {"data": ...} or {"error": ...}
Symbol: pair string "BTCINR"
No WebSocket support.
"""

import os

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("coinswitch_exchange_data")

_coinswitch_config = None
_coinswitch_config_loaded = False


def _get_coinswitch_config():
    """Load and cache CoinSwitch YAML configuration."""
    global _coinswitch_config, _coinswitch_config_loaded
    if _coinswitch_config_loaded:
        return _coinswitch_config
    try:
        from bt_api_py.config_loader import load_exchange_config

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "coinswitch.yaml",
        )
        if os.path.exists(config_path):
            _coinswitch_config = load_exchange_config(config_path)
        _coinswitch_config_loaded = True
    except Exception as e:
        logger.warn(f"Failed to load coinswitch.yaml config: {e}")
    return _coinswitch_config


# ── fallback rest_paths ─────────────────────────────────────────
_FALLBACK_REST_PATHS = {
    "get_tick": "GET /v2/tickers",
    "get_exchange_info": "GET /v2/markets",
    "get_trade_history": "GET /v2/trades",
    "get_balance": "GET /v2/account/balance",
    "get_account": "GET /v2/account/balance",
    "make_order": "POST /v2/orders",
    "cancel_order": "DELETE /v2/orders",
    "get_open_orders": "GET /v2/orders",
}


class CoinSwitchExchangeData(ExchangeData):
    """Base class for CoinSwitch exchange."""

    def __init__(self):
        super().__init__()
        self.exchange_name = "COINSWITCH___SPOT"
        self.rest_url = "https://api.coinswitch.co"
        self.wss_url = ""
        self.rest_paths = dict(_FALLBACK_REST_PATHS)
        self.wss_paths = {}
        self.kline_periods = {
            "1m": "1",
            "5m": "5",
            "15m": "15",
            "30m": "30",
            "1h": "60",
            "4h": "240",
            "1d": "D",
        }
        self.reverse_kline_periods = {v: k for k, v in self.kline_periods.items()}
        self.legal_currency = ["INR", "USD", "EUR", "USDT", "BTC", "ETH"]

    def _load_from_config(self, asset_type):
        """Load from YAML config."""
        config = _get_coinswitch_config()
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


class CoinSwitchExchangeDataSpot(CoinSwitchExchangeData):
    """CoinSwitch Spot exchange configuration."""

    def __init__(self):
        super().__init__()
        self.asset_type = "SPOT"
        self._load_from_config("spot")

    @staticmethod
    def get_symbol(symbol):
        """Return symbol as-is (CoinSwitch uses 'BTCINR' style)."""
        return symbol

    def get_period(self, key):
        """Get kline period value."""
        return self.kline_periods.get(key, key)

    def get_rest_path(self, key):
        """Get REST path for *key*. Raises ValueError if missing."""
        if key not in self.rest_paths or self.rest_paths[key] == "":
            raise ValueError(f"[{self.exchange_name}] REST path not found: {key}")
        return self.rest_paths[key]
