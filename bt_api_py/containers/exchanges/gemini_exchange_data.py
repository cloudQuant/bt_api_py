"""Gemini Exchange Data Configuration."""

import os
from typing import Any

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("gemini_exchange_data")
_gemini_config = None
_gemini_config_loaded = False


def _get_gemini_config() -> Any | None:
    """Lazy load and cache Gemini YAML configuration.

    Returns:
        Configuration dictionary or None if not found
    """
    global _gemini_config, _gemini_config_loaded
    if _gemini_config_loaded:
        return _gemini_config
    try:
        from bt_api_py.config_loader import load_exchange_config

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "gemini.yaml",
        )
        if os.path.exists(config_path):
            _gemini_config = load_exchange_config(config_path)
            _gemini_config_loaded = True
            return _gemini_config
    except (OSError, ValueError, KeyError, ImportError) as e:
        logger.info(f"Failed to load gemini.yaml config: {e}")
        _gemini_config_loaded = True
        return None
    return None


class GeminiExchangeData(ExchangeData):
    """Base class for all Gemini exchange types.

    Provides shared utility methods (get_symbol, get_period, get_rest_path,
    get_wss_path) and default kline_periods.
    Subclasses MUST set exchange-specific: exchange_name, rest_paths, wss_paths.
    """

    def __init__(self) -> None:
        """Initialize Gemini exchange data with default configuration."""
        super().__init__()
        self.exchange_name = "GEMINI"
        self.rest_url = "https://api.gemini.com"
        self.wss_url = "wss://api.gemini.com/v1/marketdata"
        self.account_wss_url = "wss://api.gemini.com/v1/order/events"

        self.rest_paths = {
            "get_symbols": "/v1/symbols",
            "get_symbol_details": "/v1/symbols/details/{symbol}",
            "get_ticker": "/v1/pubticker/{symbol}",
            "get_depth": "/v1/book/{symbol}",
            "get_trades": "/v1/trades/{symbol}",
            "get_kline": "/v2/candles/{symbol}/{time_frame}",
            "get_system_time": "/v1/timestamp",
            "get_price_feed": "/v1/pricefeed",
            "get_balance": "/v1/balances",
            "get_open_orders": "/v1/orders",
            "get_order_history": "/v1/mytrades",
            "make_order": "/v1/order/new",
            "cancel_order": "/v1/order/cancel",
            "cancel_orders": "/v1/order/cancel/all",
            "query_order": "/v1/order/status",
            "get_transfers": "/v1/transfers",
        }
        self.wss_paths: dict[str, Any] = {}
        self.kline_periods: dict[str, str] = {}
        self.reverse_kline_periods: dict[str, str] = {}
        self.status_dict: dict[str, Any] = {}
        self.symbol_dict: dict[str, str] = {}

        self._load_from_config("spot")

    def _load_from_config(self, asset_type: str) -> bool:
        """Load exchange parameters from YAML configuration file.

        Args:
            asset_type: Asset type key, e.g., 'spot', 'swap'

        Returns:
            bool: Whether loading was successful
        """
        config = _get_gemini_config()
        if config is None:
            return False
        asset_cfg = config.asset_types.get(asset_type)
        if asset_cfg is None:
            return False

        if asset_cfg.exchange_name:
            self.exchange_name = asset_cfg.exchange_name

        if config.base_urls:
            self.rest_url = config.base_urls.rest.get("default", self.rest_url)
            self.wss_url = config.base_urls.wss.get("public", self.wss_url)
            self.account_wss_url = config.base_urls.wss.get("private", self.account_wss_url)

        if hasattr(asset_cfg, "rest_paths") and asset_cfg.rest_paths:
            self.rest_paths.update(asset_cfg.rest_paths)

        if hasattr(asset_cfg, "wss_paths") and asset_cfg.wss_paths:
            self.wss_paths.update(asset_cfg.wss_paths)

        if hasattr(asset_cfg, "kline_periods") and asset_cfg.kline_periods:
            self.kline_periods = asset_cfg.kline_periods

        if hasattr(asset_cfg, "reverse_kline_periods") and asset_cfg.reverse_kline_periods:
            self.reverse_kline_periods = asset_cfg.reverse_kline_periods

        if hasattr(asset_cfg, "status_dict") and asset_cfg.status_dict:
            self.status_dict = asset_cfg.status_dict

        kp = asset_cfg.kline_periods or (config.kline_periods if config.kline_periods else None)
        if kp:
            self.kline_periods = dict(kp)

        lc = asset_cfg.legal_currency or (config.legal_currency if config.legal_currency else None)
        if lc:
            self.legal_currency = list(lc)

        return True

    def get_symbol(self, symbol: str) -> str:
        """Format trading pair name.

        Args:
            symbol: Raw trading pair name

        Returns:
            str: Formatted trading pair name
        """
        if symbol in self.symbol_dict:
            return self.symbol_dict[symbol]

        if symbol:
            return symbol.replace("/", "").replace("-", "").lower()

        return symbol

    def get_period(self, period: str) -> str:
        """Convert period name.

        Args:
            period: Period name

        Returns:
            str: Converted period name
        """
        return self.reverse_kline_periods.get(period, period)

    def get_rest_path(self, request_type: str, **kwargs: Any) -> str:
        """Get REST API path.

        Args:
            request_type: Request type

        Returns:
            str: REST API path
        """
        if request_type in self.rest_paths:
            return self.rest_paths[request_type]
        return ""


class GeminiExchangeDataSpot(GeminiExchangeData):
    """Gemini Spot Exchange Configuration."""

    def __init__(self) -> None:
        """Initialize Gemini spot exchange data."""
        super().__init__()
        self.asset_type = "SPOT"


class GeminiExchangeDataSwap(GeminiExchangeData):
    """Gemini Swap Exchange Configuration."""

    def __init__(self) -> None:
        """Initialize Gemini swap exchange data."""
        super().__init__()
        self.asset_type = "SWAP"
