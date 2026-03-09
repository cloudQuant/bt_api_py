"""Gate.io Order Book Data Container."""

import json
import time
from typing import Any

from bt_api_py.containers.orderbooks.orderbook import OrderBookData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_int


class GateioOrderBookData(OrderBookData):
    """Gate.io订单簿数据容器.

    保存和管理Gate.io交易所的订单簿信息。
    """

    def __init__(
        self,
        orderbook_info: str | dict,
        symbol_name: str,
        asset_type: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """初始化Gate.io订单簿数据.

        Args:
            orderbook_info: 订单簿原始数据（JSON字符串或字典）
            symbol_name: 交易对名称
            asset_type: 资产类型（如 "SPOT"）
            has_been_json_encoded: 数据是否已经过JSON编码

        """
        super().__init__(orderbook_info, has_been_json_encoded)
        self.exchange_name = "GATEIO"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.orderbook_data: Any = orderbook_info if has_been_json_encoded else None
        self.sequence_id: int | None = None
        self.current_time: float | None = None
        self.update_time: float | None = None
        self.bids: list[dict[str, float]] | None = None
        self.asks: list[dict[str, float]] | None = None
        self.all_data: dict[str, Any] | None = None
        self.has_been_init_data = False

    def init_data(self) -> "GateioOrderBookData":
        """初始化并解析订单簿数据.

        Returns:
            GateioOrderBookData: 返回self以支持链式调用

        """
        if not self.has_been_json_encoded:
            self.orderbook_data = json.loads(self.order_book_info)
            self.has_been_json_encoded = True

        if self.has_been_init_data:
            return self

        self.sequence_id = from_dict_get_int(self.orderbook_data, "id")
        self.current_time = from_dict_get_float(self.orderbook_data, "current")
        self.update_time = from_dict_get_float(self.orderbook_data, "update")

        self.bids = []
        if "bids" in self.orderbook_data and isinstance(self.orderbook_data["bids"], list):
            for bid in self.orderbook_data["bids"]:
                if isinstance(bid, list) and len(bid) >= 2:
                    self.bids.append(
                        {
                            "price": from_dict_get_float(bid, 0),
                            "amount": from_dict_get_float(bid, 1),
                        }
                    )

        self.asks = []
        if "asks" in self.orderbook_data and isinstance(self.orderbook_data["asks"], list):
            for ask in self.orderbook_data["asks"]:
                if isinstance(ask, list) and len(ask) >= 2:
                    self.asks.append(
                        {
                            "price": from_dict_get_float(ask, 0),
                            "amount": from_dict_get_float(ask, 1),
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
                "sequence_id": self.sequence_id,
                "current_time": self.current_time,
                "update_time": self.update_time,
                "bids": self.bids,
                "asks": self.asks,
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
            str: 交易所名称（"GATEIO"）

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
            str: 资产类型

        """
        return self.asset_type

    def get_sequence_id(self) -> int | None:
        """获取序列ID.

        Returns:
            int | None: 序列ID

        """
        return self.sequence_id

    def get_current_time(self) -> float | None:
        """获取当前时间.

        Returns:
            float | None: 当前时间戳

        """
        return self.current_time

    def get_update_time(self) -> float | None:
        """获取更新时间.

        Returns:
            float | None: 更新时间戳

        """
        return self.update_time

    def get_bids(self) -> list[dict[str, float]] | None:
        """获取买单列表.

        Returns:
            list | None: 买单列表

        """
        return self.bids

    def get_asks(self) -> list[dict[str, float]] | None:
        """获取卖单列表.

        Returns:
            list | None: 卖单列表

        """
        return self.asks

    def get_best_bid(self) -> float | None:
        """获取最佳买价.

        Returns:
            float | None: 最佳买价，如果无数据则返回None

        """
        if self.bids and len(self.bids) > 0:
            return self.bids[0]["price"]
        return None

    def get_best_ask(self) -> float | None:
        """获取最佳卖价.

        Returns:
            float | None: 最佳卖价，如果无数据则返回None

        """
        if self.asks and len(self.asks) > 0:
            return self.asks[0]["price"]
        return None

    def get_spread(self) -> float | None:
        """获取买卖价差.

        Returns:
            float | None: 价差（卖一价 - 买一价），如果数据不足则返回None

        """
        best_bid = self.get_best_bid()
        best_ask = self.get_best_ask()
        if best_bid and best_ask:
            return best_ask - best_bid
        return None

    def get_bid_depth(self, price_levels: int = 5) -> list[dict[str, float]]:
        """获取指定深度的买单数据.

        Args:
            price_levels: 要获取的档数，默认为5

        Returns:
            list: 买单列表（最多包含指定档数）

        """
        if self.bids:
            return self.bids[:price_levels]
        return []

    def get_ask_depth(self, price_levels: int = 5) -> list[dict[str, float]]:
        """获取指定深度的卖单数据.

        Args:
            price_levels: 要获取的档数，默认为5

        Returns:
            list: 卖单列表（最多包含指定档数）

        """
        if self.asks:
            return self.asks[:price_levels]
        return []


class GateioRequestOrderBookData(GateioOrderBookData):
    """Gate.io REST API订单簿数据容器."""

    def init_data(self) -> "GateioRequestOrderBookData":
        """初始化并解析REST API订单簿数据.

        Returns:
            GateioRequestOrderBookData: 返回self以支持链式调用

        """
        if not self.has_been_json_encoded:
            self.orderbook_data = json.loads(self.order_book_info)
            self.has_been_json_encoded = True

        if self.has_been_init_data:
            return self

        self.sequence_id = from_dict_get_int(self.orderbook_data, "id")
        self.current_time = from_dict_get_float(self.orderbook_data, "current")
        self.update_time = from_dict_get_float(self.orderbook_data, "update")

        self.bids = []
        if "bids" in self.orderbook_data and isinstance(self.orderbook_data["bids"], list):
            for bid in self.orderbook_data["bids"]:
                if isinstance(bid, list) and len(bid) >= 2:
                    self.bids.append(
                        {
                            "price": from_dict_get_float(bid, 0),
                            "amount": from_dict_get_float(bid, 1),
                        }
                    )

        self.asks = []
        if "asks" in self.orderbook_data and isinstance(self.orderbook_data["asks"], list):
            for ask in self.orderbook_data["asks"]:
                if isinstance(ask, list) and len(ask) >= 2:
                    self.asks.append(
                        {
                            "price": from_dict_get_float(ask, 0),
                            "amount": from_dict_get_float(ask, 1),
                        }
                    )

        self.has_been_init_data = True
        return self


class GateioWssOrderBookData(GateioOrderBookData):
    """Gate.io WebSocket订单簿数据容器（预留实现）."""

    def init_data(self) -> "GateioWssOrderBookData":
        """初始化并解析WebSocket订单簿数据.

        Returns:
            GateioWssOrderBookData: 返回self以支持链式调用

        """
        return self
