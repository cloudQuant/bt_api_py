import json
import time
from bt_api_py.containers.tickers.ticker import TickerData
from bt_api_py.functions.utils import from_dict_get_string, from_dict_get_float


class OkxTickerData(TickerData):
    """保存ticker信息"""

    def __init__(self, ticker_info, symbol_name, asset_type, has_been_json_encoded=False):
        super(OkxTickerData, self).__init__(ticker_info, has_been_json_encoded)
        self.exchange_name = 'OKX'  # 交易所名称
        self.local_update_time = time.time()  # 本地时间戳
        self.asset_type = asset_type  # ticker的类型
        self.symbol_name = symbol_name
        self.ticker_data = self.ticker_info
        self.ticker_symbol_name = None
        self.server_time = None
        self.bid_price = None
        self.ask_price = None
        self.bid_volume = None
        self.ask_volume = None
        self.last_price = None
        self.last_volume = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self
        self.ticker_symbol_name = from_dict_get_string(self.ticker_data, "instId")
        self.server_time = from_dict_get_float(self.ticker_data, "ts")
        self.bid_price = from_dict_get_float(self.ticker_data, "bidPx")
        self.ask_price = from_dict_get_float(self.ticker_data, "askPx")
        self.bid_volume = from_dict_get_float(self.ticker_data, "bidSz")
        self.ask_volume = from_dict_get_float(self.ticker_data, "askSz")
        self.last_price = from_dict_get_float(self.ticker_data, "last")
        self.last_volume = from_dict_get_float(self.ticker_data, "lastSz")
        self.has_been_init_data = True
        return self

    def get_all_data(self):
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "ticker_symbol_name": self.ticker_symbol_name,
                "server_time": self.server_time,
                "bid_price": self.bid_price,
                "ask_price": self.ask_price,
                "bid_volume": self.bid_volume,
                "ask_volume": self.ask_volume,
                "last_price": self.last_price,
                "last_volume": self.last_volume,
            }
        return self.all_data

    def __str__(self):
        self.init_data()
        return json.dumps(self.get_all_data())

    def __repr__(self):
        return self.__str__()

    def get_exchange_name(self):
        return self.exchange_name

    def get_local_update_time(self):
        return self.local_update_time

    def get_symbol_name(self):
        return self.symbol_name

    def get_ticker_symbol_name(self):
        return self.ticker_symbol_name

    def get_asset_type(self):
        return self.asset_type

    def get_server_time(self):
        return self.server_time

    def get_bid_price(self):
        return self.bid_price

    def get_ask_price(self):
        return self.ask_price

    def get_bid_volume(self):
        return self.bid_volume

    def get_ask_volume(self):
        return self.ask_volume

    def get_last_price(self):
        return self.last_price

    def get_last_volume(self):
        return self.last_volume