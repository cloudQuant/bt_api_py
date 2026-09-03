"""Operation backend protocol shared by direct and ZMQ transports (Task 1.2)."""

from __future__ import annotations

from typing import Any, Protocol

from bt_api_py._contracts.models import (
    CancelAllRequest,
    CancelOrderRequest,
    CommandStatus,
    Consistency,
    OrderRequest,
    QueryOrderRequest,
)


class OperationBackend(Protocol):
    """The operations ``BtApi`` delegates to a transport backend.

    Direct and ZMQ backends expose the same named operations so a single
    ``BtApi`` boundary can route to either without a second public client.
    """

    def get_tick(
        self,
        exchange_name: str,
        symbol: str,
        *,
        consistency: Consistency = Consistency.LIVE,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any: ...

    def get_depth(
        self,
        exchange_name: str,
        symbol: str,
        count: int = 10,
        *,
        consistency: Consistency = Consistency.LIVE,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any: ...

    def get_kline(
        self,
        exchange_name: str,
        symbol: str,
        period: str,
        count: int = 500,
        *,
        consistency: Consistency = Consistency.LIVE,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any: ...

    def get_account(
        self,
        exchange_name: str,
        *,
        consistency: Consistency = Consistency.LIVE,
        symbol: str = "ALL",
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any: ...

    def get_balance(
        self,
        exchange_name: str,
        *,
        consistency: Consistency = Consistency.LIVE,
        symbol: str | None = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any: ...

    def get_position(
        self,
        exchange_name: str,
        *,
        consistency: Consistency = Consistency.LIVE,
        symbol: str | None = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any: ...

    def get_open_orders(
        self,
        exchange_name: str,
        *,
        consistency: Consistency = Consistency.LIVE,
        symbol: str | None = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any: ...

    def get_deals(
        self,
        exchange_name: str,
        *,
        consistency: Consistency = Consistency.LIVE,
        symbol: str | None = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any: ...

    def make_order(self, exchange_name: str, request: OrderRequest) -> Any: ...

    def cancel_order(
        self,
        exchange_name: str,
        request: CancelOrderRequest,
        *,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any: ...

    def cancel_all(
        self,
        exchange_name: str,
        request: CancelAllRequest,
        *,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any: ...

    def query_order(
        self,
        exchange_name: str,
        request: QueryOrderRequest,
        *,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any: ...

    def get_command_status(self, exchange_name: str, command_id: str) -> CommandStatus: ...
