import copy
import json
import os
import time

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.functions.log_message import SpdLogManager

logger = SpdLogManager(
    file_name="hyperliquid_exchange_data.log", logger_name="hyperliquid_data", print_info=False
).create_logger()

# ── 配置加载缓存 ──────────────────────────────────────────────
_hyperliquid_config = None
_hyperliquid_config_loaded = False


def _get_hyperliquid_config():
    """延迟加载并缓存 Hyperliquid YAML 配置"""
    global _hyperliquid_config, _hyperliquid_config_loaded
    if _hyperliquid_config_loaded:
        return _hyperliquid_config
    try:
        from bt_api_py.config_loader import load_exchange_config

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "hyperliquid.yaml",
        )
        if os.path.exists(config_path):
            _hyperliquid_config = load_exchange_config(config_path)
        _hyperliquid_config_loaded = True
    except Exception as e:
        logger.warn(f"Failed to load hyperliquid.yaml config: {e}")
    return _hyperliquid_config


class HyperliquidExchangeData(ExchangeData):
    """Base class for Hyperliquid exchange types.

    Provides shared utility methods (get_symbol, get_period, get_rest_path,
    get_wss_path) and default kline_periods.
    Subclasses MUST set exchange-specific: exchange_name, rest_url, wss_url.
    """

    def __init__(self):
        """Initialize Hyperliquid exchange data"""
        super().__init__()
        self.exchange_name = "hyperliquid"
        self.rest_url = "https://api.hyperliquid.xyz"
        self.wss_url = "wss://api.hyperliquid.xyz/ws"
        self.testnet_rest_url = "https://api.hyperliquid-testnet.xyz"
        self.testnet_wss_url = "wss://api.hyperliquid-testnet.xyz/ws"

        # Default kline periods for Hyperliquid
        self.kline_periods = {
            "1m": "1m",
            "3m": "3m",
            "5m": "5m",
            "15m": "15m",
            "30m": "30m",
            "1h": "1h",
            "2h": "2h",
            "4h": "4h",
            "8h": "8h",
            "12h": "12h",
            "1d": "1d",
            "3d": "3d",
            "1w": "1w",
            "1M": "1M",
        }

        self.reverse_kline_periods = {v: k for k, v in self.kline_periods.items()}

        # Status dictionary
        self.status_dict = {
            0: "NEW",
            1: "PENDING_CANCEL",
            2: "FILLED",
            3: "CANCELED",
            4: "REJECTED",
            5: "EXPIRED",
            6: "OPEN",
            7: "CLOSED",
        }

        # Trading symbol mapping for Hyperliquid
        self.trading_symbols = {
            "BTC/USDC": "BTC",
            "ETH/USDC": "ETH",
            "SOL/USDC": "SOL",
            "LINK/USDC": "LINK",
            "UNI/USDC": "UNI",
            "OP/USDC": "OP",
            "ARB/USDC": "ARB",
        }

        # From YAML config (default to spot)
        self._load_from_config("spot")

    def _load_from_config(self, asset_type):
        """从 YAML 配置文件加载交易所参数

        Args:
            asset_type: 资产类型 key, 如 'spot', 'swap'
        Returns:
            bool: 是否加载成功
        """
        config = _get_hyperliquid_config()
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
            self.wss_url = config.base_urls.wss.get("public", self.wss_url)

        # Update trading symbols if defined in config
        if asset_cfg.trading_symbols:
            self.trading_symbols.update(asset_cfg.trading_symbols)


        # kline_periods - load from YAML, prefer asset-specific config
        kp = asset_cfg.kline_periods or (config.kline_periods if config.kline_periods else None)
        if kp:
            self.kline_periods = dict(kp)

        # legal_currency - load from YAML
        lc = asset_cfg.legal_currency or (config.legal_currency if config.legal_currency else None)
        if lc:
            self.legal_currency = list(lc)

        return True

    def get_symbol(self, symbol):
        """Convert trading symbol to Hyperliquid format"""
        # For Hyperliquid, most symbols use the base name (e.g., BTC, ETH)
        # Spot symbols might use different format
        if "/" in symbol and symbol.split("/")[1] == "USDC":
            return symbol.split("/")[0]
        return symbol

    def get_rest_path(self, request_type):
        """Get REST API path for request type"""
        rest_paths = {
            "get_all_mids": "/info",
            "get_meta": "/info",
            "get_spot_meta": "/info",
            "get_l2_book": "/info",
            "get_candle_snapshot": "/info",
            "get_recent_trades": "/info",
            "get_exchange_status": "/info",
            "get_clearinghouse_state": "/info",
            "get_spot_clearinghouse_state": "/info",
            "get_order_status": "/info",
            "get_user_fills": "/info",
            "get_user_funding": "/info",
            "make_order": "/exchange",
            "cancel_order": "/exchange",
            "modify_order": "/exchange",
            "update_leverage": "/exchange",
            "usdc_transfer": "/exchange",
            "withdraw": "/exchange",
        }
        return rest_paths.get(request_type, "/info")

    def get_wss_path(self, channel_type):
        """Get WebSocket channel path"""
        # Hyperliquid uses simple subscription format
        return channel_type

    def get_account_wss_symbol(self, symbol):
        """Get symbol for account WebSocket"""
        return self.get_symbol(symbol)

    def get_order_status_text(self, status_code):
        """Convert status code to text"""
        return self.status_dict.get(status_code, f"UNKNOWN_{status_code}")

    def get_timeframe_minutes(self, timeframe):
        """Convert timeframe string to minutes"""
        return self.kline_periods.get(timeframe, 60)

    def get_timeframe_from_minutes(self, minutes):
        """Convert minutes to timeframe string"""
        return self.reverse_kline_periods.get(minutes, "1h")

    def get_leverage_limit(self, symbol):
        """Get maximum leverage for symbol"""
        # Default leverage limits for Hyperliquid
        leverage_limits = {
            "BTC": 100,
            "ETH": 50,
            "SOL": 50,
            "LINK": 20,
            "UNI": 20,
            "OP": 20,
            "ARB": 20,
        }
        base_symbol = self.get_symbol(symbol)
        return leverage_limits.get(base_symbol, 20)


class HyperliquidExchangeDataSpot(HyperliquidExchangeData):
    """Hyperliquid spot trading data container"""

    def __init__(self):
        """Initialize Hyperliquid spot data"""
        super().__init__()
        self.exchange_name = "hyperliquid_spot"

        # Spot-specific configurations
        self.spot_symbols = {
            "PURR/USDC": "PURR/USDC",
        }

        # Load from config
        self._load_from_config("spot")


class HyperliquidExchangeDataSwap(HyperliquidExchangeData):
    """Hyperliquid swap/futures data container"""

    def __init__(self):
        """Initialize Hyperliquid swap data"""
        super().__init__()
        self.exchange_name = "hyperliquid_swap"

        # Load from config
        self._load_from_config("swap")