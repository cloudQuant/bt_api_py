from __future__ import annotations

import contextlib
import threading
import time
from typing import TYPE_CHECKING, Any

import zmq

from bt_api_py.gateway.adapters import (
    BinanceGatewayAdapter,
    CtpGatewayAdapter,
    IbWebGatewayAdapter,
    OkxGatewayAdapter,
)
from bt_api_py.gateway.protocol import CHANNEL_EVENT, CHANNEL_MARKET, dumps_message, loads_message

if TYPE_CHECKING:
    from bt_api_py.gateway.adapters.base import BaseGatewayAdapter
    from bt_api_py.gateway.config import GatewayConfig


class GatewayRuntime:
    ADAPTER_REGISTRY = {
        "CTP": CtpGatewayAdapter,
        "IB_WEB": IbWebGatewayAdapter,
        "BINANCE": BinanceGatewayAdapter,
        "OKX": OkxGatewayAdapter,
    }

    def __init__(self, config: GatewayConfig, **kwargs: Any) -> None:
        self.config = config
        self.kwargs = dict(kwargs)
        self.context = zmq.Context.instance()
        self.command_socket: zmq.Socket | None = None
        self.event_socket: zmq.Socket | None = None
        self.market_socket: zmq.Socket | None = None
        self.poller = zmq.Poller()
        self.adapter = self._create_adapter()
        self.running = False
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if self.running:
            return
        self.command_socket = self.context.socket(zmq.ROUTER)
        self.event_socket = self.context.socket(zmq.PUB)
        self.market_socket = self.context.socket(zmq.PUB)
        self.command_socket.bind(self.config.command_endpoint)
        self.event_socket.bind(self.config.event_endpoint)
        self.market_socket.bind(self.config.market_endpoint)
        self.poller.register(self.command_socket, zmq.POLLIN)
        self.adapter.connect()
        self.running = True
        while self.running:
            try:
                self._handle_commands()
                self._flush_adapter_output()
            except zmq.ZMQError:
                if not self.running:
                    break
                raise

    def start_in_thread(self) -> None:
        if self.thread is not None and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self.start, daemon=True)
        self.thread.start()
        time.sleep(0.2)

    def stop(self) -> None:
        self.running = False
        self.adapter.disconnect()
        for socket in (self.command_socket, self.event_socket, self.market_socket):
            if socket is not None:
                with contextlib.suppress(KeyError):
                    self.poller.unregister(socket)
                socket.close(0)
        self.command_socket = None
        self.event_socket = None
        self.market_socket = None
        if self.thread is not None and self.thread.is_alive() and threading.current_thread() is not self.thread:
            self.thread.join(timeout=1.0)

    @classmethod
    def register_adapter(cls, exchange_type: str, adapter_cls: type[BaseGatewayAdapter]) -> None:
        cls.ADAPTER_REGISTRY[str(exchange_type).strip().upper()] = adapter_cls

    @classmethod
    def get_adapter_class(cls, exchange_type: str) -> type[BaseGatewayAdapter]:
        normalized = str(exchange_type).strip().upper()
        adapter_cls = cls.ADAPTER_REGISTRY.get(normalized)
        if adapter_cls is None:
            raise ValueError(f"unsupported gateway exchange: {exchange_type}")
        return adapter_cls

    def _create_adapter(self):
        adapter_cls = self.get_adapter_class(self.config.exchange_type)
        return adapter_cls(**self.kwargs)

    def _handle_commands(self) -> None:
        if self.command_socket is None:
            return
        events = dict(self.poller.poll(timeout=self.config.poll_timeout_ms))
        if self.command_socket not in events:
            return
        parts = self.command_socket.recv_multipart()
        identity = parts[0]
        payload = loads_message(parts[-1])
        request_id = str(payload.get("request_id") or "")
        command = str(payload.get("command") or "").lower()
        data = dict(payload.get("payload") or {})
        try:
            result = self._dispatch(command, data)
            response = {"request_id": request_id, "status": "ok", "data": result}
        except Exception as exc:
            response = {"request_id": request_id, "status": "error", "error": str(exc)}
        self.command_socket.send_multipart([identity, dumps_message(response)])
        self._publish(CHANNEL_EVENT, {"kind": "command_result", **response, "command": command})

    def _dispatch(self, command: str, payload: dict[str, Any]) -> Any:
        if command == "ping":
            return {"ready": True}
        if command == "subscribe":
            return self.adapter.subscribe_symbols(list(payload.get("symbols") or []))
        if command == "get_balance":
            return self.adapter.get_balance()
        if command == "get_positions":
            return self.adapter.get_positions()
        if command == "place_order":
            return self.adapter.place_order(payload)
        if command == "cancel_order":
            return self.adapter.cancel_order(payload)
        raise ValueError(f"unsupported command: {command}")

    def _flush_adapter_output(self) -> None:
        while True:
            item = self.adapter.poll_output()
            if item is None:
                return
            channel, payload = item
            self._publish(channel, payload)

    def _publish(self, channel: str, payload: Any) -> None:
        try:
            if channel == CHANNEL_MARKET and self.market_socket is not None:
                self.market_socket.send(
                    dumps_message(payload.to_dict() if hasattr(payload, "to_dict") else payload)
                )
            elif channel == CHANNEL_EVENT and self.event_socket is not None:
                self.event_socket.send(dumps_message(payload))
        except zmq.ZMQError:
            if self.running:
                raise
