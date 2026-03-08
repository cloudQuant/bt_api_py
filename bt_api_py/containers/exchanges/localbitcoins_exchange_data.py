"""LocalBitcoins exchange data – Feed pattern."""

import os

import yaml

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("localbitcoins_exchange_data")

# ── YAML config cache ────────────────────────────────────────
_config_cache = None


def _load_yaml():
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    try:
        cfg_path = os.path.join(
            os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            ),
            "configs",
            "localbitcoins.yaml",
        )
        if os.path.exists(cfg_path):
            with open(cfg_path, encoding="utf-8") as f:
                _config_cache = yaml.safe_load(f)
    except Exception as e:
        logger.warn(f"Failed to load localbitcoins.yaml: {e}")
    return _config_cache


# ── Base class ────────────────────────────────────────────────


class LocalBitcoinsExchangeData(ExchangeData):
    """Base exchange data for LocalBitcoins (P2P, HMAC-SHA256)."""

    def __init__(self):
        super().__init__()
        self.exchange_name = "LOCALBITCOINS___SPOT"
        self.asset_type = "SPOT"
        self.rest_url = "https://localbitcoins.com"
        self.wss_url = ""

        self.rest_paths = {
            "get_server_time": "GET /api/ecjson.php",
            "get_tick": "GET /bitcoinaverage/ticker-all-currencies/",
            "get_all_tickers": "GET /bitcoinaverage/ticker-all-currencies/",
            "get_exchange_info": "GET /api/currencies/",
            "get_ads": "GET /api/ad-get/{id}/",
            "get_ads_search": "GET /buy-bitcoins-online/{currency}/",
            "get_online_ads": "GET /buy-bitcoins-online/{currency}/{country_code}/",
            "get_wallet": "GET /api/wallet/",
            "get_wallet_balance": "GET /api/wallet-balance/",
            "get_account": "GET /api/myself/",
            "get_balance": "GET /api/wallet-balance/",
        }

        self.kline_periods = {"1d": "1d"}
        self.reverse_kline_periods = {"1d": "1d"}

        self.legal_currency = ["USD", "EUR", "GBP", "RUB", "BTC"]

    # ── symbol helpers ──────────────────────────────────────────

    @staticmethod
    def get_symbol(symbol):
        """BTC/USD | BTC-USD → btc_usd"""
        return symbol.lower().replace("-", "_").replace("/", "_")

    @staticmethod
    def get_reverse_symbol(symbol):
        """btc_usd → BTC-USD"""
        return symbol.upper().replace("_", "-")

    # ── period helpers ──────────────────────────────────────────

    def get_period(self, period):
        return self.kline_periods.get(period, period)

    def get_reverse_period(self, period):
        return self.reverse_kline_periods.get(period, period)

    # ── path helpers ────────────────────────────────────────────

    def get_rest_path(self, key, **kwargs):
        if key not in self.rest_paths or self.rest_paths[key] == "":
            raise ValueError(f"[{self.exchange_name}] REST path not found: {key}")
        path = self.rest_paths[key]
        if kwargs:
            for k, v in kwargs.items():
                path = path.replace(f"{{{k}}}", str(v).lower())
        return path


# ── Spot class ────────────────────────────────────────────────


class LocalBitcoinsExchangeDataSpot(LocalBitcoinsExchangeData):
    """LocalBitcoins Spot (P2P) exchange data."""

    def __init__(self):
        super().__init__()
        cfg = _load_yaml()
        if cfg:
            self.exchange_name = cfg.get("exchange_name", self.exchange_name)
            self.rest_url = cfg.get("rest_url", self.rest_url)
            self.wss_url = cfg.get("wss_url", self.wss_url)
            if cfg.get("rest_paths"):
                self.rest_paths.update(cfg["rest_paths"])
            if cfg.get("kline_periods"):
                self.kline_periods = dict(cfg["kline_periods"])
                self.reverse_kline_periods = {v: k for k, v in self.kline_periods.items()}
            if cfg.get("legal_currency"):
                self.legal_currency = list(cfg["legal_currency"])
