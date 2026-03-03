"""
Bitso Exchange Data Configuration.

Symbol format: lowercase underscore (btc_mxn).
"""

import os
from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.functions.log_message import SpdLogManager

logger = SpdLogManager(
    file_name="bitso_exchange_data.log", logger_name="bitso_data", print_info=False
).create_logger()

_bitso_config = None
_bitso_config_loaded = False


def _get_bitso_config():
    """Load Bitso YAML configuration (cached)."""
    global _bitso_config, _bitso_config_loaded
    if _bitso_config_loaded:
        return _bitso_config
    try:
        from bt_api_py.config_loader import load_exchange_config
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "bitso.yaml",
        )
        if os.path.exists(config_path):
            _bitso_config = load_exchange_config(config_path)
        _bitso_config_loaded = True
    except Exception as e:
        logger.warn(f"Failed to load bitso.yaml config: {e}")
    return _bitso_config


# ── fallback rest_paths (mirrors YAML) ─────────────────────────
_FALLBACK_REST_PATHS = {
    "ping": "GET /ping",
    "get_server_time": "GET /time",
    "get_exchange_info": "GET /available_books",
    "get_tick": "GET /ticker",
    "get_depth": "GET /order_book",
    "get_kline": "GET /ohlc",
    "get_trades": "GET /trades",
    "get_account": "GET /balance",
    "get_balance": "GET /balance",
    "make_order": "POST /orders",
    "cancel_order": "DELETE /orders",
    "cancel_all_orders": "DELETE /orders/all",
    "query_order": "GET /orders",
    "get_open_orders": "GET /open_orders",
    "get_deals": "GET /user_trades",
}


class BitsoExchangeData(ExchangeData):
    """Base class for Bitso exchange."""

    def __init__(self):
        super().__init__()
        self.exchange_name = "BITSO___SPOT"
        self.rest_url = "https://bitso.com/api/v3"
        self.wss_url = "wss://ws.bitso.com"
        self.rest_paths = dict(_FALLBACK_REST_PATHS)
        self.wss_paths = {}
        self.kline_periods = {
            "1m": "60", "3m": "180", "5m": "300", "15m": "900",
            "30m": "1800", "1h": "3600", "2h": "7200", "4h": "14400",
            "6h": "21600", "12h": "43200", "1d": "86400", "3d": "259200",
        }
        self.legal_currency = ["MXN", "USD", "BTC", "ETH", "USDC"]

    def _load_from_config(self, asset_type):
        """Load from YAML config."""
        config = _get_bitso_config()
        if config is None:
            return False
        asset_cfg = config.asset_types.get(asset_type)
        if asset_cfg is None:
            return False

        if asset_cfg.exchange_name:
            self.exchange_name = asset_cfg.exchange_name
        # rest_url / wss_url inside asset_types.spot
        if hasattr(asset_cfg, "rest_url") and asset_cfg.rest_url:
            self.rest_url = asset_cfg.rest_url
        if hasattr(asset_cfg, "wss_url") and asset_cfg.wss_url:
            self.wss_url = asset_cfg.wss_url
        if asset_cfg.rest_paths:
            self.rest_paths.update(asset_cfg.rest_paths)
        if asset_cfg.wss_paths:
            self.wss_paths.update(asset_cfg.wss_paths)
        if asset_cfg.kline_periods:
            self.kline_periods = dict(asset_cfg.kline_periods)
        lc = asset_cfg.legal_currency or (config.legal_currency if config.legal_currency else None)
        if lc:
            self.legal_currency = list(lc)
        return True


class BitsoExchangeDataSpot(BitsoExchangeData):
    """Bitso Spot exchange configuration."""

    def __init__(self):
        super().__init__()
        self.asset_type = "SPOT"
        self._load_from_config("spot")

    def get_symbol(self, symbol):
        """Convert symbol to Bitso format: lowercase underscore (btc_mxn)."""
        s = symbol.replace("/", "_").replace("-", "_").lower()
        return s

    def get_period(self, key):
        """Get kline period for API (in seconds string)."""
        return self.kline_periods.get(key, key)

    def get_rest_path(self, key):
        """Get REST path for *key*. Raises ValueError if missing."""
        if key not in self.rest_paths or self.rest_paths[key] == "":
            raise ValueError(f"[{self.exchange_name}] REST path not found: {key}")
        return self.rest_paths[key]

    def get_wss_path(self, channel, symbol=None):
        """Get WSS subscription path for *channel*."""
        tpl = self.wss_paths.get(channel, "")
        if symbol and tpl:
            book = self.get_symbol(symbol)
            return tpl.replace("{book}", book)
        return tpl
