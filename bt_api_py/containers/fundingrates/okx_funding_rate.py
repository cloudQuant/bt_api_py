import time
import json
from bt_api_py.containers.fundingrates.funding_rate import FundingRateData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string


class OkxFundingRateData(FundingRateData):
    """保存资金费率信息"""

    def __init__(self, funding_rate_info, symbol_name, asset_type, has_been_json_encoded):
        super().__init__(funding_rate_info, has_been_json_encoded)
        self.exchange_name = "OKX"
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.local_update_time = time.time()
        self.funding_rate_data = funding_rate_info if has_been_json_encoded else None
        self.server_time = None
        self.next_funding_rate = None
        self.next_funding_time = None
        self.max_funding_rate = None
        self.min_funding_rate = None
        self.current_funding_rate = None
        self.current_funding_time = None
        self.settlement_funding_rate = None
        self.settlement_status = None
        self.method = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        if not self.has_been_json_encoded:
            self.funding_rate_data = json.loads(self.funding_rate_info)['data'][0]
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self
        self.server_time = from_dict_get_float(self.funding_rate_data, "ts")
        self.next_funding_rate = from_dict_get_float(self.funding_rate_data, "nextFundingRate")
        self.next_funding_time = from_dict_get_float(self.funding_rate_data, "nextFundingTime")
        self.max_funding_rate = from_dict_get_float(self.funding_rate_data, "maxFundingRate")
        self.min_funding_rate = from_dict_get_float(self.funding_rate_data, "minFundingRate")
        self.current_funding_rate = from_dict_get_float(self.funding_rate_data, "fundingRate")
        self.current_funding_time = from_dict_get_float(self.funding_rate_data, "fundingTime")
        self.settlement_funding_rate = from_dict_get_float(self.funding_rate_data, "settFundingRate")
        self.settlement_status = from_dict_get_string(self.funding_rate_data, "settState")
        self.method = from_dict_get_string(self.funding_rate_data, "method")
        self.has_been_init_data = True
        return self

    def get_all_data(self):
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "local_update_time": self.local_update_time,
                "server_time": self.server_time,
                "next_funding_rate": self.next_funding_rate,
                "next_funding_time": self.next_funding_time,
                "max_funding_rate": self.max_funding_rate,
                "min_funding_rate": self.min_funding_rate,
                "current_funding_rate": self.current_funding_rate,
                "current_funding_time": self.current_funding_time,
                "settlement_funding_rate": self.settlement_funding_rate,
                "settlement_status": self.settlement_status,
                "method": self.method,
            }
        return self.all_data

    def __str__(self):
        self.init_data()
        return json.dumps(self.get_all_data())

    def __repr__(self):
        return self.__str__()

    def get_exchange_name(self):
        return self.exchange_name

    def get_server_time(self):
        return self.server_time

    def get_local_update_time(self):
        return self.local_update_time

    def get_asset_type(self):
        return self.asset_type

    def get_symbol(self):
        return self.symbol_name

    def get_pre_funding_rate(self):
        return None

    def get_pre_funding_time(self):
        return None

    def get_next_funding_rate(self):
        return self.next_funding_rate

    def get_next_funding_time(self):
        return self.next_funding_time

    def get_max_funding_rate(self):
        return self.max_funding_rate

    def get_min_funding_rate(self):
        return self.min_funding_rate

    def get_current_funding_rate(self):
        return self.current_funding_rate

    def get_current_funding_time(self):
        return self.current_funding_time

    def get_settlement_funding_rate(self):
        """
        get settlement funding rate
        :return: float
        """
        return self.settlement_funding_rate

    def get_settlement_status(self):
        return self.settlement_status

    def get_method(self):
        return self.method
