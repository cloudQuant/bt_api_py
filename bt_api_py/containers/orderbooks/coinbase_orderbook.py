"""Coinbase orderbook data container."""

from __future__ import annotations

import json
import time
from typing import Any

from bt_api_py.containers.orderbooks.orderbook import OrderBookData
from bt_api_py.functions.utils import from_dict_get_string
from bt_api_py.logging_factory import get_logger

logger = get_logger("container")


class CoinbaseOrderBookData(OrderBookData):
    """Coinbase订单簿数据容器基类.

    保存和管理Coinbase交易所的订单簿信息。
    """

    def __init__(
        self,
        order_book_info: str | dict,
        symbol_name: str,
        asset_type: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """初始化Coinbase订单簿数据.

        Args:
            order_book_info: 订单簿原始数据（JSON字符串或字典）
            symbol_name: 交易对名称
            asset_type: 资产类型（如 "SPOT"）
            has_been_json_encoded: 数据是否已经过JSON编码
        """
        super().__init__(order_book_info, has_been_json_encoded)
        self.exchange_name = "COINBASE"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.order_book_data: Any = order_book_info if has_been_json_encoded else None
        self.order_book_symbol_name: str | None = None
        self.server_time: str | None = None
        self.bid_price_list: list[float] | None = None
        self.ask_price_list: list[float] | None = None
        self.bid_volume_list: list[float] | None = None
        self.ask_volume_list: list[float] | None = None
        self.bid_trade_nums: list[int] | None = None
        self.ask_trade_nums: list[int] | None = None
        self.all_data: dict[str, Any] | None = None
        self.has_been_init_data = False

    def init_data(self) -> CoinbaseOrderBookData:
        """初始化并解析订单簿数据（子类需实现）.

        Returns:
            CoinbaseOrderBookData: 返回self以支持链式调用

        Raises:
            NotImplementedError: 子类必须实现此方法
        """
        raise NotImplementedError("Subclasses must implement init_data")

    def get_all_data(self) -> dict[str, Any]:
        """获取所有订单簿数据.

        Returns:
            dict: 包含交易所名称、交易对、买卖单列表等完整信息
        """
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "asset_type": self.asset_type,
                "symbol_name": self.symbol_name,
                "order_book_symbol_name": self.order_book_symbol_name,
                "local_update_time": self.local_update_time,
                "server_time": self.server_time,
                "bid_price_list": self.bid_price_list,
                "ask_price_list": self.ask_price_list,
                "bid_volume_list": self.bid_volume_list,
                "ask_volume_list": self.ask_volume_list,
                "bid_trade_nums": self.bid_trade_nums,
                "ask_trade_nums": self.ask_trade_nums,
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
            str: 交易所名称（"COINBASE"）
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
            str: 资产类型（如 "SPOT"）
        """
        return self.asset_type

    def get_server_time(self) -> str | None:
        """获取服务器时间.

        Returns:
            str | None: 服务器时间字符串
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

    def get_bid_trade_nums(self) -> list[int] | None:
        """获取买单订单数量列表.

        Returns:
            list | None: 买单订单数量列表
        """
        return self.bid_trade_nums

    def get_ask_trade_nums(self) -> list[int] | None:
        """获取卖单订单数量列表.

        Returns:
            list | None: 卖单订单数量列表
        """
        return self.ask_trade_nums


class CoinbaseRequestOrderBookData(CoinbaseOrderBookData):
    """Coinbase REST API订单簿数据容器.

    API响应格式示例:
    {
        "pricebook": {
            "product_id": "BTC-USD",
            "bids": [{"price": "49999", "size": "1.5"}, ...],
            "asks": [{"price": "50001", "size": "1.8"}, ...],
            "time": "2024-01-01T00:00:00Z"
        }
    }
    """

    def init_data(self) -> CoinbaseRequestOrderBookData:
        """初始化并解析REST API订单簿数据.

        Returns:
            CoinbaseRequestOrderBookData: 返回self以支持链式调用
        """
        if not self.has_been_json_encoded:
            self.order_book_data = json.loads(self.order_book_info)
            self.has_been_json_encoded = True
        if isinstance(self.order_book_data, str):
            self.order_book_data = json.loads(self.order_book_data)
        if self.has_been_init_data:
            return self
        try:
            if isinstance(self.order_book_data, dict):
                pricebook = self.order_book_data.get("pricebook", self.order_book_data)
                self.order_book_symbol_name = from_dict_get_string(pricebook, "product_id")
                self.server_time = from_dict_get_string(pricebook, "time")

                bids = pricebook.get("bids", [])
                asks = pricebook.get("asks", [])

                self.bid_price_list = []
                self.bid_volume_list = []
                for bid in bids:
                    if isinstance(bid, dict):
                        self.bid_price_list.append(float(bid.get("price", 0)))
                        self.bid_volume_list.append(float(bid.get("size", 0)))
                    elif isinstance(bid, (list, tuple)) and len(bid) >= 2:
                        self.bid_price_list.append(float(bid[0]))
                        self.bid_volume_list.append(float(bid[1]))

                self.ask_price_list = []
                self.ask_volume_list = []
                for ask in asks:
                    if isinstance(ask, dict):
                        self.ask_price_list.append(float(ask.get("price", 0)))
                        self.ask_volume_list.append(float(ask.get("size", 0)))
                    elif isinstance(ask, (list, tuple)) and len(ask) >= 2:
                        self.ask_price_list.append(float(ask[0]))
                        self.ask_volume_list.append(float(ask[1]))

        except Exception as e:
            logger.error(f"Error parsing orderbook data: {e}", exc_info=True)
            self.order_book_data = {}
        self.has_been_init_data = True
        return self
