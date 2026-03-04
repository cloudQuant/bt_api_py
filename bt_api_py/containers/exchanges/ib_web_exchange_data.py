"""
Interactive Brokers Web API 交易所配置数据
包含 REST/WebSocket URL、端点路径映射、市场数据字段定义等

IB Web API 分为两大组件:
  - Trading API (/iserver 端点): 交易、行情、持仓
  - Account Management API (/gw/api/v1 端点): 账户管理、资金、报告
"""

import os

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("ib_web_exchange_data")

# ── 配置加载缓存 ──────────────────────────────────────────────
_ib_config = None  # pydantic ExchangeConfig 对象
_ib_raw_config = None  # 原始 yaml dict (用于加载 pydantic 忽略的额外字段)
_ib_config_loaded = False


def _get_ib_yaml_path():
    """获取 ib.yaml 配置文件路径"""
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "configs",
        "ib.yaml",
    )


def _get_ib_config():
    """延迟加载并缓存 IB Web API YAML 配置 (pydantic 模型)"""
    global _ib_config, _ib_raw_config, _ib_config_loaded
    if _ib_config_loaded:
        return _ib_config
    try:
        import yaml

        from bt_api_py.config_loader import load_exchange_config

        config_path = _get_ib_yaml_path()
        if os.path.exists(config_path):
            _ib_config = load_exchange_config(config_path)
            # 同时缓存原始 dict 以访问 pydantic 忽略的额外字段
            with open(config_path, encoding="utf-8") as f:
                _ib_raw_config = yaml.safe_load(f)
        _ib_config_loaded = True
    except Exception as e:
        logger.warn(f"Failed to load ib.yaml config: {e}")
    return _ib_config


def _get_ib_raw_config():
    """获取原始 yaml dict (包含 pydantic ExchangeConfig 忽略的额外字段)"""
    if not _ib_config_loaded:
        _get_ib_config()  # 触发加载
    return _ib_raw_config


class IbWebExchangeData(ExchangeData):
    """IB Web API 交易所配置基类"""

    # 生产环境URL
    PROD_REST_URL = "https://api.interactivebrokers.com"
    # 测试环境URL
    TEST_REST_URL = "https://api.test.interactivebrokers.com"
    # Client Portal Gateway 默认URL
    GATEWAY_REST_URL = "https://localhost:5000"

    def __init__(self):
        super().__init__()
        self.exchange_name = "IB_WEB"
        self.rest_url = self.GATEWAY_REST_URL
        self.wss_url = ""

        # 以下均为空白默认值 — 实际数据全部由 ib.yaml 加载
        self.kline_periods = {}
        self.reverse_kline_periods = {}
        self.sec_type_map = {}
        self.market_data_fields = {}
        self.default_snapshot_fields = []
        self.rest_paths = {}
        self.wss_paths = {}
        self.status_dict = {}
        self.order_type_map = {}
        self.tif_map = {}
        self.rate_limits_config = {}

        # 从 YAML 配置加载 (默认加载 stk)
        self._load_from_config("stk")

    def _load_from_config(self, asset_type):
        """从 YAML 配置文件加载交易所参数

        同时使用 pydantic 校验后的 config 和原始 yaml dict:
          - config (pydantic): base_urls, kline_periods, status_dict, asset_types 等标准字段
          - raw (dict): sec_type_map, market_data_fields, order_type_map 等 pydantic 忽略的额外字段

        Args:
            asset_type: 资产类型 key, 如 'stk', 'fut', 'opt', 'cash'
        Returns:
            bool: 是否加载成功
        """
        config = _get_ib_config()
        raw = _get_ib_raw_config()
        if config is None:
            return False
        asset_cfg = config.asset_types.get(asset_type)
        if asset_cfg is None:
            return False

        # ── 从 pydantic config 加载标准字段 ──

        # exchange_name
        if asset_cfg.exchange_name:
            self.exchange_name = asset_cfg.exchange_name

        # URLs
        if config.base_urls:
            self.rest_url = config.base_urls.rest.get("default", self.rest_url)
            self.wss_url = config.base_urls.wss.get("default", self.wss_url)
            self.acct_wss_url = config.base_urls.acct_wss.get("default", self.wss_url)

        # rest_paths
        if asset_cfg.rest_paths:
            self.rest_paths = dict(asset_cfg.rest_paths)

        # wss_paths
        if asset_cfg.wss_paths:
            self.wss_paths = dict(asset_cfg.wss_paths)

        # kline_periods (asset-level 优先, 否则用 exchange-level)
        kp = asset_cfg.kline_periods or (config.kline_periods if config.kline_periods else None)
        if kp:
            self.kline_periods = dict(kp)
            self.reverse_kline_periods = {v: k for k, v in self.kline_periods.items()}

        # status_dict
        if config.status_dict:
            self.status_dict = dict(config.status_dict)

        # rate_limits → rate_limits_config
        if config.rate_limits:
            self.rate_limits_config = {r.name: r.limit / r.interval for r in config.rate_limits}

        # ── 从原始 yaml dict 加载 pydantic 忽略的额外字段 ──

        if raw:
            # sec_type_map
            if "sec_type_map" in raw:
                self.sec_type_map = dict(raw["sec_type_map"])

            # market_data_fields
            if "market_data_fields" in raw:
                self.market_data_fields = {k: str(v) for k, v in raw["market_data_fields"].items()}

            # default_snapshot_fields
            if "default_snapshot_fields" in raw:
                self.default_snapshot_fields = [str(f) for f in raw["default_snapshot_fields"]]

            # order_type_map
            if "order_type_map" in raw:
                self.order_type_map = dict(raw["order_type_map"])

            # tif_map
            if "tif_map" in raw:
                self.tif_map = dict(raw["tif_map"])

        return True

    def get_symbol(self, symbol):
        """IB Web API 品种名称直接使用"""
        return symbol

    def get_period(self, key):
        """获取 IB kline 周期映射值"""
        if key not in self.kline_periods:
            return key
        return self.kline_periods[key]

    def get_rest_path(self, key):
        """获取 REST 端点路径 (含 HTTP 方法, 如 'GET /path')"""
        path = self.rest_paths.get(key)
        if path is None or path == "":
            self.raise_path_error(self.exchange_name, key)
        return path

    def get_rest_url(self, key, **kwargs):
        """获取完整 REST URL (含路径参数替换)

        Returns:
            tuple: (method, full_url)
        """
        raw = self.get_rest_path(key)
        # 解析 'METHOD /path' 格式
        parts = raw.split(" ", 1)
        if len(parts) == 2:
            method, path = parts
        else:
            method, path = "GET", parts[0]
        if kwargs:
            path = path.format(**kwargs)
        return method, f"{self.rest_url}{path}"

    def get_wss_path(self, **kwargs):
        """获取 WebSocket URL"""
        base = self.rest_url.replace("https://", "wss://").replace("http://", "ws://")
        # 避免重复 /v1/api 前缀
        if "/v1/api" in base:
            return f"{base}/ws"
        return f"{base}/v1/api/ws"

    def get_snapshot_fields_str(self, fields=None):
        """获取市场数据快照字段字符串"""
        if fields is None:
            fields = self.default_snapshot_fields
        return ",".join(str(f) for f in fields)


class IbWebExchangeDataStock(IbWebExchangeData):
    """IB Web API 股票配置"""

    def __init__(self):
        super().__init__()
        self._load_from_config("stk")


class IbWebExchangeDataFuture(IbWebExchangeData):
    """IB Web API 期货配置"""

    def __init__(self):
        super().__init__()
        self._load_from_config("fut")


class IbWebExchangeDataOption(IbWebExchangeData):
    """IB Web API 期权配置"""

    def __init__(self):
        super().__init__()
        self._load_from_config("opt")


class IbWebExchangeDataForex(IbWebExchangeData):
    """IB Web API 外汇配置"""

    def __init__(self):
        super().__init__()
        self._load_from_config("cash")
