import json
import os
from typing import Any

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("bitfinex_exchange_data")

# ── 配置加载缓存 ──────────────────────────────────────────────
_bitfinex_config = None
_bitfinex_config_loaded = False


def _get_bitfinex_config() -> Any | None:
    """延迟加载并缓存 Bitfinex YAML 配置."""
    global _bitfinex_config, _bitfinex_config_loaded
    if _bitfinex_config_loaded:
        return _bitfinex_config
    try:
        from bt_api_py.config_loader import load_exchange_config

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "bitfinex.yaml",
        )
        if os.path.exists(config_path):
            _bitfinex_config = load_exchange_config(config_path)
        _bitfinex_config_loaded = True
    except Exception as e:
        logger.warn(f"Failed to load bitfinex.yaml config: {e}")
    return _bitfinex_config


class BitfinexExchangeData(ExchangeData):
    """Base class for all Bitfinex exchange types.

    Provides shared utility methods (get_symbol, get_period, get_rest_path,
    get_wss_path, account_wss_symbol) and default kline_periods.
    Subclasses MUST set exchange-specific: exchange_name, rest_url,
    acct_wss_url, wss_url, rest_paths, wss_paths, legal_currency.
    """

    def __init__(self) -> None:
        super().__init__()
        self.exchange_name = "BITFINEX"
        self.rest_url = ""
        self.acct_wss_url = ""
        self.wss_url = ""
        self.rest_paths = {}
        self.wss_paths = {}

        self.kline_periods = {
            "1m": "1m",
            "5m": "5m",
            "15m": "15m",
            "30m": "30m",
            "1h": "1h",
            "3h": "3h",
            "6h": "6h",
            "12h": "12h",
            "1d": "1D",
            "7d": "7D",
            "14d": "14D",
            "1M": "1M",
        }
        self.reverse_kline_periods = {v: k for k, v in self.kline_periods.items()}

        self.legal_currency = [
            "USD",
            "USDT",
            "BTC",
            "ETH",
            "EUR",
            "GBP",
            "JPY",
            "CAD",
            "AUD",
            "CHF",
        ]

    def _load_from_config(self, asset_type) -> bool:
        """从 YAML 配置文件加载交易所参数.

        Args:
            asset_type: 资产类型 key, 如 'spot', 'margin', 'derivatives' 等
        Returns:
            bool: 是否加载成功

        """
        config = _get_bitfinex_config()
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
        """获取交易所标准格式交易对.

        Args:
            symbol: 原始交易对符号 (e.g., 'BTC-USD')

        Returns:
            str: 交易所标准格式交易对

        """
        # Bitfinex 使用 t 前缀表示现货交易对
        if "-" in symbol:
            # Convert BTC-USD to tBTCUSD
            base, quote = symbol.split("-")
            return f"t{base}{quote}"
        return symbol

    def get_reverse_symbol(self, symbol: str) -> str:
        """从交易所格式转换回原始格式.

        Args:
            symbol: 交易所格式交易对 (e.g., 'tBTCUSD')

        Returns:
            str: 原始格式交易对

        """
        # 移除 t 前缀
        if symbol.startswith("t"):
            symbol = symbol[1:]
        # 在适当位置添加连字符
        if len(symbol) == 6:  # 如 BTCUSD -> BTC-USD
            return f"{symbol[:3]}-{symbol[3:]}"
        return symbol

    def account_wss_symbol(self, symbol: str) -> str:
        """获取 WebSocket 账户更新使用的交易对格式."""
        # Bitfinex WebSocket 使用标准格式
        return symbol.lower()

    def get_rest_path(self, key: str, **kwargs) -> str:
        """Get REST API path for a given endpoint, with optional parameter substitution.

        Args:
            key: The endpoint key (e.g., 'ticker', 'orderbook', 'klines')
            **kwargs: Optional parameters for path substitution (e.g., 'precision', 'timeframe', 'symbol')

        Returns:
            str: The API path with parameters substituted

        """
        if key not in self.rest_paths or self.rest_paths[key] == "":
            self.raise_path_error(self.exchange_name, key)
        path = self.rest_paths[key]

        # Handle list paths - return first element if list of one
        if isinstance(path, list) and len(path) == 1:
            path = path[0]

        # If path is a string, substitute parameters
        if isinstance(path, str):
            # Replace placeholders with actual values
            for param_key, param_value in kwargs.items():
                placeholder = f"<{param_key}>"
                if placeholder in path:
                    path = path.replace(placeholder, str(param_value))

        return path

    def get_wss_path(self, **kwargs) -> str:
        """Get wss key path
        :param kwargs: kwargs params
        :return: path.
        """
        # 'trade': {'params': ['trades:<symbol>'], 'method': 'SUBSCRIBE', 'id': 1}
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

    def get_period(self, period: str) -> str:
        """获取 K 线时间周期.

        Args:
            period: 时间周期 (e.g., '1m', '5m', '1h')

        Returns:
            str: 交易所标准时间周期

        """
        return self.kline_periods.get(period, period)

    def get_reverse_period(self, period: str) -> str:
        """从交易所时间周期转换回原始格式.

        Args:
            period: 交易所时间周期

        Returns:
            str: 原始时间周期

        """
        return self.reverse_kline_periods.get(period, period)


class BitfinexExchangeDataSpot(BitfinexExchangeData):
    """Bitfinex Spot Trading Data Configuration.

    Handles spot trading specific configurations for Bitfinex.
    """

    def __init__(self) -> None:
        super().__init__()
        self.asset_type = "spot"
        if not self._load_from_config("spot"):
            self.exchange_name = "BITFINEX___SPOT"
            self.rest_url = "https://api-pub.bitfinex.com/v2"
            self.wss_url = "wss://api.bitfinex.com/ws/v2"
            self.acct_wss_url = "wss://api.bitfinex.com/ws/v2"

        # Bitfinex specific REST paths (fallback only — don't override YAML)
        _defaults = {
            "base_url": "https://api-pub.bitfinex.com/v2",
            "ticker": "GET /ticker/{symbol}",
            "orderbook": "GET /book/{symbol}/{precision}",
            "klines": "GET /candles/trade:{timeframe}:{symbol}/hist",
            "trade_history": "GET /trades/{symbol}/hist",
            "account_balance": "POST /auth/r/wallets",
            "open_orders": "POST /auth/r/orders",
            "make_order": "POST /auth/w/order/submit",
            "cancel_order": "POST /auth/w/order/cancel",
            "order_status": "POST /auth/r/orders/{symbol}",
            "query_order": "GET /auth/r/orders/{id}",
            "get_order": "GET /auth/r/orders/{id}",
            "get_exchange_info": "GET /pub/symbols",
            "trade_history_auth": "POST /auth/r/trades/{symbol}/hist",
            "platform_status": "GET /platform/status",
            "symbols": "GET /conf/pub:list:pair:exchange",
            "currencies": "GET /conf/pub:list:currency",
        }
        for k, v in _defaults.items():
            self.rest_paths.setdefault(k, v)

        # Bitfinex specific WebSocket paths
        self.wss_paths.update(
            {
                "ticker": {"params": ["ticker:{symbol}"], "method": "SUBSCRIBE", "id": 1},
                "trades": {"params": ["trades:{symbol}"], "method": "SUBSCRIBE", "id": 1},
                "orderbook": {
                    "params": ["book:{symbol}:{precision}:{len}"],
                    "method": "SUBSCRIBE",
                    "id": 1,
                },
                "klines": {
                    "params": ["candle_raw_{symbol}:trade:{period}"],
                    "method": "SUBSCRIBE",
                    "id": 1,
                },
                "auth": {"params": [], "method": "SUBSCRIBE", "id": 1},
            }
        )

        # Spot specific symbol mappings
        self.symbol_mappings = {
            # Common spot pairs
            "BTC-USD": "tBTCUSD",
            "ETH-USD": "tETHUSD",
            "ETH-BTC": "tETHBTC",
            "XRP-USD": "tXRPUSD",
            "LTC-USD": "tLTCUSD",
            "ADA-USD": "tADAUSD",
            "DOT-USD": "tDOTUSD",
            "SOL-USD": "tSOLUSD",
            "MATIC-USD": "tMATICUSD",
            "AVAX-USD": "tAVAXUSD",
            "LINK-USD": "tLINKUSD",
            "UNI-USD": "tUNIUSD",
            "BCH-USD": "tBCHUSD",
            "EOS-USD": "tEOSUSD",
            "ETC-USD": "tETCUSD",
            "XLM-USD": "tXLMUSD",
            "THETA-USD": "tTHETAUSD",
            "VET-USD": "tVETUSD",
            "FIL-USD": "tFILUSD",
            "TRX-USD": "tTRXUSD",
            "XTZ-USD": "tXTZUSD",
            "ATOM-USD": "tATOMUSD",
            "NEO-USD": "tNEOUSD",
            "AAVE-USD": "tAAVEUSD",
            "COMP-USD": "tCOMPUSD",
            "MKR-USD": "tMKRUSD",
            "SNX-USD": "tSNXUSD",
            "YFI-USD": "tYFIUSD",
            "CRV-USD": "tCRVUSD",
            "SUSHI-USD": "tSUSHIUSD",
            "1INCH-USD": "t1INCHUSD",
            "ENJ-USD": "tENJUSD",
            "BAT-USD": "tBATUSD",
            "ZEC-USD": "tZECUSD",
            "DASH-USD": "tDASHUSD",
            "ZRX-USD": "tZRXUSD",
            "KNC-USD": "tKNCUSD",
            "OMG-USD": "tOMGUSD",
            "REN-USD": "tRENUSD",
            "MLN-USD": "tMLNUSD",
            "MANA-USD": "tMANAUSD",
            "SAND-USD": "tSANDUSD",
            "AXS-USD": "tAXSUSD",
            "GRT-USD": "tGRTUSD",
            "CHZ-USD": "tCHZUSD",
            "RUNE-USD": "tRUNEUSD",
            "NKN-USD": "tNKNUSD",
            "BNT-USD": "tBNTUSD",
            "KAVA-USD": "tKAVAUSD",
            "BAND-USD": "tBANDUSD",
            "ALPHA-USD": "tALPHAUSD",
            "CTSI-USD": "tCTSIUSD",
            "RVN-USD": "tRVNUSD",
            "CTK-USD": "tCTKUSD",
            "NU-USD": "tNUUSD",
            "CRO-USD": "tCROUSD",
            "LEO-USD": "tLEOUSD",
            "LRC-USD": "tLRCUSD",
            "SKL-USD": "tSKLUSD",
            "UMA-USD": "tUMAUSD",
            "PERP-USD": "tPERPUSD",
            "BADGER-USD": "tBADGERUSD",
            "FEI-USD": "tFEIUSD",
            "ANKR-USD": "tANKRUSD",
            "HNT-USD": "tHNTUSD",
            "FTT-USD": "tFTTUSD",
            "FTM-USD": "tFTMUSD",
            "CELO-USD": "tCELOUSD",
            "HEX-USD": "tHEXUSD",
            "EGLD-USD": "tEGLDUSD",
            "HBAR-USD": "tHBARUSD",
            "AMP-USD": "tAMPUSD",
            "TFUEL-USD": "tTFUELUSD",
            "HT-USD": "tHTUSD",
            "IOTA-USD": "tIOTAUSD",
            "VTHO-USD": "tVTHOUSD",
            "XVG-USD": "tXVGUSD",
            "WAVES-USD": "tWAVESUSD",
            "ZIL-USD": "tZILUSD",
            "DFI-USD": "tDFIUSD",
            "LUNA-USD": "tLUNAUSD",
            "BUSD-USD": "tBUSDUSD",  # Binance USD
            "USDC-USD": "tUSDCUSD",  # USD Coin
            "PAX-USD": "tPAXUSD",  # Paxos Standard
            "GUSD-USD": "tGUSDUSD",  # Gemini Dollar
            "HUSD-USD": "tHUSDUSD",  # HUSD
            "PAXG-USD": "tPAXGUSD",  # Paxos Gold
            "tXAUUSD": "tXAUUSD",  # Gold
            "tXAGUSD": "tXAGUSD",  # Silver
            # Stablecoins
            "fUSD": "fUSD",  # USD margin funding
            "fUSDT": "fUSDT",  # USDT margin funding
            "fBTC": "fBTC",  # BTC margin funding
            "fETH": "fETH",  # ETH margin funding
        }

        # Reverse mappings for easy lookup
        self.reverse_symbol_mappings = {v: k for k, v in self.symbol_mappings.items()}

    def get_symbol(self, symbol: str) -> str:
        """获取交易对标准格式，支持自定义映射."""
        # 首先检查是否有自定义映射
        if symbol in self.symbol_mappings:
            return self.symbol_mappings[symbol]

        # 默认处理：添加 t 前缀
        if "-" in symbol:
            base, quote = symbol.split("-")
            return f"t{base}{quote}"
        return symbol

    def get_reverse_symbol(self, symbol: str) -> str:
        """从交易所格式转换回原始格式."""
        # 首先检查反向映射
        if symbol in self.reverse_symbol_mappings:
            return self.reverse_symbol_mappings[symbol]

        # 默认处理：移除 t 前缀
        if symbol.startswith("t"):
            symbol = symbol[1:]

        # 尝试智能分割
        if len(symbol) >= 6:
            # 尝试找到 3 个字符的基础货币
            for i in range(3, len(symbol) - 2):
                base = symbol[:i]
                quote = symbol[i:]
                if base in self.legal_currency or quote in self.legal_currency:
                    return f"{base}-{quote}"

        # 如果无法智能分割，返回原始格式
        return symbol

    def get_precision(self, symbol: str) -> str:
        """获取交易对默认精度设置."""
        # 返回默认精度，可以根据需要扩展
        return "P0"  # 默认精度级别

    def get_orderbook_length(self, symbol: str) -> str:
        """获取订单簿默认长度."""
        return "25"  # 默认返回 25 档价格


class BitfinexExchangeDataMargin(BitfinexExchangeData):
    """Bitfinex Margin Trading Data Configuration.

    Handles margin trading specific configurations for Bitfinex.
    """

    def __init__(self) -> None:
        super().__init__()
        self._load_from_config("margin")


class BitfinexExchangeDataFutures(BitfinexExchangeData):
    """Bitfinex Futures Trading Data Configuration.

    Handles futures trading specific configurations for Bitfinex.
    """

    def __init__(self) -> None:
        super().__init__()
        self._load_from_config("futures")


class BitfinexExchangeDataFunding(BitfinexExchangeData):
    """Bitfinex Funding Data Configuration.

    Handles funding specific configurations for Bitfinex.
    """

    def __init__(self) -> None:
        super().__init__()
        self._load_from_config("funding")
