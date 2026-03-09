"""Ripio Exchange Data Configuration
Provides URL configurations, symbol mappings, and REST paths for Ripio API.
"""

import os
from typing import Any

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("ripio_exchange_data")

# ── 配置加载缓存 ──────────────────────────────────────────────
_ripio_config = None
_ripio_config_loaded = False


def _get_ripio_config() -> Any | None:
    """延迟加载并缓存 Ripio YAML 配置."""
    global _ripio_config, _ripio_config_loaded
    if _ripio_config_loaded:
        return _ripio_config
    try:
        from bt_api_py.config_loader import load_exchange_config

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "ripio.yaml",
        )
        if os.path.exists(config_path):
            _ripio_config = load_exchange_config(config_path)
        _ripio_config_loaded = True
    except Exception as e:
        logger.warn(f"Failed to load ripio.yaml config: {e}")
    return _ripio_config


class RipioExchangeData(ExchangeData):
    """Base class for all Ripio exchange types.

    Provides shared utility methods and default kline_periods.
    Subclasses MUST set exchange-specific: exchange_name, rest_url,
    wss_url, rest_paths, wss_paths, legal_currency.
    """

    def __init__(self) -> None:
        super().__init__()
        self.exchange_name = "ripio"
        self.rest_url = "https://api.exchange.ripio.com"
        self.wss_url = "wss://api.exchange.ripio.com/ws"
        self.rest_paths = {}
        self.wss_paths = {}

        self.kline_periods = {
            "1m": "1",
            "5m": "5",
            "15m": "15",
            "30m": "30",
            "1h": "60",
            "4h": "240",
            "1d": "1440",
            "1w": "10080",
        }
        self.reverse_kline_periods = {v: k for k, v in self.kline_periods.items()}

        self.legal_currency = ["ARS", "BRL", "EUR", "MXN", "USD", "USDT", "BTC", "ETH", "UAH"]

    def _load_from_config(self, asset_type) -> bool:
        """从 YAML 配置文件加载交易所参数.

        Args:
            asset_type: 资产类型 key, 如 'spot'
        Returns:
            bool: 是否加载成功

        """
        config = _get_ripio_config()
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
            self.wss_url = config.base_urls.wss.get("default", self.wss_url)

        # rest_paths
        if asset_cfg.rest_paths:
            self.rest_paths.update(dict(asset_cfg.rest_paths))

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
            self.wss_paths.update(converted)

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
        """将交易对名称转换为 Ripio 格式.

        Ripio uses underscore format: BTC_USDT

        Args:
            symbol: 交易对名称 (e.g., 'BTC/USDT', 'BTC-USDT')

        Returns:
            str: Ripio 格式的交易对名称

        """
        # Convert to underscore format
        return symbol.replace("-", "_").replace("/", "_").upper()

    def get_period(self, period: str) -> str:
        """将周期转换为 Ripio 格式.

        Args:
            period: 周期名称 (e.g., '1m', '5m', '1h', '1d')

        Returns:
            str: Ripio 格式的周期

        """
        return self.kline_periods.get(period, period)

    def get_rest_path(self, request_type: str, **kwargs) -> str:
        """获取 REST API 路径.

        Args:
            request_type: 请求类型
        Returns:
            str: REST API 路径

        """
        if request_type not in self.rest_paths or self.rest_paths[request_type] == "":
            self.raise_path_error(self.exchange_name, request_type)
        return self.rest_paths[request_type]

    def get_wss_path(self, **kwargs) -> str:
        """获取 WebSocket 路径.

        Args:
            **kwargs: kwargs params
        Returns:
            str: WebSocket 路径

        """
        key = kwargs.get("topic", "")
        if "symbol" in kwargs:
            kwargs["symbol"] = self.get_symbol(kwargs["symbol"])

        if key not in self.wss_paths or self.wss_paths[key] == "":
            self.raise_path_error(self.exchange_name, key)

        import json

        req = self.wss_paths[key].copy()
        req_key = list(req.keys())[0]
        for k, v in kwargs.items():
            if isinstance(v, str):
                req[req_key] = [req[req_key][0].replace(f"<{k}>", v)]

        return json.dumps(req)


class RipioExchangeDataSpot(RipioExchangeData):
    """Ripio Spot Trading Configuration."""

    def __init__(self) -> None:
        super().__init__()
        self.asset_type = "SPOT"
        self._load_from_config("spot")

    def get_symbol(self, symbol: str) -> str:
        """将交易对名称转换为 Ripio Spot 格式.

        Ripio uses underscore format: BTC_USDT
        """
        return symbol.replace("-", "_").replace("/", "_").upper()
