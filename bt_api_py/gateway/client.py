from __future__ import annotations

import collections
import uuid
from copy import deepcopy
from typing import Any

import zmq

from bt_api_py.gateway.config import GatewayConfig
from bt_api_py.gateway.models import GatewayTick
from bt_api_py.gateway.protocol import dumps_message, loads_message, make_request_id
from bt_api_py.gateway.runtime import GatewayRuntime


class GatewayClient:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = dict(kwargs)
        self.config = GatewayConfig.from_kwargs(**kwargs)
        self.context = zmq.Context.instance()
        self.command_socket: zmq.Socket | None = None
        self.market_socket: zmq.Socket | None = None
        self.event_socket: zmq.Socket | None = None
        self.runtime: GatewayRuntime | None = None
        self.connected = False
        self.tick_queues = collections.defaultdict(collections.deque)
        self.broker_updates = collections.deque()
        self.pending_orders: dict[str, dict[str, Any]] = {}
        self.subscribed = set()

    def connect(self) -> None:
        if self.connected:
            return
        if bool(self.kwargs.get("gateway_start_local_runtime", True)):
            self.runtime = GatewayRuntime(self.config, **self.kwargs)
            self.runtime.start_in_thread()
        self.command_socket = self.context.socket(zmq.DEALER)
        self.command_socket.setsockopt(zmq.IDENTITY, uuid.uuid4().hex.encode("utf-8"))
        self.market_socket = self.context.socket(zmq.SUB)
        self.event_socket = self.context.socket(zmq.SUB)
        self.market_socket.setsockopt(zmq.SUBSCRIBE, b"")
        self.event_socket.setsockopt(zmq.SUBSCRIBE, b"")
        self.command_socket.connect(self.config.command_endpoint)
        self.market_socket.connect(self.config.market_endpoint)
        self.event_socket.connect(self.config.event_endpoint)
        self._command("ping")
        self.connected = True

    def start(self) -> None:
        self.connect()

    def disconnect(self) -> None:
        self.connected = False
        for socket in (self.command_socket, self.market_socket, self.event_socket):
            if socket is not None:
                socket.close(0)
        self.command_socket = None
        self.market_socket = None
        self.event_socket = None
        if self.runtime is not None:
            self.runtime.stop()
            self.runtime = None

    def stop(self) -> None:
        self.disconnect()

    def subscribe(self, symbols) -> dict[str, Any]:
        values = [symbols] if isinstance(symbols, str) else list(symbols or [])
        result = self._command("subscribe", {"symbols": values})
        self.subscribed.update(result.get("subscribed") or result.get("symbols") or [])
        return result

    def poll_tick(self, symbol):
        self._drain_market()
        queue = self.tick_queues.get(str(symbol))
        if not queue:
            return None
        return queue.popleft()

    def get_next_tick(self, symbol):
        return self.poll_tick(symbol)

    def has_pending_tick(self, symbol) -> bool:
        self._drain_market()
        return bool(self.tick_queues.get(str(symbol)))

    def supports_live_ticks(self, symbol) -> bool:
        return str(symbol) in self.subscribed

    def poll_broker_update(self):
        self._drain_event()
        if not self.broker_updates:
            return None
        return self.broker_updates.popleft()

    def get_balance(self) -> dict[str, Any]:
        return dict(self._command("get_balance"))

    def get_account(self) -> dict[str, Any]:
        return self.get_balance()

    def get_positions(self) -> list[dict[str, Any]]:
        return list(self._command("get_positions") or [])

    def submit_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = dict(self._command("place_order", payload))
        if "data_name" not in response and payload.get("data_name") not in (None, ""):
            response = {**response, "data_name": payload["data_name"]}
        self._remember_order(response)
        return response

    def create_order(self, **kwargs):
        return self.submit_order(kwargs)

    def cancel_order(self, order_ref, dataname=None):
        details = dict(self.pending_orders.get(str(order_ref), {}))
        payload = {
            "data_name": dataname or details.get("data_name") or details.get("instrument") or "",
            "order_id": details.get("external_order_id") or details.get("order_id") or order_ref,
            "external_order_id": details.get("external_order_id") or order_ref,
            "order_ref": details.get("order_ref"),
            "front_id": details.get("front_id"),
            "session_id": details.get("session_id"),
            "exchange_id": details.get("exchange_id"),
        }
        return dict(self._command("cancel_order", payload))

    def fetch_bars(self, symbol: str, timeframe: str = "M1", count: int = 100) -> list[dict[str, Any]]:
        return list(self._command("get_bars", {"symbol": symbol, "timeframe": timeframe, "count": count}) or [])

    def fetch_symbol_info(self, symbol: str) -> dict[str, Any]:
        return dict(self._command("get_symbol_info", {"symbol": symbol}) or {})

    def fetch_open_orders(self) -> list[dict[str, Any]]:
        return list(self._command("get_open_orders") or [])

    def _command(self, command: str, payload: dict[str, Any] | None = None) -> Any:
        if self.command_socket is None:
            raise RuntimeError("gateway client is not connected")
        request_id = make_request_id()
        self.command_socket.send(dumps_message({"request_id": request_id, "command": command, "payload": payload or {}}))
        timeout_ms = int(self.config.command_timeout_sec * 1000)
        if self.command_socket.poll(timeout=timeout_ms) == 0:
            raise TimeoutError(f"gateway command timed out: {command}")
        response = loads_message(self.command_socket.recv())
        if response.get("status") != "ok":
            raise RuntimeError(response.get("error") or f"gateway command failed: {command}")
        self._drain_event()
        return response.get("data")

    def _drain_market(self) -> None:
        if self.market_socket is None:
            return
        while True:
            try:
                payload = loads_message(self.market_socket.recv(zmq.NOBLOCK))
            except zmq.Again:
                return
            tick = GatewayTick.from_dict(payload)
            self.tick_queues[str(tick.symbol)].append(tick)

    def _drain_event(self) -> None:
        if self.event_socket is None:
            return
        while True:
            try:
                payload = loads_message(self.event_socket.recv(zmq.NOBLOCK))
            except zmq.Again:
                return
            if str(payload.get("kind") or "") == "command_result":
                continue
            if payload.get("kind") in {"order", "trade"}:
                self._remember_order(payload)
            self.broker_updates.append(payload)

    def _remember_order(self, payload: dict[str, Any]) -> None:
        payload_copy = deepcopy(payload)
        keys = [
            payload_copy.get("external_order_id"),
            payload_copy.get("order_id"),
            payload_copy.get("order_ref"),
        ]
        for key in keys:
            if key not in (None, ""):
                existing = deepcopy(self.pending_orders.get(str(key), {}))
                merged = {**existing, **payload_copy}
                self.pending_orders[str(key)] = merged
