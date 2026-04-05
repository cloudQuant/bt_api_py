"""bitFlyer Exchange Data Configuration."""

from __future__ import annotations

from typing import Any

from bt_api_py.config_loader import get_exchange_config_path, load_exchange_config
from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("bitflyer_exchange_data")

_bitflyer_config = None
_bitflyer_config_loaded = False


def _get_bitflyer_config() -> Any | None:
    """Load bitFlyer YAML configuration."""
    global _bitflyer_config, _bitflyer_config_loaded
    if _bitflyer_config_loaded:
        return _bitflyer_config
    try:
        config_path = get_exchange_config_path("bitflyer.yaml")
        if config_path.exists():
            _bitflyer_config = load_exchange_config(str(config_path))
        _bitflyer_config_loaded = True
    except (OSError, ValueError, KeyError, ImportError) as e:
        logger.warning(f"Failed to load bitflyer.yaml config: {e}")
    return _bitflyer_config


class BitflyerExchangeData(ExchangeData):
    """Base class for bitFlyer exchange."""

    def __init__(self) -> None:
        super().__init__()
        self.exchange_name = "bitflyer"
        self.rest_url = "https://api.bitflyer.com"
        self.wss_url = "wss://ws.lightstream.bitflyer.com/json-rpc"
        self.kline_periods = {
            "1m": "1m",
            "5m": "5m",
            "15m": "15m",
            "30m": "30m",
            "1h": "1h",
            "4h": "4h",
            "1d": "1d",
            "1w": "1w",
        }
        self.legal_currency = ["JPY", "USD", "EUR", "BTC"]

    def _load_from_config(self, asset_type) -> bool:
        """Load from YAML config."""
        config = _get_bitflyer_config()
        if config is None:
            return False
        asset_cfg = config.asset_types.get(asset_type)
        if asset_cfg is None:
            return False

        if asset_cfg.exchange_name:
            self.exchange_name = asset_cfg.exchange_name
        if config.base_urls:
            # Handle both dict and string formats for URLs
            rest_urls = config.base_urls.rest
            wss_urls = config.base_urls.wss
            if isinstance(rest_urls, dict):
                # Try to get asset_type specific URL, then 'default', then first value
                self.rest_url = rest_urls.get(
                    asset_type, rest_urls.get("default", list(rest_urls.values())[0])
                )
            else:
                self.rest_url = rest_urls
            if isinstance(wss_urls, dict):
                # Try to get asset_type specific URL, then 'default', then first value
                self.wss_url = wss_urls.get(
                    asset_type, wss_urls.get("default", list(wss_urls.values())[0])
                )
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


class BitflyerExchangeDataSpot(BitflyerExchangeData):
    """bitFlyer Spot exchange configuration."""

    def __init__(self) -> None:
        super().__init__()
        self.asset_type = "spot"
        self.rest_paths = {}
        self.wss_paths = {}
        self.api_key = None
        self.api_secret = None
        self._load_from_config("spot")
        # Set exchange_name after loading config to override YAML value
        self.exchange_name = "BITFLYER"
