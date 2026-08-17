"""Module-level docstring."""
from __future__ import annotations

import uuid
from collections import defaultdict, deque
from math import isfinite
from types import SimpleNamespace
from typing import Any, NoReturn, TypeVar

from bt_api_py.forwarding.memory import InMemoryForwardingBus, MarketSubscription
from bt_api_py.forwarding.schema import (
    MarketEvent,
    OrderCommand,
    PrivateEvent,
    normalize_market_symbol,
)
from bt_api_py.forwarding.transport import (
    ZmqCommandClient,
    ZmqEventSubscriber,
    ZmqMarketSubscriber,
)

_EVENT_CACHE_KINDS = ("tick", "orderbook", "bar", "broker_update")
_CachedEvent = TypeVar("_CachedEvent")


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
        self._account_cache: dict[str, Any] = {"cash": 0.0, "value": 0.0}
        self._positions_cache: list[dict[str, Any]] = []
        self._orders_cache: list[dict[str, Any]] = []

    def connect(self) -> bool:
        """connect method"""
        if self.connected:
            return True
        self.connected = True
        self._private_subscriptions.append(
            self.bus.subscribe_private(f"acct.{self.account_id}.", replay=self.replay)
        )
        self._private_subscriptions.append(
            self.bus.subscribe_private(f"strategy.{self.strategy_id}.", replay=self.replay)
        )
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

    def get_balance(self) -> dict[str, Any]:
        """get_balance method"""
        self._drain_private()
        command = OrderCommand(
            command_type="get_account",
            strategy_id=self.strategy_id,
            account_id=self.account_id,
        )
        try:
            ack = self._send_command_sync(command)
        except (RuntimeError, TimeoutError):
            return dict(self._account_cache)
        if ack.accepted:
            self._account_cache = {
                "cash": ack.payload.get("available_cash", ack.payload.get("cash", 0.0)),
                "value": ack.payload.get("equity", ack.payload.get("value", 0.0)),
                **ack.payload,
            }
        return dict(self._account_cache)

    get_account = get_balance

    def get_positions(self) -> list[dict[str, Any]]:
        """get_positions method"""
        self._drain_private()
        command = OrderCommand(
            command_type="list_positions",
            strategy_id=self.strategy_id,
            account_id=self.account_id,
        )
        try:
            ack = self._send_command_sync(command)
        except (RuntimeError, TimeoutError):
            return list(self._positions_cache)
        if ack.accepted:
            self._positions_cache = list(ack.payload.get("positions", []))
        return list(self._positions_cache)

    def fetch_open_orders(self) -> list[dict[str, Any]]:
        """fetch_open_orders method"""
        self._drain_private()
        command = OrderCommand(
            command_type="list_orders",
            strategy_id=self.strategy_id,
            account_id=self.account_id,
        )
        try:
            ack = self._send_command_sync(command)
        except (RuntimeError, TimeoutError):
            return list(self._orders_cache)
        if ack.accepted:
            self._orders_cache = [
                order
                for order in ack.payload.get("orders", [])
                if order.get("status") == "submitted"
            ]
        return list(self._orders_cache)

    get_open_orders = fetch_open_orders

    def submit_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        """submit_order method"""
        command = self._payload_to_order_command(payload)
        ack = self._send_command_sync(command)
        self._drain_private()
        if not ack.accepted:
            raise RuntimeError(ack.reason or "forwarded order rejected")
        result = dict(ack.payload)
        result.setdefault("id", ack.order_id)
        result.setdefault("order_id", ack.order_id)
        result.setdefault("order_ref", payload.get("bt_order_ref") or command.client_order_id)
        return result

    create_order = submit_order

    def cancel_order(self, order_ref: Any, dataname: str | None = None) -> dict[str, Any]:
        """cancel_order method"""
        command = OrderCommand(
            command_type="cancel_order",
            strategy_id=self.strategy_id,
            account_id=self.account_id,
            symbol=str(dataname or ""),
            order_id=str(order_ref or ""),
            idempotency_key=f"cancel:{self.strategy_id}:{self.account_id}:{order_ref}",
        )
        ack = self._send_command_sync(command)
        self._drain_private()
        if not ack.accepted:
            raise RuntimeError(ack.reason or "forwarded cancel rejected")
        result = dict(ack.payload)
        result.setdefault("id", ack.order_id)
        result.setdefault("order_id", ack.order_id)
        result.setdefault("order_ref", order_ref)
        return result

    def poll_broker_update(self) -> dict[str, Any] | None:
        """poll_broker_update method"""
        self._drain_private()
        if not self._broker_updates:
            return None
        return self._broker_updates.popleft()

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
            client_order_id=str(payload.get("client_order_id") or bt_ref or idempotency_key),
            idempotency_key=str(idempotency_key),
            extra={key: value for key, value in payload.items() if key not in _ORDER_COMMAND_KEYS},
        )

    def _send_command_sync(self, command: OrderCommand):
        return self.bus.send_command_sync(command, timeout=self.command_timeout)

    def _refresh_event_caches(self) -> None:
        for symbol_key in list(self._market_subscriptions):
            self._drain_market(symbol_key)
        self._drain_private()

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
        for subscription in list(self._private_subscriptions):
            while True:
                event = subscription.poll()
                if event is None:
                    break
                self._cache_private_event(event)

    def _cache_private_event(self, event: PrivateEvent) -> None:
        payload = dict(event.payload or {})
        kind = str(payload.get("kind") or event.event_type).lower()
        if kind == "account":
            self._account_cache = {
                "cash": payload.get("available_cash", payload.get("cash", 0.0)),
                "value": payload.get("equity", payload.get("value", 0.0)),
                **payload,
            }
        elif kind == "position":
            self._positions_cache = [
                item
                for item in self._positions_cache
                if item.get("symbol") != payload.get("symbol")
            ]
            self._positions_cache.append(payload)
        elif kind in {"order", "trade", "error"}:
            self._append_cached_event(self._broker_updates, payload, "broker_update")

    def _clear_event_caches(self) -> None:
        self._ticks.clear()
        self._orderbooks.clear()
        self._bars.clear()
        self._broker_updates.clear()

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


def _market_event_to_tick(event: MarketEvent) -> Any:
    payload = dict(event.payload or {})
    timestamp = float(payload.get("timestamp") or event.event_time / 1000.0)
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
    timestamp = float(payload.get("timestamp") or event.event_time / 1000.0)
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
    timestamp = float(payload.get("timestamp") or event.event_time / 1000.0)
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
}


class ZmqForwardingClient(ForwardingClient):
    """Forwarding client that talks to a standalone ZeroMQ forwarding service."""

    def __init__(
        self,
        *,
        market_endpoint: str,
        command_endpoint: str,
        private_endpoint: str | None = None,
        exchange: str = "SIM",
        market_type: str = "SPOT",
        account_id: str = "paper",
        strategy_id: str = "default",
        command_timeout_ms: int = 2000,
        event_cache_size: int | None = 4096,
    ) -> None:
        """__init__ method"""
        super().__init__(
            bus=InMemoryForwardingBus(),
            exchange=exchange,
            market_type=market_type,
            account_id=account_id,
            strategy_id=strategy_id,
            event_cache_size=event_cache_size,
        )
        self.market_endpoint = market_endpoint
        self.command_endpoint = command_endpoint
        self.private_endpoint = private_endpoint
        self.command_timeout_ms = _normalize_command_timeout_ms(command_timeout_ms)
        self._command_client: ZmqCommandClient | None = None
        self._market_subscriptions: dict[str, ZmqMarketSubscriber] = {}
        self._private_subscriptions: list[ZmqEventSubscriber] = []

    def connect(self) -> bool:
        """connect method"""
        if self.connected:
            return True
        self._command_client = ZmqCommandClient(self.command_endpoint)
        if self.private_endpoint:
            self._private_subscriptions.append(
                ZmqEventSubscriber(self.private_endpoint, f"acct.{self.account_id}.")
            )
            self._private_subscriptions.append(
                ZmqEventSubscriber(self.private_endpoint, f"strategy.{self.strategy_id}.")
            )
        self.connected = True
        return True

    start = connect

    def disconnect(self) -> bool:
        """disconnect method"""
        for subscription in list(self._market_subscriptions.values()):
            subscription.close()
        for subscription in list(self._private_subscriptions):
            subscription.close()
        if self._command_client is not None:
            self._command_client.close()
        self._market_subscriptions.clear()
        self._private_subscriptions.clear()
        self._command_client = None
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
            self._market_subscriptions[symbol_key] = ZmqMarketSubscriber(
                self.market_endpoint, prefix
            )

    def _drain_market(self, symbol: str) -> None:
        symbol_key = normalize_market_symbol(symbol)
        if symbol_key not in self._market_subscriptions:
            self.subscribe(symbol_key)
        subscription = self._market_subscriptions[symbol_key]
        while True:
            event = subscription.poll(0)
            if event is None:
                break
            self._cache_market_event(event)

    def _drain_private(self) -> None:
        for subscription in list(self._private_subscriptions):
            while True:
                event = subscription.poll(0)
                if event is None:
                    break
                if isinstance(event, PrivateEvent):
                    self._cache_private_event(event)

    def _send_command_sync(self, command: OrderCommand):
        if self._command_client is None:
            self.connect()
        if self._command_client is None:
            raise RuntimeError("ZMQ command client is not connected")
        return self._command_client.send(command, timeout_ms=self.command_timeout_ms)


def _require(payload: dict, key: str) -> NoReturn:
    """Raise when a required order payload field is missing."""
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
