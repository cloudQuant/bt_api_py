"""
配置系统 — 基于 pydantic 的 Schema 校验

支持从 YAML 文件加载交易所/场所配置，自动校验字段合法性。
"""

import os
from enum import Enum, unique
from typing import Any, Dict, List, Optional

try:
    from pydantic import BaseModel, Field, field_validator
except ImportError:
    raise ImportError("pydantic is required for config_loader. Install with: pip install pydantic")

try:
    import yaml
except ImportError:
    yaml = None


# ── 枚举定义 ──────────────────────────────────────────────────


@unique
class VenueType(str, Enum):
    CEX = "cex"
    DEX = "dex"
    BROKER = "broker"


@unique
class AuthType(str, Enum):
    NONE = "none"
    API_KEY = "api_key"
    HMAC_SHA256 = "hmac_sha256"
    HMAC_SHA512 = "hmac_sha512"
    OAUTH = "oauth"
    CERTIFICATE = "certificate"
    PASSWORD = "password"


@unique
class ConnectionType(str, Enum):
    HTTP = "http"
    WEBSOCKET = "websocket"
    SPI = "spi"
    TWS = "tws"
    LOCAL_TERMINAL = "local_terminal"
    RPC = "rpc"


# ── 配置子模型 ────────────────────────────────────────────────


class BaseUrlsConfig(BaseModel):
    rest: Dict[str, str] = Field(default_factory=dict)
    wss: Dict[str, str] = Field(default_factory=dict)
    acct_wss: Dict[str, str] = Field(default_factory=dict)


class ConnectionConfig(BaseModel):
    type: ConnectionType
    timeout: int = Field(default=10, ge=1, le=120)
    max_retries: int = Field(default=3, ge=0, le=10)

    # SPI/本地终端 特定配置
    md_front: Optional[str] = None
    td_front: Optional[str] = None
    exe_path: Optional[str] = None
    session_id: Optional[int] = None

    # TWS 特定配置
    host: Optional[str] = None
    port: Optional[int] = None
    client_id: Optional[int] = None


class AuthConfig(BaseModel):
    type: AuthType
    header_name: Optional[str] = None
    timestamp_key: Optional[str] = None
    signature_key: Optional[str] = None
    api_key_param: Optional[str] = None


class RateLimitRuleConfig(BaseModel):
    name: str
    type: str = Field(..., pattern="^(sliding_window|fixed_window|token_bucket)$")
    interval: int = Field(..., gt=0)
    limit: int = Field(..., gt=0)
    scope: str = Field(default="global", pattern="^(global|endpoint|ip)$")
    endpoint: Optional[str] = None
    weight: int = Field(default=1, ge=1)
    weight_map: Optional[Dict[str, int]] = None


class AssetTypeConfig(BaseModel):
    exchange_name: Optional[str] = Field(
        default=None, description="交易所子类型名称, 如 binance_swap"
    )
    symbol_format: str = Field(..., description="如 {base}{quote} 或 {base}-{quote}")
    rest_paths: Dict[str, str] = Field(default_factory=dict)
    wss_paths: Dict[str, Any] = Field(default_factory=dict)
    wss_channels: Dict[str, str] = Field(default_factory=dict)
    kline_periods: Optional[Dict[str, str]] = None
    legal_currency: Optional[List[str]] = None
    symbols: Optional[List[str]] = None


# ── 主配置模型 ────────────────────────────────────────────────


class ExchangeConfig(BaseModel):
    """交易所/场所配置"""

    id: str = Field(..., min_length=2, max_length=30)
    display_name: str
    venue_type: VenueType
    website: Optional[str] = None
    api_doc: Optional[str] = None

    base_urls: Optional[BaseUrlsConfig] = None
    connection: ConnectionConfig
    authentication: Optional[AuthConfig] = None
    rate_limits: List[RateLimitRuleConfig] = Field(default_factory=list)
    asset_types: Dict[str, AssetTypeConfig] = Field(default_factory=dict)

    # DEX 特定
    chains: Optional[List[str]] = None
    router_address: Optional[str] = None
    factory_address: Optional[str] = None

    # 共享数据
    kline_periods: Optional[Dict[str, str]] = None
    legal_currency: Optional[List[str]] = None
    status_dict: Optional[Dict[str, str]] = None
    exchange_id_map: Optional[Dict[str, str]] = None

    # Broker 特定
    broker_id: Optional[str] = None
    app_id: Optional[str] = None

    model_config = {"extra": "ignore"}

    @field_validator("base_urls")
    @classmethod
    def validate_base_urls(cls, v, info):
        venue_type = info.data.get("venue_type")
        # CEX 必须有 base_urls
        if venue_type == VenueType.CEX and not v:
            raise ValueError("CEX must have base_urls")
        # DEX 和 Broker 可选 base_urls（如 Hyperliquid 类CEX DEX、IB Web API）
        return v

    @field_validator("connection")
    @classmethod
    def validate_connection(cls, v, info):
        venue_type = info.data.get("venue_type")
        conn_type = v.type
        # CEX 必须使用 HTTP、WEBSOCKET 或 SPI（如 CTP）
        if venue_type == VenueType.CEX and conn_type not in (
            ConnectionType.HTTP,
            ConnectionType.WEBSOCKET,
            ConnectionType.SPI,
        ):
            raise ValueError("CEX must use HTTP, WEBSOCKET or SPI connection")
        return v


# ── 加载函数 ──────────────────────────────────────────────────


def load_exchange_config(config_path: str) -> ExchangeConfig:
    """从 YAML 文件加载交易所配置

    :param config_path: YAML 配置文件路径
    :return: ExchangeConfig
    :raises FileNotFoundError: 配置文件不存在
    :raises ValueError: 配置校验失败
    """
    if yaml is None:
        raise ImportError(
            "PyYAML is required to load config files. Install with: pip install PyYAML"
        )

    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not data:
        raise ValueError(f"Config file is empty: {config_path}")

    return ExchangeConfig(**data)


def load_all_exchange_configs(config_dir: str) -> Dict[str, ExchangeConfig]:
    """从目录加载所有交易所配置

    :param config_dir: 配置目录路径
    :return: {exchange_id: ExchangeConfig}
    """
    configs = {}
    if not os.path.isdir(config_dir):
        return configs

    for filename in os.listdir(config_dir):
        if filename.endswith((".yaml", ".yml")) and not filename.startswith("_"):
            filepath = os.path.join(config_dir, filename)
            try:
                config = load_exchange_config(filepath)
                configs[config.id] = config
            except Exception as e:
                import logging

                logging.getLogger(__name__).warning(f"Failed to load config {filepath}: {e}")

    return configs
