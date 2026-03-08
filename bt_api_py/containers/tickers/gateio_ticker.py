"""
Gate.io Ticker Data Container
"""

import json
import time

from bt_api_py.containers.tickers.ticker import TickerData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string


class GateioTickerData(TickerData):
    """Gate.io ticker data container"""

    def __init__(self, ticker_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(ticker_info, has_been_json_encoded)
        self.exchange_name = "GATEIO"
        self.local_update_time = time.time()
        self.ticker_data = ticker_info if has_been_json_encoded else None
        self.ticker_symbol_name = None
        self.has_been_init_data = False
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.ticker_data = ticker_info if has_been_json_encoded else None
        self.ticker_symbol_name = None
        self.server_time = None
        self.last_price = None
        self.high_24h = None
        self.low_24h = None
        self.volume_24h = None
        self.volume_quote_24h = None
        self.price_change_percentage = None
        self.bid_price = None
        self.ask_price = None
        self.base_volume = None
        self.quote_volume = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        """Initialize and parse ticker data"""
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True

        if self.has_been_init_data:
            return self

        # Parse ticker data
        self.ticker_symbol_name = from_dict_get_string(self.ticker_data, "currency_pair")
        self.server_time = from_dict_get_float(self.ticker_data, "timestamp") or time.time()
        self.last_price = from_dict_get_float(self.ticker_data, "last")
        self.high_24h = from_dict_get_float(self.ticker_data, "high_24h")
        self.low_24h = from_dict_get_float(self.ticker_data, "low_24h")
        self.volume_24h = from_dict_get_float(self.ticker_data, "base_volume")
        self.volume_quote_24h = from_dict_get_float(self.ticker_data, "quote_volume")
        self.price_change_percentage = from_dict_get_float(self.ticker_data, "change_percentage")
        self.bid_price = from_dict_get_float(self.ticker_data, "highest_bid")
        self.ask_price = from_dict_get_float(self.ticker_data, "lowest_ask")
        self.base_volume = from_dict_get_float(self.ticker_data, "base_volume")
        self.quote_volume = from_dict_get_float(self.ticker_data, "quote_volume")

        self.has_been_init_data = True
        return self

    def get_all_data(self):
        """Get all ticker data as dictionary"""
        if self.all_data is None:
            self.init_data()
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "ticker_symbol_name": self.ticker_symbol_name,
                "server_time": self.server_time,
                "last_price": self.last_price,
                "high_24h": self.high_24h,
                "low_24h": self.low_24h,
                "volume_24h": self.volume_24h,
                "volume_quote_24h": self.volume_quote_24h,
                "price_change_percentage": self.price_change_percentage,
                "bid_price": self.bid_price,
                "ask_price": self.ask_price,
                "base_volume": self.base_volume,
                "quote_volume": self.quote_volume,
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

    def get_last_price(self):
        return self.last_price

    def get_high_24h(self):
        return self.high_24h

    def get_low_24h(self):
        return self.low_24h

    def get_volume_24h(self):
        return self.volume_24h

    def get_price_change_percentage(self):
        return self.price_change_percentage

    def get_bid_price(self):
        return self.bid_price

    def get_ask_price(self):
        return self.ask_price

    def get_base_volume(self):
        return self.base_volume

    def get_quote_volume(self):
        return self.quote_volume


class GateioRequestTickerData(GateioTickerData):
    """Gate.io REST API ticker data"""

    def init_data(self):
        """Initialize and parse ticker data from REST API"""
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True

        if self.has_been_init_data:
            return self

        # Parse REST API ticker data
        self.ticker_symbol_name = from_dict_get_string(self.ticker_data, "currency_pair")
        self.server_time = from_dict_get_float(self.ticker_data, "time") or time.time()
        self.last_price = from_dict_get_float(self.ticker_data, "last")
        self.high_24h = from_dict_get_float(self.ticker_data, "high_24h")
        self.low_24h = from_dict_get_float(self.ticker_data, "low_24h")
        self.volume_24h = from_dict_get_float(self.ticker_data, "base_volume")
        self.volume_quote_24h = from_dict_get_float(self.ticker_data, "quote_volume")
        self.price_change_percentage = from_dict_get_float(self.ticker_data, "change_percentage")
        self.bid_price = from_dict_get_float(self.ticker_data, "highest_bid")
        self.ask_price = from_dict_get_float(self.ticker_data, "lowest_ask")
        self.base_volume = from_dict_get_float(self.ticker_data, "base_volume")
        self.quote_volume = from_dict_get_float(self.ticker_data, "quote_volume")

        self.has_been_init_data = True
        return self


class GateioWssTickerData(GateioTickerData):
    """Gate.io WebSocket ticker data (placeholder for future implementation)"""

    def init_data(self):
        """Initialize and parse WebSocket ticker data"""
        # TODO: Implement WebSocket ticker data parsing
        return self
