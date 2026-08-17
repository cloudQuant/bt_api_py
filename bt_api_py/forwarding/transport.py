"""Module documentation"""
from __future__ import annotations

import inspect
import threading
from collections.abc import Awaitable, Callable
from typing import Union

import zmq
from bt_api_base.logging_factory import get_logger

from bt_api_py.forwarding.memory import _run_awaitable_sync
from bt_api_py.forwarding.schema import (
    CommandAck,
    ForwardingError,
    MarketEvent,
    OrderCommand,
    PrivateEvent,
    deserialize_message,
    serialize_message,
)

logger = get_logger("forwarding.transport")


class ZmqMarketPublisher:
    """ZeroMQ PUB publisher for normalized market events."""

    def __init__(
        self, endpoint: str, *, bind: bool = True, context: zmq.Context | None = None
    ) -> None:
        """__init__ method"""
        self.endpoint = endpoint
        self.context = context or zmq.Context.instance()
        self.socket = self.context.socket(zmq.PUB)
        if bind:
            self.socket.bind(endpoint)
        else:
            self.socket.connect(endpoint)

    def publish(self, event: MarketEvent) -> None:
        """publish method"""
        self.socket.send_multipart([event.topic.encode("utf-8"), serialize_message(event)])

    def close(self) -> None:
        """close method"""
        self.socket.close(linger=0)


class ZmqMarketSubscriber:
    """ZeroMQ SUB subscriber for normalized market events."""

    def __init__(
        self,
        endpoint: str,
        topic_prefix: str = "md.",
        *,
        connect: bool = True,
        context: zmq.Context | None = None,
    ) -> None:
        """__init__ method"""
        self.endpoint = endpoint
        self.context = context or zmq.Context.instance()
        self.socket = self.context.socket(zmq.SUB)
        self.socket.setsockopt_string(zmq.SUBSCRIBE, topic_prefix)
        if connect:
            self.socket.connect(endpoint)
        else:
            self.socket.bind(endpoint)

    def poll(self, timeout_ms: int = 0) -> MarketEvent | None:
        """poll method"""
        if self.socket.poll(timeout_ms) == 0:
            return None
        _topic, payload = self.socket.recv_multipart()
        message = deserialize_message(payload)
        if not isinstance(message, MarketEvent):
            raise ForwardingError(f"Expected MarketEvent, got {type(message)!r}")
        return message

    def close(self) -> None:
        """close method"""
        self.socket.close(linger=0)


class ZmqEventPublisher:
    """ZeroMQ PUB publisher for market and private forwarding events."""

    def __init__(
        self, endpoint: str, *, bind: bool = True, context: zmq.Context | None = None
    ) -> None:
        """__init__ method"""
        self.endpoint = endpoint
        self.context = context or zmq.Context.instance()
        self.socket = self.context.socket(zmq.PUB)
        if bind:
            self.socket.bind(endpoint)
        else:
            self.socket.connect(endpoint)

    def publish(self, event: MarketEvent | PrivateEvent) -> None:
        """publish method"""
        self.socket.send_multipart([event.topic.encode("utf-8"), serialize_message(event)])

    def close(self) -> None:
        """close method"""
        self.socket.close(linger=0)


class ZmqEventSubscriber:
    """ZeroMQ SUB subscriber for any forwarding event type."""

    def __init__(
        self,
        endpoint: str,
        topic_prefix: str,
        *,
        connect: bool = True,
        context: zmq.Context | None = None,
    ) -> None:
        """__init__ method"""
        self.endpoint = endpoint
        self.context = context or zmq.Context.instance()
        self.socket = self.context.socket(zmq.SUB)
        self.socket.setsockopt_string(zmq.SUBSCRIBE, topic_prefix)
        if connect:
            self.socket.connect(endpoint)
        else:
            self.socket.bind(endpoint)

    def poll(self, timeout_ms: int = 0) -> MarketEvent | PrivateEvent | None:
        """poll method"""
        if self.socket.poll(timeout_ms) == 0:
            return None
        _topic, payload = self.socket.recv_multipart()
        message = deserialize_message(payload)
        if not isinstance(message, (MarketEvent, PrivateEvent)):
            raise ForwardingError(f"Expected forwarding event, got {type(message)!r}")
        return message

    def close(self) -> None:
        """close method"""
        self.socket.close(linger=0)


CommandHandler = Callable[[OrderCommand], Union[CommandAck, Awaitable[CommandAck]]]


class ZmqCommandServer:
    """ZeroMQ ROUTER command server.

    The server runs a small background loop so tests and local services can use
    the same ROUTER/DEALER protocol without introducing an external daemon.
    """

    def __init__(
        self,
        endpoint: str,
        handler: CommandHandler,
        *,
        bind: bool = True,
        context: zmq.Context | None = None,
    ) -> None:
        """__init__ method"""
        self.endpoint = endpoint
        self.handler = handler
        self.context = context or zmq.Context.instance()
        self.socket = self.context.socket(zmq.ROUTER)
        if bind:
            self.socket.bind(endpoint)
        else:
            self.socket.connect(endpoint)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """start method"""
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """stop method"""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self.socket.close(linger=0)

    def _run(self) -> None:
        while not self._stop.is_set():
            if self.socket.poll(50) == 0:
                continue
            frames = self.socket.recv_multipart()
            if len(frames) < 2:
                continue
            identity, payload = frames[0], frames[-1]
            command: OrderCommand | None = None
            try:
                command = deserialize_message(payload)
                if not isinstance(command, OrderCommand):
                    raise ForwardingError(f"Expected OrderCommand, got {type(command)!r}")
                ack = _run_handler(self.handler, command)
            except Exception as exc:
                logger.warning(
                    "ZMQ command handler failed: "
                    f"command_id={command.command_id if command is not None else 'unknown'}, "
                    "idempotency_key="
                    f"{str(command.idempotency_key) if command is not None else 'unknown'}, "
                    f"error_type={type(exc).__name__}, error={exc}"
                )
                ack = CommandAck(
                    command_id=command.command_id if command is not None else "unknown",
                    idempotency_key=(
                        str(command.idempotency_key) if command is not None else "unknown"
                    ),
                    accepted=False,
                    status="rejected",
                    account_id=command.account_id if command is not None else "",
                    strategy_id=command.strategy_id if command is not None else "",
                    reason=str(exc),
                )
            self.socket.send_multipart([identity, serialize_message(ack)])


class ZmqCommandClient:
    """ZeroMQ DEALER command client."""

    def __init__(self, endpoint: str, *, context: zmq.Context | None = None) -> None:
        """__init__ method"""
        self.endpoint = endpoint
        self.context = context or zmq.Context.instance()
        self.socket = self.context.socket(zmq.DEALER)
        self.socket.connect(endpoint)

    def send(self, command: OrderCommand, timeout_ms: int = 2000) -> CommandAck:
        """send method"""
        timeout_ms = _normalize_timeout_ms(timeout_ms)
        self.socket.send(serialize_message(command))
        if self.socket.poll(timeout_ms) == 0:
            # Drain stale replies to prevent ack mismatch on next send
            while self.socket.poll(0):
                self.socket.recv()
            raise TimeoutError(f"forwarding command timed out after {timeout_ms}ms")
        message = deserialize_message(self.socket.recv())
        if not isinstance(message, CommandAck):
            raise ForwardingError(f"Expected CommandAck, got {type(message)!r}")
        return message

    def close(self) -> None:
        """close method"""
        self.socket.close(linger=0)


def _normalize_timeout_ms(timeout_ms: int) -> int:
    timeout_ms = int(timeout_ms)
    if timeout_ms < 0:
        raise ValueError("timeout_ms must be non-negative")
    return timeout_ms


def _run_handler(handler: CommandHandler, command: OrderCommand) -> CommandAck:
    result = handler(command)
    if inspect.isawaitable(result):
        return _run_awaitable_sync(result)
    return result
