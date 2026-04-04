import threading
import time
import uuid
from collections import defaultdict, deque
from typing import Any
from unittest.mock import MagicMock

import pytest
import zmq

from bt_api_py.gateway.adapters import (
    BinanceGatewayAdapter,
    CtpGatewayAdapter,
    IbWebGatewayAdapter,
    OkxGatewayAdapter,
)
from bt_api_py.gateway.adapters.binance_adapter import _normalize_asset_type as bn_normalize
from bt_api_py.gateway.adapters.ctp_adapter import _split as ctp_split
from bt_api_py.gateway.adapters.okx_adapter import _normalize_asset_type as okx_normalize
from bt_api_py.gateway.client import GatewayClient
from bt_api_py.gateway.config import GatewayConfig
from bt_api_py.gateway.health import ConnectionState, GatewayState
from bt_api_py.gateway.models import GatewayTick
from bt_api_py.gateway.protocol import CHANNEL_EVENT, CHANNEL_MARKET
from bt_api_py.gateway.runtime import GatewayRuntime


class FakeGatewayAdapter:
    def __init__(self) -> None:
        self.connected = False
        self.outputs: deque[tuple[str, Any]] = deque()
        self.subscriptions: list[str] = []

    def connect(self) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def subscribe_symbols(self, symbols: list[str]) -> dict[str, Any]:
        self.subscriptions.extend(symbols)
        return {"symbols": list(symbols)}

    def get_balance(self) -> dict[str, Any]:
        return {"cash": 1000.0, "equity": 1000.0}

    def get_positions(self) -> list[dict[str, Any]]:
        return [{"instrument": "IF2506.CFFEX", "volume": 1}]

    def place_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "order_id": "ord-1",
            "external_order_id": "ord-1",
            **payload,
        }

    def cancel_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "order_id": payload.get("order_id") or "ord-1",
            "status": "canceled",
        }

    def poll_output(self) -> tuple[str, Any] | None:
        if not self.outputs:
            return None
        return self.outputs.popleft()

    def emit(self, channel: str, payload: Any) -> None:
        self.outputs.append((channel, payload))


class FakeJoinableThread:
    def __init__(self, alive: bool = True) -> None:
        self.alive = alive
        self.join_calls: list[float] = []

    def is_alive(self) -> bool:
        return self.alive

    def join(self, timeout: float | None = None) -> None:
        self.join_calls.append(0.0 if timeout is None else timeout)
        self.alive = False


def _wait_until(predicate, timeout: float = 2.0, interval: float = 0.05) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_gateway_client_remember_order_copies_nested_payload():
    client = GatewayClient(exchange_type="CTP", asset_type="FUTURE", account_id="immut-1")
    payload = {
        "order_id": "ord-immut-1",
        "external_order_id": "ord-immut-1",
        "meta": {"status": "submitted"},
    }

    client._remember_order(payload)
    payload["meta"]["status"] = "mutated"

    assert client.pending_orders["ord-immut-1"]["meta"]["status"] == "submitted"


def test_gateway_client_submit_order_result_is_isolated_from_pending_state(monkeypatch):
    client = GatewayClient(exchange_type="CTP", asset_type="FUTURE", account_id="immut-2")
    monkeypatch.setattr(
        client,
        "_command",
        lambda command, payload=None: {
            "order_id": "ord-immut-2",
            "external_order_id": "ord-immut-2",
            "meta": {"tag": "original"},
        },
    )

    order = client.submit_order({"data_name": "rb2510", "volume": 1})
    order["meta"]["tag"] = "mutated"

    assert client.pending_orders["ord-immut-2"]["meta"]["tag"] == "original"


def test_gateway_client_wait_for_adapter_ready_logs_last_error(monkeypatch, caplog):
    client = GatewayClient(exchange_type="CTP", asset_type="FUTURE", account_id="wait-1")
    client.config.startup_timeout_sec = 0.01

    monkeypatch.setattr(
        client,
        "_command",
        lambda command, payload=None: (_ for _ in ()).throw(RuntimeError("ping failed")),
    )
    monkeypatch.setattr("bt_api_py.gateway.client.time.sleep", lambda _: None)

    with caplog.at_level("WARNING"):
        client._wait_for_adapter_ready()

    assert "Gateway adapter not ready after 0.0s" in caplog.text
    assert "RuntimeError: ping failed" in caplog.text


def test_ib_web_adapter_emits_alias_symbol_from_topic_conid_only():
    adapter = object.__new__(IbWebGatewayAdapter)
    adapter.asset_type = "STK"
    adapter.aliases = defaultdict(set, {265598: {"AAPL", "265598"}})
    emitted: list[tuple[str, Any]] = []
    adapter.emit = lambda channel, payload: emitted.append((channel, payload))

    adapter._emit_market(
        {
            "topic": "smd+265598",
            "31": "212.34",
            "84": "212.30",
            "86": "212.38",
            "87": "100",
        }
    )

    market_payloads = [payload for channel, payload in emitted if channel == CHANNEL_MARKET]
    assert market_payloads
    assert any(payload.symbol == "AAPL" for payload in market_payloads)
    aapl_tick = next(payload for payload in market_payloads if payload.symbol == "AAPL")
    assert aapl_tick.instrument_id == "265598"
    assert aapl_tick.price == 212.34
    assert aapl_tick.bid_price == 212.3
    assert aapl_tick.ask_price == 212.38
    assert aapl_tick.volume == 100.0


def test_gateway_client_connect_cleans_up_after_initial_ping_failure(monkeypatch):
    class FakeSocket:
        def __init__(self) -> None:
            self.closed = False
            self.options: list[tuple[Any, Any]] = []
            self.endpoints: list[str] = []

        def setsockopt(self, option: Any, value: Any) -> None:
            self.options.append((option, value))

        def connect(self, endpoint: str) -> None:
            self.endpoints.append(endpoint)

        def close(self, linger: int = 0) -> None:
            self.closed = True

    class FakeContext:
        def __init__(self) -> None:
            self.sockets: list[FakeSocket] = []

        def socket(self, socket_type: int) -> FakeSocket:
            sock = FakeSocket()
            self.sockets.append(sock)
            return sock

    class FakeRuntime:
        def __init__(self) -> None:
            self.started = False
            self.stopped = False

        def start_in_thread(self) -> None:
            self.started = True

        def stop(self) -> None:
            self.stopped = True

    fake_runtime = FakeRuntime()
    monkeypatch.setattr(
        "bt_api_py.gateway.client.GatewayRuntime", lambda config, **kwargs: fake_runtime
    )

    client = GatewayClient(exchange_type="CTP", asset_type="FUTURE", account_id="connect-fail-1")
    fake_context = FakeContext()
    client.context = fake_context
    monkeypatch.setattr(
        client,
        "_command",
        lambda command, payload=None: (_ for _ in ()).throw(RuntimeError("initial ping failed")),
    )

    with pytest.raises(RuntimeError, match="initial ping failed"):
        client.connect()

    assert fake_runtime.started is True
    assert fake_runtime.stopped is True
    assert client.connected is False
    assert client.runtime is None
    assert client.command_socket is None
    assert client.market_socket is None
    assert client.event_socket is None
    assert len(fake_context.sockets) == 3
    assert all(socket.closed for socket in fake_context.sockets)


def test_gateway_client_disconnect_cleans_up_even_if_socket_close_fails(caplog):
    class FakeSocket:
        def __init__(self, name: str) -> None:
            self.name = name

        def close(self, linger: int = 0) -> None:
            raise RuntimeError(f"{self.name} close failed")

    client = GatewayClient(
        exchange_type="CTP", asset_type="FUTURE", account_id="disconnect-close-1"
    )
    client.connected = True
    client.command_socket = FakeSocket("command")
    client.market_socket = FakeSocket("market")
    client.event_socket = FakeSocket("event")

    with caplog.at_level("WARNING"):
        client.disconnect()

    assert client.connected is False
    assert client.command_socket is None
    assert client.market_socket is None
    assert client.event_socket is None
    assert "Gateway client socket close failed during cleanup" in caplog.text
    assert "command close failed" in caplog.text
    assert "market close failed" in caplog.text
    assert "event close failed" in caplog.text


def test_gateway_client_disconnect_cleans_up_even_if_runtime_stop_fails(caplog):
    class BadRuntime:
        def stop(self) -> None:
            raise RuntimeError("runtime stop failed")

    client = GatewayClient(exchange_type="CTP", asset_type="FUTURE", account_id="disconnect-stop-1")
    client.runtime = BadRuntime()
    client.connected = True

    with caplog.at_level("WARNING"):
        client.disconnect()

    assert client.connected is False
    assert client.runtime is None
    assert "Gateway client runtime stop failed during disconnect" in caplog.text
    assert "runtime stop failed" in caplog.text


def test_gateway_client_disconnect_clears_subscription_and_event_state() -> None:
    client = GatewayClient(
        exchange_type="CTP", asset_type="FUTURE", account_id="disconnect-state-1"
    )
    client.connected = True
    client.subscribed.update({"rb2510"})
    client.tick_queues["rb2510"].append(GatewayTick(timestamp=1.0, symbol="rb2510", price=100.0))
    client.broker_updates.append({"kind": "order", "order_id": "ord-1"})
    client.pending_orders["ord-1"] = {"order_id": "ord-1"}

    client.disconnect()

    assert client.supports_live_ticks("rb2510") is False
    assert client.has_pending_tick("rb2510") is False
    assert client.poll_broker_update() is None
    assert client.pending_orders == {}


def test_gateway_client_connect_failure_clears_subscription_and_event_state(monkeypatch):
    class FakeSocket:
        def setsockopt(self, option: Any, value: Any) -> None:
            return None

        def connect(self, endpoint: str) -> None:
            return None

        def close(self, linger: int = 0) -> None:
            return None

    class FakeContext:
        def socket(self, socket_type: int) -> FakeSocket:
            return FakeSocket()

    client = GatewayClient(
        exchange_type="CTP",
        asset_type="FUTURE",
        account_id="connect-state-1",
        gateway_start_local_runtime=False,
    )
    client.context = FakeContext()
    client.subscribed.update({"rb2510"})
    client.tick_queues["rb2510"].append(GatewayTick(timestamp=1.0, symbol="rb2510", price=100.0))
    client.broker_updates.append({"kind": "order", "order_id": "ord-1"})
    client.pending_orders["ord-1"] = {"order_id": "ord-1"}
    monkeypatch.setattr(
        client,
        "_command",
        lambda command, payload=None: (_ for _ in ()).throw(RuntimeError("initial ping failed")),
    )

    with pytest.raises(RuntimeError, match="initial ping failed"):
        client.connect()

    assert client.supports_live_ticks("rb2510") is False
    assert client.has_pending_tick("rb2510") is False
    assert client.poll_broker_update() is None
    assert client.pending_orders == {}


def test_gateway_runtime_registry_contains_all_exchanges():
    assert GatewayRuntime.get_adapter_class("ctp") is CtpGatewayAdapter
    assert GatewayRuntime.get_adapter_class("IB_WEB") is IbWebGatewayAdapter
    assert GatewayRuntime.get_adapter_class("BINANCE") is BinanceGatewayAdapter
    assert GatewayRuntime.get_adapter_class("binance") is BinanceGatewayAdapter
    assert GatewayRuntime.get_adapter_class("OKX") is OkxGatewayAdapter
    assert GatewayRuntime.get_adapter_class("okx") is OkxGatewayAdapter


def test_gateway_runtime_register_adapter_supports_extension():
    class CustomGatewayAdapter(FakeGatewayAdapter):
        pass

    GatewayRuntime.register_adapter("customx", CustomGatewayAdapter)

    assert GatewayRuntime.get_adapter_class("CUSTOMX") is CustomGatewayAdapter


def test_gateway_runtime_connect_background_updates_health_on_retry(monkeypatch):
    class FlakyGatewayAdapter(FakeGatewayAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.connect_calls = 0

        def connect(self) -> None:
            self.connect_calls += 1
            if self.connect_calls == 1:
                raise RuntimeError("temporary connect failure")
            self.connected = True

    adapter = FlakyGatewayAdapter()
    monkeypatch.setattr(GatewayRuntime, "_create_adapter", lambda self: adapter)
    monkeypatch.setattr("bt_api_py.gateway.runtime.time.sleep", lambda _: None)

    config = GatewayConfig.from_kwargs(
        exchange_type="binance",
        asset_type="swap",
        account_id="acc-retry",
        gateway_runtime_name=f"gw-{uuid.uuid4().hex[:8]}",
        gateway_poll_timeout_ms=10,
    )
    runtime = GatewayRuntime(
        config,
        exchange_type="BINANCE",
        asset_type="SWAP",
        account_id="acc-retry",
    )
    runtime.running = True

    runtime._connect_adapter_background()

    snap = runtime.health.snapshot()
    assert adapter.connect_calls == 2
    assert runtime._adapter_connected is True
    assert snap["market_connection"] == ConnectionState.CONNECTED.value
    assert snap["recent_errors"][-1]["source"] == "adapter_connect"
    assert (
        "attempt 1/3 RuntimeError: temporary connect failure"
        in snap["recent_errors"][-1]["message"]
    )


def test_gateway_runtime_subscribe_tracks_only_adapter_accepted_symbols(monkeypatch):
    class PartialSubscribeAdapter(FakeGatewayAdapter):
        def subscribe_symbols(self, symbols: list[str]) -> dict[str, Any]:
            self.subscriptions.extend(symbols)
            return {
                "symbols": ["EURUSD", "XAUUSD"],
                "skipped_symbols": ["NAS100"],
            }

    adapter = PartialSubscribeAdapter()
    monkeypatch.setattr(GatewayRuntime, "_create_adapter", lambda self: adapter)

    config = GatewayConfig.from_kwargs(
        exchange_type="mt5",
        asset_type="otc",
        account_id="acc-mt5-subscribe",
        gateway_runtime_name=f"gw-{uuid.uuid4().hex[:8]}",
        gateway_poll_timeout_ms=10,
    )
    runtime = GatewayRuntime(
        config,
        exchange_type="MT5",
        asset_type="OTC",
        account_id="acc-mt5-subscribe",
    )
    runtime._adapter_connected = True

    result = runtime._dispatch(
        "subscribe",
        {
            "strategy_id": "quote_page",
            "symbols": ["EURUSD", "NAS100", "XAUUSD"],
        },
    )

    assert result["accepted"] == ["EURUSD", "XAUUSD"]
    assert result["skipped"] == ["NAS100"]
    assert result["subscribed"] == ["EURUSD", "XAUUSD"]
    assert runtime.subscriptions.get_strategy_symbols("quote_page") == {"EURUSD", "XAUUSD"}
    assert runtime.health.snapshot()["symbol_count"] == 2


def test_gateway_runtime_refresh_runtime_heartbeat_without_ping(monkeypatch):
    adapter = FakeGatewayAdapter()
    monkeypatch.setattr(GatewayRuntime, "_create_adapter", lambda self: adapter)

    config = GatewayConfig.from_kwargs(
        exchange_type="binance",
        asset_type="swap",
        account_id="acc-heartbeat",
        gateway_runtime_name=f"gw-{uuid.uuid4().hex[:8]}",
        gateway_poll_timeout_ms=10,
    )
    runtime = GatewayRuntime(
        config,
        exchange_type="BINANCE",
        asset_type="SWAP",
        account_id="acc-heartbeat",
    )
    runtime.running = True
    runtime.health.record_heartbeat = MagicMock()

    monotonic_values = iter([100.0, 100.5, 101.2])
    monkeypatch.setattr("bt_api_py.gateway.runtime.time.monotonic", lambda: next(monotonic_values))

    runtime._refresh_runtime_heartbeat()
    runtime._refresh_runtime_heartbeat()
    runtime._refresh_runtime_heartbeat()

    assert runtime.health.record_heartbeat.call_count == 2


def test_gateway_runtime_stop_records_disconnect_errors(monkeypatch):
    class BadDisconnectAdapter(FakeGatewayAdapter):
        def disconnect(self) -> None:
            raise RuntimeError("disconnect failed")

    adapter = BadDisconnectAdapter()
    monkeypatch.setattr(GatewayRuntime, "_create_adapter", lambda self: adapter)

    config = GatewayConfig.from_kwargs(
        exchange_type="binance",
        asset_type="swap",
        account_id="acc-stop",
        gateway_runtime_name=f"gw-{uuid.uuid4().hex[:8]}",
        gateway_poll_timeout_ms=10,
    )
    runtime = GatewayRuntime(
        config,
        exchange_type="BINANCE",
        asset_type="SWAP",
        account_id="acc-stop",
    )
    runtime._adapter_connected = True
    runtime.health.update_trade_connection(ConnectionState.CONNECTED)

    runtime.stop()

    snap = runtime.health.snapshot()
    assert runtime._adapter_connected is False
    assert snap["state"] == GatewayState.STOPPED.value
    assert snap["market_connection"] == ConnectionState.DISCONNECTED.value
    assert snap["trade_connection"] == ConnectionState.DISCONNECTED.value
    assert snap["recent_errors"][-1]["source"] == "runtime_stop"
    assert "RuntimeError: disconnect failed" in snap["recent_errors"][-1]["message"]


def test_gateway_runtime_start_records_bind_errors(monkeypatch):
    class BindFailingSocket:
        def bind(self, endpoint: str) -> None:
            raise zmq.ZMQError(f"bind failed for {endpoint}")

        def close(self, linger: int = 0) -> None:
            return None

    class BindFailingContext:
        def socket(self, socket_type: int) -> BindFailingSocket:
            return BindFailingSocket()

    adapter = FakeGatewayAdapter()
    monkeypatch.setattr(GatewayRuntime, "_create_adapter", lambda self: adapter)

    config = GatewayConfig.from_kwargs(
        exchange_type="binance",
        asset_type="swap",
        account_id="acc-bind",
        gateway_runtime_name=f"gw-{uuid.uuid4().hex[:8]}",
        gateway_poll_timeout_ms=10,
    )
    runtime = GatewayRuntime(
        config,
        exchange_type="BINANCE",
        asset_type="SWAP",
        account_id="acc-bind",
    )
    runtime.context = BindFailingContext()

    runtime.start()

    snap = runtime.health.snapshot()
    assert runtime.running is False
    assert snap["state"] == GatewayState.ERROR.value
    assert snap["recent_errors"][-1]["source"] == "runtime_start"
    assert "bind failed" in snap["recent_errors"][-1]["message"]


def test_gateway_runtime_main_loop_errors_transition_health_to_error(monkeypatch):
    adapter = FakeGatewayAdapter()
    monkeypatch.setattr(GatewayRuntime, "_create_adapter", lambda self: adapter)

    config = GatewayConfig.from_kwargs(
        exchange_type="binance",
        asset_type="swap",
        account_id="acc-loop",
        gateway_runtime_name=f"gw-{uuid.uuid4().hex[:8]}",
        gateway_poll_timeout_ms=10,
    )
    runtime = GatewayRuntime(
        config,
        exchange_type="BINANCE",
        asset_type="SWAP",
        account_id="acc-loop",
    )
    runtime.health.update_trade_connection(ConnectionState.CONNECTED)
    monkeypatch.setattr(
        runtime,
        "_handle_commands",
        lambda: (_ for _ in ()).throw(zmq.ZMQError("loop exploded")),
    )

    runtime.start()

    snap = runtime.health.snapshot()
    assert runtime.running is False
    assert snap["state"] == GatewayState.ERROR.value
    assert snap["market_connection"] == ConnectionState.ERROR.value
    assert snap["trade_connection"] == ConnectionState.ERROR.value
    assert snap["recent_errors"][-1]["source"] == "runtime_loop"
    assert "loop exploded" in snap["recent_errors"][-1]["message"]


def test_gateway_runtime_failure_records_disconnect_errors(monkeypatch, caplog):
    class BadDisconnectAdapter(FakeGatewayAdapter):
        def disconnect(self) -> None:
            raise RuntimeError("disconnect exploded")

    adapter = BadDisconnectAdapter()
    monkeypatch.setattr(GatewayRuntime, "_create_adapter", lambda self: adapter)

    config = GatewayConfig.from_kwargs(
        exchange_type="binance",
        asset_type="swap",
        account_id="acc-loop-disconnect",
        gateway_runtime_name=f"gw-{uuid.uuid4().hex[:8]}",
        gateway_poll_timeout_ms=10,
    )
    runtime = GatewayRuntime(
        config,
        exchange_type="BINANCE",
        asset_type="SWAP",
        account_id="acc-loop-disconnect",
    )
    monkeypatch.setattr(
        runtime,
        "_handle_commands",
        lambda: (_ for _ in ()).throw(RuntimeError("loop exploded")),
    )

    with caplog.at_level("WARNING"):
        runtime.start()

    snap = runtime.health.snapshot()
    assert snap["state"] == GatewayState.ERROR.value
    assert snap["trade_connection"] == ConnectionState.ERROR.value
    assert snap["recent_errors"][-1]["source"] == "runtime_loop_disconnect"
    assert "RuntimeError: disconnect exploded" in snap["recent_errors"][-1]["message"]
    assert "GatewayRuntime adapter disconnect failed during runtime_loop" in caplog.text


def test_gateway_runtime_start_cleans_up_sockets_after_loop_exit(monkeypatch):
    class FakeSocket:
        def __init__(self) -> None:
            self.closed = False

        def bind(self, endpoint: str) -> None:
            return None

        def close(self, linger: int = 0) -> None:
            self.closed = True

    class FakeContext:
        def __init__(self) -> None:
            self.sockets: list[FakeSocket] = []

        def socket(self, socket_type: int) -> FakeSocket:
            sock = FakeSocket()
            self.sockets.append(sock)
            return sock

    class FakePoller:
        def register(self, socket: Any, flags: Any) -> None:
            return None

        def unregister(self, socket: Any) -> None:
            return None

        def poll(self, timeout: int = 0) -> list[Any]:
            return []

    adapter = FakeGatewayAdapter()
    monkeypatch.setattr(GatewayRuntime, "_create_adapter", lambda self: adapter)
    monkeypatch.setattr(threading.Thread, "start", lambda self: None)

    config = GatewayConfig.from_kwargs(
        exchange_type="binance",
        asset_type="swap",
        account_id="acc-cleanup",
        gateway_runtime_name=f"gw-{uuid.uuid4().hex[:8]}",
        gateway_poll_timeout_ms=10,
    )
    runtime = GatewayRuntime(
        config,
        exchange_type="BINANCE",
        asset_type="SWAP",
        account_id="acc-cleanup",
    )
    fake_context = FakeContext()
    runtime.context = fake_context
    runtime.poller = FakePoller()
    monkeypatch.setattr(runtime, "_handle_commands", lambda: setattr(runtime, "running", False))

    runtime.start()

    assert all(sock.closed for sock in fake_context.sockets)
    assert runtime.command_socket is None
    assert runtime.event_socket is None
    assert runtime.market_socket is None


def test_gateway_runtime_cleanup_sockets_continues_after_close_failure(caplog):
    class FakeSocket:
        def __init__(self, name: str, should_fail: bool = False) -> None:
            self.name = name
            self.should_fail = should_fail
            self.closed = False

        def close(self, linger: int = 0) -> None:
            if self.should_fail:
                raise RuntimeError(f"{self.name} close failed")
            self.closed = True

    class FakePoller:
        def unregister(self, socket: FakeSocket) -> None:
            return None

    config = GatewayConfig.from_kwargs(
        exchange_type="binance",
        asset_type="swap",
        account_id="acc-cleanup-fail",
        gateway_runtime_name=f"gw-{uuid.uuid4().hex[:8]}",
        gateway_poll_timeout_ms=10,
    )
    runtime = GatewayRuntime(
        config,
        exchange_type="BINANCE",
        asset_type="SWAP",
        account_id="acc-cleanup-fail",
    )
    command_socket = FakeSocket("command", should_fail=True)
    event_socket = FakeSocket("event")
    market_socket = FakeSocket("market")
    runtime.command_socket = command_socket
    runtime.event_socket = event_socket
    runtime.market_socket = market_socket
    runtime.poller = FakePoller()

    with caplog.at_level("WARNING"):
        runtime._cleanup_sockets()

    assert event_socket.closed is True
    assert market_socket.closed is True
    assert runtime.command_socket is None
    assert runtime.event_socket is None
    assert runtime.market_socket is None
    assert "GatewayRuntime socket cleanup failed" in caplog.text
    assert "command close failed" in caplog.text


def test_gateway_runtime_client_ipc_roundtrip(monkeypatch, tmp_path):
    adapter = FakeGatewayAdapter()
    monkeypatch.setattr(GatewayRuntime, "_create_adapter", lambda self: adapter)
    gateway_base_dir = "/tmp/btgw"
    runtime_name = f"gw-{uuid.uuid4().hex[:8]}"

    config = GatewayConfig.from_kwargs(
        exchange_type="ctp",
        asset_type="future",
        account_id="acc-1",
        gateway_base_dir=gateway_base_dir,
        gateway_runtime_name=runtime_name,
        gateway_poll_timeout_ms=10,
        gateway_command_timeout_sec=1.0,
    )
    runtime = GatewayRuntime(
        config,
        exchange_type="CTP",
        asset_type="FUTURE",
        account_id="acc-1",
        gateway_base_dir=gateway_base_dir,
        gateway_runtime_name=runtime_name,
    )
    client = GatewayClient(
        exchange_type="CTP",
        asset_type="FUTURE",
        account_id="acc-1",
        gateway_command_endpoint=config.command_endpoint,
        gateway_event_endpoint=config.event_endpoint,
        gateway_market_endpoint=config.market_endpoint,
        gateway_base_dir=gateway_base_dir,
        gateway_runtime_name=runtime_name,
        gateway_poll_timeout_ms=10,
        gateway_command_timeout_sec=1.0,
        gateway_start_local_runtime=False,
    )

    runtime.start_in_thread()
    try:
        client.connect()

        assert adapter.connected is True
        assert client.get_balance()["cash"] == 1000.0
        assert client.get_positions()[0]["instrument"] == "IF2506.CFFEX"
        assert client.subscribe(["IF2506.CFFEX"]) == {
            "subscribed": ["IF2506.CFFEX"],
            "accepted": ["IF2506.CFFEX"],
            "skipped": [],
        }

        order = client.submit_order({"data_name": "IF2506.CFFEX", "volume": 1})
        assert order["order_id"] == "ord-1"
        assert order["data_name"] == "IF2506.CFFEX"

        cancel = client.cancel_order("ord-1", dataname="IF2506.CFFEX")
        assert cancel["status"] == "canceled"

        adapter.emit(
            CHANNEL_MARKET,
            GatewayTick(timestamp=time.time(), symbol="IF2506.CFFEX", price=100.0, volume=2.0),
        )
        assert _wait_until(lambda: client.has_pending_tick("IF2506.CFFEX"))
        tick = client.poll_tick("IF2506.CFFEX")
        assert tick is not None
        assert tick.price == 100.0

        adapter.emit(
            CHANNEL_EVENT,
            {
                "kind": "order",
                "order_id": "ord-1",
                "external_order_id": "ord-1",
                "status": "filled",
            },
        )
        update = None
        deadline = time.time() + 2.0
        while time.time() < deadline:
            update = client.poll_broker_update()
            if update is not None:
                break
            time.sleep(0.05)
        assert update is not None
        assert update["status"] == "filled"
    finally:
        client.disconnect()
        runtime.stop()


def test_ctp_split_normalizes_czce_with_exchange():
    assert ctp_split("CF2609.CZCE") == ("CF609", "CZCE")


def test_ctp_split_normalizes_known_czce_prefix_without_exchange():
    assert ctp_split("TA2609") == ("TA609", "")


def test_ctp_split_does_not_change_cffex_style_symbol_without_exchange():
    assert ctp_split("IF2609") == ("IF2609", "")


# ---------------------------------------------------------------------------
# Binance / OKX adapter unit tests
# ---------------------------------------------------------------------------


class TestBinanceAdapterAssetNormalization:
    def test_swap_default(self):
        assert bn_normalize(None) == "SWAP"
        assert bn_normalize("") == "SWAP"

    def test_swap_explicit(self):
        assert bn_normalize("SWAP") == "SWAP"
        assert bn_normalize("swap") == "SWAP"

    def test_spot(self):
        assert bn_normalize("SPOT") == "SPOT"
        assert bn_normalize("spot") == "SPOT"

    def test_future_maps_to_swap(self):
        assert bn_normalize("FUTURE") == "SWAP"
        assert bn_normalize("FUT") == "SWAP"


class TestOkxAdapterAssetNormalization:
    def test_swap_default(self):
        assert okx_normalize(None) == "SWAP"

    def test_spot(self):
        assert okx_normalize("SPOT") == "SPOT"

    def test_future_maps_to_swap(self):
        assert okx_normalize("FUTURE") == "SWAP"
        assert okx_normalize("FUT") == "SWAP"


def test_binance_adapter_instantiation():
    """BinanceGatewayAdapter can be constructed without network access."""
    adapter = BinanceGatewayAdapter(
        asset_type="SWAP",
        public_key="test_key",
        private_key="test_secret",
    )
    assert adapter.asset_type == "SWAP"
    assert adapter.running is False
    assert adapter.kwargs["exchange_name"] == "BINANCE"
    assert adapter.feed is not None


def test_okx_adapter_instantiation():
    """OkxGatewayAdapter can be constructed without network access."""
    adapter = OkxGatewayAdapter(
        asset_type="SWAP",
        public_key="test_key",
        private_key="test_secret",
        passphrase="test_pass",
    )
    assert adapter.asset_type == "SWAP"
    assert adapter.running is False
    assert adapter.kwargs["exchange_name"] == "OKX"
    assert adapter.feed is not None


def test_binance_adapter_emit_ticker():
    """BinanceGatewayAdapter._emit_ticker produces a GatewayTick on the market channel."""
    from bt_api_py.containers.tickers.binance_ticker import BinanceWssTickerData

    adapter = BinanceGatewayAdapter(
        asset_type="SWAP",
        public_key="k",
        private_key="s",
    )
    ticker_info = {
        "e": "bookTicker",
        "s": "BTCUSDT",
        "E": 1700000000000,
        "b": "42000.0",
        "a": "42001.0",
        "B": "1.5",
        "A": "2.0",
    }
    ticker = BinanceWssTickerData(ticker_info, "BTCUSDT", "SWAP", True)
    adapter._emit_ticker(ticker)

    result = adapter.poll_output()
    assert result is not None
    channel, payload = result
    assert channel == CHANNEL_MARKET
    assert isinstance(payload, GatewayTick)
    assert payload.symbol == "BTCUSDT"
    assert payload.exchange == "BINANCE"
    assert payload.bid_price == 42000.0
    assert payload.ask_price == 42001.0
    assert payload.price == 42000.5


def test_binance_adapter_disconnect_resets_stream_state():
    adapter = BinanceGatewayAdapter(
        asset_type="SWAP",
        public_key="k",
        private_key="s",
    )
    thread = FakeJoinableThread()
    adapter.running = True
    adapter.thread = thread
    adapter.market_stream = object()
    adapter.account_stream = object()
    adapter.aliases["BTCUSDT"].add("BTCUSDT")

    adapter.disconnect()

    assert adapter.running is False
    assert adapter.thread is None
    assert adapter.market_stream is None
    assert adapter.account_stream is None
    assert dict(adapter.aliases) == {}
    assert thread.join_calls == [2.0]


def test_okx_adapter_emit_ticker():
    """OkxGatewayAdapter._emit_ticker produces a GatewayTick on the market channel."""
    from bt_api_py.containers.tickers.okx_ticker import OkxTickerData

    adapter = OkxGatewayAdapter(
        asset_type="SWAP",
        public_key="k",
        private_key="s",
        passphrase="p",
    )
    ticker_info = {
        "instId": "BTC-USDT-SWAP",
        "ts": "1700000000000",
        "bidPx": "42000.0",
        "askPx": "42001.0",
        "bidSz": "1.5",
        "askSz": "2.0",
        "last": "42000.5",
        "lastSz": "0.1",
    }
    ticker = OkxTickerData(ticker_info, "BTC-USDT-SWAP", "SWAP", True)
    adapter._emit_ticker(ticker)

    result = adapter.poll_output()
    assert result is not None
    channel, payload = result
    assert channel == CHANNEL_MARKET
    assert isinstance(payload, GatewayTick)
    assert payload.symbol == "BTC-USDT-SWAP"
    assert payload.exchange == "OKX"
    assert payload.bid_price == 42000.0
    assert payload.ask_price == 42001.0
    assert payload.price == 42000.5


def test_okx_adapter_disconnect_resets_stream_state():
    adapter = OkxGatewayAdapter(
        asset_type="SWAP",
        public_key="k",
        private_key="s",
        passphrase="p",
    )
    thread = FakeJoinableThread()
    adapter.running = True
    adapter.thread = thread
    adapter.market_stream = object()
    adapter.account_stream = object()
    adapter.aliases["BTC-USDT-SWAP"].add("BTC-USDT-SWAP")

    adapter.disconnect()

    assert adapter.running is False
    assert adapter.thread is None
    assert adapter.market_stream is None
    assert adapter.account_stream is None
    assert dict(adapter.aliases) == {}
    assert thread.join_calls == [2.0]


def test_binance_adapter_dispatch_routes_ticker():
    """_dispatch_item routes BinanceWssTickerData to market channel."""
    from bt_api_py.containers.tickers.binance_ticker import BinanceWssTickerData

    adapter = BinanceGatewayAdapter(
        asset_type="SWAP",
        public_key="k",
        private_key="s",
    )
    ticker_info = {
        "e": "bookTicker",
        "s": "ETHUSDT",
        "E": 1700000000000,
        "b": "2000.0",
        "a": "2001.0",
        "B": "10",
        "A": "20",
    }
    ticker = BinanceWssTickerData(ticker_info, "ETHUSDT", "SWAP", True)
    adapter._dispatch_item(ticker)

    result = adapter.poll_output()
    assert result is not None
    assert result[0] == CHANNEL_MARKET
    assert result[1].symbol == "ETHUSDT"


def test_binance_adapter_merges_book_ticker_and_24hr_ticker_fields():
    from bt_api_py.containers.tickers.binance_ticker import BinanceWssTickerData

    adapter = BinanceGatewayAdapter(
        asset_type="SWAP",
        public_key="k",
        private_key="s",
    )

    book_ticker = BinanceWssTickerData(
        {"e": "bookTicker", "s": "BTCUSDT", "E": 1700000000000, "b": "42000.0", "a": "42001.0"},
        "BTCUSDT",
        "SWAP",
        True,
    )
    day_ticker = BinanceWssTickerData(
        {
            "e": "24hrTicker",
            "s": "BTCUSDT",
            "E": 1700000001000,
            "c": "42000.5",
            "o": "41000.0",
            "h": "43000.0",
            "l": "40000.0",
            "v": "123.4",
            "q": "567890.0",
        },
        "BTCUSDT",
        "SWAP",
        True,
    )

    adapter._emit_ticker(book_ticker)
    adapter.poll_output()
    adapter._emit_ticker(day_ticker)

    result = adapter.poll_output()
    assert result is not None
    assert result[0] == CHANNEL_MARKET
    payload = result[1]
    assert payload.symbol == "BTCUSDT"
    assert payload.bid_price == 42000.0
    assert payload.ask_price == 42001.0
    assert payload.price == 42000.5
    assert payload.open_price == 41000.0


def test_okx_adapter_dispatch_routes_ticker():
    """_dispatch_item routes OkxTickerData to market channel."""
    from bt_api_py.containers.tickers.okx_ticker import OkxTickerData

    adapter = OkxGatewayAdapter(
        asset_type="SWAP",
        public_key="k",
        private_key="s",
        passphrase="p",
    )
    ticker_info = {
        "instId": "ETH-USDT-SWAP",
        "ts": "1700000000000",
        "bidPx": "2000.0",
        "askPx": "2001.0",
        "bidSz": "10",
        "askSz": "20",
        "last": "2000.5",
        "lastSz": "1",
    }
    ticker = OkxTickerData(ticker_info, "ETH-USDT-SWAP", "SWAP", True)
    adapter._dispatch_item(ticker)

    result = adapter.poll_output()
    assert result is not None
    assert result[0] == CHANNEL_MARKET
    assert result[1].symbol == "ETH-USDT-SWAP"


def test_backtrader_gateway_wrapper_integration(monkeypatch, tmp_path):
    """Verify the backtrader CtpGatewayClientWrapper works end-to-end with GatewayRuntime.

    This simulates the path backtrader's BtApiStore/BtApiFeed/BtApiBroker would take:
    subscribe -> supports_live_ticks -> poll_tick -> place_order -> poll_broker_update.
    """
    adapter = FakeGatewayAdapter()
    monkeypatch.setattr(GatewayRuntime, "_create_adapter", lambda self: adapter)
    runtime_name = f"bt-{uuid.uuid4().hex[:8]}"

    config = GatewayConfig.from_kwargs(
        exchange_type="ctp",
        asset_type="future",
        account_id="bt-acc",
        gateway_base_dir="/tmp/btgw",
        gateway_runtime_name=runtime_name,
        gateway_poll_timeout_ms=10,
        gateway_command_timeout_sec=1.0,
    )
    runtime = GatewayRuntime(
        config,
        exchange_type="CTP",
        asset_type="FUTURE",
        account_id="bt-acc",
        gateway_base_dir="/tmp/btgw",
        gateway_runtime_name=runtime_name,
    )
    client = GatewayClient(
        exchange_type="CTP",
        asset_type="FUTURE",
        account_id="bt-acc",
        gateway_command_endpoint=config.command_endpoint,
        gateway_event_endpoint=config.event_endpoint,
        gateway_market_endpoint=config.market_endpoint,
        gateway_base_dir="/tmp/btgw",
        gateway_runtime_name=runtime_name,
        gateway_poll_timeout_ms=10,
        gateway_command_timeout_sec=1.0,
        gateway_start_local_runtime=False,
    )
    runtime.start_in_thread()
    try:
        client.connect()

        # 1. subscribe (as backtrader's CtpGatewayClientWrapper.subscribe does)
        result = client.subscribe(["rb2510"])
        assert "rb2510" in result["subscribed"]

        # 2. supports_live_ticks — must reflect subscribed set (was broken before fix)
        assert client.supports_live_ticks("rb2510") is True
        assert client.supports_live_ticks("ag2512") is False

        # 3. get_balance / get_positions (as broker/store would call)
        balance = client.get_balance()
        assert balance["cash"] == 1000.0
        positions = client.get_positions()
        assert len(positions) > 0

        # 4. Adapter emits a tick — verify poll_tick receives it
        adapter.emit(
            CHANNEL_MARKET,
            GatewayTick(timestamp=time.time(), symbol="rb2510", price=3800.0, volume=5.0),
        )
        assert _wait_until(lambda: client.has_pending_tick("rb2510"))
        tick = client.poll_tick("rb2510")
        assert tick is not None
        assert tick.symbol == "rb2510"
        assert tick.price == 3800.0
        # After drain, no more ticks
        assert client.poll_tick("rb2510") is None

        # 5. place_order (as broker would call via submit_order)
        order = client.submit_order(
            {
                "data_name": "rb2510",
                "volume": 1,
                "direction": "buy",
                "price": 3800.0,
                "order_type": "limit",
            }
        )
        assert order["order_id"] == "ord-1"
        assert order["data_name"] == "rb2510"

        # 6. Adapter emits order event — verify poll_broker_update receives it
        adapter.emit(
            CHANNEL_EVENT,
            {
                "kind": "order",
                "order_id": "ord-1",
                "external_order_id": "ord-1",
                "status": "submitted",
                "data_name": "rb2510",
            },
        )
        update = None
        deadline = time.time() + 2.0
        while time.time() < deadline:
            update = client.poll_broker_update()
            if update is not None:
                break
            time.sleep(0.05)
        assert update is not None
        assert update["kind"] == "order"
        assert update["status"] == "submitted"

        # 7. Adapter emits trade event — verify broker receives fill
        adapter.emit(
            CHANNEL_EVENT,
            {
                "kind": "trade",
                "order_id": "ord-1",
                "external_order_id": "ord-1",
                "status": "filled",
                "fill_price": 3800.0,
                "fill_volume": 1,
            },
        )
        fill = None
        deadline = time.time() + 2.0
        while time.time() < deadline:
            fill = client.poll_broker_update()
            if fill is not None:
                break
            time.sleep(0.05)
        assert fill is not None
        assert fill["kind"] == "trade"
        assert fill["fill_price"] == 3800.0

        # 8. cancel_order
        cancel = client.cancel_order("ord-1", dataname="rb2510")
        assert cancel["status"] == "canceled"

    finally:
        client.disconnect()
        runtime.stop()


# ── IB_WEB Adapter Verification ──────────────────────────────────


def test_ib_web_adapter_asset_normalization():
    """IB_WEB adapter normalizes asset types correctly."""
    from bt_api_py.gateway.adapters.ib_web_adapter import _normalize_asset_type as ib_normalize

    assert ib_normalize("STK") == "STK"
    assert ib_normalize("STOCK") == "STK"
    assert ib_normalize("EQUITY") == "STK"
    assert ib_normalize("FUT") == "FUT"
    assert ib_normalize("FUTURE") == "FUT"
    assert ib_normalize(None) == "STK"
    assert ib_normalize("") == "STK"


def test_ib_web_adapter_instantiation():
    """IbWebGatewayAdapter can be constructed without network access."""
    adapter = IbWebGatewayAdapter(
        asset_type="STK",
        account_id="test-ib",
        base_url="https://localhost:5000",
        verify_ssl=False,
    )
    assert adapter.asset_type == "STK"
    assert adapter.feed is not None
    assert adapter.running is False
    assert isinstance(adapter.aliases, dict)


def test_ib_web_adapter_instantiation_future():
    """IbWebGatewayAdapter can be constructed for futures."""
    adapter = IbWebGatewayAdapter(
        asset_type="FUTURE",
        account_id="test-ib-fut",
        base_url="https://localhost:5000",
    )
    assert adapter.asset_type == "FUT"


def test_ib_web_adapter_implements_interface():
    """IbWebGatewayAdapter implements all BaseGatewayAdapter abstract methods."""
    from bt_api_py.gateway.adapters.base import BaseGatewayAdapter

    required = {
        "connect",
        "disconnect",
        "subscribe_symbols",
        "get_balance",
        "get_positions",
        "place_order",
        "cancel_order",
    }
    for method_name in required:
        assert hasattr(IbWebGatewayAdapter, method_name), f"missing {method_name}"
    assert issubclass(IbWebGatewayAdapter, BaseGatewayAdapter)


def test_ib_web_adapter_ipc_roundtrip_with_fake(monkeypatch, tmp_path):
    """IB_WEB via GatewayRuntime IPC roundtrip using FakeGatewayAdapter."""
    adapter = FakeGatewayAdapter()
    monkeypatch.setattr(GatewayRuntime, "_create_adapter", lambda self: adapter)
    runtime_name = f"ib-{uuid.uuid4().hex[:8]}"

    config = GatewayConfig.from_kwargs(
        exchange_type="IB_WEB",
        asset_type="STK",
        account_id="test-ib",
        gateway_base_dir="/tmp/btgw",
        gateway_runtime_name=runtime_name,
        gateway_poll_timeout_ms=10,
        gateway_command_timeout_sec=1.0,
    )
    runtime = GatewayRuntime(
        config,
        exchange_type="IB_WEB",
        asset_type="STK",
        account_id="test-ib",
        gateway_base_dir="/tmp/btgw",
        gateway_runtime_name=runtime_name,
    )
    client = GatewayClient(
        exchange_type="IB_WEB",
        asset_type="STK",
        account_id="test-ib",
        gateway_command_endpoint=config.command_endpoint,
        gateway_event_endpoint=config.event_endpoint,
        gateway_market_endpoint=config.market_endpoint,
        gateway_base_dir="/tmp/btgw",
        gateway_runtime_name=runtime_name,
        gateway_poll_timeout_ms=10,
        gateway_command_timeout_sec=1.0,
        gateway_start_local_runtime=False,
    )
    runtime.start_in_thread()
    try:
        client.connect()
        # L3: 连接 + 订阅 + 账户查询
        assert client.get_balance()["cash"] == 1000.0
        result = client.subscribe(["AAPL"])
        assert "AAPL" in result["subscribed"]
        assert client.supports_live_ticks("AAPL") is True

        positions = client.get_positions()
        assert len(positions) > 0

        # Verify tick delivery
        adapter.emit(
            CHANNEL_MARKET,
            GatewayTick(
                timestamp=time.time(),
                symbol="AAPL",
                price=150.0,
                exchange="IB_WEB",
                asset_type="STK",
            ),
        )
        assert _wait_until(lambda: client.has_pending_tick("AAPL"))
        tick = client.poll_tick("AAPL")
        assert tick is not None
        assert tick.price == 150.0
        assert tick.exchange == "IB_WEB"
    finally:
        client.disconnect()
        runtime.stop()


# ── Binance Adapter Order/Trade Dispatch Verification ────────────


def test_binance_adapter_emit_order():
    """BinanceGatewayAdapter._emit_order produces correct event on event channel."""
    from bt_api_py.containers.orders.binance_order import BinanceSwapWssOrderData

    adapter = BinanceGatewayAdapter(
        asset_type="SWAP",
        public_key="",
        private_key="",
    )
    order_info = {
        "E": 1700000000000,
        "o": {
            "s": "BTCUSDT",
            "i": "12345",
            "c": "client-1",
            "X": "NEW",
            "S": "BUY",
            "p": "42000.0",
            "q": "0.01",
            "z": "0.0",
            "o": "LIMIT",
            "t": 0,
            "T": 1700000000000,
            "R": False,
            "f": "GTC",
            "ap": "0",
            "sp": "0",
            "AP": "0",
            "cr": "0",
            "wt": "CONTRACT_PRICE",
            "ot": "LIMIT",
            "ps": "BOTH",
            "cp": False,
        },
    }
    order = BinanceSwapWssOrderData(order_info, "BTCUSDT", "SWAP", True)
    adapter._dispatch_item(order)

    result = adapter.poll_output()
    assert result is not None
    channel, payload = result
    assert channel == CHANNEL_EVENT
    assert payload["kind"] == "order"
    assert payload["exchange"] == "BINANCE"
    assert payload["symbol"] is not None


def test_binance_adapter_emit_trade():
    """BinanceGatewayAdapter._emit_trade produces correct event on event channel."""
    from bt_api_py.containers.trades.binance_trade import BinanceSwapWssTradeData

    adapter = BinanceGatewayAdapter(
        asset_type="SWAP",
        public_key="",
        private_key="",
    )
    trade_info = {
        "E": 1700000000000,
        "o": {
            "s": "ETHUSDT",
            "t": "99999",
            "i": "12345",
            "c": "cl-1",
            "L": "2000.5",
            "l": "1.0",
            "z": "1.0",
            "S": "BUY",
            "T": 1700000000000,
            "m": False,
            "X": "FILLED",
            "ps": "BOTH",
            "n": "0.01",
            "N": "USDT",
        },
    }
    trade = BinanceSwapWssTradeData(trade_info, "ETHUSDT", "SWAP", True)
    adapter._dispatch_item(trade)

    result = adapter.poll_output()
    assert result is not None
    channel, payload = result
    assert channel == CHANNEL_EVENT
    assert payload["kind"] == "trade"
    assert payload["exchange"] == "BINANCE"


def test_binance_adapter_implements_interface():
    """BinanceGatewayAdapter implements all BaseGatewayAdapter abstract methods."""
    from bt_api_py.gateway.adapters.base import BaseGatewayAdapter

    required = {
        "connect",
        "disconnect",
        "subscribe_symbols",
        "get_balance",
        "get_positions",
        "place_order",
        "cancel_order",
    }
    for method_name in required:
        assert hasattr(BinanceGatewayAdapter, method_name), f"missing {method_name}"
    assert issubclass(BinanceGatewayAdapter, BaseGatewayAdapter)


# ── OKX Adapter Order/Trade Dispatch Verification ────────────────


def test_okx_adapter_emit_order():
    """OkxGatewayAdapter._emit_order produces correct event on event channel."""
    from bt_api_py.containers.orders.okx_order import OkxOrderData

    adapter = OkxGatewayAdapter(
        asset_type="SWAP",
        public_key="",
        private_key="",
        passphrase="",
    )
    order_info = {
        "instId": "BTC-USDT-SWAP",
        "ordId": "ord-123",
        "clOrdId": "cl-1",
        "state": "live",
        "side": "buy",
        "px": "42000",
        "sz": "1",
        "fillSz": "0",
        "ordType": "limit",
        "cTime": "1700000000000",
        "uTime": "1700000000000",
    }
    order = OkxOrderData(order_info, "BTC-USDT-SWAP", "SWAP", True)
    adapter._dispatch_item(order)

    result = adapter.poll_output()
    assert result is not None
    channel, payload = result
    assert channel == CHANNEL_EVENT
    assert payload["kind"] == "order"
    assert payload["exchange"] == "OKX"
    assert payload["order_id"] is not None


def test_okx_adapter_emit_trade():
    """OkxGatewayAdapter._emit_trade produces correct event on event channel."""
    from bt_api_py.containers.trades.okx_trade import OkxWssFillsData

    adapter = OkxGatewayAdapter(
        asset_type="SWAP",
        public_key="",
        private_key="",
        passphrase="",
    )
    trade_info = {
        "instId": "ETH-USDT-SWAP",
        "tradeId": "t-999",
        "ordId": "ord-456",
        "fillPx": "2000.5",
        "fillSz": "1",
        "side": "buy",
        "ts": "1700000000000",
    }
    trade = OkxWssFillsData(trade_info, "ETH-USDT-SWAP", "SWAP", True)
    adapter._dispatch_item(trade)

    result = adapter.poll_output()
    assert result is not None
    channel, payload = result
    assert channel == CHANNEL_EVENT
    assert payload["kind"] == "trade"
    assert payload["exchange"] == "OKX"


def test_okx_adapter_implements_interface():
    """OkxGatewayAdapter implements all BaseGatewayAdapter abstract methods."""
    from bt_api_py.gateway.adapters.base import BaseGatewayAdapter

    required = {
        "connect",
        "disconnect",
        "subscribe_symbols",
        "get_balance",
        "get_positions",
        "place_order",
        "cancel_order",
    }
    for method_name in required:
        assert hasattr(OkxGatewayAdapter, method_name), f"missing {method_name}"
    assert issubclass(OkxGatewayAdapter, BaseGatewayAdapter)


# ── Exchange-Specific IPC Roundtrip Tests ────────────────────────


def test_binance_adapter_ipc_roundtrip_with_fake(monkeypatch, tmp_path):
    """BinanceGatewayAdapter via GatewayRuntime IPC roundtrip using FakeGatewayAdapter."""
    adapter = FakeGatewayAdapter()
    monkeypatch.setattr(GatewayRuntime, "_create_adapter", lambda self: adapter)
    runtime_name = f"bn-{uuid.uuid4().hex[:8]}"

    config = GatewayConfig.from_kwargs(
        exchange_type="BINANCE",
        asset_type="SWAP",
        account_id="test-bn",
        gateway_base_dir="/tmp/btgw",
        gateway_runtime_name=runtime_name,
        gateway_poll_timeout_ms=10,
        gateway_command_timeout_sec=1.0,
    )
    runtime = GatewayRuntime(
        config,
        exchange_type="BINANCE",
        asset_type="SWAP",
        account_id="test-bn",
        gateway_base_dir="/tmp/btgw",
        gateway_runtime_name=runtime_name,
    )
    client = GatewayClient(
        exchange_type="BINANCE",
        asset_type="SWAP",
        account_id="test-bn",
        gateway_command_endpoint=config.command_endpoint,
        gateway_event_endpoint=config.event_endpoint,
        gateway_market_endpoint=config.market_endpoint,
        gateway_base_dir="/tmp/btgw",
        gateway_runtime_name=runtime_name,
        gateway_poll_timeout_ms=10,
        gateway_command_timeout_sec=1.0,
        gateway_start_local_runtime=False,
    )
    runtime.start_in_thread()
    try:
        client.connect()
        assert client.get_balance()["cash"] == 1000.0
        result = client.subscribe(["BTCUSDT"])
        assert "BTCUSDT" in result["subscribed"]
    finally:
        client.disconnect()
        runtime.stop()


def test_okx_adapter_ipc_roundtrip_with_fake(monkeypatch, tmp_path):
    """OkxGatewayAdapter via GatewayRuntime IPC roundtrip using FakeGatewayAdapter."""
    adapter = FakeGatewayAdapter()
    monkeypatch.setattr(GatewayRuntime, "_create_adapter", lambda self: adapter)
    runtime_name = f"okx-{uuid.uuid4().hex[:8]}"

    config = GatewayConfig.from_kwargs(
        exchange_type="OKX",
        asset_type="SWAP",
        account_id="test-okx",
        gateway_base_dir="/tmp/btgw",
        gateway_runtime_name=runtime_name,
        gateway_poll_timeout_ms=10,
        gateway_command_timeout_sec=1.0,
    )
    runtime = GatewayRuntime(
        config,
        exchange_type="OKX",
        asset_type="SWAP",
        account_id="test-okx",
        gateway_base_dir="/tmp/btgw",
        gateway_runtime_name=runtime_name,
    )
    client = GatewayClient(
        exchange_type="OKX",
        asset_type="SWAP",
        account_id="test-okx",
        gateway_command_endpoint=config.command_endpoint,
        gateway_event_endpoint=config.event_endpoint,
        gateway_market_endpoint=config.market_endpoint,
        gateway_base_dir="/tmp/btgw",
        gateway_runtime_name=runtime_name,
        gateway_poll_timeout_ms=10,
        gateway_command_timeout_sec=1.0,
        gateway_start_local_runtime=False,
    )
    runtime.start_in_thread()
    try:
        client.connect()
        assert client.get_balance()["cash"] == 1000.0
        result = client.subscribe(["BTC-USDT-SWAP"])
        assert "BTC-USDT-SWAP" in result["subscribed"]
    finally:
        client.disconnect()
        runtime.stop()
