"""
KuCoin orderbook data container.
"""

import json
import time

from bt_api_py.containers.orderbooks.orderbook import OrderBookData
from bt_api_py.functions.utils import from_dict_get_float


class KuCoinOrderBookData(OrderBookData):
    """Base class for KuCoin orderbook data."""

    def __init__(self, order_book_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(order_book_info, has_been_json_encoded)
        self.exchange_name = "KUCOIN"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
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
        raise NotImplementedError("Subclasses must implement init_data")

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


class KuCoinRequestOrderBookData(KuCoinOrderBookData):
    """KuCoin REST API orderbook data.

    API response format for level2_100:
    {
        "code": "200000",
        "data": {
            "time": 1688671955000,
            "sequence": "1234567890",
            "bids": [
                ["49999", "1.5"],
                ["49998", "2.3"]
            ],
            "asks": [
                ["50001", "1.8"],
                ["50002", "3.2"]
            ]
        }
    }

    For level2_20 (aggregated):
    {
        "code": "200000",
        "data": {
            "time": 1688671955000,
            "sequence": "1234567890",
            "bids": [
                ["49999", "1.5", "3"],
                ["49998", "2.3", "5"]
            ],
            "asks": [
                ["50001", "1.8", "2"],
                ["50002", "3.2", "4"]
            ]
        }
    }

    Each bid/ask array: [price, size] or [price, size, orders_count]
    """

    def init_data(self):
        if not self.has_been_json_encoded:
            self.order_book_info = json.loads(self.order_book_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        # Extract data field from response
        self.order_book_data = self.order_book_info.get("data", {})
        self.order_book_symbol_name = self.symbol_name
        self.server_time = from_dict_get_float(self.order_book_data, "time")

        # Parse bids and asks
        bids = self.order_book_data.get("bids", [])
        asks = self.order_book_data.get("asks", [])

        # Each level is [price, size] or [price, size, orders_count]
        self.bid_price_list = [float(level[0]) for level in bids]
        self.bid_volume_list = [float(level[1]) for level in bids]
        # If third element exists, it's the number of orders
        self.bid_trade_nums = [float(level[2]) if len(level) > 2 else 1.0 for level in bids]

        self.ask_price_list = [float(level[0]) for level in asks]
        self.ask_volume_list = [float(level[1]) for level in asks]
        self.ask_trade_nums = [float(level[2]) if len(level) > 2 else 1.0 for level in asks]

        self.has_been_init_data = True
        return self


class KuCoinWssOrderBookData(KuCoinOrderBookData):
    """KuCoin WebSocket orderbook data.

    WebSocket orderbook message format:
    {
        "topic": "/market/level2:BTC-USDT",
        "type": "message",
        "subject": "level2",
        "data": {
            "sequence": "1234567890",
            "bids": [["49999", "1.5", "3"]],
            "asks": [["50001", "1.8", "2"]]
        }
    }

    For level2 snapshot (initial):
    {
        "topic": "/market/level2:snapshot:BTC-USDT",
        "type": "message",
        "subject": "level2",
        "data": {
            "sequence": "1234567890",
            "bids": [["49999", "1.5"], ["49998", "2.3"]],
            "asks": [["50001", "1.8"], ["50002", "3.2"]]
        }
    }
    """

    def init_data(self):
        if not self.has_been_json_encoded:
            self.order_book_info = json.loads(self.order_book_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        # Extract data from WebSocket message
        data = self.order_book_info.get("data", {})
        self.order_book_data = data
        self.order_book_symbol_name = self.symbol_name
        self.server_time = time.time() * 1000  # WebSocket messages may not have time

        # Parse bids and asks
        bids = data.get("bids", [])
        asks = data.get("asks", [])

        self.bid_price_list = [float(level[0]) for level in bids]
        self.bid_volume_list = [float(level[1]) for level in bids]
        self.bid_trade_nums = [float(level[2]) if len(level) > 2 else 1.0 for level in bids]

        self.ask_price_list = [float(level[0]) for level in asks]
        self.ask_volume_list = [float(level[1]) for level in asks]
        self.ask_trade_nums = [float(level[2]) if len(level) > 2 else 1.0 for level in asks]

        self.has_been_init_data = True
        return self


class KuCoinLevel3OrderBookData(KuCoinOrderBookData):
    """KuCoin Level3 orderbook data (full order book with order IDs).

    This is for the full order book that includes individual order IDs.
    Used for advanced trading strategies.
    """

    def init_data(self):
        if not self.has_been_json_encoded:
            self.order_book_info = json.loads(self.order_book_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        # Extract data from response
        self.order_book_data = self.order_book_info.get("data", {})
        self.order_book_symbol_name = self.symbol_name
        self.server_time = from_dict_get_float(
            self.order_book_data, "sequence"
        )  # Use sequence as time

        # Level3 format: [[order_id, price, size], ...]
        bids = self.order_book_data.get("bids", [])
        asks = self.order_book_data.get("asks", [])

        # For level3, we aggregate by price
        bid_dict = {}
        ask_dict = {}

        for order_id, price, size in bids:
            price_float = float(price)
            size_float = float(size)
            if price_float in bid_dict:
                bid_dict[price_float] += size_float
                bid_dict[price_float + "_orders"] = bid_dict.get(price_float + "_orders", 0) + 1
            else:
                bid_dict[price_float] = size_float
                bid_dict[price_float + "_orders"] = 1

        for order_id, price, size in asks:
            price_float = float(price)
            size_float = float(size)
            if price_float in ask_dict:
                ask_dict[price_float] += size_float
                ask_dict[price_float + "_orders"] = ask_dict.get(price_float + "_orders", 0) + 1
            else:
                ask_dict[price_float] = size_float
                ask_dict[price_float + "_orders"] = 1

        # Extract aggregated data
        self.bid_price_list = sorted([k for k in bid_dict if isinstance(k, float)], reverse=True)
        self.bid_volume_list = [bid_dict[p] for p in self.bid_price_list]
        self.bid_trade_nums = [bid_dict.get(f"{p}_orders", 1) for p in self.bid_price_list]

        self.ask_price_list = sorted([k for k in ask_dict if isinstance(k, float)])
        self.ask_volume_list = [ask_dict[p] for p in self.ask_price_list]
        self.ask_trade_nums = [ask_dict.get(f"{p}_orders", 1) for p in self.ask_price_list]

        self.has_been_init_data = True
        return self
