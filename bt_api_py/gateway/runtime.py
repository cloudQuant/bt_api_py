from __future__ import annotations

import contextlib
import json
import logging
import threading
import time
from typing import TYPE_CHECKING, Any

import zmq

from bt_api_py.gateway.adapters import (
    BinanceGatewayAdapter,
    CtpGatewayAdapter,
    IbWebGatewayAdapter,
    Mt5GatewayAdapter,
    OkxGatewayAdapter,
)
from bt_api_py.gateway.health import ConnectionState, GatewayHealth, GatewayState
from bt_api_py.gateway.order_identity_map import OrderIdentityMap
from bt_api_py.gateway.order_ref_allocator import OrderRefAllocator
from bt_api_py.gateway.protocol import CHANNEL_EVENT, CHANNEL_MARKET, dumps_message, loads_message
from bt_api_py.gateway.subscription_manager import SubscriptionManager

if TYPE_CHECKING:
    from bt_api_py.gateway.adapters.base import BaseGatewayAdapter
    from bt_api_py.gateway.config import GatewayConfig
    from bt_api_py.gateway.storage.tick_writer import TickWriter

logger = logging.getLogger(__name__)


class GatewayRuntime:
    ADAPTER_REGISTRY = {
        "CTP": CtpGatewayAdapter,
        "IB_WEB": IbWebGatewayAdapter,
        "BINANCE": BinanceGatewayAdapter,
        "OKX": OkxGatewayAdapter,
        **({"MT5": Mt5GatewayAdapter} if Mt5GatewayAdapter is not None else {}),
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
        self._adapter_connected = False
        self.thread: threading.Thread | None = None

        # Core sub-systems
        self.subscriptions = SubscriptionManager()
        self.order_map = OrderIdentityMap()
        self.health = GatewayHealth()
        self.order_ref_allocator: OrderRefAllocator | None = None
        self.tick_writer: TickWriter | None = None

        # Initialise CTP-specific OrderRefAllocator
        if config.exchange_type == "CTP":
            state_dir = kwargs.get(
                "state_dir",
                config.gateway_base_dir
                if hasattr(config, "gateway_base_dir")
                else "/tmp/bt_gateway_state",
            )
            self.order_ref_allocator = OrderRefAllocator(config.account_id, state_dir=state_dir)

        # Initialise TickWriter if tick_writer config provided
        tick_writer_cfg = kwargs.get("tick_writer")
        if isinstance(tick_writer_cfg, dict):
            from bt_api_py.gateway.storage.tick_writer import TickWriter as _TW  # noqa: N814

            self.tick_writer = _TW(
                base_dir=tick_writer_cfg.get("base_dir", "/tmp/bt_ticks"),
                exchange=config.exchange_type,
                asset_type=config.asset_type,
                flush_count=tick_writer_cfg.get("flush_count", 1000),
                flush_interval_sec=tick_writer_cfg.get("flush_interval_sec", 5.0),
            )

        self.health.set_extra("exchange", config.exchange_type)
        self.health.set_extra("asset_type", config.asset_type)
        self.health.set_extra("account_id", config.account_id)

    def start(self) -> None:
        if self.running:
            return
        self.health.set_state(GatewayState.STARTING)
        self._adapter_connected = False
        try:
            self.command_socket = self.context.socket(zmq.ROUTER)
            self.event_socket = self.context.socket(zmq.PUB)
            self.market_socket = self.context.socket(zmq.PUB)
            self.command_socket.bind(self.config.command_endpoint)
            self.event_socket.bind(self.config.event_endpoint)
            self.market_socket.bind(self.config.market_endpoint)
            self.poller.register(self.command_socket, zmq.POLLIN)
        except zmq.ZMQError as exc:
            self.health.set_state(GatewayState.ERROR)
            self.health.record_error("runtime_start", f"{type(exc).__name__}: {exc}")
            logger.error(
                "GatewayRuntime failed to bind sockets for %s: %s: %s",
                self.config.exchange_type,
                type(exc).__name__,
                exc,
            )
            self._cleanup_sockets()
            self.running = False
            return
        if self.tick_writer is not None:
            self.tick_writer.start()
        self.running = True
        self.health.set_state(GatewayState.RUNNING)
        # Connect adapter in a background thread so the command loop can
        # immediately respond to ping / health requests.
        adapter_thread = threading.Thread(
            target=self._connect_adapter_background,
            daemon=True,
        )
        adapter_thread.start()
        logger.info("GatewayRuntime started: %s", self.config.exchange_type)
        try:
            while self.running:
                try:
                    self._handle_commands()
                    if self._adapter_connected:
                        self._flush_adapter_output()
                except zmq.ZMQError as exc:
                    if not self.running:
                        break
                    self._handle_runtime_failure("runtime_loop", exc)
                    return
                except Exception as exc:
                    if not self.running:
                        break
                    self._handle_runtime_failure("runtime_loop", exc)
                    return
        finally:
            self._cleanup_sockets()

    def start_in_thread(self) -> None:
        if self.thread is not None and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self.start, daemon=True)
        self.thread.start()
        # Wait until the command loop is running so callers can ping immediately.
        deadline = time.monotonic() + 2.0
        while (
            not self.running
            and self.health.state != GatewayState.ERROR
            and time.monotonic() < deadline
        ):
            time.sleep(0.05)

    def stop(self) -> None:
        self.health.set_state(GatewayState.STOPPING)
        self.running = False
        self._adapter_connected = False
        try:
            self.adapter.disconnect()
        except Exception as exc:
            logger.warning(
                "GatewayRuntime adapter disconnect failed for %s: %s: %s",
                self.config.exchange_type,
                type(exc).__name__,
                exc,
            )
            self.health.record_error("runtime_stop", f"{type(exc).__name__}: {exc}")
        self.health.update_market_connection(ConnectionState.DISCONNECTED)
        self.health.update_trade_connection(ConnectionState.DISCONNECTED)
        if self.tick_writer is not None:
            self.tick_writer.stop()
        if (
            self.thread is not None
            and self.thread.is_alive()
            and threading.current_thread() is not self.thread
        ):
            self.thread.join(timeout=1.0)
        self._cleanup_sockets()
        self.health.set_state(GatewayState.STOPPED)
        logger.info("GatewayRuntime stopped: %s", self.config.exchange_type)

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
        sock = self.command_socket
        if sock is None:
            return
        events = dict(self.poller.poll(timeout=self.config.poll_timeout_ms))
        if sock not in events:
            return
        parts = sock.recv_multipart()
        if len(parts) < 2:
            self.health.record_error("command_parse", "ValueError: invalid command frame")
            logger.warning(
                "Gateway command parse failed for %s request_id=%s: %s: %s",
                self.config.exchange_type,
                "<unknown>",
                "ValueError",
                "invalid command frame",
            )
            return
        identity = parts[0]
        request_id = ""
        command = ""
        try:
            payload = loads_message(parts[-1])
            if not isinstance(payload, dict):
                raise TypeError(
                    f"command payload must decode to dict, got {type(payload).__name__}"
                )
            request_id = str(payload.get("request_id") or "")
            command = str(payload.get("command") or "").lower()
            raw_payload = payload.get("payload")
            if raw_payload is None:
                data = {}
            elif isinstance(raw_payload, dict):
                data = dict(raw_payload)
            else:
                raise TypeError(
                    f"command payload field must be dict, got {type(raw_payload).__name__}"
                )
            result = self._dispatch(command, data)
            response = {"request_id": request_id, "status": "ok", "data": result}
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError) as exc:
            self.health.record_error(
                "command_parse",
                f"{type(exc).__name__}: {exc}",
            )
            logger.warning(
                "Gateway command parse failed for %s request_id=%s: %s: %s",
                self.config.exchange_type,
                request_id or "<unknown>",
                type(exc).__name__,
                exc,
            )
            response = {
                "request_id": request_id,
                "status": "error",
                "error": f"invalid command payload: {exc}",
            }
        except Exception as exc:
            self.health.record_error(
                "command", f"{command or '<empty>'}: {type(exc).__name__}: {exc}"
            )
            logger.warning(
                "Gateway command failed for %s request_id=%s command=%s: %s: %s",
                self.config.exchange_type,
                request_id,
                command or "<empty>",
                type(exc).__name__,
                exc,
            )
            response = {
                "request_id": request_id,
                "status": "error",
                "error": str(exc),
            }
        if self.command_socket is not None:
            sock.send_multipart([identity, dumps_message(response)])
        self._publish(CHANNEL_EVENT, {"kind": "command_result", **response, "command": command})

    def _connect_adapter_background(self) -> None:
        """Connect the exchange adapter with retries; runs in a background thread."""
        max_retries = 3
        self.health.update_market_connection(ConnectionState.CONNECTING)
        for attempt in range(max_retries):
            if not self.running:
                return
            try:
                self.adapter.connect()
                self._adapter_connected = True
                self.health.update_market_connection(ConnectionState.CONNECTED)
                logger.info(
                    "Adapter connected: %s (attempt %d)",
                    self.config.exchange_type,
                    attempt + 1,
                )
                return
            except Exception as exc:
                next_state = (
                    ConnectionState.RECONNECTING
                    if attempt < max_retries - 1 and self.running
                    else ConnectionState.ERROR
                )
                self.health.update_market_connection(next_state)
                logger.warning(
                    "Adapter connect attempt %d/%d failed: %s: %s",
                    attempt + 1,
                    max_retries,
                    type(exc).__name__,
                    exc,
                )
                self.health.record_error(
                    "adapter_connect",
                    f"attempt {attempt + 1}/{max_retries} {type(exc).__name__}: {exc}",
                )
                if attempt < max_retries - 1 and self.running:
                    time.sleep(2.0)
        self._adapter_connected = False
        self.health.update_market_connection(ConnectionState.ERROR)
        logger.error(
            "Adapter failed to connect after %d attempts for %s",
            max_retries,
            self.config.exchange_type,
        )

    def _dispatch(self, command: str, payload: dict[str, Any]) -> Any:
        if command == "ping":
            self.health.record_heartbeat()
            return {"ready": self._adapter_connected}
        if command == "register_strategy":
            strategy_id = str(payload.get("strategy_id") or "")
            symbols = list(payload.get("symbols") or [])
            newly = self.subscriptions.add(strategy_id, symbols)
            if newly and self._adapter_connected:
                self.adapter.subscribe_symbols(list(newly))
            self._sync_health_counts()
            return {"strategy_id": strategy_id, "newly_subscribed": sorted(newly)}
        if command == "unregister_strategy":
            strategy_id = str(payload.get("strategy_id") or "")
            removed = self.subscriptions.remove_strategy(strategy_id)
            self._sync_health_counts()
            return {"strategy_id": strategy_id, "unsubscribed": sorted(removed)}
        if command == "health":
            return self.health.snapshot()
        if not self._adapter_connected:
            raise RuntimeError(f"adapter not yet connected, cannot execute: {command}")
        if command == "subscribe":
            symbols = list(payload.get("symbols") or [])
            strategy_id = str(payload.get("strategy_id") or "default")
            newly = self.subscriptions.add(strategy_id, symbols)
            if newly:
                self.adapter.subscribe_symbols(list(newly))
            self._sync_health_counts()
            return {"subscribed": sorted(newly)}
        if command == "get_balance":
            return self.adapter.get_balance()
        if command == "get_positions":
            return self.adapter.get_positions()
        if command == "place_order":
            request_id = str(payload.get("request_id") or "")
            strategy_id = str(payload.get("strategy_id") or "default")
            self.order_map.register(
                request_id,
                strategy_id,
                symbol=payload.get("symbol"),
            )
            if self.order_ref_allocator is not None:
                client_oid = self.order_ref_allocator.next()
                self.order_map.set_client_order_id(request_id, client_oid)
                payload["client_order_id"] = client_oid
            self.health.record_order()
            return self.adapter.place_order(payload)
        if command == "cancel_order":
            return self.adapter.cancel_order(payload)
        if command == "get_bars":
            symbol = str(payload.get("symbol") or "")
            timeframe = str(payload.get("timeframe") or "M1")
            count = int(payload.get("count") or 100)
            return self.adapter.get_bars(symbol, timeframe, count)
        if command == "get_symbol_info":
            symbol = str(payload.get("symbol") or "")
            return self.adapter.get_symbol_info(symbol)
        if command == "get_open_orders":
            return self.adapter.get_open_orders()
        raise ValueError(f"unsupported command: {command}")

    def _flush_adapter_output(self) -> None:
        while True:
            item = self.adapter.poll_output()
            if item is None:
                return
            channel, payload = item
            if channel == CHANNEL_MARKET:
                self.health.record_tick()
                if self.tick_writer is not None and hasattr(payload, "symbol"):
                    self.tick_writer.write(payload)
            self._publish(channel, payload)

    def _sync_health_counts(self) -> None:
        self.health.update_counts(
            strategy_count=self.subscriptions.strategy_count,
            symbol_count=self.subscriptions.symbol_count,
        )

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

    def _handle_runtime_failure(self, source: str, exc: Exception) -> None:
        self.running = False
        self._adapter_connected = False
        self.health.set_state(GatewayState.ERROR)
        self.health.update_market_connection(ConnectionState.ERROR)
        self.health.update_trade_connection(ConnectionState.ERROR)
        self.health.record_error(source, f"{type(exc).__name__}: {exc}")
        logger.error(
            "GatewayRuntime failed for %s: %s: %s",
            self.config.exchange_type,
            type(exc).__name__,
            exc,
        )
        if self.tick_writer is not None:
            self.tick_writer.stop()
        try:
            self.adapter.disconnect()
        except Exception as disconnect_exc:
            logger.warning(
                "GatewayRuntime adapter disconnect failed during %s for %s: %s: %s",
                source,
                self.config.exchange_type,
                type(disconnect_exc).__name__,
                disconnect_exc,
            )
            self.health.record_error(
                f"{source}_disconnect",
                f"{type(disconnect_exc).__name__}: {disconnect_exc}",
            )

    def _cleanup_sockets(self) -> None:
        for socket in (self.command_socket, self.event_socket, self.market_socket):
            if socket is not None:
                with contextlib.suppress(KeyError):
                    self.poller.unregister(socket)
                try:
                    socket.close(0)
                except Exception as exc:
                    logger.warning(
                        "GatewayRuntime socket cleanup failed for %s: %s: %s",
                        self.config.exchange_type,
                        type(exc).__name__,
                        exc,
                    )
        self.command_socket = None
        self.event_socket = None
        self.market_socket = None
