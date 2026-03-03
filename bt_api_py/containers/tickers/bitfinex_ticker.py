import json
import time

from bt_api_py.containers.tickers.ticker import TickerData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string


class BitfinexTickerData(TickerData):
    """保存 Bitfinex ticker 信息"""

    def __init__(self, ticker_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(ticker_info, has_been_json_encoded)
        self.exchange_name = "BITFINEX"  # 交易所名称
        self.local_update_time = time.time()
        self.ticker_data = ticker_info if has_been_json_encoded else None
        self.ticker_symbol_name = None
        self.has_been_init_data = False  # 本地时间戳
        self.symbol_name = symbol_name
        self.asset_type = asset_type  # ticker的类型
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

        if isinstance(self.ticker_data, list) and len(self.ticker_data) >= 8:
            # Bitfinex ticker 格式:
            # [SYMBOL, BID, BID_SIZE, ASK, ASK_SIZE, DAILY_CHANGE, DAILY_CHANGE_PERC, LAST_PRICE, VOLUME, HIGH, LOW]
            self.ticker_symbol_name = from_dict_get_string(self.ticker_data[0], "")
            self.bid_price = from_dict_get_float(self.ticker_data[1], 0.0)
            self.bid_volume = from_dict_get_float(self.ticker_data[2], 0.0)
            self.ask_price = from_dict_get_float(self.ticker_data[3], 0.0)
            self.ask_volume = from_dict_get_float(self.ticker_data[4], 0.0)
            self.daily_change = from_dict_get_float(self.ticker_data[5], 0.0)
            self.daily_change_percentage = from_dict_get_float(self.ticker_data[6], 0.0)
            self.last_price = from_dict_get_float(self.ticker_data[7], 0.0)
            self.volume = from_dict_get_float(self.ticker_data[8], 0.0) if len(self.ticker_data) > 8 else None
            self.high = from_dict_get_float(self.ticker_data[9], 0.0) if len(self.ticker_data) > 9 else None
            self.low = from_dict_get_float(self.ticker_data[10], 0.0) if len(self.ticker_data) > 10 else None
            self.server_time = time.time()  # Bitfinex 不提供服务器时间，使用本地时间

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


class BitfinexWssTickerData(BitfinexTickerData):
    """保存 Bitfinex WebSocket ticker 信息"""

    def init_data(self):
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        # Bitfinex WebSocket ticker 格式:
        # [SYMBOL, BID, BID_SIZE, ASK, ASK_SIZE, DAILY_CHANGE, DAILY_CHANGE_PERC, LAST_PRICE, VOLUME, HIGH, LOW, TIMESTAMP]
        if isinstance(self.ticker_data, list) and len(self.ticker_data) >= 12:
            self.ticker_symbol_name = self.ticker_data[0]
            self.bid_price = float(self.ticker_data[1])
            self.bid_volume = float(self.ticker_data[2])
            self.ask_price = float(self.ticker_data[3])
            self.ask_volume = float(self.ticker_data[4])
            self.daily_change = float(self.ticker_data[5])
            self.daily_change_percentage = float(self.ticker_data[6])
            self.last_price = float(self.ticker_data[7])
            self.volume = float(self.ticker_data[8]) if len(self.ticker_data) > 8 else None
            self.high = float(self.ticker_data[9]) if len(self.ticker_data) > 9 else None
            self.low = float(self.ticker_data[10]) if len(self.ticker_data) > 10 else None
            self.server_time = float(self.ticker_data[11]) / 1000 if len(self.ticker_data) > 11 else time.time()

        self.has_been_init_data = True
        return self


class BitfinexRequestTickerData(BitfinexTickerData):
    """保存 Bitfinex REST API ticker 信息"""

    def init_data(self):
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        # Bitfinex REST API ticker 格式与 WebSocket 相同
        if isinstance(self.ticker_data, list) and len(self.ticker_data) >= 8:
            self.ticker_symbol_name = self.ticker_data[0]
            self.bid_price = float(self.ticker_data[1])
            self.bid_volume = float(self.ticker_data[2])
            self.ask_price = float(self.ticker_data[3])
            self.ask_volume = float(self.ticker_data[4])
            self.daily_change = float(self.ticker_data[5])
            self.daily_change_percentage = float(self.ticker_data[6])
            self.last_price = float(self.ticker_data[7])
            self.volume = float(self.ticker_data[8]) if len(self.ticker_data) > 8 else None
            self.high = float(self.ticker_data[9]) if len(self.ticker_data) > 9 else None
            self.low = float(self.ticker_data[10]) if len(self.ticker_data) > 10 else None
            self.server_time = time.time()  # REST API 不提供服务器时间

        self.has_been_init_data = True
        return self