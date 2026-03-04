"""
Crypto.com Exchange Data Configuration
"""

import json
import os

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("cryptocom_exchange_data")

# ── 配置加载缓存 ──────────────────────────────────────────────
_cryptocom_config = None
_cryptocom_config_loaded = False


def _get_cryptocom_config():
    """延迟加载并缓存 Crypto.com YAML 配置"""
    global _cryptocom_config, _cryptocom_config_loaded
    if _cryptocom_config_loaded:
        return _cryptocom_config
    try:
        from bt_api_py.config_loader import load_exchange_config

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "cryptocom.yaml",
        )
        if os.path.exists(config_path):
            _cryptocom_config = load_exchange_config(config_path)
        _cryptocom_config_loaded = True
    except Exception as e:
        logger.warn(f"Failed to load cryptocom.yaml config: {e}")
    return _cryptocom_config


class CryptoComExchangeData(ExchangeData):
    """Base class for all Crypto.com exchange types.

    Provides shared utility methods (get_symbol, get_period, get_rest_path,
    get_wss_path, account_wss_symbol) and default kline_periods.
    Subclasses MUST set exchange-specific: exchange_name, rest_url,
    acct_wss_url, wss_url, rest_paths, wss_paths, legal_currency.
    """

    def __init__(self):
        super().__init__()
        self.exchange_name = "cryptocom"
        self.rest_url = ""
        self.acct_wss_url = ""
        self.wss_url = ""
        self.rest_paths = {}
        self.wss_paths = {}

        self.kline_periods = {
            "1m": "1m",
            "3m": "3m",
            "5m": "5m",
            "15m": "15m",
            "30m": "30m",
            "1h": "1h",
            "2h": "2h",
            "4h": "4h",
            "6h": "6h",
            "12h": "12h",
            "1d": "1D",
            "7d": "7D",
            "14d": "14D",
            "1M": "1M",
        }
        self.reverse_kline_periods = {v: k for k, v in self.kline_periods.items()}

        self.legal_currency = [
            "USDT",
            "USD",
            "BTC",
            "ETH",
            "CRO",
        ]

    def _load_from_config(self, asset_type):
        """从 YAML 配置文件加载交易所参数

        Args:
            asset_type: 资产类型 key, 如 'spot'
        Returns:
            bool: 是否加载成功
        """
        config = _get_cryptocom_config()
        if config is None:
            return False
        asset_cfg = config.asset_types.get(asset_type)
        if asset_cfg is None:
            return False

        # exchange_name
        if asset_cfg.exchange_name:
            self.exchange_name = asset_cfg.exchange_name

        # URLs
        if config.base_urls:
            self.rest_url = config.base_urls.rest.get(asset_type, self.rest_url)
            self.wss_url = config.base_urls.wss.get(asset_type, self.wss_url)
            self.acct_wss_url = config.base_urls.acct_wss.get(asset_type, self.acct_wss_url)

        # rest_paths (直接使用, 格式一致)
        if asset_cfg.rest_paths:
            self.rest_paths = dict(asset_cfg.rest_paths)

        # wss_paths: YAML 模板字符串 → {'params': [template], 'method': 'SUBSCRIBE', 'id': 1}
        if asset_cfg.wss_paths:
            converted = {}
            for key, value in asset_cfg.wss_paths.items():
                if isinstance(value, str):
                    if value:
                        converted[key] = {"params": [value], "method": "SUBSCRIBE", "id": 1}
                    else:
                        converted[key] = ""
                else:
                    converted[key] = value
            self.wss_paths = converted

        # kline_periods (asset-level 优先, 否则用 exchange-level)
        kp = asset_cfg.kline_periods or (config.kline_periods if config.kline_periods else None)
        if kp:
            self.kline_periods = dict(kp)
            self.reverse_kline_periods = {v: k for k, v in self.kline_periods.items()}

        # legal_currency (asset-level 优先, 否则用 exchange-level)
        lc = asset_cfg.legal_currency or (config.legal_currency if config.legal_currency else None)
        if lc:
            self.legal_currency = list(lc)

        return True

    # noinspection PyMethodMayBeStatic
    def get_symbol(self, symbol):
        """Get normalized symbol for API requests.

        Crypto.com uses underscore format like BTC_USDT
        """
        return symbol.replace("/", "_").replace("-", "_")

    # noinspection PyMethodMayBeStatic
    def get_period(self, key):
        return key

    def get_rest_path(self, key):
        if key not in self.rest_paths or self.rest_paths[key] == "":
            self.raise_path_error(self.exchange_name, key)
        return self.rest_paths[key]

    def get_wss_path(self, **kwargs):
        """
        get wss key path
        :param kwargs: kwargs params
        :return: path
        """
        # 'ticker': {'params': ['ticker.{symbol}'], 'method': 'SUBSCRIBE', 'id': 1},
        key = kwargs["topic"]
        if "symbol" in kwargs:
            kwargs["symbol"] = self.get_symbol(kwargs["symbol"])
        if "pair" in kwargs:
            kwargs["pair"] = self.get_symbol(kwargs["pair"])
        if "period" in kwargs:
            kwargs["period"] = self.get_period(kwargs["period"])

        if key not in self.wss_paths or self.wss_paths[key] == "":
            self.raise_path_error(self.exchange_name, key)
        req = self.wss_paths[key].copy()
        key = list(req.keys())[0]
        for k, v in kwargs.items():
            if isinstance(v, str):
                req[key] = [req[key][0].replace(f"<{k}>", v.lower())]
        new_value = []
        if "symbol_list" in kwargs:
            for symbol in kwargs["symbol_list"]:
                value = req[key]
                new_value.append(value[0].replace("<symbol>", self.get_symbol(symbol).lower()))
            req[key] = new_value
        return json.dumps(req)


class CryptoComExchangeDataSpot(CryptoComExchangeData):
    """Crypto.com Spot exchange data configuration."""

    def __init__(self):
        super().__init__()
        self.asset_type = "spot"
        if not self._load_from_config("spot"):
            self.exchange_name = "CRYPTOCOM___SPOT"
            self.rest_url = "https://api.crypto.com/exchange/v1"
            self.wss_url = "wss://stream.crypto.com/exchange/v1/market"
            self.acct_wss_url = "wss://stream.crypto.com/exchange/v1/user"

        # Fallback REST paths — don't override YAML
        _defaults = {
            "get_server_time": "GET /public/get-ticker",
            "get_exchange_info": "GET /public/get-instruments",
            "get_tick": "GET /public/get-tickers",
            "get_ticker": "GET /public/get-tickers",
            "get_depth": "GET /public/get-book",
            "get_kline": "GET /public/get-candlestick",
            "get_trade_history": "GET /public/get-trades",
            "get_account": "POST /private/get-account-summary",
            "get_balance": "POST /private/get-account-summary",
            "make_order": "POST /private/create-order",
            "cancel_order": "POST /private/cancel-order",
            "query_order": "POST /private/get-order-detail",
            "get_order": "POST /private/get-order-detail",
            "get_open_orders": "POST /private/get-open-orders",
        }
        for k, v in _defaults.items():
            self.rest_paths.setdefault(k, v)

        # Status mapping
        self.status_dict = {
            "active": "ACTIVE",
            "suspended": "SUSPENDED",
            "delisted": "DELISTED",
        }

        # Rate limits
        self.rate_limit_type = "REQUEST_WEIGHT"
        self.interval = "SECOND"
        self.interval_num = 1
        self.limit = 100
        self.rate_limits = [
            {
                "rateLimitType": "REQUEST_WEIGHT",
                "interval": "SECOND",
                "intervalNum": 1,
                "limit": 100,
            },
            {
                "rateLimitType": "ORDERS",
                "interval": "SECOND",
                "intervalNum": 1,
                "limit": 15,
            },
            {
                "rateLimitType": "REQUEST_WEIGHT",
                "interval": "MINUTE",
                "intervalNum": 1,
                "limit": 6000,
            },
        ]

        # Initialize server time
        self.server_time = 0.0
        self.local_update_time = 0.0
        self.timezone = "UTC"

    def get_symbol_path(self, symbol):
        """Get normalized symbol path for API requests.

        Crypto.com uses underscore format like BTC_USDT
        """
        return symbol.replace("/", "_")

    def get_rest_path(self, endpoint):
        """Get REST API path for endpoint."""
        if endpoint not in self.rest_paths or self.rest_paths[endpoint] == "":
            self.raise_path_error(self.exchange_name, endpoint)
        return self.rest_paths[endpoint]

    def get_wss_path(self, channel_type, **kwargs):
        """Get WebSocket path for channel type."""
        if channel_type in self.wss_paths:
            path = self.wss_paths[channel_type]
            for key, value in kwargs.items():
                path = path.replace(f"{{{key}}}", str(value))
            return path
        return None

    def get_instrument_name(self, symbol):
        """Convert symbol to Crypto.com instrument_name format.

        BTC/USDT -> BTC_USDT
        """
        return symbol.replace("/", "_")

    def get_symbol_from_instrument(self, instrument_name):
        """Convert Crypto.com instrument_name to symbol format.

        BTC_USDT -> BTC/USDT
        """
        return instrument_name.replace("_", "/")

    def validate_symbol(self, symbol):
        """Validate symbol format for Crypto.com."""
        if not symbol:
            return False

        # Check if it matches expected format
        if "/" in symbol:
            base, quote = symbol.split("/")
            if len(base) < 1 or len(quote) < 1:
                return False
        else:
            # Check underscore format for API calls
            if "_" in symbol:
                base, quote = symbol.split("_")
                if len(base) < 1 or len(quote) < 1:
                    return False

        return True

    def get_depth_levels(self, depth=50):
        """Get valid depth levels for orderbook."""
        return min(max(1, depth), 50)

    def get_kline_period(self, period):
        """Convert period to Crypto.com kline format."""
        return self.kline_periods.get(period, period)

    def get_period_from_kline(self, kline_period):
        """Convert Crypto.com kline period to standard format."""
        return self.reverse_kline_periods.get(kline_period, kline_period)
