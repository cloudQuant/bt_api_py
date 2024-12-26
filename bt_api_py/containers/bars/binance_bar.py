import time
import json
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_bool
from bt_api_py.containers.bars.bar import BarData


class BinanceRequestBarData(BarData):
    def __init__(self, bar_info, symbol_name, asset_type, has_been_json_encoded=False):
        super(BinanceRequestBarData, self).__init__(bar_info, has_been_json_encoded)
        self.exchange_name = "BINANCE"
        self.symbol_name = symbol_name
        self.asset_type = asset_type
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
        self.taker_buy_base_asset_volume = None
        self.taker_buy_quote_asset_volume = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        if not self.has_been_json_encoded:
            self.bar_data = json.loads(self.bar_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self
        self.open_time = float(self.bar_data[0])
        self.open_price = float(self.bar_data[1])
        self.high_price = float(self.bar_data[2])
        self.low_price = float(self.bar_data[3])
        self.close_price = float(self.bar_data[4])
        self.volume = float(self.bar_data[5])
        self.close_time = float(self.bar_data[6])
        self.amount = float(self.bar_data[7])
        self.num_trades = float(self.bar_data[8])
        self.taker_buy_base_asset_volume = float(self.bar_data[9])
        self.taker_buy_quote_asset_volume = float(self.bar_data[10])
        self.has_been_init_data = True
        return self

    def get_all_data(self):
        if self.all_data is None:
            self.all_data = {
                "open_time": self.open_time,
                "open_price": self.open_price,
                "high_price": self.high_price,
                "low_price": self.low_price,
                "close_price": self.close_price,
                "volume": self.volume,
                "close_time": self.close_time,
                "amount": self.amount,
                "num_trades": self.num_trades,
                "taker_buy_base_asset_volume": self.taker_buy_base_asset_volume,
                "taker_buy_quote_asset_volume": self.taker_buy_quote_asset_volume,
                "exchange_name": self.exchange_name,
                "local_update_time": self.local_update_time,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type
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
        return None

    def get_local_update_time(self):
        return self.local_update_time

    def get_open_time(self):
        return self.open_time

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
        return self.amount

    def get_close_time(self):
        return self.close_time

    def get_quote_asset_volume(self):
        return None

    def get_base_asset_volume(self):
        return None

    def get_num_trades(self):
        return self.num_trades

    def get_taker_buy_base_asset_volume(self):
        return self.taker_buy_base_asset_volume

    def get_taker_buy_quote_asset_volume(self):
        return self.taker_buy_quote_asset_volume

    def get_bar_status(self):
        return None

    def get_bar_data(self):
        return self.bar_data


class BinanceWssBarData(BarData):
    def __init__(self, bar_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(bar_info)
        self.exchange_name = "BINANCE"
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.local_update_time = time.time()
        self.has_been_json_encoded = has_been_json_encoded
        self.bar_data = bar_info['k'] if has_been_json_encoded else None
        self.server_time = None
        self.open_time = None
        self.open_price = None
        self.high_price = None
        self.low_price = None
        self.close_price = None
        self.volume = None
        self.amount = None
        self.close_time = None
        self.num_trades = None
        self.taker_buy_base_asset_volume = None
        self.taker_buy_quote_asset_volume = None
        self.bar_status = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        if not self.has_been_json_encoded:
            self.bar_info = json.loads(self.bar_info)
            self.bar_data = self.bar_info['k']
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self
        self.server_time = from_dict_get_float(self.bar_info, 'E')
        self.open_time = from_dict_get_float(self.bar_data, 't')
        self.open_price = from_dict_get_float(self.bar_data, 'o')
        self.high_price = from_dict_get_float(self.bar_data, 'h')
        self.low_price = from_dict_get_float(self.bar_data, 'l')
        self.close_price = from_dict_get_float(self.bar_data, 'c')
        self.volume = from_dict_get_float(self.bar_data, 'v')
        self.amount = from_dict_get_float(self.bar_data, 'q')
        self.close_time = from_dict_get_float(self.bar_data, 'T')
        self.num_trades = from_dict_get_float(self.bar_data, 'n')
        self.taker_buy_base_asset_volume = from_dict_get_float(self.bar_data, 'V')
        self.taker_buy_quote_asset_volume = from_dict_get_float(self.bar_data, 'Q')
        self.bar_status = from_dict_get_bool(self.bar_data, 'x')
        self.has_been_init_data = True
        return self

    def get_all_data(self):
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "local_update_time": self.local_update_time,
                "asset_type": self.asset_type,
                "open_time": self.open_time,
                "open_price": self.open_price,
                "high_price": self.high_price,
                "low_price": self.low_price,
                "close_price": self.close_price,
                "volume": self.volume,
                "amount": self.amount,
                "close_time": self.close_time,
                "num_trades": self.num_trades,
                "taker_buy_base_asset_volume": self.taker_buy_quote_asset_volume,
                "taker_buy_quote_asset_volume": self.taker_buy_quote_asset_volume,
                "bar_status": self.bar_status,
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
        return self.open_time

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
        return self.amount

    def get_close_time(self):
        return self.close_time

    def get_quote_asset_volume(self):
        return None

    def get_base_asset_volume(self):
        return None

    def get_num_trades(self):
        return self.num_trades

    def get_taker_buy_base_asset_volume(self):
        return self.taker_buy_base_asset_volume

    def get_taker_buy_quote_asset_volume(self):
        return self.taker_buy_quote_asset_volume

    def get_bar_status(self):
        return self.bar_status

    def get_bar_data(self):
        return self.bar_data
