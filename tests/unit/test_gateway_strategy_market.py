"""Owner-thread market facade, retained poll result and cleanup behavior."""

from dataclasses import FrozenInstanceError

import pytest
from bt_api_base.service_protocol import WireScope

from bt_api_py import gateway_strategy_market as module
from bt_api_py.gateway_strategy_market import (
    GatewayMarketClientSettings,
    GatewayMarketOwner,
    GatewayMarketSubscriptionClient,
    GatewayMarketSubscriptionSettings,
)
from bt_api_py.service_market import MarketBootstrap, ServiceMarketError
from bt_api_py.service_runtime import ClientCurveKeys
from bt_api_py.service_runtime_thread import OwnerOperationError, ThreadedRuntimeError
from tests.unit.test_service_market import (
    _KEY,
    _OPERATIONS,
    FakeSubscriberFactory,
    _make_client,
    _test_source_descriptor,
)

_OPERATIONS = _OPERATIONS | frozenset(
    {"market.bootstrap_begin", "market.bootstrap_status", "market.bootstrap_cancel"}
)


@pytest.fixture
def settings():
    return GatewayMarketClientSettings(
        {"main": GatewayMarketSubscriptionSettings(_KEY)}, _OPERATIONS
    )


def test_settings_copy_and_explicit_permissions():
    original = {"main": GatewayMarketSubscriptionSettings(_KEY)}
    settings = GatewayMarketClientSettings(original, _OPERATIONS)
    original.clear()
    assert list(settings.subscriptions) == ["main"]
    with pytest.raises(TypeError):
        settings.subscriptions["other"] = settings.subscriptions["main"]
    with pytest.raises(FrozenInstanceError):
        settings.subscriptions["main"].queue_capacity = 10
    with pytest.raises(ValueError, match="INVALID_REQUESTED_OPERATIONS"):
        GatewayMarketClientSettings(
            {"main": GatewayMarketSubscriptionSettings(_KEY)}, frozenset({"market.subscribe"})
        )
    with pytest.raises(ValueError, match="INVALID_MARKET_SETTINGS"):
        GatewayMarketClientSettings(
            {"bad.name": GatewayMarketSubscriptionSettings(_KEY)}, _OPERATIONS
        )
    with pytest.raises(ValueError, match="DUPLICATE_MARKET_KEY"):
        GatewayMarketClientSettings(
            {
                "one": GatewayMarketSubscriptionSettings(_KEY),
                "two": GatewayMarketSubscriptionSettings(_KEY),
            },
            _OPERATIONS,
        )


def test_default_async_and_explicit_legacy_permissions():
    subscriptions = {"main": GatewayMarketSubscriptionSettings(_KEY)}
    default = GatewayMarketClientSettings(subscriptions)
    assert default.requested_operations == module._REQUIRED
    legacy = frozenset(
        {
            "market.subscribe",
            "market.unsubscribe",
            "market.snapshot",
            "market.head",
            "market.read_after",
        }
    )
    explicit = GatewayMarketClientSettings(subscriptions, legacy)
    assert explicit.requested_operations == legacy
    for operation in module._ASYNC_OPERATIONS:
        with pytest.raises(ValueError, match="INVALID_REQUESTED_OPERATIONS"):
            GatewayMarketClientSettings(subscriptions, legacy | {operation})
    assert (
        GatewayMarketClientSettings(
            subscriptions, legacy | module._ASYNC_OPERATIONS
        ).requested_operations
        == default.requested_operations
    )


def test_bootstrap_codec_preserves_all_fields_and_rejects_bad_shape():
    bootstrap = MarketBootstrap(
        None,
        "latest_event",
        False,
        False,
        _KEY.stream_id,
        "market-epoch-one",
        7,
        "market-epoch-one",
        5,
        True,
        _test_source_descriptor(),
    )
    wire = module._bootstrap_to_wire(bootstrap)
    assert len(wire) == 11
    assert module._bootstrap_from_wire(wire) == bootstrap
    wire["snapshot_sequence"] = True
    with pytest.raises(ValueError, match="INVALID_MARKET_BOOTSTRAP"):
        module._bootstrap_from_wire(wire)


def test_real_market_client_with_synthetic_registry(tmp_path, settings, monkeypatch):
    unused, raw, registry, market, _, _ = _make_client(tmp_path)
    subscribers = FakeSubscriberFactory()
    monkeypatch.setattr(module, "ZmqMarketTransportFactory", lambda profiles: subscribers)
    owner = GatewayMarketOwner(
        settings,
        stream_scope=WireScope("paper:market-unit"),
        security_profiles={"profile": ClientCurveKeys(b"a" * 40, b"b" * 40, b"c" * 40)},
    )
    token = "a" * 32

    def invoke(method, args=None):
        data = dict(args or {})
        if method != "status":
            data["_consumer_token"] = token
        return owner.invoke("main." + method, data)

    try:
        owner.start(raw)
        assert market.starts == {}
        bootstrap = module._bootstrap_from_wire(invoke("start"))
        assert bootstrap.stream_id == _KEY.stream_id
        assert bootstrap.snapshot is None
        assert invoke("poll") is None
        state = invoke("status")
        assert state["healthy"] and not state["recovery_required"]
        assert module._bootstrap_from_wire(invoke("recover")).stream_id == _KEY.stream_id
        owner._clients["main"]._interrupt_authorization("MARKET_KEY_DENIED")
        assert invoke("status")["authorization_interrupted"] == "MARKET_KEY_DENIED"
        with pytest.raises(OwnerOperationError, match="MARKET_AUTHORIZATION_INTERRUPTED"):
            invoke("poll")
        with pytest.raises(OwnerOperationError, match="MARKET_AUTHORIZATION_INTERRUPTED"):
            invoke("recover")
        invoke("resync_after_regrant")
        assert invoke("status")["authorization_generation"] == 1
        with pytest.raises(OwnerOperationError):
            invoke("poll", {"timeout_ms": 1})
        with pytest.raises(OwnerOperationError, match="INVALID_CONSUMER_TOKEN"):
            owner.invoke("main.poll", {})
        with pytest.raises(OwnerOperationError, match="CONSUMER_ALREADY_BOUND"):
            owner.invoke("main.poll", {"_consumer_token": "b" * 32})
        event = market.publish(_KEY, "codec-failure")
        subscribers.created[-1].events.append(event)
        monkeypatch.setattr(module, "envelope_to_wire_mapping", lambda event: 1 / 0)
        with pytest.raises(OwnerOperationError, match="LOCAL_RESULT_GAP"):
            invoke("poll")
        assert invoke("status")["local_result_gap"] is True
        for args in ({}, {"_consumer_token": "b" * 32}, {"_consumer_token": token}):
            with pytest.raises(OwnerOperationError, match="LOCAL_RESULT_GAP"):
                owner.invoke("main.poll", args)
        owner.close()
        assert all(subscriber.closed for subscriber in subscribers.created)
    finally:
        raw.close()
        registry.close()


class Handle:
    def __init__(self, result=None, errors=()):
        self.value = result
        self.errors = list(errors)
        self.calls = 0

    def result(self, *, timeout_seconds):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return self.value


class Runtime:
    def __init__(self, handle):
        self.handle = handle
        self.calls = []

    def submit_owner(self, operation, arguments):
        self.calls.append((operation, arguments))
        return self.handle


def test_timeout_keeps_same_handle_and_no_second_poll():
    handle = Handle(None, [ThreadedRuntimeError("OWNER_TIMEOUT_RUNNING")])
    runtime = Runtime(handle)
    client = GatewayMarketSubscriptionClient(runtime, "main")
    with pytest.raises(ThreadedRuntimeError, match="OWNER_TIMEOUT_RUNNING"):
        client.poll(timeout_seconds=0.01)
    with pytest.raises(ThreadedRuntimeError, match="OWNER_OPERATION_PENDING"):
        client.recover()
    assert client.poll() is None
    assert runtime.calls == [("market.main.poll", {"_consumer_token": client._consumer_token})]
    assert handle.calls == 2


@pytest.mark.parametrize(
    "error", [ThreadedRuntimeError("INVALID_OWNER_RESULT"), OwnerOperationError("LOCAL_RESULT_GAP")]
)
def test_result_gap_preserves_handle_and_blocks_further_io(error):
    handle = Handle(errors=[error])
    runtime = Runtime(handle)
    client = GatewayMarketSubscriptionClient(runtime, "main")
    with pytest.raises(type(error)):
        client.poll()
    assert client._result_gap_handle is handle
    for method in (client.poll, client.start, client.recover, client.resync_after_regrant):
        with pytest.raises(ThreadedRuntimeError, match="LOCAL_RESULT_GAP"):
            method()
    assert len(runtime.calls) == 1


def test_decode_failure_is_terminal_gap():
    runtime = Runtime(Handle({"bad": "envelope"}))
    client = GatewayMarketSubscriptionClient(runtime, "main")
    with pytest.raises(ThreadedRuntimeError, match="LOCAL_RESULT_GAP"):
        client.poll()
    with pytest.raises(ThreadedRuntimeError, match="LOCAL_RESULT_GAP"):
        client.poll()
    assert len(runtime.calls) == 1


def test_invalid_timeout_never_enqueues():
    runtime = Runtime(Handle())
    client = GatewayMarketSubscriptionClient(runtime, "main")
    with pytest.raises(ValueError):
        client.poll(timeout_seconds=float("nan"))
    assert runtime.calls == []


def test_pid_check_precedes_held_lock_and_concurrent_calls_fail(monkeypatch):
    runtime = Runtime(Handle())
    client = GatewayMarketSubscriptionClient(runtime, "main")
    client._lock.acquire()
    try:
        with pytest.raises(ThreadedRuntimeError, match="OWNER_OPERATION_PENDING"):
            client.poll()
        monkeypatch.setattr(module.os, "getpid", lambda: client._owner_pid + 1)
        with pytest.raises(ThreadedRuntimeError, match="WRONG_PROCESS"):
            client.poll()
    finally:
        client._lock.release()
    assert runtime.calls == []


def test_fork_with_held_caller_lock_rejects_without_waiting():
    import os
    import select
    import signal

    if not hasattr(os, "fork"):
        pytest.skip("fork unavailable")
    client = GatewayMarketSubscriptionClient(Runtime(Handle()), "main")
    read_fd, write_fd = os.pipe()
    client._lock.acquire()
    child = os.fork()
    if child == 0:
        os.close(read_fd)
        try:
            client.poll()
        except ThreadedRuntimeError as error:
            os.write(write_fd, error.code.encode("ascii"))
        finally:
            os._exit(0)
    os.close(write_fd)
    try:
        ready, _, _ = select.select([read_fd], [], [], 2)
        if not ready:
            os.kill(child, signal.SIGKILL)
            pytest.fail("forked caller blocked on inherited lock")
        assert os.read(read_fd, 128) == b"WRONG_PROCESS"
    finally:
        os.close(read_fd)
        os.waitpid(child, 0)
        client._lock.release()


def test_close_retries_local_failure_and_keeps_remote_uncertainty(settings):
    class CloseClient:
        def __init__(self, outcome):
            self.outcome = outcome
            self.unsubscription_uncertain = False
            self.calls = 0

        def close(self):
            self.calls += 1
            if self.calls == 1:
                raise ServiceMarketError(self.outcome)

    owner = GatewayMarketOwner(
        settings,
        stream_scope=WireScope("paper:market-unit"),
        security_profiles={"profile": ClientCurveKeys(b"a" * 40, b"b" * 40, b"c" * 40)},
    )
    local, uncertain = CloseClient("LOCAL_CLOSE_FAILED"), CloseClient("UNSUBSCRIPTION_UNCERTAIN")
    owner._clients = {"a": local, "b": uncertain}
    with pytest.raises(OwnerOperationError, match="LOCAL_CLOSE_FAILED"):
        owner.close()
    assert owner.cleanup_uncertainties == 1
    assert list(owner._clients) == ["a"]
    owner.close()
    assert local.calls == 2
    assert owner.cleanup_uncertainties == 1


def test_real_threaded_owner_poll_timeout_retains_dequeued_event(tmp_path, settings, monkeypatch):
    import threading

    from bt_api_py.gateway_strategy_market import GatewayStrategyMarketClient
    from bt_api_py.service_market import ServiceMarketClient
    from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime

    holder = {}
    subscribers = FakeSubscriberFactory()
    entered, release = threading.Event(), threading.Event()
    original_poll = ServiceMarketClient.poll
    count = []

    def blocked_poll(client, timeout_ms=0):
        result = original_poll(client, timeout_ms=timeout_ms)
        count.append(1)
        entered.set()
        assert release.wait(3)
        return result

    def runtime_factory():
        unused, raw, registry, market, _, _ = _make_client(tmp_path)
        holder.update(raw=raw, registry=registry, market=market)
        raw.start = lambda: None
        return raw

    monkeypatch.setattr(module, "ZmqMarketTransportFactory", lambda profiles: subscribers)
    monkeypatch.setattr(ServiceMarketClient, "poll", blocked_poll)

    class Composite:
        def __init__(self):
            self.market = GatewayMarketOwner(
                settings,
                stream_scope=WireScope("paper:market-unit"),
                security_profiles={"profile": ClientCurveKeys(b"a" * 40, b"b" * 40, b"c" * 40)},
            )

        def start(self, raw):
            self.market.start(raw)

        def invoke(self, operation, arguments):
            assert operation.startswith("market.")
            return self.market.invoke(operation[len("market.") :], arguments)

        def close(self):
            self.market.close()

        @property
        def cleanup_uncertainties(self):
            return self.market.cleanup_uncertainties

    owner = Composite
    runtime = ThreadedServiceClientRuntime(
        runtime_factory, owner_extension_factory=owner, monotonic_clock=lambda: 0.0
    )
    try:
        runtime.start()
        client = GatewayStrategyMarketClient(runtime, settings).subscription("main")
        other = GatewayStrategyMarketClient(runtime, settings).subscription("main")
        client.start()
        event = holder["market"].publish(_KEY, "dequeued")
        subscribers.created[0].events.append(event)
        with pytest.raises(ThreadedRuntimeError, match="OWNER_TIMEOUT_RUNNING"):
            client.poll(timeout_seconds=0.05)
        assert entered.is_set()
        with pytest.raises(ThreadedRuntimeError, match="OWNER_OPERATION_PENDING"):
            client.recover()
        release.set()
        for method in (other.start, other.poll):
            with pytest.raises(OwnerOperationError, match="CONSUMER_ALREADY_BOUND"):
                method()
        received = client.poll()
        assert received.header.sequence == event.header.sequence
        assert received.payload["data"]["marker"] == "dequeued"
        assert len(count) == 1
    finally:
        release.set()
        runtime.close()
        if "registry" in holder:
            holder["registry"].close()
