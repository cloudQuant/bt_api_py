"""BitMart Exchange Data Configuration.

BitMart API V2/V3 with HMAC SHA256 authentication.
Signature: timestamp + "#" + memo + "#" + body
Symbol format: BTC_USDT (underscore separated).
Response format: {"code": 1000, "message": "OK", "data": {...}}
"""

import os
from typing import Any

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("bitmart_exchange_data")

_bitmart_config = None
_bitmart_config_loaded = False


def _get_bitmart_config() -> Any | None:
    """Load BitMart YAML configuration."""
    global _bitmart_config, _bitmart_config_loaded
    if _bitmart_config_loaded:
        return _bitmart_config
    try:
        from bt_api_py.config_loader import load_exchange_config

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "bitmart.yaml",
        )
        if os.path.exists(config_path):
            _bitmart_config = load_exchange_config(config_path)
        _bitmart_config_loaded = True
    except Exception as e:
        logger.warn(f"Failed to load bitmart.yaml config: {e}")
    return _bitmart_config


class BitmartExchangeData(ExchangeData):
    """Base class for BitMart exchange."""

    def __init__(self) -> None:
        super().__init__()
        self.exchange_name = "BITMART___SPOT"
        self.rest_url = "https://api-cloud.bitmart.com"
        self.wss_url = "wss://ws-manager-compress.bitmart.com/api?protocol=1.1"
        self.rest_paths = {}
        self.wss_paths = {}
        self.kline_periods = {
            "1m": "1",
            "3m": "3",
            "5m": "5",
            "15m": "15",
            "30m": "30",
            "45m": "45",
            "1h": "60",
            "2h": "120",
            "3h": "180",
            "4h": "240",
            "1d": "1440",
            "1w": "10080",
            "1M": "43200",
        }
        self.legal_currency = ["USDT", "USD", "BTC", "ETH", "USDC"]

    def _load_from_config(self, asset_type) -> bool:
        """Load from YAML config."""
        config = _get_bitmart_config()
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
        """Convert symbol to BitMart format (underscore separated, uppercase).
        e.g. 'BTC/USDT' -> 'BTC_USDT', 'BTC-USDT' -> 'BTC_USDT'.
        """
        s = symbol.upper().replace("/", "_").replace("-", "_")
        return s

    def get_period(self, period: str) -> str:
        """Map standard period to BitMart kline step (minutes string)."""
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


class BitmartExchangeDataSpot(BitmartExchangeData):
    """BitMart Spot exchange configuration."""

    def __init__(self) -> None:
        super().__init__()
        self.asset_type = "SPOT"
        if not self._load_from_config("spot"):
            # Fallback defaults
            self.exchange_name = "BITMART___SPOT"
            self.rest_url = "https://api-cloud.bitmart.com"
            self.wss_url = "wss://ws-manager-compress.bitmart.com/api?protocol=1.1"
            self.rest_paths = {
                "get_server_time": "GET /spot/v1/time",
                "get_exchange_info": "GET /spot/v1/currencies",
                "get_tick": "GET /spot/quotation/v3/ticker",
                "get_tick_all": "GET /spot/quotation/v3/tickers",
                "get_depth": "GET /spot/quotation/v3/books",
                "get_trades": "GET /spot/quotation/v3/trades",
                "get_kline": "GET /spot/quotation/v3/klines",
                "make_order": "POST /spot/v2/submit_order",
                "cancel_order": "POST /spot/v3/cancel_order",
                "cancel_all_orders": "POST /spot/v4/cancel_all",
                "query_order": "POST /spot/v4/query/order",
                "get_open_orders": "POST /spot/v4/query/open-orders",
                "get_deals": "POST /spot/v4/query/history-orders",
                "get_balance": "GET /spot/v1/wallet",
                "get_account": "GET /spot/v1/wallet",
            }


# Backward compatibility alias
BitmartSpotExchangeData = BitmartExchangeDataSpot
