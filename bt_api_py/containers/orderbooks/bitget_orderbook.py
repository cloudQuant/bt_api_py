"""
Bitget OrderBook Data Container
"""

import json
import time

from bt_api_py.containers.orderbooks.orderbook import OrderBookData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_int, from_dict_get_string


class BitgetOrderBookData(OrderBookData):
    """保存Bitget订单簿信息"""

    def __init__(self, orderbook_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(orderbook_info, has_been_json_encoded)
        self.exchange_name = "BITGET"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.orderbook_data = orderbook_info if has_been_json_encoded else None
        self.symbol = None
        self.time = None
        self.bids = []  # List of (price, size) tuples
        self.asks = []  # List of (price, size) tuples
        self.level = None
        self.has_been_init_data = False

    def init_data(self):
        if not self.has_been_json_encoded:
            self.orderbook_data = json.loads(self.order_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        self.symbol = from_dict_get_string(self.orderbook_data, "symbol")
        self.time = from_dict_get_float(self.orderbook_data, "ts") or from_dict_get_float(
            self.orderbook_data, "time"
        )
        self.level = from_dict_get_int(self.orderbook_data, "level")

        # Process bids
        self.bids = []
        if "bids" in self.orderbook_data:
            for bid in self.orderbook_data["bids"]:
                if isinstance(bid, list) and len(bid) >= 2:
                    self.bids.append((float(bid[0]), float(bid[1])))

        # Process asks
        self.asks = []
        if "asks" in self.orderbook_data:
            for ask in self.orderbook_data["asks"]:
                if isinstance(ask, list) and len(ask) >= 2:
                    self.asks.append((float(ask[0]), float(ask[1])))

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
                "symbol": self.symbol,
                "time": self.time,
                "level": self.level,
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

    def get_symbol(self):
        return self.symbol

    def get_time(self):
        return self.time

    def get_level(self):
        return self.level

    def get_bids(self):
        """Get bid orders (sorted by price descending)

        Returns:
            list: List of (price, size) tuples
        """
        self.init_data()
        return sorted(self.bids, key=lambda x: x[0], reverse=True)

    def get_asks(self):
        """Get ask orders (sorted by price ascending)

        Returns:
            list: List of (price, size) tuples
        """
        self.init_data()
        return sorted(self.asks, key=lambda x: x[0])

    def get_spread(self):
        """Get bid-ask spread

        Returns:
            float: Spread (ask price - bid price) or None if no data
        """
        self.init_data()
        if not self.bids or not self.asks:
            return None
        best_bid = max(bid[0] for bid in self.bids)
        best_ask = min(ask[0] for ask in self.asks)
        return best_ask - best_bid

    def get_best_bid(self):
        """Get best bid price

        Returns:
            float: Best bid price or None
        """
        self.init_data()
        if not self.bids:
            return None
        return max(bid[0] for bid in self.bids)

    def get_best_ask(self):
        """Get best ask price

        Returns:
            float: Best ask price or None
        """
        self.init_data()
        if not self.asks:
            return None
        return min(ask[0] for ask in self.asks)

    def get_total_bid_volume(self):
        """Get total bid volume

        Returns:
            float: Total bid volume
        """
        self.init_data()
        return sum(bid[1] for bid in self.bids)

    def get_total_ask_volume(self):
        """Get total ask volume

        Returns:
            float: Total ask volume
        """
        self.init_data()
        return sum(ask[1] for ask in self.asks)

    def get_mid_price(self):
        """Get mid price (average of best bid and ask)

        Returns:
            float: Mid price or None if no data
        """
        self.init_data()
        best_bid = self.get_best_bid()
        best_ask = self.get_best_ask()
        if best_bid is None or best_ask is None:
            return None
        return (best_bid + best_ask) / 2


class BitgetWssOrderBookData(BitgetOrderBookData):
    """Bitget WebSocket OrderBook Data"""

    def init_data(self):
        if not self.has_been_json_encoded:
            self.orderbook_data = json.loads(self.order_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        self.symbol = from_dict_get_string(self.orderbook_data, "s")
        self.time = from_dict_get_float(self.orderbook_data, "E")
        self.level = 1  # Bitget WS typically provides level 1 data

        # Process bids
        self.bids = []
        if "b" in self.orderbook_data:
            for bid in self.orderbook_data["b"]:
                if isinstance(bid, list) and len(bid) >= 2:
                    self.bids.append((float(bid[0]), float(bid[1])))

        # Process asks
        self.asks = []
        if "a" in self.orderbook_data:
            for ask in self.orderbook_data["a"]:
                if isinstance(ask, list) and len(ask) >= 2:
                    self.asks.append((float(ask[0]), float(ask[1])))

        self.has_been_init_data = True
        return self


class BitgetRequestOrderBookData(BitgetOrderBookData):
    """Bitget REST API OrderBook Data"""

    def init_data(self):
        if not self.has_been_json_encoded:
            self.orderbook_data = json.loads(self.order_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        self.symbol = from_dict_get_string(self.orderbook_data, "symbol")
        self.time = from_dict_get_float(self.orderbook_data, "ts")
        self.level = from_dict_get_int(self.orderbook_data, "level")

        # Process bids
        self.bids = []
        if "bids" in self.orderbook_data:
            for bid in self.orderbook_data["bids"]:
                if isinstance(bid, list) and len(bid) >= 2:
                    self.bids.append((float(bid[0]), float(bid[1])))

        # Process asks
        self.asks = []
        if "asks" in self.orderbook_data:
            for ask in self.orderbook_data["asks"]:
                if isinstance(ask, list) and len(ask) >= 2:
                    self.asks.append((float(ask[0]), float(ask[1])))

        self.has_been_init_data = True
        return self
