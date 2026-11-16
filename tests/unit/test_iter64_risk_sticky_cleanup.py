"""Synthetic host lifecycle checks; no external host, Gateway, or account is used."""

from __future__ import annotations

import threading
import weakref
from pathlib import Path
from types import SimpleNamespace

import pytest

from bt_api_py import risk_service_host as module


class StopWait(BaseException):
    pass


@pytest.fixture
def host(monkeypatch):
    class Config:
        strict_admin_cancel = False

    monkeypatch.setattr(module, "RiskServiceConfig", Config)
    monkeypatch.setattr(module, "_RETAINED_HOSTS", set())
    monkeypatch.setattr(module, "_PROBE_STATE", {"active": False, "uncertain": []})
    monkeypatch.setattr(module, "_ENTRY_PROOFS", weakref.WeakKeyDictionary())
    monkeypatch.setattr(module, "_STORAGE_ACTIVE_ROOTS", {})
    monkeypatch.setattr(module, "_STORAGE_INIT_ROOTS", {})
    value = module.RiskServiceHost(Config())
    value._storage_owner = threading.current_thread()
    value.events = []
    value._stack = opened_storage_stack(value)
    yield value


def opened_storage_stack(value):
    """Build the exact stack/manager shape that a completed open_storage leaves.

    The host records one active root: its own stack holds the acquiring stack,
    which holds the single fenced-journal manager. A duck-typed substitute is
    not enough: closing on an unproven stack is refused by design.
    """
    from contextlib import ExitStack

    from bt_api_risk.risk_order_journal import RiskFenceExitCleanup

    journal = SimpleNamespace(
        prepare_borrowed_fence_close=lambda *_args: None,
        consume_borrowed_fence_close=lambda *_args: RiskFenceExitCleanup.COMPLETE,
    )
    fence = object()

    class Inner:
        def __enter__(self):
            return journal, fence

        def __exit__(self, *_args):
            value.events.append("stack")
            return False

    inner = Inner()
    manager = module._FencedJournalManager(Path("synthetic-risk.sqlite"), object())
    manager._entry_attempted = True
    manager._inner_manager = inner
    manager._binding = (inner, journal, fence)
    acquiring = ExitStack()
    # This is the entry ExitStack.enter_context records for the manager.
    acquiring._exit_callbacks.append((True, manager.__exit__))
    stack = ExitStack()
    stack._exit_callbacks.append((True, acquiring.__exit__))
    module._STORAGE_ACTIVE_ROOTS[id(value)] = (value, acquiring, manager, journal, fence)
    return stack


def snapshot():
    return SimpleNamespace(state="closed", thread_alive=False, error_code=None,
                           unregister_uncertainties=0, owner_cleanup_uncertainties=0,
                           unknown_registration_count=0)


class Service:
    def __init__(self, host, state="started", result="closed", raises=False):
        self.host, self.state, self.result, self.raises = host, state, result, raises

    def close(self):
        self.host.events.append("service")
        if self.raises:
            raise RuntimeError("private service exception")
        self.state = self.result


class Port:
    cleanup_uncertain = False

    def __init__(self, host, result=True):
        self.host, self.result = host, result

    def close(self):
        self.host.events.append("port")
        if self.result == "raise":
            raise RuntimeError("private port exception")
        return self.result


class Runtime:
    def __init__(self, host, name, result):
        self.host, self.name, self.result = host, name, result

    def close(self, **_kwargs):
        self.host.events.append(self.name)
        if self.result == "raise":
            raise RuntimeError("private runtime exception")
        return self.result


def attach(host):
    host.service_host = Service(host)
    host.order_port = Port(host)
    host._runtimes = [Runtime(host, "first", snapshot()), Runtime(host, "last", snapshot())]


def assert_sticky(host, events):
    assert host.close() is False
    assert host.events == events
    assert host in module._RETAINED_HOSTS
    assert host.close() is False
    assert host.events == events


def test_success_closes_in_order_once(host):
    attach(host)
    assert host.close() is True
    assert host.events == ["service", "port", "last", "first", "stack"]
    assert host.close() is True
    assert host.events == ["service", "port", "last", "first", "stack"]
    assert host not in module._RETAINED_HOSTS


def test_new_service_is_closed_before_other_resources(host):
    attach(host)
    host.service_host.state = "new"
    assert host.close() is True
    assert host.events == ["service", "port", "last", "first", "stack"]


def test_start_attempt_left_in_new_state_is_uncertain(host):
    attach(host)
    host.service_host.state = "new"
    host._service_host_start_attempted = True
    assert_sticky(host, [])


@pytest.mark.parametrize("state", ["close_failed", "unknown", None, 1])
def test_service_already_uncertain_never_reclosed(host, state):
    attach(host)
    host.service_host.state = state
    assert_sticky(host, [])


@pytest.mark.parametrize("state", ["failed", "closed"])
def test_service_completed_start_cleanup_is_not_reclosed(host, state):
    attach(host)
    host.service_host.state = state
    assert host.close() is True
    assert host.events == ["port", "last", "first", "stack"]


@pytest.mark.parametrize("raises,result", [(True, "closed"), (False, "close_failed"),
                                          (False, "unknown"), (False, True)])
def test_service_close_failure_stops_all_dependencies(host, raises, result, caplog):
    attach(host)
    host.service_host.raises, host.service_host.result = raises, result
    assert_sticky(host, ["service"])
    assert "private service exception" not in caplog.text


@pytest.mark.parametrize("result", [False, None, 1, "raise"])
def test_port_requires_exact_true(host, result, caplog):
    attach(host)
    host.order_port.result = result
    assert_sticky(host, ["service", "port"])
    assert "private port exception" not in caplog.text


@pytest.mark.parametrize("result", [False, None, 1, "raise"])
def test_runtime_requires_confirmed_snapshot(host, result, caplog):
    attach(host)
    host._runtimes[-1].result = result
    assert_sticky(host, ["service", "port", "last"])
    assert "private runtime exception" not in caplog.text


def test_already_unresolved_runtime_prevents_any_new_cleanup(host):
    attach(host)
    old = Runtime(host, "old", snapshot())
    host._delegate_cleanup_unresolved.append(old)
    assert_sticky(host, [])
    assert host._delegate_cleanup_unresolved == [old]


def test_stack_exception_sticky_without_claiming_fence(host, caplog):
    attach(host)
    def fail():
        host.events.append("stack")
        raise RuntimeError("private stack exception")
    host._stack.close = fail
    assert_sticky(host, ["service", "port", "last", "first", "stack"])
    assert host._storage_cleanup_uncertain is True
    assert "private stack exception" not in caplog.text


def test_wrong_owner_or_process_does_not_consume_first_close(host):
    attach(host)
    results = []
    worker = threading.Thread(target=lambda: results.append(host.close()))
    worker.start()
    worker.join()
    assert results == [False] and host.events == []
    creator_pid = host._creator_pid
    host._creator_pid = -1
    assert host.close() is False and host.events == []
    host._creator_pid = creator_pid
    assert host.close() is True
    assert host.events == ["service", "port", "last", "first", "stack"]


def test_component_owner_prevents_wrong_thread_close_before_storage(host):
    from contextlib import ExitStack

    attach(host)
    # "Before storage": there is no acquiring stack or active root yet, so the
    # component owner is the only thing that can refuse the wrong-thread close.
    host._stack = ExitStack()
    module._STORAGE_ACTIVE_ROOTS.pop(id(host), None)
    host._storage_owner = None
    host._claim_resource_owner()
    results = []
    worker = threading.Thread(target=lambda: results.append(host.close()))
    worker.start()
    worker.join()
    assert results == [False] and host.events == []
    assert host.close() is True
    assert host.events == ["service", "port", "last", "first"]


def test_start_failure_keeps_original_exception_and_never_recloses(host, monkeypatch):
    attach(host)
    host.service_host.state = "close_failed"
    monkeypatch.setattr(module, "fault_point_from_environment", lambda _: None)
    monkeypatch.setattr(host, "open_storage", lambda: None)
    monkeypatch.setattr(host, "build_engine", lambda: None)
    monkeypatch.setattr(module, "bootstrap_policy", lambda *_: None)
    host.rules = object()
    host.engine = SimpleNamespace(recover=lambda: None)
    host.pump = SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(host, "start_gateway", lambda: None)
    error = RuntimeError("original private error")
    def fail_start():
        raise error
    monkeypatch.setattr(host, "start_service_host", fail_start)
    with pytest.raises(RuntimeError) as exc:
        host.start()
    assert exc.value is error
    assert host._cleanup_state == "uncertain"
    with pytest.raises(StopWait):
        module._wait_for_cleanup(host, _wait=lambda: (_ for _ in ()).throw(StopWait()))
    assert host.events == []


def test_successful_wait_returns_without_waiting(monkeypatch):
    monkeypatch.setattr(module, "_RETAINED_HOSTS", set())
    host = SimpleNamespace(close=lambda: True)
    module._wait_for_cleanup(host, _wait=lambda: pytest.fail("unexpected wait"))
    assert not module._RETAINED_HOSTS


def test_wait_does_not_call_close_after_runtime_latch(host, monkeypatch):
    host._record_cleanup_uncertain(object(), "SYNTHETIC")
    monkeypatch.setattr(host, "close", lambda: pytest.fail("unexpected retry"))
    with pytest.raises(StopWait):
        module._wait_for_cleanup(host, _wait=lambda: (_ for _ in ()).throw(StopWait()))


@pytest.mark.parametrize("result", [False, None, 1, "raise"])
def test_wait_never_retries_and_requires_exact_true(result, monkeypatch):
    monkeypatch.setattr(module, "_RETAINED_HOSTS", set())
    events = []
    class Host:
        def close(self):
            events.append("close")
            if result == "raise":
                raise RuntimeError("private wait exception")
            return result
    host = Host()
    def stop():
        events.append("wait")
        raise StopWait
    with pytest.raises(StopWait):
        module._wait_for_cleanup(host, _wait=stop)
    assert events == ["close", "wait"]
    assert host in module._RETAINED_HOSTS


@pytest.mark.parametrize("command", ["run", "status"])
def test_cli_finally_does_not_repeat_attempted_cleanup(host, monkeypatch, command):
    attach(host)
    host.order_port.result = False
    host.config.risk_database = SimpleNamespace(is_file=lambda: True)
    monkeypatch.setattr(module, "load_risk_service_config", lambda _: host.config)
    monkeypatch.setattr(module, "RiskServiceHost", lambda _: host)
    monkeypatch.setattr(module.signal, "signal", lambda *_args: None)
    original_wait = module._wait_for_cleanup
    def stop():
        raise StopWait
    monkeypatch.setattr(module, "_wait_for_cleanup", lambda h: original_wait(h, _wait=stop))
    def fail():
        assert host.close() is False
        raise module.RiskServiceStartError("SYNTHETIC_FAILURE")
    monkeypatch.setattr(host, "start" if command == "run" else "open_storage", fail)
    with pytest.raises(StopWait):
        module.main([command, "--config", "synthetic"])
    assert host.events == ["service", "port"]


@pytest.mark.parametrize("result", [None, False, "raise", "interrupt"])
def test_observer_cleanup_failure_latches_original_runtime(host, result):
    class Observer:
        def status(self):
            return SimpleNamespace(state="failed")
        def close(self, **_kwargs):
            host.events.append("observer")
            if result == "raise":
                raise RuntimeError("private observer error")
            if result == "interrupt":
                raise StopWait
            return result
    runtime = Observer()
    host.config.maintain_seconds = 1
    host.config.call_timeout_seconds = 1
    host._monotonic = lambda: 1
    host._observer_runtime = runtime
    host._observer_route = object()
    host._runtimes = [runtime]
    host.pump = SimpleNamespace(_gap=lambda _: host.events.append("gap"))
    if result == "interrupt":
        with pytest.raises(StopWait):
            host._reconnect_observer()
    else:
        host._reconnect_observer()
    assert host._cleanup_uncertain is True
    assert any(item is runtime for item in host._cleanup_uncertain_resources)
    assert host._observer_runtime is runtime and host._runtimes == [runtime]
    assert_sticky(host, ["gap", "observer"])
    with pytest.raises(module.RiskServiceStartError):
        host._reconnect_observer()
    assert host.events == ["gap", "observer"]


@pytest.mark.parametrize("start_result,close_result", [("raise", False), (None, False),
                                                       ("raise", "interrupt")])
def test_replacement_start_failure_with_unconfirmed_cleanup(host, monkeypatch, start_result, close_result):
    class Old:
        def status(self):
            return SimpleNamespace(state="failed")
        def close(self, **_kwargs):
            host.events.append("old")
            return snapshot()
    class Replacement:
        def start(self, **_kwargs):
            host.events.append("start")
            if start_result == "raise":
                raise RuntimeError("private start error")
            return start_result
        def close(self, **_kwargs):
            host.events.append("replacement")
            if close_result == "interrupt":
                raise StopWait
            return close_result
    old, replacement = Old(), Replacement()
    host.config.maintain_seconds = 1
    host.config.call_timeout_seconds = 1
    host._monotonic = lambda: 1
    host._observer_runtime = old
    host._observer_route = SimpleNamespace(scope=object())
    host._runtimes = [old]
    host.pump = SimpleNamespace(_gap=lambda _: host.events.append("gap"))
    monkeypatch.setattr(host, "_service_runtime", lambda *_args, **_kwargs: replacement)
    monkeypatch.setattr(module, "_granted", lambda *_args: False)
    if close_result == "interrupt":
        with pytest.raises(StopWait):
            host._reconnect_observer()
    else:
        host._reconnect_observer()
    assert host._cleanup_uncertain is True
    assert any(item is replacement for item in host._cleanup_uncertain_resources)
    assert host._observer_runtime is replacement
    assert_sticky(host, ["gap", "old", "start", "replacement"])


def test_confirmed_failed_replacement_can_be_replaced_later(host, monkeypatch):
    class Replacement:
        def __init__(self):
            self.count = 0
        def start(self, **_kwargs):
            host.events.append("start")
            raise RuntimeError("synthetic failure")
        def close(self, **_kwargs):
            self.count += 1
            host.events.append("cleanup")
            return snapshot()
    host.config.maintain_seconds = 1
    host.config.call_timeout_seconds = 1
    clock = [1]
    host._monotonic = lambda: clock[0]
    host._observer_route = SimpleNamespace(scope=object())
    host.pump = SimpleNamespace(_gap=lambda _: host.events.append("gap"))
    made = []
    def create(*_args, **_kwargs):
        obj = Replacement()
        made.append(obj)
        return obj
    monkeypatch.setattr(host, "_service_runtime", create)
    host._reconnect_observer()
    clock[0] = 3
    host._reconnect_observer()
    assert len(made) == 2 and all(item.count == 1 for item in made)
    assert host._cleanup_uncertain is False
    assert host._runtimes == [] and host._observer_runtime is None


def test_delegate_failed_start_latches_original_runtime_without_retries(host, monkeypatch):
    class Delegate:
        def start(self, **_kwargs):
            host.events.append("start")
            raise RuntimeError("private start error")
        def close(self, **_kwargs):
            host.events.append("delegate")
            return None
    delegate = Delegate()
    host.config.call_timeout_seconds = 1
    monkeypatch.setattr(host, "_service_runtime", lambda *_args, **_kwargs: delegate)
    route = SimpleNamespace(scope=SimpleNamespace(run_id="synthetic"))
    with pytest.raises(RuntimeError, match="private start error"):
        host._connect_delegate(route)
    assert any(item is delegate for item in host._cleanup_uncertain_resources)
    assert host._delegate_cleanup_unresolved == [delegate]
    assert_sticky(host, ["start", "delegate"])
    with pytest.raises(module.RiskServiceStartError):
        host._connect_delegate(route)
    assert host.events == ["start", "delegate"]


def test_runtime_close_reentrancy_is_rejected_before_any_component(host):
    attach(host)
    class Observer:
        def status(self):
            return SimpleNamespace(state="failed")
        def close(self, **_kwargs):
            host.events.append("observer")
            assert host.close() is False
            with pytest.raises(module.RiskServiceStartError):
                host._reconnect_observer()
            return False
    runtime = Observer()
    host.config.maintain_seconds = 1
    host.config.call_timeout_seconds = 1
    host._monotonic = lambda: 1
    host._observer_runtime = runtime
    host._observer_route = object()
    host.pump = SimpleNamespace(_gap=lambda _: host.events.append("gap"))
    host._reconnect_observer()
    assert_sticky(host, ["gap", "observer"])


def test_port_callback_latches_precise_resource_and_blocks_host_close(host):
    attach(host)
    handle = object()
    host._record_cleanup_uncertain(handle, "HANDLE_CLOSE_UNCONFIRMED")
    assert host._cleanup_uncertain_resources == [handle]
    assert_sticky(host, [])


def test_port_callback_during_service_close_stops_next_dependency(host):
    attach(host)
    handle = object()
    def close_service():
        host.events.append("service")
        host._record_cleanup_uncertain(handle, "HANDLE_CLOSE_EXCEPTION")
        host.service_host.state = "closed"
    host.service_host.close = close_service
    assert_sticky(host, ["service"])
    assert host._cleanup_uncertain_resources == [handle]


def test_port_property_uncertain_stops_before_service_close(host):
    attach(host)
    host.order_port.cleanup_uncertain = True
    assert_sticky(host, [])
    assert host._cleanup_uncertain_resources == [host.order_port]


@pytest.mark.parametrize("value", [None, 0, 1, ""])
def test_port_property_requires_exact_false(host, value):
    attach(host)
    host.order_port.cleanup_uncertain = value
    assert_sticky(host, [])
    assert host._cleanup_uncertain_resources == [host.order_port]


@pytest.mark.parametrize("stage", ["serve", "observer", "pump", "market", "account", "financial", "maintain"])
def test_run_once_stops_immediately_after_latch(host, monkeypatch, stage):
    handle = object()
    def action(name):
        def call(*_args):
            host.events.append(name)
            if name == stage:
                host._record_cleanup_uncertain(handle, "SYNTHETIC")
        return call
    host.config.loop_timeout_ms = 1
    host.config.observation_poll_seconds = 1
    host.config.market_poll_seconds = 1
    host.config.account_snapshot_seconds = 1
    host.config.strategy_financial_seconds = 1
    host.config.maintain_seconds = 1
    host._monotonic = lambda: 1
    host._next = dict.fromkeys(("observation", "market", "account", "financial", "maintain"), 0)
    host.service_host = SimpleNamespace(serve_once=action("serve"))
    host.ledger = SimpleNamespace(fence=object())
    monkeypatch.setattr(host, "_reconnect_observer", action("observer"))
    host.pump = SimpleNamespace(poll=action("pump"))
    host.market = SimpleNamespace(poll=action("market"))
    host.account = SimpleNamespace(refresh=action("account"))
    host.financial = SimpleNamespace(refresh=action("financial"))
    host.engine = SimpleNamespace(maintain=action("maintain"))
    order = ["serve", "observer", "pump", "market", "account", "financial", "maintain"]
    with pytest.raises(module.RiskServiceStartError):
        host.run_once()
    assert host.events == order[:order.index(stage) + 1]
    assert host._cleanup_uncertain_resources == [handle]
    assert host.close() is False


@pytest.mark.parametrize("name", ["open_storage", "build_engine", "start_gateway", "start_service_host", "start", "run_once", "run"])
def test_public_lifecycle_refuses_after_latch(host, name):
    attach(host)
    host._record_cleanup_uncertain(object(), "SYNTHETIC")
    with pytest.raises(module.RiskServiceStartError):
        getattr(host, name)()
    assert host.events == []


def test_start_monotonic_failure_invokes_first_cleanup(host, monkeypatch):
    attach(host)
    monkeypatch.setattr(module, "fault_point_from_environment", lambda _: None)
    monkeypatch.setattr(host, "open_storage", lambda: None)
    monkeypatch.setattr(host, "build_engine", lambda: None)
    monkeypatch.setattr(module, "bootstrap_policy", lambda *_: None)
    host.rules = object()
    host.engine = SimpleNamespace(recover=lambda: None)
    host.pump = SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(host, "start_gateway", lambda: None)
    monkeypatch.setattr(host, "start_service_host", lambda: None)
    def fail_clock():
        raise ValueError("private clock error")
    host._monotonic = fail_clock
    with pytest.raises(ValueError, match="private clock error"):
        host.start()
    assert host.events == ["service", "port", "last", "first", "stack"]
    assert host._cleanup_state == "complete"


def make_gateway_port(host, handle):
    from bt_api_py.risk_service_gateway import DelegateOrderPort, _DelegateSlot

    port = DelegateOrderPort(
        account_scope="synthetic", execution_domain_ref="synthetic", identity=object(),
        service_id="synthetic", instance_id="synthetic", scope_ref="synthetic",
        grant_revisions={"synthetic": "v1"}, instrument_for=lambda *_: None,
        connect=lambda *_: None, timeout_seconds=1, retry_seconds=1,
        on_cleanup_uncertain=host._record_cleanup_uncertain,
        is_cleanup_uncertain=host._port_cleanup_uncertain,
    )
    port._slots["synthetic"] = _DelegateSlot("first", handle=handle)
    return port


def test_gateway_port_callback_preserves_exact_handle_during_run(host):
    class Handle:
        def close(self):
            host.events.append("handle")
            return None
    handle = Handle()
    attach(host)
    host.order_port = make_gateway_port(host, handle)
    host.order_port.note_run("synthetic", "second")
    assert host.order_port.cleanup_uncertain is True
    assert host._cleanup_uncertain is True
    assert host._cleanup_uncertain_resources == [handle]
    assert_sticky(host, ["handle"])


def test_gateway_port_callback_during_host_close_stops_runtime(host):
    class Handle:
        def close(self):
            host.events.append("handle")
            return None
    handle = Handle()
    attach(host)
    host.order_port = make_gateway_port(host, handle)
    assert_sticky(host, ["service", "handle"])
    assert host._cleanup_uncertain_resources[0] is handle
    assert any(item is host.order_port for item in host._cleanup_uncertain_resources)


def test_gateway_ordinary_blocked_slot_is_not_cleanup_latch(host):
    from bt_api_py.risk_service_gateway import _DelegateSlot

    attach(host)
    host.order_port = make_gateway_port(host, object())
    host.order_port._slots["synthetic"] = _DelegateSlot("first", blocked=True)
    assert host.close() is True
    assert host.events == ["service", "last", "first", "stack"]
    assert host._cleanup_uncertain is False
    assert host.order_port.cleanup_uncertain is False
    assert host not in module._RETAINED_HOSTS
    assert host.close() is True


def test_build_engine_wires_gateway_callback_and_aborts_on_reentry(host, monkeypatch):
    import bt_api_py.risk_service_gateway as gateway

    host.config.account_snapshot_max_age_seconds = None
    host.config.strategy_financial_max_age_seconds = None
    host.config.instruments = {}
    host.config.account_scope = "synthetic"
    host.config.execution_domain_ref = "synthetic"
    host.config.call_timeout_seconds = 1
    host.config.maintain_seconds = 1
    host.config.gateway = SimpleNamespace(
        delegate=SimpleNamespace(principal_ref="synthetic", actor_ref="synthetic"),
        service_id="synthetic", instance_id="synthetic", scope_ref="synthetic",
        grant_revisions={},
    )
    monkeypatch.setattr(module, "WireIdentity", lambda *_: object())
    monkeypatch.setattr(gateway, "RiskFactsCache", lambda *_args, **_kwargs: object())
    handle = object()
    def port_factory(**kwargs):
        assert kwargs["is_cleanup_uncertain"]() is False
        kwargs["on_cleanup_uncertain"](handle, "HANDLE_CLOSE_UNCONFIRMED")
        assert kwargs["is_cleanup_uncertain"]() is True
        return object()
    monkeypatch.setattr(gateway, "DelegateOrderPort", port_factory)
    monkeypatch.setattr(host, "_control_runtime", lambda: pytest.fail("unexpected runtime"))
    with pytest.raises(module.RiskServiceStartError):
        host.build_engine()
    assert host._cleanup_uncertain_resources[0] is handle
    assert any(item is host.order_port for item in host._cleanup_uncertain_resources)
    assert host.close() is False


def test_host_close_reentry_does_not_create_cleanup_failure(host):
    attach(host)
    def close_service():
        host.events.append("service")
        assert host.close() is False
        host.service_host.state = "closed"
    host.service_host.close = close_service
    assert host.close() is True
    assert host._cleanup_uncertain is False
    assert host.events == ["service", "port", "last", "first", "stack"]


def test_start_final_clock_latch_preserves_resources(host, monkeypatch):
    attach(host)
    monkeypatch.setattr(module, "fault_point_from_environment", lambda _: None)
    monkeypatch.setattr(host, "open_storage", lambda: None)
    monkeypatch.setattr(host, "build_engine", lambda: None)
    monkeypatch.setattr(module, "bootstrap_policy", lambda *_: None)
    host.rules = object()
    host.engine = SimpleNamespace(recover=lambda: None)
    host.pump = SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(host, "start_gateway", lambda: None)
    monkeypatch.setattr(host, "start_service_host", lambda: None)
    handle = object()
    def clock():
        host._record_cleanup_uncertain(handle, "SYNTHETIC")
        return 1
    host._monotonic = clock
    with pytest.raises(module.RiskServiceStartError):
        host.start()
    assert host.events == []
    assert host._cleanup_uncertain_resources == [handle]
    assert host.close() is False


def test_gateway_port_observes_host_delegate_cleanup_latch(host, monkeypatch):
    from bt_api_py.risk_service_gateway import _DelegateSlot

    class FailedRuntime:
        def start(self, **_kwargs):
            host.events.append("start")
            raise RuntimeError("synthetic start failure")
        def close(self, **_kwargs):
            host.events.append("delegate")
            return None
    class OtherHandle:
        def close(self):
            host.events.append("other")
            return snapshot()
    runtime, other = FailedRuntime(), OtherHandle()
    attach(host)
    host.config.call_timeout_seconds = 1
    port = make_gateway_port(host, other)
    host.order_port = port
    port._slots["synthetic"] = _DelegateSlot("first")
    port._slots["other"] = _DelegateSlot("first", handle=other)
    port._revisions["other"] = "v1"
    port._connect = host._connect_delegate
    monkeypatch.setattr(port, "route_for", lambda *_: SimpleNamespace(
        scope=SimpleNamespace(run_id="synthetic")))
    monkeypatch.setattr(host, "_service_runtime", lambda *_args, **_kwargs: runtime)
    with pytest.raises(RuntimeError):
        port._handle("synthetic", "first")
    assert host._cleanup_uncertain is True
    assert port.cleanup_uncertain is True
    assert any(item is runtime for item in host._cleanup_uncertain_resources)
    assert port._handle("other", "first") is None
    assert port.close() is False
    assert host.close() is False
    assert host.events == ["start", "delegate"]


def test_delegate_factory_reentrant_close_cannot_release_host(host, monkeypatch):
    class Delegate:
        def start(self, **_kwargs):
            host.events.append("start")
            raise RuntimeError("synthetic failure")
        def close(self, **_kwargs):
            host.events.append("delegate")
            return snapshot()
    runtime = Delegate()
    attach(host)
    host.config.call_timeout_seconds = 1
    def factory(*_args, **_kwargs):
        assert host.close() is False
        assert host.events == []
        return runtime
    monkeypatch.setattr(host, "_service_runtime", factory)
    route = SimpleNamespace(scope=SimpleNamespace(run_id="synthetic"))
    with pytest.raises(RuntimeError):
        host._connect_delegate(route)
    assert host.events == ["start", "delegate"]
    assert host._cleanup_state == "ready" and host._cleanup_uncertain is False


def test_observer_factory_reentrant_close_cannot_release_host(host, monkeypatch):
    class Observer:
        def start(self, **_kwargs):
            host.events.append("start")
            raise RuntimeError("synthetic failure")
        def close(self, **_kwargs):
            host.events.append("observer")
            return snapshot()
    runtime = Observer()
    attach(host)
    host.config.call_timeout_seconds = 1
    host.config.maintain_seconds = 1
    host._observer_route = SimpleNamespace(scope=object())
    host._monotonic = lambda: 1
    host.pump = SimpleNamespace(_gap=lambda _: host.events.append("gap"))
    def factory(*_args, **_kwargs):
        assert host.close() is False
        assert host.events == ["gap"]
        return runtime
    monkeypatch.setattr(host, "_service_runtime", factory)
    host._reconnect_observer()
    assert host.events == ["gap", "start", "observer"]
    assert host._cleanup_state == "ready" and host._cleanup_uncertain is False


def test_open_storage_body_and_exit_failure_retain_exact_fake_resources(host, monkeypatch):
    import bt_api_risk.core.admission as admission
    from bt_api_risk.risk_order_journal import RiskFenceExitCleanup

    fence, body_error = object(), ValueError("private body error")
    class _ReceiptJournal:
        def prepare_borrowed_fence_close(self, *_args):
            pass
        def consume_borrowed_fence_close(self, *_args):
            return RiskFenceExitCleanup.COMPLETE
    journal = _ReceiptJournal()
    class Inner:
        def __exit__(self, *_args):
            host.events.append("exit")
            raise RuntimeError("private exit error")
    inner = Inner()
    manager = module._FencedJournalManager(Path("synthetic-risk.sqlite"), object())
    def open_context(outer, _path, _scope):
        host.events.append("enter")
        assert host.close() is False
        outer._inner_manager = inner
        outer._binding = (inner, journal, fence)
        return journal, fence
    host.config.storage_directory = object()
    host.config.risk_database = object()
    host.config.provider = host.config.account_scope = host.config.environment = "synthetic"
    monkeypatch.setattr(module, "ensure_private_directory", lambda _: None)
    monkeypatch.setattr(admission, "AccountScope", lambda *_: object())
    monkeypatch.setattr(module, "_open_fenced_journal", lambda *_: manager)
    monkeypatch.setattr(module, "_open_fenced_journal_context", open_context)
    def fail(_):
        raise body_error
    monkeypatch.setattr(module, "restrict_file", fail)
    # A close that itself raises cannot be shown to have completed, so the
    # unproven outcome must surface as the cleanup-uncertain code rather than
    # the original body error; the exact resources stay retained.
    with pytest.raises(module.RiskServiceStartError,
                       match="RISK_SERVICE_STORAGE_CLEANUP_UNCERTAIN"):
        host.open_storage()
    assert host._storage_cleanup_uncertain is True
    assert host._uncertain_storage == (journal, fence)
    assert any(isinstance(item, tuple) and item[1:] == (journal, fence)
               for item in host._cleanup_uncertain_resources)
    assert_sticky(host, ["enter", "exit"])


def test_open_storage_latch_transfers_fake_stack_without_exit(host, monkeypatch):
    from contextlib import ExitStack

    import bt_api_risk.core.admission as admission

    journal, fence, failure = object(), object(), object()
    class FakeFence:
        def __enter__(self):
            host.events.append("enter")
            return journal, fence
        def __exit__(self, *_args):
            host.events.append("exit")
    host._stack = ExitStack()
    host.config.storage_directory = object()
    host.config.risk_database = object()
    host.config.provider = host.config.account_scope = host.config.environment = "synthetic"
    monkeypatch.setattr(module, "ensure_private_directory", lambda _: None)
    monkeypatch.setattr(admission, "AccountScope", lambda *_: object())
    monkeypatch.setattr(module, "_open_fenced_journal", lambda *_: FakeFence())
    monkeypatch.setattr(module, "restrict_file", lambda _: host._record_cleanup_uncertain(failure, "SYNTHETIC"))
    with pytest.raises(module.RiskServiceStartError):
        host.open_storage()
    assert host._uncertain_storage == (journal, fence)
    assert host.events == ["enter"]
    assert host.close() is False
    assert host.events == ["enter"]


def test_gateway_observer_start_latch_stops_control_start(host, monkeypatch):
    class Observer:
        def start(self, **_kwargs):
            host.events.append("observer")
            assert host.close() is False
            host._record_cleanup_uncertain(self, "SYNTHETIC")
            return object()
    observer = Observer()
    host.config.call_timeout_seconds = 1
    host.config.execution_domain_ref = host.config.account_scope = "synthetic"
    host.config.gateway = SimpleNamespace()
    monkeypatch.setattr(module, "WireScope", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(host, "_service_runtime", lambda *_args, **_kwargs: observer)
    host.control_runtime = SimpleNamespace(start=lambda **_: pytest.fail("control started"))
    with pytest.raises(module.RiskServiceStartError):
        host.start_gateway()
    assert host.events == ["observer"]
    assert host._runtimes == [observer]
    assert host.close() is False


def test_service_host_factory_latch_retains_object_without_start(host, monkeypatch):
    import bt_api_py.risk_service_component as component
    import bt_api_py.service_config as service_config
    import bt_api_py.service_host as service_host

    host.config.account_scope = host.config.route = host.config.execution_domain_ref = "synthetic"
    host.config.principals = {}
    host.config.registry_database = object()
    host.instrument_for = lambda *_: None
    host.order_port = SimpleNamespace(note_run=lambda *_: None)
    host.control = SimpleNamespace(cancel_scope_status=lambda *_: None)
    fake_service = SimpleNamespace(start=lambda: pytest.fail("unexpected start"))
    monkeypatch.setattr(component, "RiskOrderService", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "service_config_document", lambda *_: object())
    monkeypatch.setattr(service_config, "parse_service_config", lambda *_: object())
    monkeypatch.setattr(module, "build_authority", lambda *_: object())
    def factory(*_args, **_kwargs):
        assert host.close() is False
        host._record_cleanup_uncertain(fake_service, "SYNTHETIC")
        return fake_service
    monkeypatch.setattr(service_host, "ServiceHost", factory)
    monkeypatch.setattr(module, "restrict_file", lambda *_: pytest.fail("unexpected file operation"))
    with pytest.raises(module.RiskServiceStartError):
        host.start_service_host()
    assert host.service_host is fake_service
    assert host._cleanup_uncertain_resources == [fake_service]
    assert host.close() is False


@pytest.mark.parametrize("stage", ["scope", "factory", "observer_start", "grant_observer", "identity",
                                   "route", "observer_client", "control_start", "grant_control",
                                   "payload", "pump"])
def test_gateway_start_stops_after_each_callback_latches(host, monkeypatch, stage):
    import bt_api_py.gateway_account_observer_client as observer_module
    import bt_api_py.risk_service_gateway as gateway_module

    token = object()
    def event(name, result=None):
        def call(*_args, **_kwargs):
            host.events.append(name)
            assert host.close() is False
            if name == stage:
                host._record_cleanup_uncertain(token, "SYNTHETIC")
            return result
        return call
    observer = SimpleNamespace(start=event("observer_start", object()))
    control = SimpleNamespace(start=event("control_start", object()))
    host.config.call_timeout_seconds = 1
    host.config.execution_domain_ref = host.config.account_scope = "synthetic"
    host.config.strategy_financial_seconds = None
    host.config.gateway = SimpleNamespace(
        delegate=SimpleNamespace(principal_ref="synthetic", actor_ref="synthetic"),
        principal_ref="synthetic-gateway", server_public_key=b"x" * 40,
        service_id="synthetic", instance_id="synthetic", scope_ref="synthetic",
        account_snapshot=None, market=None,
    )
    host.control_runtime = control
    host.delegate_storage = object()
    monkeypatch.setattr(module, "WireScope", event("scope", object()))
    monkeypatch.setattr(host, "_service_runtime", event("factory", observer))
    peer = module.WireIdentity(module.WireRole.SERVICE, "synthetic-gateway", "synthetic")
    monkeypatch.setattr(host, "_peer", lambda: peer)
    monkeypatch.setattr(module, "WireIdentity", event("identity", object()))
    monkeypatch.setattr(observer_module, "GatewayAccountObserverRoute", event("route", object()))
    monkeypatch.setattr(observer_module, "GatewayAccountObserverClient", event("observer_client", object()))
    monkeypatch.setattr(gateway_module, "payload_reader", event("payload", object()))
    monkeypatch.setattr(gateway_module, "ObservationPump", event("pump", object()))
    def grant(_snapshot, mount, _operations):
        name = "grant_observer" if mount == "upstream.account" else "grant_control"
        return event(name, True)()
    monkeypatch.setattr(module, "_granted", grant)
    expected = ["scope", "factory", "observer_start", "grant_observer", "identity", "route",
                "observer_client", "control_start", "grant_control", "payload", "pump"]
    with pytest.raises(module.RiskServiceStartError):
        host.start_gateway()
    assert host.events == expected[:expected.index(stage) + 1]
    assert host._cleanup_uncertain_resources[0] is token
    if stage != "scope":
        assert host._runtimes[0] is observer
    assert host.close() is False


@pytest.mark.parametrize("stage", ["component", "document", "parse", "authority", "factory", "start", "restrict"])
def test_service_start_stops_after_each_callback_latches(host, monkeypatch, stage):
    import bt_api_py.risk_service_component as component
    import bt_api_py.service_config as service_config
    import bt_api_py.service_host as service_host

    token = object()
    def event(name, result=None):
        def call(*_args, **_kwargs):
            host.events.append(name)
            assert host.close() is False
            if name == stage:
                host._record_cleanup_uncertain(token, "SYNTHETIC")
            return result
        return call
    fake_service = SimpleNamespace(start=event("start"))
    host.config.account_scope = host.config.route = host.config.execution_domain_ref = "synthetic"
    host.config.principals = {}
    host.config.registry_database = object()
    host.instrument_for = lambda *_: None
    host.order_port = SimpleNamespace(note_run=lambda *_: None)
    host.control = SimpleNamespace(cancel_scope_status=lambda *_: None)
    monkeypatch.setattr(component, "RiskOrderService", event("component", object()))
    monkeypatch.setattr(module, "service_config_document", event("document", object()))
    monkeypatch.setattr(service_config, "parse_service_config", event("parse", object()))
    monkeypatch.setattr(module, "build_authority", event("authority", object()))
    monkeypatch.setattr(service_host, "ServiceHost", event("factory", fake_service))
    monkeypatch.setattr(module, "restrict_file", event("restrict"))
    expected = ["component", "document", "parse", "authority", "factory", "start", "restrict"]
    with pytest.raises(module.RiskServiceStartError):
        host.start_service_host()
    assert host.events == expected[:expected.index(stage) + 1]
    assert host._cleanup_uncertain_resources[0] is token
    if stage in ("factory", "start", "restrict"):
        assert host.service_host is fake_service
    assert host.close() is False


def test_engine_recover_may_connect_delegate_within_lifecycle(host, monkeypatch):
    import bt_api_py.gateway_delegate_risk_host as delegate_host
    import bt_api_py.gateway_delegate_service_client as delegate_client

    class Delegate:
        def start(self, **_kwargs):
            host.events.append("delegate_start")
            assert host.close() is False
            return object()
    runtime = Delegate()
    host.config.call_timeout_seconds = 1
    host.delegate_storage = object()
    route = SimpleNamespace(scope=SimpleNamespace(run_id="synthetic"))
    monkeypatch.setattr(host, "_service_runtime", lambda *_args, **_kwargs: runtime)
    monkeypatch.setattr(module, "_granted", lambda *_args: True)
    monkeypatch.setattr(delegate_host, "GatewayRiskDelegateHost", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(delegate_client, "GatewayDelegateServiceClient", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "fault_point_from_environment", lambda _: None)
    monkeypatch.setattr(host, "open_storage", lambda: None)
    monkeypatch.setattr(host, "build_engine", lambda: None)
    monkeypatch.setattr(module, "bootstrap_policy", lambda *_: None)
    host.rules = object()
    host.engine = SimpleNamespace(recover=lambda: host._connect_delegate(route))
    host.pump = SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(host, "start_gateway", lambda: None)
    monkeypatch.setattr(host, "start_service_host", lambda: None)
    host._monotonic = lambda: 1
    assert host.start() is host
    assert host.events == ["delegate_start"]
    assert host._cleanup_uncertain is False and host._cleanup_state == "ready"


def test_delegate_factory_cannot_recursively_connect(host, monkeypatch):
    class Delegate:
        def start(self, **_kwargs):
            raise RuntimeError("synthetic failure")
        def close(self, **_kwargs):
            return snapshot()
    host.config.call_timeout_seconds = 1
    route = SimpleNamespace(scope=SimpleNamespace(run_id="synthetic"))
    def factory(*_args, **_kwargs):
        with pytest.raises(module.RiskServiceStartError):
            host._connect_delegate(route)
        assert host.close() is False
        return Delegate()
    monkeypatch.setattr(host, "_service_runtime", factory)
    with pytest.raises(RuntimeError):
        host._connect_delegate(route)
    assert host._cleanup_state == "ready"


@pytest.mark.parametrize("kind", ["delegate", "observer"])
def test_factory_state_change_retains_unstarted_runtime(host, monkeypatch, kind):
    runtime = SimpleNamespace(start=lambda **_: pytest.fail("unexpected start"))
    route = SimpleNamespace(scope=SimpleNamespace(run_id="synthetic"))
    def factory(*_args, **_kwargs):
        host._cleanup_state = "complete"
        return runtime
    monkeypatch.setattr(host, "_service_runtime", factory)
    if kind == "observer":
        host.config.maintain_seconds = 1
        host._monotonic = lambda: 1
        host._observer_route = route
        host.pump = SimpleNamespace(_gap=lambda _: None)
    with pytest.raises(module.RiskServiceStartError):
        if kind == "delegate":
            host._connect_delegate(route)
        else:
            host._reconnect_observer()
    assert host._cleanup_uncertain_resources == [runtime]
    assert host in module._RETAINED_HOSTS
    assert host.close() is False


def test_open_storage_body_failure_with_confirmed_fake_exit_is_not_sticky(host, monkeypatch):
    import bt_api_risk.core.admission as admission
    from bt_api_risk.risk_order_journal import RiskFenceExitCleanup

    # A confirmed cleanup needs the exact fenced manager and its COMPLETE
    # receipt; a duck-typed context manager cannot prove a close.
    fence = object()
    class _ReceiptJournal:
        def prepare_borrowed_fence_close(self, *_args):
            pass
        def consume_borrowed_fence_close(self, *_args):
            return RiskFenceExitCleanup.COMPLETE
    journal = _ReceiptJournal()
    class Inner:
        def __exit__(self, *_args):
            host.events.append("exit")
    inner = Inner()
    manager = module._FencedJournalManager(Path("synthetic-risk.sqlite"), object())
    def open_context(outer, _path, _scope):
        host.events.append("enter")
        outer._inner_manager = inner
        outer._binding = (inner, journal, fence)
        return journal, fence
    host.config.storage_directory = object()
    host.config.risk_database = object()
    host.config.provider = host.config.account_scope = host.config.environment = "synthetic"
    monkeypatch.setattr(module, "ensure_private_directory", lambda _: None)
    monkeypatch.setattr(admission, "AccountScope", lambda *_: object())
    monkeypatch.setattr(module, "_open_fenced_journal", lambda *_: manager)
    monkeypatch.setattr(module, "_open_fenced_journal_context", open_context)
    def fail(_):
        raise ValueError("synthetic body failure")
    monkeypatch.setattr(module, "restrict_file", fail)
    with pytest.raises(ValueError):
        host.open_storage()
    assert host.events == ["enter", "exit"]
    assert host._cleanup_uncertain is False
    assert host not in module._RETAINED_HOSTS


@pytest.mark.parametrize("error", [RuntimeError("synthetic failure"), StopWait()])
def test_fence_entry_unknown_retains_manager_and_original_without_closing(host, monkeypatch, error):
    import bt_api_risk.core.admission as admission

    class Manager:
        def __enter__(self):
            host.events.append("enter")
            raise error
        def __exit__(self, *_args):
            host.events.append("exit")
    manager = Manager()
    host.config.storage_directory = object()
    host.config.risk_database = object()
    host.config.provider = host.config.account_scope = host.config.environment = "synthetic"
    monkeypatch.setattr(module, "ensure_private_directory", lambda _: None)
    monkeypatch.setattr(admission, "AccountScope", lambda *_: object())
    monkeypatch.setattr(module, "_open_fenced_journal", lambda *_: manager)
    monkeypatch.setattr(module, "restrict_file", lambda _: pytest.fail("unexpected file operation"))
    with pytest.raises(type(error)) as raised:
        host.open_storage()
    assert raised.value is error
    assert host._storage_cleanup_uncertain is True
    assert host._uncertain_storage == (manager, error)
    assert any(isinstance(item, tuple) and item == (manager, error)
               for item in host._cleanup_uncertain_resources)
    assert_sticky(host, ["enter"])


def test_entry_error_from_non_context_manager_stays_a_proven_refusal(host, monkeypatch):
    import bt_api_risk.core.admission as admission

    class Manager:
        """Not a context manager: ExitStack raises TypeError before entry."""

    manager = Manager()
    host.config.storage_directory = object()
    host.config.risk_database = object()
    host.config.provider = host.config.account_scope = host.config.environment = "synthetic"
    monkeypatch.setattr(module, "ensure_private_directory", lambda *_: None)
    monkeypatch.setattr(admission, "AccountScope", lambda *_: object())
    monkeypatch.setattr(module, "_open_fenced_journal", lambda *_: manager)
    with pytest.raises(TypeError):
        host.open_storage()
    # A manager that is not a context manager cannot produce an entry or a
    # close proof; the refusal is retained instead of being lost.
    assert host._storage_cleanup_uncertain is True
    assert host._uncertain_storage[0] is manager
    assert isinstance(host._uncertain_storage[1], TypeError)
    assert_sticky(host, [])


def test_only_exit_callback_owner_without_exit_is_not_proof():
    from contextlib import ExitStack

    stack = ExitStack()
    stack._exit_callbacks.append((True, lambda *_args: None))
    # A missing __exit__ must read as "no proof" rather than raising while the
    # caller is already classifying a failed entry.
    assert module._only_exit_callback(stack, object()) is False


def test_fence_manager_factory_exception_is_unknown(host, monkeypatch):
    import bt_api_risk.core.admission as admission

    error = ValueError("synthetic factory error")
    host.config.storage_directory = object()
    host.config.risk_database = object()
    host.config.provider = host.config.account_scope = host.config.environment = "synthetic"
    monkeypatch.setattr(module, "ensure_private_directory", lambda _: None)
    monkeypatch.setattr(admission, "AccountScope", lambda *_: object())
    def factory(*_args):
        raise error
    monkeypatch.setattr(module, "_open_fenced_journal", factory)
    with pytest.raises(ValueError) as raised:
        host.open_storage()
    assert raised.value is error
    assert host._uncertain_storage == (None, error)
    assert_sticky(host, [])


@pytest.mark.parametrize("mode", ["complete", "unknown", "legacy", "subclass", "spoof", "invalid",
                                  "invalid_result", "truthy_result"])
def test_risk_fence_entry_contract_requires_exact_complete(host, monkeypatch, mode):
    from enum import Enum

    import bt_api_risk.core.admission as admission
    import bt_api_risk.risk_order_journal as risk_journal

    class Cleanup(Enum):
        COMPLETE = "complete"
        UNKNOWN = "unknown"
    class Complete(Enum):
        HELD = "held"
        OTHER = "other"
        INVALID = "invalid"
    class EntryError(risk_journal.RiskOrderJournalError):
        def __init__(self, cleanup):
            super().__init__("WRITER_FENCE_HELD")
            self.cleanup = cleanup
    class DerivedEntryError(EntryError):
        pass
    if mode == "subclass":
        error = DerivedEntryError(Cleanup.COMPLETE)
    elif mode == "spoof":
        error = risk_journal.RiskOrderJournalError("WRITER_FENCE_HELD")
        error.cleanup = Cleanup.COMPLETE
    else:
        value = (Cleanup.COMPLETE if mode in ("complete", "legacy") else
                 Cleanup.UNKNOWN if mode == "unknown" else "complete")
        error = EntryError(value)
    class Manager:
        def __enter__(self):
            host.events.append("enter")
            raise error
        def __exit__(self, *_args):
            host.events.append("exit")
    manager = Manager()
    class Journal:
        @staticmethod
        def open_fenced(*_args, **_kwargs):
            return manager
    host.config.storage_directory = object()
    host.config.risk_database = object()
    host.config.provider = host.config.account_scope = host.config.environment = "synthetic"
    monkeypatch.setattr(module, "ensure_private_directory", lambda _: None)
    monkeypatch.setattr(admission, "AccountScope", lambda *_: object())
    monkeypatch.setattr(risk_journal, "RiskOrderJournal", Journal)
    if mode == "legacy":
        monkeypatch.delattr(risk_journal, "RiskFenceEntryError", raising=False)
        monkeypatch.delattr(risk_journal, "consume_fence_entry_complete", raising=False)
    else:
        monkeypatch.setattr(risk_journal, "RiskFenceEntryError", EntryError, raising=False)
        monkeypatch.setattr(risk_journal, "RiskFenceEntryComplete", Complete, raising=False)
        issued = [(manager, error)] if mode in ("complete", "invalid_result", "truthy_result") else []
        def consume(candidate_manager, candidate_error):
            if issued and issued[0] == (candidate_manager, candidate_error):
                issued.pop()
                return (Complete.INVALID if mode == "invalid_result" else
                        True if mode == "truthy_result" else Complete.HELD)
            return None
        monkeypatch.setattr(risk_journal, "consume_fence_entry_complete", consume, raising=False)
    with pytest.raises(module.RiskServiceStartError) as raised:
        host.open_storage()
    expected_code = "RISK_SERVICE_FENCE_HELD" if mode == "complete" else "RISK_SERVICE_STORAGE_UNAVAILABLE"
    assert raised.value.code == expected_code
    assert raised.value.original is error
    assert raised.value.manager is manager
    if mode == "complete":
        assert host._cleanup_uncertain is False
        assert host.close() is True
        assert host.events == ["enter", "stack"]
    else:
        assert host._cleanup_uncertain is True
        assert host._uncertain_storage[1] is raised.value
        assert_sticky(host, ["enter"])


@pytest.mark.parametrize("command", ["run", "status"])
def test_cli_waits_without_reclosing_unknown_fence_entry(host, monkeypatch, capsys, command):
    import bt_api_risk.core.admission as admission

    class Manager:
        def __enter__(self):
            host.events.append("enter")
            raise module.RiskServiceStartError("RISK_SERVICE_STORAGE_UNAVAILABLE")
        def __exit__(self, *_args):
            host.events.append("exit")
    host.config.storage_directory = object()
    host.config.risk_database = SimpleNamespace(is_file=lambda: True)
    host.config.provider = host.config.account_scope = host.config.environment = "synthetic"
    monkeypatch.setattr(module, "ensure_private_directory", lambda _: None)
    monkeypatch.setattr(admission, "AccountScope", lambda *_: object())
    monkeypatch.setattr(module, "_open_fenced_journal", lambda *_: Manager())
    monkeypatch.setattr(module, "fault_point_from_environment", lambda _: None)
    monkeypatch.setattr(module, "load_risk_service_config", lambda _: host.config)
    monkeypatch.setattr(module, "RiskServiceHost", lambda _: host)
    monkeypatch.setattr(module.signal, "signal", lambda *_args: None)
    original_wait = module._wait_for_cleanup
    def stop():
        raise StopWait
    monkeypatch.setattr(module, "_wait_for_cleanup", lambda h: original_wait(h, _wait=stop))
    with pytest.raises(StopWait):
        module.main([command, "--config", "synthetic"])
    assert host.events == ["enter"]
    assert host._cleanup_uncertain is True
    assert host in module._RETAINED_HOSTS
    output = capsys.readouterr()
    assert "RISK_SERVICE_STORAGE_UNAVAILABLE" in output.out + output.err


def test_fence_entry_classification_interrupt_retains_both_errors(host, monkeypatch):
    import bt_api_risk.core.admission as admission

    entry_error, interruption = RuntimeError("synthetic entry"), StopWait()
    class Manager:
        def __enter__(self):
            host.events.append("enter")
            raise entry_error
        def __exit__(self, *_args):
            host.events.append("exit")
    manager = Manager()
    host.config.storage_directory = object()
    host.config.risk_database = object()
    host.config.provider = host.config.account_scope = host.config.environment = "synthetic"
    monkeypatch.setattr(module, "ensure_private_directory", lambda _: None)
    monkeypatch.setattr(admission, "AccountScope", lambda *_: object())
    monkeypatch.setattr(module, "_open_fenced_journal", lambda *_: manager)
    def interrupt(_manager, _error):
        raise interruption
    monkeypatch.setattr(module, "_consume_fence_entry_result", interrupt)
    with pytest.raises(StopWait) as raised:
        host.open_storage()
    assert raised.value is interruption
    assert host._uncertain_storage == (manager, entry_error)
    assert host._cleanup_uncertain_resources == [(manager, entry_error, interruption)]
    assert_sticky(host, ["enter"])


@pytest.mark.parametrize("complete", [True, False])
def test_sdk_rejects_unbound_actual_risk_entry_error(host, monkeypatch, complete):
    import bt_api_risk.core.admission as admission
    import bt_api_risk.risk_order_journal as risk_journal

    retained, token = [], object()
    monkeypatch.setattr(risk_journal, "_FENCE_ENTRY_UNCERTAIN", retained)
    original = risk_journal.RiskOrderJournalError("WRITER_FENCE_HELD")
    class Manager:
        def __enter__(self):
            host.events.append("enter")
            risk_journal._raise_fence_entry(original, complete=complete, retained=(token,))
        def __exit__(self, *_args):
            host.events.append("exit")
    manager = Manager()
    class Journal:
        @staticmethod
        def open_fenced(*_args, **_kwargs):
            return manager
    host.config.storage_directory = object()
    host.config.risk_database = object()
    host.config.provider = host.config.account_scope = host.config.environment = "synthetic"
    monkeypatch.setattr(module, "ensure_private_directory", lambda _: None)
    monkeypatch.setattr(admission, "AccountScope", lambda *_: object())
    monkeypatch.setattr(risk_journal, "RiskOrderJournal", Journal)
    with pytest.raises(module.RiskServiceStartError) as raised:
        host.open_storage()
    native = raised.value.original
    assert type(native) is risk_journal.RiskFenceEntryError
    assert native.original is original
    if complete:
        assert native.cleanup is risk_journal.RiskFenceEntryCleanup.COMPLETE
        assert native.retained == (token,) and retained == []
    else:
        assert native.cleanup is risk_journal.RiskFenceEntryCleanup.UNKNOWN
        assert native.retained == (token,)
        assert retained == [(token, original)]
    assert raised.value.code == "RISK_SERVICE_STORAGE_UNAVAILABLE"
    assert host._uncertain_storage[1] is raised.value
    assert_sticky(host, ["enter"])


@pytest.fixture
def probe_state(monkeypatch):
    state = {"active": False, "uncertain": []}
    monkeypatch.setattr(module, "_PROBE_STATE", state)
    return state


def test_probe_unknown_entry_blocks_all_later_probes(probe_state):
    import traceback

    calls = []
    error = RuntimeError("synthetic unknown")
    class Manager:
        def __enter__(self):
            calls.append("enter")
            raise error
        def __exit__(self, *_args):
            calls.append("exit")
    manager = Manager()
    class Journal:
        def writer_fence(self, *_args, **_kwargs):
            calls.append("factory")
            return manager
    journal, scope = Journal(), object()
    with pytest.raises(module.RiskServiceStartError) as raised:
        module.probe_writer_fence(journal, scope)
    assert raised.value.code == "RISK_SERVICE_STORAGE_UNAVAILABLE"
    assert "synthetic unknown" not in "".join(traceback.format_exception(raised.value))
    assert calls == ["factory", "enter"]
    retained = probe_state["uncertain"]
    assert len(retained) == 1
    assert retained[0][0] is journal and retained[0][1] is scope
    assert retained[0][2] is manager and retained[0][4] is error
    with pytest.raises(module.RiskServiceStartError):
        module.probe_writer_fence(Journal(), object())
    assert calls == ["factory", "enter"]


def test_probe_strict_complete_contention_allows_later_probe(probe_state, monkeypatch):
    from enum import Enum

    import bt_api_risk.risk_order_journal as risk_journal

    class Cleanup(Enum):
        COMPLETE = "complete"
        UNKNOWN = "unknown"
    class Complete(Enum):
        HELD = "held"
        OTHER = "other"
    class EntryError(risk_journal.RiskOrderJournalError):
        def __init__(self):
            super().__init__("WRITER_FENCE_HELD")
            self.cleanup = Cleanup.COMPLETE
    class Manager:
        def __enter__(self):
            raise EntryError()
        def __exit__(self, *_args):
            pytest.fail("unentered manager exited")
    class Journal:
        def writer_fence(self, *_args, **_kwargs):
            return Manager()
    monkeypatch.setattr(risk_journal, "RiskFenceEntryError", EntryError, raising=False)
    monkeypatch.setattr(risk_journal, "RiskFenceEntryComplete", Complete, raising=False)
    monkeypatch.setattr(risk_journal, "consume_fence_entry_complete", lambda *_: Complete.HELD, raising=False)
    for _ in range(2):
        with pytest.raises(module.RiskServiceStartError) as raised:
            module.probe_writer_fence(Journal(), object())
        assert raised.value.code == "RISK_SERVICE_FENCE_HELD"
    assert probe_state == {"active": False, "uncertain": []}


@pytest.mark.parametrize("raise_exit", [False, True])
def test_probe_single_exit_and_failed_exit_latches(probe_state, raise_exit):
    calls = []
    class Manager:
        def __enter__(self):
            calls.append("enter")
            return object()
        def __exit__(self, *_args):
            calls.append("exit")
            if raise_exit:
                raise RuntimeError("synthetic exit")
    class Journal:
        def writer_fence(self, *_args, **_kwargs):
            calls.append("factory")
            return Manager()
    if raise_exit:
        with pytest.raises(module.RiskServiceStartError):
            module.probe_writer_fence(Journal(), object())
        assert len(probe_state["uncertain"]) == 1
        with pytest.raises(module.RiskServiceStartError):
            module.probe_writer_fence(Journal(), object())
    else:
        module.probe_writer_fence(Journal(), object())
        assert probe_state["uncertain"] == []
    assert calls == ["factory", "enter", "exit"]


def test_probe_reentrant_call_does_not_touch_another_journal(probe_state):
    calls = []
    class Other:
        def writer_fence(self, *_args, **_kwargs):
            calls.append("other")
            pytest.fail("unexpected nested probe")
    class Manager:
        def __enter__(self):
            with pytest.raises(module.RiskServiceStartError):
                module.probe_writer_fence(Other(), object())
            calls.append("enter")
            return object()
        def __exit__(self, *_args):
            calls.append("exit")
    class Journal:
        def writer_fence(self, *_args, **_kwargs):
            return Manager()
    module.probe_writer_fence(Journal(), object())
    assert calls == ["enter", "exit"]
    assert probe_state["uncertain"] == []


def test_probe_entry_interrupt_is_propagated_and_latched(probe_state):
    error = StopWait()
    class Manager:
        def __enter__(self):
            raise error
        def __exit__(self, *_args):
            pytest.fail("unentered manager exited")
    class Journal:
        def writer_fence(self, *_args, **_kwargs):
            return Manager()
    with pytest.raises(StopWait) as raised:
        module.probe_writer_fence(Journal(), object())
    assert raised.value is error
    assert len(probe_state["uncertain"]) == 1
    with pytest.raises(module.RiskServiceStartError):
        module.probe_writer_fence(Journal(), object())


@pytest.mark.parametrize("complete", [True, False])
def test_probe_rejects_unbound_actual_risk_entry_error(probe_state, monkeypatch, complete):
    import bt_api_risk.risk_order_journal as risk_journal

    retained, token = [], object()
    monkeypatch.setattr(risk_journal, "_FENCE_ENTRY_UNCERTAIN", retained)
    original = risk_journal.RiskOrderJournalError("WRITER_FENCE_HELD")
    calls = []
    class Manager:
        def __enter__(self):
            calls.append("enter")
            risk_journal._raise_fence_entry(original, complete=complete, retained=(token,))
        def __exit__(self, *_args):
            calls.append("exit")
    manager = Manager()
    class Journal:
        def writer_fence(self, *_args, **_kwargs):
            calls.append("factory")
            return manager
    journal, scope = Journal(), object()
    with pytest.raises(module.RiskServiceStartError) as raised:
        module.probe_writer_fence(journal, scope)
    assert calls == ["factory", "enter"]
    assert raised.value.code == "RISK_SERVICE_STORAGE_UNAVAILABLE"
    assert retained == ([] if complete else [(token, original)])
    _, _, actual_manager, _, native = probe_state["uncertain"][0]
    assert actual_manager is manager
    assert type(native) is risk_journal.RiskFenceEntryError
    assert native.original is original and native.retained == (token,)
    with pytest.raises(module.RiskServiceStartError):
        module.probe_writer_fence(Journal(), object())
    assert calls == ["factory", "enter"]


def test_probe_concurrent_attempt_is_refused_before_touching_other_journal(probe_state):
    entered, release = threading.Event(), threading.Event()
    events = []
    class Manager:
        def __enter__(self):
            events.append("enter")
            entered.set()
            assert release.wait(2)
            return object()
        def __exit__(self, *_args):
            events.append("exit")
    class Journal:
        def writer_fence(self, *_args, **_kwargs):
            return Manager()
    class Other:
        def writer_fence(self, *_args, **_kwargs):
            events.append("other")
            pytest.fail("unexpected concurrent probe")
    errors = []
    def first():
        try:
            module.probe_writer_fence(Journal(), object())
        except BaseException as error:
            errors.append(error)
    worker = threading.Thread(target=first)
    worker.start()
    assert entered.wait(2)
    try:
        with pytest.raises(module.RiskServiceStartError):
            module.probe_writer_fence(Other(), object())
    finally:
        release.set()
        worker.join(2)
    assert not worker.is_alive() and errors == []
    assert events == ["enter", "exit"]
    assert probe_state == {"active": False, "uncertain": []}


def test_probe_unknown_blocks_host_before_storage_actions(host, monkeypatch):
    calls = []
    class Manager:
        def __enter__(self):
            calls.append("probe_enter")
            raise RuntimeError("synthetic")
        def __exit__(self, *_args):
            pytest.fail("unexpected exit")
    class Journal:
        def writer_fence(self, *_args, **_kwargs):
            calls.append("probe_factory")
            return Manager()
    with pytest.raises(module.RiskServiceStartError):
        module.probe_writer_fence(Journal(), object())
    monkeypatch.setattr(module, "ensure_private_directory", lambda _: pytest.fail("unexpected storage action"))
    with pytest.raises(module.RiskServiceStartError):
        host.open_storage()
    assert calls == ["probe_factory", "probe_enter"]


def test_host_unknown_blocks_probe_before_journal_actions(host):
    calls = []
    class Journal:
        def writer_fence(self, *_args, **_kwargs):
            calls.append("probe_factory")
            pytest.fail("unexpected probe action")
    host._record_cleanup_uncertain(object(), "SYNTHETIC")
    with pytest.raises(module.RiskServiceStartError):
        module.probe_writer_fence(Journal(), object())
    assert calls == []


def test_probe_cannot_enter_during_host_storage_acquisition(host, monkeypatch):
    calls = []
    class Journal:
        def writer_fence(self, *_args, **_kwargs):
            calls.append("probe_factory")
            pytest.fail("unexpected probe action")
    host.config.storage_directory = object()
    def ensure(_):
        with pytest.raises(module.RiskServiceStartError):
            module.probe_writer_fence(Journal(), object())
        raise ValueError("synthetic stop")
    monkeypatch.setattr(module, "ensure_private_directory", ensure)
    with pytest.raises(ValueError):
        host.open_storage()
    assert calls == []
    assert module._PROBE_STATE == {"active": False, "uncertain": []}


def fake_builtin_competing_journal(monkeypatch):
    import os

    import bt_api_risk.risk_order_journal as risk_journal

    class Guard:
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
    class Lock:
        def acquire(self, *, blocking):
            return False
    journal = object.__new__(risk_journal.RiskOrderJournal)
    journal._process_id = os.getpid()
    journal._initialized = False
    journal._owner_id = "synthetic"
    journal._fence_key = lambda _: ("synthetic", "scope")
    journal._lock_path = lambda _: "synthetic"
    journal._open_and_lock = lambda *_args, **_kwargs: pytest.fail("unexpected lock open")
    journal._initialize = lambda **_: pytest.fail("unexpected initialization")
    journal._next_fencing_token = lambda *_args, **_kwargs: pytest.fail("unexpected token")
    journal._unlock_and_close_entry = lambda *_: pytest.fail("unexpected unlock")
    monkeypatch.setattr(risk_journal, "_PROCESS_LOCK_GUARD", Guard())
    monkeypatch.setattr(risk_journal, "_PROCESS_LOCKS", {"synthetic": Lock()})
    monkeypatch.setattr(risk_journal, "_thread_fences", lambda: {})
    monkeypatch.setattr(risk_journal, "_entry_scope_key", lambda *_: ("synthetic", "scope"))
    monkeypatch.setattr(risk_journal, "_trusted_fence_context", lambda *_: True)
    monkeypatch.setattr(risk_journal, "_trusted_fence_entry", lambda *_: True)
    monkeypatch.setattr(risk_journal, "_FENCE_ENTRY_QUARANTINED_KEYS", set())
    monkeypatch.setattr(risk_journal, "_FENCE_ENTRY_QUARANTINED_ALL", False)
    monkeypatch.setattr(risk_journal, "_FENCE_ENTRY_COMPLETE_RECEIPTS", {})
    return journal


def test_probe_builtin_bound_complete_remains_available(probe_state, monkeypatch):
    from bt_api_risk.core.admission import AccountScope

    journal = fake_builtin_competing_journal(monkeypatch)
    for _ in range(2):
        with pytest.raises(module.RiskServiceStartError) as raised:
            module.probe_writer_fence(journal, AccountScope("fake", "account", "sandbox"))
        assert raised.value.code == "RISK_SERVICE_FENCE_HELD"
    assert probe_state == {"active": False, "uncertain": []}


def test_host_builtin_bound_complete_remains_available(host, monkeypatch):
    import bt_api_risk.core.admission as admission
    import bt_api_risk.risk_order_journal as risk_journal

    fake = fake_builtin_competing_journal(monkeypatch)
    def init(self, *_args, **_kwargs):
        self.__dict__.update(fake.__dict__)
    monkeypatch.setattr(risk_journal.RiskOrderJournal, "__init__", init)
    monkeypatch.setattr(risk_journal, "_NATIVE_JOURNAL_INIT", init)
    admission_scope = risk_journal.AccountScope("fake", "account", "sandbox")
    monkeypatch.setattr(admission, "AccountScope", lambda *_: admission_scope)
    monkeypatch.setattr(module, "ensure_private_directory", lambda _: None)
    host.config.storage_directory = object()
    host.config.risk_database = object()
    host.config.provider = host.config.account_scope = host.config.environment = "synthetic"
    with pytest.raises(module.RiskServiceStartError) as raised:
        host.open_storage()
    assert raised.value.code == "RISK_SERVICE_FENCE_HELD"
    assert host._cleanup_uncertain is False
    assert module._PROBE_STATE == {"active": False, "uncertain": []}
    assert host.close() is True


def test_probe_preserves_entered_manager_when_host_becomes_unknown(host):
    events = []
    token = object()
    class Manager:
        def __enter__(self):
            events.append("enter")
            host._record_cleanup_uncertain(token, "SYNTHETIC")
            return object()
        def __exit__(self, *_args):
            events.append("exit")
    manager = Manager()
    class Journal:
        def writer_fence(self, *_args, **_kwargs):
            return manager
    with pytest.raises(module.RiskServiceStartError):
        module.probe_writer_fence(Journal(), object())
    assert events == ["enter"]
    assert host._cleanup_uncertain_resources == [token]
    assert any(manager is item for row in module._PROBE_STATE["uncertain"] for item in row)


def test_host_storage_factory_retained_when_other_host_becomes_unknown(host, monkeypatch):
    import bt_api_risk.core.admission as admission

    token, manager = object(), object()
    class Other:
        pass
    other = Other()
    def factory(*_args):
        with module._PROBE_GUARD:
            module._PROBE_STATE["uncertain"].append((other, token))
        return manager
    host.config.storage_directory = object()
    host.config.risk_database = object()
    host.config.provider = host.config.account_scope = host.config.environment = "synthetic"
    monkeypatch.setattr(module, "ensure_private_directory", lambda _: None)
    monkeypatch.setattr(admission, "AccountScope", lambda *_: object())
    monkeypatch.setattr(module, "_open_fenced_journal", factory)
    with pytest.raises(module.RiskServiceStartError):
        host.open_storage()
    assert host._cleanup_uncertain is True
    assert any(resource is manager for resource in host._cleanup_uncertain_resources)
    assert host.close() is False


def test_sdk_unissued_refusal_cannot_forge_clean_entry(host, monkeypatch):
    import bt_api_risk.core.admission as admission

    original, inner = RuntimeError("synthetic"), object()
    forged = module._FencedJournalEntryError("RISK_SERVICE_FENCE_HELD", original, inner)
    class Manager:
        def __enter__(self):
            raise forged
        def __exit__(self, *_args):
            pytest.fail("unentered manager exited")
    manager = Manager()
    host.config.storage_directory = object()
    host.config.risk_database = object()
    host.config.provider = host.config.account_scope = host.config.environment = "synthetic"
    monkeypatch.setattr(module, "ensure_private_directory", lambda _: None)
    monkeypatch.setattr(admission, "AccountScope", lambda *_: object())
    monkeypatch.setattr(module, "_open_fenced_journal", lambda *_: manager)
    with pytest.raises(module.RiskServiceStartError) as raised:
        host.open_storage()
    assert raised.value.code == "RISK_SERVICE_STORAGE_UNAVAILABLE"
    assert host._cleanup_uncertain is True
    assert host._uncertain_storage == (manager, forged)
    assert host.close() is False


@pytest.mark.parametrize("mutate", [False, True])
def test_sdk_complete_receipt_is_one_use_and_binds_originals(monkeypatch, mutate):
    from enum import Enum

    import bt_api_risk.risk_order_journal as risk_journal

    class Complete(Enum):
        HELD = "held"
        OTHER = "other"
    class EntryError(risk_journal.RiskOrderJournalError):
        pass
    native_error = EntryError("WRITER_FENCE_HELD")
    class Manager:
        def __enter__(self):
            raise native_error
        def __exit__(self, *_args):
            pytest.fail("unentered manager exited")
    native_manager = Manager()
    class Journal:
        @staticmethod
        def open_fenced(*_args, **_kwargs):
            return native_manager
    issued = [(native_manager, native_error)]
    def consume(manager, error):
        if issued and issued[0] == (manager, error):
            issued.pop()
            return Complete.HELD
        return None
    monkeypatch.setattr(module, "_ENTRY_PROOFS", weakref.WeakKeyDictionary())
    monkeypatch.setattr(risk_journal, "RiskOrderJournal", Journal)
    monkeypatch.setattr(risk_journal, "RiskFenceEntryError", EntryError)
    monkeypatch.setattr(risk_journal, "RiskFenceEntryComplete", Complete)
    monkeypatch.setattr(risk_journal, "consume_fence_entry_complete", consume)
    sdk_manager = module._open_fenced_journal(object(), object())
    with pytest.raises(module.RiskServiceStartError) as raised:
        sdk_manager.__enter__()
    refusal = raised.value
    if mutate:
        refusal.manager = object()
    result = module._consume_fence_entry_result(sdk_manager, refusal)
    assert result is (None if mutate else Complete.HELD)
    assert module._consume_fence_entry_result(sdk_manager, refusal) is None


def test_host_rejects_complete_receipt_from_different_outer_manager(host, monkeypatch):
    from enum import Enum

    import bt_api_risk.core.admission as admission
    import bt_api_risk.risk_order_journal as risk_journal

    class Complete(Enum):
        HELD = "held"
        OTHER = "other"
    class EntryError(risk_journal.RiskOrderJournalError):
        pass
    native_error = EntryError("WRITER_FENCE_HELD")
    class NativeManager:
        def __enter__(self):
            raise native_error
        def __exit__(self, *_args):
            pytest.fail("unentered native manager exited")
    native = NativeManager()
    class Journal:
        @staticmethod
        def open_fenced(*_args, **_kwargs):
            return native
    monkeypatch.setattr(risk_journal, "RiskOrderJournal", Journal)
    monkeypatch.setattr(risk_journal, "RiskFenceEntryError", EntryError)
    monkeypatch.setattr(risk_journal, "RiskFenceEntryComplete", Complete)
    monkeypatch.setattr(risk_journal, "consume_fence_entry_complete", lambda *_: Complete.HELD)
    original_outer = module._open_fenced_journal(object(), object())
    with pytest.raises(module.RiskServiceStartError) as captured:
        original_outer.__enter__()
    refusal = captured.value
    with pytest.raises(module.RiskServiceStartError):
        original_outer.__enter__()
    class WrongManager:
        def __enter__(self):
            return (_ for _ in ()).throw(refusal)
        def __exit__(self, *_args):
            pytest.fail("unentered wrong manager exited")
    wrong = WrongManager()
    host.config.storage_directory = object()
    host.config.risk_database = object()
    host.config.provider = host.config.account_scope = host.config.environment = "synthetic"
    monkeypatch.setattr(module, "ensure_private_directory", lambda _: None)
    monkeypatch.setattr(admission, "AccountScope", lambda *_: object())
    monkeypatch.setattr(module, "_open_fenced_journal", lambda *_: wrong)
    with pytest.raises(module.RiskServiceStartError) as raised:
        host.open_storage()
    assert raised.value.code == "RISK_SERVICE_STORAGE_UNAVAILABLE"
    assert host._uncertain_storage == (wrong, refusal)
    assert module._consume_fence_entry_result(original_outer, refusal) is None
    assert host.close() is False


def test_outer_fenced_manager_entry_is_single_use(monkeypatch):
    import bt_api_risk.risk_order_journal as risk_journal

    class Native:
        def __enter__(self):
            raise RuntimeError("synthetic")
        def __exit__(self, *_args):
            pytest.fail("unentered native manager exited")
    class Journal:
        @staticmethod
        def open_fenced(*_args, **_kwargs):
            return Native()
    monkeypatch.setattr(risk_journal, "RiskOrderJournal", Journal)
    outer = module._open_fenced_journal(object(), object())
    with pytest.raises(module.RiskServiceStartError):
        outer.__enter__()
    with pytest.raises(module.RiskServiceStartError):
        outer.__enter__()


@pytest.mark.parametrize("body_fails", [False, True])
def test_probe_body_unknown_preserves_manager_without_exit(host, body_fails):
    calls = []
    token, body_error = object(), ValueError("synthetic body")
    class Manager:
        def __enter__(self):
            calls.append("enter")
            return object()
        def __exit__(self, *_args):
            calls.append("exit")
    manager = Manager()
    class Journal:
        def writer_fence(self, *_args, **_kwargs):
            return manager
    expected = ValueError if body_fails else module.RiskServiceStartError
    with pytest.raises(expected) as raised, module._held_fence(Journal(), object()):
        host._record_cleanup_uncertain(token, "SYNTHETIC")
        if body_fails:
            raise body_error
    if body_fails:
        assert raised.value is body_error
    assert calls == ["enter"]
    assert any(manager is item for row in module._PROBE_STATE["uncertain"] for item in row)


def test_probe_exit_callback_unknown_preserves_without_retry(host):
    calls = []
    class Manager:
        def __enter__(self):
            calls.append("enter")
            return object()
        def __exit__(self, *_args):
            calls.append("exit")
            host._record_cleanup_uncertain(object(), "SYNTHETIC")
    manager = Manager()
    class Journal:
        def writer_fence(self, *_args, **_kwargs):
            return manager
    with pytest.raises(module.RiskServiceStartError):
        module.probe_writer_fence(Journal(), object())
    with pytest.raises(module.RiskServiceStartError):
        module.probe_writer_fence(Journal(), object())
    assert calls == ["enter", "exit"]
    assert any(manager is item for row in module._PROBE_STATE["uncertain"] for item in row)


def test_host_unknown_state_is_published_under_global_guard(host):
    started = threading.Event()
    token = object()
    def publish():
        started.set()
        host._record_cleanup_uncertain(token, "SYNTHETIC")
    with module._PROBE_GUARD:
        worker = threading.Thread(target=publish)
        worker.start()
        assert started.wait(2)
        assert host._cleanup_uncertain is False
        assert module._PROBE_STATE["uncertain"] == []
    worker.join(2)
    assert not worker.is_alive()
    assert host._cleanup_uncertain is True
    assert module._PROBE_STATE["uncertain"] == [(host, token, "SYNTHETIC")]


def test_probe_body_error_not_suppressed_if_exit_publishes_unknown(host):
    calls, body_error = [], ValueError("synthetic body")
    class Manager:
        def __enter__(self):
            calls.append("enter")
            return object()
        def __exit__(self, *_args):
            calls.append("exit")
            host._record_cleanup_uncertain(object(), "SYNTHETIC")
            return True
    manager = Manager()
    class Journal:
        def writer_fence(self, *_args, **_kwargs):
            return manager
    with pytest.raises(ValueError) as raised, module._held_fence(Journal(), object()):
        raise body_error
    assert raised.value is body_error
    assert calls == ["enter", "exit"]
    assert any(manager is item for row in module._PROBE_STATE["uncertain"] for item in row)


def issue_fake_sdk_receipt(monkeypatch, category_name="HELD", *, enter=True):
    from enum import Enum

    import bt_api_risk.risk_order_journal as risk_journal

    class Complete(Enum):
        HELD = "held"
        OTHER = "other"
    class EntryError(risk_journal.RiskOrderJournalError):
        pass
    original = EntryError("WRITER_FENCE_HELD")
    class Manager:
        def __enter__(self):
            raise original
        def __exit__(self, *_args):
            pytest.fail("unentered manager exited")
    native = Manager()
    class Journal:
        @staticmethod
        def open_fenced(*_args, **_kwargs):
            return native
    receipt = Complete[category_name]
    issued = [(native, original)]
    def consume(manager, error):
        if issued and issued[0] == (manager, error):
            issued.pop()
            return receipt
        return None
    monkeypatch.setattr(risk_journal, "RiskOrderJournal", Journal)
    monkeypatch.setattr(risk_journal, "RiskFenceEntryError", EntryError)
    monkeypatch.setattr(risk_journal, "RiskFenceEntryComplete", Complete)
    monkeypatch.setattr(risk_journal, "consume_fence_entry_complete", consume)
    outer = module._open_fenced_journal(object(), object())
    if not enter:
        return outer, None, Complete
    try:
        outer.__enter__()
    except module.RiskServiceStartError as error:
        return outer, error, Complete
    pytest.fail("entry unexpectedly succeeded")


@pytest.mark.parametrize("category", ["HELD", "OTHER"])
def test_host_uses_saved_category_despite_modified_error_code(host, monkeypatch, category):
    import bt_api_risk.core.admission as admission

    outer, _, _ = issue_fake_sdk_receipt(monkeypatch, category, enter=False)
    original_consume = module._consume_fence_entry_result
    def mutate(manager, error):
        error.code = "RISK_SERVICE_FENCE_HELD" if category == "OTHER" else "RISK_SERVICE_STORAGE_UNAVAILABLE"
        error.args = ("synthetic private replacement",)
        return original_consume(manager, error)
    host.config.storage_directory = object()
    host.config.risk_database = object()
    host.config.provider = host.config.account_scope = host.config.environment = "synthetic"
    monkeypatch.setattr(module, "ensure_private_directory", lambda _: None)
    monkeypatch.setattr(admission, "AccountScope", lambda *_: object())
    monkeypatch.setattr(module, "_open_fenced_journal", lambda *_: outer)
    monkeypatch.setattr(module, "_consume_fence_entry_result", mutate)
    with pytest.raises(module.RiskServiceStartError) as raised:
        host.open_storage()
    expected = "RISK_SERVICE_FENCE_HELD" if category == "HELD" else "RISK_SERVICE_STORAGE_UNAVAILABLE"
    assert raised.value.code == expected and raised.value.args == (expected,)
    assert host._cleanup_uncertain is False
    assert host.close() is True


def test_unused_complete_receipt_does_not_keep_fake_objects_alive(monkeypatch):
    monkeypatch.setattr(module, "_ENTRY_PROOFS", weakref.WeakKeyDictionary())
    def issue_and_forget():
        outer, refusal, _ = issue_fake_sdk_receipt(monkeypatch)
        outer_ref, refusal_ref = weakref.ref(outer), weakref.ref(refusal)
        # These are only this test's COMPLETE fake exceptions; drop traceback cycles.
        refusal.original.__traceback__ = None
        refusal.__traceback__ = None
        refusal.__context__ = None
        assert len(module._ENTRY_PROOFS) == 1
        return outer_ref, refusal_ref
    outer_ref, refusal_ref = issue_and_forget()
    assert outer_ref() is None and refusal_ref() is None
    assert len(module._ENTRY_PROOFS) == 0


def test_complete_receipt_is_consumed_once_across_threads(monkeypatch):
    monkeypatch.setattr(module, "_ENTRY_PROOFS", weakref.WeakKeyDictionary())
    outer, refusal, Complete = issue_fake_sdk_receipt(monkeypatch)
    barrier = threading.Barrier(3)
    results = []
    def consume():
        barrier.wait()
        results.append(module._consume_fence_entry_result(outer, refusal))
    workers = [threading.Thread(target=consume) for _ in range(2)]
    for worker in workers:
        worker.start()
    barrier.wait()
    for worker in workers:
        worker.join(2)
    assert all(not worker.is_alive() for worker in workers)
    assert len(results) == 2 and results.count(Complete.HELD) == 1 and results.count(None) == 1


def test_unweakrefable_native_manager_is_unknown(host, monkeypatch):
    from enum import Enum

    import bt_api_risk.core.admission as admission
    import bt_api_risk.risk_order_journal as risk_journal

    class Complete(Enum):
        HELD = "held"
        OTHER = "other"
    class EntryError(risk_journal.RiskOrderJournalError):
        pass
    original = EntryError("WRITER_FENCE_HELD")
    class Manager:
        __slots__ = ()
        def __enter__(self):
            raise original
        def __exit__(self, *_args):
            pytest.fail("unentered manager exited")
    native = Manager()
    class Journal:
        @staticmethod
        def open_fenced(*_args, **_kwargs):
            return native
    monkeypatch.setattr(risk_journal, "RiskOrderJournal", Journal)
    monkeypatch.setattr(risk_journal, "RiskFenceEntryError", EntryError)
    monkeypatch.setattr(risk_journal, "RiskFenceEntryComplete", Complete)
    monkeypatch.setattr(risk_journal, "consume_fence_entry_complete", lambda *_: Complete.HELD)
    monkeypatch.setattr(module, "ensure_private_directory", lambda _: None)
    monkeypatch.setattr(admission, "AccountScope", lambda *_: object())
    host.config.storage_directory = object()
    host.config.risk_database = object()
    host.config.provider = host.config.account_scope = host.config.environment = "synthetic"
    with pytest.raises(module.RiskServiceStartError) as raised:
        host.open_storage()
    assert raised.value.code == "RISK_SERVICE_STORAGE_UNAVAILABLE"
    assert raised.value.original is original and raised.value.manager is native
    assert host._cleanup_uncertain is True
    assert host.close() is False


def test_saved_category_interruption_is_retained_as_unknown(host, monkeypatch):
    import bt_api_risk.core.admission as admission

    outer, _, _ = issue_fake_sdk_receipt(monkeypatch, enter=False)
    original_code = module._fence_entry_result_code
    interruption = StopWait()
    calls = []
    def code(result):
        calls.append(1)
        if len(calls) == 2:
            raise interruption
        return original_code(result)
    host.config.storage_directory = object()
    host.config.risk_database = object()
    host.config.provider = host.config.account_scope = host.config.environment = "synthetic"
    monkeypatch.setattr(module, "ensure_private_directory", lambda _: None)
    monkeypatch.setattr(admission, "AccountScope", lambda *_: object())
    monkeypatch.setattr(module, "_open_fenced_journal", lambda *_: outer)
    monkeypatch.setattr(module, "_fence_entry_result_code", code)
    with pytest.raises(StopWait) as raised:
        host.open_storage()
    assert raised.value is interruption and len(calls) == 2
    assert host._storage_cleanup_uncertain is True
    assert host._uncertain_storage[0] is outer
    assert host._cleanup_uncertain_resources[0][2] is interruption
    assert host.close() is False


def test_probe_category_interruption_retains_both_errors(probe_state, monkeypatch):
    entry_error, interruption = RuntimeError("synthetic entry"), StopWait()
    class Manager:
        def __enter__(self):
            raise entry_error
        def __exit__(self, *_args):
            pytest.fail("unentered manager exited")
    manager = Manager()
    class Journal:
        def writer_fence(self, *_args, **_kwargs):
            return manager
    def interrupt(_result):
        raise interruption
    monkeypatch.setattr(module, "_risk_fence_entry_is_held", interrupt)
    with pytest.raises(StopWait) as raised:
        module.probe_writer_fence(Journal(), object())
    assert raised.value is interruption
    assert len(probe_state["uncertain"]) == 1
    assert probe_state["uncertain"][0][2] is manager
    assert probe_state["uncertain"][0][4:] == (entry_error, interruption)


@pytest.mark.parametrize("stage", ["unpack", "restrict", "ledger", "delegate", "policy", "rules"])
@pytest.mark.parametrize("publish_unknown", [False, True])
@pytest.mark.parametrize("interrupt", [False, True])
@pytest.mark.parametrize("invoke_start", [False, True])
def test_storage_exception_checks_cross_host_unknown_before_cleanup(host, monkeypatch, stage, publish_unknown, interrupt, invoke_start):
    from contextlib import ExitStack

    import bt_api_risk.core.admission as admission
    import bt_api_risk.gateway_delegate_journal as delegate_module
    import bt_api_risk.pretrade as pretrade

    other = module.RiskServiceHost(host.config)
    other._storage_owner = threading.current_thread()
    # This test drives open_storage()/start() from a host that has not opened
    # storage yet. The shared fixture represents a completed open (owner,
    # acquiring stack and active root); leaving that shape in place would make
    # this host's own cleanup collide with the fixture's root.
    host._stack = ExitStack()
    host._storage_owner = None
    host._resource_owner = None
    module._STORAGE_ACTIVE_ROOTS.pop(id(host), None)

    class ReceiptJournal:
        # A close is only proven by the fenced-journal receipt; the host must
        # not treat a bare context manager as a completed cleanup.
        def prepare_borrowed_fence_close(self, *_args):
            pass

        def consume_borrowed_fence_close(self, *_args):
            from bt_api_risk.risk_order_journal import RiskFenceExitCleanup

            return RiskFenceExitCleanup.COMPLETE

    token, journal, fence = object(), ReceiptJournal(), object()
    original = StopWait() if interrupt else ValueError("synthetic callback failure")
    calls = []
    def step(name, result):
        def call(*_args, **_kwargs):
            calls.append(name)
            if name == stage:
                if publish_unknown:
                    other._record_cleanup_uncertain(token, "SYNTHETIC")
                raise original
            return result
        return call
    class Pair:
        def __iter__(self):
            step("unpack", None)()
            return iter((journal, fence))
    pair = Pair()

    class Inner:
        def __exit__(self, *_args):
            calls.append("exit")
            return False

    inner = Inner()
    manager = module._FencedJournalManager(Path("synthetic-risk.sqlite"), object())

    def open_context(outer, _path, _scope):
        calls.append("enter")
        outer._inner_manager = inner
        outer._binding = (inner, journal, fence)
        return pair

    host.config.storage_directory = object()
    host.config.risk_database = object()
    host.config.provider = host.config.account_scope = host.config.environment = "synthetic"
    host.config.trusted_rules = {}
    monkeypatch.setattr(module, "ensure_private_directory", lambda _: None)
    monkeypatch.setattr(admission, "AccountScope", lambda *_: object())
    monkeypatch.setattr(module, "_open_fenced_journal", lambda *_: manager)
    monkeypatch.setattr(module, "_open_fenced_journal_context", open_context)
    monkeypatch.setattr(module, "restrict_file", step("restrict", None))
    monkeypatch.setattr(pretrade, "PretradeLedger", step("ledger", object()))
    monkeypatch.setattr(delegate_module, "RiskGatewayDelegateJournal", step("delegate", object()))
    monkeypatch.setattr(pretrade, "PolicyStore", step("policy", object()))
    monkeypatch.setattr(pretrade, "RuleRegistry", step("rules", object()))
    monkeypatch.setattr(module, "fault_point_from_environment", lambda _: None)
    with pytest.raises((ValueError, module.RiskServiceStartError, StopWait)) as raised:
        host.start() if invoke_start else host.open_storage()
    order = ["enter", "unpack", "restrict", "ledger", "delegate", "policy", "rules"]
    expected = order[:order.index(stage) + 1]
    # Which failures must retain instead of closing?
    # - A concurrently published UNKNOWN blocks any cleanup.
    # - An interruption keeps the entry/result-handoff in-flight mark: unwinding
    #   during teardown cannot be shown to complete.
    # - RiskGatewayDelegateJournal has no COMMIT/ROLLBACK-and-close receipt, so
    #   even an ordinary constructor exception stays UNKNOWN.
    in_flight_unknown = stage == "delegate" or (interrupt and stage in ("unpack", "ledger"))
    if publish_unknown or in_flight_unknown:
        assert calls == expected
        assert host._cleanup_uncertain is True
        assert host in module._RETAINED_HOSTS
        if publish_unknown:
            assert other in module._RETAINED_HOSTS
        resources = host._cleanup_uncertain_resources
        if stage == "delegate":
            # The delegate handler records the provisional manager and both
            # errors before deciding the outcome is unknown; the outer handler
            # additionally records the exact acquiring stack.
            five = [item for item in resources
                    if isinstance(item, tuple) and len(item) == 5]
            assert len(five) == 1
            _acquiring, actual_manager, actual_journal, actual_fence, body_error = five[0]
            assert actual_manager is manager and (actual_journal, actual_fence) == (journal, fence)
        else:
            six = [item for item in resources
                   if isinstance(item, tuple) and len(item) == 6 and isinstance(item[0], ExitStack)]
            assert len(six) == 1
            acquiring, actual_manager, actual_journal, actual_fence, actual_result, body_error = six[0]
            assert acquiring is not host._stack and actual_manager is manager and actual_result is pair
            assert (actual_journal, actual_fence) == ((None, None) if stage == "unpack" else (journal, fence))
        if in_flight_unknown:
            # A retained in-flight UNKNOWN reports its own finite code rather
            # than the original callback error (it keeps the latch visible).
            assert body_error is original
            assert isinstance(raised.value, module.RiskServiceStartError)
            expected_code = ("RISK_SERVICE_STORAGE_CLEANUP_UNCERTAIN" if stage == "unpack"
                             else "RISK_SERVICE_STORAGE_INIT_COMMIT_UNCERTAIN")
            assert raised.value.code == expected_code
        else:
            # Only a concurrently published UNKNOWN keeps the host here; the
            # failure the caller observes is the one recorded for cleanup.
            assert body_error is raised.value
        assert host.close() is False
        assert calls == expected
    else:
        assert calls == expected + ["exit"]
        assert host._cleanup_uncertain is False and host not in module._RETAINED_HOSTS
        assert host.close() is True


def test_global_unknown_empty_host_finishes_without_touching_global_state(host):
    from contextlib import ExitStack

    other = module.RiskServiceHost(host.config)
    token = object()
    other._record_cleanup_uncertain(token, "SYNTHETIC")
    before = list(module._PROBE_STATE["uncertain"])
    host._storage_owner = None
    host._stack = ExitStack()
    assert host.close() is True
    assert host._cleanup_state == "complete"
    assert host not in module._RETAINED_HOSTS
    assert module._PROBE_STATE["uncertain"] == before
    assert host.events == []


def test_global_unknown_with_resources_refuses_before_first_action(host):
    attach(host)
    other = module.RiskServiceHost(host.config)
    other._record_cleanup_uncertain(object(), "SYNTHETIC")
    assert_sticky(host, [])
    assert host._cleanup_uncertain is True


@pytest.mark.parametrize("stage", ["port_state", "service", "port", "runtime", "stack"])
def test_other_host_unknown_during_close_stops_next_component(host, stage):
    other = module.RiskServiceHost(host.config)
    token = object()
    def publish():
        other._record_cleanup_uncertain(token, "SYNTHETIC")
    attach(host)
    if stage == "port_state":
        class PortState:
            @property
            def cleanup_uncertain(self):
                host.events.append("port_state")
                publish()
                return False
            def close(self):
                pytest.fail("port close after unknown")
        host.order_port = PortState()
        expected = ["port_state"]
    elif stage == "service":
        def close_service():
            host.events.append("service")
            publish()
            host.service_host.state = "closed"
        host.service_host.close = close_service
        expected = ["service"]
    elif stage == "port":
        def close_port():
            host.events.append("port")
            publish()
            return True
        host.order_port.close = close_port
        expected = ["service", "port"]
    elif stage == "runtime":
        class Snapshot:
            @property
            def state(self):
                pytest.fail("snapshot inspected after unknown")
        def close_runtime(**_kwargs):
            host.events.append("last")
            publish()
            return Snapshot()
        host._runtimes[-1].close = close_runtime
        expected = ["service", "port", "last"]
    else:
        def close_stack():
            host.events.append("stack")
            publish()
        host._stack.close = close_stack
        expected = ["service", "port", "last", "first", "stack"]
    assert_sticky(host, expected)
    assert host._cleanup_uncertain is True
    assert other in module._RETAINED_HOSTS


def test_start_does_not_close_resources_when_early_callback_publishes_global_unknown(host, monkeypatch):
    attach(host)
    other = module.RiskServiceHost(host.config)
    host.config.storage_directory = object()
    monkeypatch.setattr(module, "fault_point_from_environment", lambda _: None)
    def fail(_):
        other._record_cleanup_uncertain(object(), "SYNTHETIC")
        raise ValueError("synthetic early error")
    monkeypatch.setattr(module, "ensure_private_directory", fail)
    with pytest.raises(ValueError, match="synthetic early error"):
        host.start()
    assert host.events == []
    assert host._cleanup_uncertain is True
    assert host in module._RETAINED_HOSTS


def test_other_host_unknown_blocks_runtime_cleanup_before_call(host):
    other = module.RiskServiceHost(host.config)
    runtime = Runtime(host, "runtime", snapshot())
    other._record_cleanup_uncertain(object(), "SYNTHETIC")
    assert host._close_runtime_once(runtime, 1) is False
    assert host.events == []
    assert any(item is runtime for item in host._cleanup_uncertain_resources)


def test_other_host_unknown_during_runtime_close_skips_snapshot(host):
    other = module.RiskServiceHost(host.config)
    class Snapshot:
        @property
        def state(self):
            pytest.fail("snapshot inspected after UNKNOWN")
    class RuntimeFake:
        def close(self, **_kwargs):
            host.events.append("runtime")
            other._record_cleanup_uncertain(object(), "SYNTHETIC")
            return Snapshot()
    runtime = RuntimeFake()
    assert host._close_runtime_once(runtime, 1) is False
    assert host.events == ["runtime"]
    assert any(item is runtime for item in host._cleanup_uncertain_resources)


def test_other_host_unknown_during_snapshot_attribute_stops_inspection(host):
    other = module.RiskServiceHost(host.config)
    class Snapshot:
        @property
        def state(self):
            host.events.append("state")
            other._record_cleanup_uncertain(object(), "SYNTHETIC")
            return "closed"
        @property
        def thread_alive(self):
            pytest.fail("later snapshot field inspected after UNKNOWN")
    class RuntimeFake:
        def close(self, **_kwargs):
            host.events.append("runtime")
            return Snapshot()
    runtime = RuntimeFake()
    assert host._close_runtime_once(runtime, 1) is False
    assert host.events == ["runtime", "state"]
    assert any(item is runtime for item in host._cleanup_uncertain_resources)


def test_other_host_unknown_blocks_reconnect_before_status(host):
    other = module.RiskServiceHost(host.config)
    class Observer:
        def status(self):
            pytest.fail("observer status after UNKNOWN")
        def close(self, **_kwargs):
            pytest.fail("observer close after UNKNOWN")
    runtime = Observer()
    host._observer_runtime = runtime
    host._observer_route = object()
    host.pump = object()
    other._record_cleanup_uncertain(object(), "SYNTHETIC")
    with pytest.raises(module.RiskServiceStartError):
        host._reconnect_observer()
    assert host._observer_runtime is runtime
    assert any(item is runtime for item in host._cleanup_uncertain_resources)


def test_other_host_unknown_during_delegate_start_prevents_cleanup(host, monkeypatch):
    other = module.RiskServiceHost(host.config)
    class RuntimeFake:
        def start(self, **_kwargs):
            host.events.append("start")
            other._record_cleanup_uncertain(object(), "SYNTHETIC")
            raise ValueError("synthetic start failure")
        def close(self, **_kwargs):
            pytest.fail("delegate close after UNKNOWN")
    runtime = RuntimeFake()
    host.config.call_timeout_seconds = 1
    monkeypatch.setattr(host, "_service_runtime", lambda *_args, **_kwargs: runtime)
    route = SimpleNamespace(scope=SimpleNamespace(run_id="synthetic"))
    with pytest.raises(ValueError, match="synthetic start failure"):
        host._connect_delegate(route)
    assert host.events == ["start"]
    assert any(item is runtime for item in host._cleanup_uncertain_resources)


def test_other_host_unknown_blocks_port_before_handle_cleanup(host):
    other = module.RiskServiceHost(host.config)
    class Handle:
        def close(self):
            pytest.fail("delegate handle close after UNKNOWN")
    handle = Handle()
    host.order_port = make_gateway_port(host, handle)
    slot = host.order_port._slots["synthetic"]
    other._record_cleanup_uncertain(object(), "SYNTHETIC")
    host.order_port.note_run("synthetic", "second")
    assert slot.handle is handle
    assert host.order_port.cleanup_uncertain is True
    assert host._cleanup_uncertain is True
    assert host.close() is False


@pytest.mark.parametrize("stage", ["before", "during"])
def test_delegate_handle_checks_shared_unknown_without_retry(host, stage):
    other = module.RiskServiceHost(host.config)
    calls = []
    class RuntimeFake:
        def close(self, **_kwargs):
            calls.append("close")
            if stage == "during":
                other._record_cleanup_uncertain(object(), "SYNTHETIC")
            return snapshot()
    runtime = RuntimeFake()
    handle = module._DelegateHandle(runtime, object(), object(), cleanup_guard=host._retain_if_global_unknown)
    if stage == "before":
        other._record_cleanup_uncertain(object(), "SYNTHETIC")
    with pytest.raises(module.RiskServiceStartError):
        handle.close()
    assert calls == ([] if stage == "before" else ["close"])
    assert any(item is runtime for item in host._cleanup_uncertain_resources)


@pytest.mark.parametrize("kind", ["market", "account"])
@pytest.mark.parametrize("stage", ["before", "during"])
def test_owner_extension_checks_shared_unknown_without_retry(host, kind, stage):
    other = module.RiskServiceHost(host.config)
    calls = []
    class Owner:
        def close(self):
            calls.append("close")
            if stage == "during":
                other._record_cleanup_uncertain(object(), "SYNTHETIC")
        @property
        def cleanup_uncertainties(self):
            pytest.fail("owner observed after UNKNOWN")
    owner = Owner()
    if kind == "market":
        extension = module._MarketOwnerExtension(object(), stream_scope=object(), security_profiles={},
                                                 cleanup_guard=host._retain_if_global_unknown)
        extension._market = owner
    else:
        extension = module._AccountOwnerExtension(object(), account_ref="synthetic", scope_ref="synthetic",
                                                  cleanup_guard=host._retain_if_global_unknown)
        extension._owner = owner
    if stage == "before":
        other._record_cleanup_uncertain(object(), "SYNTHETIC")
    with pytest.raises(module.RiskServiceStartError):
        extension.close()
    assert calls == ([] if stage == "before" else ["close"])
    assert extension.cleanup_uncertainties == 1
    assert any(item is extension for item in host._cleanup_uncertain_resources)


@pytest.mark.parametrize("kind", ["service", "application"])
def test_risk_threaded_runtime_receives_sticky_opt_in(host, monkeypatch, kind):
    import bt_api_py.service_runtime as runtime_module
    import bt_api_py.service_runtime_thread as thread_module

    captured = {}
    sentinel = object()
    def factory(_runtime_factory, **kwargs):
        captured.update(kwargs)
        captured["raw_factory"] = _runtime_factory
        return sentinel
    class Raw:
        def __init__(self, *_args, **kwargs):
            self.options = kwargs
    host.config.gateway = SimpleNamespace(
        delegate=SimpleNamespace(principal_ref="synthetic", actor_ref="synthetic"),
        control=SimpleNamespace(principal_ref="synthetic", actor_ref="synthetic"),
    )
    host.config.execution_domain_ref = host.config.account_scope = "synthetic"
    monkeypatch.setattr(thread_module, "ThreadedServiceClientRuntime", factory)
    monkeypatch.setattr(runtime_module, "ServiceClientRuntime", Raw)
    monkeypatch.setattr(runtime_module, "ZmqTransportFactory", lambda *_: object())
    monkeypatch.setattr(module, "WireIdentity", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "WireScope", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "_gateway_client_config", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "_control_client_config", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(host, "_curve", lambda *_: object())
    monkeypatch.setattr(host, "_peer", lambda: object())
    result = (host._service_runtime("synthetic", object(), frozenset()) if kind == "service"
              else host._application_runtime({"synthetic": frozenset()}))
    assert result is sentinel
    assert captured["cleanup_unknown"]() is False
    raw = captured["raw_factory"]()
    assert raw.options["cleanup_unknown"]() is False
    raw.options["on_cleanup_sticky"]()
    assert raw.options["cleanup_unknown"]() is True
    assert any(item is raw for item in host._cleanup_uncertain_resources)
    captured["on_cleanup_sticky"](sentinel)
    assert captured["cleanup_unknown"]() is True
    assert any(item is sentinel for item in host._cleanup_uncertain_resources)


def test_market_extension_receives_strict_owner_policy(host, monkeypatch):
    import bt_api_py.gateway_strategy_market as market_module

    captured, calls = {}, []
    fake = SimpleNamespace(start=lambda _: calls.append("start"))
    def factory(*_args, **kwargs):
        captured.update(kwargs)
        return fake
    monkeypatch.setattr(market_module, "GatewayMarketOwner", factory)
    extension = module._MarketOwnerExtension(
        object(), stream_scope=object(), security_profiles={}, cleanup_guard=host._retain_if_global_unknown,
        runtime_cleanup_unknown=host._runtime_cleanup_unknown, on_cleanup_sticky=host._runtime_cleanup_sticky,
    )
    extension.start(object())
    assert calls == ["start"] and captured["strict_cleanup"] is True
    assert captured["cleanup_guard"]() is False
    captured["cleanup_uncertain_callback"]()
    assert captured["cleanup_guard"]() is True
    assert any(item is extension for item in host._cleanup_uncertain_resources)


def test_other_host_unknown_during_replacement_start_prevents_cleanup(host, monkeypatch):
    other = module.RiskServiceHost(host.config)
    class Replacement:
        def start(self, **_kwargs):
            host.events.append("start")
            other._record_cleanup_uncertain(object(), "SYNTHETIC")
            raise ValueError("synthetic start failure")
        def close(self, **_kwargs):
            pytest.fail("replacement close after UNKNOWN")
    replacement = Replacement()
    host.config.call_timeout_seconds = 1
    host.config.maintain_seconds = 1
    host._monotonic = lambda: 1
    host._observer_route = SimpleNamespace(scope=object())
    host.pump = SimpleNamespace(_gap=lambda _: host.events.append("gap"))
    monkeypatch.setattr(host, "_service_runtime", lambda *_args, **_kwargs: replacement)
    host._reconnect_observer()
    assert host.events == ["gap", "start"]
    assert host._observer_runtime is replacement and host._runtimes == [replacement]
    assert any(item is replacement for item in host._cleanup_uncertain_resources)


def test_other_host_unknown_during_gap_prevents_observer_cleanup(host):
    other = module.RiskServiceHost(host.config)
    class Observer:
        def status(self):
            host.events.append("status")
            return SimpleNamespace(state="failed")
        def close(self, **_kwargs):
            pytest.fail("observer close after UNKNOWN")
    runtime = Observer()
    host.config.maintain_seconds = 1
    host._monotonic = lambda: 1
    host._observer_runtime = runtime
    host._observer_route = object()
    def gap(_reason):
        host.events.append("gap")
        other._record_cleanup_uncertain(object(), "SYNTHETIC")
    host.pump = SimpleNamespace(_gap=gap)
    with pytest.raises(module.RiskServiceStartError):
        host._reconnect_observer()
    assert host.events == ["status", "gap"]
    assert any(item is runtime for item in host._cleanup_uncertain_resources)


def test_account_extension_receives_strict_owner_policy(host, monkeypatch):
    import bt_api_py.gateway_strategy_account as account_module

    captured, calls = {}, []
    fake = SimpleNamespace(start=lambda _: calls.append("start"))
    def factory(*_args, **kwargs):
        captured.update(kwargs)
        return fake
    monkeypatch.setattr(account_module, "GatewayAccountOwner", factory)
    extension = module._AccountOwnerExtension(
        object(), account_ref="synthetic", scope_ref="synthetic", cleanup_guard=host._retain_if_global_unknown,
        runtime_cleanup_unknown=host._runtime_cleanup_unknown, on_cleanup_sticky=host._runtime_cleanup_sticky,
    )
    extension.start(object())
    assert calls == ["start"] and captured["strict_cleanup"] is True
    assert captured["cleanup_guard"]() is False
    captured["cleanup_uncertain_callback"]()
    assert captured["cleanup_guard"]() is True
    assert any(item is extension for item in host._cleanup_uncertain_resources)


@pytest.mark.parametrize("kind", ["market", "account"])
def test_owner_extension_without_opt_in_preserves_constructor_contract(monkeypatch, kind):
    import bt_api_py.gateway_strategy_account as account_module
    import bt_api_py.gateway_strategy_market as market_module

    captured = {}
    def factory(*_args, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(start=lambda _: None)
    if kind == "market":
        monkeypatch.setattr(market_module, "GatewayMarketOwner", factory)
        extension = module._MarketOwnerExtension(object(), stream_scope=object(), security_profiles={})
    else:
        monkeypatch.setattr(account_module, "GatewayAccountOwner", factory)
        extension = module._AccountOwnerExtension(object(), account_ref="synthetic", scope_ref="synthetic")
    extension.start(object())
    assert "strict_cleanup" not in captured
    assert "cleanup_guard" not in captured
    assert "cleanup_uncertain_callback" not in captured


def test_raw_runtime_early_sticky_notification_retains_returned_object(host):
    created = []
    class Raw:
        def __init__(self, *, cleanup_unknown, on_cleanup_sticky):
            created.append(self)
            assert cleanup_unknown() is False
            on_cleanup_sticky()
    with pytest.raises(module.RiskServiceStartError):
        host._construct_risk_raw_runtime(Raw)
    assert len(created) == 1
    assert any(item is created[0] for item in host._cleanup_uncertain_resources)
    assert any(isinstance(item, dict) and item["early_unknown"]
               for item in host._cleanup_uncertain_resources)


def test_raw_runtime_constructor_exception_retains_original(host):
    error = ValueError("synthetic constructor failure")
    def factory(**_kwargs):
        raise error
    with pytest.raises(ValueError) as raised:
        host._construct_risk_raw_runtime(factory)
    assert raised.value is error
    assert any(isinstance(item, tuple) and item[1] is error
               for item in host._cleanup_uncertain_resources)


def test_other_host_unknown_blocks_raw_runtime_constructor(host):
    other = module.RiskServiceHost(host.config)
    other._record_cleanup_uncertain(object(), "SYNTHETIC")
    def factory(**_kwargs):
        pytest.fail("raw constructor after UNKNOWN")
    with pytest.raises(module.RiskServiceStartError):
        host._construct_risk_raw_runtime(factory)
    assert host._cleanup_uncertain is True


@pytest.mark.parametrize("stage", ["serve", "observer", "pump", "market", "account", "financial", "maintain"])
def test_run_once_stops_when_other_host_publishes_unknown(host, monkeypatch, stage):
    other = module.RiskServiceHost(host.config)
    def action(name):
        def call(*_args):
            host.events.append(name)
            if name == stage:
                other._record_cleanup_uncertain(object(), "SYNTHETIC")
                if name == "observer":
                    # run_once calls _reconnect_observer directly, and the real
                    # method checks global cleanup readiness itself after its
                    # callbacks. A bare replacement must preserve that step or
                    # it tests a call path production never uses.
                    host._check_global_cleanup_ready(host)
        return call
    host.config.loop_timeout_ms = 1
    host.config.observation_poll_seconds = 1
    host.config.market_poll_seconds = 1
    host.config.account_snapshot_seconds = 1
    host.config.strategy_financial_seconds = 1
    host.config.maintain_seconds = 1
    host._monotonic = lambda: 1
    host._next = dict.fromkeys(("observation", "market", "account", "financial", "maintain"), 0)
    host.service_host = SimpleNamespace(serve_once=action("serve"))
    host.ledger = SimpleNamespace(fence=object())
    monkeypatch.setattr(host, "_reconnect_observer", action("observer"))
    host.pump = SimpleNamespace(poll=action("pump"))
    host.market = SimpleNamespace(poll=action("market"))
    host.account = SimpleNamespace(refresh=action("account"))
    host.financial = SimpleNamespace(refresh=action("financial"))
    host.engine = SimpleNamespace(maintain=action("maintain"))
    order = ["serve", "observer", "pump", "market", "account", "financial", "maintain"]
    with pytest.raises(module.RiskServiceStartError):
        host.run_once()
    assert host.events == order[:order.index(stage) + 1]
    assert host._cleanup_uncertain is True
    assert host.close() is False


def test_runtime_factory_stops_after_other_host_publishes_unknown(host, monkeypatch):
    other = module.RiskServiceHost(host.config)
    host.config.gateway = SimpleNamespace(delegate=object())
    def config(*_args, **_kwargs):
        other._record_cleanup_uncertain(object(), "SYNTHETIC")
        return object()
    monkeypatch.setattr(module, "_gateway_client_config", config)
    monkeypatch.setattr(module, "WireIdentity", lambda *_args, **_kwargs: pytest.fail("identity after UNKNOWN"))
    # Production always builds a runtime through _call_lifecycle, which retains
    # the host when the callback publishes an UNKNOWN; calling _service_runtime
    # directly would bypass that retention and test an impossible call path.
    with pytest.raises(module.RiskServiceStartError):
        host._call_lifecycle(host._service_runtime, "synthetic", object(), frozenset())
    assert host._cleanup_uncertain is True


@pytest.mark.parametrize("kind", ["market", "account"])
def test_owner_factory_stops_after_other_host_publishes_unknown(host, monkeypatch, kind):
    import bt_api_py.gateway_strategy_account as account_module
    import bt_api_py.gateway_strategy_market as market_module

    other = module.RiskServiceHost(host.config)
    fake = SimpleNamespace(start=lambda _: pytest.fail("owner start after UNKNOWN"))
    def factory(*_args, **_kwargs):
        other._record_cleanup_uncertain(object(), "SYNTHETIC")
        return fake
    if kind == "market":
        monkeypatch.setattr(market_module, "GatewayMarketOwner", factory)
        extension = module._MarketOwnerExtension(object(), stream_scope=object(), security_profiles={},
                                                 cleanup_guard=host._retain_if_global_unknown)
    else:
        monkeypatch.setattr(account_module, "GatewayAccountOwner", factory)
        extension = module._AccountOwnerExtension(object(), account_ref="synthetic", scope_ref="synthetic",
                                                  cleanup_guard=host._retain_if_global_unknown)
    with pytest.raises(module.RiskServiceStartError):
        extension.start(object())
    assert any(item is extension for item in host._cleanup_uncertain_resources)


def test_delegate_port_snapshot_stops_after_other_host_publishes_unknown(host):
    other = module.RiskServiceHost(host.config)
    calls = []
    class Snapshot:
        @property
        def state(self):
            calls.append("state")
            other._record_cleanup_uncertain(object(), "SYNTHETIC")
            return "closed"
        @property
        def thread_alive(self):
            pytest.fail("later snapshot field inspected after UNKNOWN")
    class RuntimeFake:
        def close(self, **_kwargs):
            calls.append("runtime")
            return Snapshot()
    runtime = RuntimeFake()
    handle = module._DelegateHandle(
        runtime, object(), object(), cleanup_guard=host._retain_if_global_unknown,
        snapshot_guard=lambda resource, snap: module._GuardedCleanupSnapshot(host, resource, snap),
    )
    host.order_port = make_gateway_port(host, handle)
    slot = host.order_port._slots["synthetic"]
    with pytest.raises(module.RiskServiceStartError):
        host.order_port.note_run("synthetic", "second")
    assert calls == ["runtime", "state"]
    assert slot.handle is handle
    assert any(item is runtime for item in host._cleanup_uncertain_resources)


def test_unweakrefable_raw_runtime_is_retained_unknown(host):
    created = []
    class Raw:
        __slots__ = ()
        def __init__(self, *, cleanup_unknown, on_cleanup_sticky):
            created.append(self)
    with pytest.raises(module.RiskServiceStartError):
        host._construct_risk_raw_runtime(Raw)
    assert len(created) == 1
    assert any(item is created[0] for item in host._cleanup_uncertain_resources)
    assert host._cleanup_uncertain is True


def test_account_extension_exposes_strict_owner_uncertainty(host):
    extension = module._AccountOwnerExtension(
        object(), account_ref="synthetic", scope_ref="synthetic", cleanup_guard=host._retain_if_global_unknown,
        runtime_cleanup_unknown=host._runtime_cleanup_unknown, on_cleanup_sticky=host._runtime_cleanup_sticky,
    )
    extension._owner = SimpleNamespace(cleanup_uncertainties=1)
    assert extension.cleanup_uncertainties == 1


def test_callback_introspection_error_does_not_replace_original(host):
    other = module.RiskServiceHost(host.config)
    original = ValueError("synthetic original")
    class Callback:
        def __call__(self):
            other._record_cleanup_uncertain(object(), "SYNTHETIC")
            raise original
        def __getattribute__(self, name):
            if name == "__self__":
                raise RuntimeError("synthetic introspection failure")
            return super().__getattribute__(name)
    with pytest.raises(ValueError) as raised:
        host._call_lifecycle(Callback())
    assert raised.value is original
    assert host._cleanup_uncertain is True


def setup_poll_schedule(host, monkeypatch, now, intervals, enabled=("market", "account", "financial")):
    events = host.events
    host.config.loop_timeout_ms = 1
    host.config.observation_poll_seconds = intervals["observation"]
    host.config.market_poll_seconds = intervals["market"]
    host.config.account_snapshot_seconds = intervals["account"]
    host.config.strategy_financial_seconds = intervals["financial"]
    host.config.maintain_seconds = intervals["maintain"]
    host.service_host = SimpleNamespace(serve_once=lambda _: events.append("serve"))
    host.ledger = SimpleNamespace(fence=object())
    host._monotonic = lambda: now[0]
    host._next = dict.fromkeys(("observation", "market", "account", "financial", "maintain"), 0.0)
    monkeypatch.setattr(host, "_reconnect_observer", lambda: events.append("reconnect"))
    host.pump = SimpleNamespace(poll=lambda: events.append("observation"))
    host.market = SimpleNamespace(poll=lambda: events.append("market")) if "market" in enabled else None
    host.account = SimpleNamespace(refresh=lambda: events.append("account")) if "account" in enabled else None
    host.financial = (SimpleNamespace(refresh=lambda: events.append("financial"))
                      if "financial" in enabled else None)
    host.engine = SimpleNamespace(maintain=lambda: events.append("maintain"))


def poll_intervals(value):
    return dict.fromkeys(("observation", "market", "account", "financial", "maintain"), value)


@pytest.mark.parametrize("name", ["observation", "market", "account", "financial", "maintain"])
@pytest.mark.parametrize("interval", [1.0, float.fromhex("0x0.0000000000001p-1022")])
def test_poll_schedule_rejects_unrepresentable_due_deadline_before_any_poll(host, monkeypatch, name, interval):
    intervals = poll_intervals(4.0)
    intervals[name] = interval
    setup_poll_schedule(host, monkeypatch, [float(2**53)], intervals)
    before = host._next.copy()
    with pytest.raises(module.RiskServiceStartError) as raised:
        host.run_once()
    assert raised.value.code == "RISK_SERVICE_POLL_DEADLINE_INVALID"
    assert host.events == ["serve"]
    assert host._next == before


@pytest.mark.parametrize("now,interval", [
    (0.0, float.fromhex("0x0.0000000000001p-1022")),
    (1000.0, 0.25),
])
def test_poll_schedule_accepts_representable_deadlines(host, monkeypatch, now, interval):
    setup_poll_schedule(host, monkeypatch, [now], poll_intervals(interval))
    host.run_once()
    assert host.events == ["serve", "reconnect", "observation", "market", "account", "financial", "maintain"]
    assert host._next == poll_intervals(now + interval)
    assert all(value > now for value in host._next.values())


def test_poll_schedule_rechecks_precision_after_clock_growth(host, monkeypatch):
    now = [1.0]
    setup_poll_schedule(host, monkeypatch, now, poll_intervals(1.0))
    host.run_once()
    assert host._next == poll_intervals(2.0)
    events_after_first = host.events.copy()
    now[0] = float(2**53)
    with pytest.raises(module.RiskServiceStartError, match="RISK_SERVICE_POLL_DEADLINE_INVALID"):
        host.run_once()
    assert host.events == events_after_first + ["serve"]
    assert host._next == poll_intervals(2.0)


def test_poll_schedule_ignores_invalid_disabled_optional_intervals(host, monkeypatch):
    intervals = poll_intervals(1.0)
    intervals.update(market=float("nan"), account=0.0, financial=float("inf"))
    setup_poll_schedule(host, monkeypatch, [1.0], intervals, enabled=())
    host.run_once()
    assert host.events == ["serve", "reconnect", "observation", "maintain"]
    assert host._next == {"observation": 2.0, "market": 0.0, "account": 0.0,
                          "financial": 0.0, "maintain": 2.0}


def test_poll_schedule_ignores_not_due_optional_interval(host, monkeypatch):
    intervals = poll_intervals(1.0)
    intervals["market"] = float("nan")
    setup_poll_schedule(host, monkeypatch, [1.0], intervals)
    host._next["market"] = 2.0
    host.run_once()
    assert host.events == ["serve", "reconnect", "observation", "account", "financial", "maintain"]
    assert host._next["market"] == 2.0


@pytest.mark.parametrize("now", [float("nan"), float("inf"), -float("inf")])
def test_poll_schedule_rejects_nonfinite_clock_before_poll(host, monkeypatch, now):
    setup_poll_schedule(host, monkeypatch, [now], poll_intervals(1.0))
    before = host._next.copy()
    with pytest.raises(module.RiskServiceStartError, match="RISK_SERVICE_POLL_DEADLINE_INVALID"):
        host.run_once()
    assert host.events == ["serve"]
    assert host._next == before


@pytest.mark.parametrize("now,interval", [
    (1.0, 0.0),
    (1.0, -1.0),
    (1.0, float("inf")),
    (1.0, float("nan")),
    (1.0, True),
    (float.fromhex("0x1.fffffffffffffp+1023"), float.fromhex("0x1.fffffffffffffp+1023")),
])
def test_poll_schedule_rejects_nonadvancing_or_nonfinite_due_deadline(host, monkeypatch, now, interval):
    intervals = poll_intervals(1.0)
    intervals["maintain"] = interval
    setup_poll_schedule(host, monkeypatch, [now], intervals)
    before = host._next.copy()
    with pytest.raises(module.RiskServiceStartError, match="RISK_SERVICE_POLL_DEADLINE_INVALID"):
        host.run_once()
    assert host.events == ["serve"]
    assert host._next == before


@pytest.mark.parametrize("now", [float("nan"), float("inf"), -float("inf"), None, True, "invalid", 10**400])
def test_start_rejects_invalid_initial_poll_clock_without_writing_next(host, monkeypatch, now):
    attach(host)
    monkeypatch.setattr(module, "fault_point_from_environment", lambda _: None)
    monkeypatch.setattr(host, "open_storage", lambda: None)
    monkeypatch.setattr(host, "build_engine", lambda: None)
    monkeypatch.setattr(module, "bootstrap_policy", lambda *_: None)
    host.rules = object()
    host.engine = SimpleNamespace(recover=lambda: host.events.append("recover"))
    host.pump = SimpleNamespace(poll=lambda: host.events.append("startup_poll"))
    monkeypatch.setattr(host, "start_gateway", lambda: None)
    monkeypatch.setattr(host, "start_service_host", lambda: host.events.append("start_service"))
    host._monotonic = lambda: now
    old_table = host._next = {"unchanged": object()}
    with pytest.raises(module.RiskServiceStartError) as raised:
        host.start()
    assert raised.value.code == "RISK_SERVICE_POLL_DEADLINE_INVALID"
    assert host._next is old_table and tuple(host._next) == ("unchanged",)
    # Startup's initial poll precedes the final clock sample by contract.
    assert host.events == ["recover", "startup_poll", "start_service", "service", "port", "last", "first", "stack"]
    assert host._cleanup_state == "complete"


@pytest.mark.parametrize("now", [0.0, float(2**53)])
def test_start_accepts_finite_initial_poll_clock(host, monkeypatch, now):
    monkeypatch.setattr(module, "fault_point_from_environment", lambda _: None)
    monkeypatch.setattr(host, "open_storage", lambda: None)
    monkeypatch.setattr(host, "build_engine", lambda: None)
    monkeypatch.setattr(module, "bootstrap_policy", lambda *_: None)
    host.rules = object()
    host.engine = SimpleNamespace(recover=lambda: None)
    host.pump = SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(host, "start_gateway", lambda: None)
    monkeypatch.setattr(host, "start_service_host", lambda: None)
    host._monotonic = lambda: now
    assert host.start() is host
    assert host._next == poll_intervals(now)


@pytest.mark.parametrize("name", ["observation", "market", "account", "financial", "maintain"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), None, True, "invalid", 10**400])
def test_poll_schedule_rejects_invalid_existing_enabled_deadline_before_any_poll(host, monkeypatch, name, value):
    setup_poll_schedule(host, monkeypatch, [1.0], poll_intervals(1.0))
    host._next[name] = value
    table = host._next
    before = table.copy()
    with pytest.raises(module.RiskServiceStartError) as raised:
        host.run_once()
    assert raised.value.code == "RISK_SERVICE_POLL_DEADLINE_INVALID"
    assert host.events == ["serve"]
    assert host._next is table and all(host._next[key] is item for key, item in before.items())


def test_poll_schedule_ignores_invalid_existing_disabled_deadlines(host, monkeypatch):
    setup_poll_schedule(host, monkeypatch, [1.0], poll_intervals(1.0), enabled=())
    host._next.update(market=float("nan"), account=float("inf"), financial=None)
    before = {name: host._next[name] for name in ("market", "account", "financial")}
    host.run_once()
    assert host.events == ["serve", "reconnect", "observation", "maintain"]
    assert all(host._next[name] is item for name, item in before.items())
