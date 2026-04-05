"""资金费率类，用于确定资金费率的属性和方法。"""

from __future__ import annotations

from typing import Any

from bt_api_py._compat import Self
from bt_api_py.containers.auto_init_mixin import AutoInitMixin


class FundingRateData(AutoInitMixin):
    """保存资金费率信息"""

    def __init__(self, funding_rate_info: Any, has_been_json_encoded: bool) -> None:
        self.event = "FundingEvent"
        self.funding_rate_info = funding_rate_info
        self.has_been_json_encoded = has_been_json_encoded
        self.exchange_name: str | None = None
        self.symbol_name: str | None = None
        self.asset_type: str | None = None
        self.funding_rate_data: Any = funding_rate_info if has_been_json_encoded else None
        self.local_update_time: float | None = None
        self.server_time: float | None = None
        self.funding_rate_symbol_name: str | None = None
        self.pre_funding_rate: float | None = None
        self.pre_funding_time: float | None = None
        self.next_funding_rate: float | None = None
        self.next_funding_time: float | None = None
        self.max_funding_rate: float | None = None
        self.min_funding_rate: float | None = None
        self.current_funding_rate: float | None = None
        self.current_funding_time: float | None = None
        self.settlement_funding_rate: float | None = None
        self.settlement_status: str | None = None
        self.method: str | None = None
        self.all_data: dict[str, Any] | None = None

    def get_event(self) -> str:
        return self.event

    def get_event_type(self) -> str:
        return self.get_event()

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
                "funding_rate_symbol_name": self.funding_rate_symbol_name,
                "pre_funding_rate": self.pre_funding_rate,
                "pre_funding_time": self.pre_funding_time,
                "next_funding_rate": self.next_funding_rate,
                "next_funding_time": self.next_funding_time,
                "max_funding_rate": self.max_funding_rate,
                "min_funding_rate": self.min_funding_rate,
                "current_funding_rate": self.current_funding_rate,
                "current_funding_time": self.current_funding_time,
                "settlement_funding_rate": self.settlement_funding_rate,
                "settlement_status": self.settlement_status,
                "method": self.method,
            }
        return self.all_data

    def get_exchange_name(self) -> str:
        raise NotImplementedError

    def get_server_time(self) -> float | None:
        raise NotImplementedError

    def get_local_update_time(self) -> float | None:
        raise NotImplementedError

    def get_asset_type(self) -> str | None:
        raise NotImplementedError

    def get_symbol_name(self) -> str | None:
        raise NotImplementedError

    def get_pre_funding_rate(self) -> float | None:
        raise NotImplementedError

    def get_pre_funding_time(self) -> float | None:
        raise NotImplementedError

    def get_next_funding_rate(self) -> float | None:
        raise NotImplementedError

    def get_next_funding_time(self) -> float | None:
        raise NotImplementedError

    def get_max_funding_rate(self) -> float | None:
        raise NotImplementedError

    def get_min_funding_rate(self) -> float | None:
        raise NotImplementedError

    def get_current_funding_rate(self) -> float | None:
        raise NotImplementedError

    def get_current_funding_time(self) -> float | None:
        raise NotImplementedError

    def get_settlement_funding_rate(self) -> float | None:
        raise NotImplementedError

    def get_settlement_status(self) -> str | None:
        raise NotImplementedError

    def get_method(self) -> str | None:
        raise NotImplementedError

    def __str__(self) -> str:
        raise NotImplementedError

    def __repr__(self) -> str:
        raise NotImplementedError
