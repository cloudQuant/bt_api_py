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
        ExchangeConfig or None if not found
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


def _config_to_dict(config: Any) -> dict[str, Any]:
    """Extract asset_type.spot config as dict, handling ExchangeConfig (Pydantic)."""
    if hasattr(config, "asset_types") and "spot" in config.asset_types:
        ac = config.asset_types["spot"]
        d: dict[str, Any] = {}
        if getattr(ac, "exchange_name", None):
            d["exchange_name"] = ac.exchange_name
        if getattr(ac, "rest_url", None) is not None:
            d["rest_url"] = ac.rest_url
        if getattr(ac, "wss_url", None) is not None:
            d["wss_url"] = ac.wss_url
        d["rest_paths"] = getattr(ac, "rest_paths", None) or {}
        d["wss_paths"] = getattr(ac, "wss_paths", None) or {}
        d["kline_periods"] = getattr(ac, "kline_periods", None) or (
            getattr(config, "kline_periods", None) or {}
        )
        d["legal_currency"] = getattr(ac, "legal_currency", None) or (
            getattr(config, "legal_currency", None) or []
        )
        d["symbols"] = getattr(ac, "symbols", None) or {}
        return d
    if isinstance(config, dict):
        return config
    return {}


class CoinSwitchExchangeData(ExchangeData):
    """CoinSwitch Exchange Data Configuration."""

    def __init__(self, config: Any = None) -> None:
        """Initialize CoinSwitch exchange data.

        Args:
            config: Optional ExchangeConfig or dict
        """
        if config is None:
            config = _get_coinswitch_config()
        if config is None:
            raise ValueError("CoinSwitch configuration not found")
        super().__init__()
        cfg = _config_to_dict(config)
        self.exchange_name = cfg.get("exchange_name", "COINSWITCH")
        self._load_from_config(cfg)

    def _load_from_config(self, config: dict[str, Any]) -> None:
        """Load configuration from dict."""
        self.rest_url = config.get("rest_url", "")
        self.wss_url = config.get("wss_url", "")
        self.api_version = config.get("api_version", "")
        self.kline_periods = config.get("kline_periods", {})
        self.rest_paths = config.get("rest_paths", {})
        self.wss_paths = config.get("wss_paths", {})
        self.legal_currency = config.get("legal_currency", [])
        self.symbols = config.get("symbols", {})
        self.exchange_info = dict(config.get("exchange_info", {}))

    @staticmethod
    def get_symbol(symbol: str) -> str:
        """Return symbol as-is (CoinSwitch uses pair format like BTCINR)."""
        return symbol

    def get_period(self, key: str) -> str:
        """Map period key to exchange-specific value via kline_periods."""
        return self.kline_periods.get(key, key)

    def get_rest_path(self, key: str, **kwargs: Any) -> str:
        """Get REST path for the given key. Raises ValueError if not found."""
        if key not in self.rest_paths or self.rest_paths[key] == "":
            raise ValueError(f"REST path not found for {key}")
        return self.rest_paths[key]


class CoinSwitchExchangeDataSpot(CoinSwitchExchangeData):
    """CoinSwitch Spot Exchange Configuration."""

    def __init__(self, config: Any = None) -> None:
        """Initialize CoinSwitch spot exchange data."""
        super().__init__(config)
        self.asset_type = "SPOT"
