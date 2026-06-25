from __future__ import annotations

import collections
import json
import time
import uuid
from typing import Any


DEFAULT_MAX_TICKS_PER_SYMBOL = 1_000
DEFAULT_MAX_EVENTS = 1_000
DEFAULT_DRAIN_MAX_MESSAGES = 250
DEFAULT_SOCKET_RCVHWM = 1_000
_ALL_SYMBOLS = frozenset({"*", "all", "ALL"})


def _positive_int(value: Any, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def _normalize_symbol(value: Any) -> str:
    return str(value or "").strip()


class GatewayClient:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = dict(kwargs)
        self.command_endpoint = str(kwargs.get("gateway_command_endpoint") or "").strip()
        self.event_endpoint = str(kwargs.get("gateway_event_endpoint") or "").strip()
        self.market_endpoint = str(kwargs.get("gateway_market_endpoint") or "").strip()
        self.command_timeout_sec = float(kwargs.get("gateway_command_timeout_sec") or 10.0)
        self.max_ticks_per_symbol = _positive_int(
            kwargs.get("gateway_max_ticks_per_symbol")
            or kwargs.get("max_ticks_per_symbol"),
            DEFAULT_MAX_TICKS_PER_SYMBOL,
        )
        self.max_events = _positive_int(
            kwargs.get("gateway_max_events") or kwargs.get("max_events"),
            DEFAULT_MAX_EVENTS,
        )
        self.drain_max_messages = _positive_int(
            kwargs.get("gateway_drain_max_messages")
            or kwargs.get("drain_max_messages"),
            DEFAULT_DRAIN_MAX_MESSAGES,
        )
        self.socket_rcvhwm = _positive_int(
            kwargs.get("gateway_socket_rcvhwm") or kwargs.get("socket_rcvhwm"),
            DEFAULT_SOCKET_RCVHWM,
        )
        self._connected = False
        self._context = None
        self._market_socket = None
        self._event_socket = None
        self.subscribed: set[str] = set()
        self._tick_queues: dict[str, collections.deque[dict[str, Any]]] = collections.defaultdict(
            self._new_tick_queue
        )
        self._event_queue: collections.deque[dict[str, Any]] = collections.deque(
            maxlen=self.max_events
        )

    def _new_tick_queue(self) -> collections.deque[dict[str, Any]]:
        return collections.deque(maxlen=self.max_ticks_per_symbol)

    def connect(self) -> None:
        if not self.command_endpoint:
            raise RuntimeError("gateway_command_endpoint is required")
        try:
            import zmq
        except ImportError as exc:
            raise RuntimeError("pyzmq is required for GatewayClient") from exc
        self._context = zmq.Context.instance()
        if self.market_endpoint and self._market_socket is None:
            self._market_socket = self._context.socket(zmq.SUB)
            self._market_socket.setsockopt(zmq.SUBSCRIBE, b"")
            self._market_socket.setsockopt(zmq.RCVHWM, self.socket_rcvhwm)
            self._market_socket.setsockopt(zmq.RCVTIMEO, 0)
            self._market_socket.connect(self.market_endpoint)
        if self.event_endpoint and self._event_socket is None:
            self._event_socket = self._context.socket(zmq.SUB)
            self._event_socket.setsockopt(zmq.SUBSCRIBE, b"")
            self._event_socket.setsockopt(zmq.RCVHWM, self.socket_rcvhwm)
            self._event_socket.setsockopt(zmq.RCVTIMEO, 0)
            self._event_socket.connect(self.event_endpoint)
        self._connected = True

    def disconnect(self) -> None:
        for sock in (self._market_socket, self._event_socket):
            if sock is not None:
                sock.close(linger=0)
        self._market_socket = None
        self._event_socket = None
        self._connected = False

    def _ensure_connected(self) -> None:
        if not self._connected:
            self.connect()

    def _send_command(self, command: str, payload: dict[str, Any] | None = None) -> Any:
        self._ensure_connected()
        import zmq

        ctx = zmq.Context.instance()
        timeout_ms = int(max(self.command_timeout_sec, 0.1) * 1000)
        attempts = int(self.kwargs.get("gateway_command_retries") or 3)
        attempts = max(attempts, 1)
        response = None
        for attempt in range(attempts):
            sock = ctx.socket(zmq.DEALER)
            sock.setsockopt(zmq.IDENTITY, uuid.uuid4().hex.encode("utf-8"))
            sock.setsockopt(zmq.SNDTIMEO, timeout_ms)
            sock.setsockopt(zmq.RCVTIMEO, timeout_ms)
            try:
                sock.connect(self.command_endpoint)
                request = {
                    "request_id": uuid.uuid4().hex,
                    "command": command,
                    "payload": dict(payload or {}),
                }
                sock.send(
                    json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode()
                )
                response = json.loads(sock.recv().decode("utf-8"))
                break
            except zmq.Again:
                if attempt >= attempts - 1:
                    raise
                time.sleep(min(0.5 * (attempt + 1), 2.0))
            finally:
                sock.close(linger=0)
        if response is None:
            raise RuntimeError(f"gateway command timed out: {command}")
        if not isinstance(response, dict):
            raise RuntimeError(f"invalid gateway response for {command}: {response!r}")
        if response.get("status") != "ok":
            raise RuntimeError(str(response.get("error") or f"gateway command failed: {command}"))
        return response.get("data")

    def _command(self, command: str, payload: dict[str, Any] | None = None) -> Any:
        return self._send_command(command, payload)

    def subscribe(self, symbols: str | list[str]) -> dict[str, Any]:
        if isinstance(symbols, str):
            symbol_list = [symbols]
        else:
            symbol_list = list(symbols)
        symbol_list = [_normalize_symbol(symbol) for symbol in symbol_list]
        symbol_list = [symbol for symbol in symbol_list if symbol]
        result = self._send_command("subscribe", {"symbols": symbol_list}) or {}
        self.subscribed.update(symbol_list)
        return result

    def get_balance(self) -> dict[str, Any]:
        return self._send_command("get_balance", {}) or {}

    def get_account(self) -> dict[str, Any]:
        return self.get_balance()

    def get_positions(self) -> list[dict[str, Any]]:
        return self._send_command("get_positions", {}) or []

    def fetch_open_orders(self) -> list[dict[str, Any]]:
        return self._send_command("get_open_orders", {}) or []

    def fetch_bars(self, symbol: str, timeframe: str = "M1", count: int = 200) -> list[dict[str, Any]]:
        return (
            self._send_command(
                "get_bars",
                {"symbol": symbol, "timeframe": timeframe, "count": count},
            )
            or []
        )

    def fetch_symbol_info(self, symbol: str) -> dict[str, Any]:
        return self._send_command("get_symbol_info", {"symbol": symbol}) or {}

    def submit_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._send_command("place_order", dict(payload or {})) or {}

    def cancel_order(self, order_ref: str, dataname: str | None = None) -> dict[str, Any]:
        return self._send_command(
            "cancel_order",
            {"order_ref": order_ref, "dataname": dataname},
        ) or {}

    def poll_broker_update(self) -> dict[str, Any] | None:
        if self._event_queue:
            return self._event_queue.popleft()
        self._drain_events()
        if self._event_queue:
            return self._event_queue.popleft()
        return None

    def poll_tick(self, symbol: str) -> dict[str, Any] | None:
        symbol_key = str(symbol or "")
        queue = self._tick_queues.get(symbol_key)
        if queue:
            return queue.popleft()
        self._drain_market()
        queue = self._tick_queues.get(symbol_key)
        if queue:
            return queue.popleft()
        return None

    def get_next_tick(self, symbol: str) -> dict[str, Any] | None:
        return self.poll_tick(symbol)

    def has_pending_tick(self, symbol: str) -> bool:
        queue = self._tick_queues.get(str(symbol or ""))
        if queue:
            return True
        self._drain_market()
        queue = self._tick_queues.get(str(symbol or ""))
        return bool(queue)

    def supports_live_ticks(self, _symbol: str | None = None) -> bool:
        return bool(self.market_endpoint)

    def supports_live_streaming(self, _symbol: str | None = None) -> bool:
        return bool(self.market_endpoint)

    def _drain_market(self) -> None:
        if self._market_socket is None:
            return
        self._drain_socket(
            self._market_socket,
            self._store_tick,
            max_messages=self.drain_max_messages,
        )

    def _drain_events(self) -> None:
        if self._event_socket is None:
            return
        self._drain_socket(
            self._event_socket,
            self._event_queue.append,
            max_messages=self.drain_max_messages,
        )

    @staticmethod
    def _drain_socket(
        sock: Any,
        callback: Any,
        *,
        max_messages: int = DEFAULT_DRAIN_MAX_MESSAGES,
    ) -> None:
        import zmq

        drained = 0
        limit = max(int(max_messages or 0), 0)
        deadline = time.monotonic() + 0.05
        while time.monotonic() < deadline:
            if limit and drained >= limit:
                return
            try:
                raw = sock.recv(flags=zmq.NOBLOCK)
            except zmq.Again:
                return
            drained += 1
            payload = json.loads(raw.decode("utf-8"))
            if isinstance(payload, dict):
                callback(payload)

    def _store_tick(self, payload: dict[str, Any]) -> None:
        symbol = _normalize_symbol(payload.get("symbol"))
        instrument = _normalize_symbol(payload.get("instrument_id"))
        keys = [key for key in (symbol, instrument) if key]
        keys = list(dict.fromkeys(keys))
        if not keys:
            return
        if self.subscribed and not (self.subscribed & _ALL_SYMBOLS):
            keys = [key for key in keys if key in self.subscribed]
            if not keys:
                return
        for key in keys:
            self._tick_queues[key].append(payload)
