"""Zaif Exchange Data Configuration – Feed pattern."""

import os
from typing import Any

import yaml

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("zaif_exchange_data")

_zaif_yaml_cache = None


def _load_zaif_yaml() -> Any | None:
    global _zaif_yaml_cache
    if _zaif_yaml_cache is not None:
        return _zaif_yaml_cache
    try:
        cfg_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "zaif.yaml",
        )
        if os.path.exists(cfg_path):
            with open(cfg_path, encoding="utf-8") as f:
                _zaif_yaml_cache = yaml.safe_load(f) or {}
    except (OSError, ValueError, KeyError, ImportError) as e:
        logger.warning(f"Failed to load zaif.yaml: {e}")
        _zaif_yaml_cache = {}
    return _zaif_yaml_cache


class ZaifExchangeData(ExchangeData):
    """Base class for Zaif exchange."""

    def __init__(self) -> None:
        super().__init__()
        self.exchange_name = "ZAIF"
        self.rest_url = "https://api.zaif.jp"
        self.wss_url = "wss://ws.zaif.jp:8888"
        self.kline_periods = {
            "1m": "1min",
            "5m": "5min",
            "15m": "15min",
            "30m": "30min",
            "1h": "1hour",
            "4h": "4hour",
            "1d": "1day",
            "1w": "1week",
        }
        self.legal_currency = ["JPY", "BTC", "ETH"]

    @staticmethod
    def get_symbol(symbol):
        """Normalize symbol: btc_jpy (lowercase with underscore)."""
        s = symbol.strip().replace("-", "_")
        if "/" in s:
            s = s.replace("/", "_")
        return s.lower()

    @staticmethod
    def get_reverse_symbol(symbol):
        return symbol.strip().replace("/", "_").replace("-", "_").lower()

    def get_period(self, period: str) -> str:
        return self.kline_periods.get(period, period)

    def get_reverse_period(self, period: str) -> str:
        for k, v in self.kline_periods.items():
            if v == period:
                return k
        return period


class ZaifExchangeDataSpot(ZaifExchangeData):
    """Zaif Spot exchange configuration."""

    def __init__(self) -> None:
        super().__init__()
        self.exchange_name = "ZAIF___SPOT"
        self.asset_type = "SPOT"
        self.rest_paths = {
            "get_tick": "GET /api/1/ticker/{pair}",
            "get_ticker": "GET /api/1/ticker/{pair}",
            "get_depth": "GET /api/1/depth/{pair}",
            "get_kline": "GET /api/1/trades/{pair}",
            "get_trades": "GET /api/1/trades/{pair}",
            "get_exchange_info": "GET /api/1/currency_pairs/all",
            "get_server_time": "GET /api/1/last_price/{pair}",
            "get_account": "POST /tapi",
            "get_balance": "POST /tapi",
            "make_order": "POST /tapi",
            "cancel_order": "POST /tapi",
            "query_order": "POST /tapi",
        }
        self.wss_paths = {}
        self._load_yaml()

    def _load_yaml(self) -> None:
        cfg = _load_zaif_yaml() or {}
        spot = cfg.get("ZAIF___SPOT", {})
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
