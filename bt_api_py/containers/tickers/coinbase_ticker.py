"""
Coinbase Ticker Data Container
"""

import json
import time
from datetime import datetime

from bt_api_py.containers.tickers.ticker import TickerData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string


def parse_iso_time_to_timestamp(time_str: str) -> float:
    """Parse ISO 8601 time string to Unix timestamp."""
    if not time_str:
        return None
    try:
        # Handle various ISO formats
        time_str = time_str.replace("Z", "+00:00")
        # Remove microseconds if present
        if "." in time_str:
            time_str = time_str.split(".")[0] + time_str.split(".")[1][-6:]  # Keep timezone
        dt = datetime.fromisoformat(time_str)
        return dt.timestamp()
    except Exception:
        return None


class CoinbaseTickerData(TickerData):
    """保存Coinbase ticker信息"""

    def __init__(self, ticker_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(ticker_info, has_been_json_encoded)
        self.exchange_name = "COINBASE"  # 交易所名称
        self.local_update_time = time.time()
        self.ticker_data = ticker_info if has_been_json_encoded else None
        self.ticker_symbol_name = None
        self.has_been_init_data = False  # 本地时间戳
        self.symbol_name = symbol_name
        self.asset_type = asset_type  # ticker的类型
        # If already JSON encoded, parse it to dict; otherwise store raw
        if has_been_json_encoded:
            self.ticker_data = (
                json.loads(ticker_info) if isinstance(ticker_info, str) else ticker_info
            )
        else:
            self.ticker_data = None
        self.ticker_symbol_name = None
        self.server_time = None
        self.bid_price = None
        self.ask_price = None
        self.bid_volume = None
        self.ask_volume = None
        self.last_price = None
        self.last_volume = None
        self.price_24h_change = None
        self.price_24h_change_percent = None
        self.volume_24h = None
        self.high_24h = None
        self.low_24h = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self
        try:
            # Ticker data from /brokerage/products/{product_id}/ticker
            if isinstance(self.ticker_data, dict):
                self.ticker_symbol_name = from_dict_get_string(self.ticker_data, "product_id")
                # Handle ISO timestamp for server_time
                time_str = from_dict_get_string(self.ticker_data, "time")
                self.server_time = parse_iso_time_to_timestamp(time_str)
                self.bid_price = from_dict_get_float(self.ticker_data, "best_bid")
                self.ask_price = from_dict_get_float(self.ticker_data, "best_ask")
                self.last_price = from_dict_get_float(self.ticker_data, "last_trade")
                self.volume_24h = from_dict_get_float(self.ticker_data, "volume_24h")
                self.price_24h_change_percent = from_dict_get_float(
                    self.ticker_data, "price_percentage_change_24h"
                )

                # Calculate bid/ask volumes (if available)
                if "bids" in self.ticker_data and isinstance(self.ticker_data["bids"], list):
                    self.bid_volume = sum(float(bid[1]) for bid in self.ticker_data["bids"][:5])
                if "asks" in self.ticker_data and isinstance(self.ticker_data["asks"], list):
                    self.ask_volume = sum(float(ask[1]) for ask in self.ticker_data["asks"][:5])
        except Exception as e:
            print(f"Error parsing ticker data: {e}")
            self.ticker_data = {}
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
                "last_volume": self.last_volume,
                "price_24h_change": self.price_24h_change,
                "price_24h_change_percent": self.price_24h_change_percent,
                "volume_24h": self.volume_24h,
                "high_24h": self.high_24h,
                "low_24h": self.low_24h,
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
        self.init_data()
        return self.bid_price

    def get_ask_price(self):
        self.init_data()
        return self.ask_price

    def get_bid_volume(self):
        self.init_data()
        return self.bid_volume

    def get_ask_volume(self):
        self.init_data()
        return self.ask_volume

    def get_last_price(self):
        self.init_data()
        return self.last_price

    def get_last_volume(self):
        self.init_data()
        return self.last_volume

    def get_price_24h_change(self):
        self.init_data()
        return self.price_24h_change

    def get_price_24h_change_percent(self):
        self.init_data()
        return self.price_24h_change_percent

    def get_volume_24h(self):
        self.init_data()
        return self.volume_24h

    def get_high_24h(self):
        self.init_data()
        return self.high_24h

    def get_low_24h(self):
        self.init_data()
        return self.low_24h


class CoinbaseWssTickerData(CoinbaseTickerData):
    """保存WebSocket ticker信息"""

    def init_data(self):
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        # Ensure ticker_data is a dict
        if isinstance(self.ticker_data, str):
            self.ticker_data = json.loads(self.ticker_data)
        if self.has_been_init_data:
            return self
        try:
            # WebSocket ticker data
            if isinstance(self.ticker_data, dict):
                self.ticker_symbol_name = from_dict_get_string(self.ticker_data, "product_id")
                # Handle ISO timestamp for server_time
                time_str = from_dict_get_string(self.ticker_data, "time")
                self.server_time = parse_iso_time_to_timestamp(time_str)
                self.last_price = from_dict_get_float(self.ticker_data, "price")
                self.volume_24h = from_dict_get_float(self.ticker_data, "volume_24h")
                self.price_24h_change_percent = from_dict_get_float(
                    self.ticker_data, "price_percentage_change_24h"
                )
        except Exception as e:
            print(f"Error parsing WebSocket ticker data: {e}")
            self.ticker_data = {}
        self.has_been_init_data = True
        return self


class CoinbaseRequestTickerData(CoinbaseTickerData):
    """保存REST API ticker信息"""

    def init_data(self):
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        # Ensure ticker_data is a dict
        if isinstance(self.ticker_data, str):
            self.ticker_data = json.loads(self.ticker_data)
        if self.has_been_init_data:
            return self
        try:
            # REST API ticker data - support both "price" and "last_trade" fields
            if isinstance(self.ticker_data, dict):
                self.ticker_symbol_name = from_dict_get_string(self.ticker_data, "product_id")
                # Handle ISO timestamp for server_time
                time_str = from_dict_get_string(self.ticker_data, "time")
                self.server_time = parse_iso_time_to_timestamp(time_str)
                # Try "last_trade" first, fall back to "price"
                self.last_price = from_dict_get_float(
                    self.ticker_data, "last_trade"
                ) or from_dict_get_float(self.ticker_data, "price")
                self.volume_24h = from_dict_get_float(self.ticker_data, "volume_24h")
                self.price_24h_change_percent = from_dict_get_float(
                    self.ticker_data, "price_percentage_change_24h"
                )
                # Also try to get bid/ask from best_bid/best_ask
                self.bid_price = from_dict_get_float(self.ticker_data, "best_bid")
                self.ask_price = from_dict_get_float(self.ticker_data, "best_ask")
        except Exception as e:
            print(f"Error parsing REST ticker data: {e}")
            self.ticker_data = {}
        self.has_been_init_data = True
        return self
