import copy
import datetime
import json
import logging
import os
import time

from bt_api_py.containers.exchanges.exchange_data import ExchangeData

logger = logging.getLogger(__name__)

# ── 配置加载缓存 ──────────────────────────────────────────────
_okx_config = None
_okx_config_loaded = False


def _get_okx_config():
    """延迟加载并缓存 OKX YAML 配置"""
    global _okx_config, _okx_config_loaded
    if _okx_config_loaded:
        return _okx_config
    _okx_config_loaded = True
    try:
        from bt_api_py.config_loader import load_exchange_config

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "okx.yaml",
        )
        if os.path.exists(config_path):
            _okx_config = load_exchange_config(config_path)
    except Exception as e:
        logger.warning("Failed to load okx.yaml config: %s", e)
    return _okx_config


class OkxExchangeData(ExchangeData):
    """Base class for all OKX exchange types.

    Provides shared utility methods (get_symbol, get_period, get_rest_path,
    get_wss_path) and default kline_periods.
    Subclasses MUST set exchange-specific: exchange_name, rest_paths, wss_paths.
    """

    def __init__(self):
        """这个类存放一些交易所用到的参数"""
        super().__init__()
        self.exchange_name = "OkxSwap"
        self.rest_url = "https://www.okx.com"
        self.account_wss_url = "wss://ws.okx.com:8443/ws/v5/private"
        self.wss_url = "wss://ws.okx.com:8443/ws/v5/public"
        self.kline_wss_url = "wss://ws.okx.com:8443/ws/v5/business"
        self.symbol_leverage_dict = {
            "BTC-USDT": 100,
            "ETH-USDT": 10,
            "BCH-USDT": 10,
            "DOGE-USDT": 0.001,
            "BNB-USDT": 10,
            "OP-USDT": 1,
        }

        self.rest_paths = {}
        self.wss_paths = {}
        self.kline_periods = {}
        self.reverse_kline_periods = {}
        self.status_dict = {}

        # 从 YAML 配置加载 (默认加载 swap)
        self._load_from_config("swap")

    def _load_from_config(self, asset_type):
        """从 YAML 配置文件加载交易所参数

        Args:
            asset_type: 资产类型 key, 如 'swap', 'futures', 'spot'
        Returns:
            bool: 是否加载成功
        """
        config = _get_okx_config()
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
            self.kline_wss_url = config.base_urls.wss.get("business", self.kline_wss_url)

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

    # noinspection PyMethodMayBeStatic
    def get_symbol(self, symbol):
        return symbol.replace("/", "-").upper() + "-SWAP"

    # noinspection PyMethodMayBeStatic
    def get_symbol_re(self, symbol):
        return symbol.replace("-", "/").lower().rsplit("/", 1)[0]

    # noinspection PyMethodMayBeStatic
    def get_period(self, key):
        if key not in self.kline_periods:
            return key
        return self.kline_periods[key]

    def get_rest_path(self, key):
        if key not in self.rest_paths or self.rest_paths[key] == "":
            self.raise_path_error(self.exchange_name, key)
        return self.rest_paths[key]

    # noinspection PyMethodMayBeStatic
    def str2int(self, time_str):
        dt = datetime.datetime.strptime(time_str, "%Y-%m-%dT%H:%M:%S.%fZ")
        timestamp = int((time.mktime(dt.timetuple()) + dt.microsecond / 1000000) * 1000)
        return timestamp

    def get_wss_path(self, **kwargs):
        """拿wss订阅字段
        Returns:
            TYPE: Description
        """
        key = kwargs["topic"]
        if key == "mark_price" or key == "positions":
            if "symbol" in kwargs:
                kwargs["symbol"] = kwargs["symbol"]
        else:
            if "symbol" in kwargs:
                kwargs["symbol"] = self.get_symbol(kwargs["symbol"])
        if "period" in kwargs:
            kwargs["period"] = self.get_period(kwargs["period"])
        if key not in self.wss_paths or self.wss_paths[key] == "":
            self.raise_path_error(self.exchange_name, key)
        # print("kwargs", kwargs)
        req = copy.deepcopy(self.wss_paths[key])
        for k, v in req["args"][0].items():
            symbol = kwargs.get("symbol", "")
            # print("symbol", symbol, "k = ", k, "v = ", v)
            req["args"][0][k] = req["args"][0][k].replace("<symbol>", symbol)
            if "USDT" in symbol:
                currency = symbol.split("-")[1]  # self.symbol.split("-")[0] + "-" +
            else:
                currency = symbol.split("-")[0]
            req["args"][0][k] = req["args"][0][k].replace("<currency>", currency)
            req["args"][0][k] = req["args"][0][k].replace("<period>", kwargs.get("period", ""))
        req = json.dumps(req)
        # print("req_1", req)
        return req


class OkxExchangeDataSwap(OkxExchangeData):
    """OKX USDT-M Perpetual Swap."""

    pass


class OkxExchangeDataFutures(OkxExchangeData):
    """OKX Futures (expiry-based contracts)."""

    def __init__(self):
        super().__init__()
        self._load_from_config("futures")
        # Override instType in wss_paths for FUTURES
        for key in ["tick", "depth", "books", "bidAsk", "orders", "account", "positions"]:
            if key in self.wss_paths:
                for arg in self.wss_paths[key]["args"]:
                    if "instType" in arg:
                        arg["instType"] = "FUTURES"

    def get_symbol(self, symbol):
        return symbol.replace("/", "-").upper()

    def get_symbol_re(self, symbol):
        return symbol.replace("-", "/").lower()


class OkxExchangeDataSpot(OkxExchangeData):
    """OKX Spot Trading."""

    def __init__(self):
        super().__init__()
        self._load_from_config("spot")

    def get_symbol(self, symbol):
        return symbol.replace("/", "-").upper()

    # noinspection PyMethodMayBeStatic
    def get_symbol_re(self, symbol):
        return symbol.replace("-", "/").lower()

    def get_wss_path(self, **kwargs):
        """拿wss订阅字段
        Returns:
            TYPE: Description
        """
        key = kwargs["topic"]
        if "symbol" in kwargs:
            kwargs["symbol"] = self.get_symbol(kwargs["symbol"])
        if "period" in kwargs:
            kwargs["period"] = self.get_period(kwargs["period"])

        if key not in self.wss_paths or self.wss_paths[key] == "":
            self.raise_path_error(self.exchange_name, key)
        req = copy.deepcopy(self.wss_paths[key])
        for k, v in req["args"][0].items():
            symbol = kwargs.get("symbol", "")
            req["args"][0][k] = req["args"][0][k].replace("<symbol>", symbol)
            req["args"][0][k] = req["args"][0][k].replace("<currency>", symbol.split("-")[0])
            req["args"][0][k] = req["args"][0][k].replace("<period>", kwargs.get("period", ""))
        return json.dumps(req)
