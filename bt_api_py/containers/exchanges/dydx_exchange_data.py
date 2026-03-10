import copy
import datetime
import json
import os
import time
from typing import Any

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("dydx_exchange_data")

# ── 配置加载缓存 ──────────────────────────────────────────────
_dydx_config = None
_dydx_config_loaded = False


def _get_dydx_config() -> Any | None:
    """延迟加载并缓存 dYdX YAML 配置."""
    global _dydx_config, _dydx_config_loaded
    if _dydx_config_loaded:
        return _dydx_config
    try:
        from bt_api_py.config_loader import load_exchange_config

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "dydx.yaml",
        )
        if os.path.exists(config_path):
            _dydx_config = load_exchange_config(config_path)
        _dydx_config_loaded = True
    except Exception as e:
        logger.warn(f"Failed to load dydx.yaml config: {e}")
    return _dydx_config


class DydxExchangeData(ExchangeData):
    """Base class for all dYdX exchange types.

    Provides shared utility methods (get_symbol, get_period, get_rest_path,
    get_wss_path) and default kline_periods.
    Subclasses MUST set exchange-specific: exchange_name, rest_paths, wss_paths.
    """

    def __init__(self) -> None:
        """这个类存放一些交易所用到的参数."""
        super().__init__()
        self.exchange_name = "DydxSwap"
        self.rest_url = "https://indexer.dydx.trade/v4"
        self.wss_url = "wss://indexer.dydx.trade/v4/ws"
        self.testnet_rest_url = "https://indexer.v4testnet.dydx.exchange/v4"
        self.testnet_wss_url = "wss://indexer.v4testnet.dydx.exchange/v4/ws"

        # 默认支持的交易对
        self.supported_symbols = [
            "BTC-USD",
            "ETH-USD",
            "SOL-USD",
            "ADA-USD",
            "AVAX-USD",
            "DOT-USD",
            "MATIC-USD",
            "LINK-USD",
            "UNI-USD",
            "AAVE-USD",
        ]

        self.rest_paths = {}
        self.wss_paths = {}
        self.kline_periods = {}
        self.reverse_kline_periods = {}
        self.status_dict = {}
        self.legal_currency = ["USD", "ETH"]

        # 从 YAML 配置加载 (默认加载 swap)
        self._load_from_config("swap")

    def _load_from_config(self, asset_type) -> bool:
        """从 YAML 配置文件加载交易所参数.

        Args:
            asset_type: 资产类型 key, 如 'swap', 'spot'
        Returns:
            bool: 是否加载成功

        """
        config = _get_dydx_config()
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
            self.rest_url = config.base_urls.rest.get("mainnet", self.rest_url)
            self.wss_url = config.base_urls.wss.get("mainnet", self.wss_url)
            self.testnet_rest_url = config.base_urls.rest.get("testnet", self.testnet_rest_url)
            self.testnet_wss_url = config.base_urls.wss.get("testnet", self.testnet_wss_url)

        # rest_paths
        if asset_cfg.rest_paths:
            self.rest_paths = dict(asset_cfg.rest_paths)

        # wss_paths: YAML channel dict -> {'args': [channel_dict], 'op': 'subscribe'}
        if asset_cfg.wss_paths:
            converted = {}
            for key, value in asset_cfg.wss_paths.items():
                if isinstance(value, dict):
                    converted[key] = {"args": [dict(value)], "op": "subscribe"}
                elif isinstance(value, str):
                    converted[key] = value if value else ""
                else:
                    converted[key] = value
            self.wss_paths = converted

        # kline_periods
        kp = asset_cfg.kline_periods or (config.kline_periods if config.kline_periods else None)
        if kp:
            self.kline_periods = dict(kp)
            self.reverse_kline_periods = {v: k for k, v in self.kline_periods.items()}

        # status_dict
        if config.status_dict:
            self.status_dict = dict(config.status_dict)

        return True

    def get_symbol(self, symbol: str) -> str:
        """格式化交易对符号.

        dYdX uses USD as quote currency, convert USDT to USD.
        Also ensures proper formatting with dash separator.
        """
        symbol = symbol.upper()
        # dYdX uses USD for perpetual contracts, convert USDT to USD
        if symbol.endswith("USDT"):
            # Handle BTC-USDT -> BTC-USD or BTCUSDT -> BTC-USD
            base = symbol[:-4]  # Remove USDT
            # If base ends with dash, keep it, otherwise add it
            symbol = base + "USD" if base.endswith("-") else base + "-USD"
        elif "-" not in symbol:
            # If no dash, insert one before the last 3-4 chars (quote currency)
            # This handles cases like BTCUSD -> BTC-USD
            if len(symbol) > 6:  # Likely need to split
                # Common quote currencies: USD, USDT, EUR, etc.
                for quote in ["USD", "EUR", "ETH", "BTC"]:
                    if symbol.endswith(quote):
                        base = symbol[: -len(quote)]
                        symbol = f"{base}-{quote}"
                        break
        return symbol

    def get_symbol_re(self, symbol):
        """反向解析交易对符号."""
        return symbol.lower()

    def get_period(self, key: str) -> str:
        """获取K线周期."""
        if key not in self.kline_periods:
            return key
        return self.kline_periods[key]

    def get_rest_path(self, key: str, **kwargs) -> str:
        """获取REST API路径."""
        if key not in self.rest_paths or self.rest_paths[key] == "":
            self.raise_path_error(self.exchange_name, key)

        path = self.rest_paths[key]
        # 替换路径中的占位符
        if "<symbol>" in path:
            path = path.replace("<symbol>", "<placeholder>")
        if "<address>" in path:
            path = path.replace("<address>", "<placeholder>")
        if "<subaccount_number>" in path:
            path = path.replace("<subaccount_number>", "<placeholder>")
        if "<limit>" in path:
            path = path.replace("<limit>", "<placeholder>")
        if "<resolution>" in path:
            path = path.replace("<resolution>", "<placeholder>")

        return path

    def str2int(self, time_str):
        """将时间字符串转换为时间戳."""
        if time_str.endswith("Z"):
            time_str = time_str[:-1]
        dt = datetime.datetime.strptime(time_str, "%Y-%m-%dT%H:%M:%S.%f")
        timestamp = int((time.mktime(dt.timetuple()) + dt.microsecond / 1000000) * 1000)
        return timestamp

    def get_wss_path(self, **kwargs) -> str:
        """获取WebSocket订阅字段."""
        key = kwargs["topic"]
        if key not in self.wss_paths or self.wss_paths[key] == "":
            self.raise_path_error(self.exchange_name, key)

        req = copy.deepcopy(self.wss_paths[key])

        # 替换订阅参数中的占位符
        for arg_dict in req.get("args", []):
            for k, v in arg_dict.items():
                if "<symbol>" in v:
                    v = v.replace("<symbol>", kwargs.get("symbol", ""))
                if "<address>" in v:
                    v = v.replace("<address>", kwargs.get("address", ""))
                if "<subaccount_number>" in v:
                    v = v.replace("<subaccount_number>", str(kwargs.get("subaccount_number", "")))
                if "<period>" in v:
                    v = v.replace("<period>", self.get_period(kwargs.get("period", "")))
                arg_dict[k] = v

        return json.dumps(req)

    def is_testnet(self):
        """检查是否使用测试网."""
        return self.rest_url == self.testnet_rest_url


class DydxExchangeDataSwap(DydxExchangeData):
    """dYdX 永续合约."""

    def __init__(self) -> None:
        super().__init__()


class DydxExchangeDataSpot(DydxExchangeData):
    """dYdX 现货 (如果支持)."""

    def __init__(self) -> None:
        super().__init__()
        self._load_from_config("spot")
