"""
ExchangeData保存交易所数据
"""


class ExchangeData(object):

    def __init__(self):
        self.rate_limit_type = ""  # 频率限制类型
        self.interval = ""  # 间隔
        self.interval_num = 0  # 间隔数
        self.limit = 0  # 限制
        self.server_time = 0.0  # 服务器时间戳
        self.local_update_time = 0.0  # 本地时间戳
        self.timezone = ""  # 时区
        self.rate_limits = list()  # 频率限制
        self.exchange_filters = list()  # 交易所过滤
        self.symbols = list()  # 品种信息
        self.exchange_name = ''  # 交易所名称
        self.rest_url = ''
        self.acct_wss_url = ''
        self.wss_url = ''
        self.um_rest_url = ""
        self.um_wss_Url = ""
        self.rest_paths = {}  # rest paths
        self.wss_paths = {}  # wss paths
        self.kline_periods = {}  # kline periods
        self.reverse_kline_periods = {v: k for k, v in self.kline_periods.items()}
        self.status_dict = {}  # 交易状态
        self.legal_currency = []  # 合法货币

    def get_wss_url(self):
        return self.wss_url

    def raise_path_error(self, *args):
        """检查请求路径path是否合规
        Args:
            args: 不定参数
        """
        raise Exception(f"wbfAPI还未封装 {args} 接口")

    def raise_timeout(self, timeout, *args):
        """raise 超时错误

        Args:
            timeout (int): 超时时间，单位s
            *args: Description
        """
        raise Exception(f"{args} rest请求超时{timeout}s")

    def raise400(self, *args):
        """http 400
        Args:
            *args: Description
        """
        raise Exception(f"{args} rest请求返回<400>")

    def raise_proxy_error(self, *args):
        """代理错误
        Args:
            *args: Description
        """
        raise Exception(f"{args} 网络代理错误")

    @staticmethod
    def update_info(exchange_info):
        result = ExchangeData()
        for key in exchange_info:
            setattr(result, key, exchange_info[key])
        return result

    def to_dict(self):
        content = {key: getattr(self, key) for key in dir(self) if
                   ((not key.startswith("__")) & (not key.startswith("update")) & (not key.startswith("to_dict")))}
        return content
