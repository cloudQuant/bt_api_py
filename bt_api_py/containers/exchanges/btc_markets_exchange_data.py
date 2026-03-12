"""BTC Markets Exchange Data Configuration."""

import os
from typing import Any

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("btc_markets_exchange_data")

_btc_markets_config = None
_btc_markets_config_loaded = False


def _get_btc_markets_config() -> Any | None:
    """Load BTC Markets YAML configuration."""
    global _btc_markets_config, _btc_markets_config_loaded
    if _btc_markets_config_loaded:
        return _btc_markets_config
    try:
        from bt_api_py.config_loader import load_exchange_config

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "btc-markets.yaml",
        )
        if os.path.exists(config_path):
            _btc_markets_config = load_exchange_config(config_path)
        _btc_markets_config_loaded = True
    except Exception as e:
        logger.warning(f"Failed to load btc-markets.yaml config: {e}")
    return _btc_markets_config


class BtcMarketsExchangeData(ExchangeData):
    """Base class for BTC Markets exchange."""

    def __init__(self) -> None:
        super().__init__()
        self.exchange_name = "btc_markets"
        self.rest_url = "https://api.btcmarkets.net"
        self.wss_url = "wss://socket.btcmarkets.net/v3"
        self.kline_periods = {
            "1m": "1m",
            "1h": "1h",
            "1d": "1d",
        }
        self.legal_currency = ["AUD"]

    def _load_from_config(self, asset_type) -> bool:
        """Load from YAML config.

        Args:
            asset_type: Asset type key, such as 'spot'
        Returns:
            bool: Whether loading was successful

        """
        config = _get_btc_markets_config()
        if config is None:
            return False
        asset_cfg = config.asset_types.get(asset_type)
        if asset_cfg is None:
            return False

        # exchange_name
        if asset_cfg.exchange_name:
            self.exchange_name = asset_cfg.exchange_name

        # URLs - extract asset-specific URLs from dictionaries
        if config.base_urls:
            if config.base_urls.rest:
                self.rest_url = config.base_urls.rest.get(asset_type, self.rest_url)
            if config.base_urls.wss:
                self.wss_url = config.base_urls.wss.get(asset_type, self.wss_url)

        # rest_paths
        if asset_cfg.rest_paths:
            self.rest_paths = dict(asset_cfg.rest_paths)

        # wss_paths
        if asset_cfg.wss_paths:
            self.wss_paths = dict(asset_cfg.wss_paths)

        # kline_periods - load from YAML, prefer asset-specific config
        kp = asset_cfg.kline_periods or (config.kline_periods if config.kline_periods else None)
        if kp:
            self.kline_periods = dict(kp)

        # legal_currency - load from YAML
        lc = asset_cfg.legal_currency or (config.legal_currency if config.legal_currency else None)
        if lc:
            self.legal_currency = list(lc)

        return True


class BtcMarketsExchangeDataSpot(BtcMarketsExchangeData):
    """BTC Markets Spot exchange configuration."""

    def __init__(self) -> None:
        super().__init__()
        self.asset_type = "spot"
        self.rest_paths = {}
        self.wss_paths = {}
        self._load_from_config("spot")
