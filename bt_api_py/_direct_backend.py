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
from bt_api_py._gateway_wire import build_intent, outcome_from_ack, outcome_from_dispatch_status


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
        execution_capability: Any = None,
        client: Any = None,
    ) -> None:
        self._get_feed = get_feed
        self._feeds = feeds
        self._execution_capability = execution_capability
        # Optional wire client hook: ``None`` keeps the direct feed semantics
        # byte-unchanged; a client (assembled only from the gateway wire config)
        # routes typed writes over the gateway command wire instead.
        self._client = client

    def _feed(self, exchange_name: str) -> Any:
        return self._get_feed(exchange_name)

    def _execution_options(self, exchange_name: str) -> dict[str, Any]:
        if str(exchange_name).partition("___")[0].upper() != "CTP":
            return {}
        capability = self._execution_capability
        capability = capability() if callable(capability) else capability
        return {"_execution_capability": capability} if capability is not None else {}

    def _wire_write(self, operation: str, exchange_name: str, request: Any) -> dict[str, Any]:
        """Route one typed write over the attached gateway wire client.

        The wire reaction mirrors the retired forwarding client: build the
        intent, send it, normalize the acknowledgement.  A dispatch failure is
        never a rejection by itself: the router status probe (when the
        transport exposes one) decides unknown vs definite, otherwise the
        outcome stays unknown and must be reconciled, never blind-retried.
        """
        client = self._client
        command = build_intent(
            operation,
            exchange_name,
            request,
            strategy_id=str(getattr(client, "strategy_id", "") or ""),
        )
        try:
            ack = client._send_command_sync(command)
        except Exception:
            status_row = None
            probe = getattr(client, "command_status", None)
            if callable(probe):
                try:
                    status_row = probe(command.command_id)
                except Exception:
                    status_row = None
            return outcome_from_dispatch_status(status_row, command=command)
        return outcome_from_ack(ack, command=command)

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
        method = self._read_method(exchange_name, "get_account_config", "get_config")
        return method(extra_data=extra_data, **kwargs)

    def get_position_mode(
        self, exchange_name: str, *, extra_data: Any = None, **kwargs: Any
    ) -> Any:
        aliases = ("get_config",) if exchange_name.startswith("OKX___") else ()
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
        feed = self._feed(exchange_name)
        if exchange_name == "OKX___SWAP":
            method = getattr(feed, "set_position_mode", None)
            if not callable(method):
                method = getattr(feed, "set_mode", None)
            native_mode: Any = {
                "net": "net_mode",
                "dual_side": "long_short_mode",
            }[position_mode]
        elif exchange_name == "BINANCE___SWAP":
            method = getattr(feed, "change_position_mode", None)
            native_mode = position_mode == "dual_side"
        else:
            raise CapabilityNotSupportedError(
                "set_position_mode",
                detail=f"{exchange_name} has no normalized position-mode mutation",
                definite_reject=True,
            )
        if not callable(method):
            raise CapabilityNotSupportedError(
                "set_position_mode",
                detail=f"transport=direct; {exchange_name} does not implement this operation",
                definite_reject=True,
            )
        return method(native_mode, extra_data=extra_data, **kwargs)

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
        provider = str(exchange_name).partition("___")[0].upper()
        if (
            request.quantity_unit == "contracts"
            and exchange_name
            not in {
                "OKX___SWAP",
            }
            and provider != "CTP"
        ):
            raise CapabilityNotSupportedError(
                "make_order",
                detail="explicit contract units require a contract-based venue",
                definite_reject=True,
            )
        if request.quantity_unit == "lots" and provider != "CTP":
            raise CapabilityNotSupportedError(
                "make_order",
                detail="lots require a supported lot-based backend",
                definite_reject=True,
            )

    def make_order(self, exchange_name: str, request: OrderRequest) -> Any:
        if self._client is not None:
            return self._wire_write("make_order", exchange_name, request)
        self._validate_order_units(exchange_name, request)
        feed = self._feed(exchange_name)
        from bt_api_py._feed_adapter import FeedAdapter
        from bt_api_py._venue_mappers import get_venue_mapper

        mapper = get_venue_mapper(exchange_name)
        if mapper is not None:
            return FeedAdapter(
                feed,
                mapper,
                execution_capability=self._execution_options(exchange_name).get(
                    "_execution_capability"
                ),
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
            **self._execution_options(exchange_name),
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
                execution_capability=self._execution_options(exchange_name).get(
                    "_execution_capability"
                ),
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
            **self._execution_options(exchange_name),
        )

    def cancel_order(
        self,
        exchange_name: str,
        request: CancelOrderRequest,
        *,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        if self._client is not None:
            return self._wire_write("cancel_order", exchange_name, request)
        order_id = request.order_id or request.client_order_id
        if str(exchange_name).partition("___")[0].upper() == "CTP":
            order_id = request.order_id
            for key in ("exchange_id", "front_id", "session_id", "order_ref"):
                value = getattr(request, key, None)
                if value is not None:
                    kwargs[key] = value
            if request.runtime_order_id is not None:
                kwargs["runtime_order_id"] = request.runtime_order_id
            if request.runtime_action_id is not None:
                kwargs["runtime_action_id"] = request.runtime_action_id
            if request.managed_cancel_intent_id is not None:
                kwargs["managed_cancel_intent_id"] = request.managed_cancel_intent_id
            if order_id is None:
                kwargs.setdefault("order_ref", request.order_ref or request.client_order_id)
            kwargs.update(self._execution_options(exchange_name))
        if exchange_name.split("___")[0] in {"OKX", "BINANCE"}:
            order_id = request.order_id
            if order_id is None:
                kwargs["client_order_id"] = request.client_order_id
        return self._feed(exchange_name).cancel_order(
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
        if str(exchange_name).partition("___")[0].upper() == "CTP":
            order_id = request.order_id
            for key in ("exchange_id", "front_id", "session_id", "order_ref"):
                value = getattr(request, key, None)
                if value is not None:
                    kwargs[key] = value
            if request.runtime_order_id is not None:
                kwargs["runtime_order_id"] = request.runtime_order_id
            if request.runtime_action_id is not None:
                kwargs["runtime_action_id"] = request.runtime_action_id
            if request.managed_cancel_intent_id is not None:
                kwargs["managed_cancel_intent_id"] = request.managed_cancel_intent_id
            if order_id is None:
                kwargs.setdefault("order_ref", request.order_ref or request.client_order_id)
            kwargs.update(self._execution_options(exchange_name))
        if exchange_name.split("___")[0] in {"OKX", "BINANCE"}:
            order_id = request.order_id
            if order_id is None:
                kwargs["client_order_id"] = request.client_order_id
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
        if str(exchange_name).partition("___")[0].upper() == "CTP":
            order_id = request.order_id
            for key in ("exchange_id", "front_id", "session_id", "order_ref"):
                value = getattr(request, key, None)
                if value is not None:
                    kwargs[key] = value
            if order_id is None:
                kwargs.setdefault("order_ref", request.order_ref or request.client_order_id)
        if exchange_name.split("___")[0] in {"OKX", "BINANCE"}:
            order_id = request.order_id
            if order_id is None:
                kwargs["client_order_id"] = request.client_order_id
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
        if str(exchange_name).partition("___")[0].upper() == "CTP":
            order_id = request.order_id
            for key in ("exchange_id", "front_id", "session_id", "order_ref"):
                value = getattr(request, key, None)
                if value is not None:
                    kwargs[key] = value
            if order_id is None:
                kwargs.setdefault("order_ref", request.order_ref or request.client_order_id)
        if exchange_name.split("___")[0] in {"OKX", "BINANCE"}:
            order_id = request.order_id
            if order_id is None:
                kwargs["client_order_id"] = request.client_order_id
        return await method(request.symbol, order_id, extra_data=extra_data, **kwargs)

    def poll_event(self, exchange_name: str) -> Any:
        """Poll one private/broker message from the wire client.

        Only meaningful for a wire-configured session; a plain direct backend
        has no event ingress and reports the capability gap.
        """
        del exchange_name
        client = self._client
        if client is None:
            raise CapabilityNotSupportedError(
                "poll_event",
                detail="transport=direct has no event ingress",
            )
        updater = getattr(client, "poll_broker_update", None)
        if not callable(updater):
            return None
        return updater()

    def close(self) -> None:
        """Disconnect the attached wire client; a plain direct backend is inert."""
        client = self._client
        if client is None:
            return
        self._client = None
        disconnect = getattr(client, "disconnect", None)
        if callable(disconnect):
            disconnect()

    def get_command_status(self, exchange_name: str, command_id: str) -> Any:
        del exchange_name, command_id
        raise CapabilityNotSupportedError(
            "get_command_status",
            detail="transport=direct has no forwarding command receipt store",
        )
