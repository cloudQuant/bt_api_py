import time


class RequestData(object):
    """request data info"""

    def __init__(self, data, extra_data, status=False, normalize_func=None):
        self.event = "RequestEvent"
        self.input_data = data
        # 额外的数据，策略中传入的，用于策略得到数据之后进行分析
        self.extra_data = extra_data
        # 传递的数据
        self.data: list = []
        # 返回的状态, False代表失败，True代表成功
        self.status = status
        # 标准化数据函数
        self.normalize_func = normalize_func
        # 本地化时间
        self.local_update_time = time.time()
        # 交易所名称
        self.exchange_name = extra_data.get("exchange_name", "")
        # symbol名称
        self.symbol_name = extra_data.get("symbol_name", "")
        # 资产类型
        self.asset_type = extra_data.get("asset_type", "")
        # 请求类型
        self.request_type = extra_data.get("request_type", "")
        # 是否初始化
        self.has_been_init_data = False

    def init_data(self):
        # print("self.input_data", self.input_data)
        normalize_func = self.extra_data.get("normalize_function", None)
        if normalize_func is None:
            self.data = self.input_data
            self.status = None
        else:
            self.data, self.status = normalize_func(self.input_data, self.extra_data)
        # print(f"asset_type: {self.asset_type}, self.data: {self.data},status: {self.status}")

    def set_data(self, data):
        """
        set data to request
        :param data: list type
        :return: None
        """
        self.data = data

    def set_status(self, status=True):
        """
        set status of request
        :param status: default:True
        :return: None
        """
        self.status = status

    def get_event(self):
        """
        get an event type from request data info
        :return: str
        """
        return self.event

    def get_request_type(self):
        return self.request_type

    def get_input_data(self):
        """
        get input data from request data info
        :return: str or dict
        """
        return self.input_data

    def get_extra_data(self):
        """
        get extra data from request data info
        :return: None or dict
        """
        return self.extra_data

    def get_data(self):
        """
        get data from a request data info
        :return: list of different data instance
        """
        if not self.has_been_init_data:
            self.init_data()
            self.has_been_init_data = True
        return self.data

    def get_status(self):
        """
        get status of request
        :return: bool, True or False
        """
        return self.status

    def get_exchange_name(self):
        return self.exchange_name

    def get_symbol_name(self):
        return self.symbol_name

    def get_asset_type(self):
        return self.asset_type
