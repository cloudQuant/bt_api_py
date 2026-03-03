import json
import time

from bt_api_py.containers.orderbooks.orderbook import OrderBookData
from bt_api_py.functions.utils import from_dict_get_float


class BitfinexOrderBookData(OrderBookData):
    """保存 Bitfinex 订单簿信息"""

    def __init__(self, orderbook_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(orderbook_info, has_been_json_encoded)
        self.exchange_name = "BITFINEX"  # 交易所名称
        self.local_update_time = time.time()  # 本地时间戳
        self.symbol_name = symbol_name
        self.asset_type = asset_type  # 订单簿的类型
        self.orderbook_info = orderbook_info  # Raw orderbook data
        self.orderbook_data = orderbook_info if has_been_json_encoded else None
        self.bids = []  # 买单列表
        self.asks = []  # 卖单列表
        self.timestamp = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        if not self.has_been_json_encoded:
            self.orderbook_data = json.loads(self.orderbook_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        # Bitfinex 订单簿格式:
        # 聚合订单簿 (P0-P3): [[PRICE, COUNT, AMOUNT], ...]
        # 原始订单簿 (R0): [[ORDER_ID, PRICE, AMOUNT], ...]
        if isinstance(self.orderbook_data, list):
            self.bids = []
            self.asks = []

            for level in self.orderbook_data:
                if len(level) >= 3:  # 确保有足够的字段
                    price = float(level[0])
                    count = int(level[1])
                    amount = float(level[2])

                    # 判断是买单还是卖单
                    if amount > 0:
                        # 买单
                        self.bids.append({
                            "price": price,
                            "count": count,
                            "amount": amount,
                            "total": price * amount  # 总价值
                        })
                    else:
                        # 卖单 (Bitfinex 使用负数表示卖单)
                        self.asks.append({
                            "price": price,
                            "count": count,
                            "amount": abs(amount),
                            "total": price * abs(amount)  # 总价值
                        })

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
                "bids": self.bids,
                "asks": self.asks,
                "timestamp": self.timestamp,
                "bid_count": len(self.bids),
                "ask_count": len(self.asks),
                "bid_volume": sum(bid["amount"] for bid in self.bids),
                "ask_volume": sum(ask["amount"] for ask in self.asks),
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

    def get_timestamp(self):
        return self.timestamp

    def get_bid_price(self, level=0):
        """获取指定档的买单价格"""
        if not self.has_been_init_data:
            self.init_data()
        if level < len(self.bids):
            return self.bids[level]["price"]
        return None

    def get_ask_price(self, level=0):
        """获取指定档的卖单价格"""
        if not self.has_been_init_data:
            self.init_data()
        if level < len(self.asks):
            return self.asks[level]["price"]
        return None

    def get_bid_volume(self, level=0):
        """获取指定档的买单数量"""
        if not self.has_been_init_data:
            self.init_data()
        if level < len(self.bids):
            return self.bids[level]["amount"]
        return None

    def get_ask_volume(self, level=0):
        """获取指定档的卖单数量"""
        if not self.has_been_init_data:
            self.init_data()
        if level < len(self.asks):
            return self.asks[level]["amount"]
        return None

    def get_bid_count(self, level=0):
        """获取指定档的买单订单数量"""
        if not self.has_been_init_data:
            self.init_data()
        if level < len(self.bids):
            return self.bids[level]["count"]
        return None

    def get_ask_count(self, level=0):
        """获取指定档的卖单订单数量"""
        if not self.has_been_init_data:
            self.init_data()
        if level < len(self.asks):
            return self.asks[level]["count"]
        return None

    def get_spread(self):
        """获取买卖价差"""
        if not self.has_been_init_data:
            self.init_data()
        if self.bids and self.asks:
            return self.asks[0]["price"] - self.bids[0]["price"]
        return None

    def get_mid_price(self):
        """获取中间价格"""
        if not self.has_been_init_data:
            self.init_data()
        if self.bids and self.asks:
            return (self.bids[0]["price"] + self.asks[0]["price"]) / 2
        return None

    def get_total_bid_volume(self):
        """获取买单总数量"""
        if not self.has_been_init_data:
            self.init_data()
        return sum(bid["amount"] for bid in self.bids)

    def get_total_ask_volume(self):
        """获取卖单总数量"""
        if not self.has_been_init_data:
            self.init_data()
        return sum(ask["amount"] for ask in self.asks)

    def get_total_bid_value(self):
        """获取买单总价值"""
        if not self.has_been_init_data:
            self.init_data()
        return sum(bid["total"] for bid in self.bids)

    def get_total_ask_value(self):
        """获取卖单总价值"""
        if not self.has_been_init_data:
            self.init_data()
        return sum(ask["total"] for ask in self.asks)

    def get_bid_levels(self, levels=5):
        """获取指定档数的买单数据"""
        if not self.has_been_init_data:
            self.init_data()
        return self.bids[:levels]

    def get_ask_levels(self, levels=5):
        """获取指定档数的卖单数据"""
        if not self.has_been_init_data:
            self.init_data()
        return self.asks[:levels]

    def get_price_impact(self, volume_ratio=0.1):
        """计算价格影响（估算）

        Args:
            volume_ratio: 要计算的成交量比例（相对于总深度）

        Returns:
            dict: 包含买入影响和卖出影响
        """
        if not self.has_been_init_data:
            self.init_data()

        # 买入影响：在买一价上买入指定比例的深度
        buy_impact = None
        if self.bids:
            total_volume = self.get_total_bid_volume()
            target_volume = total_volume * volume_ratio

            cumulative_volume = 0
            weighted_price = 0

            for bid in self.bids:
                cumulative_volume += bid["amount"]
                weighted_price += bid["price"] * bid["amount"]

                if cumulative_volume >= target_volume:
                    buy_impact = weighted_price / cumulative_volume
                    break

        # 卖出影响：在卖一价上卖出指定比例的深度
        sell_impact = None
        if self.asks:
            total_volume = self.get_total_ask_volume()
            target_volume = total_volume * volume_ratio

            cumulative_volume = 0
            weighted_price = 0

            for ask in self.asks:
                cumulative_volume += ask["amount"]
                weighted_price += ask["price"] * ask["amount"]

                if cumulative_volume >= target_volume:
                    sell_impact = weighted_price / cumulative_volume
                    break

        return {
            "buy_impact": buy_impact,
            "sell_impact": sell_impact,
            "spread": self.get_spread()
        }


class BitfinexWssOrderBookData(BitfinexOrderBookData):
    """保存 Bitfinex WebSocket 订单簿信息"""
    pass  # WebSocket 订单簿格式与 REST API 相同


class BitfinexRequestOrderBookData(BitfinexOrderBookData):
    """保存 Bitfinex REST API 订单簿信息"""
    pass  # REST API 订单簿格式直接处理