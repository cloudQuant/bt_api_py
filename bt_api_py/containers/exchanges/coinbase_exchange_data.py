"""Coinbase Exchange Data Container
Handles configuration loading and data structures for Coinbase exchange.
"""

import os
from typing import Any, Never

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("coinbase_exchange_data")

# ── 配置加载缓存 ──────────────────────────────────────────────
_coinbase_config = None
_coinbase_config_loaded = False


def _get_coinbase_config() -> Any | None:
    """延迟加载并缓存 Coinbase YAML 配置."""
    global _coinbase_config, _coinbase_config_loaded
    if _coinbase_config_loaded:
        return _coinbase_config
    try:
        from bt_api_py.config_loader import load_exchange_config

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "coinbase.yaml",
        )
        if os.path.exists(config_path):
            _coinbase_config = load_exchange_config(config_path)
        _coinbase_config_loaded = True
    except Exception as e:
        logger.error("Failed to load coinbase.yaml config: %s", e)
    return _coinbase_config


class CoinbaseExchangeData(ExchangeData):
    """Base class for Coinbase exchange data.

    Provides shared utility methods (get_symbol, get_period, get_rest_path) and default kline_periods.
    Subclasses MUST set exchange-specific: exchange_name, rest_url, rest_paths, legal_currency.
    """

    def __init__(self) -> None:
        super().__init__()
        self.exchange_name = "COINBASE___SPOT"
        self.rest_url = "https://api.coinbase.com/api/v3"
        self.rest_paths = {
            "get_server_time": "GET /time",
            "get_ticker": "GET /brokerage/products/{product_id}/ticker",
            "get_depth": "GET /brokerage/product_book",
            "get_kline": "GET /brokerage/products/{product_id}/candles",
            "get_exchange_info": "GET /brokerage/products",
            "make_order": "POST /brokerage/orders",
            "cancel_order": "POST /brokerage/orders/batch_cancel",
            "query_order": "GET /brokerage/orders/historical/{order_id}",
            "get_open_orders": "GET /brokerage/orders/historical/batch",
            "get_account": "GET /brokerage/accounts",
            "get_balance": "GET /brokerage/accounts",
            "get_deals": "GET /brokerage/orders/historical/fills",
        }

        self.kline_periods = {
            "1m": "ONE_MINUTE",
            "5m": "FIVE_MINUTE",
            "15m": "FIFTEEN_MINUTE",
            "30m": "THIRTY_MINUTE",
            "1h": "ONE_HOUR",
            "6h": "SIX_HOUR",
            "1d": "ONE_DAY",
        }
        self.reverse_kline_periods = {v: k for k, v in self.kline_periods.items()}

        self.legal_currency = [
            "USD",
            "USDC",
            "BTC",
            "ETH",
            "GBP",
            "EUR",
        ]

    def _load_from_config(self, asset_type) -> bool:
        """从 YAML 配置文件加载交易所参数.

        Args:
            asset_type: 资产类型 key, 如 'spot'
        Returns:
            bool: 是否加载成功

        """
        config = _get_coinbase_config()
        if config is None:
            return False
        asset_cfg = config.asset_types.get(asset_type)
        if asset_cfg is None:
            return False

        # exchange_name
        if asset_cfg.exchange_name:
            self.exchange_name = asset_cfg.exchange_name

        # rest_url
        if config.base_urls and hasattr(config.base_urls, "rest"):
            self.rest_url = getattr(config.base_urls, "rest", {}).get(asset_type, self.rest_url)

        # rest_paths
        if asset_cfg.rest_paths:
            self.rest_paths = dict(asset_cfg.rest_paths)

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

    # noinspection PyMethodMayBeStatic
    def get_symbol(self, symbol: str) -> str:
        """Convert symbol format to exchange format.

        Coinbase Advanced Trade API uses hyphenated symbols (e.g., "BTC-USD").

        Args:
            symbol: Input symbol (e.g., "BTC-USD")

        Returns:
            str: Exchange symbol format (e.g., "BTC-USD")

        """
        return symbol

    def get_rest_path(self, key: str, **kwargs) -> str:
        """Get REST API path for given key.

        Args:
            key: Path key
        Returns:
            str: REST path

        """
        if key not in self.rest_paths or self.rest_paths[key] == "":
            self.raise_path_error(self.exchange_name, key)
        return self.rest_paths[key]

    def get_period(self, key: str) -> str:
        """Get kline period for given key.

        Args:
            key: Period key (e.g., "1m", "1h", "1d")

        Returns:
            str: Exchange period format

        """
        return self.kline_periods.get(key, key)

    def raise_path_error(self, *args) -> Never:
        """Raise path error exception.

        Args:
            *args: Arguments for error message

        """
        raise Exception(f"API path not found: {args}")


class CoinbaseExchangeDataSpot(CoinbaseExchangeData):
    """Coinbase Spot Trading Data."""

    def __init__(self) -> None:
        super().__init__()
        self.exchange_name = "COINBASE___SPOT"
        self._load_from_config("spot")
