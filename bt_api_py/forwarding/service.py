"""Module documentation"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from dataclasses import dataclass

from bt_api_py.brokers.base import BrokerAdapter
from bt_api_py.forwarding.hub import MarketDataHub
from bt_api_py.forwarding.memory import InMemoryForwardingBus, _run_awaitable_sync
from bt_api_py.forwarding.private_event_pump import PrivateEventPump
from bt_api_py.forwarding.router import OrderRouter, RiskRuleSet
from bt_api_py.forwarding.source_supervisor import SourceSupervisor
from bt_api_py.forwarding.state import SQLiteStateStore
from bt_api_py.forwarding.transport import ZmqCommandServer, ZmqEventPublisher
from bt_api_py.gateway.config import GatewayConfig


@dataclass
class ForwardingRuntime:
    """Embedded runtime that composes market fan-out and order routing."""

    adapter: BrokerAdapter
    bus: InMemoryForwardingBus | None = None
    risk_rules: RiskRuleSet | None = None
    state_store: SQLiteStateStore | None = None
    source: object | None = None

    def __post_init__(self) -> None:
        if self.bus is None:
            self.bus = InMemoryForwardingBus()
        self.market_data = MarketDataHub(self.bus)
        self.order_router = OrderRouter(
            self.adapter,
            bus=self.bus,
            risk_rules=self.risk_rules,
            state_store=self.state_store,
        )
        self.source_supervisor = SourceSupervisor(self.source) if self.source is not None else None
        account_id = str(getattr(self.adapter, "account_id", "default"))
        self.private_event_pump = PrivateEventPump(self.adapter, self.bus, account_id=account_id)
        self._pump_task: asyncio.Task | None = None

    async def start(self) -> bool:
        """start method"""
        result = await self.order_router.connect()
        self._pump_task = asyncio.create_task(self.private_event_pump.run_once())
        return result

    async def stop(self) -> bool:
        """stop method"""
        if self._pump_task is not None:
            self._pump_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._pump_task
            self._pump_task = None
        return await self.order_router.disconnect()

    async def health(self) -> dict[str, object]:
        """health method"""
        return {
            "runtime": type(self).__name__,
            "market_data": self.market_data.stats(),
            "order_router": await self.order_router.health(),
        }


class ZmqForwardingRuntime(ForwardingRuntime):
    """Standalone ZeroMQ runtime for market fan-out and order routing."""

    def __init__(
        self,
        adapter: BrokerAdapter,
        *,
        market_endpoint: str,
        command_endpoint: str,
        private_endpoint: str | None = None,
        enable_trading: bool = False,
        allow_remote: bool = False,
        allow_shared_private_endpoint: bool = False,
        bus: InMemoryForwardingBus | None = None,
        risk_rules: RiskRuleSet | None = None,
        state_store: SQLiteStateStore | None = None,
    ) -> None:
        """__init__ method"""
        super().__init__(adapter=adapter, bus=bus, risk_rules=risk_rules, state_store=state_store)
        # Safe-by-default: read-only loopback/IPC unless explicitly authorized.
        # A missing private_endpoint must not silently share the public market port.
        config = GatewayConfig(
            command_endpoint=command_endpoint,
            market_endpoint=market_endpoint,
            private_endpoint=private_endpoint,
            enable_trading=enable_trading,
            allow_remote=allow_remote,
            allow_shared_private_endpoint=allow_shared_private_endpoint,
        )
        self.config = config
        self.market_endpoint = config.market_endpoint
        self.command_endpoint = config.command_endpoint
        self.private_endpoint = config.private_endpoint or config.market_endpoint
        self._market_publisher: ZmqEventPublisher | None = None
        self._private_publisher: ZmqEventPublisher | None = None
        self._command_server: ZmqCommandServer | None = None
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    @property
    def is_running(self) -> bool:
        """is_running method"""
        return self._command_server is not None

    def start_sync(self) -> None:
        """start_sync method"""
        if self.is_running:
            return
        _run_awaitable_sync(self.start())
        try:
            self._stop.clear()
            self._market_publisher = ZmqEventPublisher(self.market_endpoint)
            if self.private_endpoint == self.market_endpoint:
                self._private_publisher = self._market_publisher
            else:
                self._private_publisher = ZmqEventPublisher(self.private_endpoint)
            self._command_server = ZmqCommandServer(
                self.command_endpoint, self.order_router.handle_command
            )
            self._command_server.start()
            self._threads = [
                threading.Thread(target=self._forward_market_events, daemon=True),
                threading.Thread(target=self._forward_private_events, daemon=True),
            ]
            for thread in self._threads:
                thread.start()
        except Exception:
            self._cleanup_sync_resources(disconnect_adapter=True)
            raise

    def stop_sync(self) -> None:
        """stop_sync method"""
        if (
            self._command_server is None
            and self._market_publisher is None
            and self._private_publisher is None
            and not self._threads
        ):
            return
        self._cleanup_sync_resources(disconnect_adapter=True)

    def _cleanup_sync_resources(self, *, disconnect_adapter: bool) -> None:
        self._stop.set()
        for thread in self._threads:
            if thread.ident is not None:
                thread.join(timeout=2.0)
        self._threads.clear()
        if self._command_server is not None:
            self._command_server.stop()
            self._command_server = None
        if (
            self._private_publisher is not None
            and self._private_publisher is not self._market_publisher
        ):
            self._private_publisher.close()
        if self._market_publisher is not None:
            self._market_publisher.close()
        self._private_publisher = None
        self._market_publisher = None
        if disconnect_adapter:
            _run_awaitable_sync(self.stop())

    async def health(self) -> dict[str, object]:
        """health method"""
        payload = await super().health()
        payload["zmq"] = {
            "market_endpoint": self.market_endpoint,
            "command_endpoint": self.command_endpoint,
            "private_endpoint": self.private_endpoint,
            "running": self.is_running,
            "forwarder_thread_count": len(
                [thread for thread in self._threads if thread.is_alive()]
            ),
            "market_publisher_active": self._market_publisher is not None,
            "private_publisher_active": self._private_publisher is not None,
        }
        return payload

    def _forward_market_events(self) -> None:
        subscription = self.bus.subscribe_market("md.")  # type: ignore[union-attr]
        try:
            while not self._stop.is_set():
                event = subscription.poll()
                if event is None:
                    time.sleep(0.001)
                    continue
                if self._market_publisher is not None:
                    self._market_publisher.publish(event)
        finally:
            subscription.close()

    def _forward_private_events(self) -> None:
        subscription = self.bus.subscribe_private("")  # type: ignore[union-attr]
        try:
            while not self._stop.is_set():
                event = subscription.poll()
                if event is None:
                    time.sleep(0.001)
                    continue
                if self._private_publisher is not None:
                    self._private_publisher.publish(event)
        finally:
            subscription.close()
