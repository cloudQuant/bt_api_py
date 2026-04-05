"""
CTP 错误翻译器
"""

from __future__ import annotations

from bt_api_py.error import ErrorTranslator, UnifiedErrorCode


class CTPErrorTranslator(ErrorTranslator):
    ERROR_MAP = {
        0: (None, "成功"),
        -1: (UnifiedErrorCode.NETWORK_DISCONNECTED, "网络连接失败"),
        -2: (UnifiedErrorCode.INTERNAL_ERROR, "未处理请求超过许可数"),
        -3: (UnifiedErrorCode.RATE_LIMIT_EXCEEDED, "每秒发送请求数超过许可数"),
        -4: (UnifiedErrorCode.RATE_LIMIT_EXCEEDED, "上传请求数超过许可数"),
        -5: (UnifiedErrorCode.INTERNAL_ERROR, "发送请求失败"),
        -6: (UnifiedErrorCode.NETWORK_TIMEOUT, "接收响应失败"),
        -7: (UnifiedErrorCode.NETWORK_DISCONNECTED, "连接断开"),
        -8: (UnifiedErrorCode.NETWORK_TIMEOUT, "请求处理超时"),
        # CTP 业务错误 (正数)
        2: (UnifiedErrorCode.INVALID_ORDER, "不允许的报单操作"),
        3: (UnifiedErrorCode.INSUFFICIENT_BALANCE, "资金不足"),
        12: (UnifiedErrorCode.INVALID_PARAMETER, "报单字段有误"),
        22: (UnifiedErrorCode.INVALID_SYMBOL, "找不到合约"),
        25: (UnifiedErrorCode.ORDER_NOT_FOUND, "找不到报单"),
        31: (UnifiedErrorCode.MARKET_CLOSED, "不在交易时间"),
        44: (UnifiedErrorCode.INVALID_PRICE, "报单价格超出涨跌停限制"),
        51: (UnifiedErrorCode.DUPLICATE_ORDER, "报单重复"),
    }
