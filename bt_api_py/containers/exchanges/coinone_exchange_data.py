"""Coinone Exchange Data Configuration.

API: Public V2 / Private V2.1  (https://docs.coinone.co.kr)
Auth: HMAC-SHA512 over Base64 payload, secret uppercased
Response: {"result": "success", "errorCode": "0", ...}
Symbol: KRW-BTC  (quote-target, dash separated)
"""

import os
from typing import Any

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("coinone_exchange_data")

_coinone_config = None
_coinone_config_loaded = False


def _get_coinone_config() -> Any | None:
    """Load and cache Coinone YAML configuration."""
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


# ── fallback rest_paths ─────────────────────────────────────────
_FALLBACK_REST_PATHS = {
    "get_exchange_info": "GET /public/v2/markets/KRW",
    "get_tick": "GET /public/v2/ticker_new/KRW",
    "get_depth": "GET /public/v2/orderbook/KRW",
    "get_kline": "GET /public/v2/chart/KRW",
    "get_trades": "GET /public/v2/trades/KRW",
    "get_account": "POST /v2.1/account/balance/all",
    "get_balance": "POST /v2.1/account/balance/all",
    "make_order": "POST /v2.1/order/limit",
    "cancel_order": "POST /v2.1/order/cancel",
    "query_order": "POST /v2.1/order/info",
    "get_open_orders": "POST /v2.1/order/open_orders",
    "get_deals": "POST /v2.1/order/complete_orders",
}


class CoinoneExchangeData(ExchangeData):
    """Base class for Coinone exchange."""

    def __init__(self) -> None:
        super().__init__()
        self.exchange_name = "COINONE___SPOT"
        self.rest_url = "https://api.coinone.co.kr"
        self.wss_url = "wss://stream.coinone.co.kr"
        self.rest_paths = dict(_FALLBACK_REST_PATHS)
        self.wss_paths = {}
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
        self.reverse_kline_periods = {v: k for k, v in self.kline_periods.items()}
        self.legal_currency = ["KRW"]

    def _load_from_config(self, asset_type) -> bool:
        """Load from YAML config."""
        config = _get_coinone_config()
        if config is None:
            return False
        asset_cfg = config.asset_types.get(asset_type)
        if asset_cfg is None:
            return False

        if asset_cfg.exchange_name:
            self.exchange_name = asset_cfg.exchange_name
        if hasattr(asset_cfg, "rest_url") and asset_cfg.rest_url:
            self.rest_url = asset_cfg.rest_url
        if hasattr(asset_cfg, "wss_url") and asset_cfg.wss_url:
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


class CoinoneExchangeDataSpot(CoinoneExchangeData):
    """Coinone Spot exchange configuration."""

    def __init__(self) -> None:
        super().__init__()
        self.asset_type = "SPOT"
        self._load_from_config("spot")

    @staticmethod
    def parse_symbol(symbol):
        """Parse 'KRW-BTC' into (quote_currency, target_currency)."""
        if "-" in symbol:
            parts = symbol.split("-", 1)
            return parts[0], parts[1]
        return "KRW", symbol

    def get_symbol(self, symbol: str) -> str:
        """Return symbol as-is (Coinone uses KRW-BTC format)."""
        return symbol

    def get_period(self, key: str) -> str:
        """Get kline period value (Coinone uses same keys: 1m, 1h, …)."""
        return self.kline_periods.get(key, key)

    def get_rest_path(self, key: str, **kwargs) -> str:
        """Get REST path for *key*. Raises ValueError if missing."""
        if key not in self.rest_paths or self.rest_paths[key] == "":
            raise ValueError(f"[{self.exchange_name}] REST path not found: {key}")
        return self.rest_paths[key]
