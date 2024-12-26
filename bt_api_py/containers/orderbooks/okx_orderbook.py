import time
import json
from bt_api_py.containers.orderbooks.orderbook import OrderBookData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string


class OkxOrderBookData(OrderBookData):
    """保存订单簿相关信息"""

    def __init__(self, order_book_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(order_book_info, has_been_json_encoded)
        self.exchange_name = 'OKX'  # 交易所名称
        self.local_update_time = time.time()  # 本地时间戳
        self.symbol_name = symbol_name  # instrument name
        self.asset_type = asset_type  # order_book的类型
        self.order_book_data = order_book_info if has_been_json_encoded else None
        self.order_book_symbol_name = None
        self.server_time = None
        self.bid_price_list = None
        self.ask_price_list = None
        self.bid_volume_list = None
        self.ask_volume_list = None
        self.bid_trade_nums = None
        self.ask_trade_nums = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        if not self.has_been_json_encoded:
            self.order_book_info = json.loads(self.order_book_info)
            self.order_book_data = self.order_book_info['data'][0]
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self
        if "arg" in self.order_book_info:
            self.order_book_symbol_name = from_dict_get_string(self.order_book_info["arg"], "instId")
        self.server_time = from_dict_get_float(self.order_book_data, "ts")
        self.bid_price_list = [float(i[0]) for i in self.order_book_data['bids']]
        self.ask_price_list = [float(i[0]) for i in self.order_book_data['asks']]
        self.bid_volume_list = [float(i[1]) for i in self.order_book_data['bids']]
        self.ask_volume_list = [float(i[1]) for i in self.order_book_data['asks']]
        self.bid_trade_nums = [float(i[3]) for i in self.order_book_data['bids']]
        self.ask_trade_nums = [float(i[3]) for i in self.order_book_data['asks']]
        self.has_been_init_data = True
        return self

    def get_all_data(self):
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "asset_type": self.asset_type,
                "symbol_name": self.symbol_name,
                "order_book_symbol_name": self.order_book_symbol_name,
                "local_update_time": self.local_update_time,
                "server_time": self.server_time,
                "bid_price_list": self.bid_price_list,
                "ask_price_list": self.ask_price_list,
                "bid_volume_list": self.bid_volume_list,
                "ask_volume_list": self.ask_volume_list,
                "bid_trade_nums": self.bid_trade_nums,
                "ask_trade_nums": self.ask_trade_nums,
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

    def get_bid_price_list(self):
        return self.bid_price_list

    def get_ask_price_list(self):
        return self.ask_price_list

    def get_bid_volume_list(self):
        return self.bid_volume_list

    def get_ask_volume_list(self):
        return self.ask_volume_list

    def get_bid_trade_nums(self):
        return self.bid_trade_nums

    def get_ask_trade_nums(self):
        return self.ask_trade_nums
