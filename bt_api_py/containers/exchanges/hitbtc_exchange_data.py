"""HitBTC Exchange Data Container.

This module provides HitBTC-specific configuration and path management.
Loads configuration from YAML file and provides REST/WSS endpoints.
"""

from typing import Any
import os

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("hitbtc_exchange_data")

# ── 配置加载缓存 ──────────────────────────────────────────────
_hitbtc_config = None
_hitbtc_config_loaded = False


def _get_hitbtc_config() -> Any | None:
    """延迟加载并缓存 HitBTC YAML 配置."""
    global _hitbtc_config, _hitbtc_config_loaded
    if _hitbtc_config_loaded:
        return _hitbtc_config
    try:
        from bt_api_py.config_loader import load_exchange_config

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "hitbtc.yaml",
        )
        if os.path.exists(config_path):
            _hitbtc_config = load_exchange_config(config_path)
        _hitbtc_config_loaded = True
    except Exception as e:
        logger.warn(f"Failed to load hitbtc.yaml config: {e}")
    return _hitbtc_config


class HitBtcExchangeData(ExchangeData):
    """Base class for HitBTC exchange data.

    Provides shared utility methods (get_symbol, get_period, get_rest_path,
    get_wss_path) and default kline_periods.
    Subclasses MUST set exchange-specific: exchange_name, rest_paths, wss_paths.
    """

    def __init__(self) -> None:
        super().__init__()
        self.exchange_name = "HITBTC"
        self.rest_url = "https://api.hitbtc.com/api/3"
        self.wss_url = "wss://api.hitbtc.com/api/3/ws/public"
        self.legal_currency = ["USDT", "BTC", "ETH"]
        self.kline_periods = {
            "1m": "M1",
            "3m": "M3",
            "5m": "M5",
            "15m": "M15",
            "30m": "M30",
            "1h": "H1",
            "2h": "H2",
            "4h": "H4",
            "1d": "D1",
            "1w": "D7",
            "1M": "1M",
        }
        self.rest_paths = {}
        self.wss_paths = {}

    def _load_from_config(self, asset_type) -> bool | None:
        config = _get_hitbtc_config()
        if config is None:
            return False
        try:
            asset_cfg = config.asset_types.get(asset_type)
            if asset_cfg is None:
                return False
            if getattr(asset_cfg, "exchange_name", None):
                self.exchange_name = asset_cfg.exchange_name
            if getattr(asset_cfg, "rest_url", None):
                self.rest_url = asset_cfg.rest_url
            if getattr(asset_cfg, "wss_url", None):
                self.wss_url = asset_cfg.wss_url
            if getattr(asset_cfg, "rest_paths", None):
                self.rest_paths.update(dict(asset_cfg.rest_paths))
            if getattr(asset_cfg, "wss_paths", None):
                self.wss_paths.update(dict(asset_cfg.wss_paths))
            return True
        except Exception as e:
            logger.warn(f"Failed to load hitbtc config for {asset_type}: {e}")
            return False

    def get_rest_path(self, key: str, **kwargs) -> str:
        """Get REST API path for request type (returns 'METHOD /path' format)."""
        path = self.rest_paths.get(key)
        if path is None:
            raise ValueError(f"Unknown REST path key: {key}")
        return path

    def get_wss_path(self, channel: Any, **kwargs) -> str:
        """Get WebSocket channel path."""
        path = self.wss_paths.get(channel)
        if path is None:
            raise ValueError(f"Unknown WSS channel: {channel}")
        return path

    def get_symbol(self, symbol: str) -> str:
        """Normalize symbol format for HitBTC (uppercase, no separator)."""
        return symbol.upper().replace("/", "").replace("-", "")

    def get_period(self, period: str) -> str:
        """Convert period to HitBTC format (e.g. '1h' -> 'H1')."""
        return self.kline_periods.get(period.lower(), period.upper())

    def __str__(self) -> str:
        return f"{self.exchange_name} Exchange Data"

    def __repr__(self) -> str:
        return f"<HitBtcExchangeData exchange='{self.exchange_name}'>"


class HitBtcExchangeDataSpot(HitBtcExchangeData):
    """HitBTC Spot Trading exchange data."""

    def __init__(self) -> None:
        super().__init__()
        self.asset_type = "SPOT"
        if not self._load_from_config("spot"):
            self.exchange_name = "HITBTC___SPOT"
            self.rest_url = "https://api.hitbtc.com/api/3"
            self.wss_url = "wss://api.hitbtc.com/api/3/ws/public"
        # Fallback defaults for REST paths
        _defaults = {
            "get_server_time": "GET /public/time",
            "get_exchange_info": "GET /public/symbol",
            "get_tick": "GET /public/ticker/{symbol}",
            "get_tick_all": "GET /public/ticker",
            "get_depth": "GET /public/orderbook/{symbol}",
            "get_trades": "GET /public/trades/{symbol}",
            "get_kline": "GET /public/candles/{symbol}",
            "make_order": "POST /spot/order",
            "cancel_order": "DELETE /spot/order/{client_order_id}",
            "cancel_all_orders": "DELETE /spot/order",
            "get_open_orders": "GET /spot/order",
            "query_order": "GET /spot/order/{client_order_id}",
            "get_order_history": "GET /spot/history/order",
            "get_trades_history": "GET /spot/history/trade",
            "get_balance": "GET /spot/balance",
            "get_account": "GET /spot/balance",
        }
        for k, v in _defaults.items():
            self.rest_paths.setdefault(k, v)
        _wss_defaults = {
            "ticker": "ticker/1s",
            "orderbook": "orderbook/full",
            "trades": "trades",
            "candles": "candles",
        }
        for k, v in _wss_defaults.items():
            self.wss_paths.setdefault(k, v)
        # Normalize exchange_name
        if self.exchange_name in ("HITBTC", "HITBTC_SPOT", "HitBTC"):
            self.exchange_name = "HITBTC___SPOT"


# Backward-compatible alias
HitBtcSpotExchangeData = HitBtcExchangeDataSpot
