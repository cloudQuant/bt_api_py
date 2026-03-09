import json
import time
from typing import Any

from bt_api_py.containers.orderbooks.orderbook import OrderBookData


class BitfinexOrderBookData(OrderBookData):
    """Bitfinex订单簿数据容器.

    保存和管理Bitfinex交易所的订单簿信息，包括买单、卖单列表，
    以及各种订单簿相关的计算功能。
    """

    def __init__(
        self,
        orderbook_info: str | dict,
        symbol_name: str,
        asset_type: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """初始化Bitfinex订单簿数据.

        Args:
            orderbook_info: 订单簿原始数据（JSON字符串或字典）
            symbol_name: 交易对名称（如 "BTCUSD"）
            asset_type: 资产类型（如 "SPOT", "MARGIN"）
            has_been_json_encoded: 数据是否已经过JSON编码
        """
        super().__init__(orderbook_info, has_been_json_encoded)
        self.exchange_name = "BITFINEX"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.orderbook_info = orderbook_info
        self.orderbook_data: Any = orderbook_info if has_been_json_encoded else None
        self.bids: list[dict[str, Any]] = []
        self.asks: list[dict[str, Any]] = []
        self.timestamp: float | None = None
        self.all_data: dict[str, Any] | None = None
        self.has_been_init_data = False

    def init_data(self) -> "BitfinexOrderBookData":
        """初始化并解析订单簿数据.

        将原始订单簿数据解析为结构化的买单和卖单列表。

        Returns:
            BitfinexOrderBookData: 返回self以支持链式调用
        """
        if not self.has_been_json_encoded:
            self.orderbook_data = json.loads(self.orderbook_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        if isinstance(self.orderbook_data, list):
            self.bids = []
            self.asks = []

            for level in self.orderbook_data:
                if len(level) >= 3:
                    price = float(level[0])
                    count = int(level[1])
                    amount = float(level[2])

                    if amount > 0:
                        self.bids.append(
                            {
                                "price": price,
                                "count": count,
                                "amount": amount,
                                "total": price * amount,
                            }
                        )
                    else:
                        self.asks.append(
                            {
                                "price": price,
                                "count": count,
                                "amount": abs(amount),
                                "total": price * abs(amount),
                            }
                        )

        self.has_been_init_data = True
        return self

    def get_all_data(self) -> dict[str, Any]:
        """获取所有订单簿数据.

        Returns:
            dict: 包含交易所名称、交易对、买卖单列表等完整信息
        """
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

    def __str__(self) -> str:
        """返回订单簿的JSON字符串表示."""
        self.init_data()
        return json.dumps(self.get_all_data())

    def __repr__(self) -> str:
        """返回订单簿的字符串表示."""
        return self.__str__()

    def get_exchange_name(self) -> str:
        """获取交易所名称.

        Returns:
            str: 交易所名称（"BITFINEX"）
        """
        return self.exchange_name

    def get_local_update_time(self) -> float:
        """获取本地更新时间戳.

        Returns:
            float: 本地时间戳（Unix时间戳）
        """
        return self.local_update_time

    def get_symbol_name(self) -> str:
        """获取交易对名称.

        Returns:
            str: 交易对名称
        """
        return self.symbol_name

    def get_asset_type(self) -> str:
        """获取资产类型.

        Returns:
            str: 资产类型（如 "SPOT", "MARGIN"）
        """
        return self.asset_type

    def get_bids(self) -> list[dict[str, Any]]:
        """获取买单列表.

        Returns:
            list: 买单列表，每个元素包含price、count、amount、total字段
        """
        if not self.has_been_init_data:
            self.init_data()
        return self.bids

    def get_asks(self) -> list[dict[str, Any]]:
        """获取卖单列表.

        Returns:
            list: 卖单列表，每个元素包含price、count、amount、total字段
        """
        if not self.has_been_init_data:
            self.init_data()
        return self.asks

    def get_timestamp(self) -> float | None:
        """获取订单簿时间戳.

        Returns:
            float | None: 时间戳，可能为None
        """
        return self.timestamp

    def get_bid_price(self, level: int = 0) -> float | None:
        """获取指定档位的买单价格.

        Args:
            level: 档位索引（从0开始）

        Returns:
            float | None: 该档位的价格，如果不存在则返回None
        """
        if not self.has_been_init_data:
            self.init_data()
        if level < len(self.bids):
            return self.bids[level]["price"]
        return None

    def get_ask_price(self, level: int = 0) -> float | None:
        """获取指定档位的卖单价格.

        Args:
            level: 档位索引（从0开始）

        Returns:
            float | None: 该档位的价格，如果不存在则返回None
        """
        if not self.has_been_init_data:
            self.init_data()
        if level < len(self.asks):
            return self.asks[level]["price"]
        return None

    def get_bid_volume(self, level: int = 0) -> float | None:
        """获取指定档位的买单数量.

        Args:
            level: 档位索引（从0开始）

        Returns:
            float | None: 该档位的数量，如果不存在则返回None
        """
        if not self.has_been_init_data:
            self.init_data()
        if level < len(self.bids):
            return self.bids[level]["amount"]
        return None

    def get_ask_volume(self, level: int = 0) -> float | None:
        """获取指定档位的卖单数量.

        Args:
            level: 档位索引（从0开始）

        Returns:
            float | None: 该档位的数量，如果不存在则返回None
        """
        if not self.has_been_init_data:
            self.init_data()
        if level < len(self.asks):
            return self.asks[level]["amount"]
        return None

    def get_bid_count(self, level: int = 0) -> int | None:
        """获取指定档位的买单订单数量.

        Args:
            level: 档位索引（从0开始）

        Returns:
            int | None: 该档位的订单数量，如果不存在则返回None
        """
        if not self.has_been_init_data:
            self.init_data()
        if level < len(self.bids):
            return self.bids[level]["count"]
        return None

    def get_ask_count(self, level: int = 0) -> int | None:
        """获取指定档位的卖单订单数量.

        Args:
            level: 档位索引（从0开始）

        Returns:
            int | None: 该档位的订单数量，如果不存在则返回None
        """
        if not self.has_been_init_data:
            self.init_data()
        if level < len(self.asks):
            return self.asks[level]["count"]
        return None

    def get_spread(self) -> float | None:
        """获取买卖价差.

        Returns:
            float | None: 价差（卖一价 - 买一价），如果数据不足则返回None
        """
        if not self.has_been_init_data:
            self.init_data()
        if self.bids and self.asks:
            return self.asks[0]["price"] - self.bids[0]["price"]
        return None

    def get_mid_price(self) -> float | None:
        """获取中间价格.

        Returns:
            float | None: 中间价（(买一价 + 卖一价) / 2），如果数据不足则返回None
        """
        if not self.has_been_init_data:
            self.init_data()
        if self.bids and self.asks:
            return (self.bids[0]["price"] + self.asks[0]["price"]) / 2
        return None

    def get_total_bid_volume(self) -> float:
        """获取买单总数量.

        Returns:
            float: 所有买单的数量总和
        """
        if not self.has_been_init_data:
            self.init_data()
        return sum(bid["amount"] for bid in self.bids)

    def get_total_ask_volume(self) -> float:
        """获取卖单总数量.

        Returns:
            float: 所有卖单的数量总和
        """
        if not self.has_been_init_data:
            self.init_data()
        return sum(ask["amount"] for ask in self.asks)

    def get_total_bid_value(self) -> float:
        """获取买单总价值.

        Returns:
            float: 所有买单的总价值（价格 × 数量之和）
        """
        if not self.has_been_init_data:
            self.init_data()
        return sum(bid["total"] for bid in self.bids)

    def get_total_ask_value(self) -> float:
        """获取卖单总价值.

        Returns:
            float: 所有卖单的总价值（价格 × 数量之和）
        """
        if not self.has_been_init_data:
            self.init_data()
        return sum(ask["total"] for ask in self.asks)

    def get_bid_levels(self, levels: int = 5) -> list[dict[str, Any]]:
        """获取指定档数的买单数据.

        Args:
            levels: 要获取的档数，默认为5

        Returns:
            list: 买单列表（最多包含指定档数）
        """
        if not self.has_been_init_data:
            self.init_data()
        return self.bids[:levels]

    def get_ask_levels(self, levels: int = 5) -> list[dict[str, Any]]:
        """获取指定档数的卖单数据.

        Args:
            levels: 要获取的档数，默认为5

        Returns:
            list: 卖单列表（最多包含指定档数）
        """
        if not self.has_been_init_data:
            self.init_data()
        return self.asks[:levels]

    def get_price_impact(self, volume_ratio: float = 0.1) -> dict[str, Any]:
        """计算价格影响（估算）.

        Args:
            volume_ratio: 要计算的成交量比例（相对于总深度）

        Returns:
            dict: 包含buy_impact、sell_impact和spread的字典
        """
        if not self.has_been_init_data:
            self.init_data()

        buy_impact = None
        if self.bids:
            total_volume = self.get_total_bid_volume()
            target_volume = total_volume * volume_ratio

            cumulative_volume = 0.0
            weighted_price = 0.0

            for bid in self.bids:
                cumulative_volume += bid["amount"]
                weighted_price += bid["price"] * bid["amount"]

                if cumulative_volume >= target_volume:
                    buy_impact = weighted_price / cumulative_volume
                    break

        sell_impact = None
        if self.asks:
            total_volume = self.get_total_ask_volume()
            target_volume = total_volume * volume_ratio

            cumulative_volume = 0.0
            weighted_price = 0.0

            for ask in self.asks:
                cumulative_volume += ask["amount"]
                weighted_price += ask["price"] * ask["amount"]

                if cumulative_volume >= target_volume:
                    sell_impact = weighted_price / cumulative_volume
                    break

        return {"buy_impact": buy_impact, "sell_impact": sell_impact, "spread": self.get_spread()}


class BitfinexWssOrderBookData(BitfinexOrderBookData):
    """Bitfinex WebSocket订单簿数据容器.

    WebSocket订单簿格式与REST API相同。
    """


class BitfinexRequestOrderBookData(BitfinexOrderBookData):
    """Bitfinex REST API订单簿数据容器.

    REST API订单簿格式直接处理。
    """
