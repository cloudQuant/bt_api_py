"""
Independent Reserve Exchange Data Configuration.

API doc: https://www.independentreserve.com/API
Auth: HMAC-SHA256 signature in POST JSON body
Public: GET /Public/...  Private: POST /Private/...
Symbol: primaryCurrencyCode + secondaryCurrencyCode (e.g. Xbt, Aud)
"""

import os

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("independent_reserve_exchange_data")

_independent_reserve_config = None
_independent_reserve_config_loaded = False


def _get_independent_reserve_config():
    """Load and cache Independent Reserve YAML configuration."""
    global _independent_reserve_config, _independent_reserve_config_loaded
    if _independent_reserve_config_loaded:
        return _independent_reserve_config
    try:
        from bt_api_py.config_loader import load_exchange_config

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "independent_reserve.yaml",
        )
        if os.path.exists(config_path):
            _independent_reserve_config = load_exchange_config(config_path)
        _independent_reserve_config_loaded = True
    except Exception as e:
        logger.warn(f"Failed to load independent_reserve.yaml config: {e}")
    return _independent_reserve_config


# ── currency code mapping ───────────────────────────────────
_CODE_MAP = {
    "BTC": "Xbt",
    "ETH": "Eth",
    "USDT": "Usdt",
    "USDC": "Usdc",
    "LTC": "Ltc",
    "XRP": "Xrp",
    "BCH": "Bch",
    "EOS": "Eos",
    "XLM": "Xlm",
    "BAT": "Bat",
    "ETC": "Etc",
    "LINK": "Link",
    "GNT": "Gnt",
    "OMG": "Omg",
    "ZRX": "Zrx",
    "PMGT": "Pmgt",
    "AUD": "Aud",
    "NZD": "Nzd",
    "USD": "Usd",
    "SGD": "Sgd",
}

# ── fallback rest_paths ─────────────────────────────────────
_FALLBACK_REST_PATHS = {
    "get_tick": "GET /Public/GetMarketSummary",
    "get_depth": "GET /Public/GetOrderBook",
    "get_deals": "GET /Public/GetRecentTrades",
    "get_exchange_info": "GET /Public/GetValidPrimaryCurrencyCodes",
    "get_secondary_currencies": "GET /Public/GetValidSecondaryCurrencyCodes",
    "make_order_limit": "POST /Private/PlaceLimitOrder",
    "make_order_market": "POST /Private/PlaceMarketOrder",
    "cancel_order": "POST /Private/CancelOrder",
    "get_open_orders": "POST /Private/GetOpenOrders",
    "get_order_details": "POST /Private/GetOrderDetails",
    "get_account": "POST /Private/GetAccounts",
    "get_balance": "POST /Private/GetAccounts",
    "get_trades": "POST /Private/GetTrades",
}


class IndependentReserveExchangeData(ExchangeData):
    """Base class for Independent Reserve exchange."""

    def __init__(self):
        super().__init__()
        self.exchange_name = "INDEPENDENT_RESERVE___SPOT"
        self.rest_url = "https://api.independentreserve.com"
        self.wss_url = ""
        self.rest_paths = dict(_FALLBACK_REST_PATHS)
        self.wss_paths = {}
        self.kline_periods = {
            "1m": "1m",
            "5m": "5m",
            "15m": "15m",
            "30m": "30m",
            "1h": "1h",
            "4h": "4h",
            "1d": "1d",
            "1w": "1w",
        }
        self.reverse_kline_periods = {v: k for k, v in self.kline_periods.items()}
        self.legal_currency = ["AUD", "NZD", "USD", "SGD", "USDT", "BTC", "ETH"]

    def _load_from_config(self, asset_type):
        """Load from YAML config."""
        config = _get_independent_reserve_config()
        if config is None:
            return False
        asset_cfg = config.asset_types.get(asset_type)
        if asset_cfg is None:
            return False

        if asset_cfg.exchange_name:
            self.exchange_name = asset_cfg.exchange_name
        if hasattr(asset_cfg, "rest_url") and asset_cfg.rest_url:
            self.rest_url = asset_cfg.rest_url
        if hasattr(asset_cfg, "wss_url") and asset_cfg.wss_url is not None:
            self.wss_url = asset_cfg.wss_url
        if asset_cfg.rest_paths:
            self.rest_paths.update(asset_cfg.rest_paths)
        if asset_cfg.wss_paths:
            self.wss_paths.update(asset_cfg.wss_paths)
        kp = asset_cfg.kline_periods or (config.kline_periods if config.kline_periods else None)
        if kp:
            self.kline_periods = dict(kp)
            self.reverse_kline_periods = {v: k for k, v in self.kline_periods.items()}
        lc = asset_cfg.legal_currency or (config.legal_currency if config.legal_currency else None)
        if lc:
            self.legal_currency = list(lc)
        return True


class IndependentReserveExchangeDataSpot(IndependentReserveExchangeData):
    """Independent Reserve Spot exchange configuration."""

    def __init__(self):
        super().__init__()
        self.asset_type = "SPOT"
        self._load_from_config("spot")

    @staticmethod
    def get_symbol(symbol):
        """Parse 'BTC/AUD' → ('Xbt', 'Aud'). Returns tuple."""
        parts = symbol.split("/") if "/" in symbol else symbol.split("-")
        if len(parts) == 2:
            base = _CODE_MAP.get(parts[0].upper(), parts[0].capitalize())
            quote = _CODE_MAP.get(parts[1].upper(), parts[1].capitalize())
            return base, quote
        return "Xbt", "Aud"

    def get_period(self, key):
        """Get kline period value."""
        return self.kline_periods.get(key, key)

    def get_rest_path(self, key):
        """Get REST path for *key*. Raises ValueError if missing."""
        if key not in self.rest_paths or self.rest_paths[key] == "":
            raise ValueError(f"[{self.exchange_name}] REST path not found: {key}")
        return self.rest_paths[key]
