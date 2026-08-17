"""Module-level docstring."""
from __future__ import annotations

import asyncio
import inspect
import math
import threading
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from typing import Any, Union

from bt_api_py.forwarding.schema import CommandAck, MarketEvent, OrderCommand, PrivateEvent


class MarketSubscription:
    """In-memory subscription queue used by tests and local embedded clients."""

    def __init__(
        self, bus: InMemoryForwardingBus, topic_prefix: str, private: bool = False
    ) -> None:
        """__init__ method"""
        self.bus = bus
        self.topic_prefix = topic_prefix
        self.private = private
        self.queue: deque[Any] = deque()
        self.closed = False

    def matches(self, topic: str) -> bool:
        """matches method"""
        return topic.startswith(self.topic_prefix)

    def put(self, event: Any) -> None:
        """put method"""
        if not self.closed:
            self.queue.append(event)

    def poll(self) -> Any | None:
        """poll method"""
        if not self.queue:
            return None
        return self.queue.popleft()

    def close(self) -> None:
        """close method"""
        if self.closed:
            return
        self.closed = True
        self.bus._remove_subscription(self)


CommandHandler = Callable[[OrderCommand], Union[CommandAck, Awaitable[CommandAck]]]


class InMemoryForwardingBus:
    """Deterministic local forwarding bus.

    This is intentionally small, but it has the same semantics the production
    transports need: topic fan-out, sequence assignment, replay cache and a
    single command handler for the central order router.
    """

    def __init__(self, replay_size: int = 128) -> None:
        """__init__ method"""
        self.replay_size = _normalize_non_negative_int(replay_size, "replay_size")
        self._market_subscriptions: list[MarketSubscription] = []
        self._private_subscriptions: list[MarketSubscription] = []
        self._market_sequences: dict[str, int] = defaultdict(int)
        self._private_sequences: dict[str, int] = defaultdict(int)
        self._market_replay: dict[str, deque[MarketEvent]] = defaultdict(
            lambda: deque(maxlen=self.replay_size or 1)
        )
        self._private_replay: dict[str, deque[PrivateEvent]] = defaultdict(
            lambda: deque(maxlen=self.replay_size or 1)
        )
        self._command_handler: CommandHandler | None = None
        self._lock = threading.RLock()

    def set_command_handler(self, handler: CommandHandler | None) -> None:
        """set_command_handler method"""
        self._command_handler = handler

    def subscribe_market(self, topic_prefix: str, replay: int = 0) -> MarketSubscription:
        """subscribe_market method"""
        replay = _normalize_non_negative_int(replay, "replay")
        subscription = MarketSubscription(self, topic_prefix, private=False)
        with self._lock:
            self._market_subscriptions.append(subscription)
            if replay > 0:
                for topic, events in self._market_replay.items():
                    if topic.startswith(topic_prefix):
                        for event in list(events)[-replay:
                            ]:
                            subscription.put(event)
        return subscription

    def subscribe_private(self, topic_prefix: str, replay: int = 0) -> MarketSubscription:
        """subscribe_private method"""
        replay = _normalize_non_negative_int(replay, "replay")
        subscription = MarketSubscription(self, topic_prefix, private=True)
        with self._lock:
            self._private_subscriptions.append(subscription)
            if replay > 0:
                for topic, events in self._private_replay.items():
                    if topic.startswith(topic_prefix):
                        for event in list(events)[-replay:
                            ]:
                            subscription.put(event)
        return subscription

    def publish_market(self, event: MarketEvent) -> MarketEvent:
        """publish_market method"""
        with self._lock:
            if event.sequence_id <= 0:
                self._market_sequences[event.topic] += 1
                event.sequence_id = self._market_sequences[event.topic]
            if self.replay_size > 0:
                self._market_replay[event.topic].append(event)
            for subscription in list(self._market_subscriptions):
                if subscription.matches(event.topic):
                    subscription.put(event)
        return event

    def publish_private(self, event: PrivateEvent) -> PrivateEvent:
        """publish_private method"""
        with self._lock:
            if event.sequence_id <= 0:
                self._private_sequences[event.topic] += 1
                event.sequence_id = self._private_sequences[event.topic]
            if self.replay_size > 0:
                self._private_replay[event.topic].append(event)
            for subscription in list(self._private_subscriptions):
                if subscription.matches(event.topic):
                    subscription.put(event)
        return event

    async def send_command(self, command: OrderCommand) -> CommandAck:
        """send_command method"""
        if self._command_handler is None:
            raise RuntimeError("No forwarding command handler is registered")
        result = self._command_handler(command)
        if inspect.isawaitable(result):
            return await result
        return result

    def send_command_sync(
        self, command: OrderCommand, *, timeout: float | None = None
    ) -> CommandAck:
        """send_command_sync method"""
        timeout = _normalize_timeout(timeout)
        return _run_awaitable_sync(self.send_command(command), timeout=timeout)

    def stats(self) -> dict[str, Any]:
        """stats method"""
        with self._lock:
            return {
                "replay_size": self.replay_size,
                "market_subscription_count": len(self._market_subscriptions),
                "private_subscription_count": len(self._private_subscriptions),
                "market_replay_topic_count": len(self._market_replay),
                "private_replay_topic_count": len(self._private_replay),
                "market_sequence_topic_count": len(self._market_sequences),
                "private_sequence_topic_count": len(self._private_sequences),
                "command_handler_registered": self._command_handler is not None,
            }

    def _remove_subscription(self, subscription: MarketSubscription) -> None:
        with self._lock:
            if subscription.private:
                if subscription in self._private_subscriptions:
                    self._private_subscriptions.remove(subscription)
            elif subscription in self._market_subscriptions:
                self._market_subscriptions.remove(subscription)


def _timeout_error(timeout: float) -> TimeoutError:
    return TimeoutError(f"forwarding command sync bridge timed out after {timeout}s")


def _normalize_timeout(timeout: float | None) -> float | None:
    if timeout is None:
        return None
    timeout = float(timeout)
    if timeout < 0 or not math.isfinite(timeout):
        raise ValueError("timeout must be a non-negative finite number")
    return timeout


def _normalize_non_negative_int(value: int, name: str) -> int:
    value = int(value)
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


def _close_awaitable(awaitable: Awaitable[Any]) -> None:
    close = getattr(awaitable, "close", None)
    if callable(close):
        close()


async def _await_with_timeout(awaitable: Awaitable[Any], timeout: float | None) -> Any:
    try:
        timeout = _normalize_timeout(timeout)
    except ValueError:
        _close_awaitable(awaitable)
        raise
    try:
        if timeout is None:
            return await awaitable
        return await asyncio.wait_for(awaitable, timeout=timeout)
    except TimeoutError as exc:
        raise _timeout_error(timeout) from exc


def _run_awaitable_sync(awaitable: Awaitable[Any], *, timeout: float | None = None) -> Any:
    try:
        timeout = _normalize_timeout(timeout)
    except ValueError:
        _close_awaitable(awaitable)
        raise

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_await_with_timeout(awaitable, timeout))

    result: dict[str, Any] = {}

    def runner() -> None:
        try:
            result["value"] = asyncio.run(_await_with_timeout(awaitable, timeout))
        except BaseException as exc:  # pragma: no cover - defensive bridge
            result["error"] = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join(None if timeout is None else max(timeout + 0.5, 0.5))
    if thread.is_alive() and timeout is not None:
        raise _timeout_error(timeout)
    if "error" in result:
        raise result["error"]
    return result.get("value")
