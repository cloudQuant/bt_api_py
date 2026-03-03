class BybitErrorTranslator(ErrorTranslator):
    """Bybit API 错误翻译器

    Bybit API 返回的错误格式:
    {
      "retCode": 10003,
      "retMsg": "Invalid API key",
      "retExtInfo": {},
      "time": 1672304484978
    }
    """

    ERROR_MAP = {
        10001: (UnifiedErrorCode.INVALID_PARAMETER, "Parameter error"),
        10003: (UnifiedErrorCode.INVALID_API_KEY, "Invalid API key"),
        10004: (UnifiedErrorCode.INVALID_SIGNATURE, "Invalid signature"),
        10005: (UnifiedErrorCode.PERMISSION_DENIED, "Permission denied"),
        10006: (UnifiedErrorCode.RATE_LIMIT_EXCEEDED, "Too many requests"),
        10016: (UnifiedErrorCode.INTERNAL_ERROR, "Server error"),
        110001: (UnifiedErrorCode.ORDER_NOT_FOUND, "Order does not exist"),
        110004: (UnifiedErrorCode.INSUFFICIENT_BALANCE, "Insufficient balance"),
        110007: (UnifiedErrorCode.INVALID_PRICE, "Order price is out of range"),
        110043: (UnifiedErrorCode.PERMISSION_DENIED, "Set margin mode first"),
    }

    @classmethod
    def translate(cls, raw_error: dict, venue: str) -> UnifiedError | None:
        """Bybit API 错误码是数字，在 retCode 字段中"""
        ret_code = raw_error.get("retCode")
        ret_msg = raw_error.get("retMsg", "")

        if ret_code is not None:
            code_str = str(ret_code)
            if code_str in cls.ERROR_MAP:
                unified_code, default_msg = cls.ERROR_MAP[code_str]
                if unified_code is None:
                    return None
                return UnifiedError(
                    code=unified_code,
                    category=cls._get_category(unified_code),
                    venue=venue,
                    message=ret_msg or default_msg,
                    original_error=f"{ret_code}: {ret_msg}",
                    context={"raw_response": raw_error},
                )

        return super().translate(raw_error, venue)