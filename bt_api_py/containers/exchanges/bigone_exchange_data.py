"""BigONE Exchange Data Configuration.

BigONE API v3 with JWT (HS256) authentication.
Symbol format: BTC-USDT (dash separated).
Responses wrapped in {"data": ...}.
"""

import os
from typing import Any

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("bigone_exchange_data")

_bigone_config = None
_bigone_config_loaded = False


def _get_bigone_config() -> Any | None:
    """Load BigONE YAML configuration."""
    global _bigone_config, _bigone_config_loaded
    if _bigone_config_loaded:
        return _bigone_config
    try:
        from bt_api_py.config_loader import load_exchange_config

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "bigone.yaml",
        )
        if os.path.exists(config_path):
            _bigone_config = load_exchange_config(config_path)
        _bigone_config_loaded = True
    except Exception as e:
        logger.warn(f"Failed to load bigone.yaml config: {e}")
    return _bigone_config


class BigONEExchangeData(ExchangeData):
    """Base class for BigONE exchange."""

    def __init__(self) -> None:
        super().__init__()
        self.exchange_name = "BIGONE___SPOT"
        self.rest_url = "https://big.one/api/v3"
        self.wss_url = "wss://big.one/ws/v2"
        self.rest_paths = {}
        self.wss_paths = {}
        self.kline_periods = {
            "1m": "min1",
            "5m": "min5",
            "15m": "min15",
            "30m": "min30",
            "1h": "hour1",
            "4h": "hour4",
            "12h": "hour12",
            "1d": "day1",
            "1w": "week1",
            "1M": "month1",
        }
        self.legal_currency = ["USDT", "USD", "BTC", "ETH", "EUR"]

    def _load_from_config(self, asset_type) -> bool:
        """Load from YAML config."""
        config = _get_bigone_config()
        if config is None:
            return False
        asset_cfg = config.asset_types.get(asset_type)
        if asset_cfg is None:
            return False

        if asset_cfg.exchange_name:
            self.exchange_name = asset_cfg.exchange_name
        if hasattr(asset_cfg, "rest_url") and asset_cfg.rest_url:
            self.rest_url = asset_cfg.rest_url
        elif config.base_urls and config.base_urls.rest:
            rest = config.base_urls.rest
            self.rest_url = (
                rest.get(asset_type, rest.get("default", self.rest_url))
                if isinstance(rest, dict)
                else rest
            )

        if hasattr(asset_cfg, "wss_url") and asset_cfg.wss_url:
            self.wss_url = asset_cfg.wss_url
        elif config.base_urls and config.base_urls.wss:
            wss = config.base_urls.wss
            self.wss_url = (
                wss.get(asset_type, wss.get("default", self.wss_url))
                if isinstance(wss, dict)
                else wss
            )

        if asset_cfg.rest_paths:
            self.rest_paths.update(asset_cfg.rest_paths)
        if asset_cfg.wss_paths:
            self.wss_paths.update(asset_cfg.wss_paths)

        kp = asset_cfg.kline_periods or (config.kline_periods if config.kline_periods else None)
        if kp:
            self.kline_periods = dict(kp)

        lc = asset_cfg.legal_currency or (config.legal_currency if config.legal_currency else None)
        if lc:
            self.legal_currency = list(lc)

        return True

    def get_symbol(self, symbol: str) -> str:
        """Convert symbol to BigONE format (dash separated, uppercase).
        e.g. 'BTC/USDT' -> 'BTC-USDT', 'BTCUSDT' kept as-is if already has dash.
        """
        s = symbol.upper().replace("/", "-").replace("_", "-")
        return s

    def get_period(self, period: str) -> str:
        """Map standard period to BigONE kline period."""
        return self.kline_periods.get(period, period)

    def get_rest_path(self, request_type: str, **kwargs) -> str:
        """Get REST path for request_type. Raises ValueError if not found."""
        path = self.rest_paths.get(request_type)
        if path is None:
            raise ValueError(
                f"Unknown rest path: {request_type}. Available: {list(self.rest_paths.keys())}"
            )
        return path

    def get_wss_path(self, channel_type, symbol: str | None = None, **kwargs) -> str:
        """Get WebSocket subscription path."""
        path = self.wss_paths.get(channel_type, "")
        if symbol and "{symbol}" in str(path):
            path = str(path).replace("{symbol}", self.get_symbol(symbol))
        return path


class BigONEExchangeDataSpot(BigONEExchangeData):
    """BigONE Spot exchange configuration."""

    def __init__(self) -> None:
        super().__init__()
        self.asset_type = "spot"
        if not self._load_from_config("spot"):
            # Fallback defaults
            self.exchange_name = "BIGONE___SPOT"
            self.rest_url = "https://big.one/api/v3"
            self.wss_url = "wss://big.one/ws/v2"
            self.rest_paths = {
                "get_server_time": "GET /ping",
                "get_exchange_info": "GET /asset_pairs",
                "get_tick": "GET /asset_pairs/{symbol}/ticker",
                "get_tick_all": "GET /asset_pairs/tickers",
                "get_depth": "GET /asset_pairs/{symbol}/depth",
                "get_trades": "GET /asset_pairs/{symbol}/trades",
                "get_kline": "GET /asset_pairs/{symbol}/candles",
                "make_order": "POST /viewer/orders",
                "cancel_order": "POST /viewer/orders/{order_id}/cancel",
                "cancel_all_orders": "POST /viewer/orders/cancel",
                "get_open_orders": "GET /viewer/orders",
                "query_order": "GET /viewer/orders/{order_id}",
                "get_deals": "GET /viewer/trades",
                "get_balance": "GET /viewer/accounts",
                "get_account": "GET /viewer/accounts",
            }


# Backward compatibility alias
BigONESpotExchangeData = BigONEExchangeDataSpot
