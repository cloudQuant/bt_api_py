"""Module-level docstring."""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import inspect
import math
import threading
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

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


CommandHandler = Callable[[OrderCommand], CommandAck | Awaitable[CommandAck]]


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
                        for event in list(events)[-replay:]:
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
                        for event in list(events)[-replay:]:
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
    return TimeoutError(f"forwarding command result unknown after {timeout}s timeout")


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


class _LoopRunner:
    """常驻事件循环单例，供 sync→async 桥接统一复用。

    一个守护线程跑一个 `run_forever()` 的 loop，所有需要把协程转同步的路径
    （`_run_awaitable_sync`、`start_sync`、ZMQ `_run_handler`）都把协程投递到
    这同一个常驻 loop，避免反复 `asyncio.run` 创建/销毁 loop，也避免在新 loop
    上执行绑定原 loop 的 async handler 导致 RuntimeError。
    """

    _instance: _LoopRunner | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="forwarding-loop")
        self._thread.start()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def submit(self, coro: Coroutine[Any, Any, Any]) -> concurrent.futures.Future[Any]:
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    @classmethod
    def get(cls) -> _LoopRunner:
        with cls._lock:
            if cls._instance is None or not cls._instance._loop.is_running():
                cls._instance = cls()
            return cls._instance


def _consume_future_result(future: concurrent.futures.Future[Any]) -> None:
    with contextlib.suppress(Exception):
        future.exception()


def _run_awaitable_sync(awaitable: Awaitable[Any], *, timeout: float | None = None) -> Any:
    try:
        timeout = _normalize_timeout(timeout)
    except ValueError:
        _close_awaitable(awaitable)
        raise

    # run_coroutine_threadsafe 只接受 coroutine，这里把任意 awaitable 包成 coroutine。
    async def _resolve() -> Any:
        return await awaitable

    # 统一投递到常驻 loop，禁止嵌套 asyncio.run（会绑定到错误的 loop）。
    future = _LoopRunner.get().submit(_resolve())
    try:
        return future.result(timeout)
    except concurrent.futures.TimeoutError:
        # 结果未知：协程仍在常驻 loop 上继续执行，消费其最终异常避免告警。
        future.add_done_callback(_consume_future_result)
        raise _timeout_error(timeout) from None  # type: ignore[arg-type]  # 超时分支 timeout 必非 None
