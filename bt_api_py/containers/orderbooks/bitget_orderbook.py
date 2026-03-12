"""Bitget OrderBook Data Container."""

import json
import time
from typing import Any

from bt_api_py.containers.orderbooks.orderbook import OrderBookData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_int, from_dict_get_string


class BitgetOrderBookData(OrderBookData):
    """Bitget订单簿数据容器.

    保存和管理Bitget交易所的订单簿信息，包括买单、卖单列表，
    以及各种订单簿相关的计算功能。
    """

    def __init__(
        self,
        orderbook_info: str | dict,
        symbol_name: str,
        asset_type: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """初始化Bitget订单簿数据.

        Args:
            orderbook_info: 订单簿原始数据（JSON字符串或字典）
            symbol_name: 交易对名称
            asset_type: 资产类型（如 "SPOT", "SWAP"）
            has_been_json_encoded: 数据是否已经过JSON编码
        """
        super().__init__(orderbook_info, has_been_json_encoded)
        self.exchange_name = "BITGET"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.orderbook_data: Any = orderbook_info if has_been_json_encoded else None
        self.symbol: str | None = None
        self.time: float | None = None
        self.bids: list[tuple[float, float]] = []
        self.asks: list[tuple[float, float]] = []
        self.level: int | None = None
        self.all_data: dict[str, Any] | None = None
        self.has_been_init_data = False

    def init_data(self) -> "BitgetOrderBookData":
        """初始化并解析订单簿数据.

        将原始订单簿数据解析为结构化的买单和卖单列表。

        Returns:
            BitgetOrderBookData: 返回self以支持链式调用
        """
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

        self.bids = []
        if "bids" in self.orderbook_data:
            for bid in self.orderbook_data["bids"]:
                if isinstance(bid, list) and len(bid) >= 2:
                    self.bids.append((float(bid[0]), float(bid[1])))

        self.asks = []
        if "asks" in self.orderbook_data:
            for ask in self.orderbook_data["asks"]:
                if isinstance(ask, list) and len(ask) >= 2:
                    self.asks.append((float(ask[0]), float(ask[1])))

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
                "symbol": self.symbol,
                "time": self.time,
                "level": self.level,
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
            str: 交易所名称（"BITGET"）
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
            str: 资产类型（如 "SPOT", "SWAP"）
        """
        return self.asset_type

    def get_symbol(self) -> str | None:
        """获取交易所原始交易对符号.

        Returns:
            str | None: 交易所原始交易对符号
        """
        return self.symbol

    def get_time(self) -> float | None:
        """获取服务器时间戳.

        Returns:
            float | None: 服务器时间戳
        """
        return self.time

    def get_level(self) -> int | None:
        """获取订单簿深度级别.

        Returns:
            int | None: 深度级别
        """
        return self.level

    def get_bids(self) -> list[tuple[float, float]]:
        """获取买单列表（按价格降序排列）.

        Returns:
            list: 买单列表，每个元素为(价格, 数量)元组
        """
        self.init_data()
        return sorted(self.bids, key=lambda x: x[0], reverse=True)

    def get_asks(self) -> list[tuple[float, float]]:
        """获取卖单列表（按价格升序排列）.

        Returns:
            list: 卖单列表，每个元素为(价格, 数量)元组
        """
        self.init_data()
        return sorted(self.asks, key=lambda x: x[0])

    def get_spread(self) -> float | None:
        """获取买卖价差.

        Returns:
            float | None: 价差（卖一价 - 买一价），如果数据不足则返回None
        """
        self.init_data()
        if not self.bids or not self.asks:
            return None
        best_bid = max(bid[0] for bid in self.bids)
        best_ask = min(ask[0] for ask in self.asks)
        return best_ask - best_bid

    def get_best_bid(self) -> float | None:
        """获取最佳买价.

        Returns:
            float | None: 最佳买价，如果无数据则返回None
        """
        self.init_data()
        if not self.bids:
            return None
        return max(bid[0] for bid in self.bids)

    def get_best_ask(self) -> float | None:
        """获取最佳卖价.

        Returns:
            float | None: 最佳卖价，如果无数据则返回None
        """
        self.init_data()
        if not self.asks:
            return None
        return min(ask[0] for ask in self.asks)

    def get_total_bid_volume(self) -> float:
        """获取买单总数量.

        Returns:
            float: 所有买单的数量总和
        """
        self.init_data()
        return sum(bid[1] for bid in self.bids)

    def get_total_ask_volume(self) -> float:
        """获取卖单总数量.

        Returns:
            float: 所有卖单的数量总和
        """
        self.init_data()
        return sum(ask[1] for ask in self.asks)

    def get_mid_price(self) -> float | None:
        """获取中间价格（最佳买卖价的平均值）.

        Returns:
            float | None: 中间价，如果数据不足则返回None
        """
        self.init_data()
        best_bid = self.get_best_bid()
        best_ask = self.get_best_ask()
        if best_bid is None or best_ask is None:
            return None
        return (best_bid + best_ask) / 2


class BitgetWssOrderBookData(BitgetOrderBookData):
    """Bitget WebSocket订单簿数据容器."""

    def init_data(self) -> "BitgetWssOrderBookData":
        """初始化并解析WebSocket订单簿数据.

        Returns:
            BitgetWssOrderBookData: 返回self以支持链式调用
        """
        if not self.has_been_json_encoded:
            self.orderbook_data = json.loads(self.order_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        self.symbol = from_dict_get_string(self.orderbook_data, "s")
        self.time = from_dict_get_float(self.orderbook_data, "E")
        self.level = 1

        self.bids = []
        if "b" in self.orderbook_data:
            for bid in self.orderbook_data["b"]:
                if isinstance(bid, list) and len(bid) >= 2:
                    self.bids.append((float(bid[0]), float(bid[1])))

        self.asks = []
        if "a" in self.orderbook_data:
            for ask in self.orderbook_data["a"]:
                if isinstance(ask, list) and len(ask) >= 2:
                    self.asks.append((float(ask[0]), float(ask[1])))

        self.has_been_init_data = True
        return self


class BitgetRequestOrderBookData(BitgetOrderBookData):
    """Bitget REST API订单簿数据容器."""

    def init_data(self) -> "BitgetRequestOrderBookData":
        """初始化并解析REST API订单簿数据.

        Returns:
            BitgetRequestOrderBookData: 返回self以支持链式调用
        """
        if not self.has_been_json_encoded:
            self.orderbook_data = json.loads(self.order_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        self.symbol = from_dict_get_string(self.orderbook_data, "symbol")
        self.time = from_dict_get_float(self.orderbook_data, "ts")
        self.level = from_dict_get_int(self.orderbook_data, "level")

        self.bids = []
        if "bids" in self.orderbook_data:
            for bid in self.orderbook_data["bids"]:
                if isinstance(bid, list) and len(bid) >= 2:
                    self.bids.append((float(bid[0]), float(bid[1])))

        self.asks = []
        if "asks" in self.orderbook_data:
            for ask in self.orderbook_data["asks"]:
                if isinstance(ask, list) and len(ask) >= 2:
                    self.asks.append((float(ask[0]), float(ask[1])))

        self.has_been_init_data = True
        return self
