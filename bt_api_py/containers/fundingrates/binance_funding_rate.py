import time
import json
from bt_api_py.containers.fundingrates.funding_rate import FundingRateData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string


class BinanceRequestFundingRateData(FundingRateData):
    """保存资金费率信息"""

    def __init__(self, funding_rate_info, symbol_name, asset_type, has_been_json_encoded):
        super().__init__(funding_rate_info, has_been_json_encoded)
        self.exchange_name = "BINANCE"
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.local_update_time = time.time()
        self.funding_rate_data = funding_rate_info if has_been_json_encoded else None
        self.server_time = None
        self.funding_rate_symbol_name = None
        self.next_funding_rate = None
        self.next_funding_rate_time = None
        self.current_funding_rate = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        if not self.has_been_json_encoded:
            self.funding_rate_data = json.loads(self.funding_rate_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self
        # {'symbol': 'BTCUSDT', 'fundingTime': 1731744000001, 'fundingRate': '0.00010000', 'markPrice': '91172.07627273'}
        self.server_time = from_dict_get_float(self.funding_rate_data, "time")
        self.funding_rate_symbol_name = from_dict_get_string(self.funding_rate_data, 'symbol')
        self.next_funding_rate = from_dict_get_float(self.funding_rate_data, 'nextFundingRate')
        self.next_funding_rate_time = from_dict_get_float(self.funding_rate_data, 'nextFundingTime')
        self.current_funding_rate = from_dict_get_float(self.funding_rate_data, 'lastFundingRate')
        self.has_been_init_data = True
        return self

    def get_all_data(self):
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "next_funding_rate": self.next_funding_rate,
                "next_funding_rate_time": self.next_funding_rate_time,
                "current_funding_rate": self.current_funding_rate,
                "server_time": self.server_time,
                "funding_rate_symbol_name": self.funding_rate_symbol_name,
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

    def get_symbol_name(self):
        return self.symbol_name

    def get_funding_rate_symbol_name(self):
        return self.funding_rate_symbol_name

    def get_pre_funding_rate(self):
        return None

    def get_pre_funding_time(self):
        return None

    def get_next_funding_rate(self):
        return self.next_funding_rate

    def get_next_funding_time(self):
        return self.next_funding_rate_time

    def get_max_funding_rate(self):
        return None

    def get_min_funding_rate(self):
        return None

    def get_current_funding_rate(self):
        return self.current_funding_rate

    def get_current_funding_time(self):
        return None

    def get_settlement_funding_rate(self):
        return None

    def get_settlement_status(self):
        return None

    def get_method(self):
        return None


class BinanceRequestHistoryFundingRateData(FundingRateData):
    """保存资金费率信息"""

    def __init__(self, funding_rate_info, symbol_name, asset_type, has_been_json_encoded):
        super().__init__(funding_rate_info, has_been_json_encoded)
        self.exchange_name = "BINANCE"
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.local_update_time = time.time()
        self.funding_rate_data = funding_rate_info if has_been_json_encoded else None
        self.server_time = None
        self.current_funding_rate = None
        self.mark_price = None
        self.funding_rate_symbol_name = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        if not self.has_been_json_encoded:
            self.funding_rate_data = json.loads(self.funding_rate_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self
        # {'symbol': 'BTCUSDT', 'fundingTime': 1731744000001, 'fundingRate': '0.00010000', 'markPrice': '91172.07627273'}
        self.server_time = from_dict_get_float(self.funding_rate_data, "fundingTime")
        self.current_funding_rate = from_dict_get_float(self.funding_rate_data, 'fundingRate')
        self.mark_price = from_dict_get_float(self.funding_rate_data, 'markPrice')
        self.has_been_init_data = True
        return self

    def get_all_data(self):
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "current_funding_rate": self.current_funding_rate,
                "server_time": self.server_time,
                "mark_price": self.mark_price,
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

    def get_symbol_name(self):
        return self.symbol_name

    def get_funding_rate_symbol_name(self):
        return self.funding_rate_symbol_name

    def get_pre_funding_rate(self):
        return None

    def get_pre_funding_time(self):
        return None

    def get_next_funding_rate(self):
        return None

    def get_next_funding_time(self):
        return None

    def get_max_funding_rate(self):
        return None

    def get_min_funding_rate(self):
        return None

    def get_current_funding_rate(self):
        return self.current_funding_rate

    def get_current_funding_time(self):
        return None

    def get_settlement_funding_rate(self):
        return None

    def get_settlement_status(self):
        return None

    def get_method(self):
        return None


class BinanceWssFundingRateData(FundingRateData):
    """保存资金费率信息"""

    def __init__(self, funding_rate_info, symbol_name, asset_type, has_been_json_encoded):
        super().__init__(funding_rate_info, has_been_json_encoded)
        self.exchange_name = "BINANCE"
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.local_update_time = time.time()
        self.funding_rate_data = funding_rate_info if has_been_json_encoded else None
        self.server_time = None
        self.funding_rate_symbol_name = None
        self.next_funding_time = None
        self.current_funding_rate = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        if not self.has_been_json_encoded:
            self.funding_rate_data = json.loads(self.funding_rate_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self
        self.server_time = from_dict_get_float(self.funding_rate_data, "E")
        self.funding_rate_symbol_name = from_dict_get_string(self.funding_rate_data, "s")
        self.next_funding_time = from_dict_get_float(self.funding_rate_data, "T")
        self.current_funding_rate = from_dict_get_float(self.funding_rate_data, "r")
        self.has_been_init_data = True
        return self

    def get_all_data(self):
        if self.all_data is None:
            self.all_data = {
                "server_time": self.server_time,
                "next_funding_time": self.next_funding_time,
                "current_funding_rate": self.current_funding_rate,
                "funding_rate_symbol_name": self.funding_rate_symbol_name,
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "local_update_time": self.local_update_time,
                "asset_type": self.asset_type,
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

    def get_symbol_name(self):
        return self.symbol_name

    def get_funding_rate_symbol_name(self):
        return self.funding_rate_symbol_name

    def get_pre_funding_rate(self):
        return None

    def get_pre_funding_time(self):
        return None

    def get_next_funding_rate(self):
        return None

    def get_next_funding_time(self):
        return self.next_funding_time

    def get_max_funding_rate(self):
        return None

    def get_min_funding_rate(self):
        return None

    def get_current_funding_rate(self):
        return self.current_funding_rate

    def get_current_funding_time(self):
        return None

    def get_settlement_funding_rate(self):
        return None

    def get_settlement_status(self):
        return None

    def get_method(self):
        return None
