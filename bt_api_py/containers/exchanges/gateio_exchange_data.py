from typing import Any

"""Gate.io Exchange Data Configuration."""

import json
import os

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("gateio_exchange_data")

# ── 配置加载缓存 ──────────────────────────────────────────────
_gateio_config = None
_gateio_config_loaded = False


def _get_gateio_config() -> Any | None:
    """延迟加载并缓存 Gate.io YAML 配置."""
    global _gateio_config, _gateio_config_loaded
    if _gateio_config_loaded:
        return _gateio_config
    try:
        from bt_api_py.config_loader import load_exchange_config

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "gateio.yaml",
        )
        if os.path.exists(config_path):
            _gateio_config = load_exchange_config(config_path)
        _gateio_config_loaded = True
    except Exception as e:
        logger.warn(f"Failed to load gateio.yaml config: {e}")
    return _gateio_config


class GateioExchangeData(ExchangeData):
    """Base class for Gate.io exchange data."""

    def __init__(self) -> None:
        super().__init__()
        self.exchange_name = "gateio"
        self.rest_url = ""
        self.acct_wss_url = ""
        self.wss_url = ""
        self.rest_paths = {}
        self.wss_paths = {}

        # Kline periods
        self.kline_periods = {
            "1s": "10s",
            "1m": "1m",
            "3m": "3m",
            "5m": "5m",
            "15m": "15m",
            "30m": "30m",
            "1h": "1h",
            "2h": "2h",
            "4h": "4h",
            "6h": "6h",
            "8h": "8h",
            "12h": "12h",
            "1d": "1d",
            "3d": "3d",
            "1w": "7d",
            "1M": "30d",
        }
        self.reverse_kline_periods = {v: k for k, v in self.kline_periods.items()}

        # Legal currencies
        self.legal_currency = [
            "USDT",
            "USD",
            "BTC",
            "ETH",
            "USDC",
        ]

    def _load_from_config(self, asset_type) -> bool:
        """从 YAML 配置文件加载交易所参数.

        Args:
            asset_type: 资产类型 key, 如 'spot', 'margin', 'futures', 'delivery', 'option'
        Returns:
            bool: 是否加载成功

        """
        config = _get_gateio_config()
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

    def get_symbol(self, symbol: str) -> str:
        """Convert symbol to exchange format."""
        # Gate.io uses underscore as separator
        return symbol.replace("-", "_")

    def get_rest_path(self, key: str, **kwargs) -> str:
        """Get REST API path."""
        return self.rest_paths.get(key, "")

    def get_wss_path(self, **kwargs) -> str:
        """Get WebSocket path (placeholder for future implementation)."""
        return json.dumps({})

    def get_period(self, period: str) -> str:
        """Convert period key to Gate.io format."""
        return self.kline_periods.get(period, period)

    def account_wss_symbol(self, symbol: str) -> str:
        """Convert symbol for WebSocket account stream."""
        for lc in self.legal_currency:
            if lc in symbol:
                symbol = f"{symbol.split(lc)[0]}_{lc}".lower()
                break
        return symbol


class GateioExchangeDataSpot(GateioExchangeData):
    """Gate.io Spot Trading Data Configuration."""

    def __init__(self) -> None:
        super().__init__()
        self.asset_type = "spot"
        self._load_from_config("spot")


class GateioExchangeDataSwap(GateioExchangeData):
    """Gate.io Futures (USDT-M) Trading Data Configuration."""

    def __init__(self) -> None:
        super().__init__()
        self.asset_type = "swap"
        self._load_from_config("futures")
