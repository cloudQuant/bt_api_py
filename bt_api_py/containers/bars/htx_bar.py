"""HTX Bar/Kline Data Container"""

import json
import time

from bt_api_py.containers.bars.bar import BarData
from bt_api_py.functions.utils import from_dict_get_float


class HtxRequestBarData(BarData):
    """HTX REST API kline/bar data.

    HTX kline response format (from /market/history/kline):
    {
        "id": 1234567890,      # timestamp in seconds
        "open": 50000,
        "close": 50500,
        "low": 49000,
        "high": 51000,
        "amount": 1234.56,     # trade amount (base currency)
        "vol": 61728350,       # trade volume (quote currency)
        "count": 10000         # number of trades
    }
    """

    def __init__(self, bar_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(bar_info, has_been_json_encoded)
        self.exchange_name = "HTX"
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.server_time = None
        self.local_update_time = time.time()
        self.bar_data = bar_info if has_been_json_encoded else None
        self.open_time = None
        self.open_price = None
        self.high_price = None
        self.low_price = None
        self.close_price = None
        self.volume = None
        self.close_time = None
        self.amount = None
        self.num_trades = None
        self.bar_status = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        if self.has_been_init_data:
            return self

        if not self.has_been_json_encoded:
            self.bar_data = json.loads(self.bar_info)
            self.has_been_json_encoded = True

        self.open_time = from_dict_get_float(self.bar_data, "id")
        self.server_time = self.open_time
        self.open_price = from_dict_get_float(self.bar_data, "open")
        self.high_price = from_dict_get_float(self.bar_data, "high")
        self.low_price = from_dict_get_float(self.bar_data, "low")
        self.close_price = from_dict_get_float(self.bar_data, "close")
        self.amount = from_dict_get_float(self.bar_data, "amount")
        self.volume = from_dict_get_float(self.bar_data, "vol")
        self.num_trades = from_dict_get_float(self.bar_data, "count")
        self.bar_status = True
        self.has_been_init_data = True
        return self

    def get_all_data(self):
        if not self.has_been_init_data:
            self.init_data()
        if self.all_data is None:
            self.all_data = {
                "server_time": self.server_time,
                "open_time": self.open_time,
                "open_price": self.open_price,
                "high_price": self.high_price,
                "low_price": self.low_price,
                "close_price": self.close_price,
                "volume": self.volume,
                "amount": self.amount,
                "num_trades": self.num_trades,
                "exchange_name": self.exchange_name,
                "local_update_time": self.local_update_time,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type,
                "bar_status": self.bar_status,
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

    def get_asset_type(self):
        return self.asset_type

    def get_server_time(self):
        return self.server_time

    def get_open_time(self):
        self.init_data()
        return self.open_time

    def get_open_price(self):
        self.init_data()
        return self.open_price

    def get_high_price(self):
        self.init_data()
        return self.high_price

    def get_low_price(self):
        self.init_data()
        return self.low_price

    def get_close_price(self):
        self.init_data()
        return self.close_price

    def get_volume(self):
        self.init_data()
        return self.volume

    def get_amount(self):
        self.init_data()
        return self.amount

    def get_num_trades(self):
        self.init_data()
        return self.num_trades

    def get_bar_status(self):
        self.init_data()
        return self.bar_status
