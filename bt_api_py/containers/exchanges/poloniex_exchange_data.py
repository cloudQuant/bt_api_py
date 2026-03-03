"""
Poloniex Exchange Data Configuration

This module contains exchange-specific configuration for Poloniex,
including REST endpoints, WebSocket channels, and symbol formatting.
"""

import os
from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.functions.log_message import SpdLogManager

logger = SpdLogManager(
    file_name="poloniex_exchange_data.log", logger_name="poloniex_data", print_info=False
).create_logger()

# ── 配置加载缓存 ──────────────────────────────────────────────
_poloniex_config = None
_poloniex_config_loaded = False


def _get_poloniex_config():
    """延迟加载并缓存 Poloniex YAML 配置"""
    global _poloniex_config, _poloniex_config_loaded
    if _poloniex_config_loaded:
        return _poloniex_config
    try:
        from bt_api_py.config_loader import load_exchange_config

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "poloniex.yaml",
        )
        if os.path.exists(config_path):
            _poloniex_config = load_exchange_config(config_path)
        _poloniex_config_loaded = True
    except Exception as e:
        logger.warn(f"Failed to load poloniex.yaml config: {e}")
    return _poloniex_config


class PoloniexExchangeData(ExchangeData):
    """Base class for all Poloniex exchange types.

    Provides shared utility methods (get_symbol, get_period, get_rest_path,
    get_wss_path, account_wss_symbol) and default kline_periods.
    Subclasses MUST set exchange-specific: exchange_name, rest_url,
    acct_wss_url, wss_url, rest_paths, wss_paths, legal_currency.
    """

    def __init__(self):
        super().__init__()
        self.exchange_name = "poloniex"
        self.rest_url = ""
        self.acct_wss_url = ""
        self.wss_url = ""
        self.rest_paths = {}
        self.wss_paths = {}

        self.kline_periods = {
            "1m": "MINUTE_1",
            "5m": "MINUTE_5",
            "10m": "MINUTE_10",
            "15m": "MINUTE_15",
            "30m": "MINUTE_30",
            "1h": "HOUR_1",
            "2h": "HOUR_2",
            "4h": "HOUR_4",
            "6h": "HOUR_6",
            "12h": "HOUR_12",
            "1d": "DAY_1",
            "3d": "DAY_3",
            "1w": "WEEK_1",
            "1M": "MONTH_1",
        }
        self.reverse_kline_periods = {v: k for k, v in self.kline_periods.items()}

        self.legal_currency = [
            "USDT",
            "USD",
            "BTC",
            "ETH",
        ]

    def _load_from_config(self, asset_type):
        """从 YAML 配置文件加载交易所参数

        Args:
            asset_type: 资产类型 key, 如 'spot', 'futures' 等
        Returns:
            bool: 是否加载成功
        """
        config = _get_poloniex_config()
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
            # Poloniex uses default REST URL for all types
            self.rest_url = config.base_urls.rest.get("default", self.rest_url)
            self.wss_url = config.base_urls.wss.get("public", self.wss_url)
            self.acct_wss_url = config.base_urls.acct_wss.get("default", self.acct_wss_url)

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

    def get_symbol(self, symbol):
        """
        Convert symbol to Poloniex format.

        Args:
            symbol: Symbol in any format (e.g., "BTC-USDT", "BTC/USDT")

        Returns:
            Poloniex format symbol (e.g., "BTC_USDT")
        """
        # Remove common separators and replace with underscore
        symbol = symbol.replace("/", "").replace("-", "").upper()
        return symbol

    def account_wss_symbol(self, symbol):
        """
        Convert symbol for account WebSocket.

        Args:
            symbol: Symbol in any format

        Returns:
            Lowercase symbol with slash (e.g., "btc/usdt")
        """
        for lc in self.legal_currency:
            if lc in symbol:
                symbol = f"{symbol.split(lc)[0]}/{lc}".lower()
                break
        return symbol

    def get_period(self, key):
        """
        Get kline period in Poloniex format.

        Args:
            key: Period key (e.g., "1m", "1h", "1d")

        Returns:
            Poloniex period string (e.g., "MINUTE_1", "HOUR_1", "DAY_1")
        """
        return self.kline_periods.get(key, key)

    def get_rest_path(self, request_type):
        """
        Get REST API path for a request type.

        Args:
            request_type: Request type key (e.g., "get_ticker", "make_order")

        Returns:
            REST API path (e.g., "GET /markets/{symbol}/ticker24h")
        """
        path = self.rest_paths.get(request_type, "")
        return path

    def get_wss_path(self, channel_type, symbol=None):
        """
        Get WebSocket subscription path.

        Args:
            channel_type: Channel type (e.g., "ticker", "depth", "kline")
            symbol: Trading symbol

        Returns:
            WebSocket subscription parameters dict
        """
        path_template = self.wss_paths.get(channel_type, "")
        if isinstance(path_template, dict) and "params" in path_template:
            params = path_template["params"]
            if params and symbol:
                # Replace symbol placeholder
                symbol = self.get_symbol(symbol)
                return {
                    "params": [p.replace("<symbol>", symbol) for p in params],
                    "method": path_template.get("method", "SUBSCRIBE"),
                    "id": path_template.get("id", 1),
                }
            return path_template
        return path_template


class PoloniexExchangeDataSpot(PoloniexExchangeData):
    """Poloniex Spot Trading Configuration"""

    def __init__(self):
        super().__init__()
        # Load from YAML config
        if not self._load_from_config("spot"):
            # Fallback defaults if config loading fails
            self.exchange_name = "PoloniexSpot"
            self.rest_url = "https://api.poloniex.com"
            self.wss_url = "wss://ws.poloniex.com/ws/public"
            self.acct_wss_url = "wss://ws.poloniex.com/ws/private"

            # REST endpoints
            self.rest_paths = {
                # Market Data
                "get_markets": "GET /markets",
                "get_ticker": "GET /markets/{symbol}/ticker24h",
                "get_tickers": "GET /markets/ticker24h",
                "get_price": "GET /markets/{symbol}/price",
                "get_prices": "GET /markets/price",
                "get_orderbook": "GET /markets/{symbol}/orderBook",
                "get_kline": "GET /markets/{symbol}/candles",
                "get_trades": "GET /markets/{symbol}/trades",
                "get_server_time": "GET /markets/time",

                # Trading
                "make_order": "POST /orders",
                "cancel_order": "DELETE /orders/{id}",
                "cancel_orders": "DELETE /orders",
                "query_order": "GET /orders/{id}",
                "get_open_orders": "GET /orders",
                "get_order_history": "GET /orders/history",
                "get_deals": "GET /trades",

                # Account
                "get_balance": "GET /accounts/balances",
                "get_account": "GET /accounts/balances",
                "get_config": "GET /accounts/config",
            }

            # WebSocket channels
            self.wss_paths = {
                "ticker": {"params": ["ticker"], "method": "SUBSCRIBE", "id": 1},
                "trades": {"params": ["trades"], "method": "SUBSCRIBE", "id": 2},
                "book": {"params": ["book"], "method": "SUBSCRIBE", "id": 3},
                "book_lv2": {"params": ["book_lv2"], "method": "SUBSCRIBE", "id": 4},
                "candles_1m": {"params": ["candles_1m"], "method": "SUBSCRIBE", "id": 5},
                "candles_5m": {"params": ["candles_5m"], "method": "SUBSCRIBE", "id": 6},
                "candles_15m": {"params": ["candles_15m"], "method": "SUBSCRIBE", "id": 7},
                "candles_30m": {"params": ["candles_30m"], "method": "SUBSCRIBE", "id": 8},
                "candles_1h": {"params": ["candles_1h"], "method": "SUBSCRIBE", "id": 9},
                "candles_4h": {"params": ["candles_4h"], "method": "SUBSCRIBE", "id": 10},
                "candles_1d": {"params": ["candles_1d"], "method": "SUBSCRIBE", "id": 11},
            }
