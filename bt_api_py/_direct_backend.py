"""Direct backend that calls the BtApi-registered exchange feeds (Task 1.2)."""

from __future__ import annotations

from typing import Any

from bt_api_py._contracts.errors import CapabilityNotSupportedError
from bt_api_py._contracts.models import (
    CancelAllRequest,
    CancelOrderRequest,
    Consistency,
    OrderRequest,
    QueryOrderRequest,
)


class DirectBackend:
    """Direct transport backend delegating to ``BtApi`` exchange feeds.

    Keeps the existing feed call semantics for ``transport_mode="direct"``.
    Result normalization into contract snapshots happens in the venue mapper
    layer (Task 2.2), so this backend preserves feed-native return values for
    backward compatibility.
    """

    def __init__(self, get_feed: Any, feeds: dict[str, Any]) -> None:
        self._get_feed = get_feed
        self._feeds = feeds

    def _feed(self, exchange_name: str) -> Any:
        return self._get_feed(exchange_name)

    def get_tick(
        self,
        exchange_name: str,
        symbol: str,
        *,
        consistency: Consistency = Consistency.LIVE,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        return self._feed(exchange_name).get_tick(symbol, extra_data=extra_data, **kwargs)

    def get_depth(
        self,
        exchange_name: str,
        symbol: str,
        count: int = 10,
        *,
        consistency: Consistency = Consistency.LIVE,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        return self._feed(exchange_name).get_depth(
            symbol, count=count, extra_data=extra_data, **kwargs
        )

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
    ) -> Any:
        return self._feed(exchange_name).get_kline(
            symbol, period, count=count, extra_data=extra_data, **kwargs
        )

    def get_account(
        self,
        exchange_name: str,
        *,
        consistency: Consistency = Consistency.LIVE,
        symbol: str = "ALL",
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        return self._feed(exchange_name).get_account(symbol, extra_data=extra_data, **kwargs)

    def get_balance(
        self,
        exchange_name: str,
        *,
        consistency: Consistency = Consistency.LIVE,
        symbol: str | None = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        return self._feed(exchange_name).get_balance(symbol, extra_data=extra_data, **kwargs)

    def get_position(
        self,
        exchange_name: str,
        *,
        consistency: Consistency = Consistency.LIVE,
        symbol: str | None = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        return self._feed(exchange_name).get_position(symbol, extra_data=extra_data, **kwargs)

    def get_open_orders(
        self,
        exchange_name: str,
        *,
        consistency: Consistency = Consistency.LIVE,
        symbol: str | None = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        return self._feed(exchange_name).get_open_orders(symbol, extra_data=extra_data, **kwargs)

    def get_deals(
        self,
        exchange_name: str,
        *,
        consistency: Consistency = Consistency.LIVE,
        symbol: str | None = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        return self._feed(exchange_name).get_deals(symbol, extra_data=extra_data, **kwargs)

    def make_order(self, exchange_name: str, request: OrderRequest) -> Any:
        feed = self._feed(exchange_name)
        from bt_api_py._feed_adapter import FeedAdapter
        from bt_api_py._venue_mappers import get_venue_mapper

        mapper = get_venue_mapper(exchange_name)
        if mapper is not None:
            return FeedAdapter(feed, mapper).make_order(request)
        return feed.make_order(
            request.symbol,
            float(request.quantity),
            float(request.price) if request.price is not None else 0,
            f"{request.side.value}-{request.order_type.value}",
            offset="close" if request.reduce_only else "open",
            post_only=request.time_in_force == "post_only",
            client_order_id=request.client_order_id,
        )

    def cancel_order(
        self,
        exchange_name: str,
        request: CancelOrderRequest,
        *,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        order_id = request.order_id or request.client_order_id
        return self._feed(exchange_name).cancel_order(
            request.symbol, order_id or "", extra_data=extra_data, **kwargs
        )

    def cancel_all(
        self,
        exchange_name: str,
        request: CancelAllRequest,
        *,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        return self._feed(exchange_name).cancel_all(request.symbol, extra_data=extra_data, **kwargs)

    def query_order(
        self,
        exchange_name: str,
        request: QueryOrderRequest,
        *,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        order_id = request.order_id or request.client_order_id
        return self._feed(exchange_name).query_order(
            request.symbol, order_id or "", extra_data=extra_data, **kwargs
        )

    def get_command_status(self, exchange_name: str, command_id: str) -> Any:
        del exchange_name, command_id
        raise CapabilityNotSupportedError(
            "get_command_status",
            detail="transport=direct has no forwarding command receipt store",
        )
