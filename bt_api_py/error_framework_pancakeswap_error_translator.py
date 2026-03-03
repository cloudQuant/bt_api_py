"""
PancakeSwap Error Translator

Translate PancakeSwap-specific errors to unified error codes
"""

from bt_api_py.error_framework import ErrorTranslator


class PancakeSwapErrorTranslator(ErrorTranslator):
    """
    PancakeSwap Error Translation

    PancakeSwap primarily uses GraphQL responses with custom error messages.
    Common error patterns:
    - Authentication errors
    - Rate limiting
    - Invalid parameters
    - Insufficient liquidity
    - Transaction failures
    """

    ERROR_MAP = {
        # GraphQL errors
        "UNAUTHORIZED": (2001, "Authentication failed"),
        "INVALID_TOKEN": (2001, "Invalid API token"),
        "FORBIDDEN": (2004, "Permission denied"),

        # Rate limiting
        "RATE_LIMITED": (3001, "Rate limit exceeded"),
        "TOO_MANY_REQUESTS": (3003, "Too many requests"),

        # Validation errors
        "INVALID_INPUT": (4001, "Invalid input parameter"),
        "MISSING_PARAMETER": (4002, "Missing required parameter"),
        "INVALID_ADDRESS": (4003, "Invalid address format"),
        "INVALID_AMOUNT": (4004, "Invalid amount"),

        # Business logic errors
        "INSUFFICIENT_LIQUIDITY": (5001, "Insufficient liquidity"),
        "SLIPPAGE_TOO_HIGH": (5002, "Slippage too high"),
        "TRADE_TOO_SMALL": (5003, "Trade amount too small"),
        "INVALID_PATH": (5004, "Invalid trade path"),

        # Transaction errors
        "TRANSACTION_FAILED": (6001, "Transaction failed"),
        "REVERTED": (6002, "Transaction reverted"),
        "TIMEOUT": (6003, "Transaction timeout"),
        "PENDING": (6004, "Transaction pending"),

        # Token errors
        "TOKEN_NOT_FOUND": (7001, "Token not found"),
        "TOKEN_NOT_TRADABLE": (7002, "Token not tradable"),
        "BLACKLISTED_TOKEN": (7003, "Blacklisted token"),

        # Network errors
        "NETWORK_ERROR": (1001, "Network error"),
        "GAS_ERROR": (1002, "Gas error"),
        "BLOCKCHAIN_ERROR": (1003, "Blockchain error"),

        # System errors
        "INTERNAL_ERROR": (8001, "Internal server error"),
        "MAINTENANCE": (8002, "Exchange under maintenance"),
        "SERVICE_UNAVAILABLE": (8003, "Service unavailable"),
    }

    def translate_error(self, error_response) -> tuple:
        """
        Translate PancakeSwap error response to unified error code and message.

        Args:
            error_response: Error response from PancakeSwap API

        Returns:
            tuple: (unified_error_code, error_message)
        """
        if isinstance(error_response, dict):
            # Handle GraphQL error responses
            if "errors" in error_response:
                for error in error_response["errors"]:
                    if "message" in error:
                        message = error["message"]
                        # Check against known error patterns
                        for error_pattern, (code, msg) in self.ERROR_MAP.items():
                            if error_pattern in message.upper():
                                return code, msg
                        return 8001, f"Unknown GraphQL error: {message}"

            # Handle custom error fields
            if "error" in error_response:
                error_msg = str(error_response["error"])
                return self._translate_message(error_msg)

            # Handle error codes
            if "code" in error_response:
                code = error_response["code"]
                if code in self.ERROR_MAP:
                    return self.ERROR_MAP[code]

        elif isinstance(error_response, str):
            # Direct error message
            return self._translate_message(error_response)

        # Default error
        return 9999, f"Unknown PancakeSwap error: {str(error_response)}"

    def _translate_message(self, message: str) -> tuple:
        """Translate error message to unified error code."""
        message_upper = message.upper()

        for error_pattern, (code, msg) in self.ERROR_MAP.items():
            if error_pattern in message_upper:
                return code, msg

        # Try to match common patterns
        if "authentication" in message_lower or "auth" in message_lower:
            return 2001, f"Authentication error: {message}"
        elif "rate limit" in message_lower or "too many" in message_lower:
            return 3001, f"Rate limit error: {message}"
        elif "invalid" in message_lower:
            return 4001, f"Validation error: {message}"
        elif "insufficient" in message_lower:
            return 5001, f"Insufficient funds/liquidity: {message}"
        elif "transaction" in message_lower:
            return 6001, f"Transaction error: {message}"

        # Default unknown error
        return 9999, f"Unknown PancakeSwap error: {message}"