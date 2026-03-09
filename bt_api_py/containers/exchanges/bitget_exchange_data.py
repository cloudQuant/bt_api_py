"""Bitget Exchange Data Configuration."""

import os
from typing import Any

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("bitget_exchange_data")

_bitget_config = None
_bitget_config_loaded = False


def _get_bitget_config() -> Any | None:
    """Lazy load and cache Bitget YAML configuration.

    Returns:
        Configuration dictionary or None if not found
    """
    global _bitget_config, _bitget_config_loaded
    if _bitget_config_loaded:
        return _bitget_config
    try:
        from bt_api_py.config_loader import load_exchange_config

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "bitget.yaml",
        )
        if os.path.exists(config_path):
            _bitget_config = load_exchange_config(config_path)
        _bitget_config_loaded = True
        return _bitget_config
    except Exception as e:
        logger.error(f"Failed to load bitget.yaml config: {e}")
        _bitget_config_loaded = True
        return None
    return None


class BitgetExchangeData(ExchangeData):
    """Bitget Exchange Data Configuration."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Initialize Bitget exchange data.

        Args:
            config: Optional configuration dictionary to If None, loads from YAML
        """
        if config is None:
            config = _get_bitget_config()
        if config is None:
            raise ValueError("Bitget configuration not found")

        super().__init__(config or {})
        self.exchange_name = "BITGET"
        self._load_from_config(config)

    def _load_from_config(self, config: dict[str, Any]) -> None:
        """Load configuration from dict.

        Args:
            config: Configuration dictionary
        """
        self.rest_url = config.get("rest_url", "")
        self.wss_url = config.get("wss_url", "")
        self.api_version = config.get("api_version", "v2")
        self.kline_periods = config.get("kline_periods", {})
        self.rest_paths = config.get("rest_paths", {})
        self.wss_paths = config.get("wss_paths", {})
        self.legal_currency = config.get("legal_currency", [])
        self.symbols = config.get("symbols", {})
        self._parse_exchange_info()

    def _parse_exchange_info(self) -> None:
        """Parse exchange information from config."""
        if "exchange_info" in self.config:
            for key, value in self.config["exchange_info"].items():
                self.exchange_info[key] = value

        else:
            self.exchange_info = {}


class BitgetExchangeDataSpot(BitgetExchangeData):
    """Bitget Spot Exchange Configuration."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Initialize Bitget spot exchange data.

        Args:
            config: Optional configuration dictionary
        """
        super().__init__(config or {})
        self.asset_type = "SPOT"


class BitgetExchangeDataSwap(BitgetExchangeData):
    """Bitget Swap Exchange Configuration."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Initialize Bitget swap exchange data.

        Args:
            config: Optional configuration dictionary
        """
        super().__init__(config or {})
        self.asset_type = "SWAP"
