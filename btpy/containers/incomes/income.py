"""收入类，用于确定收入的属性和方法
"""


class IncomeData:
    """保存收入信息"""

    def __init__(self, income_info, has_been_json_encoded):
        self.event = "IncomeEvent"
        self.income_info = income_info
        self.has_been_json_encoded = has_been_json_encoded

    def init_data(self):
        raise NotImplementedError

    def get_event_type(self):
        return self.event

    def get_exchange_name(self):
        raise NotImplementedError

    def get_server_time(self):
        raise NotImplementedError

    def get_local_update_time(self):
        raise NotImplementedError

    def get_symbol_name(self):
        raise NotImplementedError

    def get_income_type(self):
        raise NotImplementedError

    def get_income_value(self):
        raise NotImplementedError

    def get_income_asset(self):
        raise NotImplementedError

    def __str__(self):
        raise NotImplementedError

    def __repr__(self):
        raise NotImplementedError
