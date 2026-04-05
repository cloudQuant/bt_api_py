"""标记价格类，用于确定标记价格的属性和方法。"""

from __future__ import annotations

from typing import Any

from bt_api_py._compat import Self
from bt_api_py.containers.auto_init_mixin import AutoInitMixin


class MarkPriceData(AutoInitMixin):
    """保存标记价格信息."""

    def __init__(self, mark_price_info: Any, has_been_json_encoded: bool = False) -> None:
        self.event = "MarkPriceEvent"
        self.mark_price_info = mark_price_info
        self.has_been_json_encoded = has_been_json_encoded
        self.exchange_name: str | None = None
        self.symbol_name: str | None = None
        self.asset_type: str | None = None
        self.mark_price_data: Any = mark_price_info if has_been_json_encoded else None
        self.local_update_time: float | None = None
        self.server_time: float | None = None
        self.mark_price_symbol_name: str | None = None
        self.mark_price: float | None = None
        self.index_price: float | None = None
        self.settlement_price: float | None = None
        self.all_data: dict[str, Any] | None = None

    def init_data(self) -> None | Self:
        raise NotImplementedError

    def get_all_data(self) -> dict[str, Any]:
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "server_time": self.server_time,
                "mark_price_symbol_name": self.mark_price_symbol_name,
                "mark_price": self.mark_price,
                "index_price": self.index_price,
                "settlement_price": self.settlement_price,
            }
        return self.all_data

    def get_event(self) -> str:
        return self.event

    def get_exchange_name(self) -> str:
        raise NotImplementedError

    def get_server_time(self) -> float | None:
        raise NotImplementedError

    def get_local_update_time(self) -> float | None:
        raise NotImplementedError

    def get_symbol_name(self) -> str | None:
        raise NotImplementedError

    def get_mark_price(self) -> float | None:
        raise NotImplementedError

    def __str__(self) -> str:
        raise NotImplementedError

    def __repr__(self) -> str:
        raise NotImplementedError
