"""WazirX Exchange Data Configuration – Feed pattern."""

import os
from typing import Any

import yaml

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("wazirx_exchange_data")

_wazirx_yaml_cache = None


def _load_wazirx_yaml() -> Any | None:
    global _wazirx_yaml_cache
    if _wazirx_yaml_cache is not None:
        return _wazirx_yaml_cache
    try:
        cfg_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "wazirx.yaml",
        )
        if os.path.exists(cfg_path):
            with open(cfg_path, encoding="utf-8") as f:
                _wazirx_yaml_cache = yaml.safe_load(f) or {}
    except (OSError, ValueError, KeyError, ImportError) as e:
        logger.warning(f"Failed to load wazirx.yaml: {e}")
        _wazirx_yaml_cache = {}
    return _wazirx_yaml_cache


class WazirxExchangeData(ExchangeData):
    """Base class for WazirX exchange."""

    def __init__(self) -> None:
        super().__init__()
        self.exchange_name = "WAZIRX"
        self.rest_url = "https://api.wazirx.com"
        self.wss_url = "wss://stream.wazirx.com/stream"
        self.kline_periods = {
            "1m": "1m",
            "3m": "3m",
            "5m": "5m",
            "15m": "15m",
            "30m": "30m",
            "1h": "1h",
            "2h": "2h",
            "4h": "4h",
            "6h": "6h",
            "12h": "12h",
            "1d": "1d",
            "1w": "1w",
        }
        self.legal_currency = ["INR", "USDT", "WRX", "BTC", "ETH"]

    @staticmethod
    def get_symbol(symbol):
        """Normalize symbol: btcinr (lowercase, no separators)."""
        s = symbol.strip().replace("/", "").replace("-", "").replace("_", "")
        return s.lower()

    @staticmethod
    def get_reverse_symbol(symbol):
        return symbol.strip().replace("/", "").replace("-", "").replace("_", "").lower()

    def get_period(self, period: str) -> str:
        return self.kline_periods.get(period, period)

    def get_reverse_period(self, period: str) -> str:
        for k, v in self.kline_periods.items():
            if v == period:
                return k
        return period


class WazirxExchangeDataSpot(WazirxExchangeData):
    """WazirX Spot exchange configuration."""

    def __init__(self) -> None:
        super().__init__()
        self.exchange_name = "WAZIRX___SPOT"
        self.asset_type = "SPOT"
        self.rest_paths = {
            "ping": "GET /sapi/v1/ping",
            "get_server_time": "GET /sapi/v1/time",
            "get_tick": "GET /sapi/v1/ticker/24hr",
            "get_ticker": "GET /sapi/v1/ticker/24hr",
            "get_all_tickers": "GET /sapi/v1/tickers/24hr",
            "get_depth": "GET /sapi/v1/depth",
            "get_kline": "GET /sapi/v1/klines",
            "get_trades": "GET /sapi/v1/trades",
            "get_exchange_info": "GET /sapi/v1/exchangeInfo",
            "get_account": "GET /sapi/v1/account",
            "get_balance": "GET /sapi/v1/funds",
            "make_order": "POST /sapi/v1/order",
            "cancel_order": "DELETE /sapi/v1/order",
            "query_order": "GET /sapi/v1/order",
            "get_open_orders": "GET /sapi/v1/openOrders",
        }
        self.wss_paths = {}
        self._load_yaml()

    def _load_yaml(self) -> None:
        cfg = _load_wazirx_yaml() or {}
        spot = cfg.get("WAZIRX___SPOT", {})
        if not spot:
            return
        self.exchange_name = spot.get("exchange_name", self.exchange_name)
        self.asset_type = spot.get("asset_type", self.asset_type)
        self.rest_url = spot.get("rest_url", self.rest_url)
        self.wss_url = spot.get("wss_url", self.wss_url)
        rp = spot.get("rest_paths")
        if rp:
            self.rest_paths.update(rp)
        kp = spot.get("kline_periods")
        if kp:
            self.kline_periods = dict(kp)
        lc = spot.get("legal_currency")
        if lc:
            self.legal_currency = list(lc)

    def get_rest_path(self, key: str, **kwargs) -> str:
        path = self.rest_paths.get(key, "")
        if not path:
            raise ValueError(f"[{self.exchange_name}] REST path not found: {key}")
        if kwargs:
            path = path.format(**kwargs)
        return path
