"""CTP 交易所配置数据类
包含 CTP 行情和交易的前置地址、REST path 映射、品种信息等.
"""

import os
from typing import Any, Never

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("ctp_exchange_data")

# ── 配置加载缓存 ──────────────────────────────────────────────
_ctp_config = None
_ctp_config_loaded = False


def _get_ctp_config() -> Any | None:
    """延迟加载并缓存 CTP YAML 配置."""
    global _ctp_config, _ctp_config_loaded
    if _ctp_config_loaded:
        return _ctp_config
    try:
        from bt_api_py.config_loader import load_exchange_config

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "ctp.yaml",
        )
        if os.path.exists(config_path):
            _ctp_config = load_exchange_config(config_path)
        _ctp_config_loaded = True
    except Exception as e:
        logger.warn(f"Failed to load ctp.yaml config: {e}")
    return _ctp_config


class CtpExchangeData(ExchangeData):
    """CTP 交易所配置基类."""

    def __init__(self) -> None:
        super().__init__()
        self.exchange_name = "CTP"
        # CTP 没有 REST API，使用 SPI 回调模式
        self.rest_url = ""
        self.wss_url = ""
        # CTP 前置地址（由用户配置传入）
        self.md_front = ""  # 行情前置, 如 "tcp://180.168.146.187:10131"
        self.td_front = ""  # 交易前置, 如 "tcp://180.168.146.187:10130"

        self.kline_periods = {
            "1m": "1",
            "5m": "5",
            "15m": "15",
            "30m": "30",
            "1h": "60",
            "1d": "D",
        }
        self.reverse_kline_periods = {v: k for k, v in self.kline_periods.items()}

        # CTP 交易所代码映射
        self.exchange_id_map = {
            "CFFEX": "中金所",  # 中国金融期货交易所 (股指期货、国债期货)
            "SHFE": "上期所",  # 上海期货交易所
            "DCE": "大商所",  # 大连商品交易所
            "CZCE": "郑商所",  # 郑州商品交易所
            "INE": "能源中心",  # 上海国际能源交易中心
            "GFEX": "广期所",  # 广州期货交易所
        }

        # 从 YAML 配置加载 (基类不指定 asset_type)
        self._load_from_config(None)

    def _load_from_config(self, asset_type) -> bool:
        """从 YAML 配置文件加载交易所参数.

        Args:
            asset_type: 资产类型 key, 如 'future', 'option', 或 None (仅加载 exchange 级别)

        Returns:
            bool: 是否加载成功

        """
        config = _get_ctp_config()
        if config is None:
            return False

        # exchange-level kline_periods
        if config.kline_periods:
            self.kline_periods = dict(config.kline_periods)
            self.reverse_kline_periods = {v: k for k, v in self.kline_periods.items()}

        # exchange_id_map
        if config.exchange_id_map:
            self.exchange_id_map = dict(config.exchange_id_map)

        # asset-type level
        if asset_type:
            asset_cfg = config.asset_types.get(asset_type)
            if asset_cfg:
                if asset_cfg.exchange_name:
                    self.exchange_name = asset_cfg.exchange_name
                if asset_cfg.kline_periods:
                    self.kline_periods = dict(asset_cfg.kline_periods)
                    self.reverse_kline_periods = {v: k for k, v in self.kline_periods.items()}

        return True

    def get_symbol(self, symbol: str) -> str:
        """CTP 品种名称直接使用, 如 'IF2506', 'rb2510'."""
        return symbol

    def get_rest_path(self, key) -> Never:
        raise NotImplementedError("CTP does not use REST API, use SPI callback instead")

    def get_wss_path(self, **kwargs) -> Never:
        raise NotImplementedError("CTP does not use WebSocket, use SPI callback instead")


class CtpExchangeDataFuture(CtpExchangeData):
    """CTP 期货配置."""

    def __init__(self) -> None:
        super().__init__()
        self._load_from_config("future")


class CtpExchangeDataOption(CtpExchangeData):
    """CTP 期权配置."""

    def __init__(self) -> None:
        super().__init__()
        self._load_from_config("option")
