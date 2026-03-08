"""
Kraken API 错误翻译器
"""

from bt_api_py.error import ErrorCategory, ErrorTranslator, UnifiedError, UnifiedErrorCode


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
        "EOrder:Order already closed": (
            UnifiedErrorCode.ORDER_ALREADY_FILLED,
            "Order already closed",
        ),
        "EOrder:Order would immediately trigger": (
            UnifiedErrorCode.INVALID_ORDER,
            "Order would immediately trigger",
        ),
        "EOrder:Margin position too small": (
            UnifiedErrorCode.INSUFFICIENT_MARGIN,
            "Margin position too small",
        ),
        # 参数错误
        "EInput:Invalid arguments": (UnifiedErrorCode.INVALID_PARAMETER, "Invalid arguments"),
        "EInput:Missing arguments": (UnifiedErrorCode.MISSING_PARAMETER, "Missing arguments"),
        "EInput:Unknown arguments": (UnifiedErrorCode.INVALID_PARAMETER, "Unknown arguments"),
        # 系统错误
        "EService:Unavailable": (UnifiedErrorCode.EXCHANGE_MAINTENANCE, "Service unavailable"),
        "EService:Market in cancel_only mode": (
            UnifiedErrorCode.MARKET_CLOSED,
            "Market in cancel only mode",
        ),
        "EService:Duplicate post": (UnifiedErrorCode.DUPLICATE_ORDER, "Duplicate request"),
        "EService:Order timeout": (UnifiedErrorCode.UNSUPPORTED_OPERATION, "Order timeout"),
        # 其他错误
        "ETrade:Invalid user transaction": (
            UnifiedErrorCode.INVALID_ORDER,
            "Invalid user transaction",
        ),
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
