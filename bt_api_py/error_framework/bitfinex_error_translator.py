"""
Bitfinex Error Translator

Translate Bitfinex-specific error codes to unified error codes
"""

from bt_api_py.error_framework import ErrorTranslator


class BitfinexErrorTranslator(ErrorTranslator):
    """
    Bitfinex Error Translation

    Bitfinex error codes are primarily HTTP status codes with some custom messages.
    Common error patterns:
    - 400: Bad request
    - 401: Authentication failed
    - 403: Permission denied
    - 429: Rate limit exceeded
    - 500: Internal server error
    - 503: Service unavailable
    """

    ERROR_MAP = {
        # Authentication errors
        10100: (None, "Success"),  # Success (treated as no error)
        10114: (1001, "Invalid API key"),  # Invalid API key
        10115: (1002, "Invalid signature"),  # Invalid signature
        10116: (1003, "Expired nonce"),  # Expired nonce
        10117: (1004, "Permission denied"),  # Permission denied
        10118: (1005, "Invalid order"),  # Invalid order
        10119: (1006, "Order not found"),  # Order not found
        10120: (1007, "Symbol not found"),  # Symbol not found
        10121: (1008, "Insufficient balance"),  # Insufficient balance
        10122: (1009, "Duplicate order"),  # Duplicate order
        10123: (1010, "Order filled"),  # Order already filled
        10124: (1011, "Order canceled"),  # Order canceled
        10125: (1012, "Order expired"),  # Order expired
        10126: (1013, "Order would trigger immediately"),  # Order would trigger immediately
        10127: (1014, "Only one order at a time"),  # Only one order at a time
        10128: (1015, "Insufficient margin"),  # Insufficient margin
        10129: (1016, "Insufficient funds"),  # Insufficient funds
        10130: (1017, "Insufficient credit"),  # Insufficient credit
        10131: (1018, "Withdrawal failed"),  # Withdrawal failed
        10132: (1019, "Deposit failed"),  # Deposit failed
        10133: (1020, "Transfer failed"),  # Transfer failed

        # Rate limiting
        11000: (3001, "Rate limit exceeded"),  # Rate limit exceeded
        11001: (3002, "IP banned"),  # IP banned
        11002: (3003, "Too many requests"),  # Too many requests
        11003: (3004, "Request timeout"),  # Request timeout

        # System errors
        20000: (5001, "Maintenance mode"),  # Maintenance mode
        20001: (5002, "Overloaded"),  # System overloaded
        20002: (5003, "Internal error"),  # Internal error
        20003: (5004, "Service unavailable"),  # Service unavailable
        20004: (5005, "Gateway timeout"),  # Gateway timeout
        20005: (5006, "Bad gateway"),  # Bad gateway

        # API specific
        50000: (5003, "Unknown error"),  # Unknown error
        50001: (5003, "Error"),  # Generic error
        50002: (5003, "Invalid request"),  # Invalid request
        50003: (5003, "Forbidden"),  # Forbidden
        50004: (5003, "Not found"),  # Not found
        50005: (5003, "Method not allowed"),  # Method not allowed
        50006: (5003, "Not acceptable"),  # Not acceptable
        50007: (5003, "Conflict"),  # Conflict
        50008: (5003, "Gone"),  # Gone
        50009: (5003, "Length required"),  # Length required
        50010: (5003, "Precondition failed"),  # Precondition failed
        50011: (5003, "Request entity too large"),  # Request entity too large
        50012: (5003, "Request uri too long"),  # Request URI too long
        50013: (5003, "Unsupported media type"),  # Unsupported media type
        50014: (5003, "Requested range not satisfiable"),  # Requested range not satisfiable
        50015: (5003, "Expectation failed"),  # Expectation failed
        50016: (5003, "Unprocessable entity"),  # Unprocessable entity
        50017: (5003, "Locked"),  # Locked
        50018: (5003, "Failed dependency"),  # Failed dependency
        50019: (5003, "Upgrade required"),  # Upgrade required
        50020: (5003, "Precondition required"),  # Precondition required
        50021: (5003, "Too many requests"),  # Too many requests
        50022: (5003, "Internal server error"),  # Internal server error
        50023: (5003, "Not implemented"),  # Not implemented
        50024: (5003, "Bad gateway"),  # Bad gateway
        50025: (5003, "Service unavailable"),  # Service unavailable
        50026: (5003, "Gateway timeout"),  # Gateway timeout
        50027: (5003, "HTTP version not supported"),  # HTTP version not supported
    }

    @classmethod
    def translate(cls, raw_error, venue: str = "BITFINEX"):
        """Translate Bitfinex error response to unified error

        Args:
            raw_error: Raw error response from Bitfinex
            venue: Exchange venue name (default: "BITFINEX")

        Returns:
            UnifiedError or None
        """
        if isinstance(raw_error, str):
            # String error messages (e.g., "error message")
            return cls.translate_string_error(raw_error, venue)
        elif isinstance(raw_error, dict):
            # Dictionary error responses
            return cls.translate_dict_error(raw_error, venue)
        elif isinstance(raw_error, list) and len(raw_error) > 0 and raw_error[0] == 'error':
            # List error format: ['error', code, message]
            if len(raw_error) >= 3:
                code = raw_error[1]
                msg = raw_error[2]
                return cls.translate_from_code(code, msg, venue)

        # Fallback: treat as generic error
        return cls._translate_fallback(raw_error, venue)

    @classmethod
    def translate_string_error(cls, error_msg: str, venue: str):
        """Translate string error messages"""
        # Common error patterns
        error_lower = error_msg.lower()

        if "invalid api key" in error_lower:
            return cls._create_unified_error(10114, "Invalid API key", venue, error_msg)
        elif "invalid signature" in error_lower:
            return cls._create_unified_error(10115, "Invalid signature", venue, error_msg)
        elif "rate limit" in error_lower or "too many requests" in error_lower:
            return cls._create_unified_error(11000, "Rate limit exceeded", venue, error_msg)
        elif "insufficient balance" in error_lower:
            return cls._create_unified_error(10121, "Insufficient balance", venue, error_msg)
        elif "order not found" in error_lower:
            return cls._create_unified_error(10119, "Order not found", venue, error_msg)
        elif "symbol" in error_lower and ("not found" in error_lower or "invalid" in error_lower):
            return cls._create_unified_error(10120, "Symbol not found", venue, error_msg)

        # Fallback for unknown string errors
        return cls._create_unified_error(50000, error_msg, venue, error_msg)

    @classmethod
    def translate_dict_error(cls, error_dict: dict, venue: str):
        """Translate dictionary error responses"""
        # Common keys: 'error', 'message', 'code', 'reason'
        error_msg = error_dict.get('message', error_dict.get('msg', error_dict.get('reason', '')))
        code = error_dict.get('code')

        if code:
            return cls.translate_from_code(code, error_msg, venue)

        # Check for specific error patterns
        error_lower = error_msg.lower() if error_msg else ''

        if "authentication" in error_lower:
            return cls._create_unified_error(10114, "Authentication failed", venue, error_msg)
        elif "permission" in error_lower:
            return cls._create_unified_error(10117, "Permission denied", venue, error_msg)
        elif "maintenance" in error_lower:
            return cls._create_unified_error(20000, "Maintenance mode", venue, error_msg)

        # Fallback
        return cls._create_unified_error(50001, error_msg or "Unknown error", venue, str(error_dict))

    @classmethod
    def translate_from_code(cls, code, message: str, venue: str):
        """Translate from error code"""
        if code in cls.ERROR_MAP:
            unified_code, default_msg = cls.ERROR_MAP[code]
            if unified_code is None:
                return None  # Success case
            return cls._create_unified_error(unified_code, message or default_msg, venue, f"{code}: {message}")

        # Default handling for unknown codes
        return cls._create_unified_error(50000, message or f"Unknown error code {code}", venue, f"{code}: {message}")

    @classmethod
    def _create_unified_error(cls, code, message, venue, original_error):
        """Create a unified error instance"""
        from bt_api_py.error_framework import UnifiedError, UnifiedErrorCode, ErrorCategory

        # Map Bitfinex codes to unified codes
        code_mapping = {
            1001: UnifiedErrorCode.INVALID_API_KEY,
            1002: UnifiedErrorCode.INVALID_SIGNATURE,
            1003: UnifiedErrorCode.EXPIRED_TIMESTAMP,
            1004: UnifiedErrorCode.PERMISSION_DENIED,
            1005: UnifiedErrorCode.INVALID_ORDER,
            1006: UnifiedErrorCode.ORDER_NOT_FOUND,
            1007: UnifiedErrorCode.INVALID_SYMBOL,
            1008: UnifiedErrorCode.INSUFFICIENT_BALANCE,
            1009: UnifiedErrorCode.DUPLICATE_ORDER,
            1010: UnifiedErrorCode.ORDER_ALREADY_FILLED,
            1011: UnifiedErrorCode.ORDER_CANCELLED,
            1012: UnifiedErrorCode.EXPIRED_ORDER,
            1013: UnifiedErrorCode.UNSUPPORTED_OPERATION,
            1014: UnifiedErrorCode.INVALID_ORDER,
            1015: UnifiedErrorCode.INSUFFICIENT_MARGIN,
            1016: UnifiedErrorCode.INSUFFICIENT_FUNDS,
            1017: UnifiedErrorCode.INSUFFICIENT_CREDIT,
            1018: UnifiedErrorCode.WITHDRAWAL_FAILED,
            1019: UnifiedErrorCode.DEPOSIT_FAILED,
            1020: UnifiedErrorCode.TRANSFER_FAILED,
            3001: UnifiedErrorCode.RATE_LIMIT_EXCEEDED,
            3002: UnifiedErrorCode.IP_BANNED,
            3003: UnifiedErrorCode.TOO_MANY_REQUESTS,
            3004: UnifiedErrorCode.NETWORK_TIMEOUT,
            5001: UnifiedErrorCode.EXCHANGE_MAINTENANCE,
            5002: UnifiedErrorCode.EXCHANGE_OVERLOADED,
            5003: UnifiedErrorCode.INTERNAL_ERROR,
            5004: UnifiedErrorCode.EXCHANGE_MAINTENANCE,
            5005: UnifiedErrorCode.NETWORK_TIMEOUT,
            5006: UnifiedErrorCode.EXCHANGE_OVERLOADED,
        }

        unified_code = code_mapping.get(code, UnifiedErrorCode.INTERNAL_ERROR)

        # Determine category
        if 1000 <= code < 2000:
            category = ErrorCategory.AUTH
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