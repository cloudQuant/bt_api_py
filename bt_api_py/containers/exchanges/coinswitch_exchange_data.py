"""CoinSwitch Exchange Data Configuration."""

import os
from typing import Any

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("coinswitch_exchange_data")
_coinswitch_config = None
_coinswitch_config_loaded = False


def _get_coinswitch_config() -> Any | None:
    """Lazy load and cache CoinSwitch YAML configuration.

    Returns:
        Configuration dictionary or None if not found
    """
    global _coinswitch_config, _coinswitch_config_loaded
    if _coinswitch_config_loaded:
        return _coinswitch_config
    try:
        from bt_api_py.config_loader import load_exchange_config

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "coinswitch.yaml",
        )
        if os.path.exists(config_path):
            _coinswitch_config = load_exchange_config(config_path)
            _coinswitch_config_loaded = True
            return _coinswitch_config
    except Exception as e:
        logger.error(f"Failed to load coinswitch.yaml config: {e}")
        _coinswitch_config_loaded = True
        return None
    return None


class CoinSwitchExchangeData(ExchangeData):
    """CoinSwitch Exchange Data Configuration."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Initialize CoinSwitch exchange data.

        Args:
            config: Optional configuration dictionary
        """
        if config is None:
            config = _get_coinswitch_config()
        if config is None:
            raise ValueError("CoinSwitch configuration not found")
        super().__init__(config or {})
        self.exchange_name = "COINSWITCH"
        self._load_from_config(config)

    def _load_from_config(self, config: dict[str, Any]) -> None:
        """Load configuration from dict.

        Args:
            config: Configuration dictionary
        """
        self.rest_url = config.get("rest_url", "")
        self.wss_url = config.get("wss_url", "")
        self.api_version = config.get("api_version", "")
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


class CoinSwitchExchangeDataSpot(CoinSwitchExchangeData):
    """CoinSwitch Spot Exchange Configuration."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Initialize CoinSwitch spot exchange data.

        Args:
            config: Optional configuration dictionary
        """
        super().__init__(config or {})
        self.asset_type = "SPOT"
