"""
Phemex Exchange Data Configuration
Provides URL configurations, symbol mappings, and REST paths for Phemex API.
"""

import os

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.functions.log_message import SpdLogManager

logger = SpdLogManager(
    file_name="phemex_exchange_data.log", logger_name="phemex_data", print_info=False
).create_logger()

# ── 配置加载缓存 ──────────────────────────────────────────────
_phemex_config = None
_phemex_config_loaded = False


def _get_phemex_config():
    """延迟加载并缓存 Phemex YAML 配置"""
    global _phemex_config, _phemex_config_loaded
    if _phemex_config_loaded:
        return _phemex_config
    try:
        from bt_api_py.config_loader import load_exchange_config

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "phemex.yaml",
        )
        if os.path.exists(config_path):
            _phemex_config = load_exchange_config(config_path)
        _phemex_config_loaded = True
    except Exception as e:
        logger.warn(f"Failed to load phemex.yaml config: {e}")
    return _phemex_config


class PhemexExchangeData(ExchangeData):
    """Base class for all Phemex exchange types.

    Provides shared utility methods and default kline_periods.
    Subclasses MUST set exchange-specific: exchange_name, rest_url,
    wss_url, rest_paths, wss_paths, legal_currency.
    """

    # Phemex scaling factors for price precision
    USDT_PRICE_SCALE = 1e8
    BTC_PRICE_SCALE = 1e8
    USD_PRICE_SCALE = 1e8

    def __init__(self):
        super().__init__()
        self.exchange_name = "phemex"
        self.rest_url = "https://api.phemex.com"
        self.wss_url = "wss://ws.phemex.com"
        self.rest_paths = {}
        self.wss_paths = {}

        self.kline_periods = {
            "1m": "60",
            "5m": "300",
            "15m": "900",
            "30m": "1800",
            "1h": "3600",
            "4h": "14400",
            "1d": "86400",
            "1w": "604800",
        }
        self.reverse_kline_periods = {v: k for k, v in self.kline_periods.items()}

        self.legal_currency = ["USDT", "USD", "BTC", "ETH"]

    def _load_from_config(self, asset_type):
        """从 YAML 配置文件加载交易所参数

        Args:
            asset_type: 资产类型 key, 如 'spot', 'perpetual' 等
        Returns:
            bool: 是否加载成功
        """
        config = _get_phemex_config()
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

    def get_symbol(self, symbol):
        """将交易对名称转换为 Phemex 格式.

        Phemex spot symbols use 's' prefix (e.g., sBTCUSDT)
        Contract symbols don't have prefix (e.g., BTCUSDT)

        Args:
            symbol: 交易对名称 (e.g., 'BTC/USDT', 'ETHUSDT')
        Returns:
            str: Phemex 格式的交易对名称
        """
        # Remove slash if present
        symbol = symbol.replace("/", "")

        # Check if it's already in Phemex format
        if symbol.startswith("s") and not symbol.startswith("SOL"):
            return symbol

        # For spot trading, add 's' prefix
        # For perpetuals, no prefix needed
        if self.asset_type == "SPOT":
            # Add 's' prefix for spot symbols
            return f"s{symbol}"

        return symbol

    def get_period(self, period):
        """将周期转换为 Phemex 格式 (resolution in seconds).

        Args:
            period: 周期名称 (e.g., '1m', '5m', '1h', '1d')
        Returns:
            str: Phemex 格式的周期 (秒数)
        """
        return self.kline_periods.get(period, period)

    def get_rest_path(self, request_type):
        """获取 REST API 路径.

        Args:
            request_type: 请求类型
        Returns:
            str: REST API 路径
        """
        if request_type not in self.rest_paths or self.rest_paths[request_type] == "":
            self.raise_path_error(self.exchange_name, request_type)
        return self.rest_paths[request_type]

    def get_wss_path(self, **kwargs):
        """获取 WebSocket 路径.

        Args:
            **kwargs: kwargs params
        Returns:
            str: WebSocket 路径
        """
        key = kwargs.get("topic", "")
        if "symbol" in kwargs:
            kwargs["symbol"] = self.get_symbol(kwargs["symbol"])
        if "period" in kwargs:
            kwargs["period"] = self.get_period(kwargs["period"])

        if key not in self.wss_paths or self.wss_paths[key] == "":
            self.raise_path_error(self.exchange_name, key)

        import json
        req = self.wss_paths[key].copy()
        req_key = list(req.keys())[0]
        for k, v in kwargs.items():
            if isinstance(v, str):
                req[req_key] = [req[req_key][0].replace(f"<{k}>", v)]

        return json.dumps(req)

    def scale_price(self, price, scale=None):
        """Scale price by Phemex scaling factor.

        Phemex uses scaled precision (Ep suffix) for prices.
        USDT pairs: 1e8 scale
        BTC pairs: 1e8 scale

        Args:
            price: Price to scale
            scale: Scaling factor (defaults to USDT_PRICE_SCALE)

        Returns:
            int: Scaled price
        """
        if scale is None:
            scale = self.USDT_PRICE_SCALE
        return int(price * scale)

    def unscale_price(self, scaled_price, scale=None):
        """Unscale price from Phemex scaling factor.

        Args:
            scaled_price: Scaled price
            scale: Scaling factor (defaults to USDT_PRICE_SCALE)

        Returns:
            float: Unscaled price
        """
        if scale is None:
            scale = self.USDT_PRICE_SCALE
        return scaled_price / scale


class PhemexExchangeDataSpot(PhemexExchangeData):
    """Phemex Spot Trading Configuration"""

    def __init__(self):
        super().__init__()
        self.asset_type = "SPOT"
        if not self._load_from_config("spot"):
            self.exchange_name = "PHEMEX___SPOT"
            self.rest_url = "https://api.phemex.com"
            self.wss_url = "wss://ws.phemex.com"

        # Fallback defaults for REST paths
        _defaults = {
            "get_server_time": "GET /exchange/public/md/v2/timestamp",
            "get_exchange_info": "GET /public/products",
            "get_tick": "GET /md/spot/ticker/24hr",
            "get_depth": "GET /md/v2/orderbook",
            "get_kline": "GET /md/v2/kline",
            "get_trades": "GET /md/v2/trade",
            "get_account": "GET /spot/wallets",
            "get_balance": "GET /spot/wallets",
            "make_order": "POST /spot/orders/create",
            "cancel_order": "DELETE /spot/orders",
            "query_order": "GET /spot/orders/active",
            "get_open_orders": "GET /spot/orders/active",
        }
        for k, v in _defaults.items():
            self.rest_paths.setdefault(k, v)

        # Ensure exchange_name is correct even if YAML loaded old value
        if self.exchange_name in ("phemex", "phemex_spot", "phemexSpot"):
            self.exchange_name = "PHEMEX___SPOT"

    def get_symbol(self, symbol):
        """将交易对名称转换为 Phemex Spot 格式.

        Spot symbols use 's' prefix: sBTCUSDT
        """
        # Remove slash if present
        symbol = symbol.replace("/", "")

        # Check if it's already in Phemex format
        if symbol.startswith("s") and not symbol.startswith("SOL"):
            return symbol

        # Add 's' prefix for spot symbols
        return f"s{symbol}"


class PhemexExchangeDataPerpetual(PhemexExchangeData):
    """Phemex Perpetual Futures Configuration"""

    def __init__(self):
        super().__init__()
        self.asset_type = "PERPETUAL"
        self._load_from_config("perpetual")

    def get_symbol(self, symbol):
        """将交易对名称转换为 Phemex Perpetual 格式.

        Perpetual symbols don't use prefix: BTCUSDT
        """
        # Remove slash if present
        return symbol.replace("/", "")
