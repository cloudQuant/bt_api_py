import time

from bt_api_py.containers.orderbooks.orderbook import OrderBookData


class BybitOrderBookData(OrderBookData):
    """保存 Bybit 订单簿信息"""

    def __init__(self, orderbook_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(orderbook_info, has_been_json_encoded)
        self.exchange_name = "BYBIT"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.orderbook_data = orderbook_info if has_been_json_encoded else None
        self.bids = []
        self.asks = []
        self.timestamp = None
        self.update_id = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        """初始化订单簿数据"""
        if self.has_been_init_data or self.orderbook_data is None:
            return self

        try:
            result = self.orderbook_data.get("result", {})

            # 获取基本信息
            self.timestamp = result.get("ts")
            self.update_id = result.get("u")

            # 获取买单和卖单
            bids_data = result.get("b", [])
            asks_data = result.get("a", [])

            # 解析买单 (价格, 数量)
            self.bids = []
            for bid in bids_data:
                if len(bid) >= 2:
                    price = float(bid[0])
                    quantity = float(bid[1])
                    self.bids.append([price, quantity])

            # 解析卖单 (价格, 数量)
            self.asks = []
            for ask in asks_data:
                if len(ask) >= 2:
                    price = float(ask[0])
                    quantity = float(ask[1])
                    self.asks.append([price, quantity])

            # 按价格排序
            self.bids.sort(key=lambda x: x[0], reverse=True)  # 从高到低
            self.asks.sort(key=lambda x: x[0])  # 从低到高

            self.has_been_init_data = True

        except Exception as e:
            print(f"Error parsing Bybit orderbook data: {e}")
            self.has_been_init_data = False
        return self

    def get_all_data(self):
        """获取所有订单簿数据"""
        if self.all_data is None:
            self.init_data()
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "timestamp": self.timestamp,
                "update_id": self.update_id,
                "bids": self.bids,
                "asks": self.asks,
            }
        return self.all_data

    def get_best_bid(self):
        """获取最佳买价"""
        if self.bids:
            return self.bids[0][0]
        return None

    def get_best_ask(self):
        """获取最佳卖价"""
        if self.asks:
            return self.asks[0][0]
        return None

    def get_spread(self):
        """获取买卖价差"""
        best_bid = self.get_best_bid()
        best_ask = self.get_best_ask()
        if best_bid is not None and best_ask is not None:
            return best_ask - best_bid
        return None

    def get_bid_depth(self, depth=5):
        """获取指定深度的买单"""
        return self.bids[:depth]

    def get_ask_depth(self, depth=5):
        """获取指定深度的卖单"""
        return self.asks[:depth]

    def get_total_bid_qty(self):
        """获取买单总数量"""
        return sum(bid[1] for bid in self.bids)

    def get_total_ask_qty(self):
        """获取卖单总数量"""
        return sum(ask[1] for ask in self.asks)

    def is_valid(self):
        """检查订单簿是否有效"""
        return len(self.bids) > 0 and len(self.asks) > 0

    def __str__(self):
        """返回订单簿的字符串表示"""
        self.init_data()
        best_bid = self.get_best_bid()
        best_ask = self.get_best_ask()
        return (f"BybitOrderBook(symbol={self.symbol_name}, "
                f"bid={best_bid}, "
                f"ask={best_ask}, "
                f"spread={self.get_spread()})")


class BybitSpotOrderBookData(BybitOrderBookData):
    """Bybit 现货订单簿数据"""

    def __init__(self, orderbook_info, symbol_name, has_been_json_encoded=False):
        super().__init__(orderbook_info, symbol_name, "spot", has_been_json_encoded)


class BybitSwapOrderBookData(BybitOrderBookData):
    """Bybit 期货/swap 订单簿数据"""

    def __init__(self, orderbook_info, symbol_name, has_been_json_encoded=False):
        super().__init__(orderbook_info, symbol_name, "swap", has_been_json_encoded)
