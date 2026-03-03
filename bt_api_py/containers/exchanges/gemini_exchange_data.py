import copy
import datetime
import json
import os
import time

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.functions.log_message import SpdLogManager

logger = SpdLogManager(
    file_name="gemini_exchange_data.log", logger_name="gemini_data", print_info=False
).create_logger()

# ── 配置加载缓存 ──────────────────────────────────────────────
_gemini_config = None
_gemini_config_loaded = False


def _get_gemini_config():
    """延迟加载并缓存 Gemini YAML 配置"""
    global _gemini_config, _gemini_config_loaded
    if _gemini_config_loaded:
        return _gemini_config
    try:
        from bt_api_py.config_loader import load_exchange_config

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "gemini.yaml",
        )
        if os.path.exists(config_path):
            _gemini_config = load_exchange_config(config_path)
        _gemini_config_loaded = True
    except Exception as e:
        logger.info(f"Failed to load gemini.yaml config: {e}")
    return _gemini_config


class GeminiExchangeData(ExchangeData):
    """Base class for all Gemini exchange types.

    Provides shared utility methods (get_symbol, get_period, get_rest_path,
    get_wss_path) and default kline_periods.
    Subclasses MUST set exchange-specific: exchange_name, rest_paths, wss_paths.
    """

    def __init__(self):
        """这个类存放一些交易所用到的参数"""
        super().__init__()
        self.exchange_name = "GeminiSpot"
        self.rest_url = "https://api.gemini.com"
        self.wss_url = "wss://api.gemini.com/v1/marketdata"
        self.account_wss_url = "wss://api.gemini.com/v1/order/events"

        # Default REST API paths (using named format strings with placeholders)
        self.rest_paths = {
            "get_symbols": "/v1/symbols",
            "get_symbol_details": "/v1/symbol_details/{symbol}",
            "get_ticker": "/v1/pubticker/{symbol}",
            "get_depth": "/v1/book/{symbol}",
            "get_trades": "/v1/trades/{symbol}",
            "get_kline": "/v2/candles/{symbol}/{time_frame}",
            "get_system_time": "/v1/timestamp",
            "get_price_feed": "/v1/pricefeed",
            "get_balance": "/v1/balances",
            "get_open_orders": "/v1/orders",
            "get_order_history": "/v1/mytrades",
            "make_order": "/v1/order/new",
            "cancel_order": "/v1/order/cancel",
            "cancel_orders": "/v1/order/cancel/all",
            "query_order": "/v1/order/status",
            "get_transfers": "/v1/transfers",
        }

        self.wss_paths = {}
        self.kline_periods = {}
        self.reverse_kline_periods = {}
        self.status_dict = {}
        self.symbol_dict = {}  # Symbol mapping dictionary

        # 从 YAML 配置加载 (默认加载 spot)
        self._load_from_config("spot")

    def _load_from_config(self, asset_type):
        """从 YAML 配置文件加载交易所参数

        Args:
            asset_type: 资产类型 key, 如 'spot', 'swap'
        Returns:
            bool: 是否加载成功
        """
        config = _get_gemini_config()
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
            self.rest_url = config.base_urls.rest.get("default", self.rest_url)
            self.wss_url = config.base_urls.wss.get("public", self.wss_url)
            self.account_wss_url = config.base_urls.wss.get("private", self.account_wss_url)

        # rest_paths
        if hasattr(asset_cfg, 'rest_paths') and asset_cfg.rest_paths:
            self.rest_paths.update(asset_cfg.rest_paths)

        # wss_paths
        if hasattr(asset_cfg, 'wss_paths') and asset_cfg.wss_paths:
            self.wss_paths.update(asset_cfg.wss_paths)

        # kline_periods
        if hasattr(asset_cfg, 'kline_periods') and asset_cfg.kline_periods:
            self.kline_periods = asset_cfg.kline_periods

        # reverse_kline_periods
        if hasattr(asset_cfg, 'reverse_kline_periods') and asset_cfg.reverse_kline_periods:
            self.reverse_kline_periods = asset_cfg.reverse_kline_periods

        # status_dict
        if hasattr(asset_cfg, 'status_dict') and asset_cfg.status_dict:
            self.status_dict = asset_cfg.status_dict


        # kline_periods - load from YAML, prefer asset-specific config
        kp = asset_cfg.kline_periods or (config.kline_periods if config.kline_periods else None)
        if kp:
            self.kline_periods = dict(kp)

        # legal_currency - load from YAML
        lc = asset_cfg.legal_currency or (config.legal_currency if config.legal_currency else None)
        if lc:
            self.legal_currency = list(lc)

        return True

    def get_symbol(self, symbol):
        """格式化交易对名称

        Args:
            symbol: 原始交易对名称

        Returns:
            str: 格式化后的交易对名称
        """
        if symbol in self.symbol_dict:
            return self.symbol_dict[symbol]

        # Gemini uses lowercase format with no separator
        # Convert BTCUSD -> btcusd, BTC/USD -> btcusd
        if symbol:
            return symbol.replace("/", "").replace("-", "").lower()

        # 如果没有在 symbol_dict 中找到，直接返回
        return symbol

    def get_period(self, period):
        """转换周期名称

        Args:
            period: 周期名称

        Returns:
            str: 转换后的周期名称
        """
        return self.reverse_kline_periods.get(period, period)

    def get_rest_path(self, request_type):
        """获取 REST API 路径

        Args:
            request_type: 请求类型

        Returns:
            str: REST API 路径
        """
        if request_type in self.rest_paths:
            return self.rest_paths[request_type]
        return ""

    def get_wss_path(self, request_type, symbol=None):
        """获取 WebSocket 路径

        Args:
            request_type: 请求类型
            symbol: 交易对名称

        Returns:
            str: WebSocket 路径
        """
        if request_type in self.wss_paths:
            path = self.wss_paths[request_type]
            if symbol and "<symbol>" in path:
                path = path.replace("<symbol>", symbol)
            return path
        return ""

    def get_status(self, status):
        """获取订单状态

        Args:
            status: 原始状态

        Returns:
            str: 标准化后的状态
        """
        return self.status_dict.get(status, status)


class GeminiExchangeDataSpot(GeminiExchangeData):
    """Gemini 现货交易数据"""

    def __init__(self):
        super().__init__()
        self.exchange_name = "GEMINI"
        self.asset_type = "SPOT"


class GeminiExchangeDataSwap(GeminiExchangeData):
    """Gemini 永续合约数据 (如果有)"""

    def __init__(self):
        super().__init__()
        self.exchange_name = "GeminiSwap"