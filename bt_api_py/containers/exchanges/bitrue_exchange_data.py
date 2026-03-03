"""
Bitrue Exchange Data Configuration

Bitrue Spot API with Binance-compatible HMAC SHA256 authentication.
Signature: HMAC-SHA256(query_string_with_timestamp, secret_key)
Header: X-MBX-APIKEY
Symbol format: BTCUSDT (concatenated uppercase, no separator).
"""

import os
from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.functions.log_message import SpdLogManager

logger = SpdLogManager(
    file_name="bitrue_exchange_data.log", logger_name="bitrue_data", print_info=False
).create_logger()

_bitrue_config = None
_bitrue_config_loaded = False


def _get_bitrue_config():
    """Load Bitrue YAML configuration."""
    global _bitrue_config, _bitrue_config_loaded
    if _bitrue_config_loaded:
        return _bitrue_config
    try:
        from bt_api_py.config_loader import load_exchange_config
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "bitrue.yaml",
        )
        if os.path.exists(config_path):
            _bitrue_config = load_exchange_config(config_path)
        _bitrue_config_loaded = True
    except Exception as e:
        logger.warn(f"Failed to load bitrue.yaml config: {e}")
    return _bitrue_config


class BitrueExchangeData(ExchangeData):
    """Base class for Bitrue exchange."""

    def __init__(self):
        super().__init__()
        self.exchange_name = "BITRUE___SPOT"
        self.rest_url = "https://www.bitrue.com"
        self.wss_url = "wss://ws.bitrue.com/kline-api/ws"
        self.rest_paths = {}
        self.wss_paths = {}
        self.kline_periods = {
            "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
            "1h": "1h", "2h": "2h", "4h": "4h", "12h": "12h",
            "1d": "1d", "1w": "1w",
        }
        self.legal_currency = ["USDT", "BTC", "ETH", "XRP"]

    def _load_from_config(self, asset_type):
        """Load from YAML config."""
        config = _get_bitrue_config()
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
            self.rest_url = rest.get(asset_type, rest.get("default", self.rest_url)) if isinstance(rest, dict) else rest
        if hasattr(asset_cfg, "wss_url") and asset_cfg.wss_url:
            self.wss_url = asset_cfg.wss_url
        elif config.base_urls and config.base_urls.wss:
            wss = config.base_urls.wss
            self.wss_url = wss.get(asset_type, wss.get("default", self.wss_url)) if isinstance(wss, dict) else wss

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
        """Convert symbol to Bitrue format (concatenated uppercase).
        e.g. 'BTC/USDT' -> 'BTCUSDT', 'BTC-USDT' -> 'BTCUSDT'
        """
        return symbol.upper().replace("/", "").replace("-", "").replace("_", "")

    def get_period(self, period):
        """Map standard period to Bitrue kline scale."""
        return self.kline_periods.get(period, period)

    def get_rest_path(self, request_type):
        """Get REST path for request_type. Raises ValueError if not found."""
        path = self.rest_paths.get(request_type)
        if path is None:
            raise ValueError(
                f"Unknown rest path: {request_type}. "
                f"Available: {list(self.rest_paths.keys())}"
            )
        return path

    def get_wss_path(self, channel_type, symbol=None):
        """Get WebSocket subscription path."""
        path = self.wss_paths.get(channel_type, "")
        if symbol and "{symbol}" in str(path):
            path = str(path).replace("{symbol}", self.get_symbol(symbol).lower())
        return path


class BitrueExchangeDataSpot(BitrueExchangeData):
    """Bitrue Spot exchange configuration."""

    def __init__(self):
        super().__init__()
        self.asset_type = "SPOT"
        if not self._load_from_config("spot"):
            self.exchange_name = "BITRUE___SPOT"
            self.rest_paths = {
                "ping": "GET /api/v1/ping",
                "get_server_time": "GET /api/v1/time",
                "get_exchange_info": "GET /api/v1/exchangeInfo",
                "get_tick": "GET /api/v1/ticker/24hr",
                "get_depth": "GET /api/v1/depth",
                "get_kline": "GET /api/v1/market/kline",
                "get_trades": "GET /api/v1/trades",
                "get_account": "GET /api/v1/account",
                "get_balance": "GET /api/v1/account",
                "make_order": "POST /api/v1/order",
                "cancel_order": "DELETE /api/v1/order",
                "cancel_all_orders": "DELETE /api/v1/openOrders",
                "query_order": "GET /api/v1/order",
                "get_open_orders": "GET /api/v1/openOrders",
                "get_all_orders": "GET /api/v1/allOrders",
                "get_deals": "GET /api/v1/myTrades",
            }


# Backward compatibility alias
BitrueSpotExchangeData = BitrueExchangeDataSpot
