"""订单簿类，用于确定订单簿的属性和方法。"""

from __future__ import annotations

from typing import Any

from bt_api_py._compat import Self
from bt_api_py.containers.auto_init_mixin import AutoInitMixin


class OrderBookData(AutoInitMixin):
    """保存订单簿相关信息"""

    def __init__(self, order_book_info: Any, has_been_json_encoded: bool = False) -> None:
        self.event = "OrderBookEvent"
        self.order_book_info = order_book_info
        self.has_been_json_encoded = has_been_json_encoded
        self.exchange_name: str | None = None
        self.symbol_name: str | None = None
        self.asset_type: str | None = None
        self.order_book_data: Any = order_book_info if has_been_json_encoded else None
        self.local_update_time: float | None = None
        self.server_time: float | None = None
        self.order_book_symbol_name: str | None = None
        self.bid_price_list: list[float] | None = None
        self.ask_price_list: list[float] | None = None
        self.bid_volume_list: list[float] | None = None
        self.ask_volume_list: list[float] | None = None
        self.bid_trade_nums: list[int] | None = None
        self.ask_trade_nums: list[int] | None = None
        self.all_data: dict[str, Any] | None = None

    def init_data(self) -> None | Self:
        raise NotImplementedError

    def get_event(self) -> str:
        return self.event

    def get_all_data(self) -> dict[str, Any]:
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type,
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

    def get_exchange_name(self) -> str:
        raise NotImplementedError

    def get_local_update_time(self) -> float | None:
        raise NotImplementedError

    def get_symbol_name(self) -> str | None:
        raise NotImplementedError

    def get_asset_type(self) -> str | None:
        raise NotImplementedError

    def get_server_time(self) -> float | None:
        raise NotImplementedError

    def get_bid_price_list(self) -> list[float] | None:
        raise NotImplementedError

    def get_ask_price_list(self) -> list[float] | None:
        raise NotImplementedError

    def get_bid_volume_list(self) -> list[float] | None:
        raise NotImplementedError

    def get_ask_volume_list(self) -> list[float] | None:
        raise NotImplementedError

    def get_bid_trade_nums(self) -> list[int] | None:
        raise NotImplementedError

    def get_ask_trade_nums(self) -> list[int] | None:
        raise NotImplementedError

    def __str__(self) -> str:
        raise NotImplementedError

    def __repr__(self) -> str:
        raise NotImplementedError
