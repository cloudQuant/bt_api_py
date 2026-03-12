"""HTX Exchange Data Configuration."""

import json
import os
from typing import Any

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("htx_exchange_data")

# ── 配置加载缓存 ──────────────────────────────────────────────
_htx_config = None
_htx_config_loaded = False


def _get_htx_config() -> Any | None:
    """延迟加载并缓存 HTX YAML 配置."""
    global _htx_config, _htx_config_loaded
    if _htx_config_loaded:
        return _htx_config
    try:
        from bt_api_py.config_loader import load_exchange_config

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "htx.yaml",
        )
        if os.path.exists(config_path):
            _htx_config = load_exchange_config(config_path)
        _htx_config_loaded = True
    except Exception as e:
        logger.warning(f"Failed to load htx.yaml config: {e}")
    return _htx_config


class HtxExchangeData(ExchangeData):
    """Base class for all HTX exchange types.

    HTX (formerly Huobi) API documentation:
    - REST API: https://api.huobi.pro or https://api.htx.com
    - WebSocket: wss://api.huobi.pro/ws
    """

    def __init__(self) -> None:
        super().__init__()
        self.exchange_name = "htx"
        self.rest_url = ""
        self.acct_wss_url = ""
        self.wss_url = ""
        self.rest_paths = {}
        self.wss_paths = {}

        self.kline_periods = {
            "1m": "1min",
            "5m": "5min",
            "15m": "15min",
            "30m": "30min",
            "60m": "60min",
            "4h": "4hour",
            "1d": "1day",
            "1w": "1week",
            "1M": "1mon",
        }
        self.reverse_kline_periods = {v: k for k, v in self.kline_periods.items()}

        # Order status mapping
        self.status_dict = {
            "submitted": "submitted",
            "partial-filled": "partial_filled",
            "filled": "filled",
            "canceled": "canceled",
            "partial-canceled": "partial_canceled",
            "rejected": "rejected",
        }

        # legal_currency
        self.legal_currency = [
            "USDT",
            "USD",
            "BTC",
            "ETH",
            "USDC",
            "HTX",
        ]

    def _load_from_config(self, asset_type) -> bool:
        """从 YAML 配置文件加载交易所参数.

        Args:
            asset_type: 资产类型 key, 如 'swap', 'spot', 'futures' 等
        Returns:
            bool: 是否加载成功

        """
        config = _get_htx_config()
        if config is None:
            return False
        asset_cfg = config.asset_types.get(asset_type)
        if asset_cfg is None:
            return False

        # exchange_name
        if asset_cfg.exchange_name:
            self.exchange_name = asset_cfg.exchange_name

        # URLs
        if config.base_urls:
            self.rest_url = config.base_urls.rest.get(asset_type, self.rest_url)
            self.wss_url = config.base_urls.wss.get(asset_type, self.wss_url)
            self.acct_wss_url = config.base_urls.acct_wss.get(asset_type, self.acct_wss_url)

        # rest_paths (直接使用, 格式一致)
        if asset_cfg.rest_paths:
            self.rest_paths = dict(asset_cfg.rest_paths)

        # wss_paths: YAML 模板字符串 → {'params': [template], 'method': 'SUBSCRIBE', 'id': 1}
        if asset_cfg.wss_paths:
            converted = {}
            for key, value in asset_cfg.wss_paths.items():
                if isinstance(value, str):
                    if value:
                        converted[key] = {"params": [value], "method": "SUBSCRIBE", "id": 1}
                    else:
                        converted[key] = ""
                else:
                    converted[key] = value
            self.wss_paths = converted

        # kline_periods (asset-level 优先, 否则用 exchange-level)
        kp = asset_cfg.kline_periods or (config.kline_periods if config.kline_periods else None)
        if kp:
            self.kline_periods = dict(kp)
            self.reverse_kline_periods = {v: k for k, v in self.kline_periods.items()}

        # legal_currency (asset-level 优先, 否则用 exchange-level)
        lc = asset_cfg.legal_currency or (config.legal_currency if config.legal_currency else None)
        if lc:
            self.legal_currency = list(lc)

        return True

    def get_rest_path(self, path_name: str, **kwargs) -> str:
        """Get REST API path by name.

        Args:
            path_name: Path name key

        Returns:
            str: REST API path

        """
        return self.rest_paths.get(path_name, "")

    def get_wss_path(self, **kwargs) -> str:
        """Build HTX WebSocket subscription message.

        HTX market WSS v1 format: {"sub": "market.btcusdt.depth.step0", "id": "id1"}
        HTX account WSS v2 format: {"action": "sub", "ch": "orders#btcusdt"}

        Args:
            kwargs: topic, symbol, period, type, etc.

        Returns:
            str: JSON subscription message

        """
        topic = kwargs.get("topic", "")
        if topic not in self.wss_paths:
            return json.dumps({})

        template = self.wss_paths.get(topic, "")
        # Handle both old string format and new dict format from YAML
        if isinstance(template, dict):
            # YAML converted format: {"params": ["template"], "method": "SUBSCRIBE", "id": 1}
            template = template.get("params", [""])[0]
        if not template:
            return json.dumps({})

        # Replace placeholders
        if "symbol" in kwargs:
            template = template.replace("{symbol}", self.get_symbol(kwargs["symbol"]))
        if "period" in kwargs:
            template = template.replace("{period}", self.get_period(kwargs["period"]))
        if "type" in kwargs:
            template = template.replace("{type}", kwargs["type"])

        # Build HTX market WSS subscription message
        channel = f"market.{template}"
        sub_id = f"{topic}_{kwargs.get('symbol', 'all')}"
        return json.dumps({"sub": channel, "id": sub_id})

    def get_symbol(self, symbol: str) -> str:
        """Convert standard symbol to HTX symbol format.

        HTX uses lowercase format (e.g., 'btcusdt').

        Args:
            symbol: Standard symbol (e.g., 'BTCUSDT', 'BTC/USDT')

        Returns:
            str: HTX symbol format

        """
        if not symbol:
            return symbol

        # Remove separator if present
        symbol = symbol.replace("/", "").replace("-", "")

        # Convert to lowercase
        return symbol.lower()

    def get_period(self, period: str) -> str:
        """Convert standard period to HTX period format.

        Args:
            period: Standard period (e.g., '1m', '1h', '1d')

        Returns:
            str: HTX period format

        """
        return self.kline_periods.get(period, period)

    def get_standard_period(self, period: str) -> str:
        """Convert HTX period to standard period format.

        Args:
            period: HTX period (e.g., '1min', '60min', '1day')

        Returns:
            str: Standard period format

        """
        return self.reverse_kline_periods.get(period, period)


class HtxExchangeDataSpot(HtxExchangeData):
    """HTX Spot trading configuration."""

    def __init__(self) -> None:
        super().__init__()
        self.exchange_name = "htx_spot"
        self.asset_type = "SPOT"

        # Load configuration from YAML
        self._load_from_config("spot")


class HtxExchangeDataMargin(HtxExchangeData):
    """HTX Margin trading configuration."""

    def __init__(self) -> None:
        super().__init__()
        self.exchange_name = "htx_margin"
        self.asset_type = "MARGIN"

        # Load configuration from YAML
        self._load_from_config("margin")


class HtxExchangeDataUsdtSwap(HtxExchangeData):
    """HTX USDT-M Linear Swap trading configuration.

    Uses api.hbdm.com with /linear-swap-api/ and /linear-swap-ex/ prefixes.
    Symbol format: BTC-USDT (uppercase, dash-separated).
    """

    def __init__(self) -> None:
        super().__init__()
        self.exchange_name = "htx_usdt_swap"
        self.asset_type = "USDT_SWAP"

        # Load configuration from YAML
        self._load_from_config("usdt_swap")

    def get_symbol(self, symbol: str) -> str:
        """Convert standard symbol to HTX derivatives format (e.g., BTC-USDT)."""
        if not symbol:
            return symbol
        symbol = symbol.replace("/", "-")
        if "-" not in symbol:
            # Try to insert dash before common quote currencies
            for quote in self.legal_currency:
                if symbol.upper().endswith(quote):
                    base = symbol[: len(symbol) - len(quote)]
                    return f"{base.upper()}-{quote.upper()}"
        return symbol.upper()


class HtxExchangeDataCoinSwap(HtxExchangeData):
    """HTX Coin-M Inverse Swap trading configuration.

    Uses api.hbdm.com with /swap-api/ and /swap-ex/ prefixes.
    Symbol format: BTC-USD (uppercase, dash-separated).
    """

    def __init__(self) -> None:
        super().__init__()
        self.exchange_name = "htx_coin_swap"
        self.asset_type = "COIN_SWAP"

        # Load configuration from YAML
        self._load_from_config("coin_swap")

    def get_symbol(self, symbol: str) -> str:
        """Convert standard symbol to HTX coin swap format (e.g., BTC-USD)."""
        if not symbol:
            return symbol
        symbol = symbol.replace("/", "-")
        if "-" not in symbol:
            for quote in self.legal_currency:
                if symbol.upper().endswith(quote):
                    base = symbol[: len(symbol) - len(quote)]
                    return f"{base.upper()}-{quote.upper()}"
        return symbol.upper()


class HtxExchangeDataOption(HtxExchangeData):
    """HTX Option trading configuration.

    Uses api.hbdm.com with /option-api/ and /option-ex/ prefixes.
    Symbol format: BTC-USDT (uppercase, dash-separated).
    """

    def __init__(self) -> None:
        super().__init__()
        self.exchange_name = "htx_option"
        self.asset_type = "OPTION"

        # Load configuration from YAML
        self._load_from_config("option")

    def get_symbol(self, symbol: str) -> str:
        """Convert standard symbol to HTX option format (e.g., BTC-USDT)."""
        if not symbol:
            return symbol
        symbol = symbol.replace("/", "-")
        if "-" not in symbol:
            for quote in self.legal_currency:
                if symbol.upper().endswith(quote):
                    base = symbol[: len(symbol) - len(quote)]
                    return f"{base.upper()}-{quote.upper()}"
        return symbol.upper()
