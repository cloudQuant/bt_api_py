"""Risk operator Web host (64.13 interface): admin caller wiring and loopback-only serving.

Evidence level: UNIT/FAKE. The SDK runtime is a fake with the real snapshot types; the
client configuration goes through the real ``parse_client_config``. No Risk service,
CURVE transport, Gateway or account is involved.
"""

from __future__ import annotations

import hashlib
import json
import os
from types import MappingProxyType

import pytest
import zmq
from bt_api_base.risk_service_protocol import RISK_ADMIN_OPERATIONS, RISK_ORDER_OPERATIONS
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_risk.web.app import RiskAdminCallError
from fastapi.testclient import TestClient

import bt_api_py.risk_web_local_host as host_module
from bt_api_py.risk_web_local_host import (
    RISK_ADMIN_MOUNT,
    LocalRiskWebHost,
    build_admin_caller,
    loopback_origin,
)
from bt_api_py.service_runtime import (
    ServiceCallOutcomeUnknownError,
    ServiceRuntimeError,
    ServiceRuntimeRequestNotSentError,
)
from bt_api_py.service_runtime_thread import BindingSnapshot, RuntimeSnapshot, ThreadedRuntimeError

ORIGIN = "http://127.0.0.1:8710"
LOGIN_TOKEN = "a1" * 32
IDENTITY = WireIdentity(WireRole.APPLICATION_CLIENT, "operator-a", "desk-1")
PEER = WireIdentity(WireRole.SERVICE, "risk-service", "risk-svc")
SCOPE = WireScope("domain-a", account_ref="account-a")
ROUTE = {"identity": IDENTITY, "scope": SCOPE, "scope_ref": "scope-a", "service_id": "risk-svc",
         "instance_id": "risk", "peer": PEER}


def binding(**overrides):
    values = {"registration_id": "reg-1", "service_epoch": "epoch-1", "active": True,
              "mount": RISK_ADMIN_MOUNT, "service_id": "risk-svc", "instance_id": "risk",
              "scope_ref": "scope-a", "scope": SCOPE, "operations": frozenset(RISK_ADMIN_OPERATIONS),
              "required": True, "authenticated_peer": PEER, "server_key_fingerprint": "a" * 64}
    values.update(overrides)
    return BindingSnapshot(**values)


def reply_for(operation, arguments):
    if operation == "risk.cancel.request":
        return MappingProxyType({"status": "OBSERVED", "operation_id": arguments["operation_id"],
                                 "state": "PENDING"})
    return MappingProxyType({"status": "OBSERVED", "summary": MappingProxyType({"available": True})})


class FakeRuntime:
    """The threaded SDK runtime surface the caller and host use."""

    def __init__(self, factory=None):
        self.factory_result = factory() if factory is not None else None
        self.bindings = (binding(),)
        self.identity = IDENTITY
        self.state, self.healthy, self.thread_alive = "new", True, True
        self.calls, self.error, self.on_call, self.closed, self.start_healthy = [], None, None, 0, True

    def status(self):
        return RuntimeSnapshot(self.state, self.thread_alive, self.healthy, None, self.bindings, 0, 0,
                               self.identity)

    def start(self, *, timeout_seconds):
        self.state = "running"
        self.healthy = self.start_healthy
        return self.status()

    def call(self, mount, operation, arguments, **kwargs):
        self.calls.append((mount, operation, dict(arguments), kwargs))
        if self.on_call is not None:
            self.on_call()
        if self.error is not None:
            raise self.error
        return reply_for(operation, arguments)

    def close(self, *, timeout_seconds):
        self.closed += 1
        self.state, self.thread_alive = "closed", False
        return RuntimeSnapshot("closed", False, False, None, (), 0, 0, self.identity)


def running():
    runtime = FakeRuntime()
    runtime.state = "running"
    return runtime


def refused(code, *, from_service=True):
    error = ServiceRuntimeError(code)
    if from_service:
        error.service_reply_code = code
    return error


def outcome(call, *args):
    with pytest.raises(RiskAdminCallError) as caught:
        call(*args)
    return caught.value.kind, caught.value.code, str(caught.value)


def test_caller_sends_one_admin_operation_on_the_admin_mount_only():
    runtime = running()
    call = build_admin_caller(runtime, **ROUTE)
    reply = call("risk.admin.status", {}, 5.0)
    assert reply["status"] == "OBSERVED"
    assert runtime.calls == [(RISK_ADMIN_MOUNT, "risk.admin.status", {},
                              {"account_ref": "account-a", "scope_ref": "scope-a", "timeout_seconds": 5.0})]


@pytest.mark.parametrize("operation", sorted(RISK_ORDER_OPERATIONS) + ["direct.control.apply", "risk.admin.x", 7])
def test_order_and_foreign_operations_are_never_sent(operation):
    runtime = running()
    runtime.bindings = (binding(operations=frozenset(RISK_ADMIN_OPERATIONS) | RISK_ORDER_OPERATIONS),)
    call = build_admin_caller(runtime, **ROUTE)
    assert outcome(call, operation, {}, 5.0)[:2] == ("NOT_SENT", "INVALID_ARGUMENTS")
    assert outcome(call, "risk.admin.status", [], 5.0)[:2] == ("NOT_SENT", "INVALID_ARGUMENTS")
    assert outcome(call, "risk.admin.status", {}, 0)[:2] == ("NOT_SENT", "INVALID_ARGUMENTS")
    assert runtime.calls == []


@pytest.mark.parametrize("change", [
    lambda r: setattr(r, "state", "failed"),
    lambda r: setattr(r, "healthy", False),
    lambda r: setattr(r, "thread_alive", False),
    lambda r: setattr(r, "identity", WireIdentity(WireRole.APPLICATION_CLIENT, "operator-b", "desk-1")),
    lambda r: setattr(r, "identity", WireIdentity(WireRole.APPLICATION_CLIENT, "operator-a", "desk-1", "d")),
    lambda r: setattr(r, "bindings", ()),
    lambda r: setattr(r, "bindings", (binding(active=False),)),
    lambda r: setattr(r, "bindings", (binding(mount="application.risk_read"),)),
    lambda r: setattr(r, "bindings", (binding(service_id="other-risk"),)),
    lambda r: setattr(r, "bindings", (binding(instance_id="other"),)),
    lambda r: setattr(r, "bindings", (binding(scope_ref="scope-b"),)),
    lambda r: setattr(r, "bindings", (binding(scope=WireScope("domain-a", account_ref="account-b")),)),
    lambda r: setattr(r, "bindings", (binding(scope=WireScope("domain-a", account_ref="account-a",
                                                                strategy_ref="strategy-a")),)),
    lambda r: setattr(r, "bindings", (binding(operations=frozenset({"risk.policy.read"})),)),
    lambda r: setattr(r, "bindings", (binding(operations=None),)),
    lambda r: setattr(r, "bindings", (binding(authenticated_peer=None),)),
    lambda r: setattr(r, "bindings", (binding(authenticated_peer=WireIdentity(WireRole.SERVICE, "x", "risk-svc")),)),
    lambda r: setattr(r, "bindings", (binding(server_key_fingerprint="not-hex"),)),
    lambda r: setattr(r, "bindings", (binding(), binding(registration_id="reg-2"))),
])
def test_without_exactly_this_operators_registration_nothing_is_sent(change):
    runtime = running()
    change(runtime)
    call = build_admin_caller(runtime, **ROUTE)
    assert outcome(call, "risk.admin.status", {}, 5.0)[:2] == ("NOT_SENT", "ROUTE_UNAVAILABLE")
    assert runtime.calls == []


@pytest.mark.parametrize("error,kind,code", [
    (ServiceRuntimeRequestNotSentError(), "NOT_SENT", "REQUEST_NOT_SENT"),
    (ThreadedRuntimeError("QUEUE_FULL"), "NOT_SENT", "QUEUE_FULL"),
    (ThreadedRuntimeError("TIMEOUT_BEFORE_START"), "NOT_SENT", "TIMEOUT_BEFORE_START"),
    (ThreadedRuntimeError("TIMEOUT_AFTER_START"), "TIMEOUT", ""),
    (ThreadedRuntimeError("OWNER_OUTCOME_UNKNOWN"), "UNKNOWN", ""),
    (refused("DENIED"), "REFUSED", "DENIED"),
    (refused("SCOPE_DENIED"), "REFUSED", "SCOPE_DENIED"),
    (refused("DENIED", from_service=False), "UNKNOWN", ""),
    (refused("INVALID_REQUEST"), "UNKNOWN", ""),
    (refused("HANDLER_FAILED"), "UNKNOWN", ""),
    (ServiceCallOutcomeUnknownError("m-1", "risk.policy.publish"), "UNKNOWN", ""),
    (RuntimeError("client_secret_key=hunter2"), "UNKNOWN", ""),
])
def test_runtime_failures_map_to_finite_outcomes_without_retry(error, kind, code):
    runtime = running()
    runtime.error = error
    call = build_admin_caller(runtime, **ROUTE)
    got = outcome(call, "risk.policy.publish", {"expected_version": 1}, 5.0)
    assert got[:2] == (kind, code) and "hunter2" not in got[2]
    assert len(runtime.calls) == 1


def test_a_reply_or_refusal_across_a_re_registration_is_unknown():
    runtime = running()
    call = build_admin_caller(runtime, **ROUTE)
    runtime.on_call = lambda: setattr(runtime, "bindings", (binding(registration_id="reg-2"),))
    assert outcome(call, "risk.cancel.request", {"operation_id": "c-1"}, 5.0)[0] == "UNKNOWN"
    runtime.bindings = (binding(),)
    runtime.error = refused("DENIED")
    runtime.on_call = lambda: setattr(runtime, "bindings", (binding(service_epoch="epoch-2"),))
    assert outcome(call, "risk.cancel.request", {"operation_id": "c-1"}, 5.0)[0] == "UNKNOWN"
    assert len(runtime.calls) == 2


def test_caller_route_must_be_an_account_operator_and_a_service_peer():
    for bad in ({"identity": WireIdentity(WireRole.STRATEGY_CLIENT, "s", "s")},
                {"peer": WireIdentity(WireRole.APPLICATION_CLIENT, "p", "risk-svc")},
                {"scope": WireScope("domain-a", account_ref="account-a", strategy_ref="s")},
                {"scope": WireScope("domain-a")},
                {"scope_ref": " scope-a"}):
        with pytest.raises(ValueError, match="INVALID_RISK_ADMIN_ROUTE"):
            build_admin_caller(running(), **{**ROUTE, **bad})


@pytest.mark.parametrize("origin", ["http://localhost:8710", "http://0.0.0.0:8710", "http://[::1]:8710",
                                    "http://127.0.0.2:8710", "http://192.168.1.2:8710", "https://127.0.0.1:8710",
                                    "http://127.0.0.1", "http://127.0.0.1:8710/x", "http://u@127.0.0.1:8710",
                                    "http://127.0.0.1:99999", 8710])
def test_only_127_0_0_1_origins_are_accepted(origin):
    with pytest.raises(ValueError, match="LOOPBACK_ORIGIN_REQUIRED"):
        loopback_origin(origin)


def test_loopback_origin_accepts_127_0_0_1_with_a_port():
    assert loopback_origin(ORIGIN) == ("127.0.0.1", 8710)
    assert loopback_origin(ORIGIN + "/") == ("127.0.0.1", 8710)


def write_config(tmp_path, **changes):
    server, client = zmq.curve_keypair(), zmq.curve_keypair()
    operator = {"principal_ref": "operator-a", "actor_ref": "desk-1",
                "login_token_sha256": hashlib.sha256(LOGIN_TOKEN.encode()).hexdigest(),
                "execution_domain_ref": "domain-a", "account_ref": "account-a", "scope_ref": "scope-a",
                "service_id": "risk-svc", "service_principal_ref": "risk-service", "instance_id": "risk",
                "endpoint": "tcp://127.0.0.1:47011", "server_public_key": server[0].decode(),
                "client_public_key": client[0].decode(), "client_secret_key": client[1].decode(),
                "operations": sorted(RISK_ADMIN_OPERATIONS - {"risk.latch.release"})}
    operator.update(changes.pop("operator", {}))
    config = {"schema_version": 1, "origin": ORIGIN, "operator": operator, **changes}
    path = (tmp_path / "risk-web.json").resolve()
    path.write_text(json.dumps(config), encoding="utf-8")
    path.chmod(0o600)
    return path


@pytest.fixture
def fake_sdk(monkeypatch):
    captured = []

    def service_client_runtime(config, **kwargs):
        captured.append((config, kwargs))
        return "inner-runtime"

    monkeypatch.setattr(host_module, "ServiceClientRuntime", service_client_runtime)
    monkeypatch.setattr(host_module, "ThreadedServiceClientRuntime", FakeRuntime)
    return captured


@pytest.mark.parametrize("change", [
    {"origin": "http://localhost:8710"}, {"origin": "http://0.0.0.0:8710"},
    {"origin": "http://192.168.1.2:8710"}, {"origin": "https://127.0.0.1:8710"},
    {"schema_version": 2}, {"extra": 1},
    {"operator": {"operations": ["risk.admin.status", "risk.order.reserve"]}},
    {"operator": {"operations": []}},
    {"operator": {"operations": ["risk.admin.status", "risk.admin.status"]}},
    {"operator": {"login_token_sha256": "A" * 64}},
    {"operator": {"account_ref": ""}},
    {"operator": {"client_public_key": zmq.curve_keypair()[0].decode()}},
])
def test_invalid_host_configuration_is_refused(tmp_path, fake_sdk, change):
    with pytest.raises(ValueError, match="LOCAL_RISK_WEB_CONFIG_INVALID"):
        LocalRiskWebHost(write_config(tmp_path, **change))
    assert fake_sdk == []


def test_unprotected_or_linked_configuration_is_refused(tmp_path, fake_sdk):
    path = write_config(tmp_path)
    path.chmod(0o644)
    with pytest.raises(ValueError, match="PROTECTED_FILE_UNAVAILABLE"):
        LocalRiskWebHost(path)
    path.chmod(0o600)
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(ValueError, match="PROTECTED_FILE_UNAVAILABLE"):
        LocalRiskWebHost(link)


def test_host_registers_the_operator_on_the_admin_mount_and_serves_the_api(tmp_path, fake_sdk):
    host = LocalRiskWebHost(write_config(tmp_path))
    runtime = host._runtime
    (config, kwargs), = fake_sdk
    public = config.to_public_dict()
    (client_entry,) = public["clients"].values()
    (bound,) = client_entry["settings"]["bindings"]
    assert bound["mount"] == RISK_ADMIN_MOUNT and bound["account_ref"] == "account-a"
    assert kwargs["identity"] == IDENTITY
    assert kwargs["requested_operations"] == {RISK_ADMIN_MOUNT: RISK_ADMIN_OPERATIONS - {"risk.latch.release"}}
    assert kwargs["scope_resolver"]("scope-a") == SCOPE and kwargs["peer_resolver"]("risk-svc") == PEER
    with pytest.raises(ValueError):
        kwargs["scope_resolver"]("scope-b")
    assert "client_secret_key" not in json.dumps(public) and "secret" not in repr(config).lower()
    assert host.start() is host and runtime.state == "running"

    browser = TestClient(host.app, base_url=ORIGIN, client=("127.0.0.1", 50001))
    assert browser.get("/api/v1/risk/status").status_code == 401
    assert browser.post("/login", data={"token": LOGIN_TOKEN}).json() == {"error": "ORIGIN_DENIED"}
    assert browser.post("/login", data={"token": "b" * 64}, headers={"Origin": ORIGIN}).status_code == 401
    login = browser.post("/login", data={"token": LOGIN_TOKEN}, headers={"Origin": ORIGIN})
    assert login.status_code == 200 and "set-cookie" not in login.headers
    headers = {"X-Risk-Session": login.json()["session"]}
    session = browser.get("/api/v1/risk/session", headers=headers).json()
    assert "risk.latch.release" not in session["operations"]
    assert browser.get("/api/v1/risk/status", headers=headers).json()["status"] == "OBSERVED"
    write = {**headers, "Origin": ORIGIN, "X-CSRF-Token": session["csrf_token"]}
    cancel = {"operation_id": "cancel-1", "scope_kind": "account", "scope_value": ""}
    assert browser.post("/api/v1/risk/cancel", json=cancel, headers=write).json()["state"] == "PENDING"
    release = browser.post("/api/v1/risk/latches/" + "0f" * 16 + "/release",
                           json={"expected_version": 1, "reason": "checked"}, headers=write)
    assert release.json() == {"error": "OPERATION_NOT_GRANTED"}
    assert [(c[0], c[1], c[3]["account_ref"], c[3]["scope_ref"]) for c in runtime.calls] == [
        (RISK_ADMIN_MOUNT, "risk.admin.status", "account-a", "scope-a"),
        (RISK_ADMIN_MOUNT, "risk.cancel.request", "account-a", "scope-a")]

    # A second, non-page client logs in with the same login token and uses the same API.
    other = TestClient(host.app, base_url=ORIGIN, client=("127.0.0.1", 50002))
    second = other.post("/login", data={"token": LOGIN_TOKEN}, headers={"Origin": ORIGIN}).json()["session"]
    assert second != headers["X-Risk-Session"]
    assert other.get("/api/v1/risk/status", headers={"X-Risk-Session": second}).status_code == 200
    assert other.post("/logout", headers={"X-Risk-Session": second, "Origin": ORIGIN}).json() == {
        "status": "SIGNED_OUT"}
    assert other.get("/api/v1/risk/status", headers={"X-Risk-Session": second}).status_code == 401
    assert browser.get("/api/v1/risk/status", headers=headers).status_code == 200

    calls_before_close = len(runtime.calls)
    host.close()
    assert runtime.closed == 1 and len(runtime.calls) == calls_before_close  # closing sends nothing
    assert browser.get("/api/v1/risk/status", headers=headers).status_code == 401


def test_a_changed_configuration_ends_every_session(tmp_path, fake_sdk):
    path = write_config(tmp_path)
    host = LocalRiskWebHost(path).start()
    browser = TestClient(host.app, base_url=ORIGIN, client=("127.0.0.1", 50003))
    token = browser.post("/login", data={"token": LOGIN_TOKEN}, headers={"Origin": ORIGIN}).json()["session"]
    assert browser.get("/api/v1/risk/status", headers={"X-Risk-Session": token}).status_code == 200
    original, before = path.read_bytes(), path.stat()
    path.write_bytes(b"changed")
    path.write_bytes(original)
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert browser.get("/api/v1/risk/status", headers={"X-Risk-Session": token}).status_code == 401
    assert browser.post("/login", data={"token": LOGIN_TOKEN}, headers={"Origin": ORIGIN}).status_code == 401
    host.close()


def test_a_session_expires_after_thirty_minutes(tmp_path, fake_sdk, monkeypatch):
    import time
    from types import SimpleNamespace

    host = LocalRiskWebHost(write_config(tmp_path)).start()
    browser = TestClient(host.app, base_url=ORIGIN, client=("127.0.0.1", 50004))
    base = time.monotonic()
    monkeypatch.setattr(host_module, "time", SimpleNamespace(monotonic=lambda: base))
    token = browser.post("/login", data={"token": LOGIN_TOKEN}, headers={"Origin": ORIGIN}).json()["session"]
    monkeypatch.setattr(host_module, "time", SimpleNamespace(monotonic=lambda: base + 1799))
    assert browser.get("/api/v1/risk/status", headers={"X-Risk-Session": token}).status_code == 200
    monkeypatch.setattr(host_module, "time", SimpleNamespace(monotonic=lambda: base + 1800))
    assert browser.get("/api/v1/risk/status", headers={"X-Risk-Session": token}).status_code == 401
    host.close()


def test_start_fails_closed_when_the_risk_registration_is_unhealthy(tmp_path, fake_sdk):
    host = LocalRiskWebHost(write_config(tmp_path))
    host._runtime.start_healthy = False
    with pytest.raises(RuntimeError, match="^RISK_ADMIN_UNAVAILABLE$"):
        host.start()
    assert host._runtime.closed == 1


def test_main_serves_on_127_0_0_1_and_closes_its_registration(tmp_path, fake_sdk, monkeypatch):
    import signal

    import uvicorn

    served = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: served.append(kwargs))
    monkeypatch.setattr(signal, "signal", lambda *args: None)
    created = []
    real_init = LocalRiskWebHost.__init__

    def tracking_init(self, path):
        real_init(self, path)
        created.append(self)

    monkeypatch.setattr(LocalRiskWebHost, "__init__", tracking_init)
    host_module.main(["--config", str(write_config(tmp_path))])
    assert served == [{"host": "127.0.0.1", "port": 8710, "proxy_headers": False, "server_header": False}]
    assert created[0]._runtime.closed == 1 and created[0]._runtime.calls == []
