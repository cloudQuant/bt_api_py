"""
Bitget Exchange Data Configuration
"""

import json
import os

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.functions.log_message import SpdLogManager

logger = SpdLogManager(
    file_name="bitget_exchange_data.log", logger_name="bitget_data", print_info=False
).create_logger()

# ── 配置加载缓存 ──────────────────────────────────────────────
_bitget_config = None
_bitget_config_loaded = False


def _get_bitget_config():
    """延迟加载并缓存 Bitget YAML 配置"""
    global _bitget_config, _bitget_config_loaded
    if _bitget_config_loaded:
        return _bitget_config
    try:
        from bt_api_py.config_loader import load_exchange_config

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "bitget.yaml",
        )
        if os.path.exists(config_path):
            _bitget_config = load_exchange_config(config_path)
        _bitget_config_loaded = True
    except Exception as e:
        logger.warn(f"Failed to load bitget.yaml config: {e}")
    return _bitget_config


class BitgetExchangeData(ExchangeData):
    """Base class for all Bitget exchange types.

    Provides shared utility methods (get_symbol, get_period, get_rest_path,
    get_wss_path, account_wss_symbol) and default kline_periods.
    Subclasses MUST set exchange-specific: exchange_name, rest_url,
    acct_wss_url, wss_url, rest_paths, wss_paths, legal_currency.
    """

    def __init__(self):
        super().__init__()
        self.exchange_name = "bitget"
        self.rest_url = "https://api.bitget.com"
        self.acct_wss_url = ""
        self.wss_url = "wss://ws.bitget.com/v3/ws/public"
        self.rest_paths = {}
        self.wss_paths = {}

        self.kline_periods = {
            "1s": "1s",
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
            "1w": "1w",
            "1M": "1M",
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
            asset_type: 资产类型 key, 如 'spot', 'swap', 'usdt' 等
        Returns:
            bool: 是否加载成功
        """
        config = _get_bitget_config()
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

    def get_symbol(self, symbol):
        """Convert symbol format for Bitget API.

        Args:
            symbol: Symbol in format "BTC-USDT"

        Returns:
            str: Symbol in format "BTCUSDT"
        """
        return symbol.replace("-", "")

    def account_wss_symbol(self, symbol):
        """Convert symbol for WebSocket account subscription.

        Args:
            symbol: Symbol name

        Returns:
            str: Formatted symbol for WebSocket
        """
        for lc in self.legal_currency:
            if lc in symbol[-4:]:
                symbol = f"{symbol.split(lc)[0]}/{lc}".lower()
        return symbol

    def get_period(self, key):
        """Convert period key to API format.

        Args:
            key: Period key (1m, 5m, etc.)

        Returns:
            str: Period format
        """
        return key

    def get_rest_path(self, key):
        """Get REST API path for the given key.

        Args:
            key: API endpoint key

        Returns:
            str: API path

        Raises:
            Exception: If path not found
        """
        if key not in self.rest_paths or self.rest_paths[key] == "":
            self.raise_path_error(self.exchange_name, key)
        return self.rest_paths[key]

    def get_wss_path(self, **kwargs):
        """Get WebSocket path for the given topic.

        Args:
            **kwargs: Parameters for topic construction

        Returns:
            str: WebSocket subscription message
        """
        key = kwargs["topic"]
        if "symbol" in kwargs:
            kwargs["symbol"] = self.get_symbol(kwargs["symbol"])
        if "pair" in kwargs:
            kwargs["pair"] = self.get_symbol(kwargs["pair"])
        if "period" in kwargs:
            kwargs["period"] = self.get_period(kwargs["period"])

        if key not in self.wss_paths or self.wss_paths[key] == "":
            self.raise_path_error(self.exchange_name, key)

        req = self.wss_paths[key].copy()
        key = list(req.keys())[0]
        for k, v in kwargs.items():
            if isinstance(v, str):
                req[key] = [req[key][0].replace(f"<{k}>", v.lower())]

        new_value = []
        if "symbol_list" in kwargs:
            for symbol in kwargs["symbol_list"]:
                value = req[key]
                new_value.append(value[0].replace("<symbol>", self.get_symbol(symbol).lower()))
            req[key] = new_value

        return json.dumps(req)


class BitgetExchangeDataSpot(BitgetExchangeData):
    """Bitget Spot Trading Data Configuration"""

    def __init__(self):
        super().__init__()
        self.asset_type = "spot"
        self._load_from_config("spot")

    def get_symbol(self, symbol):
        """Convert symbol format for Bitget Spot API.

        Args:
            symbol: Symbol in format "BTC-USDT"

        Returns:
            str: Symbol in format "BTCUSDT"
        """
        return symbol.replace("-", "")


class BitgetExchangeDataSwap(BitgetExchangeData):
    """Bitget Swap (USDT-M Futures) Trading Data Configuration"""

    def __init__(self):
        super().__init__()
        self.asset_type = "swap"
        self._load_from_config("swap")

    def get_symbol(self, symbol):
        """Convert symbol format for Bitget Swap API.

        Args:
            symbol: Symbol in format "BTC-USDT"

        Returns:
            str: Symbol in format "BTCUSDT"
        """
        return symbol.replace("-", "")
