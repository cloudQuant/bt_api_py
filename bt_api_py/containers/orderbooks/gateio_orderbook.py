"""
Gate.io Order Book Data Container
"""

import json
import time

from bt_api_py.containers.orderbooks.orderbook import OrderBookData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_int, from_dict_get_string


class GateioOrderBookData(OrderBookData):
    """Gate.io order book data container"""

    def __init__(self, orderbook_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(orderbook_info, has_been_json_encoded)
        self.exchange_name = "GATEIO"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.orderbook_data = orderbook_info if has_been_json_encoded else None
        self.sequence_id = None
        self.current_time = None
        self.update_time = None
        self.bids = None
        self.asks = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        """Initialize and parse order book data"""
        if not self.has_been_json_encoded:
            self.orderbook_data = json.loads(self.order_book_info)
            self.has_been_json_encoded = True

        if self.has_been_init_data:
            return self

        # Parse order book data
        self.sequence_id = from_dict_get_int(self.orderbook_data, "id")
        self.current_time = from_dict_get_float(self.orderbook_data, "current")
        self.update_time = from_dict_get_float(self.orderbook_data, "update")

        # Parse bids and asks
        self.bids = []
        if "bids" in self.orderbook_data and isinstance(self.orderbook_data["bids"], list):
            for bid in self.orderbook_data["bids"]:
                if isinstance(bid, list) and len(bid) >= 2:
                    self.bids.append({
                        "price": from_dict_get_float(bid, 0),
                        "amount": from_dict_get_float(bid, 1),
                    })

        self.asks = []
        if "asks" in self.orderbook_data and isinstance(self.orderbook_data["asks"], list):
            for ask in self.orderbook_data["asks"]:
                if isinstance(ask, list) and len(ask) >= 2:
                    self.asks.append({
                        "price": from_dict_get_float(ask, 0),
                        "amount": from_dict_get_float(ask, 1),
                    })

        self.has_been_init_data = True
        return self

    def get_all_data(self):
        """Get all order book data as dictionary"""
        if self.all_data is None:
            self.init_data()
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "sequence_id": self.sequence_id,
                "current_time": self.current_time,
                "update_time": self.update_time,
                "bids": self.bids,
                "asks": self.asks,
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

    def get_sequence_id(self):
        return self.sequence_id

    def get_current_time(self):
        return self.current_time

    def get_update_time(self):
        return self.update_time

    def get_bids(self):
        return self.bids

    def get_asks(self):
        return self.asks

    def get_best_bid(self):
        """Get the best bid price"""
        if self.bids and len(self.bids) > 0:
            return self.bids[0]["price"]
        return None

    def get_best_ask(self):
        """Get the best ask price"""
        if self.asks and len(self.asks) > 0:
            return self.asks[0]["price"]
        return None

    def get_spread(self):
        """Get bid-ask spread"""
        best_bid = self.get_best_bid()
        best_ask = self.get_best_ask()
        if best_bid and best_ask:
            return best_ask - best_bid
        return None

    def get_bid_depth(self, price_levels=5):
        """Get bid depth for specified levels"""
        if self.bids:
            return self.bids[:price_levels]
        return []

    def get_ask_depth(self, price_levels=5):
        """Get ask depth for specified levels"""
        if self.asks:
            return self.asks[:price_levels]
        return []


class GateioRequestOrderBookData(GateioOrderBookData):
    """Gate.io REST API order book data"""

    def init_data(self):
        """Initialize and parse REST API order book data"""
        if not self.has_been_json_encoded:
            self.orderbook_data = json.loads(self.order_book_info)
            self.has_been_json_encoded = True

        if self.has_been_init_data:
            return self

        # Parse REST API order book data
        self.sequence_id = from_dict_get_int(self.orderbook_data, "id")
        self.current_time = from_dict_get_float(self.orderbook_data, "current")
        self.update_time = from_dict_get_float(self.orderbook_data, "update")

        # Parse bids and asks
        self.bids = []
        if "bids" in self.orderbook_data and isinstance(self.orderbook_data["bids"], list):
            for bid in self.orderbook_data["bids"]:
                if isinstance(bid, list) and len(bid) >= 2:
                    self.bids.append({
                        "price": from_dict_get_float(bid, 0),
                        "amount": from_dict_get_float(bid, 1),
                    })

        self.asks = []
        if "asks" in self.orderbook_data and isinstance(self.orderbook_data["asks"], list):
            for ask in self.orderbook_data["asks"]:
                if isinstance(ask, list) and len(ask) >= 2:
                    self.asks.append({
                        "price": from_dict_get_float(ask, 0),
                        "amount": from_dict_get_float(ask, 1),
                    })

        self.has_been_init_data = True
        return self


class GateioWssOrderBookData(GateioOrderBookData):
    """Gate.io WebSocket order book data (placeholder for future implementation)"""

    def init_data(self):
        """Initialize and parse WebSocket order book data"""
        # TODO: Implement WebSocket order book data parsing
        return self