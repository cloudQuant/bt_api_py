"""
MEXC Error Translation

Provides MEXC-specific error code translation.
"""

from bt_api_py.error_framework import ErrorTranslator, UnifiedErrorCode


class MexcErrorTranslator(ErrorTranslator):
    """MEXC API 错误翻译器"""

    ERROR_MAP = {
        -1000: (UnifiedErrorCode.INTERNAL_ERROR, "Unknown error"),
        -1001: (UnifiedErrorCode.NETWORK_DISCONNECTED, "Disconnected"),
        -1002: (UnifiedErrorCode.INVALID_API_KEY, "Unauthorized"),
        -1003: (UnifiedErrorCode.RATE_LIMIT_EXCEEDED, "Too many requests"),
        -1004: (UnifiedErrorCode.DUPLICATE_ORDER, "Duplicate request"),
        -1006: (UnifiedErrorCode.EXCHANGE_OVERLOADED, "Abnormal message"),
        -1007: (UnifiedErrorCode.NETWORK_TIMEOUT, "Timeout"),
        -1013: (UnifiedErrorCode.INVALID_VOLUME, "Invalid quantity"),
        -1014: (UnifiedErrorCode.INVALID_ORDER, "Unknown order type"),
        -1015: (UnifiedErrorCode.INVALID_ORDER, "Invalid order side"),
        -1016: (UnifiedErrorCode.EXPIRED_TIMESTAMP, "Invalid timestamp"),
        -1020: (UnifiedErrorCode.UNSUPPORTED_OPERATION, "Unsupported operation"),
        -1021: (UnifiedErrorCode.EXPIRED_TIMESTAMP, "Timestamp out of range"),
        -1022: (UnifiedErrorCode.INVALID_SIGNATURE, "Invalid signature"),
        -2010: (UnifiedErrorCode.INVALID_ORDER, "New order rejected"),
        -2011: (UnifiedErrorCode.INVALID_ORDER, "Cancel order rejected"),
        -2013: (UnifiedErrorCode.ORDER_NOT_FOUND, "Order does not exist"),
        -2014: (UnifiedErrorCode.INVALID_API_KEY, "Invalid API-key format"),
        -2015: (UnifiedErrorCode.INVALID_API_KEY, "Invalid API-key"),
    }