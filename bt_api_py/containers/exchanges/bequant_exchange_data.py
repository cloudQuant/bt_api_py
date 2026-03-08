"""
BeQuant Exchange Data Configuration

BeQuant uses HitBTC V3 API (white-label). Same endpoints, HTTP Basic Auth.
"""

import os

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("bequant_exchange_data")

_bequant_config = None
_bequant_config_loaded = False


def _get_bequant_config():
    """Load BeQuant YAML configuration."""
    global _bequant_config, _bequant_config_loaded
    if _bequant_config_loaded:
        return _bequant_config
    try:
        from bt_api_py.config_loader import load_exchange_config

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "bequant.yaml",
        )
        if os.path.exists(config_path):
            _bequant_config = load_exchange_config(config_path)
        _bequant_config_loaded = True
    except Exception as e:
        logger.warn(f"Failed to load bequant.yaml config: {e}")
    return _bequant_config


class BeQuantExchangeData(ExchangeData):
    """Base class for BeQuant exchange."""

    def __init__(self):
        super().__init__()
        self.exchange_name = "BEQUANT___SPOT"
        self.rest_url = "https://api.bequant.io/api/3"
        self.wss_url = "wss://api.bequant.io/api/3/ws/public"
        self.rest_paths = {}
        self.wss_paths = {}
        self.kline_periods = {
            "1m": "M1",
            "3m": "M3",
            "5m": "M5",
            "15m": "M15",
            "30m": "M30",
            "1h": "H1",
            "4h": "H4",
            "1d": "D1",
            "1w": "D7",
            "1M": "1M",
        }
        self.legal_currency = ["USDT", "USD", "BTC", "ETH", "EUR"]

    def _load_from_config(self, asset_type):
        """Load from YAML config."""
        config = _get_bequant_config()
        if config is None:
            return False
        asset_cfg = config.asset_types.get(asset_type)
        if asset_cfg is None:
            return False

        if asset_cfg.exchange_name:
            self.exchange_name = asset_cfg.exchange_name
        # rest_url / wss_url may be in asset_cfg directly
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

    def get_symbol(self, symbol):
        """Convert symbol to BeQuant format (no separator, uppercase).
        e.g. 'BTC/USDT' -> 'BTCUSDT', 'BTC-USDT' -> 'BTCUSDT'
        """
        return symbol.upper().replace("/", "").replace("-", "").replace("_", "")

    def get_period(self, period):
        """Map standard period to BeQuant kline period."""
        return self.kline_periods.get(period, period)

    def get_rest_path(self, request_type):
        """Get REST path for request_type. Raises ValueError if not found."""
        path = self.rest_paths.get(request_type)
        if path is None:
            raise ValueError(
                f"Unknown rest path: {request_type}. Available: {list(self.rest_paths.keys())}"
            )
        return path

    def get_wss_path(self, channel_type, symbol=None):
        """Get WebSocket subscription path."""
        path = self.wss_paths.get(channel_type, "")
        if symbol and "{symbol}" in str(path):
            path = str(path).replace("{symbol}", self.get_symbol(symbol))
        return path


class BeQuantExchangeDataSpot(BeQuantExchangeData):
    """BeQuant Spot exchange configuration."""

    def __init__(self):
        super().__init__()
        self.asset_type = "spot"
        if not self._load_from_config("spot"):
            # Fallback defaults
            self.exchange_name = "BEQUANT___SPOT"
            self.rest_url = "https://api.bequant.io/api/3"
            self.wss_url = "wss://api.bequant.io/api/3/ws/public"
            self.rest_paths = {
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
                "get_balance": "GET /spot/balance",
                "get_account": "GET /spot/balance",
            }


# Backward compatibility alias
BeQuantSpotExchangeData = BeQuantExchangeDataSpot
