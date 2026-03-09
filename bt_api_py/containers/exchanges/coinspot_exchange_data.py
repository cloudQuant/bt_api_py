"""CoinSpot Exchange Data Configuration.

API: Public V2 (GET) / Private V2 (POST)
Auth: HMAC-SHA512 over JSON body with nonce; headers: key + sign
Response: {"status": "ok", ...} / {"status": "error", "message": "..."}
Symbol: coin shortname "BTC" (no trading pair)
No WebSocket support.
"""

import os
from typing import Any

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("coinspot_exchange_data")

_coinspot_config = None
_coinspot_config_loaded = False


def _get_coinspot_config() -> Any | None:
    """Load and cache CoinSpot YAML configuration."""
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


# ── fallback rest_paths ─────────────────────────────────────────
_FALLBACK_REST_PATHS = {
    "get_exchange_info": "GET /pubapi/v2/latest",
    "get_tick": "GET /pubapi/v2/latest",
    "get_buy_price": "GET /pubapi/v2/buyprice",
    "get_sell_price": "GET /pubapi/v2/sellprice",
    "get_depth": "GET /pubapi/v2/orders/open",
    "get_deals": "GET /pubapi/v2/orders/completed",
    "get_balance": "POST /api/v2/my/balances",
    "get_account": "POST /api/v2/my/balances",
    "make_order_buy": "POST /api/v2/my/buy",
    "make_order_sell": "POST /api/v2/my/sell",
    "cancel_order_buy": "POST /api/v2/my/buy/cancel",
    "cancel_order_sell": "POST /api/v2/my/sell/cancel",
}


class CoinSpotExchangeData(ExchangeData):
    """Base class for CoinSpot exchange."""

    def __init__(self) -> None:
        super().__init__()
        self.exchange_name = "COINSPOT___SPOT"
        self.rest_url = "https://www.coinspot.com.au"
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
        self.legal_currency = ["AUD", "USDT", "USD", "BTC", "ETH"]

    def _load_from_config(self, asset_type) -> bool:
        """Load from YAML config."""
        config = _get_coinspot_config()
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


class CoinSpotExchangeDataSpot(CoinSpotExchangeData):
    """CoinSpot Spot exchange configuration."""

    def __init__(self) -> None:
        super().__init__()
        self.asset_type = "SPOT"
        self._load_from_config("spot")

    @staticmethod
    def get_symbol(symbol):
        """Return coin shortname as-is (CoinSpot uses 'BTC', not a pair)."""
        return symbol

    def get_period(self, key: str) -> str:
        """Get kline period value."""
        return self.kline_periods.get(key, key)

    def get_rest_path(self, key: str, **kwargs) -> str:
        """Get REST path for *key*. Raises ValueError if missing."""
        if key not in self.rest_paths or self.rest_paths[key] == "":
            raise ValueError(f"[{self.exchange_name}] REST path not found: {key}")
        return self.rest_paths[key]
