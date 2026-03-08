"""
CoW Swap Exchange Data Configuration
CoW Swap is a DEX (Decentralized Exchange) on Ethereum and other chains.
"""

import os

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("cow_swap_exchange_data")

_cow_swap_config = None
_cow_swap_config_loaded = False


def _get_cow_swap_config():
    """Load CoW Swap YAML configuration."""
    global _cow_swap_config, _cow_swap_config_loaded
    if _cow_swap_config_loaded:
        return _cow_swap_config
    try:
        from bt_api_py.config_loader import load_exchange_config

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "cow_swap.yaml",
        )
        if os.path.exists(config_path):
            _cow_swap_config = load_exchange_config(config_path)
        _cow_swap_config_loaded = True
    except Exception as e:
        logger.warn(f"Failed to load cow_swap.yaml config: {e}")
    return _cow_swap_config


class CowSwapExchangeData(ExchangeData):
    """Base class for CoW Swap DEX."""

    def __init__(self):
        super().__init__()
        self.exchange_name = "cow_swap"
        self.rest_url = "https://api.cow.fi"
        self.wss_url = ""
        self.kline_periods = {
            "1m": "60",
            "5m": "300",
            "15m": "900",
            "30m": "1800",
            "1h": "3600",
            "4h": "14400",
            "1d": "86400",
        }
        self.legal_currency = ["USDT", "USD", "EUR", "DAI", "USDC", "BTC", "ETH", "WETH"]
        self.supported_chains = ["mainnet", "xdai", "arbitrum_one", "base", "avalanche", "polygon"]

    def _load_from_config(self, asset_type):
        """Load from YAML config."""
        config = _get_cow_swap_config()
        if config is None:
            return False
        asset_cfg = config.asset_types.get(asset_type)
        if asset_cfg is None:
            return False

        if asset_cfg.exchange_name:
            self.exchange_name = asset_cfg.exchange_name
        if config.base_urls and config.base_urls.rest:
            rest = config.base_urls.rest
            self.rest_url = (
                rest.get(asset_type, rest.get("default", self.rest_url))
                if isinstance(rest, dict)
                else rest
            )
        if config.base_urls and config.base_urls.wss:
            wss = config.base_urls.wss
            self.wss_url = (
                wss.get(asset_type, wss.get("default", self.wss_url))
                if isinstance(wss, dict)
                else wss
            )
        if asset_cfg.rest_paths:
            self.rest_paths.update(asset_cfg.rest_paths)
        if hasattr(config, "supported_chains"):
            self.supported_chains = config.supported_chains

        # kline_periods - load from YAML, prefer asset-specific config
        kp = asset_cfg.kline_periods or (config.kline_periods if config.kline_periods else None)
        if kp:
            self.kline_periods = dict(kp)

        # legal_currency - load from YAML
        lc = asset_cfg.legal_currency or (config.legal_currency if config.legal_currency else None)
        if lc:
            self.legal_currency = list(lc)

        return True


class CowSwapExchangeDataSpot(CowSwapExchangeData):
    """CoW Swap Spot exchange configuration."""

    def __init__(self):
        super().__init__()
        self.asset_type = "spot"
        self.rest_paths = {}
        self.wss_paths = {}
        self._load_from_config("spot")

    def get_rest_url(self):
        """Get the REST URL for this exchange."""
        return self.rest_url

    def get_symbol(self, address):
        """Get symbol from token address (CoW Swap uses addresses directly)."""
        return address
