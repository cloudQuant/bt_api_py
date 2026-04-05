"""KuCoin Exchange Data Configuration."""

from __future__ import annotations

from typing import Any

from bt_api_py.config_loader import get_exchange_config_path, load_exchange_config
from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("kucoin_exchange_data")
_kucoin_config = None
_kucoin_config_loaded = False


def _get_kucoin_config() -> Any | None:
    """Lazy load and cache KuCoin YAML configuration.

    Returns:
        Configuration dictionary or None if not found
    """
    global _kucoin_config, _kucoin_config_loaded
    if _kucoin_config_loaded:
        return _kucoin_config
    try:
        config_path = get_exchange_config_path("kucoin.yaml")
        if config_path.exists():
            _kucoin_config = load_exchange_config(str(config_path))
            _kucoin_config_loaded = True
            return _kucoin_config
    except (OSError, ValueError, KeyError, ImportError) as e:
        logger.info(f"Failed to load kucoin.yaml config: {e}")
        _kucoin_config_loaded = True
        return None
    return None


class KuCoinExchangeData(ExchangeData):
    """Base class for all KuCoin exchange types."""

    def __init__(self) -> None:
        """Initialize KuCoin exchange data with default configuration."""
        super().__init__()
        self.exchange_name = "KUCOIN"
        self.rest_url = "https://api.kucoin.com"
        self.wss_url = "wss://push.kucoin.com/endpoint"

        self.rest_paths: dict[str, str] = {}
        self.wss_paths: dict[str, Any] = {}
        self.kline_periods: dict[str, str] = {}
        self.reverse_kline_periods: dict[str, str] = {}

        self._load_from_config("spot")

    def _load_from_config(self, asset_type: str) -> bool:
        """Load exchange parameters from YAML configuration file.

        Args:
            asset_type: Asset type key, e.g., 'spot', 'futures', 'margin'

        Returns:
            bool: Whether loading was successful
        """
        config = _get_kucoin_config()
        if config is None:
            return False
        asset_cfg = config.asset_types.get(asset_type)
        if asset_cfg is None:
            return False

        if asset_cfg.exchange_name:
            self.exchange_name = asset_cfg.exchange_name

        if config.base_urls:
            self.rest_url = config.base_urls.rest.get(asset_type, self.rest_url)
            self.wss_url = config.base_urls.wss.get(asset_type, self.wss_url)

        if hasattr(asset_cfg, "rest_paths") and asset_cfg.rest_paths:
            self.rest_paths = dict(asset_cfg.rest_paths)

        if hasattr(asset_cfg, "wss_paths") and asset_cfg.wss_paths:
            self.wss_paths = dict(asset_cfg.wss_paths)

        kp = asset_cfg.kline_periods or (config.kline_periods if config.kline_periods else None)
        if kp:
            self.kline_periods = dict(kp)
            self.reverse_kline_periods = {v: k for k, v in self.kline_periods.items()}

        lc = asset_cfg.legal_currency or (config.legal_currency if config.legal_currency else None)
        if lc:
            self.legal_currency = list(lc)

        return True

    def get_symbol(self, symbol: str) -> str:
        """Format trading pair name for KuCoin (hyphen-separated).

        Args:
            symbol: Raw trading pair name

        Returns:
            str: Formatted trading pair name
        """
        return symbol

    def get_rest_path(self, key: str, **kwargs: Any) -> str:
        """Get REST API endpoint path.

        Args:
            key: Path key

        Returns:
            str: REST API path
        """
        if key not in self.rest_paths or self.rest_paths[key] == "":
            self.raise_path_error(self.exchange_name, key)
        return self.rest_paths[key]

    def get_wss_path(self, **kwargs: Any) -> str:
        """Get WebSocket subscription message.

        Args:
            **kwargs: Subscription parameters including topic, symbol, etc.

        Returns:
            JSON string for WebSocket subscription
        """
        topic = kwargs.get("topic", "")
        if topic not in self.wss_paths or self.wss_paths[topic] == "":
            self.raise_path_error(self.exchange_name, topic)

        wss_config = self.wss_paths[topic]
        if isinstance(wss_config, dict):
            import json

            msg = {
                "id": str(hash(topic)) % 1000000,
                "type": "subscribe",
                "topic": wss_config["topic"],
                "privateChannel": kwargs.get("private_channel", False),
                "response": True,
            }
            if "symbol" in kwargs and "<symbol>" in msg["topic"]:
                msg["topic"] = msg["topic"].replace("<symbol>", kwargs["symbol"])
            return json.dumps(msg)

        return str(wss_config)


class KuCoinExchangeDataSpot(KuCoinExchangeData):
    """KuCoin Spot Exchange Configuration."""

    def __init__(self) -> None:
        """Initialize KuCoin spot exchange data."""
        super().__init__()
        self.asset_type = "SPOT"


class KuCoinExchangeDataFutures(KuCoinExchangeData):
    """KuCoin Futures Exchange Configuration."""

    def __init__(self) -> None:
        """Initialize KuCoin futures exchange data."""
        super().__init__()
        self._load_from_config("futures")
        self.asset_type = "FUTURES"


class KuCoinExchangeDataMargin(KuCoinExchangeData):
    """KuCoin Margin Exchange Configuration."""

    def __init__(self) -> None:
        """Initialize KuCoin margin exchange data."""
        super().__init__()
        self.asset_type = "MARGIN"
