"""
Gemini Error Translator

Translate Gemini API error codes to unified error codes
"""

from bt_api_py.error import ErrorTranslator


class GeminiErrorTranslator(ErrorTranslator):
    """
    Gemini Error Translation

    Gemini error responses are typically:
    - 200: Success
    - 400: Bad Request
    - 401: Unauthorized
    - 403: Forbidden
    - 404: Not Found
    - 406: Not Acceptable
    - 429: Too Many Requests
    - 500: Internal Server Error
    - 503: Service Unavailable

    Business errors come in the format:
    {
        "result": "error",
        "reason": "reason",
        "message": "message"
    }
    """

    ERROR_MAP = {
        # Authentication errors
        "InvalidApiKey": (1001, "Invalid API key"),
        "ApikeyNotValid": (1001, "API key not valid"),
        "InvalidNonce": (1003, "Invalid nonce"),
        "SignatureDoesNotMatch": (1002, "Signature does not match"),
        "NonceTooLow": (1003, "Nonce too low"),
        "NonceTooHigh": (1003, "Nonce too high"),
        "NonceAlreadyUsed": (1003, "Nonce already used"),

        # Order errors
        "OrderNotFound": (1006, "Order not found"),
        "OrderNotOpen": (1006, "Order is not open"),
        "CancelFailedOrderNotOpen": (1006, "Failed to cancel: order not open"),
        "CancelFailedOrderNotFound": (1006, "Failed to cancel: order not found"),
        "CancelFailed": (1007, "Order cancellation failed"),
        "OrderPlacementFailed": (1016, "Order placement failed"),
        "NewOrderRejected": (1016, "New order rejected"),
        "CancelRejected": (1017, "Cancel order rejected"),
        "AmendRejected": (1018, "Amend order rejected"),

        # Trading errors
        "InvalidSymbol": (1007, "Invalid symbol"),
        "InvalidPrice": (1018, "Invalid price"),
        "InvalidQuantity": (1018, "Invalid quantity"),
        "InvalidSide": (1019, "Invalid side"),
        "InvalidOrderType": (1020, "Invalid order type"),
        "InsufficientFunds": (1008, "Insufficient funds"),
        "InsufficientFundsForOrder": (1008, "Insufficient funds for order"),
        "InsufficientFundsForCancel": (1008, "Insufficient funds for cancel"),
        "InsufficientFundsForAmend": (1008, "Insufficient funds for amend"),
        "SymbolNotTraded": (1021, "Symbol not traded"),
        "SymbolNotAvailable": (1021, "Symbol not available"),
        "InactiveSymbol": (1021, "Symbol is inactive"),
        "MarketClosed": (1022, "Market closed"),
        "MarketNotOpen": (1022, "Market not open"),
        "TradingHalted": (1022, "Trading halted"),
        "MaintenanceMode": (1023, "Maintenance mode"),
        "Suspended": (1023, "Trading suspended"),

        # Rate limiting
        "RateLimit": (3001, "Rate limit exceeded"),
        "TooManyRequests": (3001, "Too many requests"),
        "ApiLimitExceeded": (3001, "API limit exceeded"),
        "RequestTimeout": (3002, "Request timeout"),
        "RequestTimeoutOnPost": (3002, "Request timeout on POST"),
        "Timeout": (3002, "Timeout"),

        # Account errors
        "InsufficientExchangeBalance": (1008, "Insufficient exchange balance"),
        "InsufficientAvailableBalance": (1008, "Insufficient available balance"),
        "InsufficientAvailableForWithdrawal": (1008, "Insufficient available for withdrawal"),
        "WithdrawalFailed": (1024, "Withdrawal failed"),
        "DepositFailed": (1025, "Deposit failed"),
        "TransferFailed": (1026, "Transfer failed"),
        "AccountSuspended": (1027, "Account suspended"),
        "AccountFrozen": (1027, "Account frozen"),
        "AccountRestricted": (1027, "Account restricted"),

        # System errors
        "InternalServerError": (5003, "Internal server error"),
        "ServiceUnavailable": (5001, "Service unavailable"),
        "GatewayTimeout": (3002, "Gateway timeout"),
        "DownForMaintenance": (5001, "Down for maintenance"),
        "Overloaded": (5002, "System overloaded"),
        "Busy": (5002, "System busy"),
        "TooBusy": (5002, "Too busy"),
        "TemporarilyUnavailable": (5001, "Temporarily unavailable"),

        # Validation errors
        "ValidationError": (1018, "Validation error"),
        "InvalidParameter": (1018, "Invalid parameter"),
        "MissingParameter": (1018, "Missing parameter"),
        "InvalidValue": (1018, "Invalid value"),
        "InvalidFormat": (1018, "Invalid format"),
        "InvalidJson": (1018, "Invalid JSON"),
        "InvalidTimestamp": (1018, "Invalid timestamp"),
        "InvalidSignatureAlgorithm": (1018, "Invalid signature algorithm"),
        "InvalidSignature": (1002, "Invalid signature"),

        # Network errors
        "NetworkError": (1001, "Network error"),
        "ConnectionError": (1001, "Connection error"),
        "HostUnreachable": (1001, "Host unreachable"),
        "ConnectionRefused": (1001, "Connection refused"),
        "ConnectionReset": (1001, "Connection reset"),
        "ConnectionTimeout": (3002, "Connection timeout"),
        "ReadTimeout": (3002, "Read timeout"),
        "WriteTimeout": (3002, "Write timeout"),
        "DnsResolutionError": (1001, "DNS resolution error"),
        "SslError": (1001, "SSL error"),
        "CertificateError": (1001, "Certificate error"),
        "ProxyError": (1001, "Proxy error"),
        "TooManyRedirects": (1001, "Too many redirects"),
    }

    @classmethod
    def translate(cls, raw_error, venue: str = "Gemini"):
        """Translate Gemini error response to unified error

        Args:
            raw_error: Raw error response from Gemini
            venue: Exchange venue name (default: "Gemini")

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
        error_lower = error_msg.lower()

        if "invalid api key" in error_lower:
            return cls._create_unified_error(1001, "Invalid API key", venue, error_msg)
        elif "invalid signature" in error_lower or "signature does not match" in error_lower:
            return cls._create_unified_error(1002, "Invalid signature", venue, error_msg)
        elif "invalid nonce" in error_lower or "nonce too" in error_lower:
            return cls._create_unified_error(1003, "Invalid nonce", venue, error_msg)
        elif "rate limit" in error_lower or "too many requests" in error_lower:
            return cls._create_unified_error(3001, "Rate limit exceeded", venue, error_msg)
        elif "insufficient funds" in error_lower:
            return cls._create_unified_error(1008, "Insufficient funds", venue, error_msg)
        elif "invalid symbol" in error_lower or "symbol not" in error_lower:
            return cls._create_unified_error(1007, "Invalid symbol", venue, error_msg)
        elif "order not found" in error_lower:
            return cls._create_unified_error(1006, "Order not found", venue, error_msg)
        elif "invalid price" in error_lower:
            return cls._create_unified_error(1018, "Invalid price", venue, error_msg)
        elif "invalid quantity" in error_lower:
            return cls._create_unified_error(1018, "Invalid quantity", venue, error_msg)
        elif "market closed" in error_lower or "market not open" in error_lower:
            return cls._create_unified_error(1022, "Market closed", venue, error_msg)
        elif "maintenance" in error_lower or "down for maintenance" in error_lower:
            return cls._create_unified_error(5001, "Maintenance mode", venue, error_msg)
        elif "internal error" in error_lower or "server error" in error_lower:
            return cls._create_unified_error(5003, "Internal server error", venue, error_msg)
        elif "timeout" in error_lower:
            return cls._create_unified_error(3002, "Request timeout", venue, error_msg)
        elif "connection" in error_lower:
            return cls._create_unified_error(1001, "Connection error", venue, error_msg)
        elif "validation" in error_lower or "invalid parameter" in error_lower:
            return cls._create_unified_error(1018, "Validation error", venue, error_msg)

        # Fallback for unknown string errors
        return cls._create_unified_error(5003, error_msg, venue, error_msg)

    @classmethod
    def translate_dict_error(cls, error_dict: dict, venue: str):
        """Translate dictionary error responses

        Gemini error format:
        {
            "result": "error",
            "reason": "reason",
            "message": "message"
        }
        """
        # Check if this is an error response
        result = error_dict.get("result", "")
        if result != "error":
            return None  # Success

        # Extract error reason and message
        reason = error_dict.get("reason", "")
        message = error_dict.get("message", "")

        # Use reason as primary error code
        if reason in cls.ERROR_MAP:
            unified_code, default_msg = cls.ERROR_MAP[reason]
            return cls._create_unified_error(
                unified_code,
                message or default_msg,
                venue,
                f"{reason}: {message}"
            )

        # Use message for pattern matching
        if message:
            return cls.translate_string_error(message, venue)

        # Fallback for unknown error codes
        return cls._create_unified_error(
            5003,
            message or f"Unknown error reason: {reason}",
            venue,
            f"{reason}: {message}"
        )

    @classmethod
    def _create_unified_error(cls, code, message, venue, original_error):
        """Create a unified error instance"""
        from bt_api_py.error import UnifiedError, UnifiedErrorCode, ErrorCategory

        # Map Gemini codes to unified codes
        code_mapping = {
            1001: UnifiedErrorCode.INVALID_API_KEY,
            1002: UnifiedErrorCode.INVALID_SIGNATURE,
            1003: UnifiedErrorCode.EXPIRED_TIMESTAMP,
            1004: UnifiedErrorCode.PERMISSION_DENIED,
            1006: UnifiedErrorCode.ORDER_NOT_FOUND,
            1007: UnifiedErrorCode.INVALID_SYMBOL,
            1008: UnifiedErrorCode.INSUFFICIENT_BALANCE,
            1016: UnifiedErrorCode.ORDER_NOT_FOUND,
            1017: UnifiedErrorCode.ORDER_CANCEL_FAILED,
            1018: UnifiedErrorCode.INVALID_PARAMETER,
            1019: UnifiedErrorCode.INVALID_SIDE,
            1020: UnifiedErrorCode.INVALID_ORDER_TYPE,
            1021: UnifiedErrorCode.MARKET_CLOSED,
            1022: UnifiedErrorCode.MARKET_CLOSED,
            1023: UnifiedErrorCode.EXCHANGE_MAINTENANCE,
            1024: UnifiedErrorCode.WITHDRAWAL_FAILED,
            1025: UnifiedErrorCode.DEPOSIT_FAILED,
            1026: UnifiedErrorCode.TRANSFER_FAILED,
            1027: UnifiedErrorCode.ACCOUNT_SUSPENDED,
            3001: UnifiedErrorCode.RATE_LIMIT_EXCEEDED,
            3002: UnifiedErrorCode.NETWORK_TIMEOUT,
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