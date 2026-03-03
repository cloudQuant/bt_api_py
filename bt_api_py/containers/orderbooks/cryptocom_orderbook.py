"""
Crypto.com OrderBook Data Container
"""

import json
import time

from bt_api_py.containers.orderbooks.orderbook import OrderBookData


class CryptoComOrderBook(OrderBookData):
    """Crypto.com orderbook implementation."""

    def __init__(self, order_book_info, symbol_name, asset_type="SPOT", has_been_json_encoded=False):
        super().__init__(order_book_info, has_been_json_encoded)
        self.exchange_name = "CRYPTOCOM"
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
        self.bids = []
        self.asks = []
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        """Initialize orderbook data from raw response."""
        if not self.has_been_json_encoded:
            self.order_book_data = json.loads(self.order_book_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        data = self.order_book_data
        self.order_book_symbol_name = self.symbol_name
        self.server_time = float(data.get("t", 0)) / 1000 if data.get("t") else None

        if "data" in data and data["data"]:
            book_data = data["data"][0]
            self.asks = []
            for ask in book_data.get("asks", []):
                self.asks.append([float(ask[0]), float(ask[1]), int(ask[2])])

            self.bids = []
            for bid in book_data.get("bids", []):
                self.bids.append([float(bid[0]), float(bid[1]), int(bid[2])])

            self.bid_price_list = [b[0] for b in self.bids]
            self.ask_price_list = [a[0] for a in self.asks]
            self.bid_volume_list = [b[1] for b in self.bids]
            self.ask_volume_list = [a[1] for a in self.asks]

        self.has_been_init_data = True
        return self

    def get_all_data(self):
        """Get all orderbook data as dictionary."""
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "order_book_symbol_name": self.order_book_symbol_name,
                "server_time": self.server_time,
                "bid_price_list": self.bid_price_list,
                "ask_price_list": self.ask_price_list,
                "bid_volume_list": self.bid_volume_list,
                "ask_volume_list": self.ask_volume_list,
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

    def get_order_book_symbol_name(self):
        return self.order_book_symbol_name

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
        return [b[2] if len(b) > 2 else 0 for b in self.bids]

    def get_ask_trade_nums(self):
        return [a[2] if len(a) > 2 else 0 for a in self.asks]

    def get_price_levels(self, side, levels=10):
        """Get top N price levels for given side."""
        if side.upper() == "ASK":
            return self.asks[:levels]
        elif side.upper() == "BID":
            return self.bids[:levels]
        return []

    @classmethod
    def from_api_response(cls, data, symbol):
        """Create orderbook from API response.

        This is a convenience method for testing.
        For production use, use the standard constructor with json-encoded data.
        """
        import json
        return cls(
            order_book_info=json.dumps(data),
            symbol_name=symbol,
            asset_type="SPOT",
            has_been_json_encoded=False,
        )
