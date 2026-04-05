from __future__ import annotations

import json
import time

from bt_api_py.containers.orderbooks.orderbook import OrderBookData


class MexcOrderBookData(OrderBookData):
    """保存订单簿信息"""

    def __init__(self, orderbook_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(orderbook_info, has_been_json_encoded)
        self.exchange_name = "MEXC"  # 交易所名称
        self.local_update_time = time.time()  # 本地时间戳
        self.symbol_name = symbol_name
        self.asset_type = asset_type  # 订单簿的类型
        self.orderbook_data = orderbook_info if has_been_json_encoded else None
        self.bids = []  # 买单列表，每个元素是 [price, quantity]
        self.asks = []  # 卖单列表，每个元素是 [price, quantity]
        self.timestamp = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        if not self.has_been_json_encoded:
            if isinstance(self.order_book_info, str):
                self.orderbook_data = json.loads(self.order_book_info)
            else:
                self.orderbook_data = self.order_book_info

        if self.orderbook_data:
            self.timestamp = self.orderbook_data.get("time")

            # 处理买单
            self.bids = []
            for bid in self.orderbook_data.get("bids", []):
                if len(bid) >= 2:
                    price = float(bid[0])
                    quantity = float(bid[1])
                    self.bids.append([price, quantity])

            # 处理卖单
            self.asks = []
            for ask in self.orderbook_data.get("asks", []):
                if len(ask) >= 2:
                    price = float(ask[0])
                    quantity = float(ask[1])
                    self.asks.append([price, quantity])

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
                "timestamp": self.timestamp,
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

    def get_timestamp(self):
        return self.timestamp

    def get_bids(self):
        """获取买单列表"""
        if not self.has_been_init_data:
            self.init_data()
        return self.bids

    def get_asks(self):
        """获取卖单列表"""
        if not self.has_been_init_data:
            self.init_data()
        return self.asks

    def get_best_bid(self):
        """获取最佳买价"""
        if not self.has_been_init_data:
            self.init_data()
        return self.bids[0][0] if self.bids else None

    def get_best_ask(self):
        """获取最佳卖价"""
        if not self.has_been_init_data:
            self.init_data()
        return self.asks[0][0] if self.asks else None

    def get_spread(self):
        """获取买卖价差"""
        best_bid = self.get_best_bid()
        best_ask = self.get_best_ask()
        if best_bid is not None and best_ask is not None:
            return best_ask - best_bid
        return None

    def get_bid_depth(self, level=5):
        """获取指定深度的买单"""
        if not self.has_been_init_data:
            self.init_data()
        return self.bids[:level]

    def get_ask_depth(self, level=5):
        """获取指定深度的卖单"""
        if not self.has_been_init_data:
            self.init_data()
        return self.asks[:level]

    def get_total_bid_qty(self, level=5):
        """获取指定深度的买单总量"""
        depth = self.get_bid_depth(level)
        return sum(qty for _, qty in depth)

    def get_total_ask_qty(self, level=5):
        """获取指定深度的卖单总量"""
        depth = self.get_ask_depth(level)
        return sum(qty for _, qty in depth)

    def get_mid_price(self):
        """获取中间价"""
        best_bid = self.get_best_bid()
        best_ask = self.get_best_ask()
        if best_bid is not None and best_ask is not None:
            return (best_bid + best_ask) / 2
        return None

    def get_vwap(self, side, level=5):
        """计算指定深度的成交量加权平均价格"""
        if side.lower() == "bid":
            depth = self.get_bid_depth(level)
        elif side.lower() == "ask":
            depth = self.get_ask_depth(level)
        else:
            return None

        if not depth:
            return None

        total_qty = sum(qty for _, qty in depth)
        if total_qty == 0:
            return None

        weighted_sum = sum(price * qty for price, qty in depth)
        return weighted_sum / total_qty


class MexcWssOrderBookData(MexcOrderBookData):
    """保存WebSocket订单簿信息"""

    def init_data(self):
        if not self.has_been_json_encoded:
            self.orderbook_data = json.loads(self.order_book_info)
            self.has_been_json_encoded = True

        if self.orderbook_data and "data" in self.orderbook_data:
            # 处理WebSocket订单簿数据
            # 注意：MEXC WebSocket订单簿数据格式可能与REST API不同
            # 这里假设格式类似，需要根据实际情况调整
            pass

        self.has_been_init_data = True
        return self


class MexcRequestOrderBookData(MexcOrderBookData):
    """保存请求返回的订单簿信息"""

    def init_data(self):
        if not self.has_been_json_encoded:
            if isinstance(self.order_book_info, str):
                self.orderbook_data = json.loads(self.order_book_info)
            else:
                self.orderbook_data = self.order_book_info

        if self.orderbook_data:
            self.timestamp = self.orderbook_data.get("time")

            # 处理买单
            self.bids = []
            for bid in self.orderbook_data.get("bids", []):
                if len(bid) >= 2:
                    price = float(bid[0])
                    quantity = float(bid[1])
                    self.bids.append([price, quantity])

            # 处理卖单
            self.asks = []
            for ask in self.orderbook_data.get("asks", []):
                if len(ask) >= 2:
                    price = float(ask[0])
                    quantity = float(ask[1])
                    self.asks.append([price, quantity])

        self.has_been_init_data = True
        return self
