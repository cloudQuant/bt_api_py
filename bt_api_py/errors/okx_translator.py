"""
OKX API 错误翻译器
"""

from __future__ import annotations

from bt_api_py.error import ErrorTranslator, UnifiedError, UnifiedErrorCode


class OKXErrorTranslator(ErrorTranslator):
    ERROR_MAP = {
        "0": (None, "Success"),
        "50000": (UnifiedErrorCode.INTERNAL_ERROR, "Body can not be empty"),
        "50001": (UnifiedErrorCode.EXCHANGE_MAINTENANCE, "Service temporarily unavailable"),
        "50004": (UnifiedErrorCode.NETWORK_TIMEOUT, "Endpoint request timeout"),
        "50011": (UnifiedErrorCode.RATE_LIMIT_EXCEEDED, "Rate limit reached"),
        "50013": (UnifiedErrorCode.EXCHANGE_OVERLOADED, "System busy"),
        "50014": (UnifiedErrorCode.INVALID_PARAMETER, "Parameter error"),
        "50100": (UnifiedErrorCode.INVALID_API_KEY, "API frozen"),
        "50101": (UnifiedErrorCode.INVALID_API_KEY, "API key does not match"),
        "50102": (UnifiedErrorCode.EXPIRED_TIMESTAMP, "Timestamp expired"),
        "50103": (UnifiedErrorCode.INVALID_SIGNATURE, "Signature invalid"),
        "50104": (UnifiedErrorCode.PERMISSION_DENIED, "No permission"),
        "50105": (UnifiedErrorCode.PERMISSION_DENIED, "IP not whitelisted"),
        "51000": (UnifiedErrorCode.INVALID_PARAMETER, "Parameter error"),
        "51001": (UnifiedErrorCode.INVALID_SYMBOL, "Instrument ID does not exist"),
        "51004": (UnifiedErrorCode.INVALID_VOLUME, "Order amount too small"),
        "51008": (UnifiedErrorCode.INSUFFICIENT_BALANCE, "Insufficient balance"),
        "51009": (UnifiedErrorCode.INSUFFICIENT_MARGIN, "Insufficient margin"),
        "51010": (UnifiedErrorCode.INVALID_PRICE, "Price not meeting post-only rule"),
        "51020": (UnifiedErrorCode.ORDER_NOT_FOUND, "Order does not exist"),
        "51023": (UnifiedErrorCode.DUPLICATE_ORDER, "Duplicate order"),
        "51024": (UnifiedErrorCode.ORDER_ALREADY_FILLED, "Order already filled"),
        "51503": (UnifiedErrorCode.INVALID_PARAMETER, "Reduce-only parameter error"),
    }

    @classmethod
    def translate(cls, raw_error: dict, venue: str) -> UnifiedError | None:
        """OKX 错误码是字符串，需要特殊处理"""
        code = raw_error.get("code", raw_error.get("sCode", ""))
        msg = raw_error.get("msg", raw_error.get("sMsg", ""))
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
