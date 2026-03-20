"""Upbit Exchange Data Configuration.

Symbol format: KRW-BTC (quote-base with dash).
Kline uses separate endpoints for minutes/days/weeks/months.
Auth: JWT (HS256) with SHA512 query hash.
"""

import os
from typing import Any

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("upbit_exchange_data")

_upbit_config = None
_upbit_config_loaded = False


def _get_upbit_config() -> Any | None:
    """Load and cache Upbit YAML configuration."""
    global _upbit_config, _upbit_config_loaded
    if _upbit_config_loaded:
        return _upbit_config
    try:
        from bt_api_py.config_loader import load_exchange_config

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "upbit.yaml",
        )
        if os.path.exists(config_path):
            _upbit_config = load_exchange_config(config_path)
        _upbit_config_loaded = True
    except (OSError, ValueError, KeyError, ImportError) as e:
        logger.warning(f"Failed to load upbit.yaml config: {e}")
    return _upbit_config


# ── fallback rest_paths ─────────────────────────────────────────
_FALLBACK_REST_PATHS = {
    "get_exchange_info": "GET /v1/market/all",
    "get_tick": "GET /v1/ticker",
    "get_depth": "GET /v1/orderbook",
    "get_trades": "GET /v1/trades/ticks",
    "get_kline_minutes": "GET /v1/candles/minutes/{unit}",
    "get_kline_days": "GET /v1/candles/days",
    "get_kline_weeks": "GET /v1/candles/weeks",
    "get_kline_months": "GET /v1/candles/months",
    "get_account": "GET /v1/accounts",
    "get_balance": "GET /v1/accounts",
    "make_order": "POST /v1/orders",
    "cancel_order": "DELETE /v1/order",
    "query_order": "GET /v1/order",
    "get_open_orders": "GET /v1/orders",
    "get_api_keys": "GET /v1/api_keys",
    "get_wallet_status": "GET /v1/status/wallet",
}


class UpbitExchangeData(ExchangeData):
    """Base class for Upbit exchange."""

    def __init__(self) -> None:
        super().__init__()
        self.exchange_name = "UPBIT___SPOT"
        self.rest_url = "https://api.upbit.com"
        self.wss_url = "wss://api.upbit.com/websocket/v1"
        self.rest_paths = dict(_FALLBACK_REST_PATHS)
        self.wss_paths = {}
        self.kline_periods = {
            "1m": "1",
            "3m": "3",
            "5m": "5",
            "10m": "10",
            "15m": "15",
            "30m": "30",
            "1h": "60",
            "2h": "120",
            "4h": "240",
            "6h": "360",
            "8h": "480",
            "12h": "720",
            "1d": "D",
            "3d": "3D",
            "1w": "W",
            "1M": "M",
        }
        self.reverse_kline_periods = {v: k for k, v in self.kline_periods.items()}
        self.legal_currency = ["KRW", "USDT", "BTC", "ETH"]

    def _load_from_config(self, asset_type) -> bool:
        """Load from YAML config."""
        config = _get_upbit_config()
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


class UpbitExchangeDataSpot(UpbitExchangeData):
    """Upbit Spot exchange configuration."""

    def __init__(self) -> None:
        super().__init__()
        self.asset_type = "SPOT"
        self._load_from_config("spot")

    def get_symbol(self, symbol: str) -> str:
        """Return symbol as-is (Upbit uses KRW-BTC format with dashes)."""
        return symbol

    def get_period(self, key: str) -> str:
        """Get kline period value."""
        return self.kline_periods.get(key, key)

    def get_rest_path(self, key: str, **kwargs) -> str:
        """Get REST path for *key*. Raises ValueError if missing."""
        if key not in self.rest_paths or self.rest_paths[key] == "":
            raise ValueError(f"[{self.exchange_name}] REST path not found: {key}")
        return self.rest_paths[key]

    def get_wss_path(self, channel, symbol: str | None = None, **kwargs) -> str:
        """Get WSS subscription message for *channel*."""
        tpl = self.wss_paths.get(channel, "")
        if symbol and tpl:
            return tpl.replace("{market}", symbol)
        return tpl
