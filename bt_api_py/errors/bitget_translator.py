"""
Bitget API 错误翻译器
"""

from __future__ import annotations

from bt_api_py.error import ErrorTranslator, UnifiedErrorCode


class BitgetErrorTranslator(ErrorTranslator):
    """Bitget API error translator."""

    ERROR_MAP = {
        # 认证错误
        "Invalid API key": (UnifiedErrorCode.INVALID_API_KEY, "Invalid API key"),
        "Invalid signature": (UnifiedErrorCode.INVALID_SIGNATURE, "Invalid signature"),
        "Invalid timestamp": (UnifiedErrorCode.EXPIRED_TIMESTAMP, "Invalid timestamp"),
        "Access denied": (UnifiedErrorCode.PERMISSION_DENIED, "Access denied"),
        # 限流错误
        "Too many requests": (UnifiedErrorCode.RATE_LIMIT_EXCEEDED, "Too many requests"),
        # 业务错误
        "Insufficient balance": (UnifiedErrorCode.INSUFFICIENT_BALANCE, "Insufficient balance"),
        "Invalid symbol": (UnifiedErrorCode.INVALID_SYMBOL, "Invalid symbol"),
        "Invalid price": (UnifiedErrorCode.INVALID_PRICE, "Invalid price"),
        "Invalid size": (UnifiedErrorCode.INVALID_VOLUME, "Invalid size"),
        "Order not found": (UnifiedErrorCode.ORDER_NOT_FOUND, "Order not found"),
        "Order already filled": (UnifiedErrorCode.ORDER_ALREADY_FILLED, "Order already filled"),
        "Order would immediately trigger": (
            UnifiedErrorCode.INVALID_ORDER,
            "Order would immediately trigger",
        ),
        "Market closed": (UnifiedErrorCode.MARKET_CLOSED, "Market closed"),
        "Duplicate order": (UnifiedErrorCode.DUPLICATE_ORDER, "Duplicate order"),
        "Precision error": (UnifiedErrorCode.PRECISION_ERROR, "Precision error"),
        # 系统错误
        "Internal server error": (UnifiedErrorCode.INTERNAL_ERROR, "Internal server error"),
        "Service unavailable": (UnifiedErrorCode.EXCHANGE_MAINTENANCE, "Service unavailable"),
    }
