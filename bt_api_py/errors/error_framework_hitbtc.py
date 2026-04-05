"""
HitBTC Error Translation

Provides HitBTC-specific error code translation.
"""

from __future__ import annotations

from bt_api_py.error import ErrorCategory, ErrorTranslator, UnifiedError, UnifiedErrorCode


class HitBtcErrorTranslator(ErrorTranslator):
    """HitBTC API 错误翻译器"""

    ERROR_MAP = {
        # System errors
        10001: (UnifiedErrorCode.INVALID_API_KEY, "Authentication failed"),
        10020: (UnifiedErrorCode.PERMISSION_DENIED, "Permissions insufficient"),
        500: (UnifiedErrorCode.INTERNAL_ERROR, "Internal server error"),
        503: (UnifiedErrorCode.EXCHANGE_OVERLOADED, "Service unavailable"),
        # Parameter errors
        20001: (UnifiedErrorCode.INSUFFICIENT_BALANCE, "Balance insufficient"),
        20002: (UnifiedErrorCode.ORDER_NOT_FOUND, "Order does not exist"),
        20003: (UnifiedErrorCode.INVALID_VOLUME, "Invalid quantity"),
        20004: (UnifiedErrorCode.INVALID_PRICE, "Invalid price"),
        20005: (UnifiedErrorCode.INVALID_SYMBOL, "Symbol does not exist"),
        20006: (UnifiedErrorCode.INVALID_PARAMETER, "Invalid parameter"),
        20007: (UnifiedErrorCode.INVALID_ORDER, "Invalid order"),
        20008: (UnifiedErrorCode.EXCHANGE_OVERLOADED, "Order limit exceeded"),
        20009: (UnifiedErrorCode.UNSUPPORTED_OPERATION, "Trading is disabled"),
        20010: (UnifiedErrorCode.UNSUPPORTED_OPERATION, "Invalid order type"),
        20011: (UnifiedErrorCode.UNSUPPORTED_OPERATION, "Invalid time in force"),
        20012: (UnifiedErrorCode.DUPLICATE_ORDER, "Duplicate request"),
        20013: (UnifiedErrorCode.ORDER_TIMEOUT, "Order expired"),
        20014: (UnifiedErrorCode.UNSUPPORTED_OPERATION, "Rejected"),
        20015: (UnifiedErrorCode.UNSUPPORTED_OPERATION, "Pending cancellation"),
        20016: (UnifiedErrorCode.UNSUPPORTED_OPERATION, "Cancellation failed"),
        20017: (UnifiedErrorCode.UNSUPPORTED_OPERATION, "Pending replacement"),
        20018: (UnifiedErrorCode.UNSUPPORTED_OPERATION, "Replacement failed"),
        20019: (UnifiedErrorCode.INVALID_API_KEY, "Invalid API key"),
        20020: (UnifiedErrorCode.PERMISSION_DENIED, "Access denied"),
        # Trading errors
        30001: (UnifiedErrorCode.INVALID_VOLUME, "Invalid amount"),
        30002: (UnifiedErrorCode.INVALID_PRICE, "Invalid price"),
        30003: (UnifiedErrorCode.INVALID_SYMBOL, "Invalid symbol"),
        30004: (UnifiedErrorCode.UNSUPPORTED_OPERATION, "Disabled trading"),
        30005: (UnifiedErrorCode.DUPLICATE_ORDER, "Duplicate client order id"),
        30006: (UnifiedErrorCode.UNSUPPORTED_OPERATION, "Cannot modify closed order"),
        30007: (UnifiedErrorCode.ORDER_NOT_FOUND, "Order not found"),
        30008: (UnifiedErrorCode.INSUFFICIENT_BALANCE, "Insufficient balance"),
        30009: (UnifiedErrorCode.UNSUPPORTED_OPERATION, "Invalid order type"),
        30010: (UnifiedErrorCode.UNSUPPORTED_OPERATION, "Invalid time in force"),
        30011: (UnifiedErrorCode.INVALID_API_KEY, "Invalid API key"),
        30012: (UnifiedErrorCode.PERMISSION_DENIED, "Permission denied"),
        30013: (UnifiedErrorCode.UNSUPPORTED_OPERATION, "Bad request"),
        30014: (UnifiedErrorCode.EXCHANGE_OVERLOADED, "Too many orders"),
        30015: (UnifiedErrorCode.UNSUPPORTED_OPERATION, "Rejected"),
        30016: (UnifiedErrorCode.UNSUPPORTED_OPERATION, "Pending cancellation"),
        30017: (UnifiedErrorCode.UNSUPPORTED_OPERATION, "Cancellation failed"),
        30018: (UnifiedErrorCode.UNSUPPORTED_OPERATION, "Pending replacement"),
        30019: (UnifiedErrorCode.UNSUPPORTED_OPERATION, "Replacement failed"),
        30020: (UnifiedErrorCode.INVALID_API_KEY, "Invalid API key"),
        30021: (UnifiedErrorCode.PERMISSION_DENIED, "Access denied"),
        # Withdrawal errors
        40001: (UnifiedErrorCode.UNSUPPORTED_OPERATION, "Invalid address"),
        40002: (UnifiedErrorCode.INSUFFICIENT_BALANCE, "Insufficient funds"),
        40003: (UnifiedErrorCode.UNSUPPORTED_OPERATION, "Withdrawal disabled"),
        40004: (UnifiedErrorCode.UNSUPPORTED_OPERATION, "Invalid currency"),
        40005: (UnifiedErrorCode.INVALID_VOLUME, "Invalid amount"),
        40006: (UnifiedErrorCode.UNSUPPORTED_OPERATION, "Invalid network"),
        40007: (UnifiedErrorCode.UNSUPPORTED_OPERATION, "Pending withdrawal"),
        40008: (UnifiedErrorCode.UNSUPPORTED_OPERATION, "Withdrawal failed"),
        40009: (UnifiedErrorCode.INVALID_API_KEY, "Invalid API key"),
        40010: (UnifiedErrorCode.PERMISSION_DENIED, "Permission denied"),
        # Rate limit errors
        429: (UnifiedErrorCode.RATE_LIMIT_EXCEEDED, "Too many requests"),
    }

    @classmethod
    def translate(cls, raw_error: dict, venue: str) -> UnifiedError | None:
        """将原始错误转换为统一错误

        :param raw_error: 包含错误信息的字典 (code, message, description 等)
        :param venue: 场所名称
        :return: UnifiedError
        """
        # HitBTC error format: {"error": {"code": 20001, "message": "Balance insufficient", "description": "..."}}
        error = raw_error.get("error", raw_error)
        code = error.get("code")
        msg = error.get("message", error.get("description", ""))
        status = error.get("status")

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
