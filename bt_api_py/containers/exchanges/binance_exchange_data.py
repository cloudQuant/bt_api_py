import json
import os

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("binance_exchange_data")

# ── 配置加载缓存 ──────────────────────────────────────────────
_binance_config = None
_binance_config_loaded = False


def _get_binance_config():
    """延迟加载并缓存 Binance YAML 配置"""
    global _binance_config, _binance_config_loaded
    if _binance_config_loaded:
        return _binance_config
    try:
        from bt_api_py.config_loader import load_exchange_config

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "binance.yaml",
        )
        if os.path.exists(config_path):
            _binance_config = load_exchange_config(config_path)
        _binance_config_loaded = True
    except Exception as e:
        logger.warn(f"Failed to load binance.yaml config: {e}")
    return _binance_config


class BinanceExchangeData(ExchangeData):
    """Base class for all Binance exchange types.

    Provides shared utility methods (get_symbol, get_period, get_rest_path,
    get_wss_path, account_wss_symbol) and default kline_periods.
    Subclasses MUST set exchange-specific: exchange_name, rest_url,
    acct_wss_url, wss_url, rest_paths, wss_paths, legal_currency.
    """

    def __init__(self):
        super().__init__()
        self.exchange_name = "binance"
        self.rest_url = ""
        self.acct_wss_url = ""
        self.wss_url = ""
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
            asset_type: 资产类型 key, 如 'swap', 'spot', 'coin_m' 等
        Returns:
            bool: 是否加载成功
        """
        config = _get_binance_config()
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
        return symbol.replace("-", "")

    def account_wss_symbol(self, symbol):
        for lc in self.legal_currency:
            if lc in symbol:
                symbol = f"{symbol.split(lc)[0]}/{lc}".lower()
                break
        return symbol

    # noinspection PyMethodMayBeStatic
    def get_period(self, key):
        return key

    def get_rest_path(self, key):
        if key not in self.rest_paths or self.rest_paths[key] == "":
            self.raise_path_error(self.exchange_name, key)
        return self.rest_paths[key]

    def get_wss_path(self, **kwargs):
        """
        get wss key path
        :param kwargs: kwargs params
        :return: path
        """
        # 'depth': {'params': ['<symbol>@depth20@100ms'], 'method': 'SUBSCRIBE', 'id': 1},
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


class BinanceExchangeDataSwap(BinanceExchangeData):
    """Binance USDT-M Futures (fapi)."""

    def __init__(self):
        super().__init__()
        self._load_from_config("swap")

        self.symbol_leverage_dict = {
            "BTC-USDT": 100,
            "ETH-USDT": 10,
            "BCH-USDT": 10,
            "DOGE-USDT": 0.001,
            "BNB-USDT": 10,
            "OP-USDT": 1,
        }


class BinanceExchangeDataSpot(BinanceExchangeData):
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


class BinanceExchangeDataCoinM(BinanceExchangeData):
    def __init__(self):
        super().__init__()
        self._load_from_config("coin_m")


class BinanceExchangeDataOption(BinanceExchangeData):
    def __init__(self):
        super().__init__()
        self._load_from_config("option")


class BinanceExchangeDataMargin(BinanceExchangeData):
    def __init__(self):
        super().__init__()
        self._load_from_config("margin")


class BinanceExchangeDataAlgo(BinanceExchangeData):
    def __init__(self):
        super().__init__()
        self._load_from_config("algo")


class BinanceExchangeDataWallet(BinanceExchangeData):
    """Binance Wallet API - 资产钱包接口

    负责处理资产查询、划转、充值、提现等钱包相关功能。
    """

    def __init__(self):
        super().__init__()
        self._load_from_config("wallet")


class BinanceExchangeDataSubAccount(BinanceExchangeData):
    """Binance Sub-account API - 子账户管理接口

    负责处理子账户管理、资产划转、API Key管理等子账户相关功能。
    """

    def __init__(self):
        super().__init__()
        self._load_from_config("sub_account")


class BinanceExchangeDataPortfolio(BinanceExchangeData):
    """Binance Portfolio Margin API - 组合保证金接口

    负责处理组合保证金(PM)账户相关功能。
    """

    def __init__(self):
        super().__init__()
        self._load_from_config("portfolio")


class BinanceExchangeDataGrid(BinanceExchangeData):
    """Binance Grid Trading API - 网格交易接口

    负责处理合约网格交易相关功能。
    """

    def __init__(self):
        super().__init__()
        self._load_from_config("grid")


class BinanceExchangeDataStaking(BinanceExchangeData):
    """Binance Staking API - 质押理财接口

    负责处理质押、理财等投资产品相关功能。
    """

    def __init__(self):
        super().__init__()
        self._load_from_config("staking")


class BinanceExchangeDataMining(BinanceExchangeData):
    """Binance Mining API - 矿池接口

    负责处理矿池相关功能。
    """

    def __init__(self):
        super().__init__()
        self._load_from_config("mining")


class BinanceExchangeDataVipLoan(BinanceExchangeData):
    """Binance VIP Loan API - VIP借贷接口

    负责处理VIP借贷相关功能。
    """

    def __init__(self):
        super().__init__()
        self._load_from_config("vip_loan")
