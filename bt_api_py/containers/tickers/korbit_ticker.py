import json
import time

from bt_api_py.containers.tickers.ticker import TickerData
from bt_api_py.functions.utils import from_dict_get_float


class KorbitTickerData(TickerData):
    """保存 Korbit ticker 信息"""

    def __init__(self, ticker_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(ticker_info, has_been_json_encoded)
        self.exchange_name = "KORBIT"
        self.local_update_time = time.time()
        self.ticker_data = ticker_info if has_been_json_encoded else None
        self.ticker_symbol_name = None
        self.has_been_init_data = False
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.ticker_data = ticker_info if has_been_json_encoded else None
        self.ticker_symbol_name = None
        self.server_time = None
        self.bid_price = None
        self.ask_price = None
        self.bid_volume = None
        self.ask_volume = None
        self.last_price = None
        self.daily_change = None
        self.daily_change_percentage = None
        self.volume = None
        self.high = None
        self.low = None
        self.has_been_init_data = False

    def init_data(self):
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        # Korbit ticker format (dict):
        # {
        #   "last": "95000000",
        #   "bid": "94990000",
        #   "ask": "95000000",
        #   "low": "93500000",
        #   "high": "95800000",
        #   "volume": "1234.56",
        #   "change": "500000",
        #   "changePercent": "0.53",
        #   "timestamp": 1678901234000
        # }
        if isinstance(self.ticker_data, dict):
            data = self.ticker_data
            self.ticker_symbol_name = self.symbol_name
            self.last_price = from_dict_get_float(data, "last", 0.0)
            self.bid_price = from_dict_get_float(data, "bid", 0.0)
            self.ask_price = from_dict_get_float(data, "ask", 0.0)
            self.high = from_dict_get_float(data, "high", 0.0)
            self.low = from_dict_get_float(data, "low", 0.0)
            self.volume = from_dict_get_float(data, "volume", 0.0)

            # Daily change
            change = from_dict_get_float(data, "change", 0.0)
            self.daily_change = change

            # Daily change percentage
            change_percent = from_dict_get_float(data, "changePercent", 0.0)
            self.daily_change_percentage = change_percent

            # Timestamp (milliseconds)
            timestamp = data.get("timestamp")
            if timestamp:
                self.server_time = float(timestamp) / 1000
            else:
                self.server_time = time.time()

        self.has_been_init_data = True
        return self

    def get_all_data(self):
        if self.all_data is None:
            self.init_data()
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
                "daily_change": self.daily_change,
                "daily_change_percentage": self.daily_change_percentage,
                "volume": self.volume,
                "high": self.high,
                "low": self.low,
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

    def get_daily_change(self):
        return self.daily_change

    def get_daily_change_percentage(self):
        return self.daily_change_percentage

    def get_volume(self):
        return self.volume

    def get_high(self):
        return self.high

    def get_low(self):
        return self.low


class KorbitWssTickerData(KorbitTickerData):
    """保存 Korbit WebSocket ticker 信息"""

    def init_data(self):
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        # WebSocket ticker format (similar to REST but may have different structure)
        if isinstance(self.ticker_data, dict):
            data = self.ticker_data
            self.ticker_symbol_name = self.symbol_name
            self.last_price = from_dict_get_float(data, "last", 0.0)
            self.bid_price = from_dict_get_float(data, "bid", 0.0)
            self.ask_price = from_dict_get_float(data, "ask", 0.0)
            self.high = from_dict_get_float(data, "high", 0.0)
            self.low = from_dict_get_float(data, "low", 0.0)
            self.volume = from_dict_get_float(data, "volume", 0.0)

            change = from_dict_get_float(data, "change", 0.0)
            self.daily_change = change

            change_percent = from_dict_get_float(data, "changePercent", 0.0)
            self.daily_change_percentage = change_percent

            timestamp = data.get("timestamp")
            if timestamp:
                self.server_time = float(timestamp) / 1000
            else:
                self.server_time = time.time()

        self.has_been_init_data = True
        return self


class KorbitRequestTickerData(KorbitTickerData):
    """保存 Korbit REST API ticker 信息"""

    def init_data(self):
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        # REST API ticker format
        if isinstance(self.ticker_data, dict):
            data = self.ticker_data
            self.ticker_symbol_name = self.symbol_name
            self.last_price = from_dict_get_float(data, "last", 0.0)
            self.bid_price = from_dict_get_float(data, "bid", 0.0)
            self.ask_price = from_dict_get_float(data, "ask", 0.0)
            self.high = from_dict_get_float(data, "high", 0.0)
            self.low = from_dict_get_float(data, "low", 0.0)
            self.volume = from_dict_get_float(data, "volume", 0.0)

            change = from_dict_get_float(data, "change", 0.0)
            self.daily_change = change

            change_percent = from_dict_get_float(data, "changePercent", 0.0)
            self.daily_change_percentage = change_percent

            timestamp = data.get("timestamp")
            if timestamp:
                self.server_time = float(timestamp) / 1000
            else:
                self.server_time = time.time()

        self.has_been_init_data = True
        return self
