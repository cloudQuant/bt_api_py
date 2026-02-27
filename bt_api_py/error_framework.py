# -*- coding: utf-8 -*-
"""
统一错误框架

提供统一的错误码体系、错误翻译器基类，以及 Binance/OKX/CTP 的具体翻译器实现。
每个场所将原始错误码翻译为统一的 UnifiedError，便于上层统一处理。
"""
from enum import Enum, unique
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, ClassVar
from bt_api_py.exceptions import BtApiError


# ── 错误分类 ─────────────────────────────────────────────────

@unique
class ErrorCategory(str, Enum):
    NETWORK = "network"
    AUTH = "auth"
    RATE_LIMIT = "rate_limit"
    BUSINESS = "business"
    SYSTEM = "system"
    CAPABILITY = "capability"
    VALIDATION = "validation"


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
    MARKET_CLOSED = 4008
    POSITION_NOT_FOUND = 4009
    DUPLICATE_ORDER = 4010
    INVALID_ORDER = 4011
    PRECISION_ERROR = 4012

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


# ── 统一错误 ──────────────────────────────────────────────────

@dataclass
class UnifiedError(BtApiError):
    """统一错误格式，继承 BtApiError 以兼容现有异常处理"""
    code: UnifiedErrorCode
    category: ErrorCategory
    venue: str
    message: str
    original_error: Optional[str] = None
    context: Dict[str, Any] = field(default_factory=dict)

    def __str__(self):
        return f"[{self.venue}] {self.code.name}: {self.message}"

    def __repr__(self):
        return f"UnifiedError(code={self.code.name}, venue={self.venue}, message={self.message!r})"

    def to_dict(self) -> Dict[str, Any]:
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

class RateLimitError(UnifiedError):
    """限流错误"""
    def __init__(self, venue: str, response=None, message: str = "Rate limit exceeded"):
        super().__init__(
            code=UnifiedErrorCode.RATE_LIMIT_EXCEEDED,
            category=ErrorCategory.RATE_LIMIT,
            venue=venue,
            message=message,
            context={"raw_response": response} if response else {},
        )


class AuthenticationError(UnifiedError):
    """认证错误"""
    def __init__(self, venue: str, response=None, message: str = "Authentication failed"):
        super().__init__(
            code=UnifiedErrorCode.INVALID_API_KEY,
            category=ErrorCategory.AUTH,
            venue=venue,
            message=message,
            context={"raw_response": response} if response else {},
        )


class ServerError(UnifiedError):
    """服务器端错误"""
    def __init__(self, venue: str, status: int = 500, response=None, message: str = "Server error"):
        super().__init__(
            code=UnifiedErrorCode.INTERNAL_ERROR,
            category=ErrorCategory.SYSTEM,
            venue=venue,
            message=message,
            context={"status": status, "raw_response": response},
        )


class RequestFailedError(UnifiedError):
    """请求失败（通用）"""
    def __init__(self, venue: str, status: int = 0, response=None, message: str = "Request failed"):
        super().__init__(
            code=UnifiedErrorCode.INTERNAL_ERROR,
            category=ErrorCategory.SYSTEM,
            venue=venue,
            message=message,
            context={"status": status, "raw_response": response},
        )


# ── 错误翻译器 ────────────────────────────────────────────────

class ErrorTranslator:
    """错误翻译器基类"""

    # 子类覆盖此映射: {原始错误码: (UnifiedErrorCode, 默认消息)}
    ERROR_MAP: ClassVar[Dict[Any, tuple]] = {}

    # 通用 HTTP 状态码映射
    HTTP_STATUS_MAP: ClassVar[Dict[int, tuple]] = {
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
    def translate(cls, raw_error: dict, venue: str) -> UnifiedError:
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


# ── Binance 错误翻译器 ────────────────────────────────────────

class BinanceErrorTranslator(ErrorTranslator):
    ERROR_MAP = {
        -1000: (UnifiedErrorCode.INTERNAL_ERROR, "Unknown error"),
        -1001: (UnifiedErrorCode.INTERNAL_ERROR, "Disconnected"),
        -1002: (UnifiedErrorCode.PERMISSION_DENIED, "Unauthorized"),
        -1003: (UnifiedErrorCode.RATE_LIMIT_EXCEEDED, "Too many requests"),
        -1006: (UnifiedErrorCode.EXCHANGE_OVERLOADED, "Unexpected disconnect"),
        -1021: (UnifiedErrorCode.EXPIRED_TIMESTAMP, "Timestamp outside recvWindow"),
        -1022: (UnifiedErrorCode.INVALID_SIGNATURE, "Signature not valid"),
        -1100: (UnifiedErrorCode.INVALID_PARAMETER, "Illegal characters in request"),
        -1101: (UnifiedErrorCode.INVALID_PARAMETER, "Too many parameters"),
        -1102: (UnifiedErrorCode.MISSING_PARAMETER, "Mandatory parameter was not sent"),
        -1103: (UnifiedErrorCode.INVALID_PARAMETER, "Unknown parameter"),
        -1104: (UnifiedErrorCode.INVALID_PARAMETER, "Parameter is empty"),
        -1106: (UnifiedErrorCode.INVALID_PARAMETER, "Parameter is not supported"),
        -1111: (UnifiedErrorCode.PRECISION_ERROR, "Precision is over the maximum defined"),
        -1112: (UnifiedErrorCode.INVALID_VOLUME, "Invalid order quantity"),
        -1114: (UnifiedErrorCode.INVALID_API_KEY, "Invalid API-key format"),
        -1115: (UnifiedErrorCode.PERMISSION_DENIED, "Invalid API-key, IP, or permissions"),
        -2010: (UnifiedErrorCode.ORDER_NOT_FOUND, "Order does not exist"),
        -2011: (UnifiedErrorCode.ORDER_ALREADY_FILLED, "Order already filled"),
        -2013: (UnifiedErrorCode.INVALID_ORDER, "Order rejected"),
        -2014: (UnifiedErrorCode.INVALID_API_KEY, "API-key format invalid"),
        -2015: (UnifiedErrorCode.PERMISSION_DENIED, "Invalid API-key, IP, or permissions for action"),
    }


# ── OKX 错误翻译器 ────────────────────────────────────────────

class OKXErrorTranslator(ErrorTranslator):
    ERROR_MAP = {
        "0": (None, "Success"),
        "50000": (UnifiedErrorCode.INTERNAL_ERROR, "Body can not be empty"),
        "50001": (UnifiedErrorCode.EXCHANGE_MAINTENANCE, "Service temporarily unavailable"),
        "50004": (UnifiedErrorCode.NETWORK_TIMEOUT, "Endpoint request timeout"),
        "50011": (UnifiedErrorCode.RATE_LIMIT_EXCEEDED, "Rate limit reached"),
        "50013": (UnifiedErrorCode.EXCHANGE_OVERLOADED, "System busy"),
        "50014": (UnifiedErrorCode.INVALID_PARAMETER, "Parameter error"),
        "50100": (UnifiedErrorCode.INVALID_API_KEY, "API frozen"),
        "50101": (UnifiedErrorCode.INVALID_API_KEY, "API key does not match"),
        "50102": (UnifiedErrorCode.EXPIRED_TIMESTAMP, "Timestamp expired"),
        "50103": (UnifiedErrorCode.INVALID_SIGNATURE, "Signature invalid"),
        "50104": (UnifiedErrorCode.PERMISSION_DENIED, "No permission"),
        "50105": (UnifiedErrorCode.PERMISSION_DENIED, "IP not whitelisted"),
        "51000": (UnifiedErrorCode.INVALID_PARAMETER, "Parameter error"),
        "51001": (UnifiedErrorCode.INVALID_SYMBOL, "Instrument ID does not exist"),
        "51004": (UnifiedErrorCode.INVALID_VOLUME, "Order amount too small"),
        "51008": (UnifiedErrorCode.INSUFFICIENT_BALANCE, "Insufficient balance"),
        "51009": (UnifiedErrorCode.INSUFFICIENT_MARGIN, "Insufficient margin"),
        "51010": (UnifiedErrorCode.INVALID_PRICE, "Price not meeting post-only rule"),
        "51020": (UnifiedErrorCode.ORDER_NOT_FOUND, "Order does not exist"),
        "51023": (UnifiedErrorCode.DUPLICATE_ORDER, "Duplicate order"),
        "51024": (UnifiedErrorCode.ORDER_ALREADY_FILLED, "Order already filled"),
        "51503": (UnifiedErrorCode.INVALID_PARAMETER, "Reduce-only parameter error"),
    }

    @classmethod
    def translate(cls, raw_error: dict, venue: str) -> Optional[UnifiedError]:
        """OKX 错误码是字符串，需要特殊处理"""
        code = raw_error.get("code", raw_error.get("sCode", ""))
        msg = raw_error.get("msg", raw_error.get("sMsg", ""))
        code_str = str(code) if code is not None else ""

        if code_str in cls.ERROR_MAP:
            unified_code, default_msg = cls.ERROR_MAP[code_str]
            if unified_code is None:
                return None
            return UnifiedError(
                code=unified_code,
                category=cls._get_category(unified_code),
                venue=venue,
                message=msg or default_msg,
                original_error=f"{code}: {msg}",
                context={"raw_response": raw_error},
            )

        return super().translate(raw_error, venue)


# ── CTP 错误翻译器 ────────────────────────────────────────────

class CTPErrorTranslator(ErrorTranslator):
    ERROR_MAP = {
        0: (None, "成功"),
        -1: (UnifiedErrorCode.NETWORK_DISCONNECTED, "网络连接失败"),
        -2: (UnifiedErrorCode.INTERNAL_ERROR, "未处理请求超过许可数"),
        -3: (UnifiedErrorCode.RATE_LIMIT_EXCEEDED, "每秒发送请求数超过许可数"),
        -4: (UnifiedErrorCode.RATE_LIMIT_EXCEEDED, "上传请求数超过许可数"),
        -5: (UnifiedErrorCode.INTERNAL_ERROR, "发送请求失败"),
        -6: (UnifiedErrorCode.NETWORK_TIMEOUT, "接收响应失败"),
        -7: (UnifiedErrorCode.NETWORK_DISCONNECTED, "连接断开"),
        -8: (UnifiedErrorCode.NETWORK_TIMEOUT, "请求处理超时"),
        # CTP 业务错误 (正数)
        2: (UnifiedErrorCode.INVALID_ORDER, "不允许的报单操作"),
        3: (UnifiedErrorCode.INSUFFICIENT_BALANCE, "资金不足"),
        12: (UnifiedErrorCode.INVALID_PARAMETER, "报单字段有误"),
        22: (UnifiedErrorCode.INVALID_SYMBOL, "找不到合约"),
        25: (UnifiedErrorCode.ORDER_NOT_FOUND, "找不到报单"),
        31: (UnifiedErrorCode.MARKET_CLOSED, "不在交易时间"),
        44: (UnifiedErrorCode.INVALID_PRICE, "报单价格超出涨跌停限制"),
        51: (UnifiedErrorCode.DUPLICATE_ORDER, "报单重复"),
    }


# ── IB Web API 错误翻译器 ────────────────────────────────────

class IBWebErrorTranslator(ErrorTranslator):
    """IB Web API 错误翻译器

    IB Web API 返回的错误格式:
    {
      "error": {
        "code": "INVALID_REQUEST",
        "message": "...",
        "details": {...}
      }
    }
    """
    ERROR_MAP = {
        "INVALID_REQUEST": (UnifiedErrorCode.INVALID_PARAMETER, "Invalid request"),
        "UNAUTHORIZED": (UnifiedErrorCode.INVALID_API_KEY, "Authentication failed"),
        "FORBIDDEN": (UnifiedErrorCode.PERMISSION_DENIED, "Insufficient permissions"),
        "NOT_FOUND": (UnifiedErrorCode.INVALID_SYMBOL, "Resource not found"),
        "RATE_LIMIT_EXCEEDED": (UnifiedErrorCode.RATE_LIMIT_EXCEEDED, "Rate limit exceeded"),
        "ACCOUNT_NOT_ELIGIBLE": (UnifiedErrorCode.PERMISSION_DENIED, "Account not eligible"),
        "VALIDATION_ERROR": (UnifiedErrorCode.INVALID_PARAMETER, "Input validation failed"),
        "SYSTEM_ERROR": (UnifiedErrorCode.INTERNAL_ERROR, "Internal server error"),
    }

    @classmethod
    def translate(cls, raw_error: dict, venue: str) -> Optional[UnifiedError]:
        """IB Web API 错误码是字符串，嵌套在 error 对象中"""
        error_obj = raw_error.get("error", raw_error)
        code = error_obj.get("code", "")
        msg = error_obj.get("message", "")
        code_str = str(code) if code is not None else ""

        if code_str in cls.ERROR_MAP:
            unified_code, default_msg = cls.ERROR_MAP[code_str]
            if unified_code is None:
                return None
            return UnifiedError(
                code=unified_code,
                category=cls._get_category(unified_code),
                venue=venue,
                message=msg or default_msg,
                original_error=f"{code}: {msg}",
                context={"raw_response": raw_error},
            )

        return super().translate(raw_error, venue)
