import json
import os

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.functions.log_message import SpdLogManager

logger = SpdLogManager(
    file_name="upbit_exchange_data.log", logger_name="upbit_data", print_info=False
).create_logger()

# ── 配置加载缓存 ──────────────────────────────────────────────
_upbit_config = None
_upbit_config_loaded = False


def _get_upbit_config():
    """延迟加载并缓存 Upbit YAML 配置"""
    global _upbit_config, _upbit_config_loaded
    if _upbit_config_loaded:
        return _upbit_config
    try:
        from bt_api_py.config_loader import load_exchange_config

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "upbit.yaml",
        )
        if os.path.exists(config_path):
            _upbit_config = load_exchange_config(config_path)
        _upbit_config_loaded = True
    except Exception as e:
        logger.warn("Failed to load upbit.yaml config: %s", str(e))
    return _upbit_config


class UpbitExchangeData(ExchangeData):
    """Base class for all Upbit exchange types.

    Provides shared utility methods (get_symbol, get_period, get_rest_path,
    get_wss_path, account_wss_symbol) and default kline_periods.
    Subclasses MUST set exchange-specific: exchange_name, rest_url,
    acct_wss_url, wss_url, rest_paths, wss_paths, legal_currency.
    """

    def __init__(self):
        super().__init__()
        self.exchange_name = "upbit"
        self.rest_url = ""
        self.acct_wss_url = ""
        self.wss_url = ""
        self.rest_paths = {}
        self.wss_paths = {}

        self.kline_periods = {
            "1m": "1",
            "3m": "3",
            "5m": "5",
            "10m": "10",
            "15m": "15",
            "30m": "30",
            "1h": "60",
            "2h": "120",
            "4h": "240",
            "6h": "360",
            "8h": "480",
            "12h": "720",
            "1d": "D",
            "3d": "3D",
            "1w": "W",
            "1M": "M",
        }
        self.reverse_kline_periods = {v: k for k, v in self.kline_periods.items()}

        self.legal_currency = [
            "KRW",
            "USDT",
            "BTC",
            "ETH",
        ]

    def _load_from_config(self, asset_type):
        """从 YAML 配置文件加载交易所参数

        Args:
            asset_type: 资产类型 key, 如 'spot'
        Returns:
            bool: 是否加载成功
        """
        config = _get_upbit_config()
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
        return symbol

    def account_wss_symbol(self, symbol):
        for lc in self.legal_currency:
            if lc in symbol:
                symbol = f"{symbol.split(lc)[0]}/{lc}".lower()
                break
        return symbol

    # noinspection PyMethodMayBeStatic
    def get_period(self, key):
        return self.kline_periods.get(key, key)

    def get_rest_path(self, key):
        if key not in self.rest_paths or self.rest_paths[key] == "":
            self.raise_path_error(self.exchange_name, key)

        # Format path with period if needed
        path = self.rest_paths[key]
        if "{unit}" in path:
            return path
        return path

    def get_wss_path(self, **kwargs):
        """
        get wss key path
        :param kwargs: kwargs params
        :return: path
        """
        key = kwargs["topic"]
        if "symbol" in kwargs:
            kwargs["symbol"] = self.get_symbol(kwargs["symbol"])
        if "period" in kwargs:
            kwargs["period"] = self.get_period(kwargs["period"])

        if key not in self.wss_paths or self.wss_paths[key] == "":
            self.raise_path_error(self.exchange_name, key)
        req = self.wss_paths[key].copy()

        # Handle single param
        if "params" in req and len(req["params"]) > 0:
            template = req["params"][0]
            for k, v in kwargs.items():
                if isinstance(v, str):
                    template = template.replace(f"<{k}>", v.lower())
            req["params"] = [template]

        return json.dumps(req)


class UpbitExchangeDataSpot(UpbitExchangeData):
    """Upbit Spot Trading"""

    def __init__(self):
        super().__init__()
        self._load_from_config("spot")

    def get_symbol(self, symbol):
        return symbol.replace("-", "")

    def account_wss_symbol(self, symbol):
        for lc in self.legal_currency:
            if lc in symbol[-4:]:
                symbol = f"{symbol.split(lc)[0]}/{lc}".lower()
        return symbol