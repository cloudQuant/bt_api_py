"""
HTX Error Translator

Translate HTX (Huobi) API error codes to unified error codes
"""

from bt_api_py.error import ErrorTranslator


class HtxErrorTranslator(ErrorTranslator):
    """
    HTX Error Translation

    HTX uses 'status' field in responses:
    - 'ok': Success
    - 'error': Error (with 'err-code' and 'err-msg' fields)

    Common error codes from HTX API documentation.
    """

    ERROR_MAP = {
        # Authentication errors
        "api-signature-not-valid": (1002, "Invalid signature"),
        "api-signature-check-failed": (1002, "Signature check failed"),
        "api-key-invalid": (1001, "Invalid API key"),
        "api-key-expired": (1007, "API key expired"),
        "api-key-ip-invalid": (1008, "IP not in whitelist"),
        "api-key-permission-invalid": (1004, "Permission denied"),

        # Account errors
        "account-frozen-balance-insufficient-error": (1008, "Insufficient balance"),
        "account-api-trading-banned": (1011, "API trading banned for account"),
        "account-invalid": (1015, "Invalid account"),

        # Order errors
        "order-orderstate-error": (1015, "Invalid order state"),
        "order-queryorder-invalid": (1006, "Order not found"),
        "order-update-error": (1017, "Order update failed"),
        "order-place-error": (1016, "Order placement failed"),
        "order-cancel-error": (1017, "Order cancellation failed"),

        # Trading errors
        "base-symbol-error": (1007, "Invalid trading pair"),
        "base-amount-error": (1018, "Invalid amount"),
        "base-price-error": (1018, "Invalid price"),
        "base-symbol-trading-banned": (1011, "Trading disabled for symbol"),

        # System errors
        "gateway-internal-error": (5003, "Internal server error"),
        "system-busy": (5002, "System busy"),
        "maintenance": (5001, "System under maintenance"),

        # Rate limiting
        "too-many-requests": (3001, "Rate limit exceeded"),
    }

    @classmethod
    def translate(cls, raw_error, venue: str = "HTX"):
        """Translate HTX error response to unified error

        Args:
            raw_error: Raw error response from HTX
            venue: Exchange venue name (default: "HTX")

        Returns:
            UnifiedError or None
        """
        if isinstance(raw_error, str):
            # String error messages
            return cls.translate_string_error(raw_error, venue)
        elif isinstance(raw_error, dict):
            # Dictionary error responses
            return cls.translate_dict_error(raw_error, venue)

        # Fallback: treat as generic error
        return cls._translate_fallback(raw_error, venue)

    @classmethod
    def translate_string_error(cls, error_msg: str, venue: str):
        """Translate string error messages"""
        # Common error patterns
        error_lower = error_msg.lower()

        if "invalid api key" in error_lower:
            return cls._create_unified_error(1001, "Invalid API key", venue, error_msg)
        elif "invalid signature" in error_lower or "signature not valid" in error_lower:
            return cls._create_unified_error(1002, "Invalid signature", venue, error_msg)
        elif "rate limit" in error_lower or "too many requests" in error_lower:
            return cls._create_unified_error(3001, "Rate limit exceeded", venue, error_msg)
        elif "insufficient balance" in error_lower:
            return cls._create_unified_error(1008, "Insufficient balance", venue, error_msg)
        elif "order not found" in error_lower:
            return cls._create_unified_error(1006, "Order not found", venue, error_msg)
        elif "symbol" in error_lower and ("invalid" in error_lower or "not found" in error_lower):
            return cls._create_unified_error(1007, "Invalid trading pair", venue, error_msg)
        elif "permission" in error_lower:
            return cls._create_unified_error(1004, "Permission denied", venue, error_msg)
        elif "maintenance" in error_lower:
            return cls._create_unified_error(5001, "System under maintenance", venue, error_msg)

        # Fallback for unknown string errors
        return cls._create_unified_error(5003, error_msg, venue, error_msg)

    @classmethod
    def translate_dict_error(cls, error_dict: dict, venue: str):
        """Translate dictionary error responses

        HTX error format:
        {
            "status": "error",
            "err-code": "error-code",
            "err-msg": "error message"
        }
        """
        # Check status first
        status = error_dict.get("status", "")
        if status == "ok":
            return None  # Success

        # Extract error code and message
        err_code = error_dict.get("err-code", "")
        err_msg = error_dict.get("err-msg", "")

        if err_code in cls.ERROR_MAP:
            unified_code, default_msg = cls.ERROR_MAP[err_code]
            return cls._create_unified_error(
                unified_code,
                err_msg or default_msg,
                venue,
                f"{err_code}: {err_msg}"
            )

        # Check for specific error patterns in message
        if err_msg:
            return cls.translate_string_error(err_msg, venue)

        # Fallback for unknown error codes
        return cls._create_unified_error(
            5003,
            err_msg or f"Unknown error code: {err_code}",
            venue,
            f"{err_code}: {err_msg}"
        )

    @classmethod
    def _create_unified_error(cls, code, message, venue, original_error):
        """Create a unified error instance"""
        from bt_api_py.error import UnifiedError, UnifiedErrorCode, ErrorCategory

        # Map HTX codes to unified codes
        code_mapping = {
            1001: UnifiedErrorCode.INVALID_API_KEY,
            1002: UnifiedErrorCode.INVALID_SIGNATURE,
            1004: UnifiedErrorCode.PERMISSION_DENIED,
            1006: UnifiedErrorCode.ORDER_NOT_FOUND,
            1007: UnifiedErrorCode.INVALID_SYMBOL,
            1008: UnifiedErrorCode.INSUFFICIENT_BALANCE,
            1011: UnifiedErrorCode.MARKET_CLOSED,
            1015: UnifiedErrorCode.INVALID_ORDER,
            1016: UnifiedErrorCode.ORDER_NOT_FOUND,
            1017: UnifiedErrorCode.ORDER_NOT_FOUND,
            1018: UnifiedErrorCode.INVALID_PARAMETER,
            3001: UnifiedErrorCode.RATE_LIMIT_EXCEEDED,
            5001: UnifiedErrorCode.EXCHANGE_MAINTENANCE,
            5002: UnifiedErrorCode.EXCHANGE_OVERLOADED,
            5003: UnifiedErrorCode.INTERNAL_ERROR,
        }

        unified_code = code_mapping.get(code, UnifiedErrorCode.INTERNAL_ERROR)

        # Determine category
        if 1000 <= code < 2000:
            category = ErrorCategory.AUTH
        elif 2000 <= code < 3000:
            category = ErrorCategory.BUSINESS
        elif 3000 <= code < 4000:
            category = ErrorCategory.RATE_LIMIT
        elif 5000 <= code < 6000:
            category = ErrorCategory.SYSTEM
        else:
            category = ErrorCategory.BUSINESS

        return UnifiedError(
            code=unified_code,
            category=category,
            venue=venue,
            message=message,
            original_error=original_error,
            context={"raw_response": original_error}
        )

    @classmethod
    def _translate_fallback(cls, raw_error, venue: str):
        """Fallback translation for unknown error formats"""
        return cls._create_unified_error(
            UnifiedErrorCode.INTERNAL_ERROR,
            "Unknown error",
            venue,
            str(raw_error)
        )
