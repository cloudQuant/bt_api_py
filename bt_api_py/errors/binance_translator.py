"""
Binance API 错误翻译器
"""

from bt_api_py.error import ErrorTranslator, UnifiedErrorCode


class BinanceErrorTranslator(ErrorTranslator):
    ERROR_MAP = {
        -1000: (UnifiedErrorCode.INTERNAL_ERROR, "Unknown error"),
        -1001: (UnifiedErrorCode.INTERNAL_ERROR, "Disconnected"),
        -1002: (UnifiedErrorCode.PERMISSION_DENIED, "Unauthorized"),
        -1003: (UnifiedErrorCode.RATE_LIMIT_EXCEEDED, "Too many requests"),
        -1006: (UnifiedErrorCode.EXCHANGE_OVERLOADED, "Unexpected disconnect"),
        -1021: (UnifiedErrorCode.EXPIRED_TIMESTAMP, "Timestamp outside recvWindow"),
        -1022: (UnifiedErrorCode.INVALID_SIGNATURE, "Signature not valid"),
        -1100: (UnifiedErrorCode.INVALID_PARAMETER, "Illegal characters in request"),
        -1101: (UnifiedErrorCode.INVALID_PARAMETER, "Too many parameters"),
        -1102: (UnifiedErrorCode.MISSING_PARAMETER, "Mandatory parameter was not sent"),
        -1103: (UnifiedErrorCode.INVALID_PARAMETER, "Unknown parameter"),
        -1104: (UnifiedErrorCode.INVALID_PARAMETER, "Parameter is empty"),
        -1106: (UnifiedErrorCode.INVALID_PARAMETER, "Parameter is not supported"),
        -1111: (UnifiedErrorCode.PRECISION_ERROR, "Precision is over the maximum defined"),
        -1112: (UnifiedErrorCode.INVALID_VOLUME, "Invalid order quantity"),
        -1114: (UnifiedErrorCode.INVALID_API_KEY, "Invalid API-key format"),
        -1115: (UnifiedErrorCode.PERMISSION_DENIED, "Invalid API-key, IP, or permissions"),
        -2010: (UnifiedErrorCode.ORDER_NOT_FOUND, "Order does not exist"),
        -2011: (UnifiedErrorCode.ORDER_ALREADY_FILLED, "Order already filled"),
        -2013: (UnifiedErrorCode.INVALID_ORDER, "Order rejected"),
        -2014: (UnifiedErrorCode.INVALID_API_KEY, "API-key format invalid"),
        -2015: (
            UnifiedErrorCode.PERMISSION_DENIED,
            "Invalid API-key, IP, or permissions for action",
        ),
    }
