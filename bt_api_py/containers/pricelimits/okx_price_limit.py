"""
OKX Price Limit Data Container.
"""

import json
import time

from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string


class OkxPriceLimitData:
    """OKX price limit data from price-limit channel.

    Represents the buy/sell limit prices for an instrument.
    """

    def __init__(self, price_limit_info, symbol_name, asset_type, has_been_json_encoded=False):
        self.event = "PriceLimitEvent"
        self.price_limit_info = price_limit_info
        self.has_been_json_encoded = has_been_json_encoded
        self.exchange_name = "OKX"
        self.local_update_time = time.time()
        self.asset_type = asset_type
        self.symbol_name = symbol_name
        self.price_limit_data = price_limit_info if has_been_json_encoded else None
        self.has_been_init_data = False

        # Data fields
        self.server_time = None
        self.price_limit_symbol_name = None
        self.buy_limit = None
        self.sell_limit = None
        self.all_data = None

    def init_data(self):
        if not self.has_been_json_encoded:
            self.price_limit_data = json.loads(self.price_limit_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        self.price_limit_data = self.price_limit_data
        self.server_time = from_dict_get_float(self.price_limit_data, "ts")
        self.price_limit_symbol_name = from_dict_get_string(self.price_limit_data, "instId")
        self.buy_limit = from_dict_get_float(self.price_limit_data, "buyLmt")
        self.sell_limit = from_dict_get_float(self.price_limit_data, "sellLmt")

        self.has_been_init_data = True
        return self

    def get_all_data(self):
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "local_update_time": self.local_update_time,
                "asset_type": self.asset_type,
                "server_time": self.server_time,
                "price_limit_symbol_name": self.price_limit_symbol_name,
                "buy_limit": self.buy_limit,
                "sell_limit": self.sell_limit,
            }
        return self.all_data

    def get_event(self):
        return self.event

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

    def get_price_limit_symbol_name(self):
        return self.price_limit_symbol_name

    def get_buy_limit(self):
        return self.buy_limit

    def get_sell_limit(self):
        return self.sell_limit

    def __str__(self):
        self.init_data()
        return json.dumps(self.get_all_data())

    def __repr__(self):
        return self.__str__()
