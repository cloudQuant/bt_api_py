"""Module-level docstring."""

from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque
from math import isfinite
from types import SimpleNamespace
from typing import Any, NoReturn, TypeVar

from bt_api_py._contracts.errors import LegacyForwardingRetiredError
from bt_api_py._contracts.models import CommandStatus
from bt_api_py.forwarding.memory import InMemoryForwardingBus, MarketSubscription
from bt_api_py.forwarding.schema import (
    MarketEvent,
    OrderCommand,
    PrivateEvent,
    normalize_market_symbol,
)
_EVENT_CACHE_KINDS = ("tick", "orderbook", "bar", "broker_update")
_CachedEvent = TypeVar("_CachedEvent")
_MIGRATION_DETAIL = (
    "the legacy forwarding command channel was retired; ordinary backtests use BtApi with the "
    "direct transport, managed account reads use ServiceClientRuntime with ServiceHost and "
    "service.account_read, and managed writes use Backtrader ServiceExecutionSession with "
    "ServiceHost and service.order_entry"
)


def _raise_retired_command(target: str) -> NoReturn:
    raise LegacyForwardingRetiredError(target, detail=_MIGRATION_DETAIL)


class ForwardingClient:
    """Synchronous client facade used by strategy frameworks such as backtrader."""

    def __init__(
        self,
        *,
        bus: InMemoryForwardingBus | None = None,
        exchange: str = "SIM",
        market_type: str = "SPOT",
        account_id: str = "paper",
        strategy_id: str = "default",
        replay: int = 0,
        command_timeout: float | None = 2.0,
        event_cache_size: int | None = 4096,
    ) -> None:
        """__init__ method"""
        self.bus = bus or InMemoryForwardingBus()
        self.exchange = exchange
        self.market_type = market_type
        self.account_id = account_id
        self.strategy_id = strategy_id
        self.replay = _normalize_non_negative_int(replay, "replay")
        self.command_timeout = _normalize_command_timeout(command_timeout)
        self.event_cache_size = _normalize_optional_non_negative_int(
            event_cache_size, "event_cache_size"
        )
        self.connected = False
        self._market_subscriptions: dict[str, MarketSubscription] = {}
        self._private_subscriptions: list[MarketSubscription] = []
        self._ticks: dict[str, deque[Any]] = defaultdict(self._new_any_event_queue)
        self._orderbooks: dict[str, deque[Any]] = defaultdict(self._new_any_event_queue)
        self._bars: dict[str, deque[dict[str, Any]]] = defaultdict(self._new_mapping_event_queue)
        self._broker_updates: deque[dict[str, Any]] = self._new_mapping_event_queue()
        self._dropped_event_counts: dict[str, int] = dict.fromkeys(_EVENT_CACHE_KINDS, 0)
        self._account_cache: dict[str, Any] | None = None
        self._positions_cache: list[dict[str, Any]] | None = None
        self._orders_cache: list[dict[str, Any]] | None = None
        self._deals_cache: list[dict[str, Any]] | None = None
        self._latest_market_events: dict[tuple[str, str], MarketEvent] = {}
        self._latest_account_event: PrivateEvent | None = None
        self._latest_position_events: dict[str, PrivateEvent] = {}
        self._latest_order_events: dict[str, PrivateEvent] = {}
        self._latest_fill_events: dict[str, PrivateEvent] = {}

    def connect(self) -> bool:
        """connect method"""
        if self.connected:
            return True
        self.connected = True
        # The retired forwarding command/query channel has no private-event
        # consumer.  Market reads remain available through this client.
        return True

    start = connect

    def disconnect(self) -> bool:
        """disconnect method"""
        for subscription in list(self._market_subscriptions.values()):
            subscription.close()
        for subscription in list(self._private_subscriptions):
            subscription.close()
        self._market_subscriptions.clear()
        self._private_subscriptions.clear()
        self._clear_event_caches()
        self.connected = False
        return True

    stop = disconnect

    def subscribe(self, symbols: str | list[str]) -> None:
        """subscribe method"""
        if isinstance(symbols, str):
            symbols = [symbols]
        if not self.connected:
            self.connect()
        for symbol in symbols:
            symbol_key = normalize_market_symbol(symbol)
            if symbol_key in self._market_subscriptions:
                continue
            prefix = f"md.{self.exchange.upper()}.{self.market_type.upper()}.{symbol_key}."
            self._market_subscriptions[symbol_key] = self.bus.subscribe_market(
                prefix, replay=self.replay
            )

    def poll_tick(self, symbol: str) -> Any | None:
        """poll_tick method"""
        symbol_key = normalize_market_symbol(symbol)
        self._drain_market(symbol_key)
        queue = self._ticks.get(symbol_key)
        if not queue:
            return None
        return queue.popleft()

    get_next_tick = poll_tick

    def poll_orderbook(self, symbol: str) -> Any | None:
        """poll_orderbook method"""
        symbol_key = normalize_market_symbol(symbol)
        self._drain_market(symbol_key)
        queue = self._orderbooks.get(symbol_key)
        if not queue:
            return None
        return queue.popleft()

    get_next_orderbook = poll_orderbook

    def poll_bar(self, symbol: str) -> dict[str, Any] | None:
        """poll_bar method"""
        symbol_key = normalize_market_symbol(symbol)
        self._drain_market(symbol_key)
        queue = self._bars.get(symbol_key)
        if not queue:
            return None
        return queue.popleft()

    get_next_bar = poll_bar

    def peek_market_event(self, symbol: str, event_type: str) -> MarketEvent | None:
        """Return the latest matching event without removing poll queue items."""
        symbol_key = normalize_market_symbol(symbol)
        self._drain_market(symbol_key)
        return self._latest_market_events.get((symbol_key, str(event_type).lower()))

    def peek_tick_event(self, symbol: str) -> MarketEvent | None:
        return self.peek_market_event(symbol, "tick")

    def peek_orderbook_event(self, symbol: str) -> MarketEvent | None:
        return self.peek_market_event(symbol, "orderbook")

    def peek_bar_event(self, symbol: str) -> MarketEvent | None:
        return self.peek_market_event(symbol, "bar")

    def wait_for_next_market_event(
        self,
        symbol: str,
        event_type: str,
        *,
        after_sequence_id: int = 0,
        timeout_ms: int = 0,
    ) -> MarketEvent | None:
        """Wait only for an event newer than the caller's observed sequence.

        This is intentionally a bounded cache wait, not a fabricated request/
        response market-data API.  Poll queues remain untouched.
        """
        timeout_ms = _normalize_command_timeout_ms(timeout_ms)
        deadline = time.monotonic() + timeout_ms / 1000.0
        while True:
            event = self.peek_market_event(symbol, event_type)
            if event is not None and event.sequence_id > after_sequence_id:
                return event
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            time.sleep(min(0.005, remaining))

    def has_pending_tick(self, symbol: str) -> bool:
        """has_pending_tick method"""
        symbol_key = normalize_market_symbol(symbol)
        self._drain_market(symbol_key)
        return bool(self._ticks.get(symbol_key))

    def has_pending_orderbook(self, symbol: str) -> bool:
        """has_pending_orderbook method"""
        symbol_key = normalize_market_symbol(symbol)
        self._drain_market(symbol_key)
        return bool(self._orderbooks.get(symbol_key))

    def supports_live_ticks(self, symbol: str) -> bool:
        """supports_live_ticks method"""
        return normalize_market_symbol(symbol) in self._market_subscriptions

    def supports_live_orderbook(self, symbol: str) -> bool:
        """supports_live_orderbook method"""
        return normalize_market_symbol(symbol) in self._market_subscriptions

    def supports_live_streaming(self, _symbol: str | None = None) -> bool:
        """supports_live_streaming method"""
        return True

    def stats(self, *, refresh: bool = True) -> dict[str, Any]:
        """stats method"""
        if refresh:
            self._refresh_event_caches()
        return {
            "connected": self.connected,
            "event_cache_size": self.event_cache_size,
            "market_subscription_count": len(self._market_subscriptions),
            "private_subscription_count": len(self._private_subscriptions),
            "pending_event_counts": {
                "tick": sum(len(queue) for queue in self._ticks.values()),
                "orderbook": sum(len(queue) for queue in self._orderbooks.values()),
                "bar": sum(len(queue) for queue in self._bars.values()),
                "broker_update": len(self._broker_updates),
            },
            "dropped_event_counts": dict(self._dropped_event_counts),
        }

    def get_balance(self, *, allow_cached_failure: bool = True) -> dict[str, Any]:
        """Retired account snapshot; use ServiceClientRuntime account reads."""
        del allow_cached_failure
        _raise_retired_command("bt_api_py.forwarding.client.ForwardingClient.get_balance")

    get_account = get_balance

    def get_positions(self, *, allow_cached_failure: bool = True) -> list[dict[str, Any]]:
        """Retired account snapshot; use ServiceClientRuntime account reads."""
        del allow_cached_failure
        _raise_retired_command("bt_api_py.forwarding.client.ForwardingClient.get_positions")

    def fetch_open_orders(self, *, allow_cached_failure: bool = True) -> list[dict[str, Any]]:
        """Retired account snapshot; use ServiceClientRuntime account reads."""
        del allow_cached_failure
        _raise_retired_command(
            "bt_api_py.forwarding.client.ForwardingClient.fetch_open_orders"
        )

    get_open_orders = fetch_open_orders

    def submit_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Retired write path; use ServiceExecutionSession for managed orders."""
        del payload
        _raise_retired_command("bt_api_py.forwarding.client.ForwardingClient.submit_order")

    create_order = submit_order

    def cancel_order(self, order_ref: Any, dataname: str | None = None) -> dict[str, Any]:
        """Retired write path; use ServiceExecutionSession for managed orders."""
        del order_ref, dataname
        _raise_retired_command("bt_api_py.forwarding.client.ForwardingClient.cancel_order")

    def cancel_all(
        self, symbol: str | None = None, *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """Retired write path; use ServiceExecutionSession for managed orders."""
        del symbol, idempotency_key
        _raise_retired_command("bt_api_py.forwarding.client.ForwardingClient.cancel_all")

    def query_order(self, order_ref: Any, dataname: str | None = None) -> dict[str, Any]:
        del order_ref, dataname
        _raise_retired_command("bt_api_py.forwarding.client.ForwardingClient.query_order")

    def get_deals(self, *, allow_cached_failure: bool = True) -> list[dict[str, Any]]:
        """Retired fill snapshot; use ServiceClientRuntime account reads."""
        del allow_cached_failure
        _raise_retired_command("bt_api_py.forwarding.client.ForwardingClient.get_deals")

    def get_command_status(self, command_id: str) -> CommandStatus:
        del command_id
        _raise_retired_command(
            "bt_api_py.forwarding.client.ForwardingClient.get_command_status"
        )

    def latest_account_event(self) -> PrivateEvent | None:
        _raise_retired_command(
            "bt_api_py.forwarding.client.ForwardingClient.latest_account_event"
        )

    def latest_position_events(self) -> list[PrivateEvent]:
        _raise_retired_command(
            "bt_api_py.forwarding.client.ForwardingClient.latest_position_events"
        )

    def latest_order_events(self) -> list[PrivateEvent]:
        _raise_retired_command(
            "bt_api_py.forwarding.client.ForwardingClient.latest_order_events"
        )

    def latest_fill_events(self) -> list[PrivateEvent]:
        _raise_retired_command(
            "bt_api_py.forwarding.client.ForwardingClient.latest_fill_events"
        )

    def poll_broker_update(self) -> dict[str, Any] | None:
        """Retired private-event read; use the service account-read path."""
        _raise_retired_command(
            "bt_api_py.forwarding.client.ForwardingClient.poll_broker_update"
        )

    def _payload_to_order_command(self, payload: dict[str, Any]) -> OrderCommand:
        symbol = str(payload.get("symbol") or payload.get("data_name") or "")
        bt_ref = payload.get("bt_order_ref")
        idempotency_key = payload.get("idempotency_key")
        if not idempotency_key:
            idempotency_key = f"{self.strategy_id}:{self.account_id}:{bt_ref or uuid.uuid4()}"
        return OrderCommand(
            strategy_id=str(payload.get("strategy_id") or self.strategy_id),
            account_id=str(payload.get("account_id") or self.account_id),
            exchange=str(payload.get("exchange") or self.exchange),
            market_type=str(payload.get("market_type") or self.market_type),
            symbol=symbol,
            side=str(payload.get("side") or _require(payload, "side")),
            size=float(payload.get("size") or payload.get("quantity") or 0.0),
            order_type=str(payload.get("order_type") or _require(payload, "order_type")),
            price=payload.get("price"),
            time_in_force=str(payload.get("time_in_force") or "GTC"),
            reduce_only=bool(payload.get("reduce_only", False)),
            client_order_id=str(payload.get("client_order_id") or bt_ref or idempotency_key),
            idempotency_key=str(idempotency_key),
            extra={key: value for key, value in payload.items() if key not in _ORDER_COMMAND_KEYS},
        )

    def _send_command_sync(self, command: OrderCommand):
        del command
        _raise_retired_command(
            "bt_api_py.forwarding.client.ForwardingClient._send_command_sync"
        )

    def pending_commands(self) -> list[str]:
        """Return no pending commands because the retired channel cannot dispatch one."""
        return []

    def _refresh_event_caches(self) -> None:
        for symbol_key in list(self._market_subscriptions):
            self._drain_market(symbol_key)

    def _drain_market(self, symbol: str) -> None:
        symbol_key = normalize_market_symbol(symbol)
        if symbol_key not in self._market_subscriptions:
            self.subscribe(symbol_key)
        subscription = self._market_subscriptions[symbol_key]
        while True:
            event = subscription.poll()
            if event is None:
                break
            self._cache_market_event(event)

    def _cache_market_event(self, event: MarketEvent) -> None:
        symbol = normalize_market_symbol(event.symbol)
        if (
            str(event.exchange).upper() != self.exchange.upper()
            or str(event.market_type).upper() != self.market_type.upper()
        ):
            return
        self._latest_market_events[(symbol, str(event.event_type).lower())] = event
        if event.event_type == "tick":
            self._append_cached_event(self._ticks[symbol], _market_event_to_tick(event), "tick")
        elif event.event_type == "orderbook":
            self._append_cached_event(
                self._orderbooks[symbol],
                _market_event_to_orderbook(event),
                "orderbook",
            )
        elif event.event_type == "bar":
            self._append_cached_event(self._bars[symbol], _market_event_to_bar(event), "bar")

    def _drain_private(self) -> None:
        _raise_retired_command(
            "bt_api_py.forwarding.client.ForwardingClient._drain_private"
        )

    def _cache_private_event(self, event: PrivateEvent) -> None:
        if event.account_id != self.account_id or (
            event.strategy_id and event.strategy_id != self.strategy_id
        ):
            return
        payload = dict(event.payload or {})
        kind = str(payload.get("kind") or event.event_type).lower()
        if kind == "account":
            self._latest_account_event = event
            self._account_cache = {
                "cash": payload.get("available_cash", payload.get("cash", 0.0)),
                "value": payload.get("equity", payload.get("value", 0.0)),
                **payload,
            }
        elif kind == "position":
            self._latest_position_events[str(payload.get("symbol") or "")] = event
            self._positions_cache = [
                item
                for item in (self._positions_cache or [])
                if item.get("symbol") != payload.get("symbol")
            ]
            self._positions_cache.append(payload)
        elif kind == "order":
            key = str(
                payload.get("order_id") or payload.get("external_order_id") or event.sequence_id
            )
            self._latest_order_events[key] = event
            self._append_cached_event(self._broker_updates, payload, "broker_update")
        elif kind == "trade":
            key = str(payload.get("trade_id") or event.sequence_id)
            self._latest_fill_events[key] = event
            self._deals_cache = [dict(item.payload) for item in self._latest_fill_events.values()]
            self._append_cached_event(self._broker_updates, payload, "broker_update")
        elif kind == "error":
            self._append_cached_event(self._broker_updates, payload, "broker_update")

    def _clear_event_caches(self) -> None:
        self._ticks.clear()
        self._orderbooks.clear()
        self._bars.clear()
        self._broker_updates.clear()
        self._latest_market_events.clear()
        self._latest_account_event = None
        self._latest_position_events.clear()
        self._latest_order_events.clear()
        self._latest_fill_events.clear()
        self._deals_cache = None

    def _new_any_event_queue(self) -> deque[Any]:
        return deque(maxlen=self.event_cache_size)

    def _new_mapping_event_queue(self) -> deque[dict[str, Any]]:
        return deque(maxlen=self.event_cache_size)

    def _append_cached_event(
        self,
        queue: deque[_CachedEvent],
        item: _CachedEvent,
        event_kind: str,
    ) -> None:
        if queue.maxlen is not None and len(queue) >= queue.maxlen:
            self._dropped_event_counts[event_kind] += 1
        queue.append(item)


_MS_TIMESTAMP_THRESHOLD = 1e11


def _timestamp_ms(payload: dict[str, Any], event_time_ms: int) -> int:
    """统一把事件时间戳归一为毫秒 int。

    `event_time` 语义为毫秒；`payload["timestamp"]` 可能由调用方传入秒或毫秒。
    小于阈值的数值视为秒级，换算为毫秒。
    """
    raw = payload.get("timestamp")
    if raw is None:
        return int(event_time_ms)
    value = float(raw)
    if value < _MS_TIMESTAMP_THRESHOLD:
        return int(value * 1000)
    return int(value)


def _market_event_to_tick(event: MarketEvent) -> Any:
    payload = dict(event.payload or {})
    timestamp = _timestamp_ms(payload, event.event_time)
    return SimpleNamespace(
        timestamp=timestamp,
        symbol=event.symbol,
        exchange=event.exchange,
        asset_type=event.market_type.lower(),
        price=float(payload.get("price", payload.get("last_price", 0.0)) or 0.0),
        volume=float(payload.get("volume", payload.get("size", 0.0)) or 0.0),
        direction=str(payload.get("direction", "buy")),
        trade_id=str(payload.get("trade_id", "")),
        bid_price=payload.get("bid_price"),
        ask_price=payload.get("ask_price"),
        bid_volume=payload.get("bid_volume"),
        ask_volume=payload.get("ask_volume"),
        local_time=float(payload.get("local_time") or event.receive_time / 1000.0),
    )


def _market_event_to_orderbook(event: MarketEvent) -> Any:
    payload = dict(event.payload or {})
    timestamp = _timestamp_ms(payload, event.event_time)
    return SimpleNamespace(
        timestamp=timestamp,
        symbol=event.symbol,
        exchange=event.exchange,
        asset_type=event.market_type.lower(),
        bids=list(payload.get("bids") or []),
        asks=list(payload.get("asks") or []),
        local_time=float(payload.get("local_time") or event.receive_time / 1000.0),
    )


def _market_event_to_bar(event: MarketEvent) -> dict[str, Any]:
    payload = dict(event.payload or {})
    timestamp = _timestamp_ms(payload, event.event_time)
    return {
        "datetime": payload.get("datetime") or timestamp,
        "open": float(payload.get("open", payload.get("price", 0.0)) or 0.0),
        "high": float(payload.get("high", payload.get("price", 0.0)) or 0.0),
        "low": float(payload.get("low", payload.get("price", 0.0)) or 0.0),
        "close": float(payload.get("close", payload.get("price", 0.0)) or 0.0),
        "volume": float(payload.get("volume", 0.0) or 0.0),
        "openinterest": float(payload.get("openinterest", 0.0) or 0.0),
    }


_ORDER_COMMAND_KEYS = {
    "account_id",
    "bt_order_ref",
    "client_order_id",
    "data_name",
    "exchange",
    "idempotency_key",
    "market_type",
    "order_type",
    "price",
    "quantity",
    "side",
    "size",
    "strategy_id",
    "symbol",
    "time_in_force",
    "reduce_only",
}


class ZmqForwardingClient:
    """Retired network client for the v1 forwarding runtime."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise LegacyForwardingRetiredError(
            "bt_api_py.forwarding.client.ZmqForwardingClient",
            detail=(
                "the legacy ZMQ forwarding client was retired; use ServiceClientRuntime "
                "with ServiceHost ports for managed service calls, and use Backtrader "
                "ServiceExecutionSession with service.order_entry for managed writes"
            ),
        )


def _require(payload: dict, key: str) -> NoReturn:
    """Reject missing required order fields during passive DTO conversion."""
    del payload
    raise ValueError(f"{key} is required; refusing to default to a marketable value")


def _normalize_command_timeout(timeout: float | None) -> float | None:
    if timeout is None:
        return None
    timeout = float(timeout)
    if timeout < 0 or not isfinite(timeout):
        raise ValueError("command_timeout must be a non-negative finite number")
    return timeout


def _normalize_command_timeout_ms(timeout_ms: int) -> int:
    timeout_ms = int(timeout_ms)
    if timeout_ms < 0:
        raise ValueError("command_timeout_ms must be non-negative")
    return timeout_ms


def _normalize_non_negative_int(value: int, name: str) -> int:
    value = int(value)
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


def _normalize_optional_non_negative_int(value: int | None, name: str) -> int | None:
    if value is None:
        return None
    return _normalize_non_negative_int(value, name)
