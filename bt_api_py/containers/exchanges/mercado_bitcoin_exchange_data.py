from typing import Any

"""Mercado Bitcoin Exchange Data Configuration."""

import os

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("mercado_bitcoin_exchange_data")

_mercado_bitcoin_config = None
_mercado_bitcoin_config_loaded = False


def _get_mercado_bitcoin_config() -> Any | None:
    """Load Mercado Bitcoin YAML configuration."""
    global _mercado_bitcoin_config, _mercado_bitcoin_config_loaded
    if _mercado_bitcoin_config_loaded:
        return _mercado_bitcoin_config
    try:
        from bt_api_py.config_loader import load_exchange_config

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "mercado_bitcoin.yaml",
        )
        if os.path.exists(config_path):
            _mercado_bitcoin_config = load_exchange_config(config_path)
        _mercado_bitcoin_config_loaded = True
    except Exception as e:
        logger.warn(f"Failed to load mercado_bitcoin.yaml config: {e}")
    return _mercado_bitcoin_config


class MercadoBitcoinExchangeData(ExchangeData):
    """Base class for Mercado Bitcoin exchange."""

    def __init__(self) -> None:
        super().__init__()
        self.exchange_name = "mercado_bitcoin"
        self.rest_url = "https://www.mercadobitcoin.net/api"
        self.rest_private_url = "https://www.mercadobitcoin.net/tapi"
        self.rest_v4_url = "https://api.mercadobitcoin.net/api/v4"
        self.wss_url = "wss://ws.mercadobitcoin.net"
        self.kline_periods = {
            "15m": "15m",
            "1h": "1h",
            "3h": "3h",
            "1d": "1d",
            "1w": "1w",
            "1M": "1M",
        }
        self.legal_currency = ["BRL"]

    def _load_from_config(self, asset_type) -> bool:
        """Load from YAML config."""
        config = _get_mercado_bitcoin_config()
        if config is None:
            return False
        asset_cfg = config.asset_types.get(asset_type)
        if asset_cfg is None:
            return False

        if asset_cfg.exchange_name:
            self.exchange_name = asset_cfg.exchange_name

        # Load base_urls - handle both dict and string formats
        if config.base_urls:
            if config.base_urls.rest:
                rest_url = config.base_urls.rest
                # rest can be a dict (like {'spot': '...', 'default': '...'}) or a string
                if isinstance(rest_url, dict):
                    # Try asset_type first, then default
                    self.rest_url = rest_url.get(
                        asset_type,
                        rest_url.get("default", str(rest_url.get(asset_type, self.rest_url))),
                    )
                else:
                    self.rest_url = str(rest_url)

            if hasattr(config.base_urls, "rest_private") and config.base_urls.rest_private:
                self.rest_private_url = config.base_urls.rest_private

            if hasattr(config.base_urls, "rest_v4") and config.base_urls.rest_v4:
                self.rest_v4_url = config.base_urls.rest_v4

            if config.base_urls.wss:
                wss_url = config.base_urls.wss
                # wss can be a dict or a string
                if isinstance(wss_url, dict):
                    # Try asset_type first, then default
                    self.wss_url = wss_url.get(
                        asset_type,
                        wss_url.get("default", str(wss_url.get(asset_type, self.wss_url))),
                    )
                else:
                    self.wss_url = str(wss_url)

        if asset_cfg.rest_paths:
            self.rest_paths.update(asset_cfg.rest_paths)

        # kline_periods - load from YAML, prefer asset-specific config
        kp = asset_cfg.kline_periods or (config.kline_periods if config.kline_periods else None)
        if kp:
            self.kline_periods = dict(kp)

        # legal_currency - load from YAML
        lc = asset_cfg.legal_currency or (config.legal_currency if config.legal_currency else None)
        if lc:
            self.legal_currency = list(lc)

        return True


class MercadoBitcoinExchangeDataSpot(MercadoBitcoinExchangeData):
    """Mercado Bitcoin Spot exchange configuration."""

    def __init__(self) -> None:
        super().__init__()
        self.asset_type = "spot"
        self.rest_paths = {}
        self.wss_paths = {}
        # Set default exchange_name in case config loading fails
        default_exchange_name = "MERCADO_BITCOIN___SPOT"
        if not self._load_from_config("spot"):
            # If config loading failed, ensure we have the right exchange name
            self.exchange_name = default_exchange_name

    def get_symbol(self, symbol: str) -> str:
        """Get symbol in Mercado Bitcoin format.

        Mercado Bitcoin uses dash format (e.g., BTC-BRL).
        """
        return symbol

    def get_period(self, key, default=None) -> Any:
        """Get kline period.

        Args:
            key: Period key (e.g., '1h', '1d')
            default: Default value if key not found

        """
        return self.kline_periods.get(key, default or key)
