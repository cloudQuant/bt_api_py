"""Latoken exchange data – Feed pattern."""

import os
from typing import Any

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("latoken_exchange_data")

_latoken_config = None
_latoken_config_loaded = False


def _get_latoken_config() -> Any | None:
    global _latoken_config, _latoken_config_loaded
    if _latoken_config_loaded:
        return _latoken_config
    try:
        from bt_api_py.config_loader import load_exchange_config

        package_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        )
        config_path = os.path.join(package_root, "configs", "latoken.yaml")
        if os.path.exists(config_path):
            _latoken_config = load_exchange_config(config_path)
        _latoken_config_loaded = True
    except Exception as e:
        logger.warn(f"Failed to load latoken.yaml config: {e}")
    return _latoken_config


class LatokenExchangeData(ExchangeData):
    """Base Latoken exchange data (HMAC-SHA512 auth, UUID-based currencies)."""

    def __init__(self) -> None:
        super().__init__()
        self.exchange_name = "LATOKEN___SPOT"
        self.asset_type = "SPOT"
        self.rest_url = "https://api.latoken.com"
        self.wss_url = ""
        self.rest_paths = {}

        self.kline_periods = {
            "1m": "1",
            "5m": "5",
            "15m": "15",
            "30m": "30",
            "1h": "60",
            "4h": "240",
            "1d": "1D",
            "1w": "1W",
        }
        self.reverse_kline_periods = {v: k for k, v in self.kline_periods.items()}
        self.legal_currency = ["USDT", "BTC", "ETH", "LA"]

    def _load_from_config(self, asset_type) -> bool:
        config = _get_latoken_config()
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
            self.rest_paths = dict(asset_cfg.rest_paths)

        kp = asset_cfg.kline_periods or (config.kline_periods if config.kline_periods else None)
        if kp:
            self.kline_periods = dict(kp)
            self.reverse_kline_periods = {v: k for k, v in self.kline_periods.items()}

        lc = asset_cfg.legal_currency or (config.legal_currency if config.legal_currency else None)
        if lc:
            self.legal_currency = list(lc)

        return True

    @staticmethod
    def get_symbol(symbol):
        """Convert to lowercase underscore: BTC/USDT -> btc_usdt."""
        return symbol.lower().replace("/", "_").replace("-", "_")

    @staticmethod
    def get_reverse_symbol(symbol):
        """Convert back: btc_usdt -> BTC-USDT."""
        return symbol.upper().replace("_", "-")

    def get_rest_path(self, key: str, **kwargs) -> str:
        if key not in self.rest_paths or self.rest_paths[key] == "":
            raise ValueError(f"[{self.exchange_name}] REST path not found: {key}")
        path = self.rest_paths[key]
        if kwargs:
            for k, v in kwargs.items():
                path = path.replace(f"{{{k}}}", str(v).lower())
        return path

    def get_period(self, period: str) -> str:
        return self.kline_periods.get(period, period)

    def get_reverse_period(self, period: str) -> str:
        return self.reverse_kline_periods.get(period, period)


class LatokenExchangeDataSpot(LatokenExchangeData):
    """Latoken Spot exchange data."""

    def __init__(self) -> None:
        super().__init__()
        self._load_from_config("spot")

        if not self.rest_paths:
            self.rest_paths = {
                "get_server_time": "GET /v2/time",
                "get_tick": "GET /v2/ticker/{base}/{quote}",
                "get_all_tickers": "GET /v2/ticker",
                "get_depth": "GET /v2/book/{currency}/{quote}",
                "get_deals": "GET /v2/trade/history/{currency}/{quote}",
                "get_exchange_info": "GET /v2/pair",
                "get_currencies": "GET /v2/currency",
                "get_kline": "GET /v2/chart/week/{currency}/{quote}",
                "make_order": "POST /v2/auth/order/place",
                "cancel_order": "POST /v2/auth/order/cancel",
                "get_open_orders": "GET /v2/auth/order/pair/{currency}/{quote}/active",
                "get_balance": "GET /v2/auth/account",
                "get_account": "GET /v2/auth/account",
            }
