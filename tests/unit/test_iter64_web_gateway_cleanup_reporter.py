"""Pure fake checks for the synthetic Web/Gateway fixture's cleanup evidence."""

import json
import os
import queue
import threading
from types import SimpleNamespace

import pytest

from tests.integration import _iter64_web_real_host_support as fixture
from tests.integration import test_iter64_risk_service_process as shared_fixture


def _reported(tmp_path, messages):
    values = queue.Queue()
    for message in messages:
        values.put(message)
    finished = threading.Event()
    finished.set()
    state = {"ready_seen": False, "closed_seen": False, "failed": False}
    events = tmp_path / "events.jsonl"
    fixture._report_gateway_messages(values, events, 123, finished, state)
    rows = fixture._events(events)
    return state, rows


def test_reporter_requires_matching_ready_and_closed(tmp_path):
    state, rows = _reported(tmp_path, [("ready", 123, "ignored"), ("closed", 123)])
    assert state == {"ready_seen": True, "closed_seen": True, "failed": False}
    assert rows == [{"event": "ready", "pid": 123}, {"event": "closed", "pid": 123}]


def test_reporter_persists_health_challenge_without_raw_payload(tmp_path):
    nonce = "a" * 32
    state, rows = _reported(tmp_path, [("ready", 123, "ignored"), ("health", nonce), ("closed", 123)])
    assert state == {"ready_seen": True, "closed_seen": True, "failed": False}
    assert rows[1] == {"event": "health", "pid": 123, "nonce": nonce}


@pytest.mark.parametrize("messages", [
    [("ready", 123, "ignored")],
    [("closed", 123)],
    [("ready", 123, "ignored"), ("closed", 456)],
    [("ready", 123, "ignored"), ("closed", 123), ("cleanup_uncertain", "HOST_UNVERIFIED")],
])
def test_missing_mismatched_or_late_uncertainty_cannot_prove_success(tmp_path, messages):
    state, rows = _reported(tmp_path, messages)
    assert state["failed"] or not state["ready_seen"] or not state["closed_seen"]
    gateway = FakeGateway(tmp_path / "events.jsonl", live=False)
    assert not fixture._gateway_event_check(tmp_path / "events.jsonl", gateway, closed=True)
    assert rows


def test_reporter_emits_fixed_reason_not_secret(tmp_path):
    secret = "synthetic-secret-must-not-be-emitted"
    state, rows = _reported(tmp_path, [("ready", 123, "ignored"), ("error", secret)])
    assert state["failed"] and not state["closed_seen"]
    assert rows[-1] == {"event": "error", "pid": 123, "reason": "UNKNOWN"}
    assert secret not in (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    assert secret not in repr(fixture._SyntheticKeys({"key": secret}))
    assert secret not in repr(fixture._SyntheticConfig({"key": secret}))


def test_reporter_rejects_malformed_health_without_echoing_it(tmp_path):
    secret = "synthetic-private-invalid-health"
    state, rows = _reported(tmp_path, [("ready", 123, "ignored"), ("health", secret)])
    assert state["failed"] and not state["closed_seen"]
    assert secret not in (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    assert rows == [{"event": "ready", "pid": 123}]


def test_reporter_writer_failure_is_not_success(tmp_path, monkeypatch):
    def broken(*_args, **_kwargs):
        raise RuntimeError("synthetic-private-error")

    monkeypatch.setattr(fixture, "_append_gateway_event", broken)
    state, rows = _reported(tmp_path, [("ready", 123, "ignored"), ("closed", 123)])
    assert state["failed"] and not state["closed_seen"] and rows == []


def test_private_write_creates_restricted_file(tmp_path):
    path = tmp_path / "test-config.json"
    fixture._private_write(path, "synthetic")
    assert path.read_text(encoding="utf-8") == "synthetic"
    assert os.stat(path).st_mode & 0o077 == 0


def test_private_root_is_fresh_and_not_pytest_managed_identity(tmp_path):
    base = tmp_path / "retained-identities"
    first = fixture._fixture_root(base)
    second = fixture._fixture_root(base)
    assert first != second and first.parent == second.parent == base
    assert os.stat(first).st_mode & 0o077 == 0
    assert os.stat(second).st_mode & 0o077 == 0


def test_unsafe_existing_private_root_is_rejected(tmp_path):
    base = tmp_path / "public-identities"
    base.mkdir(mode=0o755)
    base.chmod(0o755)
    with pytest.raises(RuntimeError, match="SYNTHETIC_PRIVATE_ROOT_UNSAFE"):
        fixture._fixture_root(base)


def test_child_environment_does_not_inherit_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_PRIVATE_SHOULD_NOT_INHERIT", "synthetic-private")
    environment = fixture._fixture_env(tmp_path)
    assert environment["HOME"] == str(tmp_path)
    assert "FAKE_PRIVATE_SHOULD_NOT_INHERIT" not in environment
    assert "synthetic-private" not in repr(environment)


def test_scenario_uses_shared_risk_files_fixture():
    assert fixture._risk_files is shared_fixture._risk_files
    assert fixture._risk_files.__globals__["_write_key"] is shared_fixture._write_key


def test_outer_failure_hides_original_exception_text(tmp_path, monkeypatch):
    monkeypatch.setattr(fixture, "_fixture_root", lambda: tmp_path)

    def fail(*_args, **_kwargs):
        raise RuntimeError("synthetic-private-credential")

    monkeypatch.setattr(fixture, "_exercise_web_admin_scenario", fail)
    with pytest.raises(pytest.fail.Exception) as caught:
        fixture.exercise_web_admin_scenario(tmp_path, check_available=lambda _value: None)
    assert str(caught.value) == "SYNTHETIC_WEB_SCENARIO_FAILED"
    assert caught.value.__suppress_context__


def test_interrupt_is_preserved_without_private_exit_message(tmp_path, monkeypatch):
    monkeypatch.setattr(fixture, "_fixture_root", lambda: tmp_path)

    def fail(*_args, **_kwargs):
        raise SystemExit("synthetic-private-credential")

    monkeypatch.setattr(fixture, "_exercise_web_admin_scenario", fail)
    with pytest.raises(SystemExit) as caught:
        fixture.exercise_web_admin_scenario(tmp_path, check_available=lambda _value: None)
    assert caught.value.code == 1 and caught.value.__suppress_context__


def test_reporter_early_exit_does_not_mark_closed(tmp_path):
    state, rows = _reported(tmp_path, [])
    assert rows == [] and not state["closed_seen"]


class FakeStdin:
    def __init__(self, gateway):
        self.gateway = gateway

    def write(self, value):
        if value.startswith(b"health:"):
            self.gateway.actions.append(("gateway_health",))
            nonce = value.decode("ascii").strip().removeprefix("health:")
            fixture._append_gateway_event(self.gateway.events, "health", self.gateway.pid, nonce)
        else:
            self.gateway.actions.append(("gateway_stop", value))

    def flush(self):
        self.gateway.actions.append(("gateway_flush",))


class FakeGateway:
    def __init__(self, events, live=True, append_closed=True):
        self.pid = 123
        self.events = events
        self.live = live
        self.append_closed = append_closed
        self.actions = []
        self.stdin = FakeStdin(self)

    def poll(self):
        return None if self.live else 0

    def wait(self, timeout):
        self.actions.append(("gateway_wait", timeout))
        self.live = False
        if self.append_closed:
            fixture._append_gateway_event(self.events, "closed", self.pid)
        return 0


class FakeRisk:
    pid = 456

    def __init__(self):
        self.actions = []
        self.live = True

    def poll(self):
        return None if self.live else 0

    def send_signal(self, value):
        self.actions.append(("risk_signal", value))

    def wait(self, timeout):
        self.actions.append(("risk_wait", timeout))
        self.live = False
        return 0


class FakeDirect:
    _require_unregister_ack = True
    state = "registered"
    healthy = True
    cleanup_failures = 0
    unregister_uncertainties = 0
    unknown_registration_count = 0

    def __init__(self):
        self.actions = []

    def close(self):
        self.actions.append("direct_close")
        self.state = "closed"


@pytest.fixture(autouse=True)
def _fake_control_pipe(monkeypatch):
    real_write = fixture._write_gateway_control

    def write(gateway, payload):
        if not isinstance(gateway.stdin, FakeStdin):
            return real_write(gateway, payload)
        try:
            gateway.stdin.write(payload)
            gateway.stdin.flush()
            return True
        except BaseException:
            return False

    monkeypatch.setattr(fixture, "_write_gateway_control", write)


def test_control_pipe_write_is_nonblocking_and_restores_mode():
    read_fd, write_fd = os.pipe()
    try:
        gateway = SimpleNamespace(stdin=SimpleNamespace(fileno=lambda: write_fd))
        original = os.get_blocking(write_fd)
        assert fixture._write_gateway_control(gateway, b"health\n")
        assert os.read(read_fd, 7) == b"health\n"
        assert os.get_blocking(write_fd) == original
    finally:
        os.close(read_fd)
        os.close(write_fd)


class FakeWeb:
    def __init__(self, events=None, fail=False):
        self.actions = []
        self.events = events
        self.fail = fail
        self.host = SimpleNamespace(_runtime=FakeRuntime())

    def close(self, *, pre_close):
        assert pre_close()
        self.actions.append("web_close")
        if self.events:
            fixture._append_gateway_event(self.events, "cleanup_uncertain", 123, "OWNER_UNVERIFIED")
        if self.fail:
            raise RuntimeError("synthetic-private-error")
        self.host._runtime.state = "closed"


class FakeRuntime:
    strict_cleanup_required = True

    def __init__(self):
        self.state = "running"
        self.overrides = {}

    def status(self):
        values = {"state": self.state, "thread_alive": self.state == "running",
                  "healthy": self.state == "running", "error_code": None, "bindings": (),
                  "cleanup_failures": 0, "unregister_uncertainties": 0,
                  "owner_cleanup_uncertainties": 0, "unknown_registration_count": 0,
                  "strict_unregister_ack": True}
        values.update(self.overrides)
        return fixture.RuntimeSnapshot(**values)


def _owned(tmp_path):
    events = tmp_path / "events.jsonl"
    fixture._append_gateway_event(events, "ready", 123)
    return events, FakeGateway(events), FakeRisk(), FakeDirect()


def test_normal_cleanup_checks_matching_closed_marker(tmp_path):
    events, gateway, risk, direct = _owned(tmp_path)
    web = FakeWeb()
    assert fixture._cleanup_owned_fixture(gateway, risk, direct, web, events,
                                           gateway_ready=True, risk_ready=True,
                                           client_uncertain=False)
    assert web.actions == ["web_close"] and direct.actions == ["direct_close"]
    assert [action[0] for action in risk.actions] == ["risk_signal", "risk_wait"]
    assert [action[0] for action in gateway.actions if action[0] != "gateway_health"].count("gateway_stop") == 1
    assert gateway.actions[-1][0] == "gateway_wait"


@pytest.mark.parametrize("failure", ["not_ready", "client_unknown", "event_unknown", "web_error"])
def test_uncertainty_prevents_further_close_or_signal(tmp_path, failure):
    events, gateway, risk, direct = _owned(tmp_path)
    web = FakeWeb(fail=failure == "web_error")
    if failure == "event_unknown":
        fixture._append_gateway_event(events, "cleanup_uncertain", 123, "OWNER_UNVERIFIED")
    result = fixture._cleanup_owned_fixture(
        gateway, risk, direct, web, events,
        gateway_ready=failure != "not_ready", risk_ready=True,
        client_uncertain=failure == "client_unknown",
    )
    assert result is False
    assert direct.actions == [] and risk.actions == []
    assert not any(action[0] == "gateway_stop" for action in gateway.actions)
    assert web.actions == (["web_close"] if failure == "web_error" else [])


def test_async_uncertainty_rechecked_after_web_close(tmp_path):
    events, gateway, risk, direct = _owned(tmp_path)
    web = FakeWeb(events=events)
    assert not fixture._cleanup_owned_fixture(gateway, risk, direct, web, events,
                                              gateway_ready=True, risk_ready=True,
                                              client_uncertain=False)
    assert web.actions == ["web_close"]
    assert direct.actions == [] and risk.actions == []
    assert not any(action[0] == "gateway_stop" for action in gateway.actions)


def test_reporter_failure_on_challenge_prevents_any_close(tmp_path):
    events, gateway, risk, direct = _owned(tmp_path)

    class FailedReporter(FakeStdin):
        def write(self, _value):
            fixture._append_gateway_event(events, "cleanup_uncertain", gateway.pid, "MESSAGE_UNVERIFIED")

    gateway.stdin = FailedReporter(gateway)
    web = FakeWeb()
    assert not fixture._cleanup_owned_fixture(gateway, risk, direct, web, events,
                                              gateway_ready=True, risk_ready=True,
                                              client_uncertain=False)
    assert web.actions == direct.actions == risk.actions == []
    assert not any(action[0] == "gateway_stop" for action in gateway.actions)


def test_silent_reporter_challenge_fails_closed(tmp_path):
    events, gateway, _risk, _direct = _owned(tmp_path)

    class SilentReporter(FakeStdin):
        def write(self, _value):
            pass

    gateway.stdin = SilentReporter(gateway)
    assert not fixture._gateway_event_check(events, gateway, _health_timeout=0.03)
    assert not any(action[0] == "gateway_stop" for action in gateway.actions)


def test_uncertain_outcome_persists_redacted_identity_and_references(tmp_path):
    events, gateway, risk, direct = _owned(tmp_path)
    keys = fixture._SyntheticKeys({"synthetic": "private-synthetic-value"})
    try:
        assert not fixture._record_fixture_outcome(tmp_path, False, gateway, risk, direct, None, None, keys)
        retained = fixture._RETAINED_FIXTURES[str(tmp_path)]
        assert gateway in retained._resources and risk in retained._resources and keys in retained._resources
        assert "private-synthetic-value" not in repr(retained)
        identity = json.loads((tmp_path / "fixture-identity.json").read_text(encoding="utf-8"))
        assert identity == {"gateway_pid": 123, "risk_pid": 456, "state": "cleanup_unknown"}
        assert os.stat(tmp_path / "fixture-identity.json").st_mode & 0o077 == 0
    finally:
        fixture._RETAINED_FIXTURES.pop(str(tmp_path), None)


def test_identity_write_failure_retains_resources(tmp_path, monkeypatch):
    _events, gateway, risk, direct = _owned(tmp_path)

    def fail(*_args, **_kwargs):
        raise RuntimeError("synthetic-private-error")

    monkeypatch.setattr(fixture, "_private_write", fail)
    try:
        assert not fixture._record_fixture_outcome(tmp_path, True, gateway, risk, direct, None, None, None)
        assert gateway in fixture._RETAINED_FIXTURES[str(tmp_path)]._resources
    finally:
        fixture._RETAINED_FIXTURES.pop(str(tmp_path), None)


def test_unverified_direct_unregister_prevents_subsequent_signals(tmp_path):
    events, gateway, risk, direct = _owned(tmp_path)
    direct.unregister_uncertainties = 1
    assert not fixture._cleanup_owned_fixture(gateway, risk, direct, None, events,
                                              gateway_ready=True, risk_ready=True,
                                              client_uncertain=False, web_absent_proven=True)
    assert direct.actions == [] and risk.actions == []
    assert not any(action[0] == "gateway_stop" for action in gateway.actions)


def test_missing_unregister_ack_requirement_prevents_client_close(tmp_path):
    events, gateway, risk, direct = _owned(tmp_path)
    direct._require_unregister_ack = False
    assert not fixture._cleanup_owned_fixture(gateway, risk, direct, None, events,
                                              gateway_ready=True, risk_ready=True,
                                              client_uncertain=False, web_absent_proven=True)
    assert direct.actions == [] and risk.actions == []
    assert not any(action[0] == "gateway_stop" for action in gateway.actions)


@pytest.mark.parametrize("field,value", [
    ("cleanup_failures", 1), ("cleanup_failures", False), ("unregister_uncertainties", None),
    ("unknown_registration_count", True), ("state", None), ("healthy", None),
])
def test_known_direct_uncertainty_prevents_all_cleanup(tmp_path, field, value):
    events, gateway, risk, direct = _owned(tmp_path)
    web = FakeWeb()
    setattr(direct, field, value)
    assert not fixture._cleanup_owned_fixture(gateway, risk, direct, web, events,
                                              gateway_ready=True, risk_ready=True,
                                              client_uncertain=False)
    assert web.actions == direct.actions == risk.actions == gateway.actions == []


@pytest.mark.parametrize("field,value", [
    ("cleanup_failures", False), ("unregister_uncertainties", 1),
    ("owner_cleanup_uncertainties", None), ("unknown_registration_count", True),
    ("strict_unregister_ack", None), ("thread_alive", None), ("state", None),
])
def test_known_web_uncertainty_prevents_all_cleanup(tmp_path, field, value):
    events, gateway, risk, direct = _owned(tmp_path)
    web = FakeWeb()
    web.host._runtime.overrides[field] = value
    assert not fixture._cleanup_owned_fixture(gateway, risk, direct, web, events,
                                              gateway_ready=True, risk_ready=True,
                                              client_uncertain=False)
    assert web.actions == direct.actions == risk.actions == gateway.actions == []


def test_missing_or_raising_client_state_prevents_all_cleanup(tmp_path):
    events, gateway, risk, direct = _owned(tmp_path)
    web = FakeWeb()
    for candidate in (SimpleNamespace(), SimpleNamespace(host=SimpleNamespace(_runtime=SimpleNamespace(
        status=lambda: (_ for _ in ()).throw(RuntimeError("private")))))):
        assert not fixture._cleanup_owned_fixture(gateway, risk, direct, candidate, events,
                                                  gateway_ready=True, risk_ready=True,
                                                  client_uncertain=False)
    assert not fixture._cleanup_owned_fixture(gateway, risk, SimpleNamespace(), web, events,
                                              gateway_ready=True, risk_ready=True,
                                              client_uncertain=False)
    assert web.actions == direct.actions == risk.actions == gateway.actions == []


def test_raising_direct_counter_prevents_all_cleanup(tmp_path):
    events, gateway, risk, _direct = _owned(tmp_path)
    web = FakeWeb()
    class BrokenDirect(FakeDirect):
        @property
        def unregister_uncertainties(self):
            raise RuntimeError("private")
    direct = BrokenDirect()
    assert not fixture._cleanup_owned_fixture(gateway, risk, direct, web, events,
                                              gateway_ready=True, risk_ready=True,
                                              client_uncertain=False)
    assert web.actions == direct.actions == risk.actions == gateway.actions == []


def test_web_close_checks_own_runtime_before_http_shutdown():
    calls = []
    runtime = FakeRuntime()
    runtime.overrides["unknown_registration_count"] = None
    web = object.__new__(fixture._HttpWeb)
    web.server = SimpleNamespace(should_exit=False)
    web.thread = SimpleNamespace(join=lambda _timeout: calls.append("join"))
    web.host = SimpleNamespace(_runtime=runtime, close=lambda: calls.append("host_close"))
    web.sock = SimpleNamespace(close=lambda: calls.append("socket_close"))
    with pytest.raises(RuntimeError, match="SYNTHETIC_GATEWAY_UNCERTAIN"):
        web.close(pre_close=lambda: True)
    assert web.server.should_exit is False and calls == []


def test_web_close_rechecks_own_runtime_before_host_close():
    calls = []
    runtime = FakeRuntime()
    def join(_timeout):
        calls.append("join")
        runtime.overrides["unregister_uncertainties"] = 1
    web = object.__new__(fixture._HttpWeb)
    web.server = SimpleNamespace(should_exit=False)
    web.thread = SimpleNamespace(join=join, is_alive=lambda: False)
    web.host = SimpleNamespace(_runtime=runtime, close=lambda: calls.append("host_close"))
    web.sock = SimpleNamespace(close=lambda: calls.append("socket_close"))
    with pytest.raises(RuntimeError, match="SYNTHETIC_GATEWAY_UNCERTAIN"):
        web.close(pre_close=lambda: True)
    assert calls == ["join"]


def test_missing_unproven_client_prevents_signal(tmp_path):
    events, gateway, risk, direct = _owned(tmp_path)
    assert not fixture._cleanup_owned_fixture(gateway, risk, direct, None, events,
                                              gateway_ready=True, risk_ready=True,
                                              client_uncertain=False)
    assert direct.actions == risk.actions == gateway.actions == []


@pytest.mark.parametrize("after", ["before", "direct", "risk"])
def test_pre_action_detects_other_client_uncertainty_each_step(tmp_path, after):
    events, gateway, risk, direct = _owned(tmp_path)

    def check():
        return not (after == "before" or (after == "direct" and direct.actions)
                    or (after == "risk" and risk.actions))

    assert not fixture._cleanup_owned_fixture(gateway, risk, direct, None, events,
                                              gateway_ready=True, risk_ready=True,
                                              client_uncertain=False, web_absent_proven=True,
                                              pre_action=check)
    assert direct.actions == ([] if after == "before" else ["direct_close"])
    assert bool(risk.actions) is (after == "risk")
    assert not any(action[0] == "gateway_stop" for action in gateway.actions)


def test_pre_action_is_rechecked_inside_web_close(tmp_path):
    events, gateway, risk, direct = _owned(tmp_path)
    calls = []
    web = object.__new__(fixture._HttpWeb)
    web.server = SimpleNamespace(should_exit=False)
    web.thread = SimpleNamespace(join=lambda _timeout: calls.append("join"), is_alive=lambda: False)
    web.host = SimpleNamespace(_runtime=FakeRuntime(), close=lambda: calls.append("host_close"))
    web.sock = SimpleNamespace(close=lambda: calls.append("socket_close"))
    assert not fixture._cleanup_owned_fixture(gateway, risk, direct, web, events,
                                              gateway_ready=True, risk_ready=True,
                                              client_uncertain=False,
                                              pre_action=lambda: not web.server.should_exit)
    assert calls == ["join"] and direct.actions == risk.actions == []
    assert not any(action[0] == "gateway_stop" for action in gateway.actions)


@pytest.mark.parametrize("result", [None, 1, False])
def test_pre_action_requires_exact_true(tmp_path, result):
    events, gateway, risk, direct = _owned(tmp_path)
    assert not fixture._cleanup_owned_fixture(gateway, risk, direct, FakeWeb(), events,
                                              gateway_ready=True, risk_ready=True,
                                              client_uncertain=False, pre_action=lambda: result)
    assert direct.actions == risk.actions == []
    assert not any(action[0] == "gateway_stop" for action in gateway.actions)


def test_pre_action_exception_is_uncertain(tmp_path):
    events, gateway, risk, direct = _owned(tmp_path)
    def fail():
        raise RuntimeError("private")
    assert not fixture._cleanup_owned_fixture(gateway, risk, direct, FakeWeb(), events,
                                              gateway_ready=True, risk_ready=True,
                                              client_uncertain=False, pre_action=fail)
    assert direct.actions == risk.actions == []


def test_malformed_event_file_prevents_cleanup_actions(tmp_path):
    events, gateway, risk, direct = _owned(tmp_path)
    events.write_text("not-json", encoding="utf-8")
    assert not fixture._cleanup_owned_fixture(gateway, risk, direct, None, events,
                                              gateway_ready=True, risk_ready=True,
                                              client_uncertain=False, web_absent_proven=True)
    assert direct.actions == [] and risk.actions == [] and gateway.actions == []


def test_web_close_rechecks_before_closing_registered_client():
    calls = []
    thread = SimpleNamespace(join=lambda _timeout: calls.append("join"), is_alive=lambda: False)
    host = SimpleNamespace(close=lambda: calls.append("host_close"), _runtime=FakeRuntime())
    sock = SimpleNamespace(close=lambda: calls.append("socket_close"))
    web = object.__new__(fixture._HttpWeb)
    web.server, web.thread, web.host, web.sock = SimpleNamespace(should_exit=False), thread, host, sock
    answers = iter((True, False))
    with pytest.raises(RuntimeError, match="SYNTHETIC_GATEWAY_UNCERTAIN"):
        web.close(pre_close=lambda: next(answers))
    assert calls == ["join"]


def test_missing_closed_marker_cannot_prove_cleanup(tmp_path):
    events, gateway, risk, direct = _owned(tmp_path)
    gateway.append_closed = False
    assert not fixture._cleanup_owned_fixture(gateway, risk, direct, None, events,
                                              gateway_ready=True, risk_ready=True,
                                              client_uncertain=False, web_absent_proven=True)


@pytest.mark.parametrize("exit_code", [False, 0.0, None])
def test_gateway_closed_requires_exact_integer_exit_code(tmp_path, exit_code):
    events, gateway, _risk, _direct = _owned(tmp_path)
    fixture._append_gateway_event(events, "closed", gateway.pid)
    gateway.poll = lambda: exit_code
    assert not fixture._gateway_event_check(events, gateway, closed=True)


@pytest.mark.parametrize("exit_code", [False, 0.0, None])
def test_gateway_wait_requires_exact_integer_exit_code(tmp_path, exit_code):
    events, gateway, risk, direct = _owned(tmp_path)
    original_wait = gateway.wait
    def wait(timeout):
        original_wait(timeout)
        return exit_code
    gateway.wait = wait
    assert not fixture._cleanup_owned_fixture(gateway, risk, direct, None, events,
                                              gateway_ready=True, risk_ready=True,
                                              client_uncertain=False, web_absent_proven=True)
    assert any(action[0] == "gateway_stop" for action in gateway.actions)


@pytest.mark.parametrize("value", [False, 0.0, 1, None])
@pytest.mark.parametrize("phase", [0, 1, 2])
def test_web_close_requires_exact_true_before_each_action(value, phase):
    calls = []
    runtime = FakeRuntime()
    def close_host():
        calls.append("host_close")
        runtime.state = "closed"
    web = object.__new__(fixture._HttpWeb)
    web.server = SimpleNamespace(should_exit=False)
    web.thread = SimpleNamespace(join=lambda _timeout: calls.append("join"), is_alive=lambda: False)
    web.host = SimpleNamespace(_runtime=runtime, close=close_host)
    web.sock = SimpleNamespace(close=lambda: calls.append("socket_close"))
    answers = iter([True] * phase + [value])
    with pytest.raises(RuntimeError, match="SYNTHETIC_GATEWAY_UNCERTAIN"):
        web.close(pre_close=lambda: next(answers))
    assert calls == ([], ["join"], ["join", "host_close"])[phase]
    assert web.server.should_exit is (phase > 0)
