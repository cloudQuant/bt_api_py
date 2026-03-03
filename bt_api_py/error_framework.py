"""
统一错误框架

提供统一的错误码体系、错误翻译器基类，以及 Binance/OKX/CTP 的具体翻译器实现。
每个场所将原始错误码翻译为统一的 UnifiedError，便于上层统一处理。
"""

from dataclasses import dataclass, field
from enum import Enum, unique
from typing import Any, ClassVar

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
    ORDER_CANCELLED = 4008
    ORDER_TIMEOUT = 4009
    MARKET_CLOSED = 4010
    POSITION_NOT_FOUND = 4011
    DUPLICATE_ORDER = 4012
    INVALID_ORDER = 4013
    MIN_NOTIONAL = 4014
    MINIMUM_NOT_MET = 4015
    PRECISION_ERROR = 4016

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
    original_error: str | None = None
    context: dict[str, Any] = field(default_factory=dict)

    def __str__(self):
        return f"[{self.venue}] {self.code.name}: {self.message}"

    def __repr__(self):
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
    ERROR_MAP: ClassVar[dict[Any, tuple]] = {}

    # 通用 HTTP 状态码映射
    HTTP_STATUS_MAP: ClassVar[dict[int, tuple]] = {
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


# ── Gemini 错误翻译器 ─────────────────────────────────────────


class GeminiErrorTranslator(ErrorTranslator):
    """Gemini API error translator.

    Gemini API returns errors as JSON:
    {"result": "error", "reason": "InvalidSignature", "message": "..."}
    """
    ERROR_MAP = {
        "InvalidSignature": (UnifiedErrorCode.INVALID_SIGNATURE, "Invalid signature"),
        "InvalidNonce": (UnifiedErrorCode.EXPIRED_TIMESTAMP, "Invalid nonce"),
        "MissingPayloadHeader": (UnifiedErrorCode.MISSING_PARAMETER, "Missing payload header"),
        "InvalidPayload": (UnifiedErrorCode.INVALID_PARAMETER, "Invalid payload"),
        "InvalidAPIKey": (UnifiedErrorCode.INVALID_API_KEY, "Invalid API key"),
        "RateLimit": (UnifiedErrorCode.RATE_LIMIT_EXCEEDED, "Rate limit exceeded"),
        "InsufficientFunds": (UnifiedErrorCode.INSUFFICIENT_BALANCE, "Insufficient funds"),
        "InvalidPrice": (UnifiedErrorCode.INVALID_PRICE, "Invalid price"),
        "InvalidQuantity": (UnifiedErrorCode.INVALID_VOLUME, "Invalid quantity"),
        "InvalidSide": (UnifiedErrorCode.INVALID_PARAMETER, "Invalid side"),
        "InvalidOrderType": (UnifiedErrorCode.INVALID_ORDER, "Invalid order type"),
        "OrderNotFound": (UnifiedErrorCode.ORDER_NOT_FOUND, "Order not found"),
        "ClientOrderIdNotFound": (UnifiedErrorCode.ORDER_NOT_FOUND, "Client order ID not found"),
        "SystemError": (UnifiedErrorCode.INTERNAL_ERROR, "System error"),
        "MarketNotOpen": (UnifiedErrorCode.MARKET_CLOSED, "Market not open"),
        "MaintenanceError": (UnifiedErrorCode.EXCHANGE_OVERLOADED, "Exchange under maintenance"),
    }


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
        -2015: (
            UnifiedErrorCode.PERMISSION_DENIED,
            "Invalid API-key, IP, or permissions for action",
        ),
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
    def translate(cls, raw_error: dict, venue: str) -> UnifiedError | None:
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


# ── Kraken 错误翻译器 ────────────────────────────────────────────


class KrakenErrorTranslator(ErrorTranslator):
    """Kraken API 错误翻译器

    Kraken API 返回的错误格式:
    {
      "error": ["EAPI:Invalid key", "Invalid API key"],
      "result": {...}
    }
    """

    ERROR_MAP = {
        # 认证错误
        "EAPI:Invalid key": (UnifiedErrorCode.INVALID_API_KEY, "Invalid API key"),
        "EAPI:Invalid signature": (UnifiedErrorCode.INVALID_SIGNATURE, "Invalid signature"),
        "EAPI:Invalid nonce": (UnifiedErrorCode.EXPIRED_TIMESTAMP, "Invalid or duplicate nonce"),
        "EAPI:Invalid request": (UnifiedErrorCode.INVALID_PARAMETER, "Invalid request format"),
        "EAPI:Not allowed": (UnifiedErrorCode.PERMISSION_DENIED, "Request not permitted"),

        # 限流错误
        "EAPI:Rate limit exceeded": (UnifiedErrorCode.RATE_LIMIT_EXCEEDED, "Rate limit exceeded"),

        # 业务错误
        "EOrder:Insufficient funds": (UnifiedErrorCode.INSUFFICIENT_BALANCE, "Insufficient funds"),
        "EOrder:Invalid price": (UnifiedErrorCode.INVALID_PRICE, "Invalid price"),
        "EOrder:Unknown order": (UnifiedErrorCode.ORDER_NOT_FOUND, "Order not found"),
        "EOrder:Order already closed": (UnifiedErrorCode.ORDER_ALREADY_FILLED, "Order already closed"),
        "EOrder:Order would immediately trigger": (UnifiedErrorCode.INVALID_ORDER, "Order would immediately trigger"),
        "EOrder:Margin position too small": (UnifiedErrorCode.INSUFFICIENT_MARGIN, "Margin position too small"),

        # 参数错误
        "EInput:Invalid arguments": (UnifiedErrorCode.INVALID_PARAMETER, "Invalid arguments"),
        "EInput:Missing arguments": (UnifiedErrorCode.MISSING_PARAMETER, "Missing arguments"),
        "EInput:Unknown arguments": (UnifiedErrorCode.INVALID_PARAMETER, "Unknown arguments"),

        # 系统错误
        "EService:Unavailable": (UnifiedErrorCode.EXCHANGE_MAINTENANCE, "Service unavailable"),
        "EService:Market in cancel_only mode": (UnifiedErrorCode.MARKET_CLOSED, "Market in cancel only mode"),
        "EService:Duplicate post": (UnifiedErrorCode.DUPLICATE_ORDER, "Duplicate request"),
        "EService:Order timeout": (UnifiedErrorCode.UNSUPPORTED_OPERATION, "Order timeout"),

        # 其他错误
        "ETrade:Invalid user transaction": (UnifiedErrorCode.INVALID_ORDER, "Invalid user transaction"),
        "EAccount:Invalid asset": (UnifiedErrorCode.INVALID_SYMBOL, "Invalid asset"),
        "EGeneral:Unknown asset pair": (UnifiedErrorCode.INVALID_SYMBOL, "Unknown asset pair"),
    }

    @classmethod
    def translate(cls, raw_error: dict, venue: str) -> UnifiedError | None:
        """Kraken API 错误码在 error 数组中"""
        errors = raw_error.get("error", [])
        if not errors:
            return None  # No error

        # 取第一个错误
        error_msg = errors[0] if errors else "Unknown error"

        # 尝试匹配错误码
        for error_code, error_data in cls.ERROR_MAP.items():
            if error_code in error_msg:
                unified_code, default_msg = error_data
                if unified_code is None:
                    return None
                return UnifiedError(
                    code=unified_code,
                    category=cls._get_category(unified_code),
                    venue=venue,
                    message=error_msg,
                    original_error=error_msg,
                    context={"raw_response": raw_error},
                )

        # 如果没有匹配到，尝试解析错误码模式
        if error_msg.startswith("EAPI:"):
            return UnifiedError(
                code=UnifiedErrorCode.API_ERROR,
                category=ErrorCategory.API,
                venue=venue,
                message=error_msg,
                original_error=error_msg,
                context={"raw_response": raw_error},
            )
        elif error_msg.startswith("EOrder:"):
            return UnifiedError(
                code=UnifiedErrorCode.ORDER_ERROR,
                category=ErrorCategory.ORDER,
                venue=venue,
                message=error_msg,
                original_error=error_msg,
                context={"raw_response": raw_error},
            )
        elif error_msg.startswith("ETrade:"):
            return UnifiedError(
                code=UnifiedErrorCode.TRADE_ERROR,
                category=ErrorCategory.TRADE,
                venue=venue,
                message=error_msg,
                original_error=error_msg,
                context={"raw_response": raw_error},
            )
        elif error_msg.startswith("EAccount:"):
            return UnifiedError(
                code=UnifiedErrorCode.ACCOUNT_ERROR,
                category=ErrorCategory.ACCOUNT,
                venue=venue,
                message=error_msg,
                original_error=error_msg,
                context={"raw_response": raw_error},
            )

        # 默认错误
        return UnifiedError(
            code=UnifiedErrorCode.INTERNAL_ERROR,
            category=ErrorCategory.SYSTEM,
            venue=venue,
            message=error_msg,
            original_error=error_msg,
            context={"raw_response": raw_error},
        )


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
    def translate(cls, raw_error: dict, venue: str) -> UnifiedError | None:
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


# ── Kraken 错误翻译器 ────────────────────────────────────────────


class KrakenErrorTranslator(ErrorTranslator):
    """Kraken API 错误翻译器

    Kraken API 返回的错误格式:
    {
      "error": ["EAPI:Invalid key", "Invalid API key"],
      "result": {...}
    }
    """

    ERROR_MAP = {
        # 认证错误
        "EAPI:Invalid key": (UnifiedErrorCode.INVALID_API_KEY, "Invalid API key"),
        "EAPI:Invalid signature": (UnifiedErrorCode.INVALID_SIGNATURE, "Invalid signature"),
        "EAPI:Invalid nonce": (UnifiedErrorCode.EXPIRED_TIMESTAMP, "Invalid or duplicate nonce"),
        "EAPI:Invalid request": (UnifiedErrorCode.INVALID_PARAMETER, "Invalid request format"),
        "EAPI:Not allowed": (UnifiedErrorCode.PERMISSION_DENIED, "Request not permitted"),

        # 限流错误
        "EAPI:Rate limit exceeded": (UnifiedErrorCode.RATE_LIMIT_EXCEEDED, "Rate limit exceeded"),

        # 业务错误
        "EOrder:Insufficient funds": (UnifiedErrorCode.INSUFFICIENT_BALANCE, "Insufficient funds"),
        "EOrder:Invalid price": (UnifiedErrorCode.INVALID_PRICE, "Invalid price"),
        "EOrder:Unknown order": (UnifiedErrorCode.ORDER_NOT_FOUND, "Order not found"),
        "EOrder:Order already closed": (UnifiedErrorCode.ORDER_ALREADY_FILLED, "Order already closed"),
        "EOrder:Order would immediately trigger": (UnifiedErrorCode.INVALID_ORDER, "Order would immediately trigger"),
        "EOrder:Margin position too small": (UnifiedErrorCode.INSUFFICIENT_MARGIN, "Margin position too small"),

        # 参数错误
        "EInput:Invalid arguments": (UnifiedErrorCode.INVALID_PARAMETER, "Invalid arguments"),
        "EInput:Missing arguments": (UnifiedErrorCode.MISSING_PARAMETER, "Missing arguments"),
        "EInput:Unknown arguments": (UnifiedErrorCode.INVALID_PARAMETER, "Unknown arguments"),

        # 系统错误
        "EService:Unavailable": (UnifiedErrorCode.EXCHANGE_MAINTENANCE, "Service unavailable"),
        "EService:Market in cancel_only mode": (UnifiedErrorCode.MARKET_CLOSED, "Market in cancel only mode"),
        "EService:Duplicate post": (UnifiedErrorCode.DUPLICATE_ORDER, "Duplicate request"),
        "EService:Order timeout": (UnifiedErrorCode.UNSUPPORTED_OPERATION, "Order timeout"),

        # 其他错误
        "ETrade:Invalid user transaction": (UnifiedErrorCode.INVALID_ORDER, "Invalid user transaction"),
        "EAccount:Invalid asset": (UnifiedErrorCode.INVALID_SYMBOL, "Invalid asset"),
        "EGeneral:Unknown asset pair": (UnifiedErrorCode.INVALID_SYMBOL, "Unknown asset pair"),
    }

    @classmethod
    def translate(cls, raw_error: dict, venue: str) -> UnifiedError | None:
        """Kraken API 错误码在 error 数组中"""
        errors = raw_error.get("error", [])
        if not errors:
            return None  # No error

        # 取第一个错误
        error_msg = errors[0] if errors else "Unknown error"

        # 尝试匹配错误码
        for error_code, error_data in cls.ERROR_MAP.items():
            if error_code in error_msg:
                unified_code, default_msg = error_data
                if unified_code is None:
                    return None
                return UnifiedError(
                    code=unified_code,
                    category=cls._get_category(unified_code),
                    venue=venue,
                    message=error_msg,
                    original_error=error_msg,
                    context={"raw_response": raw_error},
                )

        # 如果没有匹配到，尝试解析错误码模式
        if error_msg.startswith("EAPI:"):
            return UnifiedError(
                code=UnifiedErrorCode.API_ERROR,
                category=ErrorCategory.API,
                venue=venue,
                message=error_msg,
                original_error=error_msg,
                context={"raw_response": raw_error},
            )
        elif error_msg.startswith("EOrder:"):
            return UnifiedError(
                code=UnifiedErrorCode.ORDER_ERROR,
                category=ErrorCategory.ORDER,
                venue=venue,
                message=error_msg,
                original_error=error_msg,
                context={"raw_response": raw_error},
            )
        elif error_msg.startswith("ETrade:"):
            return UnifiedError(
                code=UnifiedErrorCode.TRADE_ERROR,
                category=ErrorCategory.TRADE,
                venue=venue,
                message=error_msg,
                original_error=error_msg,
                context={"raw_response": raw_error},
            )
        elif error_msg.startswith("EAccount:"):
            return UnifiedError(
                code=UnifiedErrorCode.ACCOUNT_ERROR,
                category=ErrorCategory.ACCOUNT,
                venue=venue,
                message=error_msg,
                original_error=error_msg,
                context={"raw_response": raw_error},
            )

        # 默认错误
        return UnifiedError(
            code=UnifiedErrorCode.INTERNAL_ERROR,
            category=ErrorCategory.SYSTEM,
            venue=venue,
            message=error_msg,
            original_error=error_msg,
            context={"raw_response": raw_error},
        )


class BybitErrorTranslator(ErrorTranslator):
    """Bybit API 错误翻译器

    Bybit API 返回的错误格式:
    {
      "retCode": 10003,
      "retMsg": "Invalid API key",
      "retExtInfo": {},
      "time": 1672304484978
    }
    """

    ERROR_MAP = {
        10001: (UnifiedErrorCode.INVALID_PARAMETER, "Parameter error"),
        10003: (UnifiedErrorCode.INVALID_API_KEY, "Invalid API key"),
        10004: (UnifiedErrorCode.INVALID_SIGNATURE, "Invalid signature"),
        10005: (UnifiedErrorCode.PERMISSION_DENIED, "Permission denied"),
        10006: (UnifiedErrorCode.RATE_LIMIT_EXCEEDED, "Too many requests"),
        10016: (UnifiedErrorCode.INTERNAL_ERROR, "Server error"),
        110001: (UnifiedErrorCode.ORDER_NOT_FOUND, "Order does not exist"),
        110004: (UnifiedErrorCode.INSUFFICIENT_BALANCE, "Insufficient balance"),
        110007: (UnifiedErrorCode.INVALID_PRICE, "Order price is out of range"),
        110043: (UnifiedErrorCode.PERMISSION_DENIED, "Set margin mode first"),
    }

    @classmethod
    def translate(cls, raw_error: dict, venue: str) -> UnifiedError | None:
        """Bybit API 错误码是数字，在 retCode 字段中"""
        ret_code = raw_error.get("retCode")
        ret_msg = raw_error.get("retMsg", "")

        if ret_code is not None:
            code_str = str(ret_code)
            if code_str in cls.ERROR_MAP:
                unified_code, default_msg = cls.ERROR_MAP[code_str]
                if unified_code is None:
                    return None
                return UnifiedError(
                    code=unified_code,
                    category=cls._get_category(unified_code),
                    venue=venue,
                    message=ret_msg or default_msg,
                    original_error=f"{ret_code}: {ret_msg}",
                    context={"raw_response": raw_error},
                )

        return super().translate(raw_error, venue)


# ── Kraken 错误翻译器 ────────────────────────────────────────────


class KrakenErrorTranslator(ErrorTranslator):
    """Kraken API 错误翻译器

    Kraken API 返回的错误格式:
    {
      "error": ["EAPI:Invalid key", "Invalid API key"],
      "result": {...}
    }
    """

    ERROR_MAP = {
        # 认证错误
        "EAPI:Invalid key": (UnifiedErrorCode.INVALID_API_KEY, "Invalid API key"),
        "EAPI:Invalid signature": (UnifiedErrorCode.INVALID_SIGNATURE, "Invalid signature"),
        "EAPI:Invalid nonce": (UnifiedErrorCode.EXPIRED_TIMESTAMP, "Invalid or duplicate nonce"),
        "EAPI:Invalid request": (UnifiedErrorCode.INVALID_PARAMETER, "Invalid request format"),
        "EAPI:Not allowed": (UnifiedErrorCode.PERMISSION_DENIED, "Request not permitted"),

        # 限流错误
        "EAPI:Rate limit exceeded": (UnifiedErrorCode.RATE_LIMIT_EXCEEDED, "Rate limit exceeded"),

        # 业务错误
        "EOrder:Insufficient funds": (UnifiedErrorCode.INSUFFICIENT_BALANCE, "Insufficient funds"),
        "EOrder:Invalid price": (UnifiedErrorCode.INVALID_PRICE, "Invalid price"),
        "EOrder:Unknown order": (UnifiedErrorCode.ORDER_NOT_FOUND, "Order not found"),
        "EOrder:Order already closed": (UnifiedErrorCode.ORDER_ALREADY_FILLED, "Order already closed"),
        "EOrder:Order would immediately trigger": (UnifiedErrorCode.INVALID_ORDER, "Order would immediately trigger"),
        "EOrder:Margin position too small": (UnifiedErrorCode.INSUFFICIENT_MARGIN, "Margin position too small"),

        # 参数错误
        "EInput:Invalid arguments": (UnifiedErrorCode.INVALID_PARAMETER, "Invalid arguments"),
        "EInput:Missing arguments": (UnifiedErrorCode.MISSING_PARAMETER, "Missing arguments"),
        "EInput:Unknown arguments": (UnifiedErrorCode.INVALID_PARAMETER, "Unknown arguments"),

        # 系统错误
        "EService:Unavailable": (UnifiedErrorCode.EXCHANGE_MAINTENANCE, "Service unavailable"),
        "EService:Market in cancel_only mode": (UnifiedErrorCode.MARKET_CLOSED, "Market in cancel only mode"),
        "EService:Duplicate post": (UnifiedErrorCode.DUPLICATE_ORDER, "Duplicate request"),
        "EService:Order timeout": (UnifiedErrorCode.UNSUPPORTED_OPERATION, "Order timeout"),

        # 其他错误
        "ETrade:Invalid user transaction": (UnifiedErrorCode.INVALID_ORDER, "Invalid user transaction"),
        "EAccount:Invalid asset": (UnifiedErrorCode.INVALID_SYMBOL, "Invalid asset"),
        "EGeneral:Unknown asset pair": (UnifiedErrorCode.INVALID_SYMBOL, "Unknown asset pair"),
    }

    @classmethod
    def translate(cls, raw_error: dict, venue: str) -> UnifiedError | None:
        """Kraken API 错误码在 error 数组中"""
        errors = raw_error.get("error", [])
        if not errors:
            return None  # No error

        # 取第一个错误
        error_msg = errors[0] if errors else "Unknown error"

        # 尝试匹配错误码
        for error_code, error_data in cls.ERROR_MAP.items():
            if error_code in error_msg:
                unified_code, default_msg = error_data
                if unified_code is None:
                    return None
                return UnifiedError(
                    code=unified_code,
                    category=cls._get_category(unified_code),
                    venue=venue,
                    message=error_msg,
                    original_error=error_msg,
                    context={"raw_response": raw_error},
                )

        # 如果没有匹配到，尝试解析错误码模式
        if error_msg.startswith("EAPI:"):
            return UnifiedError(
                code=UnifiedErrorCode.API_ERROR,
                category=ErrorCategory.API,
                venue=venue,
                message=error_msg,
                original_error=error_msg,
                context={"raw_response": raw_error},
            )
        elif error_msg.startswith("EOrder:"):
            return UnifiedError(
                code=UnifiedErrorCode.ORDER_ERROR,
                category=ErrorCategory.ORDER,
                venue=venue,
                message=error_msg,
                original_error=error_msg,
                context={"raw_response": raw_error},
            )
        elif error_msg.startswith("ETrade:"):
            return UnifiedError(
                code=UnifiedErrorCode.TRADE_ERROR,
                category=ErrorCategory.TRADE,
                venue=venue,
                message=error_msg,
                original_error=error_msg,
                context={"raw_response": raw_error},
            )
        elif error_msg.startswith("EAccount:"):
            return UnifiedError(
                code=UnifiedErrorCode.ACCOUNT_ERROR,
                category=ErrorCategory.ACCOUNT,
                venue=venue,
                message=error_msg,
                original_error=error_msg,
                context={"raw_response": raw_error},
            )

        # 默认错误
        return UnifiedError(
            code=UnifiedErrorCode.INTERNAL_ERROR,
            category=ErrorCategory.SYSTEM,
            venue=venue,
            message=error_msg,
            original_error=error_msg,
            context={"raw_response": raw_error},
        )


class BitgetErrorTranslator(ErrorTranslator):
    """Bitget API error translator."""

    ERROR_MAP = {
        # 认证错误
        "Invalid API key": (UnifiedErrorCode.INVALID_API_KEY, "Invalid API key"),
        "Invalid signature": (UnifiedErrorCode.INVALID_SIGNATURE, "Invalid signature"),
        "Invalid timestamp": (UnifiedErrorCode.EXPIRED_TIMESTAMP, "Invalid timestamp"),
        "Access denied": (UnifiedErrorCode.PERMISSION_DENIED, "Access denied"),

        # 限流错误
        "Too many requests": (UnifiedErrorCode.RATE_LIMIT_EXCEEDED, "Too many requests"),

        # 业务错误
        "Insufficient balance": (UnifiedErrorCode.INSUFFICIENT_BALANCE, "Insufficient balance"),
        "Invalid symbol": (UnifiedErrorCode.INVALID_SYMBOL, "Invalid symbol"),
        "Invalid price": (UnifiedErrorCode.INVALID_PRICE, "Invalid price"),
        "Invalid size": (UnifiedErrorCode.INVALID_VOLUME, "Invalid size"),
        "Order not found": (UnifiedErrorCode.ORDER_NOT_FOUND, "Order not found"),
        "Order already filled": (UnifiedErrorCode.ORDER_ALREADY_FILLED, "Order already filled"),
        "Order would immediately trigger": (UnifiedErrorCode.INVALID_ORDER, "Order would immediately trigger"),
        "Market closed": (UnifiedErrorCode.MARKET_CLOSED, "Market closed"),
        "Duplicate order": (UnifiedErrorCode.DUPLICATE_ORDER, "Duplicate order"),
        "Precision error": (UnifiedErrorCode.PRECISION_ERROR, "Precision error"),

        # 系统错误
        "Internal server error": (UnifiedErrorCode.INTERNAL_ERROR, "Internal server error"),
        "Service unavailable": (UnifiedErrorCode.EXCHANGE_MAINTENANCE, "Service unavailable"),
    }


class KuCoinErrorTranslator(ErrorTranslator):
    """KuCoin API error translator.

    KuCoin API response format:
    {
        "code": "400001",
        "msg": "Invalid KC-API-TIMESTAMP"
    }
    """

    ERROR_MAP = {
        # Success code
        "200000": (None, "Success"),

        # 认证错误 (4xxxx)
        "400001": (UnifiedErrorCode.INVALID_API_KEY, "Missing authentication headers"),
        "400002": (UnifiedErrorCode.EXPIRED_TIMESTAMP, "Invalid KC-API-TIMESTAMP"),
        "400003": (UnifiedErrorCode.INVALID_API_KEY, "Invalid KC-API-KEY"),
        "400004": (UnifiedErrorCode.INVALID_API_KEY, "Invalid KC-API-PASSPHRASE"),
        "400005": (UnifiedErrorCode.INVALID_SIGNATURE, "Invalid KC-API-SIGN"),
        "400006": (UnifiedErrorCode.PERMISSION_DENIED, "IP not in whitelist"),
        "400007": (UnifiedErrorCode.PERMISSION_DENIED, "Access denied"),
        "400008": (UnifiedErrorCode.INVALID_SIGNATURE, "Timestamp expired"),

        # 参数错误 (4xxxx)
        "400100": (UnifiedErrorCode.INVALID_PARAMETER, "Parameter error"),
        "400101": (UnifiedErrorCode.MISSING_PARAMETER, "Missing required parameter"),
        "400102": (UnifiedErrorCode.INVALID_PARAMETER, "Invalid parameter format"),
        "400103": (UnifiedErrorCode.INVALID_PARAMETER, "Parameter out of range"),

        # 业务错误 (4xxxx, 9xxxx)
        "411100": (UnifiedErrorCode.PERMISSION_DENIED, "User is frozen"),
        "900001": (UnifiedErrorCode.INVALID_SYMBOL, "Symbol not exists"),
        "900002": (UnifiedErrorCode.MARKET_CLOSED, "Market is closed"),
        "900003": (UnifiedErrorCode.INSUFFICIENT_BALANCE, "Insufficient balance"),
        "900004": (UnifiedErrorCode.INVALID_ORDER, "Invalid order"),
        "900005": (UnifiedErrorCode.ORDER_NOT_FOUND, "Order not found"),
        "900006": (UnifiedErrorCode.ORDER_ALREADY_FILLED, "Order already filled"),
        "900007": (UnifiedErrorCode.DUPLICATE_ORDER, "Duplicate client order ID"),
        "900008": (UnifiedErrorCode.INVALID_PRICE, "Price out of range"),
        "900009": (UnifiedErrorCode.INVALID_VOLUME, "Volume out of range"),
        "900010": (UnifiedErrorCode.PRECISION_ERROR, "Precision error"),
        "900011": (UnifiedErrorCode.INSUFFICIENT_BALANCE, "Insufficient funds"),

        # 限流错误
        "429000": (UnifiedErrorCode.RATE_LIMIT_EXCEEDED, "Too many requests"),
        "400200": (UnifiedErrorCode.RATE_LIMIT_EXCEEDED, "Request limit exceeded"),

        # 系统错误 (5xxxx)
        "500000": (UnifiedErrorCode.INTERNAL_ERROR, "Internal server error"),
        "500001": (UnifiedErrorCode.EXCHANGE_OVERLOADED, "Service overloaded"),
        "500002": (UnifiedErrorCode.EXCHANGE_MAINTENANCE, "Service maintenance"),
        "500003": (UnifiedErrorCode.NETWORK_TIMEOUT, "Request timeout"),

        # 其他错误
        "800001": (UnifiedErrorCode.UNSUPPORTED_OPERATION, "Operation not supported"),
        "800002": (UnifiedErrorCode.NOT_SUPPORTED, "Feature not available"),
    }

    @classmethod
    def translate(cls, raw_error: dict, venue: str) -> UnifiedError | None:
        """KuCoin API 错误码是字符串，在 code 字段中"""
        code = raw_error.get("code")
        msg = raw_error.get("msg", "")

        if code is not None:
            code_str = str(code)
            if code_str in cls.ERROR_MAP:
                unified_code, default_msg = cls.ERROR_MAP[code_str]
                if unified_code is None:
                    return None  # Success
                return UnifiedError(
                    code=unified_code,
                    category=cls._get_category(unified_code),
                    venue=venue,
                    message=msg or default_msg,
                    original_error=f"{code}: {msg}",
                    context={"raw_response": raw_error},
                )

        return super().translate(raw_error, venue)

    @classmethod
    def translate(cls, raw_error: dict, venue: str) -> UnifiedError | None:
        """Bitget API 错误码通常在 code 和 msg 字段中"""
        code = raw_error.get("code")
        msg = raw_error.get("msg", "")

        if not code and not msg:
            return None

        # 组合错误信息
        error_msg = msg
        if code:
            error_msg = f"{code}: {msg}"

        # 尝试匹配错误码
        for error_key, error_data in cls.ERROR_MAP.items():
            if error_key in error_msg:
                return UnifiedError(
                    code=error_data[0],
                    category=ErrorCategory.API,
                    venue=venue,
                    message=error_data[1],
                    original_error=error_msg,
                    context={"raw_response": raw_error},
                )

        # 如果没有匹配到，解析错误码
        if code:
            try:
                code_num = int(code)
                if code_num >= 10000:
                    return UnifiedError(
                        code=UnifiedErrorCode.INVALID_PARAMETER,
                        category=ErrorCategory.API,
                        venue=venue,
                        message=error_msg,
                        original_error=error_msg,
                        context={"raw_response": raw_error},
                    )
            except ValueError:
                pass

        # 默认错误
        return UnifiedError(
            code=UnifiedErrorCode.API_ERROR,
            category=ErrorCategory.API,
            venue=venue,
            message=error_msg,
            original_error=error_msg,
            context={"raw_response": raw_error},
        )


# ── Upbit 错误翻译器 ─────────────────────────────────────────────


class UpbitErrorTranslator(ErrorTranslator):
    """Upbit API 错误翻译器

    Upbit API 返回的错误格式:
    {
        "error": {
            "name": "INVALID_QUERY_PAYLOAD",
            "message": "Invalid query payload"
        }
    }
    """

    ERROR_MAP = {
        # 认证错误
        "UNAUTHORIZED": (UnifiedErrorCode.INVALID_API_KEY, "Authentication failed"),
        "EXPIRED_ACCESS_KEY": (UnifiedErrorCode.INVALID_API_KEY, "API key expired"),
        "INVALID_QUERY_PAYLOAD": (UnifiedErrorCode.INVALID_PARAMETER, "Invalid request parameters"),
        "JWT_VERIFICATION": (UnifiedErrorCode.INVALID_SIGNATURE, "JWT verification failed"),
        "NO_AUTHORIZATION_IP": (UnifiedErrorCode.PERMISSION_DENIED, "IP not authorized"),
        "NONCE_USED": (UnifiedErrorCode.EXPIRED_TIMESTAMP, "Nonce already used"),

        # 限流错误
        "TOO_MANY_REQUESTS": (UnifiedErrorCode.RATE_LIMIT_EXCEEDED, "Too many requests"),
        "REQUEST_LIMIT_EXCEEDED": (UnifiedErrorCode.RATE_LIMIT_EXCEEDED, "Request limit exceeded"),

        # 业务错误
        "INSUFFICIENT_FUNDS_BID": (UnifiedErrorCode.INSUFFICIENT_BALANCE, "Insufficient funds for buy order"),
        "INSUFFICIENT_FUNDS_ASK": (UnifiedErrorCode.INSUFFICIENT_BALANCE, "Insufficient funds for sell order"),
        "UNDER_MIN_TOTAL_BID": (UnifiedErrorCode.MINIMUM_NOT_MET, "Below minimum buy amount"),
        "UNDER_MIN_TOTAL_ASK": (UnifiedErrorCode.MINIMUM_NOT_MET, "Below minimum sell amount"),
        "INVALID_MARKET": (UnifiedErrorCode.INVALID_SYMBOL, "Invalid market symbol"),
        "INVALID_PRICE": (UnifiedErrorCode.INVALID_PRICE, "Invalid price"),
        "INVALID_VOLUME": (UnifiedErrorCode.INVALID_VOLUME, "Invalid volume"),
        "ORDER_NOT_FOUND": (UnifiedErrorCode.ORDER_NOT_FOUND, "Order not found"),
        "ORDER_ALREADY_FILLED": (UnifiedErrorCode.ORDER_ALREADY_FILLED, "Order already filled"),
        "ORDER_CANCELLED": (UnifiedErrorCode.ORDER_CANCELLED, "Order cancelled"),
        "DUPLICATE_ORDER": (UnifiedErrorCode.DUPLICATE_ORDER, "Duplicate order"),
        "MARKET_CLOSED": (UnifiedErrorCode.MARKET_CLOSED, "Market is closed"),
        "ORDER_TIMEOUT": (UnifiedErrorCode.ORDER_TIMEOUT, "Order timeout"),

        # 参数错误
        "MISSING_PARAMETER": (UnifiedErrorCode.MISSING_PARAMETER, "Missing required parameter"),
        "INVALID_PARAMETER": (UnifiedErrorCode.INVALID_PARAMETER, "Invalid parameter value"),
        "INVALID_PARAMETER_TYPE": (UnifiedErrorCode.INVALID_PARAMETER, "Invalid parameter type"),
        "PARAMETER_OUT_OF_RANGE": (UnifiedErrorCode.INVALID_PARAMETER, "Parameter out of range"),

        # 系统错误
        "INTERNAL_SERVER_ERROR": (UnifiedErrorCode.INTERNAL_ERROR, "Internal server error"),
        "SERVICE_TEMPORARILY_UNAVAILABLE": (UnifiedErrorCode.EXCHANGE_MAINTENANCE, "Service temporarily unavailable"),
        "DATABASE_ERROR": (UnifiedErrorCode.INTERNAL_ERROR, "Database error"),
        "NETWORK_ERROR": (UnifiedErrorCode.NETWORK_TIMEOUT, "Network error"),
    }

    @classmethod
    def translate(cls, raw_error: dict, venue: str) -> UnifiedError | None:
        """Upbit API 错误码通常在 error 对象中"""
        error_obj = raw_error.get("error", raw_error)

        if isinstance(error_obj, dict):
            error_name = error_obj.get("name", "")
            error_msg = error_obj.get("message", "")

            # 组合错误信息
            error_full_msg = f"{error_name}: {error_msg}" if error_name else error_msg

            # 尝试匹配错误码
            for error_key, error_data in cls.ERROR_MAP.items():
                if error_key in error_name or error_key in error_full_msg:
                    unified_code, default_msg = error_data
                    if unified_code is None:
                        return None  # 不是错误
                    return UnifiedError(
                        code=unified_code,
                        category=cls._get_category(unified_code),
                        venue=venue,
                        message=error_msg or default_msg,
                        original_error=error_full_msg,
                        context={"raw_response": raw_error},
                    )

            # 如果没有匹配到，根据错误名称推断
            if "UNAUTHORIZED" in error_name or "JWT" in error_name:
                return UnifiedError(
                    code=UnifiedErrorCode.INVALID_API_KEY,
                    category=ErrorCategory.AUTH,
                    venue=venue,
                    message=error_full_msg,
                    original_error=error_full_msg,
                    context={"raw_response": raw_error},
                )
            elif "RATE" in error_name or "TOO_MANY" in error_name:
                return UnifiedError(
                    code=UnifiedErrorCode.RATE_LIMIT_EXCEEDED,
                    category=ErrorCategory.RATE_LIMIT,
                    venue=venue,
                    message=error_full_msg,
                    original_error=error_full_msg,
                    context={"raw_response": raw_error},
                )
            elif "INSUFFICIENT" in error_name or "FUNDS" in error_name:
                return UnifiedError(
                    code=UnifiedErrorCode.INSUFFICIENT_BALANCE,
                    category=ErrorCategory.BUSINESS,
                    venue=venue,
                    message=error_full_msg,
                    original_error=error_full_msg,
                    context={"raw_response": raw_error},
                )
            elif "INVALID" in error_name:
                return UnifiedError(
                    code=UnifiedErrorCode.INVALID_PARAMETER,
                    category=ErrorCategory.VALIDATION,
                    venue=venue,
                    message=error_full_msg,
                    original_error=error_full_msg,
                    context={"raw_response": raw_error},
                )

            # 默认错误
            return UnifiedError(
                code=UnifiedErrorCode.INTERNAL_ERROR,
                category=ErrorCategory.SYSTEM,
                venue=venue,
                message=error_full_msg,
                original_error=error_full_msg,
                context={"raw_response": raw_error},
            )

        # 如果 error_obj 不是字典，使用父类的翻译方法
        return super().translate(raw_error, venue)


class BitfinexErrorTranslator(ErrorTranslator):
    """Bitfinex API 错误翻译器

    Bitfinex API 返回的错误格式:
    {
        "error": "ERR_UNKNOWN_ORDER",
        "message": "Unknown order"
    }
    """

    ERROR_MAP = {
        # 认证错误
        "ERR_UNAUTHENTICATED_API_KEY": (UnifiedErrorCode.INVALID_API_KEY, "Unauthenticated API key"),
        "ERR_INVALID_API_KEY": (UnifiedErrorCode.INVALID_API_KEY, "Invalid API key"),
        "ERR_INVALID_SIGNATURE": (UnifiedErrorCode.INVALID_SIGNATURE, "Invalid signature"),
        "ERR_PERMISSION_DENIED": (UnifiedErrorCode.PERMISSION_DENIED, "Permission denied"),

        # 限流错误
        "ERR_RATE_LIMIT": (UnifiedErrorCode.RATE_LIMIT_EXCEEDED, "Rate limit exceeded"),
        "ERR_TOO_MANY_REQUESTS": (UnifiedErrorCode.RATE_LIMIT_EXCEEDED, "Too many requests"),

        # 业务错误
        "ERR_UNKNOWN_ORDER": (UnifiedErrorCode.ORDER_NOT_FOUND, "Order not found"),
        "ERR_INVALID_ORDER": (UnifiedErrorCode.INVALID_ORDER, "Invalid order"),
        "ERR_INVALID_ORDER_TYPE": (UnifiedErrorCode.INVALID_ORDER, "Invalid order type"),
        "ERR_INSUFFICIENT_BALANCE": (UnifiedErrorCode.INSUFFICIENT_BALANCE, "Insufficient balance"),
        "ERR_ORDER_NOT_FOUND": (UnifiedErrorCode.ORDER_NOT_FOUND, "Order not found"),
        "ERR_ORDER_ALREADY_FILLED": (UnifiedErrorCode.ORDER_ALREADY_FILLED, "Order already filled"),
        "ERR_MARKET_CLOSED": (UnifiedErrorCode.MARKET_CLOSED, "Market is closed"),

        # 参数错误
        "ERR_INVALID_SYMBOL": (UnifiedErrorCode.INVALID_SYMBOL, "Invalid symbol"),
        "ERR_INVALID_PRICE": (UnifiedErrorCode.INVALID_PRICE, "Invalid price"),
        "ERR_INVALID_AMOUNT": (UnifiedErrorCode.INVALID_VOLUME, "Invalid amount"),
        "ERR_INVALID_PARAMETER": (UnifiedErrorCode.INVALID_PARAMETER, "Invalid parameter"),

        # 系统错误
        "ERR_SERVER": (UnifiedErrorCode.INTERNAL_ERROR, "Server error"),
        "ERR_SERVICE_UNAVAILABLE": (UnifiedErrorCode.EXCHANGE_MAINTENANCE, "Service unavailable"),
    }

    @classmethod
    def translate(cls, raw_error: dict, venue: str) -> UnifiedError | None:
        """Bitfinex API 错误码通常在 error 或 message 字段中"""
        error_msg = raw_error.get("error", raw_error.get("message", ""))
        error_str = str(error_msg) if error_msg else ""

        # 尝试匹配错误码
        for error_key, error_data in cls.ERROR_MAP.items():
            if error_key in error_str:
                unified_code, default_msg = error_data
                if unified_code is None:
                    return None  # 不是错误
                return UnifiedError(
                    code=unified_code,
                    category=cls._get_category(unified_code),
                    venue=venue,
                    message=error_str or default_msg,
                    original_error=error_str,
                    context={"raw_response": raw_error},
                )

        # 默认使用父类翻译方法
        return super().translate(raw_error, venue)
