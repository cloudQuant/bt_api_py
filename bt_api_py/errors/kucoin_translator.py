"""
KuCoin API 错误翻译器
"""

from __future__ import annotations

from bt_api_py.error import ErrorTranslator, UnifiedError, UnifiedErrorCode


class KuCoinErrorTranslator(ErrorTranslator):
    """KuCoin API error translator.

    KuCoin API response format:
    {
        "code": "400001",
        "msg": "Invalid KC-API-TIMESTAMP"
    }
    """

    ERROR_MAP = {
        # Success code
        "200000": (None, "Success"),
        # 认证错误 (4xxxx)
        "400001": (UnifiedErrorCode.INVALID_API_KEY, "Missing authentication headers"),
        "400002": (UnifiedErrorCode.EXPIRED_TIMESTAMP, "Invalid KC-API-TIMESTAMP"),
        "400003": (UnifiedErrorCode.INVALID_API_KEY, "Invalid KC-API-KEY"),
        "400004": (UnifiedErrorCode.INVALID_API_KEY, "Invalid KC-API-PASSPHRASE"),
        "400005": (UnifiedErrorCode.INVALID_SIGNATURE, "Invalid KC-API-SIGN"),
        "400006": (UnifiedErrorCode.PERMISSION_DENIED, "IP not in whitelist"),
        "400007": (UnifiedErrorCode.PERMISSION_DENIED, "Access denied"),
        "400008": (UnifiedErrorCode.INVALID_SIGNATURE, "Timestamp expired"),
        # 参数错误 (4xxxx)
        "400100": (UnifiedErrorCode.INVALID_PARAMETER, "Parameter error"),
        "400101": (UnifiedErrorCode.MISSING_PARAMETER, "Missing required parameter"),
        "400102": (UnifiedErrorCode.INVALID_PARAMETER, "Invalid parameter format"),
        "400103": (UnifiedErrorCode.INVALID_PARAMETER, "Parameter out of range"),
        # 业务错误 (4xxxx, 9xxxx)
        "411100": (UnifiedErrorCode.PERMISSION_DENIED, "User is frozen"),
        "900001": (UnifiedErrorCode.INVALID_SYMBOL, "Symbol not exists"),
        "900002": (UnifiedErrorCode.MARKET_CLOSED, "Market is closed"),
        "900003": (UnifiedErrorCode.INSUFFICIENT_BALANCE, "Insufficient balance"),
        "900004": (UnifiedErrorCode.INVALID_ORDER, "Invalid order"),
        "900005": (UnifiedErrorCode.ORDER_NOT_FOUND, "Order not found"),
        "900006": (UnifiedErrorCode.ORDER_ALREADY_FILLED, "Order already filled"),
        "900007": (UnifiedErrorCode.DUPLICATE_ORDER, "Duplicate client order ID"),
        "900008": (UnifiedErrorCode.INVALID_PRICE, "Price out of range"),
        "900009": (UnifiedErrorCode.INVALID_VOLUME, "Volume out of range"),
        "900010": (UnifiedErrorCode.PRECISION_ERROR, "Precision error"),
        "900011": (UnifiedErrorCode.INSUFFICIENT_BALANCE, "Insufficient funds"),
        # 限流错误
        "429000": (UnifiedErrorCode.RATE_LIMIT_EXCEEDED, "Too many requests"),
        "400200": (UnifiedErrorCode.RATE_LIMIT_EXCEEDED, "Request limit exceeded"),
        # 系统错误 (5xxxx)
        "500000": (UnifiedErrorCode.INTERNAL_ERROR, "Internal server error"),
        "500001": (UnifiedErrorCode.EXCHANGE_OVERLOADED, "Service overloaded"),
        "500002": (UnifiedErrorCode.EXCHANGE_MAINTENANCE, "Service maintenance"),
        "500003": (UnifiedErrorCode.NETWORK_TIMEOUT, "Request timeout"),
        # 其他错误
        "800001": (UnifiedErrorCode.UNSUPPORTED_OPERATION, "Operation not supported"),
        "800002": (UnifiedErrorCode.NOT_SUPPORTED, "Feature not available"),
    }

    @classmethod
    def translate(cls, raw_error: dict, venue: str) -> UnifiedError | None:
        """KuCoin API 错误码是字符串，在 code 字段中"""
        code = raw_error.get("code")
        msg = raw_error.get("msg", "")

        if code is not None:
            code_str = str(code)
            if code_str in cls.ERROR_MAP:
                unified_code, default_msg = cls.ERROR_MAP[code_str]
                if unified_code is None:
                    return None  # Success
                return UnifiedError(
                    code=unified_code,
                    category=cls._get_category(unified_code),
                    venue=venue,
                    message=msg or default_msg,
                    original_error=f"{code}: {msg}",
                    context={"raw_response": raw_error},
                )

        return super().translate(raw_error, venue)
