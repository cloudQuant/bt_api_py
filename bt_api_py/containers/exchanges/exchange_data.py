"""ExchangeData保存交易所数据."""

from typing import Any, Never


class ExchangeData:
    def __init__(self, config: dict | None = None) -> None:
        self.rate_limit_type = ""  # 频率限制类型
        self.interval = ""  # 间隔
        self.interval_num = 0  # 间隔数
        self.limit = 0  # 限制
        self.server_time = 0.0  # 服务器时间戳
        self.local_update_time = 0.0  # 本地时间戳
        self.timezone = ""  # 时区
        self.rate_limits: list[Any] = []  # 频率限制
        self.exchange_filters: list[Any] = []  # 交易所过滤
        self.symbols: list[Any] = []  # 品种信息
        self.exchange_name = ""  # 交易所名称
        self.rest_url = ""
        self.acct_wss_url = ""
        self.wss_url = ""
        self.um_rest_url = ""
        self.um_wss_Url = ""
        self.rest_paths: dict[str, Any] = {}  # rest paths
        self.wss_paths: dict[str, Any] = {}  # wss paths
        self.kline_periods: dict[str, str] = {}  # kline periods
        self.reverse_kline_periods: dict[str, str] = {}
        self.status_dict: dict[str, Any] = {}  # 交易状态
        self.legal_currency: list[str] = []  # 合法货币
        self.api_key = ""  # API key for authentication
        self.api_secret = ""  # API secret for signing
        self.passphrase = ""  # Passphrase (used by some exchanges)

    def get_wss_url(self) -> Any:
        return self.wss_url

    def raise_path_error(self, *args) -> Never:
        """检查请求路径path是否合规
        Args:
            args: 不定参数.
        """
        raise NotImplementedError(f"wbfAPI还未封装 {args} 接口")

    def raise_timeout(self, timeout, *args) -> Never:
        """Raise 超时错误.

        Args:
            timeout (int): 超时时间，单位s
            *args: Description

        """
        raise TimeoutError(f"{args} rest请求超时{timeout}s")

    def raise400(self, *args) -> Never:
        """Http 400
        Args:
            *args: Description.
        """
        raise RuntimeError(f"{args} rest请求返回<400>")

    def raise_proxy_error(self, *args) -> Never:
        """代理错误
        Args:
            *args: Description.
        """
        raise ConnectionError(f"{args} 网络代理错误")

    @staticmethod
    def update_info(exchange_info):
        result = ExchangeData()
        for key in exchange_info:
            setattr(result, key, exchange_info[key])
        return result

    def to_dict(self):
        content = {
            key: getattr(self, key)
            for key in dir(self)
            if (
                (not key.startswith("__"))
                & (not key.startswith("update"))
                & (not key.startswith("to_dict"))
            )
        }
        return content
