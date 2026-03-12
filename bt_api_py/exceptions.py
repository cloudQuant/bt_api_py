"""
bt_api_py 自定义异常体系
统一各模块的异常处理，替代散乱的 assert / raise Exception / ConnectionError
"""

from __future__ import annotations

__all__ = [
    "BtApiError",
    "ExchangeNotFoundError",
    "ExchangeConnectionError",
    "ExchangeConnectionAlias",
    "AuthenticationError",
    "RequestTimeoutError",
    "RequestError",
    "RequestFailedError",
    "OrderError",
    "SubscribeError",
    "DataParseError",
    "RateLimitError",
    "NetworkError",
    "InvalidSymbolError",
    "InsufficientBalanceError",
    "InvalidOrderError",
    "OrderNotFoundError",
    "ConfigurationError",
    "WebSocketError",
]


class BtApiError(Exception):
    """bt_api_py 所有异常的基类"""


class ExchangeNotFoundError(BtApiError):
    """交易所未注册或未添加"""

    def __init__(self, exchange_name: str, available: str | list[str] | None = None) -> None:
        msg = f"Exchange not found: {exchange_name}"
        if available:
            msg += f". Available: {available}"
        super().__init__(msg)
        self.exchange_name = exchange_name
        self.available = available


class ExchangeConnectionError(BtApiError):
    """交易所连接失败"""

    def __init__(self, exchange_name: str, detail: str = "") -> None:
        msg = f"Connection failed: {exchange_name}"
        if detail:
            msg += f" — {detail}"
        super().__init__(msg)
        self.exchange_name = exchange_name


# 向后兼容别名（已弃用，请使用 ExchangeConnectionError）
# 注意：不再直接赋值给 ConnectionError，避免遮蔽 Python 内置异常
ExchangeConnectionAlias = ExchangeConnectionError


class AuthenticationError(ExchangeConnectionError):
    """认证失败（API Key / 密码 / 穿透式认证错误）"""


class RequestTimeoutError(BtApiError):
    """REST / 查询请求超时"""

    def __init__(self, exchange_name: str, url: str = "", timeout: int | float = 0) -> None:
        msg = f"{exchange_name} request timeout ({timeout}s)"
        if url:
            msg += f": {url}"
        super().__init__(msg)
        self.exchange_name = exchange_name
        self.url = url
        self.timeout = timeout


class RequestError(BtApiError):
    """REST 请求失败（非超时）"""

    def __init__(self, exchange_name: str, url: str = "", detail: str = "") -> None:
        msg = f"{exchange_name} request error"
        if url:
            msg += f": {url}"
        if detail:
            msg += f" — {detail}"
        super().__init__(msg)
        self.exchange_name = exchange_name


class OrderError(BtApiError):
    """下单 / 撤单操作失败"""

    def __init__(self, exchange_name: str, symbol: str = "", detail: str = "") -> None:
        msg = f"{exchange_name} order error"
        if symbol:
            msg += f" [{symbol}]"
        if detail:
            msg += f": {detail}"
        super().__init__(msg)
        self.exchange_name = exchange_name
        self.symbol = symbol


class SubscribeError(BtApiError):
    """订阅失败"""

    def __init__(self, exchange_name: str, detail: str = "") -> None:
        msg = f"{exchange_name} subscribe error"
        if detail:
            msg += f": {detail}"
        super().__init__(msg)
        self.exchange_name = exchange_name


class DataParseError(BtApiError):
    """数据解析失败"""

    def __init__(self, container_class: str = "", detail: str = "") -> None:
        msg = "Data parse error"
        if container_class:
            msg += f" in {container_class}"
        if detail:
            msg += f": {detail}"
        super().__init__(msg)


class RateLimitError(BtApiError):
    """API 速率限制错误"""

    def __init__(
        self, exchange_name: str, retry_after: int | float | None = None, detail: str = ""
    ) -> None:
        msg = f"{exchange_name} rate limit exceeded"
        if retry_after:
            msg += f" (retry after {retry_after}s)"
        if detail:
            msg += f": {detail}"
        super().__init__(msg)
        self.exchange_name = exchange_name
        self.retry_after = retry_after


class NetworkError(BtApiError):
    """网络错误（连接失败、DNS 解析失败等）"""

    def __init__(self, exchange_name: str, detail: str = "") -> None:
        msg = f"{exchange_name} network error"
        if detail:
            msg += f": {detail}"
        super().__init__(msg)
        self.exchange_name = exchange_name


class InvalidSymbolError(BtApiError):
    """无效的交易对符号"""

    def __init__(self, exchange_name: str, symbol: str, detail: str = "") -> None:
        msg = f"{exchange_name} invalid symbol: {symbol}"
        if detail:
            msg += f" — {detail}"
        super().__init__(msg)
        self.exchange_name = exchange_name
        self.symbol = symbol


class InsufficientBalanceError(OrderError):
    """余额不足"""

    def __init__(
        self,
        exchange_name: str,
        symbol: str = "",
        required: float | None = None,
        available: float | None = None,
    ) -> None:
        detail = "Insufficient balance"
        if required is not None and available is not None:
            detail += f" (required: {required}, available: {available})"
        super().__init__(exchange_name, symbol, detail)
        self.required = required
        self.available = available


class InvalidOrderError(OrderError):
    """无效的订单参数（价格、数量等）"""


class OrderNotFoundError(OrderError):
    """订单不存在"""

    def __init__(self, exchange_name: str, order_id: str, symbol: str = "") -> None:
        detail = f"Order not found: {order_id}"
        super().__init__(exchange_name, symbol, detail)
        self.order_id = order_id


class ConfigurationError(BtApiError):
    """配置错误（缺少必需参数、配置格式错误等）"""

    def __init__(self, detail: str = "") -> None:
        msg = "Configuration error"
        if detail:
            msg += f": {detail}"
        super().__init__(msg)


class WebSocketError(BtApiError):
    """WebSocket 连接或订阅错误"""

    def __init__(self, exchange_name: str, detail: str = "") -> None:
        msg = f"{exchange_name} WebSocket error"
        if detail:
            msg += f": {detail}"
        super().__init__(msg)
        self.exchange_name = exchange_name


class RequestFailedError(RequestError):
    """通用请求失败错误（用于 HTTP 客户端）"""

    def __init__(
        self,
        exchange_name: str | None = None,
        url: str = "",
        detail: str = "",
        *,
        venue: str = "",
        message: str = "",
        status_code: int | None = None,
    ) -> None:
        # Backward compatibility:
        # - Old call sites: RequestFailedError(exchange_name, url=..., detail=...)
        # - New call sites (HttpClient): RequestFailedError(venue=..., message=..., status_code=...)
        name = exchange_name or venue or ""
        msg = message or detail or "Request failed"
        if status_code is not None:
            msg = f"{msg} (HTTP {status_code})"

        super().__init__(name or "unknown", url=url, detail=msg)
        self.venue = name
        self.status_code = status_code
