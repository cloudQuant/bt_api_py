"""收入类，用于确定收入的属性和方法。"""

from __future__ import annotations

from typing import Any, Self

from bt_api_py.containers.auto_init_mixin import AutoInitMixin


class IncomeData(AutoInitMixin):
    """保存收入信息"""

    def __init__(self, income_info: Any, has_been_json_encoded: bool) -> None:
        self.event = "IncomeEvent"
        self.income_info = income_info
        self.has_been_json_encoded = has_been_json_encoded
        self.exchange_name: str | None = None
        self.symbol_name: str | None = None
        self.asset_type: str | None = None
        self.income_data: Any = income_info if has_been_json_encoded else None
        self.local_update_time: float | None = None
        self.server_time: float | None = None
        self.income_type: str | None = None
        self.income_value: float | None = None
        self.income_asset: str | None = None
        self.all_data: dict[str, Any] | None = None

    def init_data(self) -> None | Self:
        raise NotImplementedError

    def get_event(self) -> str:
        return self.event

    def get_event_type(self) -> str:
        return self.get_event()

    def get_all_data(self) -> dict[str, Any]:
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "server_time": self.server_time,
                "income_type": self.income_type,
                "income_value": self.income_value,
                "income_asset": self.income_asset,
            }
        return self.all_data

    def get_exchange_name(self) -> str:
        raise NotImplementedError

    def get_server_time(self) -> float | None:
        raise NotImplementedError

    def get_local_update_time(self) -> float | None:
        raise NotImplementedError

    def get_symbol_name(self) -> str | None:
        raise NotImplementedError

    def get_income_type(self) -> str | None:
        raise NotImplementedError

    def get_income_value(self) -> float | None:
        raise NotImplementedError

    def get_income_asset(self) -> str | None:
        raise NotImplementedError

    def __str__(self) -> str:
        raise NotImplementedError

    def __repr__(self) -> str:
        raise NotImplementedError
