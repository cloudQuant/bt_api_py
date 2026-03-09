import time
from typing import Any

from bt_api_py.containers.orderbooks.orderbook import OrderBookData


class BybitOrderBookData(OrderBookData):
    """Bybit订单簿数据容器.

    保存和管理Bybit交易所的订单簿信息，包括买单、卖单列表，
    以及各种订单簿相关的计算功能。
    """

    def __init__(
        self,
        orderbook_info: str | dict,
        symbol_name: str,
        asset_type: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """初始化Bybit订单簿数据.

        Args:
            orderbook_info: 订单簿原始数据（JSON字符串或字典）
            symbol_name: 交易对名称
            asset_type: 资产类型（如 "SPOT", "SWAP"）
            has_been_json_encoded: 数据是否已经过JSON编码
        """
        super().__init__(orderbook_info, has_been_json_encoded)
        self.exchange_name = "BYBIT"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.orderbook_data: Any = orderbook_info if has_been_json_encoded else None
        self.bids: list[list[float]] = []
        self.asks: list[list[float]] = []
        self.timestamp: float | None = None
        self.update_id: int | None = None
        self.all_data: dict[str, Any] | None = None
        self.has_been_init_data = False

    def init_data(self) -> "BybitOrderBookData":
        """初始化并解析订单簿数据.

        将原始订单簿数据解析为结构化的买单和卖单列表。

        Returns:
            BybitOrderBookData: 返回self以支持链式调用
        """
        if self.has_been_init_data or self.orderbook_data is None:
            return self

        try:
            result = self.orderbook_data.get("result", {})

            self.timestamp = result.get("ts")
            self.update_id = result.get("u")

            bids_data = result.get("b", [])
            asks_data = result.get("a", [])

            self.bids = []
            for bid in bids_data:
                if len(bid) >= 2:
                    price = float(bid[0])
                    quantity = float(bid[1])
                    self.bids.append([price, quantity])

            self.asks = []
            for ask in asks_data:
                if len(ask) >= 2:
                    price = float(ask[0])
                    quantity = float(ask[1])
                    self.asks.append([price, quantity])

            self.bids.sort(key=lambda x: x[0], reverse=True)
            self.asks.sort(key=lambda x: x[0])

            self.has_been_init_data = True

        except Exception as e:
            print(f"Error parsing Bybit orderbook data: {e}")
            self.has_been_init_data = False
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
                "timestamp": self.timestamp,
                "update_id": self.update_id,
                "bids": self.bids,
                "asks": self.asks,
            }
        return self.all_data

    def get_best_bid(self) -> float | None:
        """获取最佳买价.

        Returns:
            float | None: 最佳买价，如果无数据则返回None
        """
        if self.bids:
            return self.bids[0][0]
        return None

    def get_best_ask(self) -> float | None:
        """获取最佳卖价.

        Returns:
            float | None: 最佳卖价，如果无数据则返回None
        """
        if self.asks:
            return self.asks[0][0]
        return None

    def get_spread(self) -> float | None:
        """获取买卖价差.

        Returns:
            float | None: 价差（卖一价 - 买一价），如果数据不足则返回None
        """
        best_bid = self.get_best_bid()
        best_ask = self.get_best_ask()
        if best_bid is not None and best_ask is not None:
            return best_ask - best_bid
        return None

    def get_bid_depth(self, depth: int = 5) -> list[list[float]]:
        """获取指定深度的买单.

        Args:
            depth: 要获取的深度，默认为5

        Returns:
            list: 买单列表（最多包含指定深度）
        """
        return self.bids[:depth]

    def get_ask_depth(self, depth: int = 5) -> list[list[float]]:
        """获取指定深度的卖单.

        Args:
            depth: 要获取的深度，默认为5

        Returns:
            list: 卖单列表（最多包含指定深度）
        """
        return self.asks[:depth]

    def get_total_bid_qty(self) -> float:
        """获取买单总数量.

        Returns:
            float: 所有买单的数量总和
        """
        return sum(bid[1] for bid in self.bids)

    def get_total_ask_qty(self) -> float:
        """获取卖单总数量.

        Returns:
            float: 所有卖单的数量总和
        """
        return sum(ask[1] for ask in self.asks)

    def is_valid(self) -> bool:
        """检查订单簿是否有效.

        Returns:
            bool: 如果买卖单都存在则返回True，否则返回False
        """
        return len(self.bids) > 0 and len(self.asks) > 0

    def __str__(self) -> str:
        """返回订单簿的字符串表示."""
        self.init_data()
        best_bid = self.get_best_bid()
        best_ask = self.get_best_ask()
        return (
            f"BybitOrderBook(symbol={self.symbol_name}, "
            f"bid={best_bid}, "
            f"ask={best_ask}, "
            f"spread={self.get_spread()})"
        )


class BybitSpotOrderBookData(BybitOrderBookData):
    """Bybit现货订单簿数据容器."""

    def __init__(
        self,
        orderbook_info: str | dict,
        symbol_name: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """初始化Bybit现货订单簿数据.

        Args:
            orderbook_info: 订单簿原始数据（JSON字符串或字典）
            symbol_name: 交易对名称
            has_been_json_encoded: 数据是否已经过JSON编码
        """
        super().__init__(orderbook_info, symbol_name, "spot", has_been_json_encoded)


class BybitSwapOrderBookData(BybitOrderBookData):
    """Bybit期货/swap订单簿数据容器."""

    def __init__(
        self,
        orderbook_info: str | dict,
        symbol_name: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """初始化Bybit期货订单簿数据.

        Args:
            orderbook_info: 订单簿原始数据（JSON字符串或字典）
            symbol_name: 交易对名称
            has_been_json_encoded: 数据是否已经过JSON编码
        """
        super().__init__(orderbook_info, symbol_name, "swap", has_been_json_encoded)
