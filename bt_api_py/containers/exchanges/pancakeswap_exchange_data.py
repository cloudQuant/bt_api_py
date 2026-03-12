import os
from typing import Any

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.logging_factory import get_logger

logger = get_logger("pancakeswap_exchange_data")

# ── 配置加载缓存 ─────────────────────────────────────────────────
_pancakeswap_config = None
_pancakeswap_config_loaded = False


def _get_pancakeswap_config() -> Any | None:
    """延迟加载并缓存 PancakeSwap YAML 配置."""
    global _pancakeswap_config, _pancakeswap_config_loaded
    if _pancakeswap_config_loaded:
        return _pancakeswap_config
    try:
        from bt_api_py.config_loader import load_exchange_config

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "pancakeswap.yaml",
        )
        if os.path.exists(config_path):
            _pancakeswap_config = load_exchange_config(config_path)
        _pancakeswap_config_loaded = True
    except Exception as e:
        logger.warning(f"Failed to load pancakeswap.yaml config: {e}")
    return _pancakeswap_config


class PancakeSwapExchangeData(ExchangeData):
    """Base class for all PancakeSwap exchange types.

    Provides shared utility methods (get_symbol, get_period, get_rest_path,
    get_wss_path, account_wss_symbol) and default kline_periods.
    Subclasses MUST set exchange-specific: exchange_name, rest_url,
    acct_wss_url, wss_url, rest_paths, wss_paths, legal_currency.
    """

    def __init__(self) -> None:
        super().__init__()
        self.exchange_name = "pancakeswap"
        self.rest_url = ""
        self.acct_wss_url = ""
        self.wss_url = ""
        self.rest_paths = {}
        self.wss_paths = {}

        self.kline_periods = {
            "1s": "1s",
            "1m": "1m",
            "3m": "3m",
            "5m": "5m",
            "15m": "15m",
            "30m": "30m",
            "1h": "1h",
            "2h": "2h",
            "4h": "4h",
            "6h": "6h",
            "8h": "8h",
            "12h": "12h",
            "1d": "1d",
            "3d": "3d",
            "1w": "1w",
            "1M": "1M",
        }
        self.reverse_kline_periods = {v: k for k, v in self.kline_periods.items()}

        self.legal_currency = [
            "USDT",
            "USD",
            "BNB",
            "BTC",
            "ETH",
        ]

    def _load_from_config(self, asset_type) -> bool:
        """从 YAML 配置文件加载交易所参数.

        Args:
            asset_type: 资产类型 key, 如 'spot', 'swap' 等
        Returns:
            bool: 是否加载成功

        """
        config = _get_pancakeswap_config()
        if config is None:
            return False
        asset_cfg = config.asset_types.get(asset_type)
        if asset_cfg is None:
            return False

        # exchange_name
        if hasattr(asset_cfg, "exchange_name") and asset_cfg.exchange_name:
            self.exchange_name = asset_cfg.exchange_name

        # rest_url - check for rest attribute and graphql_url
        if hasattr(asset_cfg, "rest") and asset_cfg.rest:
            if hasattr(asset_cfg.rest, "graphql_url") and asset_cfg.rest.graphql_url:
                self.rest_url = asset_cfg.rest.graphql_url
            elif hasattr(asset_cfg, "rest_url") and asset_cfg.rest_url:
                self.rest_url = asset_cfg.rest_url

        # acct_wss_url
        if (
            hasattr(asset_cfg, "websocket")
            and asset_cfg.websocket
            and hasattr(asset_cfg.websocket, "private_url")
            and asset_cfg.websocket.private_url
        ):
            self.acct_wss_url = asset_cfg.websocket.private_url

        # wss_url
        if (
            hasattr(asset_cfg, "websocket")
            and asset_cfg.websocket
            and hasattr(asset_cfg.websocket, "public_url")
            and asset_cfg.websocket.public_url
        ):
            self.wss_url = asset_cfg.websocket.public_url

        # rest_paths
        if not self.rest_paths:
            self.rest_paths = {
                "graphql": "",
                "pool_info": "",
                "token_info": "",
                "server_time": "",
            }

        # wss_paths
        if not self.wss_paths:
            self.wss_paths = {
                "ticker": "",
                "depth": "",
                "trades": "",
                "orders": "",
                "account": "",
            }

        # legal_currency 从配置中提取稳定币
        if hasattr(asset_cfg, "tokens") and asset_cfg.tokens:
            stablecoins = asset_cfg.tokens.get("stablecoins", [])
            self.legal_currency = [token["symbol"] for token in stablecoins]

        # kline_periods - load from YAML, prefer asset-specific config
        kp = asset_cfg.kline_periods or (config.kline_periods if config.kline_periods else None)
        if kp:
            self.kline_periods = dict(kp)

        # legal_currency - load from YAML
        lc = asset_cfg.legal_currency or (config.legal_currency if config.legal_currency else None)
        if lc:
            self.legal_currency = list(lc)

        return True

    def get_network_config(self, network_name: str = None):
        """获取网络配置.

        Args:
            network_name: 网络名称，如 'bsc', 'bsc_testnet'

        Returns:
            dict: 网络配置信息

        """
        config = _get_pancakeswap_config()
        if config is None:
            return None

        if network_name is None:
            return config.networks.get("bsc")  # 默认返回主网

        return config.networks.get(network_name)

    def get_supported_tokens(self) -> Any:
        """获取支持的代币列表."""
        config = _get_pancakeswap_config()
        if config is None:
            return []

        asset_cfg = config.asset_types.get("spot")
        if not asset_cfg:
            return []

        tokens = []

        # Check if tokens attribute exists
        if not hasattr(asset_cfg, "tokens"):
            return tokens

        # 添加原生代币
        tokens_dict = asset_cfg.tokens if hasattr(asset_cfg, "tokens") else {}
        native = tokens_dict.get("native", {})
        if native:
            tokens.append(
                {
                    "symbol": native.get("symbol", ""),
                    "name": native.get("name", ""),
                    "address": native.get("address", ""),
                    "decimals": native.get("decimals", 18),
                    "type": "native",
                }
            )

        # 添加治理代币
        governance = tokens_dict.get("governance", {})
        if governance:
            tokens.append(
                {
                    "symbol": governance.get("symbol", ""),
                    "name": governance.get("name", ""),
                    "address": governance.get("address", ""),
                    "decimals": governance.get("decimals", 18),
                    "type": "governance",
                }
            )

        # 添加稳定币
        stablecoins = tokens_dict.get("stablecoins", [])
        tokens.extend(
            {
                "symbol": s.get("symbol", ""),
                "name": s.get("name", ""),
                "address": s.get("address", ""),
                "decimals": s.get("decimals", 18),
                "type": "stablecoin",
            }
            for s in stablecoins
        )

        return tokens

    def get_supported_pairs(self) -> Any:
        """获取支持交易对列表."""
        config = _get_pancakeswap_config()
        if config is None:
            return []

        asset_cfg = config.asset_types.get("spot")
        if not asset_cfg:
            return []

        pairs = []

        # Check if pairs attribute exists
        if not hasattr(asset_cfg, "pairs"):
            return pairs

        # 合并所有交易对分类
        pairs_dict = asset_cfg.pairs if hasattr(asset_cfg, "pairs") else {}
        for category in ["major", "popular", "emerging"]:
            category_pairs = pairs_dict.get(category, [])
            pairs.extend(
                {"symbol": p, "category": category, "enabled": True} for p in category_pairs
            )

        return pairs

    def get_fee_config(self, asset_type="spot"):
        """获取费率配置.

        Args:
            asset_type: 资产类型

        Returns:
            dict: 费率配置

        """
        config = _get_pancakeswap_config()
        if config is None:
            return None

        asset_cfg = config.asset_types.get(asset_type)
        if not asset_cfg:
            return None

        # Check if fees attribute exists
        if hasattr(asset_cfg, "fees"):
            return asset_cfg.fees

        # Return default fees for DEX
        return {"maker": 0.0025, "taker": 0.0025}

    def get_slippage_config(self) -> Any:
        """获取滑点配置."""
        config = _get_pancakeswap_config()
        if config is None:
            return None

        asset_cfg = config.asset_types.get("spot")
        if not asset_cfg:
            return None

        return asset_cfg.slippage

    def get_gas_config(self) -> Any:
        """获取Gas配置."""
        config = _get_pancakeswap_config()
        if config is None:
            return None

        return config.networks.bsc.gas

    def get_order_types(self, asset_type="spot"):
        """获取支持的订单类型."""
        config = _get_pancakeswap_config()
        if config is None:
            return []

        asset_cfg = config.asset_types.get(asset_type)
        if not asset_cfg:
            return []

        # Check if order_types attribute exists
        if hasattr(asset_cfg, "order_types"):
            return asset_cfg.order_types

        # Return default order types for DEX
        return ["MARKET", "LIMIT"]

    def get_order_statuses(self, asset_type="spot"):
        """获取订单状态列表."""
        config = _get_pancakeswap_config()
        if config is None:
            return []

        asset_cfg = config.asset_types.get(asset_type)
        if not asset_cfg:
            return []

        # Check if order_statuses attribute exists
        if hasattr(asset_cfg, "order_statuses"):
            return asset_cfg.order_statuses

        # Return default order statuses for DEX
        return ["NEW", "FILLED", "PARTIALLY_FILLED", "FAILED"]

    def get_capabilities(self, asset_type="spot"):
        """获取支持的功能列表."""
        config = _get_pancakeswap_config()
        if config is None:
            return []

        asset_cfg = config.asset_types.get(asset_type)
        if not asset_cfg:
            return []

        # Check if capabilities attribute exists
        if hasattr(asset_cfg, "capabilities"):
            return asset_cfg.capabilities

        # Return default capabilities for DEX
        return ["GET_TICK", "GET_DEPTH", "GET_KLINE", "GET_EXCHANGE_INFO", "MAKE_ORDER"]

    def get_minimum_trade_amount(self, symbol):
        """获取指定代币的最小交易额.

        Args:
            symbol: 代币符号，如 'USDT', 'BNB'

        Returns:
            float: 最小交易额

        """
        config = _get_pancakeswap_config()
        if config is None:
            return 0.0

        # Check if security attribute exists
        if hasattr(config, "security") and config.security:
            security = config.security
            if hasattr(security, "minimum_trade_amounts"):
                return security.minimum_trade_amounts.get(symbol, 0.0)

        # Return default minimum trade amounts
        default_amounts = {
            "USDT": 10.0,
            "BNB": 0.01,
            "BTC": 0.0001,
            "ETH": 0.001,
        }
        return default_amounts.get(symbol, 0.0)

    def get_maximum_trade_amount(self, symbol):
        """获取指定代币的最大交易额.

        Args:
            symbol: 代币符号，如 'USDT', 'BNB'

        Returns:
            float: 最大交易额

        """
        config = _get_pancakeswap_config()
        if config is None:
            return 0.0

        security = config.security
        return security.maximum_trade_amounts.get(symbol, 0.0)

    def get_special_features(self, asset_type="spot"):
        """获取特殊功能列表.

        Args:
            asset_type: 资产类型

        Returns:
            list: 特殊功能列表

        """
        config = _get_pancakeswap_config()
        if config is None:
            return []

        asset_cfg = config.asset_types.get(asset_type)
        if not asset_cfg:
            return []

        return asset_cfg.special_features

    def get_rate_limits(self, asset_type="spot"):
        """获取限流配置.

        Args:
            asset_type: 资产类型

        Returns:
            dict: 限流配置

        """
        config = _get_pancakeswap_config()
        if config is None:
            return {}

        asset_cfg = config.asset_types.get(asset_type)
        if not asset_cfg:
            return {}

        return asset_cfg.rate_limits

    def symbol_to_address(self, symbol):
        """将交易对符号转换为池子地址.

        Args:
            symbol: 交易对符号，如 'BTCB/USDT'

        Returns:
            str: 池子合约地址

        """
        # 这里应该有一个映射表，从符号到地址
        # 实际实现中可能需要查询配置或调用API
        symbol_mapping = {
            "BTCB/USDT": "0x58F876857a02D6762EeFA1aF755FEE1271A3ACaC",
            "ETH/USDT": "0x70e36197034F56Bf06712e97d19AEd5C0b8453D1",
            "CAKE/USDT": "0x04514E7Ba3F091234D6Be8E39864a7a3Ad4a1E1e",
            "BNB/USDT": "0x16b9a82891334a839e0545d6365b4161d9b905dc",
        }

        return symbol_mapping.get(symbol, "0x0")

    def address_to_symbol(self, address):
        """将池子地址转换为交易对符号.

        Args:
            address: 池子合约地址

        Returns:
            str: 交易对符号

        """
        # 反向映射
        symbol_mapping = {
            "0x58F876857a02D6762EeFA1aF755FEE1271A3ACaC": "BTCB/USDT",
            "0x70e36197034F56Bf06712e97d19AEd5C0b8453D1": "ETH/USDT",
            "0x04514E7Ba3F091234D6Be8E39864a7a3Ad4a1E1e": "CAKE/USDT",
            "0x16b9a82891334a839e0545d6365b4161d9b905dc": "BNB/USDT",
        }

        return symbol_mapping.get(address, "UNKNOWN")

    def get_backup_urls(self, endpoint_type="graphql"):
        """获取备用URL列表.

        Args:
            endpoint_type: 端点类型，如 'graphql'

        Returns:
            list: 备用URL列表

        """
        config = _get_pancakeswap_config()
        if config is None:
            return []

        asset_cfg = config.asset_types.get("spot")
        if not asset_cfg or not asset_cfg.rest:
            return []

        return asset_cfg.rest.get("backup_urls", [])

    def is_supported_network(self, chain_id) -> bool:
        """检查是否支持指定网络.

        Args:
            chain_id: 链ID

        Returns:
            bool: 是否支持

        """
        config = _get_pancakeswap_config()
        if config is None:
            return False

        for network_config in config.networks.values():
            if network_config.chain_id == chain_id:
                return True

        return False

    def get_symbol(self, symbol: str) -> str:
        """将交易符号转换为交易所API所需的格式.

        Args:
            symbol: 标准交易对符号，如 'BTCB/USDT'

        Returns:
            str: 交易所API所需的符号格式

        """
        # For PancakeSwap, symbol format is typically the same or needs address conversion
        # Keep the symbol format as-is for REST API calls
        return symbol

    def get_rest_path(self, request_type: str, **kwargs) -> str:
        """根据请求类型获取REST API路径.

        Args:
            request_type: 请求类型，如 'get_tick', 'get_depth' 等

        Returns:
            str: REST API路径

        """
        # Load config to get rest paths
        if not self.rest_paths:
            self._load_from_config("spot")

        return self.rest_paths.get(request_type, "")

    def get_wss_path(self, channel: Any, **kwargs) -> str:
        """根据频道获取WebSocket路径.

        Args:
            channel: WebSocket频道名称

        Returns:
            str: WebSocket路径

        """
        # Load config to get wss paths
        if not self.wss_paths:
            self._load_from_config("spot")

        return self.wss_paths.get(channel, "")

    def get_period(self, period: str) -> str:
        """转换K线周期格式.

        Args:
            period: 标准周期格式，如 '1m', '1h', '1d'

        Returns:
            str: 交易所API所需的周期格式

        """
        return self.kline_periods.get(period, period)
