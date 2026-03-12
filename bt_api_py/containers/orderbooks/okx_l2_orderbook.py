"""
OKX L2 OrderBook Data Container - 400 depth tick-by-tick orderbook.
"""

import json
import time

from bt_api_py.containers.orderbooks.orderbook import OrderBookData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string


class OkxL2OrderBookData(OrderBookData):
    """OKX 400深度逐笔推送订单簿 (books-l2-tbt channel).

    The data structure for books-l2-tbt is different from regular books:
    - bids/asks are arrays of [price, size, orders, liquidation]
    - Action field indicates 'snapshot', 'update' or partial update
    """

    def __init__(self, order_book_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(order_book_info, has_been_json_encoded)
        self.exchange_name = "OKX"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.order_book_data = order_book_info if has_been_json_encoded else None
        self.order_book_symbol_name = None
        self.server_time = None
        self.action = None
        self.bid_price_list = None
        self.ask_price_list = None
        self.bid_volume_list = None
        self.ask_volume_list = None
        self.bid_orders_list = None  # Number of orders at each price level
        self.ask_orders_list = None
        self.bid_liquidation_list = None  # Liquidation orders at each level
        self.ask_liquidation_list = None
        self.checksum = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        if not self.has_been_json_encoded:
            self.order_book_info = json.loads(self.order_book_info)
            self.order_book_data = self.order_book_info["data"][0]
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        if "arg" in self.order_book_info:
            self.order_book_symbol_name = from_dict_get_string(
                self.order_book_info["arg"], "instId"
            )

        data = self.order_book_data or {}
        self.action = from_dict_get_string(data, "action")
        self.server_time = from_dict_get_float(data, "ts")
        self.checksum = from_dict_get_string(data, "checksum")

        # books-l2-tbt has bids/asks in format: [price, size, orders, liquidation]
        bids = data.get("bids", [])
        asks = data.get("asks", [])

        self.bid_price_list = [float(i[0]) if len(i) > 0 else None for i in bids]
        self.bid_volume_list = [float(i[1]) if len(i) > 1 else None for i in bids]
        self.bid_orders_list = [int(i[2]) if len(i) > 2 else None for i in bids]
        self.bid_liquidation_list = [float(i[3]) if len(i) > 3 else None for i in bids]

        self.ask_price_list = [float(i[0]) if len(i) > 0 else None for i in asks]
        self.ask_volume_list = [float(i[1]) if len(i) > 1 else None for i in asks]
        self.ask_orders_list = [int(i[2]) if len(i) > 2 else None for i in asks]
        self.ask_liquidation_list = [float(i[3]) if len(i) > 3 else None for i in asks]

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
                "action": self.action,
                "checksum": self.checksum,
                "bid_price_list": self.bid_price_list,
                "ask_price_list": self.ask_price_list,
                "bid_volume_list": self.bid_volume_list,
                "ask_volume_list": self.ask_volume_list,
                "bid_orders_list": self.bid_orders_list,
                "ask_orders_list": self.ask_orders_list,
                "bid_liquidation_list": self.bid_liquidation_list,
                "ask_liquidation_list": self.ask_liquidation_list,
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

    def get_action(self):
        """Get the action type: 'snapshot', 'update', or partial update indicator."""
        return self.action

    def get_checksum(self):
        """Get the checksum for data integrity validation."""
        return self.checksum

    def get_bid_price_list(self):
        return self.bid_price_list

    def get_ask_price_list(self):
        return self.ask_price_list

    def get_bid_volume_list(self):
        return self.bid_volume_list

    def get_ask_volume_list(self):
        return self.ask_volume_list

    def get_bid_orders_list(self):
        """Get number of orders at each bid price level."""
        return self.bid_orders_list

    def get_ask_orders_list(self):
        """Get number of orders at each ask price level."""
        return self.ask_orders_list

    def get_bid_liquidation_list(self):
        """Get liquidation orders at each bid level."""
        return self.bid_liquidation_list

    def get_ask_liquidation_list(self):
        """Get liquidation orders at each ask level."""
        return self.ask_liquidation_list

    def get_bid_trade_nums(self):
        # For compatibility with existing code
        return self.bid_orders_list

    def get_ask_trade_nums(self):
        # For compatibility with existing code
        return self.ask_orders_list
