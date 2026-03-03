"""
KuCoin exchange configuration.
Defines REST URLs, WebSocket URLs, and API endpoints for KuCoin.
"""

import json
import os

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.functions.log_message import SpdLogManager

logger = SpdLogManager(
    file_name="kucoin_exchange_data.log", logger_name="kucoin_data", print_info=False
).create_logger()

# ── 配置加载缓存 ──────────────────────────────────────────────
_kucoin_config_cache = None
_kucoin_config_loaded = False


def _get_kucoin_config():
    """延迟加载并缓存 KuCoin YAML 配置"""
    global _kucoin_config_cache, _kucoin_config_loaded
    if _kucoin_config_loaded:
        return _kucoin_config_cache
    try:
        from bt_api_py.config_loader import load_exchange_config

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "kucoin.yaml",
        )
        if os.path.exists(config_path):
            _kucoin_config_cache = load_exchange_config(config_path)
        _kucoin_config_loaded = True
    except Exception as e:
        logger.warn(f"Failed to load kucoin.yaml config: {e}")
    return _kucoin_config_cache


class KuCoinExchangeData(ExchangeData):
    """Base class for all KuCoin exchange types.

    Provides shared utility methods and default configuration.
    Subclasses MUST set exchange-specific: exchange_name, rest_url,
    wss_url, rest_paths, wss_paths, legal_currency.
    """

    def __init__(self):
        super().__init__()
        self.exchange_name = "kucoin"
        self.rest_url = "https://api.kucoin.com"
        self.wss_url = ""  # WebSocket URL is dynamically retrieved
        self.acct_wss_url = ""  # Account WebSocket URL is dynamically retrieved
        self.rest_paths = {}
        self.wss_paths = {}

        self.kline_periods = {
            "1min": "1min",
            "3min": "3min",
            "5min": "5min",
            "15min": "15min",
            "30min": "30min",
            "1hour": "1hour",
            "2hour": "2hour",
            "4hour": "4hour",
            "6hour": "6hour",
            "8hour": "8hour",
            "12hour": "12hour",
            "1day": "1day",
            "1week": "1week",
        }
        self.reverse_kline_periods = {v: k for k, v in self.kline_periods.items()}

        self.legal_currency = [
            "USDT",
            "USD",
            "BTC",
            "ETH",
            "KCS",
        ]

    def _load_from_config(self, asset_type):
        """从 YAML 配置文件加载交易所参数

        Args:
            asset_type: 资产类型 key, 如 'spot', 'futures' 等
        Returns:
            bool: 是否加载成功
        """
        config = _get_kucoin_config()
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

    # noinspection PyMethodMayBeStatic
    def get_symbol(self, symbol):
        """KuCoin uses hyphen-separated format (BTC-USDT)."""
        return symbol

    def account_wss_symbol(self, symbol):
        """Convert symbol format for account WebSocket."""
        # KuCoin account WebSocket doesn't use symbol-specific channels
        return symbol

    # noinspection PyMethodMayBeStatic
    def get_period(self, key):
        """Return period key as-is."""
        return key

    def get_rest_path(self, key):
        """Get REST API endpoint path."""
        if key not in self.rest_paths or self.rest_paths[key] == "":
            self.raise_path_error(self.exchange_name, key)
        return self.rest_paths[key]

    def get_wss_path(self, **kwargs):
        """Get WebSocket subscription message.

        Args:
            **kwargs: Subscription parameters including topic, symbol, etc.

        Returns:
            JSON string for WebSocket subscription
        """
        topic = kwargs.get("topic", "")
        if topic not in self.wss_paths or self.wss_paths[topic] == "":
            self.raise_path_error(self.exchange_name, topic)

        wss_config = self.wss_paths[topic]
        if isinstance(wss_config, dict):
            # Build subscription message
            msg = {
                "id": int(hash(topic)) % 1000000,  # Simple ID generation
                "type": "subscribe",
                "topic": wss_config["topic"],
                "privateChannel": kwargs.get("private_channel", False),
                "response": True,
            }

            # Replace symbol placeholder if present
            if "symbol" in kwargs and "<symbol>" in msg["topic"]:
                msg["topic"] = msg["topic"].replace("<symbol>", kwargs["symbol"])

            return json.dumps(msg)

        return wss_config


class KuCoinExchangeDataSpot(KuCoinExchangeData):
    def __init__(self):
        super().__init__()
        self._load_from_config("spot")


class KuCoinExchangeDataFutures(KuCoinExchangeData):
    def __init__(self):
        super().__init__()
        self._load_from_config("futures")


class KuCoinExchangeDataMargin(KuCoinExchangeData):
    def __init__(self):
        super().__init__()
        self._load_from_config("margin")
