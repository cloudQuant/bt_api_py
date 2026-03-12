"""CoinEx Exchange Data Configuration.

API V2: https://docs.coinex.com/api/v2/
Auth: HMAC-SHA256 (X-COINEX-KEY / X-COINEX-SIGN / X-COINEX-TIMESTAMP)
Response: {"code": 0, "data": ..., "message": "OK"}
Symbol format: BTCUSDT (base+quote, no separator)
"""

import os
from typing import Any

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("coinex_exchange_data")

_coinex_config = None
_coinex_config_loaded = False


def _get_coinex_config() -> Any | None:
    """Load and cache CoinEx YAML configuration."""
    global _coinex_config, _coinex_config_loaded
    if _coinex_config_loaded:
        return _coinex_config
    try:
        from bt_api_py.config_loader import load_exchange_config

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "coinex.yaml",
        )
        if os.path.exists(config_path):
            _coinex_config = load_exchange_config(config_path)
        _coinex_config_loaded = True
    except Exception as e:
        logger.warning(f"Failed to load coinex.yaml config: {e}")
    return _coinex_config


# ── fallback rest_paths (V2) ────────────────────────────────────
_FALLBACK_REST_PATHS = {
    "get_exchange_info": "GET /v2/spot/market",
    "get_tick": "GET /v2/spot/ticker",
    "get_depth": "GET /v2/spot/depth",
    "get_trades": "GET /v2/spot/deals",
    "get_kline": "GET /v2/spot/kline",
    "get_account": "GET /v2/account/info",
    "get_balance": "GET /v2/assets/spot/balance",
    "make_order": "POST /v2/spot/order",
    "cancel_order": "DELETE /v2/spot/order",
    "query_order": "GET /v2/spot/order-status",
    "get_open_orders": "GET /v2/spot/pending-order",
    "get_deals": "GET /v2/spot/user-deals",
}


class CoinExExchangeData(ExchangeData):
    """Base class for CoinEx exchange."""

    def __init__(self) -> None:
        super().__init__()
        self.exchange_name = "COINEX___SPOT"
        self.rest_url = "https://api.coinex.com"
        self.wss_url = "wss://socket.coinex.com/v2/"
        self.rest_paths = dict(_FALLBACK_REST_PATHS)
        self.wss_paths = {}
        self.kline_periods = {
            "1m": "1min",
            "3m": "3min",
            "5m": "5min",
            "15m": "15min",
            "30m": "30min",
            "1h": "1hour",
            "2h": "2hour",
            "4h": "4hour",
            "6h": "6hour",
            "12h": "12hour",
            "1d": "1day",
            "3d": "3day",
            "1w": "1week",
        }
        self.reverse_kline_periods = {v: k for k, v in self.kline_periods.items()}
        self.legal_currency = ["USDT", "USD", "BTC", "ETH", "USDC"]

    def _load_from_config(self, asset_type) -> bool:
        """Load from YAML config."""
        config = _get_coinex_config()
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


class CoinExExchangeDataSpot(CoinExExchangeData):
    """CoinEx Spot exchange configuration."""

    def __init__(self) -> None:
        super().__init__()
        self.asset_type = "SPOT"
        self._load_from_config("spot")

    def get_symbol(self, symbol: str) -> str:
        """Return symbol as-is (CoinEx uses BTCUSDT format)."""
        return symbol.replace("-", "")

    def get_period(self, key: str) -> str:
        """Get kline period value."""
        return self.kline_periods.get(key, key)

    def get_rest_path(self, key: str, **kwargs) -> str:
        """Get REST path for *key*. Raises ValueError if missing."""
        if key not in self.rest_paths or self.rest_paths[key] == "":
            raise ValueError(f"[{self.exchange_name}] REST path not found: {key}")
        return self.rest_paths[key]
