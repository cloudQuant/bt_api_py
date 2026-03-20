"""
配置系统 — 基于 pydantic 的 Schema 校验

支持从 YAML 文件加载交易所/场所配置，自动校验字段合法性。
"""

import enum
from enum import unique
from pathlib import Path
from typing import Any

try:
    from pydantic import BaseModel, Field, ValidationError, ValidationInfo, field_validator
except ImportError:
    raise ImportError(
        "pydantic is required for config_loader. Install with: pip install pydantic"
    ) from None

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

from bt_api_py.exceptions import ConfigurationError
from bt_api_py.logging_factory import get_logger

__all__ = [
    "VenueType",
    "AuthType",
    "ConnectionType",
    "BaseUrlsConfig",
    "ConnectionConfig",
    "AuthConfig",
    "RateLimitRuleConfig",
    "AssetTypeConfig",
    "ExchangeConfig",
    "get_exchange_config_path",
    "load_exchange_config",
    "load_all_exchange_configs",
]


# ── 枚举定义 ──────────────────────────────────────────────────


@unique
class VenueType(enum.StrEnum):
    CEX = "cex"
    DEX = "dex"
    BROKER = "broker"


@unique
class AuthType(enum.StrEnum):
    NONE = "none"
    API_KEY = "api_key"
    HMAC_SHA256 = "hmac_sha256"
    HMAC_SHA256_JWT = "hmac_sha256_jwt"
    HMAC_SHA384 = "hmac_sha384"
    HMAC_SHA512 = "hmac_sha512"
    OAUTH = "oauth"
    CERTIFICATE = "certificate"
    PASSWORD = "password"


@unique
class ConnectionType(enum.StrEnum):
    HTTP = "http"
    WEBSOCKET = "websocket"
    SPI = "spi"
    TWS = "tws"
    LOCAL_TERMINAL = "local_terminal"
    RPC = "rpc"


# ── 配置子模型 ────────────────────────────────────────────────


class BaseUrlsConfig(BaseModel):
    rest: dict[str, str] = Field(default_factory=dict)
    wss: dict[str, str] = Field(default_factory=dict)
    acct_wss: dict[str, str] = Field(default_factory=dict)


class ConnectionConfig(BaseModel):
    type: ConnectionType
    timeout: int = Field(default=10, ge=1, le=120)
    max_retries: int = Field(default=3, ge=0, le=10)

    # SPI/本地终端 特定配置
    md_front: str | None = None
    td_front: str | None = None
    exe_path: str | None = None
    session_id: int | None = None

    # TWS 特定配置
    host: str | None = None
    port: int | None = None
    client_id: int | None = None


class AuthConfig(BaseModel):
    type: AuthType
    header_name: str | None = None
    timestamp_key: str | None = None
    signature_key: str | None = None
    api_key_param: str | None = None


class RateLimitRuleConfig(BaseModel):
    name: str
    type: str = Field(..., pattern="^(sliding_window|fixed_window|token_bucket)$")
    interval: int = Field(..., gt=0)
    limit: int = Field(..., gt=0)
    scope: str = Field(default="global", pattern="^(global|endpoint|ip)$")
    endpoint: str | None = None
    weight: int = Field(default=1, ge=1)
    weight_map: dict[str, int] | None = None


class AssetTypeConfig(BaseModel):
    exchange_name: str | None = Field(default=None, description="交易所子类型名称, 如 binance_swap")
    rest_url: str | None = Field(default=None, description="REST API base URL for this asset type")
    wss_url: str | None = Field(default=None, description="WebSocket URL for this asset type")
    symbol_format: str = Field(..., description="如 {base}{quote} 或 {base}-{quote}")
    rest_paths: dict[str, str] = Field(default_factory=dict)
    wss_paths: dict[str, Any] = Field(default_factory=dict)
    wss_channels: dict[str, str] = Field(default_factory=dict)
    kline_periods: dict[str, str] | None = None
    legal_currency: list[str] | None = None
    symbols: list[str] | None = None
    trading_symbols: dict[str, str] | None = Field(
        default=None, description="交易符号映射，如 BTC/USDC: BTC"
    )


# ── 主配置模型 ────────────────────────────────────────────────


class ExchangeConfig(BaseModel):
    """交易所/场所配置"""

    id: str = Field(..., min_length=2, max_length=30)
    display_name: str
    venue_type: VenueType
    website: str | None = None
    api_doc: str | None = None

    base_urls: BaseUrlsConfig | None = None
    connection: ConnectionConfig
    authentication: AuthConfig | None = None
    rate_limits: list[RateLimitRuleConfig] = Field(default_factory=list)
    asset_types: dict[str, AssetTypeConfig] = Field(default_factory=dict)

    # DEX 特定
    chains: list[str] | None = None
    router_address: str | dict[str, str] | None = None
    factory_address: str | dict[str, str] | None = None

    # 共享数据
    kline_periods: dict[str, str] | None = None
    legal_currency: list[str] | None = None
    status_dict: dict[str, str] | None = None
    exchange_id_map: dict[str, str] | None = None

    # Broker 特定
    broker_id: str | None = None
    app_id: str | None = None

    model_config = {"extra": "ignore"}

    @field_validator("base_urls")
    @classmethod
    def validate_base_urls(
        cls, v: BaseUrlsConfig | None, info: ValidationInfo
    ) -> BaseUrlsConfig | None:
        venue_type = info.data.get("venue_type")
        # CEX 必须有 base_urls
        if venue_type == VenueType.CEX and not v:
            raise ValueError("CEX must have base_urls")
        # DEX 和 Broker 可选 base_urls（如 Hyperliquid 类CEX DEX、IB Web API）
        return v

    @field_validator("connection")
    @classmethod
    def validate_connection(
        cls, v: ConnectionConfig, info: ValidationInfo
    ) -> ConnectionConfig:
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


def get_exchange_config_path(filename: str) -> Path:
    """Return Path to a config file in bt_api_py/configs/.

    Use pathlib for cross-platform path handling and cleaner code.
    """
    return Path(__file__).resolve().parent / "configs" / filename


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

    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not data:
        raise ConfigurationError(f"Config file is empty: {config_path}")
    if not isinstance(data, dict):
        raise ConfigurationError(f"Config file must contain a mapping object: {config_path}")

    return ExchangeConfig(**data)


def load_all_exchange_configs(config_dir: str) -> dict[str, ExchangeConfig]:
    """从目录加载所有交易所配置

    :param config_dir: 配置目录路径
    :return: {exchange_id: ExchangeConfig}
    """
    configs: dict[str, ExchangeConfig] = {}
    path = Path(config_dir)
    if not path.is_dir():
        return configs

    load_errors: tuple[type[Exception], ...] = (
        ConfigurationError,
        FileNotFoundError,
        ValidationError,
    )
    if yaml is not None:
        load_errors = (*load_errors, yaml.YAMLError)

    logger = get_logger("config_loader")

    for filepath in sorted(path.iterdir(), key=lambda item: item.name):
        if filepath.suffix in (".yaml", ".yml") and not filepath.name.startswith("_"):
            try:
                config = load_exchange_config(str(filepath))
                configs[config.id] = config
            except load_errors as e:
                logger.warning(f"Failed to load config {filepath!s}: {e}")

    return configs
