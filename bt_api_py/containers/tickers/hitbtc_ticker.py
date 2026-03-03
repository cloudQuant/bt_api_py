import json
import time

from bt_api_py.containers.tickers.ticker import TickerData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string


class HitBtcRequestTickerData(TickerData):
    """保存HitBTC ticker信息"""

    def __init__(self, ticker_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(ticker_info, has_been_json_encoded)
        self.exchange_name = "HITBTC"  # 交易所名称
        self.local_update_time = time.time()  # 本地时间戳
        self.symbol_name = symbol_name
        self.asset_type = asset_type  # ticker的类型
        # Always store ticker_info for init_data() to parse
        self.ticker_data = ticker_info
        self.ticker_symbol_name = None
        self.server_time = None
        self.bid_price = None
        self.ask_price = None
        self.bid_volume = None
        self.ask_volume = None
        self.last_price = None
        self.last_volume = None
        self.high_price = None
        self.low_price = None
        self.open_price = None
        self.price_change = None
        self.price_change_percent = None
        self.count = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        if self.has_been_init_data:
            return

        if self.ticker_data is None:
            return

        # 提取数据
        self.ticker_symbol_name = from_dict_get_string(self.ticker_data, "symbol")
        self.server_time = from_dict_get_float(self.ticker_data, "timestamp")
        self.last_price = from_dict_get_float(self.ticker_data, "last")
        self.bid_price = from_dict_get_float(self.ticker_data, "bid")
        self.ask_price = from_dict_get_float(self.ticker_data, "ask")
        self.last_volume = from_dict_get_float(self.ticker_data, "volume")
        self.high_price = from_dict_get_float(self.ticker_data, "high")
        self.low_price = from_dict_get_float(self.ticker_data, "low")
        self.open_price = from_dict_get_float(self.ticker_data, "open")
        self.price_change = from_dict_get_float(self.ticker_data, "volume")
        self.price_change_percent = from_dict_get_float(self.ticker_data, "volumeQuote")
        self.count = from_dict_get_float(self.ticker_data, "count")
        self.ask_volume = from_dict_get_float(self.ticker_data, "askVolume")
        self.bid_volume = from_dict_get_float(self.ticker_data, "bidVolume")

        self.has_been_init_data = True

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
                "high_price": self.high_price,
                "low_price": self.low_price,
                "open_price": self.open_price,
                "price_change": self.price_change,
                "price_change_percent": self.price_change_percent,
                "count": self.count,
            }
        return self.all_data

    def get_exchange_name(self):
        """Get exchange name."""
        return self.exchange_name

    def get_local_update_time(self):
        """Get local update time."""
        return self.local_update_time

    def get_symbol_name(self):
        """Get symbol name."""
        return self.symbol_name

    def get_ticker_symbol_name(self):
        """Get ticker symbol name."""
        return self.ticker_symbol_name

    def get_asset_type(self):
        """Get asset type."""
        return self.asset_type

    def get_server_time(self):
        """Get server time."""
        return self.server_time

    def get_bid_price(self):
        """Get bid price."""
        return self.bid_price

    def get_ask_price(self):
        """Get ask price."""
        return self.ask_price

    def get_bid_volume(self):
        """Get bid volume."""
        return self.bid_volume

    def get_ask_volume(self):
        """Get ask volume."""
        return self.ask_volume

    def get_last_price(self):
        """Get last price."""
        return self.last_price

    def get_last_volume(self):
        """Get last volume."""
        return self.last_volume

    def __str__(self):
        return f"HITBTC Ticker {self.symbol_name}: Last={self.last_price}, Bid={self.bid_price}, Ask={self.ask_price}"

    def __repr__(self):
        return f"<HitBtcTickerData {self.symbol_name} last={self.last_price}>"