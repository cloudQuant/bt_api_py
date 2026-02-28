"""
bt_api_py 自定义异常体系
统一各模块的异常处理，替代散乱的 assert / raise Exception / ConnectionError
"""


class BtApiError(Exception):
    """bt_api_py 所有异常的基类"""

    pass


class ExchangeNotFoundError(BtApiError):
    """交易所未注册或未添加"""

    def __init__(self, exchange_name, available=None):
        msg = f"Exchange not found: {exchange_name}"
        if available:
            msg += f". Available: {available}"
        super().__init__(msg)
        self.exchange_name = exchange_name


class ExchangeConnectionError(BtApiError):
    """交易所连接失败"""

    def __init__(self, exchange_name, detail=""):
        msg = f"Connection failed: {exchange_name}"
        if detail:
            msg += f" — {detail}"
        super().__init__(msg)
        self.exchange_name = exchange_name


# 向后兼容别名（已弃用，请使用 ExchangeConnectionError）
ConnectionError = ExchangeConnectionError


class AuthenticationError(ExchangeConnectionError):
    """认证失败（API Key / 密码 / 穿透式认证错误）"""

    pass


class RequestTimeoutError(BtApiError):
    """REST / 查询请求超时"""

    def __init__(self, exchange_name, url="", timeout=0):
        msg = f"{exchange_name} request timeout ({timeout}s)"
        if url:
            msg += f": {url}"
        super().__init__(msg)
        self.exchange_name = exchange_name
        self.url = url
        self.timeout = timeout


class RequestError(BtApiError):
    """REST 请求失败（非超时）"""

    def __init__(self, exchange_name, url="", detail=""):
        msg = f"{exchange_name} request error"
        if url:
            msg += f": {url}"
        if detail:
            msg += f" — {detail}"
        super().__init__(msg)
        self.exchange_name = exchange_name


class OrderError(BtApiError):
    """下单 / 撤单操作失败"""

    def __init__(self, exchange_name, symbol="", detail=""):
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

    def __init__(self, exchange_name, detail=""):
        msg = f"{exchange_name} subscribe error"
        if detail:
            msg += f": {detail}"
        super().__init__(msg)
        self.exchange_name = exchange_name


class DataParseError(BtApiError):
    """数据解析失败"""

    def __init__(self, container_class="", detail=""):
        msg = "Data parse error"
        if container_class:
            msg += f" in {container_class}"
        if detail:
            msg += f": {detail}"
        super().__init__(msg)
