from __future__ import annotations

import time

from bt_api_py.containers.orderbooks.orderbook import OrderBookData
from bt_api_py.functions.utils import from_dict_get_float


class HitBtcRequestOrderBookData(OrderBookData):
    """保存HitBTC订单簿信息"""

    def __init__(self, orderbook_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(orderbook_info, has_been_json_encoded)
        self.exchange_name = "HITBTC"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        # Always store orderbook_info for init_data() to parse
        self.orderbook_data = orderbook_info
        self.sequence = None
        self.bids = None
        self.asks = None
        self.timestamp = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        if self.has_been_init_data:
            return

        if self.orderbook_data is None:
            return

        # 提取数据
        self.sequence = from_dict_get_float(self.orderbook_data, "sequence")
        self.timestamp = from_dict_get_float(self.orderbook_data, "timestamp")
        # bid and ask are arrays, not floats - get them directly
        bid_data = self.orderbook_data.get("bid")
        ask_data = self.orderbook_data.get("ask")
        self.bids = self._parse_levels(bid_data) if bid_data else []
        self.asks = self._parse_levels(ask_data) if ask_data else []

        self.has_been_init_data = True

    def _parse_levels(self, levels):
        """解析订单簿层级"""
        if levels is None:
            return []

        parsed_levels = []
        for level in levels:
            if isinstance(level, list):
                # [price, quantity]
                price = float(level[0]) if level[0] is not None else None
                quantity = float(level[1]) if level[1] is not None else None
                parsed_levels.append({"price": price, "quantity": quantity})
            elif isinstance(level, dict):
                # {price, quantity}
                parsed_levels.append(
                    {
                        "price": from_dict_get_float(level, "price"),
                        "quantity": from_dict_get_float(level, "quantity"),
                    }
                )
        return parsed_levels

    def get_all_data(self):
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "sequence": self.sequence,
                "timestamp": self.timestamp,
                "bids": self.bids,
                "asks": self.asks,
            }
        return self.all_data

    def get_best_bid(self):
        """获取最佳买价"""
        if self.bids and len(self.bids) > 0:
            return self.bids[0]["price"]
        return None

    def get_best_ask(self):
        """获取最佳卖价"""
        if self.asks and len(self.asks) > 0:
            return self.asks[0]["price"]
        return None

    def get_spread(self):
        """获取买卖价差"""
        best_bid = self.get_best_bid()
        best_ask = self.get_best_ask()
        if best_bid and best_ask:
            return best_ask - best_bid
        return None

    def get_symbol_name(self):
        """Get symbol name."""
        return self.symbol_name

    def get_exchange_name(self):
        """Get exchange name."""
        return self.exchange_name

    def get_asset_type(self):
        """Get asset type."""
        return self.asset_type

    def get_local_update_time(self):
        """Get local update time."""
        return self.local_update_time

    def get_server_time(self):
        """Get server time."""
        return self.timestamp

    def get_bid_price_list(self):
        """Get bid price list."""
        bids = self.bids or []
        return [level["price"] for level in bids if level.get("price") is not None]

    def get_ask_price_list(self):
        """Get ask price list."""
        asks = self.asks or []
        return [level["price"] for level in asks if level.get("price") is not None]

    def get_bid_volume_list(self):
        """Get bid volume list."""
        bids = self.bids or []
        return [level["quantity"] for level in bids if level.get("quantity") is not None]

    def get_ask_volume_list(self):
        """Get ask volume list."""
        asks = self.asks or []
        return [level["quantity"] for level in asks if level.get("quantity") is not None]

    def get_bid_trade_nums(self):
        """Get bid trade numbers (not supported by HitBTC)."""
        return []

    def get_ask_trade_nums(self):
        """Get ask trade numbers (not supported by HitBTC)."""
        return []

    def __str__(self):
        best_bid = self.get_best_bid()
        best_ask = self.get_best_ask()
        return f"HITBTC OrderBook {self.symbol_name}: Bid={best_bid}, Ask={best_ask}"

    def __repr__(self) -> str:
        bids_len = len(self.bids) if self.bids else 0
        asks_len = len(self.asks) if self.asks else 0
        return f"<HitBtcOrderBookData {self.symbol_name} bids={bids_len} asks={asks_len}>"
