"""Direct backend that calls the BtApi-registered exchange feeds (Task 1.2)."""

from __future__ import annotations

import asyncio
import inspect
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

    def __init__(
        self,
        get_feed: Any,
        feeds: dict[str, Any],
    ) -> None:
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

    def _read_method(self, exchange_name: str, operation: str, *aliases: str) -> Any:
        feed = self._feed(exchange_name)
        for name in (operation, *aliases):
            method = getattr(feed, name, None)
            if callable(method):
                return method
        raise CapabilityNotSupportedError(
            operation,
            detail=f"transport=direct; {exchange_name} does not implement this operation",
        )

    def get_account_config(
        self, exchange_name: str, *, extra_data: Any = None, **kwargs: Any
    ) -> Any:
        from bt_api_py.account_config_composition import account_config_read_aliases

        aliases = account_config_read_aliases(exchange_name)
        method = self._read_method(exchange_name, "get_account_config", *aliases)
        return method(extra_data=extra_data, **kwargs)

    def get_position_mode(
        self, exchange_name: str, *, extra_data: Any = None, **kwargs: Any
    ) -> Any:
        from bt_api_py.position_mode_composition import position_mode_read_aliases

        aliases = position_mode_read_aliases(exchange_name)
        method = self._read_method(exchange_name, "get_position_mode", *aliases)
        return method(extra_data=extra_data, **kwargs)

    def set_position_mode(
        self,
        exchange_name: str,
        position_mode: str,
        *,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        from bt_api_py.position_mode_composition import resolve_position_mode_writer

        writer = resolve_position_mode_writer(exchange_name)
        return writer(self._feed(exchange_name), exchange_name, position_mode, extra_data, kwargs)

    def get_exchange_info(
        self,
        exchange_name: str,
        symbol: str | None = None,
        *,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        method = self._read_method(exchange_name, "get_exchange_info")
        return method(symbol, extra_data=extra_data, **kwargs)

    def get_symbol_config(
        self,
        exchange_name: str,
        symbol: str,
        *,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        method = self._read_method(exchange_name, "get_symbol_config")
        return method(symbol, extra_data=extra_data, **kwargs)

    def get_leverage_bracket(
        self,
        exchange_name: str,
        symbol: str,
        *,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        method = self._read_method(exchange_name, "get_leverage_bracket")
        return method(symbol, extra_data=extra_data, **kwargs)

    def get_account_instruments(
        self,
        exchange_name: str,
        symbol: str,
        *,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        method = self._read_method(exchange_name, "get_account_instruments")
        return method(symbol, extra_data=extra_data, **kwargs)

    def get_leverage_info(
        self,
        exchange_name: str,
        symbol: str,
        *,
        margin_mode: str,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        method = self._read_method(exchange_name, "get_leverage_info")
        return method(symbol, margin_mode=margin_mode, extra_data=extra_data, **kwargs)

    def get_max_size(
        self,
        exchange_name: str,
        symbol: str,
        *,
        margin_mode: str,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        method = self._read_method(exchange_name, "get_max_size")
        return method(symbol, margin_mode, extra_data=extra_data, **kwargs)

    @staticmethod
    def _validate_order_units(exchange_name: str, request: OrderRequest) -> None:
        from bt_api_py.order_quantity_units import validate_order_quantity_units

        validate_order_quantity_units(exchange_name, request)

    def make_order(self, exchange_name: str, request: OrderRequest) -> Any:
        self._validate_order_units(exchange_name, request)
        feed = self._feed(exchange_name)
        from bt_api_py._feed_adapter import FeedAdapter
        from bt_api_py._venue_mappers import get_venue_mapper

        mapper = get_venue_mapper(exchange_name)
        if mapper is not None:
            return FeedAdapter(
                feed,
                mapper,
            ).make_order(request)
        if any(
            value is not None
            for value in (
                request.position_side,
                request.position_id,
                request.position_mode,
                request.exchange_id,
                request.offset,
            )
        ):
            raise CapabilityNotSupportedError(
                "make_order",
                detail="venue has no mapper for explicit position intent",
                definite_reject=True,
            )
        return feed.make_order(
            request.symbol,
            float(request.quantity),
            float(request.price) if request.price is not None else 0,
            f"{request.side.value}-{request.order_type.value}",
            offset="close" if request.reduce_only else "open",
            post_only=request.time_in_force == "post_only",
            client_order_id=request.client_order_id,
        )

    async def async_make_order(self, exchange_name: str, request: OrderRequest) -> Any:
        """Use a true feed coroutine when supplied, with a sync-worker fallback."""
        self._validate_order_units(exchange_name, request)
        feed = self._feed(exchange_name)
        from bt_api_py._feed_adapter import FeedAdapter
        from bt_api_py._venue_mappers import get_venue_mapper

        mapper = get_venue_mapper(exchange_name)
        if mapper is not None:
            return await FeedAdapter(
                feed,
                mapper,
            ).async_make_order(request)
        method = getattr(feed, "async_make_order", None)
        if not callable(method) or not inspect.iscoroutinefunction(method):
            return await asyncio.to_thread(self.make_order, exchange_name, request)
        return await method(
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
        feed = self._feed(exchange_name)
        order_id = request.order_id or request.client_order_id
        from bt_api_py.venue_order_identity import project_order_identity

        order_id = project_order_identity(exchange_name, "cancel", request, kwargs, order_id)
        return feed.cancel_order(
            request.symbol, order_id, extra_data=extra_data, **kwargs
        )

    async def async_cancel_order(
        self,
        exchange_name: str,
        request: CancelOrderRequest,
        *,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        feed = self._feed(exchange_name)
        method = getattr(feed, "async_cancel_order", None)
        if not callable(method) or not inspect.iscoroutinefunction(method):
            return await asyncio.to_thread(
                self.cancel_order,
                exchange_name,
                request,
                extra_data=extra_data,
                **kwargs,
            )
        order_id = request.order_id or request.client_order_id
        from bt_api_py.venue_order_identity import project_order_identity

        order_id = project_order_identity(exchange_name, "cancel", request, kwargs, order_id)
        return await method(request.symbol, order_id, extra_data=extra_data, **kwargs)

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
        from bt_api_py.venue_order_identity import project_order_identity

        order_id = project_order_identity(exchange_name, "query", request, kwargs, order_id)
        return self._feed(exchange_name).query_order(
            request.symbol, order_id, extra_data=extra_data, **kwargs
        )

    async def async_query_order(
        self,
        exchange_name: str,
        request: QueryOrderRequest,
        *,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        feed = self._feed(exchange_name)
        method = getattr(feed, "async_query_order", None)
        if not callable(method) or not inspect.iscoroutinefunction(method):
            return await asyncio.to_thread(
                self.query_order,
                exchange_name,
                request,
                extra_data=extra_data,
                **kwargs,
            )
        order_id = request.order_id or request.client_order_id
        from bt_api_py.venue_order_identity import project_order_identity

        order_id = project_order_identity(exchange_name, "query", request, kwargs, order_id)
        return await method(request.symbol, order_id, extra_data=extra_data, **kwargs)

    def poll_event(self, exchange_name: str) -> Any:
        """Reject private-event polling on the ordinary direct backend."""
        del exchange_name
        raise CapabilityNotSupportedError(
            "poll_event",
            detail="transport=direct has no event ingress",
        )

    def close(self) -> None:
        """The direct backend borrows feeds and owns no transport resource."""
        return None

    def get_command_status(self, exchange_name: str, command_id: str) -> Any:
        del exchange_name, command_id
        raise CapabilityNotSupportedError(
            "get_command_status",
            detail="transport=direct has no forwarding command receipt store",
        )
