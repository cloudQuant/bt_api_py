"""
VALR Exchange Data Configuration – Feed pattern.
"""

import os
import re

import yaml

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("valr_exchange_data")

_valr_yaml_cache = None


def _load_valr_yaml():
    global _valr_yaml_cache
    if _valr_yaml_cache is not None:
        return _valr_yaml_cache
    try:
        cfg_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "valr.yaml",
        )
        if os.path.exists(cfg_path):
            with open(cfg_path, encoding="utf-8") as f:
                _valr_yaml_cache = yaml.safe_load(f) or {}
    except Exception as e:
        logger.warn(f"Failed to load valr.yaml: {e}")
        _valr_yaml_cache = {}
    return _valr_yaml_cache


class ValrExchangeData(ExchangeData):
    """Base class for VALR exchange."""

    def __init__(self):
        super().__init__()
        self.exchange_name = "VALR"
        self.rest_url = "https://api.valr.com"
        self.wss_url = "wss://api.valr.com/ws"
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
            "1M": "1M",
        }
        self.legal_currency = ["USDC", "ZAR", "BTC", "ETH"]

    @staticmethod
    def get_symbol(symbol):
        """Normalize symbol: BTCZAR (uppercase, no separators)."""
        s = symbol.strip()
        s = re.sub(r"[-/_]", "", s)
        return s.upper()

    @staticmethod
    def get_reverse_symbol(symbol):
        """Reverse normalize: keep uppercase no-separator."""
        s = symbol.strip()
        s = re.sub(r"[-/_]", "", s)
        return s.upper()

    def get_period(self, period):
        return self.kline_periods.get(period, period)

    def get_reverse_period(self, period):
        for k, v in self.kline_periods.items():
            if v == period:
                return k
        return period


class ValrExchangeDataSpot(ValrExchangeData):
    """VALR Spot exchange configuration."""

    def __init__(self):
        super().__init__()
        self.exchange_name = "VALR___SPOT"
        self.asset_type = "SPOT"
        self.rest_paths = {
            "get_server_time": "GET /v1/public/time",
            "get_tick": "GET /v1/public/{symbol}/ticker",
            "get_ticker": "GET /v1/public/{symbol}/ticker",
            "get_all_tickers": "GET /v1/public/marketsummary",
            "get_depth": "GET /v1/public/{symbol}/orderbook",
            "get_kline": "GET /v1/public/{symbol}/marketsummary",
            "get_trades": "GET /v1/public/{symbol}/trades",
            "get_exchange_info": "GET /v1/public/pairs",
            "get_currencies": "GET /v1/public/currencies",
            "get_account": "GET /v1/account/balances",
            "get_balance": "GET /v1/account/balances",
            "make_order": "POST /v1/orders/market",
            "make_limit_order": "POST /v1/orders/limit",
            "cancel_order": "DELETE /v1/order/{order_id}",
            "query_order": "GET /v1/order/{order_id}",
            "get_open_orders": "GET /v1/orders/open",
        }
        self.wss_paths = {}
        self._load_yaml()

    def _load_yaml(self):
        cfg = _load_valr_yaml()
        spot = cfg.get("VALR___SPOT", {})
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

    def get_rest_path(self, key, **kwargs):
        path = self.rest_paths.get(key, "")
        if not path:
            raise ValueError(f"[{self.exchange_name}] REST path not found: {key}")
        if kwargs:
            path = path.format(**kwargs)
        return path
