"""Swyftx Exchange Data Configuration – Feed pattern."""

import os
import re
from typing import Any

import yaml

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("swyftx_exchange_data")

_swyftx_yaml_cache = None


def _load_swyftx_yaml() -> Any | None:
    global _swyftx_yaml_cache
    if _swyftx_yaml_cache is not None:
        return _swyftx_yaml_cache
    try:
        cfg_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "swyftx.yaml",
        )
        if os.path.exists(cfg_path):
            with open(cfg_path, encoding="utf-8") as f:
                _swyftx_yaml_cache = yaml.safe_load(f) or {}
    except (OSError, ValueError, KeyError, ImportError) as e:
        logger.warning(f"Failed to load swyftx.yaml: {e}")
        _swyftx_yaml_cache = {}
    return _swyftx_yaml_cache


class SwyftxExchangeData(ExchangeData):
    """Base class for Swyftx exchange."""

    def __init__(self) -> None:
        super().__init__()
        self.exchange_name = "SWYFTX"
        self.rest_url = "https://api.swyftx.com.au"
        self.wss_url = ""
        self.kline_periods = {
            "1m": "60",
            "5m": "300",
            "15m": "900",
            "30m": "1800",
            "1h": "3600",
            "4h": "14400",
            "1d": "86400",
            "1w": "604800",
        }
        self.legal_currency = ["AUD", "USD", "BTC", "ETH", "USDT"]

    @staticmethod
    def get_symbol(symbol):
        """Normalize symbol to uppercase hyphen format: BTC-AUD."""
        s = symbol.strip()
        s = re.sub(r"[/_]", "-", s)
        return s.upper()

    @staticmethod
    def get_reverse_symbol(symbol):
        """Reverse normalize: keep uppercase hyphen."""
        s = symbol.strip()
        s = re.sub(r"[/_]", "-", s)
        return s.upper()

    def get_period(self, period: str) -> str:
        return self.kline_periods.get(period, period)

    def get_reverse_period(self, period: str) -> str:
        for k, v in self.kline_periods.items():
            if v == period:
                return k
        return period


class SwyftxExchangeDataSpot(SwyftxExchangeData):
    """Swyftx Spot exchange configuration."""

    def __init__(self) -> None:
        super().__init__()
        self.exchange_name = "SWYFTX___SPOT"
        self.asset_type = "SPOT"
        self.rest_paths = {
            "get_server_time": "GET /api/v1/time",
            "get_tick": "GET /api/v1/markets/{symbol}/ticker",
            "get_ticker": "GET /api/v1/markets/{symbol}/ticker",
            "get_all_tickers": "GET /api/v1/markets/ticker",
            "get_depth": "GET /api/v1/markets/{symbol}/orderbook",
            "get_kline": "GET /api/v1/markets/{symbol}/candles",
            "get_exchange_info": "GET /api/v1/markets",
            "get_account": "GET /api/v1/user/account",
            "get_balance": "GET /api/v1/user/balance",
            "make_order": "POST /api/v1/orders",
            "cancel_order": "DELETE /api/v1/orders/{order_id}",
            "query_order": "GET /api/v1/orders/{order_id}",
            "get_open_orders": "GET /api/v1/orders",
        }
        self.wss_paths = {}
        self._load_yaml()

    def _load_yaml(self) -> None:
        cfg = _load_swyftx_yaml() or {}
        spot = cfg.get("SWYFTX___SPOT", {})
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
        return str(path)
