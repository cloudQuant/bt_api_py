"""Crypto.com OrderBook Data Container."""

import json
import time
from typing import Any

from bt_api_py.containers.orderbooks.orderbook import OrderBookData


class CryptoComOrderBook(OrderBookData):
    """Crypto.com订单簿数据容器.

    保存和管理Crypto.com交易所的订单簿信息。
    """

    def __init__(
        self,
        order_book_info: str | dict,
        symbol_name: str,
        asset_type: str = "SPOT",
        has_been_json_encoded: bool = False,
    ) -> None:
        """初始化Crypto.com订单簿数据.

        Args:
            order_book_info: 订单簿原始数据（JSON字符串或字典）
            symbol_name: 交易对名称
            asset_type: 资产类型，默认为"SPOT"
            has_been_json_encoded: 数据是否已经过JSON编码
        """
        super().__init__(order_book_info, has_been_json_encoded)
        self.exchange_name = "CRYPTOCOM"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.order_book_data: Any = order_book_info if has_been_json_encoded else None
        self.order_book_symbol_name: str | None = None
        self.server_time: float | None = None
        self.bid_price_list: list[float] | None = None
        self.ask_price_list: list[float] | None = None
        self.bid_volume_list: list[float] | None = None
        self.ask_volume_list: list[float] | None = None
        self.bids: list[list[float | int]] = []
        self.asks: list[list[float | int]] = []
        self.all_data: dict[str, Any] | None = None
        self.has_been_init_data = False

    def init_data(self) -> "CryptoComOrderBook":
        """初始化并解析订单簿数据.

        Returns:
            CryptoComOrderBook: 返回self以支持链式调用
        """
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

    def get_all_data(self) -> dict[str, Any]:
        """获取所有订单簿数据.

        Returns:
            dict: 包含交易所名称、交易对、买卖单列表等完整信息
        """
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
            str: 交易所名称（"CRYPTOCOM"）
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

    def get_order_book_symbol_name(self) -> str | None:
        """获取订单簿交易对名称.

        Returns:
            str | None: 订单簿交易对名称
        """
        return self.order_book_symbol_name

    def get_asset_type(self) -> str:
        """获取资产类型.

        Returns:
            str: 资产类型
        """
        return self.asset_type

    def get_server_time(self) -> float | None:
        """获取服务器时间戳.

        Returns:
            float | None: 服务器时间戳
        """
        return self.server_time

    def get_bid_price_list(self) -> list[float] | None:
        """获取买单价格列表.

        Returns:
            list | None: 买单价格列表
        """
        return self.bid_price_list

    def get_ask_price_list(self) -> list[float] | None:
        """获取卖单价格列表.

        Returns:
            list | None: 卖单价格列表
        """
        return self.ask_price_list

    def get_bid_volume_list(self) -> list[float] | None:
        """获取买单数量列表.

        Returns:
            list | None: 买单数量列表
        """
        return self.bid_volume_list

    def get_ask_volume_list(self) -> list[float] | None:
        """获取卖单数量列表.

        Returns:
            list | None: 卖单数量列表
        """
        return self.ask_volume_list

    def get_bid_trade_nums(self) -> list[int]:
        """获取买单订单数量列表.

        Returns:
            list: 买单订单数量列表
        """
        return [int(b[2]) if len(b) > 2 else 0 for b in self.bids]

    def get_ask_trade_nums(self) -> list[int]:
        """获取卖单订单数量列表.

        Returns:
            list: 卖单订单数量列表
        """
        return [int(a[2]) if len(a) > 2 else 0 for a in self.asks]

    def get_price_levels(self, side: str, levels: int = 10) -> list[list[float | int]]:
        """获取指定方向的指定档数价格数据.

        Args:
            side: 方向（"ASK"或"BID"）
            levels: 要获取的档数，默认为10

        Returns:
            list: 价格档位列表
        """
        if side.upper() == "ASK":
            return self.asks[:levels]
        elif side.upper() == "BID":
            return self.bids[:levels]
        return []

    @classmethod
    def from_api_response(cls, data: dict[str, Any], symbol: str) -> "CryptoComOrderBook":
        """从API响应创建订单簿实例.

        这是一个便捷方法，主要用于测试。
        生产环境请使用标准构造函数。

        Args:
            data: API响应数据
            symbol: 交易对名称

        Returns:
            CryptoComOrderBook: 订单簿实例
        """
        return cls(
            order_book_info=json.dumps(data),
            symbol_name=symbol,
            asset_type="SPOT",
            has_been_json_encoded=False,
        )
