"""
Raydium Exchange Data Configuration
Provides URL configurations, symbol mappings, and REST paths for Raydium DEX API.
"""

import os
from enum import Enum

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("raydium_exchange_data")

# ── 配置加载缓存 ──────────────────────────────────────────────
_raydium_config = None
_raydium_config_loaded = False


def _get_raydium_config():
    """延迟加载并缓存 Raydium YAML 配置"""
    global _raydium_config, _raydium_config_loaded
    if _raydium_config_loaded:
        return _raydium_config
    try:
        from bt_api_py.config_loader import load_exchange_config

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "raydium.yaml",
        )
        if os.path.exists(config_path):
            _raydium_config = load_exchange_config(config_path)
        _raydium_config_loaded = True
    except Exception as e:
        logger.warn(f"Failed to load raydium.yaml config: {e}")
    return _raydium_config


class RaydiumChain(Enum):
    """Supported blockchain networks for Raydium"""
    SOLANA = "SOLANA"


class RaydiumExchangeData(ExchangeData):
    """Base class for Raydium DEX.

    Raydium is a Solana-based DEX that uses AMM pools.
    This class provides REST API access to pool data, prices, and liquidity.
    """

    def __init__(self, chain: RaydiumChain = RaydiumChain.SOLANA):
        super().__init__()
        self.exchange_name = "raydium"
        self.chain = chain
        self.rest_url = "https://api-v3.raydium.io"
        self.rest_paths = {}
        self.wss_paths = {}

        self.kline_periods = {
            "1m": "60",
            "5m": "300",
            "15m": "900",
            "30m": "1800",
            "1h": "3600",
            "4h": "14400",
            "1d": "86400",
            "1w": "604800",
        }
        self.reverse_kline_periods = {v: k for k, v in self.kline_periods.items()}

        self.legal_currency = ["SOL", "USDC", "USDT", "RAY"]

    def _load_from_config(self, asset_type):
        """从 YAML 配置文件加载交易所参数

        Args:
            asset_type: 资产类型 key, 如 'solana'
        Returns:
            bool: 是否加载成功
        """
        config = _get_raydium_config()
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
            self.rest_url = config.base_urls.rest.get("default", self.rest_url)

        # rest_paths
        if asset_cfg.rest_paths:
            self.rest_paths.update(dict(asset_cfg.rest_paths))

        # kline_periods
        kp = asset_cfg.kline_periods or (config.kline_periods if config.kline_periods else None)
        if kp:
            self.kline_periods = dict(kp)
            self.reverse_kline_periods = {v: k for k, v in self.kline_periods.items()}

        # legal_currency
        lc = asset_cfg.legal_currency or (config.legal_currency if config.legal_currency else None)
        if lc:
            self.legal_currency = list(lc)

        return True

    def get_chain_value(self) -> str:
        """Get the chain value for API queries."""
        return self.chain.value

    def get_symbol(self, symbol: str) -> str:
        """将交易对名称转换为 Raydium 格式.

        Raydium pools are identified by token pair (e.g., SOL/USDC)

        Args:
            symbol: 交易对名称 (e.g., 'SOL/USDC', 'SOL-USDC')
        Returns:
            str: 标准化的交易对名称
        """
        # Standardize format
        return symbol.replace("-", "/").upper()

    def get_period(self, period):
        """将周期转换为秒数.

        Args:
            period: 周期名称 (e.g., '1m', '5m', '1h', '1d')
        Returns:
            str: 周期（秒数）
        """
        return self.kline_periods.get(period, period)

    def get_rest_path(self, request_type):
        """获取 REST API 路径.

        Args:
            request_type: 请求类型
        Returns:
            str: REST API 路径
        """
        if request_type not in self.rest_paths or self.rest_paths[request_type] == "":
            self.raise_path_error(self.exchange_name, request_type)
        return self.rest_paths[request_type]

    def get_wss_path(self, **kwargs):
        """Raydium doesn't support WebSocket for public data.

        This method is provided for compatibility but raises NotImplementedError.
        """
        raise NotImplementedError("Raydium public API doesn't support WebSocket. Use REST endpoints.")


class RaydiumExchangeDataSpot(RaydiumExchangeData):
    """Raydium DEX (Solana) Configuration"""

    def __init__(self):
        super().__init__(chain=RaydiumChain.SOLANA)
        self.asset_type = "DEX"
        self._load_from_config("solana")

        # Define REST paths directly (Raydium API v3)
        # These are the main API endpoints for pool and market data
        if not self.rest_paths:
            self.rest_paths = {
                # Main API
                "get_chain_time": "/main/chain-time",
                "get_info": "/main/info",
                "get_clmm_config": "/main/clmm-config",
                "get_cpmm_config": "/main/cpmm-config",
                # Pool endpoints
                "get_pool_ids": "/pools/info/ids",
                "get_pools": "/pools/info/list",
                "get_pools_v2": "/pools/info/list-v2",
                "get_pool_by_lp": "/pools/info/lps",
                "get_pool_by_mint": "/pools/info/mint",
                "get_pool_keys": "/pools/key/ids",
                "get_pool_liquidity": "/pools/line/liquidity",
                # Farm endpoints
                "get_farm_ids": "/farms/info/ids",
                "get_farm_by_lp": "/farms/info/lp",
                "get_farm_keys": "/farms/key/ids",
                # Mint info
                "get_mint_ids": "/mint/ids",
                "get_mint_list": "/mint/list",
                "get_mint_price": "/mint/price",
                # IDO
                "get_ido_keys": "/ido/key/ids",
            }

    def get_pool_id(self, base_token: str, quote_token: str) -> str:
        """Get pool ID for a token pair.

        For Raydium, pools are identified by their pool address.
        This method would need to query the API to get the pool ID.

        Args:
            base_token: Base token address or symbol
            quote_token: Quote token address or symbol

        Returns:
            str: Pool ID
        """
        # This would need to query the API
        # For now, return a placeholder
        return f"{base_token}-{quote_token}"
