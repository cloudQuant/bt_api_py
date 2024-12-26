from btpy.containers.bars.bar import BarData
import time
import json
from btpy.functions.utils import from_dict_get_string, from_dict_get_float

class OkxBarData(BarData):
    def __init__(self, bar_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(bar_info, has_been_json_encoded)
        self.exchange_name = "OKX"
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.local_update_time = time.time()
        self.bar_data = bar_info if has_been_json_encoded else None
        self.server_time = None
        self.open_price = None
        self.high_price = None
        self.low_price = None
        self.close_price = None
        self.volume = None
        self.base_asset_volume = None
        self.quote_asset_volume = None
        self.bar_status = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        if not self.has_been_json_encoded:
            self.bar_data = json.loads(self.bar_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self
        self.server_time = float(self.bar_data[0])
        self.open_price = float(self.bar_data[1])
        self.high_price = float(self.bar_data[2])
        self.low_price = float(self.bar_data[3])
        self.close_price = float(self.bar_data[4])
        self.volume = float(self.bar_data[5])
        self.base_asset_volume = float(self.bar_data[6])
        self.quote_asset_volume = float(self.bar_data[7])
        self.bar_status = float(self.bar_data[-1])
        self.has_been_init_data = True
        return self

    def get_all_data(self):
        if self.all_data is None:
            self.all_data = {
                "open_price": self.open_price,
                "high_price": self.high_price,
                "low_price": self.low_price,
                "close_price": self.close_price,
                "volume": self.volume,
                "base_asset_volume": self.base_asset_volume,
                "quote_asset_volume": self.quote_asset_volume,
                "bar_status": self.bar_status,
                "server_time": self.server_time,
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


    def get_event_type(self):
        return self.event

    def get_exchange_name(self):
        return self.exchange_name

    def get_symbol_name(self):
        return self.symbol_name

    def get_asset_type(self):
        return self.asset_type

    def get_server_time(self):
        return self.server_time

    def get_local_update_time(self):
        return self.local_update_time

    def get_open_time(self):
        return None

    def get_open_price(self):
        return self.open_price

    def get_high_price(self):
        return self.high_price

    def get_low_price(self):
        return self.low_price

    def get_close_price(self):
        return self.close_price

    def get_volume(self):
        return self.volume

    def get_amount(self):
        return None

    def get_close_time(self):
        return None

    def get_quote_asset_volume(self):
        return self.quote_asset_volume

    def get_base_asset_volume(self):
        return self.base_asset_volume

    def get_num_trades(self):
        return None

    def get_taker_buy_base_asset_volume(self):
        return None

    def get_taker_buy_quote_asset_volume(self):
        return None

    def get_bar_status(self):
        # print("bar status:", self.bar_data)
        return self.bar_status

    def get_bar_data(self):
        return self.bar_data
