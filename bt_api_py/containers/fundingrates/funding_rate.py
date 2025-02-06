"""资金费率类，用于确定资金费率的属性和方法
# fundingRate的数据推送和请求频率不是特别高，传入的数据直接使用json格式
"""


class FundingRateData:
    """保存资金费率信息"""

    def __init__(self, funding_rate_info, has_been_json_encoded):
        self.event = "FundingEvent"
        self.funding_rate_info = funding_rate_info
        self.has_been_json_encoded = has_been_json_encoded

    def get_event_type(self):
        """
        get the data type and event type
        :return: str
        """
        return self.event

    def init_data(self):
        """
        init params, to get data
        :return: None
        """
        raise NotImplementedError

    def get_exchange_name(self):
        """
        get exchange name
        :return: str
        """
        raise NotImplementedError

    def get_server_time(self):
        """
        get server time for the data generate
        :return: float timestamp
        """
        raise NotImplementedError

    def get_local_update_time(self):
        """
        get local update time for the computer generate the data
        :return: float timestamp
        """
        raise NotImplementedError

    def get_asset_type(self):
        """
        get asset type, eg swap & spot
        :return: str
        """
        raise NotImplementedError

    def get_symbol_name(self):
        """
        get symbol name for the data
        :return: str
        """
        raise NotImplementedError

    def get_pre_funding_rate(self):
        """
        get previous period funding rate
        :return: float
        """
        raise NotImplementedError

    def get_pre_funding_time(self):
        """
        get previous period funding time
        :return: float timestamp
        """
        raise NotImplementedError

    # def get_funding_rate(self):
    #     raise NotImplementedError
    #
    # def get_funding_time(self):
    #     raise NotImplementedError

    def get_next_funding_rate(self):
        """
        get the next period funding rate
        :return: float
        """
        raise NotImplementedError

    def get_next_funding_time(self):
        """
        get the next period funding rate time
        :return: float
        """
        raise NotImplementedError

    def get_max_funding_rate(self):
        """
        get max funding rate
        :return: float
        """
        raise NotImplementedError

    def get_min_funding_rate(self):
        """
        get min funding rate
        :return: float
        """
        raise NotImplementedError

    def get_current_funding_rate(self):
        """
        get current funding rate
        :return: float
        """
        raise NotImplementedError

    def get_current_funding_time(self):
        """
        get current funding time
        :return: float, timestamp
        """
        raise NotImplementedError

    def get_settlement_funding_rate(self):
        """
        get settlement funding rate,若 settlement_status = processing，该字段代表用于本轮结算的资金费率；
        若 settlement_status = settled，该字段代表用于上轮结算的资金费率
        :return: float
        """
        raise NotImplementedError

    def get_settlement_status(self):
        """
        get settlement_status, processing：结算中 settled：已结算
        :return: str, processing or settled
        """
        raise NotImplementedError

    def get_method(self):
        """
        get funding income type, current_period：当期收 next_period：跨期收
        :return: str, current_period or next_period
        """
        raise NotImplementedError

    def __str__(self):
        raise NotImplementedError

    def __repr__(self):
        raise NotImplementedError
