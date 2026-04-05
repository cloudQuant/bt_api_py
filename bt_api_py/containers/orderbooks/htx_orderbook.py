"""HTX OrderBook Data Container"""

from __future__ import annotations

import json
import time

from bt_api_py.containers.orderbooks.orderbook import OrderBookData
from bt_api_py.functions.utils import from_dict_get_float


class HtxRequestOrderBookData(OrderBookData):
    """HTX REST API orderbook data."""

    def __init__(self, order_book_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(order_book_info, has_been_json_encoded)
        self.exchange_name = "HTX"
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
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        """Initialize orderbook data from HTX response.

        HTX depth response format:
        {
            "status": "ok",
            "ch": "market.btcusdt.depth.step0",
            "ts": 1688671955000,
            "tick": {
                "bids": [
                    [49999, 1.5],
                    [49998, 2.3]
                ],
                "asks": [
                    [50001, 1.8],
                    [50002, 3.2]
                ],
                "ts": 1688671955000,
                "version": 123456789
            }
        }
        """
        if self.has_been_init_data:
            return self

        if not self.has_been_json_encoded:
            self.order_book_data = json.loads(self.order_book_info)

        # Extract tick data
        data = self.order_book_data or {}
        tick = data.get("tick", {})

        self.server_time = from_dict_get_float(data, "ts")
        self.order_book_symbol_name = self.symbol_name

        # Parse bids
        bids = tick.get("bids", [])
        if isinstance(bids, list):
            self.bid_price_list = [from_dict_get_float({0: b[0]}, "0") for b in bids]
            self.bid_volume_list = [from_dict_get_float({0: b[1]}, "0") for b in bids]

        # Parse asks
        asks = tick.get("asks", [])
        if isinstance(asks, list):
            self.ask_price_list = [from_dict_get_float({0: a[0]}, "0") for a in asks]
            self.ask_volume_list = [from_dict_get_float({0: a[1]}, "0") for a in asks]

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

    def get_order_book_symbol_name(self):
        self.init_data()
        return self.order_book_symbol_name

    def get_asset_type(self):
        return self.asset_type

    def get_server_time(self):
        self.init_data()
        return self.server_time

    def get_bid_price_list(self):
        self.init_data()
        return self.bid_price_list

    def get_ask_price_list(self):
        self.init_data()
        return self.ask_price_list

    def get_bid_volume_list(self):
        self.init_data()
        return self.bid_volume_list

    def get_ask_volume_list(self):
        self.init_data()
        return self.ask_volume_list
