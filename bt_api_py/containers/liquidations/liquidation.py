"""Liquidation warning data container base class."""

from __future__ import annotations

from typing import Any, Self


class LiquidationData:
    """Base class for liquidation warning data."""

    def __init__(self, liquidation_info: Any, has_been_json_encoded: bool = False) -> None:
        self.event = "LiquidationWarningEvent"
        self.liquidation_info = liquidation_info
        self.has_been_json_encoded = has_been_json_encoded
        self.exchange_name: str | None = None
        self.local_update_time: float | None = None
        self.asset_type: str | None = None
        self.symbol_name: str | None = None
        self.liquidation_data: Any = liquidation_info if has_been_json_encoded else None
        self.server_time: float | None = None
        self.all_data: dict[str, Any] | None = None

    def get_event(self) -> str:
        return self.event

    def init_data(self) -> None | Self:
        raise NotImplementedError

    def get_all_data(self) -> dict[str, Any]:
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "local_update_time": self.local_update_time,
                "asset_type": self.asset_type,
                "server_time": self.server_time,
            }
        return self.all_data

    def get_exchange_name(self) -> str:
        raise NotImplementedError

    def get_asset_type(self) -> str | None:
        raise NotImplementedError

    def get_symbol_name(self) -> str | None:
        raise NotImplementedError

    def get_server_time(self) -> float | None:
        raise NotImplementedError

    def get_local_update_time(self) -> float | None:
        raise NotImplementedError

    def __str__(self) -> str:
        raise NotImplementedError

    def __repr__(self) -> str:
        return self.__str__()
