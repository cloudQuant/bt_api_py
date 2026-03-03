"""
Crypto.com Error Translator
"""

from bt_api_py.error_framework import ErrorTranslator, UnifiedErrorCode


class CryptoComErrorTranslator(ErrorTranslator):
    ERROR_MAP = {
        0: (None, "Success"),
        10001: (UnifiedErrorCode.INTERNAL_ERROR, "System error"),
        10002: (UnifiedErrorCode.INVALID_PARAMETER, "Invalid parameter"),
        10003: (UnifiedErrorCode.IP_BANNED, "IP not authorized"),
        10004: (UnifiedErrorCode.INVALID_API_KEY, "Invalid API key"),
        10005: (UnifiedErrorCode.INVALID_SIGNATURE, "Invalid signature"),
        10006: (UnifiedErrorCode.EXPIRED_TIMESTAMP, "Timestamp expired"),
        10007: (UnifiedErrorCode.RATE_LIMIT_EXCEEDED, "Request too frequent"),
        10008: (UnifiedErrorCode.PERMISSION_DENIED, "Insufficient permissions"),
        20001: (UnifiedErrorCode.INSUFFICIENT_BALANCE, "Insufficient balance"),
        20007: (UnifiedErrorCode.ORDER_NOT_FOUND, "Order not found"),
        30003: (UnifiedErrorCode.INVALID_SYMBOL, "Symbol not exists"),
        30004: (UnifiedErrorCode.PRECISION_ERROR, "Price precision error"),
        30005: (UnifiedErrorCode.INVALID_VOLUME, "Quantity precision error"),
        30006: (UnifiedErrorCode.MIN_NOTIONAL, "Minimum notional not met"),
    }