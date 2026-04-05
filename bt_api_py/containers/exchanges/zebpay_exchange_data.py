"""Zebpay Exchange Data Configuration – Feed pattern."""

from __future__ import annotations

from typing import Any

import yaml

from bt_api_py.config_loader import get_exchange_config_path
from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("zebpay_exchange_data")

_zebpay_yaml_cache = None


def _load_zebpay_yaml() -> Any | None:
    global _zebpay_yaml_cache
    if _zebpay_yaml_cache is not None:
        return _zebpay_yaml_cache
    try:
        cfg_path = get_exchange_config_path("zebpay.yaml")
        if cfg_path.exists():
            with cfg_path.open(encoding="utf-8") as f:
                _zebpay_yaml_cache = yaml.safe_load(f) or {}
    except (OSError, ValueError, KeyError, ImportError) as e:
        logger.warning(f"Failed to load zebpay.yaml: {e}")
        _zebpay_yaml_cache = {}
    return _zebpay_yaml_cache


class ZebpayExchangeData(ExchangeData):
    """Base class for Zebpay exchange."""

    def __init__(self) -> None:
        super().__init__()
        self.exchange_name = "ZEBPAY"
        self.rest_url = "https://sapi.zebpay.com"
        self.wss_url = "wss://stream.zebpay.com"
        self.kline_periods = {
            "1m": "1m",
            "5m": "5m",
            "15m": "15m",
            "30m": "30m",
            "1h": "1h",
            "2h": "2h",
            "4h": "4h",
            "12h": "12h",
            "1d": "1d",
            "1w": "1w",
        }
        self.legal_currency = ["INR", "USDT"]

    @staticmethod
    def get_symbol(symbol):
        """Normalize symbol: BTC-INR (uppercase with dash)."""
        s = symbol.strip().replace("/", "-").replace("_", "-").upper()
        return s

    @staticmethod
    def get_reverse_symbol(symbol):
        return symbol.strip().replace("/", "-").replace("_", "-").upper()

    def get_period(self, period: str) -> str:
        return self.kline_periods.get(period, period)

    def get_reverse_period(self, period: str) -> str:
        for k, v in self.kline_periods.items():
            if v == period:
                return k
        return period


class ZebpayExchangeDataSpot(ZebpayExchangeData):
    """Zebpay Spot exchange configuration."""

    def __init__(self) -> None:
        super().__init__()
        self.exchange_name = "ZEBPAY___SPOT"
        self.asset_type = "SPOT"
        self.rest_paths = {
            "get_tick": "GET /api/v2/market/ticker",
            "get_ticker": "GET /api/v2/market/ticker",
            "get_depth": "GET /api/v2/market/orderbook",
            "get_kline": "GET /api/v2/market/klines",
            "get_trades": "GET /api/v2/market/trades",
            "get_exchange_info": "GET /api/v2/ex/exchangeInfo",
            "get_server_time": "GET /api/v2/system/time",
            "get_account": "GET /api/v2/account",
            "get_balance": "GET /api/v2/account/balance",
            "make_order": "POST /api/v2/order",
            "cancel_order": "DELETE /api/v2/order",
            "query_order": "GET /api/v2/order",
        }
        self.wss_paths = {}
        self._load_yaml()

    def _load_yaml(self) -> None:
        cfg = _load_zebpay_yaml() or {}
        spot = cfg.get("ZEBPAY___SPOT", {})
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
