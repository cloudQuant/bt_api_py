"""
Kraken Exchange Data Configuration
Provides URL configurations, symbol mappings, and REST paths for Kraken API.
"""

import os

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.functions.log_message import SpdLogManager

logger = SpdLogManager(
    file_name="kraken_exchange_data.log", logger_name="kraken_data", print_info=False
).create_logger()

# ── 配置加载缓存 ──────────────────────────────────────────────
_kraken_config = None
_kraken_config_loaded = False


def _get_kraken_config():
    """延迟加载并缓存 Kraken YAML 配置"""
    global _kraken_config, _kraken_config_loaded
    if _kraken_config_loaded:
        return _kraken_config
    try:
        from bt_api_py.config_loader import load_exchange_config

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "kraken.yaml",
        )
        if os.path.exists(config_path):
            _kraken_config = load_exchange_config(config_path)
        _kraken_config_loaded = True
    except Exception as e:
        logger.warn(f"Failed to load kraken.yaml config: {e}")
    return _kraken_config


class KrakenExchangeData(ExchangeData):
    """Base class for all Kraken exchange types.

    Provides shared utility methods (get_symbol, get_period, get_rest_path,
    get_wss_path, account_wss_symbol) and default kline_periods.
    Subclasses MUST set exchange-specific: exchange_name, rest_url,
    acct_wss_url, wss_url, rest_paths, wss_paths, legal_currency.
    """

    def __init__(self):
        super().__init__()
        self.exchange_name = "kraken"
        self.rest_url = "https://api.kraken.com"
        self.acct_wss_url = "wss://ws-auth.kraken.com"
        self.wss_url = "wss://ws.kraken.com"
        self.rest_paths = {}
        self.wss_paths = {}

        self.kline_periods = {
            "1m": "1",
            "5m": "5",
            "15m": "15",
            "30m": "30",
            "1h": "60",
            "2h": "120",
            "4h": "240",
            "6h": "360",
            "12h": "720",
            "1d": "1440",
            "3d": "4320",
            "1w": "10080",
        }
        self.reverse_kline_periods = {v: k for k, v in self.kline_periods.items()}

        self.legal_currency = ["USD", "EUR", "USDT", "BTC", "ETH"]

    def _load_from_config(self, asset_type):
        """从 YAML 配置文件加载交易所参数"""
        config = _get_kraken_config()
        if config is None:
            return False
        asset_cfg = config.asset_types.get(asset_type)
        if asset_cfg is None:
            return False

        if asset_cfg.exchange_name:
            self.exchange_name = asset_cfg.exchange_name

        if config.base_urls:
            self.rest_url = config.base_urls.rest.get(asset_type, self.rest_url)
            self.wss_url = config.base_urls.wss.get(asset_type, self.wss_url)
            self.acct_wss_url = config.base_urls.acct_wss.get(asset_type, self.acct_wss_url)

        if asset_cfg.rest_paths:
            self.rest_paths.update(asset_cfg.rest_paths)

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
            self.wss_paths.update(converted)

        kp = asset_cfg.kline_periods or (config.kline_periods if config.kline_periods else None)
        if kp:
            self.kline_periods = dict(kp)
            self.reverse_kline_periods = {v: k for k, v in self.kline_periods.items()}

        lc = asset_cfg.legal_currency or (config.legal_currency if config.legal_currency else None)
        if lc:
            self.legal_currency = list(lc)

        return True

    def get_symbol(self, symbol):
        """将交易对名称转换为 Kraken 格式.

        去除 '/' 和 '-', 并将 BTC 替换为 XBT (Kraken 惯例).
        """
        result = symbol.replace("/", "").replace("-", "").upper()
        result = result.replace("BTC", "XBT")
        return result

    def get_period(self, period):
        """将周期转换为 Kraken 格式."""
        return self.kline_periods.get(period, period)

    def get_rest_path(self, request_type):
        """获取 REST API 路径.

        Returns:
            str: REST API 路径 (e.g. "POST /0/public/Ticker"), 未找到返回空字符串
        """
        return self.rest_paths.get(request_type, "")

    def get_wss_path(self, channel):
        """获取 WebSocket 路径."""
        return self.wss_paths.get(channel, "")

    def account_wss_symbol(self, symbol):
        """获取 WebSocket 账户数据中的交易对格式."""
        return self.get_symbol(symbol)


class KrakenExchangeDataSpot(KrakenExchangeData):
    """Kraken Spot Trading Configuration"""

    def __init__(self):
        super().__init__()
        self.exchange_name = "kraken"
        self.asset_type = "SPOT"

        # 从 YAML 配置加载参数
        self._load_from_config("spot")

        # 确保关键路径存在 (兜底默认值)
        defaults = {
            "get_server_time": "POST /0/public/Time",
            "get_exchange_info": "POST /0/public/AssetPairs",
            "get_tick": "POST /0/public/Ticker",
            "get_ticker": "POST /0/public/Ticker",
            "get_depth": "POST /0/public/Depth",
            "get_kline": "POST /0/public/OHLC",
            "get_trades": "POST /0/public/Trades",
            "make_order": "POST /0/private/AddOrder",
            "cancel_order": "POST /0/private/CancelOrder",
            "cancel_all_orders": "POST /0/private/CancelAll",
            "get_open_orders": "POST /0/private/OpenOrders",
            "get_closed_orders": "POST /0/private/ClosedOrders",
            "query_order": "POST /0/private/QueryOrders",
            "get_balance": "POST /0/private/Balance",
            "get_trade_balance": "POST /0/private/TradeBalance",
            "get_deals": "POST /0/private/TradesHistory",
            "get_websocket_token": "POST /0/private/GetWebSocketsToken",
        }
        for k, v in defaults.items():
            self.rest_paths.setdefault(k, v)


class KrakenExchangeDataFutures(KrakenExchangeData):
    """Kraken Futures Trading Configuration"""

    def __init__(self):
        super().__init__()
        self.exchange_name = "krakenFutures"
        self.asset_type = "FUTURES"
        self.rest_url = "https://futures.kraken.com"
        self.wss_url = "wss://futures.kraken.com"
        self.acct_wss_url = "wss://futures.kraken.com"

        self._load_from_config("futures")

        defaults = {
            "get_server_time": "GET /derivatives/api/v3/time",
            "get_exchange_info": "GET /derivatives/api/v3/instruments",
            "get_tick": "GET /derivatives/api/v3/ticker",
            "get_ticker": "GET /derivatives/api/v3/ticker",
            "get_depth": "GET /derivatives/api/v3/book",
            "get_kline": "GET /derivatives/api/v3/ohlc",
            "get_trades": "GET /derivatives/api/v3/trades",
            "make_order": "POST /derivatives/api/v3/orders",
            "cancel_order": "DELETE /derivatives/api/v3/orders",
            "get_open_orders": "POST /derivatives/api/v3/orders/open",
            "query_order": "POST /derivatives/api/v3/orders",
            "get_balance": "POST /derivatives/api/v3/accounts/all",
            "get_deals": "POST /derivatives/api/v3/fills",
        }
        for k, v in defaults.items():
            self.rest_paths.setdefault(k, v)
