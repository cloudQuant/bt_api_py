"""
Bitfinex API 错误翻译器
"""

from __future__ import annotations

from bt_api_py.error import ErrorTranslator, UnifiedError, UnifiedErrorCode


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
        "ERR_UNAUTHENTICATED_API_KEY": (
            UnifiedErrorCode.INVALID_API_KEY,
            "Unauthenticated API key",
        ),
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
