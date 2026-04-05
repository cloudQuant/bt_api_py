"""Bitbns Exchange Data Configuration."""

from __future__ import annotations

import os
from typing import Any

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("bitbns_exchange_data")

_bitbns_config = None
_bitbns_config_loaded = False


def _get_bitbns_config() -> Any | None:
    """Load Bitbns YAML configuration."""
    global _bitbns_config, _bitbns_config_loaded
    if _bitbns_config_loaded:
        return _bitbns_config
    try:
        from bt_api_py.config_loader import load_exchange_config

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "bitbns.yaml",
        )
        if os.path.exists(config_path):
            _bitbns_config = load_exchange_config(config_path)
        _bitbns_config_loaded = True
    except (OSError, ValueError, KeyError, ImportError) as e:
        logger.warning(f"Failed to load bitbns.yaml config: {e}")
    return _bitbns_config


class BitbnsExchangeData(ExchangeData):
    """Base class for Bitbns exchange."""

    def __init__(self) -> None:
        super().__init__()
        self.exchange_name = "bitbns"
        self.rest_url = "https://api.bitbns.com"
        self.rest_public_url = "https://api.bitbns.com/api/trade/v1"
        self.wss_url = "wss://ws.bitbns.com"
        self.api_key = None
        self.api_secret = None
        self.kline_periods = {
            "1m": "1m",
            "5m": "5m",
            "15m": "15m",
            "30m": "30m",
            "1h": "1h",
            "2h": "2h",
            "4h": "4h",
            "6h": "6h",
            "12h": "12h",
            "1d": "1d",
            "1w": "1w",
        }
        self.legal_currency = ["INR", "USDT"]

    def _load_from_config(self, asset_type) -> bool:
        """Load from YAML config."""
        config = _get_bitbns_config()
        if config is None:
            return False
        asset_cfg = config.asset_types.get(asset_type)
        if asset_cfg is None:
            return False

        if asset_cfg.exchange_name:
            self.exchange_name = asset_cfg.exchange_name
        if config.base_urls:
            if config.base_urls.rest:
                rest_urls = config.base_urls.rest
                # Handle both dict and string cases
                if isinstance(rest_urls, dict):
                    self.rest_url = rest_urls.get(asset_type, rest_urls.get("default", ""))
                else:
                    self.rest_url = rest_urls
            if hasattr(config.base_urls, "rest_public") and config.base_urls.rest_public:
                self.rest_public_url = config.base_urls.rest_public
            if config.base_urls.wss:
                wss_urls = config.base_urls.wss
                # Handle both dict and string cases
                if isinstance(wss_urls, dict):
                    self.wss_url = wss_urls.get(asset_type, wss_urls.get("default", ""))
                else:
                    self.wss_url = wss_urls
        if asset_cfg.rest_paths:
            self.rest_paths.update(asset_cfg.rest_paths)
        if asset_cfg.wss_paths:
            self.wss_paths.update(asset_cfg.wss_paths)

        # kline_periods - load from YAML, prefer asset-specific config
        kp = asset_cfg.kline_periods or (config.kline_periods if config.kline_periods else None)
        if kp:
            self.kline_periods = dict(kp)

        # legal_currency - load from YAML
        lc = asset_cfg.legal_currency or (config.legal_currency if config.legal_currency else None)
        if lc:
            self.legal_currency = list(lc)

        return True


class BitbnsExchangeDataSpot(BitbnsExchangeData):
    """Bitbns Spot exchange configuration."""

    def __init__(self) -> None:
        super().__init__()
        self.asset_type = "spot"
        self.rest_paths = {}
        self.wss_paths = {}
        self._load_from_config("spot")
