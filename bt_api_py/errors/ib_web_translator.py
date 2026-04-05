"""
IB Web API 错误翻译器
"""

from __future__ import annotations

from bt_api_py.error import ErrorTranslator, UnifiedError, UnifiedErrorCode


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
