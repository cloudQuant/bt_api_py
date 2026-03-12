"""BTCTurk Exchange Data Configuration."""

import os
from typing import Any

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("btcturk_exchange_data")
_btcturk_config = None
_btcturk_config_loaded = False


def _get_btcturk_config() -> Any | None:
    """Lazy load and cache BTCTurk YAML configuration.

    Returns:
        Configuration dictionary or None if not found
    """
    global _btcturk_config, _btcturk_config_loaded
    if _btcturk_config_loaded:
        return _btcturk_config
    try:
        from bt_api_py.config_loader import load_exchange_config

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "btcturk.yaml",
        )
        if os.path.exists(config_path):
            _btcturk_config = load_exchange_config(config_path)
            _btcturk_config_loaded = True
            return _btcturk_config
    except Exception as e:
        logger.error(f"Failed to load btcturk.yaml config: {e}")
        _btcturk_config_loaded = True
        return None
    return None


def _config_to_dict(config: Any) -> dict[str, Any]:
    """Extract asset_types.spot config as dict from ExchangeConfig (Pydantic)."""
    if hasattr(config, "asset_types") and config.asset_types and "spot" in config.asset_types:
        ac = config.asset_types["spot"]
        d: dict[str, Any] = {}
        if getattr(ac, "exchange_name", None):
            d["exchange_name"] = ac.exchange_name
        if getattr(ac, "rest_paths", None):
            d["rest_paths"] = dict(ac.rest_paths)
        if getattr(ac, "wss_paths", None):
            d["wss_paths"] = dict(ac.wss_paths)
        d["kline_periods"] = getattr(ac, "kline_periods", None) or (
            getattr(config, "kline_periods", None) or {}
        )
        d["legal_currency"] = getattr(ac, "legal_currency", None) or (
            getattr(config, "legal_currency", None) or []
        )
        d["symbols"] = getattr(ac, "symbols", None) or {}
        if config.base_urls:
            d["rest_url"] = config.base_urls.rest.get("spot") or config.base_urls.rest.get(
                "default", ""
            )
            d["wss_url"] = config.base_urls.wss.get("spot") or config.base_urls.wss.get(
                "default", ""
            )
        return d
    if isinstance(config, dict):
        return config
    return {}


class BTCTurkExchangeData(ExchangeData):
    """BTCTurk Exchange Data Configuration."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Initialize BTCTurk exchange data.

        Args:
            config: Optional configuration (ExchangeConfig or dict)
        """
        if config is None:
            config = _get_btcturk_config()
        if config is None:
            raise ValueError("BTCTurk configuration not found")
        super().__init__()
        cfg = _config_to_dict(config)
        self.exchange_name = cfg.get("exchange_name", "BTCTURK")
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
        if "exchange_info" in config:
            self.exchange_info = dict(config["exchange_info"])
        else:
            self.exchange_info = {}


class BTCTurkExchangeDataSpot(BTCTurkExchangeData):
    """BTCTurk Spot Exchange Configuration."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Initialize BTCTurk spot exchange data.

        Args:
            config: Optional configuration (ExchangeConfig or dict)
        """
        super().__init__(config)
        self.asset_type = "spot"
