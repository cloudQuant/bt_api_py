"""Synthetic Gateway cleanup proof uses only these in-memory fake resources."""

import ast
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import pytest

from bt_api_py.gateway_direct_owner import (
    GatewayDirectOwner,
    GatewayDirectOwnerStatus,
    GatewayDispatchCommand,
    _Task,
)
from tests.integration import _iter64_recovery_support as support

RUN = "a" * 32
GENERATION = "b" * 32
STOP = "c" * 32


class FakeHost:
    def __init__(self, events):
        self.state = "started"
        self.events = events

    def close(self):
        self.events.append("host")
        self.state = "closed"


class FakeStream:
    def __init__(self, events):
        self.events = events

    def close(self, timeout):
        assert timeout == 5
        self.events.append("stream")
        return True


class FakeReadSource:
    def __init__(self, events):
        self.events = events
        self._state = "ready"
        self.session_generation = "generation"

    def close(self):
        self.events.append("read_model")
        self._state = "closed"
        self.session_generation = None

    def read_status(self):
        return {"session_generation": self.session_generation, "ready": self._state != "closed"}


class FakeOwner:
    def __init__(self, events):
        self.events = events
        self.closed = False
        self.failure = None
        self.queue_depth = 0
        self.service_queue_depth = 0
        self.running = False
        self.accepting = False

    def close(self, timeout):
        assert timeout == 5
        self.events.append("owner")
        self.closed = True
        return True

    def status(self):
        return GatewayDirectOwnerStatus(self.queue_depth, self.running, self.accepting,
                                        False, self.failure, self.service_queue_depth)


@pytest.fixture
def resources(monkeypatch):
    # Isolate these synthetic handles; never inspect or clear a real registry.
    roots = {}
    monkeypatch.setattr(support, "_GATEWAY_CLEANUP_RETAINED", roots)
    events = []
    host, stream, source, owner = (FakeHost(events), FakeStream(events),
                                   FakeReadSource(events), FakeOwner(events))
    service = SimpleNamespace(state_source=source)
    return roots, events, host, owner, stream, service, source


def close_resources(resources):
    roots, events, host, owner, stream, service, source = resources
    return support._close_synthetic_gateway(host, owner, stream, service, source)


def test_success_closes_every_owned_component_in_order(resources):
    roots, events, *_ = resources
    assert close_resources(resources) == "COMPLETE"
    assert events == ["host", "stream", "read_model", "owner"]
    assert roots == {}


@pytest.mark.parametrize("failure,expected,events", [
    ("host_not_started", "IDENTITY_UNAVAILABLE", []),
    ("host_not_closed", "HOST_UNCONFIRMED", ["host"]),
    ("stream_false", "STREAM_UNCONFIRMED", ["host", "stream"]),
    ("read_unclosed", "READ_MODEL_UNCONFIRMED", ["host", "stream", "read_model"]),
    ("owner_false", "OWNER_UNCONFIRMED", ["host", "stream", "read_model", "owner"]),
    ("owner_unclosed", "OWNER_UNCONFIRMED", ["host", "stream", "read_model", "owner"]),
    ("owner_finalizer_failed", "OWNER_UNCONFIRMED", ["host", "stream", "read_model", "owner"]),
    ("owner_queue_nonempty", "OWNER_UNCONFIRMED", ["host", "stream", "read_model", "owner"]),
    ("owner_service_nonempty", "OWNER_UNCONFIRMED", ["host", "stream", "read_model", "owner"]),
    ("owner_running", "OWNER_UNCONFIRMED", ["host", "stream", "read_model", "owner"]),
    ("wrong_read_source", "IDENTITY_UNAVAILABLE", []),
    ("missing_resource", "RESOURCES_UNAVAILABLE", []),
])
def test_unconfirmed_cleanup_retains_handles_and_stops(resources, failure, expected, events):
    roots, recorded, host, owner, stream, service, source = resources
    if failure == "host_not_started":
        host.state = "failed"
    elif failure == "host_not_closed":
        host.close = lambda: recorded.append("host")
    elif failure == "stream_false":
        stream.close = lambda _: recorded.append("stream") or False
    elif failure == "read_unclosed":
        source.close = lambda: recorded.append("read_model")
    elif failure == "owner_false":
        owner.close = lambda _: recorded.append("owner") or False
    elif failure == "owner_unclosed":
        owner.close = lambda _: recorded.append("owner") or True
    elif failure == "owner_finalizer_failed":
        owner.failure = "finalizer_failed"
    elif failure == "owner_queue_nonempty":
        owner.queue_depth = 1
    elif failure == "owner_service_nonempty":
        owner.service_queue_depth = 1
    elif failure == "owner_running":
        owner.running = True
    elif failure == "wrong_read_source":
        service.state_source = object()
    elif failure == "missing_resource":
        resources = (roots, recorded, host, None, stream, service, source)
    assert close_resources(resources) == expected
    assert recorded == events
    assert len(roots) == 1
    assert next(iter(roots.values())) == tuple(resources[2:])


@pytest.mark.parametrize("which", ["host", "stream", "read_model", "owner"])
def test_exception_is_unknown_and_original_handles_are_retained(resources, which):
    roots, events, host, owner, stream, _service, source = resources
    def fail(*_args):
        events.append(which)
        raise RuntimeError("synthetic failure")
    {"host": host, "stream": stream, "read_model": source, "owner": owner}[which].close = fail
    assert close_resources(resources) == "CLEANUP_EXCEPTION"
    assert events[-1] == which
    assert len(roots) == 1
    assert next(iter(roots.values())) == tuple(resources[2:])


def test_interruption_never_becomes_complete(resources):
    roots, events, host, *_ = resources
    def interrupt():
        events.append("host")
        raise KeyboardInterrupt
    host.close = interrupt
    with pytest.raises(KeyboardInterrupt):
        close_resources(resources)
    assert events == ["host"]
    assert len(roots) == 1


def test_real_owner_close_can_return_true_after_a_failed_finalizer(resources):
    roots, events, host, _owner, stream, service, source = resources

    class FakeCondition:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def notify_all(self):
            pass

    class FakeDone:
        def is_set(self):
            return False

    def failed_finalizer():
        raise RuntimeError("synthetic finalizer failure")

    command = GatewayDispatchCommand("strategy", "key", "principal", "reservation", "a" * 64, 1)
    owner = object.__new__(GatewayDirectOwner)
    owner._pid = support.os.getpid()
    owner._thread = None
    owner._condition = FakeCondition()
    owner._done = FakeDone()
    owner._queue = deque([_Task(command, lambda: None, failed_finalizer, 10)])
    owner._cancel_queue = deque()
    owner._service_queue = deque()
    owner._pending = {("place", "strategy", "key"): command}
    owner._queue_size = 8
    owner._active_since = None
    owner._busy_timeout = 5
    owner._binding = None
    owner._closing = False
    owner._closed_without_thread = False
    owner._lifecycle_epoch = 0
    owner._cleanup_retry = 0
    owner._failure = None
    # Normal construction always installs the terminal-lifecycle fence; this
    # fabricated owner uses no shared coordinator, which close() handles.
    owner._lifecycle_lock = support.threading.Lock()
    owner._lifecycle_writer = None
    owner._lifecycle_coordinator = None
    owner._lifecycle_terminal_change = None
    owner._lifecycle_shutdown_requested = False

    assert support._close_synthetic_gateway(host, owner, stream, service, source) == "OWNER_UNCONFIRMED"
    assert owner.closed is True
    assert owner.status().failure_category == "finalizer_failed"
    assert len(roots) == 1
    assert events == ["host", "stream", "read_model"]


def test_receipt_requires_same_run_generation_stop_and_pid():
    command = support.gateway_stop_command(RUN, GENERATION, STOP)
    assert command == ("stop", RUN, GENERATION, STOP)
    assert support._gateway_stop_id(command, RUN, GENERATION) == STOP
    assert support._gateway_stop_id(command, "d" * 32, GENERATION) is None
    assert support._gateway_stop_id(command, RUN, "d" * 32) is None
    assert support._gateway_stop_id(("stop", RUN, GENERATION, "bad"), RUN, GENERATION) is None
    assert support._gateway_stop_id("stop", RUN, GENERATION) is None
    receipt = ("gateway_close", 12, RUN, GENERATION, STOP, "COMPLETE")
    assert support.gateway_close_receipt_matches(
        receipt, pid=12, run_id=RUN, generation=GENERATION, stop_id=STOP)
    for changed in (
        ("gateway_close", 13, RUN, GENERATION, STOP, "COMPLETE"),
        ("gateway_close", 12, "d" * 32, GENERATION, STOP, "COMPLETE"),
        ("gateway_close", 12, RUN, "d" * 32, STOP, "COMPLETE"),
        ("gateway_close", 12, RUN, GENERATION, "d" * 32, "COMPLETE"),
        ("gateway_close", 12, RUN, GENERATION, STOP, "UNKNOWN"),
        ("closed", 12),
    ):
        assert not support.gateway_close_receipt_matches(
            changed, pid=12, run_id=RUN, generation=GENERATION, stop_id=STOP)
    assert not support.gateway_close_receipt_matches(
        ("gateway_close", True, RUN, GENERATION, STOP, "COMPLETE"),
        pid=1, run_id=RUN, generation=GENERATION, stop_id=STOP)


@pytest.mark.parametrize("bad", [None, "", "A" * 32, "0" * 31, "0" * 33, "g" * 32, 1])
def test_stop_identity_rejects_malformed_tokens(bad):
    for values in ((bad, GENERATION, STOP), (RUN, bad, STOP), (RUN, GENERATION, bad)):
        with pytest.raises(ValueError, match="^INVALID_GATEWAY_STOP_IDENTITY$"):
            support.gateway_stop_command(*values)


class FakeProcess:
    def __init__(self):
        self.pid = 12
        self.exitcode = 0
        self.alive = False
        self.joins = []

    def join(self, timeout):
        self.joins.append(timeout)

    def is_alive(self):
        return self.alive


class FakeControls:
    def __init__(self):
        self.sent = []

    def put(self, value):
        self.sent.append(value)


class FakeMessages:
    def __init__(self, controls, result="complete"):
        self.controls = controls
        self.result = result

    def get(self, *, timeout):
        assert timeout > 0
        _, run, generation, stop = self.controls.sent[-1]
        if self.result == "unknown":
            return ("gateway_close_unknown", 12, run, "OWNER_UNCONFIRMED")
        values = ["gateway_close", 12, run, generation, stop, "COMPLETE"]
        if self.result == "wrong_pid":
            values[1] = 13
        if self.result == "wrong_run":
            values[2] = "d" * 32
        if self.result == "wrong_generation":
            values[3] = "d" * 32
        if self.result == "wrong_stop":
            values[4] = "d" * 32
        return tuple(values)


@pytest.fixture
def child(monkeypatch):
    roots = {}
    monkeypatch.setattr(support, "_GATEWAY_CHILD_RETAINED", roots)
    monkeypatch.setattr(support.secrets, "token_hex", lambda count: STOP)
    process, controls = FakeProcess(), FakeControls()
    messages = FakeMessages(controls)
    owner = support.SyntheticGatewayStop(process, controls, messages)
    owner.bind_ready(("ready", 12, "journal-id", GENERATION, RUN))
    return roots, owner, process, controls, messages


def test_parent_accepts_only_matching_receipt_and_reaped_child(child):
    roots, owner, process, controls, _messages = child
    owner.close()
    owner.close()  # known-complete close is harmless; it does not resend
    assert controls.sent == [("stop", RUN, GENERATION, STOP)]
    assert process.joins == [20]
    assert roots == {}


@pytest.mark.parametrize("result", ["unknown", "wrong_pid", "wrong_run", "wrong_generation", "wrong_stop"])
def test_parent_rejects_missing_or_wrong_receipt_and_never_retries(child, result):
    roots, owner, process, controls, messages = child
    messages.result = result
    with pytest.raises(RuntimeError, match="^GATEWAY_CLEANUP_UNKNOWN$"):
        owner.close()
    with pytest.raises(RuntimeError, match="^GATEWAY_CLEANUP_ALREADY_ATTEMPTED$"):
        owner.close()
    assert len(controls.sent) == 1 and process.joins == []
    assert roots[id(owner)] is owner


@pytest.mark.parametrize("alive,exitcode", [(True, 0), (False, None), (False, 1), (False, True)])
def test_matching_receipt_alone_does_not_prove_process_exit(child, alive, exitcode):
    roots, owner, process, controls, _messages = child
    process.alive, process.exitcode = alive, exitcode
    with pytest.raises(RuntimeError, match="^GATEWAY_CLEANUP_UNKNOWN$"):
        owner.close()
    assert process.joins == [20] and len(controls.sent) == 1
    assert roots[id(owner)] is owner


def test_invalid_ready_is_not_accepted_and_child_is_retained(monkeypatch):
    roots = {}
    monkeypatch.setattr(support, "_GATEWAY_CHILD_RETAINED", roots)
    process, controls = FakeProcess(), FakeControls()
    owner = support.SyntheticGatewayStop(process, controls, FakeMessages(controls))
    with pytest.raises(ValueError, match="^INVALID_GATEWAY_READY_IDENTITY$"):
        owner.bind_ready(("ready", 13, "journal-id", GENERATION, RUN))
    with pytest.raises(RuntimeError, match="^GATEWAY_READY_ALREADY_ATTEMPTED$"):
        owner.bind_ready(("ready", 12, "journal-id", GENERATION, RUN))
    with pytest.raises(RuntimeError, match="^GATEWAY_CLEANUP_UNKNOWN$"):
        owner.close()
    assert roots[id(owner)] is owner and controls.sent == []


def test_child_is_retained_before_start_and_without_ready(monkeypatch):
    roots = {}
    monkeypatch.setattr(support, "_GATEWAY_CHILD_RETAINED", roots)
    process, controls = FakeProcess(), FakeControls()
    process.pid = None
    owner = support.SyntheticGatewayStop(process, controls, FakeMessages(controls))
    assert roots[id(owner)] is owner
    with pytest.raises(RuntimeError, match="^GATEWAY_CLEANUP_UNKNOWN$"):
        owner.close()
    assert controls.sent == [] and roots[id(owner)] is owner


@pytest.mark.parametrize("stage", ["put", "receive", "join"])
def test_interrupted_parent_keeps_handles_and_does_not_retry(child, stage):
    roots, owner, process, controls, messages = child
    def interrupt(*_args, **_kwargs):
        raise KeyboardInterrupt
    {"put": controls, "receive": messages, "join": process}[stage].__dict__[
        {"put": "put", "receive": "get", "join": "join"}[stage]
    ] = interrupt
    with pytest.raises(KeyboardInterrupt):
        owner.close()
    with pytest.raises(RuntimeError, match="^GATEWAY_CLEANUP_ALREADY_ATTEMPTED$"):
        owner.close()
    assert roots[id(owner)] is owner


def test_timeout_receiving_receipt_is_unknown_without_retry(child):
    roots, owner, _process, controls, messages = child
    def no_receipt(*_args, **_kwargs):
        raise TimeoutError
    messages.get = no_receipt
    with pytest.raises(RuntimeError, match="^GATEWAY_CLEANUP_UNKNOWN$"):
        owner.close()
    with pytest.raises(RuntimeError, match="^GATEWAY_CLEANUP_ALREADY_ATTEMPTED$"):
        owner.close()
    assert len(controls.sent) == 1 and roots[id(owner)] is owner


@pytest.mark.parametrize("identity", ["process", "thread"])
def test_owner_identity_change_never_sends_stop(child, identity):
    roots, owner, _process, controls, _messages = child
    if identity == "process":
        owner._process_id = -1
    else:
        owner._owner_thread = object()
    with pytest.raises(RuntimeError, match="^GATEWAY_OWNER_IDENTITY_CHANGED$"):
        owner.close()
    assert controls.sent == [] and roots[id(owner)] is owner


def test_retained_loopback_root_is_outside_pytest_cleanup_tree(monkeypatch):
    calls = []
    monkeypatch.setattr(support.tempfile, "gettempdir", lambda: "/synthetic-parent")
    def create(*, prefix, dir):
        calls.append((prefix, dir))
        return "/synthetic-parent/iter64-risk-loopback-created"
    monkeypatch.setattr(support.tempfile, "mkdtemp", create)
    root = support.retained_loopback_root("/synthetic-parent/pytest/base")
    assert str(root) == "/synthetic-parent/iter64-risk-loopback-created"
    assert calls == [("iter64-risk-loopback-", "/synthetic-parent")]


def test_retained_loopback_root_rejects_pytest_owned_temp_base(monkeypatch):
    monkeypatch.setattr(support.tempfile, "gettempdir", lambda: "/synthetic/pytest/base/temporary")
    monkeypatch.setattr(support.tempfile, "mkdtemp", lambda **_: pytest.fail("unexpected directory"))
    with pytest.raises(RuntimeError, match="^RETAINED_ROOT_UNAVAILABLE$"):
        support.retained_loopback_root("/synthetic/pytest/base")


@pytest.mark.parametrize("name", [
    "test_iter64_risk_crash_matrix.py",
    "test_iter64_gateway_restart_risk_recovery.py",
    "test_iter64_controlled_risk_reconnect.py",
])
def test_loopback_callers_obtain_root_outside_pytest_tree(name):
    path = Path(__file__).parents[1] / "integration" / name
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    loopback_calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                      and isinstance(node.func, ast.Name) and node.func.id == "_Loopback"]
    assert len(loopback_calls) == 1
    assert len(loopback_calls[0].args) == 1
    assert isinstance(loopback_calls[0].args[0], ast.Name)
    assert loopback_calls[0].args[0].id == "root"
    helpers = [node for node in ast.walk(tree) if isinstance(node, ast.Assign)
               and any(isinstance(target, ast.Name) and target.id == "root" for target in node.targets)
               and isinstance(node.value, ast.Call)
               and isinstance(node.value.func, ast.Attribute)
               and isinstance(node.value.func.value, ast.Name)
               and node.value.func.value.id == "support"
               and node.value.func.attr == "retained_loopback_root"]
    assert len(helpers) == 1
    args = helpers[0].value.args
    assert len(args) == 1 and isinstance(args[0], ast.Call)
    assert isinstance(args[0].func, ast.Attribute) and args[0].func.attr == "getbasetemp"
    assert isinstance(args[0].func.value, ast.Name) and args[0].func.value.id == "tmp_path_factory"
