"""统一错误框架

提供统一的错误码体系、错误翻译器基类及便捷异常子类。
各交易所的具体翻译器实现位于 bt_api_py/errors/ 子模块中。

关于异常体系:
    bt_api_py 有两套异常:
    - exceptions.py: 传统异常层级 (RateLimitError, RequestError 等)，用于控制流
    - error.py (本模块): 带错误码的统一错误 (UnifiedError 及子类)，用于交易所错误翻译

    便捷子类 (UnifiedRateLimitError, UnifiedAuthError 等) 同时继承两套体系，
    因此 ``except RateLimitError`` 也能捕获 ``UnifiedRateLimitError``。
"""

import enum
import importlib
from dataclasses import dataclass, field
from enum import Enum, unique
from typing import TYPE_CHECKING, Any, ClassVar

from bt_api_py.exceptions import (
    AuthenticationError,
    BtApiError,
    RateLimitError,
    RequestFailedError,
)

# ── 错误分类 ─────────────────────────────────────────────────


@unique
class ErrorCategory(enum.StrEnum):
    NETWORK = "network"
    AUTH = "auth"
    RATE_LIMIT = "rate_limit"
    BUSINESS = "business"
    SYSTEM = "system"
    CAPABILITY = "capability"
    VALIDATION = "validation"
    API = "api"
    ORDER = "order"
    TRADE = "trade"
    ACCOUNT = "account"


# ── 统一错误码 ────────────────────────────────────────────────


@unique
class UnifiedErrorCode(int, Enum):
    # 网络错误 (1xxx)
    NETWORK_TIMEOUT = 1001
    NETWORK_DISCONNECTED = 1002
    DNS_ERROR = 1003
    CONNECTION_REFUSED = 1004

    # 认证错误 (2xxx)
    INVALID_API_KEY = 2001
    INVALID_SIGNATURE = 2002
    EXPIRED_TIMESTAMP = 2003
    PERMISSION_DENIED = 2004
    SESSION_EXPIRED = 2005

    # 限流错误 (3xxx)
    RATE_LIMIT_EXCEEDED = 3001
    IP_BANNED = 3002
    TOO_MANY_REQUESTS = 3003

    # 业务错误 (4xxx)
    INVALID_SYMBOL = 4001
    INVALID_PRICE = 4002
    INVALID_VOLUME = 4003
    INSUFFICIENT_BALANCE = 4004
    INSUFFICIENT_MARGIN = 4005
    ORDER_NOT_FOUND = 4006
    ORDER_ALREADY_FILLED = 4007
    ORDER_CANCELLED = 4008
    ORDER_TIMEOUT = 4009
    MARKET_CLOSED = 4010
    POSITION_NOT_FOUND = 4011
    DUPLICATE_ORDER = 4012
    INVALID_ORDER = 4013
    MIN_NOTIONAL = 4014
    MINIMUM_NOT_MET = 4015
    PRECISION_ERROR = 4016
    ORDER_CANCEL_FAILED = 4017
    INVALID_SIDE = 4018
    INVALID_ORDER_TYPE = 4019
    WITHDRAWAL_FAILED = 4020
    DEPOSIT_FAILED = 4021
    TRANSFER_FAILED = 4022
    ACCOUNT_SUSPENDED = 4023

    # 系统错误 (5xxx)
    EXCHANGE_MAINTENANCE = 5001
    EXCHANGE_OVERLOADED = 5002
    INTERNAL_ERROR = 5003
    UNSUPPORTED_OPERATION = 5004

    # 能力错误 (6xxx)
    NOT_SUPPORTED = 6001
    NOT_IMPLEMENTED = 6002

    # 验证错误 (7xxx)
    INVALID_PARAMETER = 7001
    MISSING_PARAMETER = 7002
    PARAMETER_OUT_OF_RANGE = 7003

    # 通用分类错误 (8xxx) — 用于无法精确映射时的兜底
    API_ERROR = 8001
    ORDER_ERROR = 8002
    TRADE_ERROR = 8003
    ACCOUNT_ERROR = 8004


# ── 统一错误 ──────────────────────────────────────────────────


@dataclass
class UnifiedError(BtApiError):
    """统一错误格式，继承 BtApiError 以兼容现有异常处理"""

    code: UnifiedErrorCode
    category: ErrorCategory
    venue: str
    message: str
    original_error: str | None = None
    context: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"[{self.venue}] {self.code.name}: {self.message}"

    def __repr__(self) -> str:
        return f"UnifiedError(code={self.code.name}, venue={self.venue}, message={self.message!r})"

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "code_name": self.code.name,
            "category": self.category.value,
            "venue": self.venue,
            "message": self.message,
            "original_error": self.original_error,
            "context": self.context,
        }


# ── 便捷异常子类 ──────────────────────────────────────────────


class UnifiedRateLimitError(UnifiedError, RateLimitError):
    """限流错误

    同时继承 UnifiedError 和 exceptions.RateLimitError，
    因此 ``except RateLimitError`` 可以捕获此异常。
    """

    def __init__(
        self,
        venue: str,
        response: Any = None,
        message: str = "Rate limit exceeded",
    ) -> None:
        UnifiedError.__init__(
            self,
            code=UnifiedErrorCode.RATE_LIMIT_EXCEEDED,
            category=ErrorCategory.RATE_LIMIT,
            venue=venue,
            message=message,
            context={"raw_response": response} if response else {},
        )
        # exceptions.RateLimitError 兼容属性
        self.exchange_name = venue
        self.retry_after = None


class UnifiedAuthError(UnifiedError, AuthenticationError):
    """认证错误

    同时继承 UnifiedError 和 exceptions.AuthenticationError，
    因此 ``except AuthenticationError`` 可以捕获此异常。
    """

    def __init__(
        self,
        venue: str,
        response: Any = None,
        message: str = "Authentication failed",
    ) -> None:
        UnifiedError.__init__(
            self,
            code=UnifiedErrorCode.INVALID_API_KEY,
            category=ErrorCategory.AUTH,
            venue=venue,
            message=message,
            context={"raw_response": response} if response else {},
        )
        # exceptions.AuthenticationError 兼容属性
        self.exchange_name = venue


class ServerError(UnifiedError):
    """服务器端错误"""

    def __init__(
        self,
        venue: str,
        status: int = 500,
        response: Any = None,
        message: str = "Server error",
    ) -> None:
        super().__init__(
            code=UnifiedErrorCode.INTERNAL_ERROR,
            category=ErrorCategory.SYSTEM,
            venue=venue,
            message=message,
            context={"status": status, "raw_response": response},
        )


class UnifiedRequestFailedError(UnifiedError, RequestFailedError):
    """请求失败（通用）

    同时继承 UnifiedError 和 exceptions.RequestFailedError，
    因此 ``except RequestFailedError`` 可以捕获此异常。
    """

    def __init__(
        self,
        venue: str,
        status: int = 0,
        response: Any = None,
        message: str = "Request failed",
    ) -> None:
        UnifiedError.__init__(
            self,
            code=UnifiedErrorCode.INTERNAL_ERROR,
            category=ErrorCategory.SYSTEM,
            venue=venue,
            message=message,
            context={"status": status, "raw_response": response},
        )
        # exceptions.RequestFailedError 兼容属性
        self.exchange_name = venue
        self.status_code = status


# ── 错误翻译器 ────────────────────────────────────────────────


class ErrorTranslator:
    """错误翻译器基类"""

    # 子类覆盖此映射: {原始错误码: (UnifiedErrorCode, 默认消息)}
    ERROR_MAP: ClassVar[dict[Any, tuple[UnifiedErrorCode | None, str]]] = {}

    # 通用 HTTP 状态码映射
    HTTP_STATUS_MAP: ClassVar[dict[int, tuple[UnifiedErrorCode, str]]] = {
        400: (UnifiedErrorCode.INVALID_PARAMETER, "Invalid request parameters"),
        401: (UnifiedErrorCode.INVALID_API_KEY, "Invalid API key"),
        403: (UnifiedErrorCode.PERMISSION_DENIED, "Permission denied"),
        404: (UnifiedErrorCode.INVALID_SYMBOL, "Resource not found"),
        429: (UnifiedErrorCode.RATE_LIMIT_EXCEEDED, "Rate limit exceeded"),
        500: (UnifiedErrorCode.INTERNAL_ERROR, "Internal server error"),
        503: (UnifiedErrorCode.EXCHANGE_OVERLOADED, "Service unavailable"),
        504: (UnifiedErrorCode.NETWORK_TIMEOUT, "Gateway timeout"),
    }

    @classmethod
    def translate(cls, raw_error: dict[str, Any], venue: str) -> UnifiedError | None:
        """将原始错误转换为统一错误

        :param raw_error: 包含错误信息的字典 (code, msg/message, status 等)
        :param venue: 场所名称
        :return: UnifiedError
        """
        code = raw_error.get("code")
        msg = raw_error.get("msg", raw_error.get("message", ""))
        status = raw_error.get("status")

        # 1. 尝试场所特定错误码映射
        if code is not None and code in cls.ERROR_MAP:
            unified_code, default_msg = cls.ERROR_MAP[code]
            if unified_code is None:
                return None  # 不是错误（如 CTP 返回码 0）
            return UnifiedError(
                code=unified_code,
                category=cls._get_category(unified_code),
                venue=venue,
                message=msg or default_msg,
                original_error=f"{code}: {msg}",
                context={"raw_response": raw_error},
            )

        # 2. 尝试 HTTP 状态码映射
        if status and status in cls.HTTP_STATUS_MAP:
            unified_code, default_msg = cls.HTTP_STATUS_MAP[status]
            return UnifiedError(
                code=unified_code,
                category=cls._get_category(unified_code),
                venue=venue,
                message=msg or default_msg,
                original_error=f"HTTP {status}: {msg}",
                context={"raw_response": raw_error},
            )

        # 3. 默认
        return UnifiedError(
            code=UnifiedErrorCode.INTERNAL_ERROR,
            category=ErrorCategory.SYSTEM,
            venue=venue,
            message=msg or "Unknown error",
            original_error=str(raw_error),
            context={"raw_response": raw_error},
        )

    @classmethod
    def _get_category(cls, code: UnifiedErrorCode) -> ErrorCategory:
        """从错误码数值范围推断类别"""
        v = code.value
        if 1000 <= v < 2000:
            return ErrorCategory.NETWORK
        elif 2000 <= v < 3000:
            return ErrorCategory.AUTH
        elif 3000 <= v < 4000:
            return ErrorCategory.RATE_LIMIT
        elif 4000 <= v < 5000:
            return ErrorCategory.BUSINESS
        elif 5000 <= v < 6000:
            return ErrorCategory.SYSTEM
        elif 6000 <= v < 7000:
            return ErrorCategory.CAPABILITY
        else:
            return ErrorCategory.VALIDATION


_TRANSLATOR_EXPORTS: dict[str, tuple[str, str]] = {
    "GeminiErrorTranslator": ("bt_api_py.errors.gemini_translator", "GeminiErrorTranslator"),
    "BinanceErrorTranslator": ("bt_api_py.errors.binance_translator", "BinanceErrorTranslator"),
    "OKXErrorTranslator": ("bt_api_py.errors.okx_translator", "OKXErrorTranslator"),
    "KrakenErrorTranslator": ("bt_api_py.errors.kraken_translator", "KrakenErrorTranslator"),
    "CTPErrorTranslator": ("bt_api_py.errors.ctp_translator", "CTPErrorTranslator"),
    "IBWebErrorTranslator": ("bt_api_py.errors.ib_web_translator", "IBWebErrorTranslator"),
    "BybitErrorTranslator": ("bt_api_py.errors.bybit_translator", "BybitErrorTranslator"),
    "BitgetErrorTranslator": ("bt_api_py.errors.bitget_translator", "BitgetErrorTranslator"),
    "KuCoinErrorTranslator": ("bt_api_py.errors.kucoin_translator", "KuCoinErrorTranslator"),
    "UpbitErrorTranslator": ("bt_api_py.errors.upbit_translator", "UpbitErrorTranslator"),
    "BitfinexErrorTranslator": (
        "bt_api_py.errors.bitfinex_error_translator",
        "BitfinexErrorTranslator",
    ),
}


def __getattr__(name: str) -> type[Any]:
    """Lazy re-export translators from `bt_api_py.errors.*` for backward compatibility."""
    if name in _TRANSLATOR_EXPORTS:
        module_name, attr = _TRANSLATOR_EXPORTS[name]
        module = importlib.import_module(module_name)
        value = getattr(module, attr)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


if TYPE_CHECKING:
    # For type checkers and IDEs, make exports visible without importing at runtime.
    from bt_api_py.errors.binance_translator import BinanceErrorTranslator as BinanceErrorTranslator
    from bt_api_py.errors.bitfinex_error_translator import (
        BitfinexErrorTranslator as BitfinexErrorTranslator,
    )
    from bt_api_py.errors.bitget_translator import BitgetErrorTranslator as BitgetErrorTranslator
    from bt_api_py.errors.bybit_translator import BybitErrorTranslator as BybitErrorTranslator
    from bt_api_py.errors.ctp_translator import CTPErrorTranslator as CTPErrorTranslator
    from bt_api_py.errors.gemini_translator import GeminiErrorTranslator as GeminiErrorTranslator
    from bt_api_py.errors.ib_web_translator import IBWebErrorTranslator as IBWebErrorTranslator
    from bt_api_py.errors.kraken_translator import KrakenErrorTranslator as KrakenErrorTranslator
    from bt_api_py.errors.kucoin_translator import KuCoinErrorTranslator as KuCoinErrorTranslator
    from bt_api_py.errors.okx_translator import OKXErrorTranslator as OKXErrorTranslator
    from bt_api_py.errors.upbit_translator import UpbitErrorTranslator as UpbitErrorTranslator

__all__ = [
    # 核心类
    "ErrorCategory",
    "UnifiedErrorCode",
    "UnifiedError",
    "UnifiedRateLimitError",
    "UnifiedAuthError",
    "ServerError",
    "UnifiedRequestFailedError",
    "ErrorTranslator",
    # 翻译器（从 errors/ 重新导出）
    "GeminiErrorTranslator",
    "BinanceErrorTranslator",
    "OKXErrorTranslator",
    "KrakenErrorTranslator",
    "CTPErrorTranslator",
    "IBWebErrorTranslator",
    "BybitErrorTranslator",
    "BitgetErrorTranslator",
    "KuCoinErrorTranslator",
    "UpbitErrorTranslator",
    "BitfinexErrorTranslator",
]
