"""
Bitstamp Exchange Data Configuration.

Symbol format: lowercase concatenated (btcusd).
Endpoints use /{pair}/ suffix for market data.
"""

import os
from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.functions.log_message import SpdLogManager

logger = SpdLogManager(
    file_name="bitstamp_exchange_data.log", logger_name="bitstamp_data", print_info=False
).create_logger()

_bitstamp_config = None
_bitstamp_config_loaded = False


def _get_bitstamp_config():
    """Load Bitstamp YAML configuration (cached)."""
    global _bitstamp_config, _bitstamp_config_loaded
    if _bitstamp_config_loaded:
        return _bitstamp_config
    try:
        from bt_api_py.config_loader import load_exchange_config
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "bitstamp.yaml",
        )
        if os.path.exists(config_path):
            _bitstamp_config = load_exchange_config(config_path)
        _bitstamp_config_loaded = True
    except Exception as e:
        logger.warn(f"Failed to load bitstamp.yaml config: {e}")
    return _bitstamp_config


# ── fallback rest_paths (mirrors YAML) ─────────────────────────
_FALLBACK_REST_PATHS = {
    "ping": "GET /ping",
    "get_server_time": "GET /server_time_utc",
    "get_exchange_info": "GET /trading-pairs-info/",
    "get_tick": "GET /ticker",
    "get_depth": "GET /order_book",
    "get_kline": "GET /ohlc",
    "get_trades": "GET /transactions",
    "get_account": "POST /balance/",
    "get_balance": "POST /balance/",
    "make_order": "POST /buy",
    "cancel_order": "POST /cancel_order/",
    "cancel_all_orders": "POST /cancel_all_orders/",
    "query_order": "POST /order_status/",
    "get_open_orders": "POST /open_orders/all/",
    "get_deals": "POST /user_transactions/",
}


class BitstampExchangeData(ExchangeData):
    """Base class for Bitstamp exchange."""

    def __init__(self):
        super().__init__()
        self.exchange_name = "BITSTAMP___SPOT"
        self.rest_url = "https://www.bitstamp.net/api/v2"
        self.wss_url = "wss://ws.bitstamp.net"
        self.rest_paths = dict(_FALLBACK_REST_PATHS)
        self.wss_paths = {}
        self.kline_periods = {
            "1m": "60", "3m": "180", "5m": "300", "15m": "900",
            "30m": "1800", "1h": "3600", "2h": "7200", "4h": "14400",
            "6h": "21600", "12h": "43200", "1d": "86400", "3d": "259200",
        }
        self.legal_currency = ["USD", "EUR", "GBP", "USDC"]

    def _load_from_config(self, asset_type):
        """Load from YAML config."""
        config = _get_bitstamp_config()
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
            self.rest_paths.update(asset_cfg.rest_paths)
        if asset_cfg.wss_paths:
            self.wss_paths.update(asset_cfg.wss_paths)
        if asset_cfg.kline_periods:
            self.kline_periods = dict(asset_cfg.kline_periods)
        lc = asset_cfg.legal_currency or (config.legal_currency if config.legal_currency else None)
        if lc:
            self.legal_currency = list(lc)
        return True


class BitstampExchangeDataSpot(BitstampExchangeData):
    """Bitstamp Spot exchange configuration."""

    def __init__(self):
        super().__init__()
        self.asset_type = "SPOT"
        self._load_from_config("spot")

    def get_symbol(self, symbol):
        """Convert symbol to Bitstamp format: lowercase concatenated (btcusd)."""
        return symbol.replace("/", "").replace("-", "").replace("_", "").lower()

    def get_period(self, key):
        """Get kline period for API (in seconds string)."""
        return self.kline_periods.get(key, key)

    def get_rest_path(self, key):
        """Get REST path for *key*. Raises ValueError if missing."""
        if key not in self.rest_paths or self.rest_paths[key] == "":
            raise ValueError(f"[{self.exchange_name}] REST path not found: {key}")
        return self.rest_paths[key]

    def get_wss_path(self, channel, symbol=None):
        """Get WSS subscription channel for *channel*."""
        tpl = self.wss_paths.get(channel, "")
        if symbol and tpl:
            pair = self.get_symbol(symbol)
            return tpl.replace("{pair}", pair)
        return tpl
