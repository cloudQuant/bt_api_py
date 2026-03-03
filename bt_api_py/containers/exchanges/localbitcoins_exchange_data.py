import json
import os
from typing import Dict, List, Optional

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.functions.log_message import SpdLogManager

logger = SpdLogManager(
    file_name="localbitcoins_exchange_data.log", logger_name="localbitcoins_data", print_info=False
).create_logger()

# Config loading cache
_localbitcoins_config = None
_localbitcoins_config_loaded = False


def _get_localbitcoins_config():
    """延迟加载并缓存 LocalBitcoins YAML 配置"""
    global _localbitcoins_config, _localbitcoins_config_loaded
    if _localbitcoins_config_loaded:
        return _localbitcoins_config
    try:
        from bt_api_py.config_loader import load_exchange_config

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "localbitcoins.yaml",
        )
        if os.path.exists(config_path):
            _localbitcoins_config = load_exchange_config(config_path)
        _localbitcoins_config_loaded = True
    except Exception as e:
        logger.warn(f"Failed to load localbitcoins.yaml config: {e}")
    return _localbitcoins_config


class LocalBitcoinsExchangeData(ExchangeData):
    """Base class for all LocalBitcoins exchange types.

    LocalBitcoins is a P2P cryptocurrency exchange focusing on fiat-to-BTC trading.
    Uses HMAC-SHA256 signature with Apiauth header.
    """

    def __init__(self):
        super().__init__()
        self.exchange_name = "localbitcoins"
        self.rest_url = ""
        self.acct_wss_url = ""
        self.wss_url = ""
        self.rest_paths = {}
        self.wss_paths = {}

        self.kline_periods = {
            "1d": "1d",
        }
        self.reverse_kline_periods = {v: k for k, v in self.kline_periods.items()}

        self.legal_currency = ["USD", "EUR", "GBP", "RUB", "BTC"]

    def _load_from_config(self, asset_type):
        """从 YAML 配置文件加载交易所参数

        Args:
            asset_type: 资产类型 key, 如 'spot'
        Returns:
            bool: 是否加载成功
        """
        config = _get_localbitcoins_config()
        if config is None:
            return False
        asset_cfg = config.asset_types.get(asset_type)
        if asset_cfg is None:
            return False

        # Store asset_type
        self.asset_type = asset_type

        # exchange_name
        if asset_cfg.exchange_name:
            self.exchange_name = asset_cfg.exchange_name

        # URLs
        if config.base_urls:
            if isinstance(config.base_urls.rest, dict):
                self.rest_url = config.base_urls.rest.get(asset_type, self.rest_url)
            elif isinstance(config.base_urls.rest, str):
                self.rest_url = config.base_urls.rest

            if isinstance(config.base_urls.wss, dict):
                self.wss_url = config.base_urls.wss.get("public", self.wss_url)

        # rest_paths
        if asset_cfg.rest_paths:
            self.rest_paths = dict(asset_cfg.rest_paths)

        # wss_paths: Convert string templates to dict format
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

        # kline_periods
        kp = asset_cfg.kline_periods or (config.kline_periods if config.kline_periods else None)
        if kp:
            self.kline_periods = dict(kp)
            self.reverse_kline_periods = {v: k for k, v in self.kline_periods.items()}

        # legal_currency
        lc = asset_cfg.legal_currency or (config.legal_currency if config.legal_currency else None)
        if lc:
            self.legal_currency = list(lc)

        return True

    def get_symbol(self, symbol: str) -> str:
        """获取交易所标准格式交易对

        Args:
            symbol: 原始交易对符号 (e.g., 'BTC-USD', 'btc_usdt', 'BTC/USD')

        Returns:
            str: 交易所标准格式交易对 (btc_usd)
        """
        # Convert to lowercase and replace dash and slash with underscore
        symbol = symbol.lower().replace("-", "_").replace("/", "_")
        return symbol

    def get_reverse_symbol(self, symbol: str) -> str:
        """从交易所格式转换回原始格式

        Args:
            symbol: 交易所格式交易对 (e.g., 'btc_usd', 'BTC-USD')

        Returns:
            str: 原始格式交易对 (BTC-USD or BTC_USD)
        """
        # Convert to uppercase and replace dash with underscore
        # This handles both underscore and dash inputs
        return symbol.upper().replace("-", "_")

    def account_wss_symbol(self, symbol: str) -> str:
        """获取 WebSocket 账户更新使用的交易对格式"""
        return self.get_symbol(symbol)

    def get_rest_path(self, key, **kwargs):
        """获取 REST API 路径

        Args:
            key: 路径键名
            **kwargs: 路径参数替换

        Returns:
            str: 完整的 REST API 路径
        """
        if key not in self.rest_paths or self.rest_paths[key] == "":
            self.raise_path_error(self.exchange_name, key)

        path = self.rest_paths[key]
        # Replace placeholders like {id}, {currency}, {country_code}
        if kwargs:
            for k, v in kwargs.items():
                path = path.replace(f"{{{k}}}", str(v).lower())
        return path

    def get_wss_path(self, **kwargs):
        """获取 WebSocket 路径

        Args:
            **kwargs: 包含 topic, symbol 等参数

        Returns:
            str: JSON 格式的 WebSocket 订阅消息
        """
        key = kwargs.get("topic", "")
        if "symbol" in kwargs:
            kwargs["symbol"] = self.get_symbol(kwargs["symbol"])

        if key not in self.wss_paths or self.wss_paths[key] == "":
            self.raise_path_error(self.exchange_name, key)

        req = self.wss_paths[key].copy()
        if "params" in req and kwargs.get("symbol"):
            # Replace symbol in params
            params = []
            for param in req["params"]:
                params.append(param.replace("{symbol}", kwargs["symbol"]))
            req["params"] = params

        return json.dumps(req)

    def get_period(self, period: str) -> str:
        """获取 K 线时间周期

        Args:
            period: 时间周期 (e.g., '1d')

        Returns:
            str: 交易所标准时间周期
        """
        return self.kline_periods.get(period, period)

    def get_reverse_period(self, period: str) -> str:
        """从交易所时间周期转换回原始格式

        Args:
            period: 交易所时间周期

        Returns:
            str: 原始时间周期
        """
        return self.reverse_kline_periods.get(period, period)


class LocalBitcoinsExchangeDataSpot(LocalBitcoinsExchangeData):
    """LocalBitcoins Spot Trading Data Configuration

    Handles spot trading specific configurations for LocalBitcoins.
    Note: LocalBitcoins is a P2P exchange with different API patterns.
    """

    def __init__(self):
        super().__init__()
        self._load_from_config("spot")

        # Default REST paths if not loaded from config
        if not self.rest_paths:
            self.rest_paths = {
                "base_url": "/api",
                "get_server_time": "GET /api/ecjson.php",
                "get_ticker": "GET /api/ecjson.php",
            }

        # Default WebSocket paths (not supported)
        if not self.wss_paths:
            self.wss_paths = {
                "ticker": "",
            }

        # Symbol mappings for common P2P trading pairs
        self.symbol_mappings = {
            "BTC-USD": "btc_usd",
            "BTC-EUR": "btc_eur",
            "BTC-GBP": "btc_gbp",
            "BTC-RUB": "btc_rub",
        }

        # Reverse mappings
        self.reverse_symbol_mappings = {v: k for k, v in self.symbol_mappings.items()}

    def get_symbol(self, symbol: str) -> str:
        """获取交易对标准格式，支持自定义映射"""
        # First check custom mappings
        if symbol in self.symbol_mappings:
            return self.symbol_mappings[symbol]

        # Default handling: convert to lowercase and replace both dash and slash with underscore
        return symbol.lower().replace("-", "_").replace("/", "_")

    def get_reverse_symbol(self, symbol: str) -> str:
        """从交易所格式转换回原始格式"""
        # First check reverse mappings
        if symbol in self.reverse_symbol_mappings:
            return self.reverse_symbol_mappings[symbol]

        # Default handling: convert to uppercase and replace dash with underscore
        return symbol.upper().replace("-", "_")
