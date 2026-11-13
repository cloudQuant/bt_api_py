"""Shared 64.13 loopback HTTP -> Web -> CURVE -> Risk test scenario.

Only synthetic principals, keys, provider ports and temporary ledgers are used.
No source-gate behavior is changed by this helper. The caller specifies the
status assertion; neither scenario is browser, account, provider, production
or release acceptance.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole

from bt_api_py.gateway_execution_stream_client import _detach_wire
from bt_api_py.risk_web_local_host import LocalRiskWebHost
from bt_api_py.service_config import parse_client_config
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ServiceRuntimeError,
    ZmqTransportFactory,
)
from tests.integration.test_iter64_risk_service_process import (
    ACCOUNT,
    ACCOUNT_ONLY,
    CONTROL,
    DOMAIN,
    OPERATOR,
    RISK,
    RISK_HOST,
    SDK_ROOT,
    STRATEGY,
    _gateway,
    _risk_files,
)
from tests.integration.test_service_account_instrument_metadata import _free_endpoint

WEB_OPS = frozenset({"risk.admin.status", "risk.policy.read", "risk.policy.publish", "risk.audit.read"})
DIRECT_OPS = frozenset({"risk.admin.status", "risk.policy.read", "risk.audit.read"})
DIRECT = WireIdentity(WireRole.APPLICATION_CLIENT, "independent-operator", "independent-desk")
LOGIN = "ab" * 32  # synthetic fixture token


def _gateway_child(config_path: str) -> None:
    """Run only this test's Gateway; stdin requests graceful shutdown."""
    data = json.loads(Path(config_path).read_text(encoding="utf-8"))
    keys = {name: tuple(value.encode("ascii") for value in pair)
            for name, pair in data["keys"].items()}
    publics = {keys["strategy"][0]: STRATEGY.principal_ref,
               keys["risk_delegate"][0]: RISK.principal_ref,
               keys["risk_control"][0]: CONTROL.principal_ref}
    messages, controls = queue.Queue(), queue.Queue()

    def report():
        while True:
            msg = messages.get()
            if msg[0] in {"ready", "error", "closed"}:
                # Do not print key material, raw exceptions or journal identifiers.
                with Path(data["events"]).open("a", encoding="utf-8") as out:
                    out.write(json.dumps({"event": msg[0], "pid": os.getpid()}) + "\n")
            if msg[0] == "closed":
                return

    def receive_stop():
        if sys.stdin.readline().strip() == "stop":
            controls.put("stop")

    reporter = threading.Thread(target=report, daemon=True)
    reporter.start()
    threading.Thread(target=receive_stop, daemon=True).start()
    _gateway(data["root"], data["endpoint"], keys["gateway_server"], publics, messages, controls)
    reporter.join(timeout=5)
    if reporter.is_alive():
        raise RuntimeError("gateway_test_reporter_not_closed")


def _wait_for(predicate, seconds=40):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("synthetic_service_readiness_timeout")


def _events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _direct_admin(endpoint: str, keys: dict) -> ServiceClientRuntime:
    config = parse_client_config({
        "client_config_schema_version": 1, "process_role": "application_client",
        "registration": {"application_ref": DIRECT.actor_ref},
        "clients": {"risk": {"config_schema_version": 1, "settings": {"bindings": [{
            "mount": "application.risk_admin", "service_id": RISK_HOST.actor_ref,
            "instance_id": "risk-a", "scope_ref": "risk-scope", "account_ref": ACCOUNT,
            "transport_ref": "risk"}]}}},
        "communication": {"config_schema_version": 1, "profiles": {"risk": {
            "backend": "zmq", "config_schema_version": 1,
            "endpoints": {"rpc_connect": endpoint}, "security_profile_ref": "risk-curve"}}},
    })
    factory = ZmqTransportFactory({
        "risk-curve": ClientCurveKeys(keys["risk_server"][0], *keys["independent"])
    })
    return ServiceClientRuntime(
        config, identity=DIRECT, scope_resolver=lambda _: ACCOUNT_ONLY,
        peer_resolver=lambda _: RISK_HOST, transport_factory=factory,
        requested_operations={"application.risk_admin": DIRECT_OPS},
        require_unregister_ack=True,
    )


def _admin(client, operation, arguments=None):
    return _detach_wire(client.call(
        "application.risk_admin", operation, arguments or {}, account_ref=ACCOUNT,
        scope_ref="risk-scope",
    ))


def _web_config(path: Path, origin: str, endpoint: str, keys: dict) -> None:
    config = {"schema_version": 1, "origin": origin, "operator": {
        "principal_ref": OPERATOR.principal_ref, "actor_ref": OPERATOR.actor_ref,
        "login_token_sha256": hashlib.sha256(LOGIN.encode("ascii")).hexdigest(),
        "execution_domain_ref": DOMAIN, "account_ref": ACCOUNT, "scope_ref": "risk-scope",
        "service_id": RISK_HOST.actor_ref, "service_principal_ref": RISK_HOST.principal_ref,
        "instance_id": "risk-a", "endpoint": endpoint,
        "server_public_key": keys["risk_server"][0].decode("ascii"),
        "client_public_key": keys["operator"][0].decode("ascii"),
        "client_secret_key": keys["operator"][1].decode("ascii"),
        "operations": sorted(WEB_OPS),
    }}
    path.write_text(json.dumps(config), encoding="utf-8")
    path.chmod(0o600)


class _HttpWeb:
    def __init__(self, path: Path, sock: socket.socket):
        self.host = LocalRiskWebHost(path)
        self.server = uvicorn.Server(uvicorn.Config(
            self.host.app, log_level="error", proxy_headers=False, server_header=False,
            lifespan="off", access_log=False,
        ))
        self.sock = sock
        self.thread = threading.Thread(target=lambda: self.server.run(sockets=[sock]), daemon=True)

    def start(self):
        self.host.start()
        self.thread.start()
        _wait_for(lambda: self.server.started or not self.thread.is_alive(), seconds=10)
        assert self.server.started
        return self

    def close(self):
        self.server.should_exit = True
        self.thread.join(10)
        if self.thread.is_alive():
            raise RuntimeError("test_web_thread_cleanup_uncertain")
        self.host.close()
        self.sock.close()


def _listener():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(32)
    return sock


def exercise_web_admin_scenario(tmp_path, *, check_available):
    keys = {name: zmq.curve_keypair() for name in (
        "gateway_server", "strategy", "risk_delegate", "risk_control", "risk_server",
        "operator", "independent")}
    gateway_root, risk_root = tmp_path / "gateway", tmp_path / "risk"
    gateway_root.mkdir()
    risk_root.mkdir()
    gateway_endpoint, risk_endpoint = _free_endpoint(), _free_endpoint()
    events = tmp_path / "gateway-events.jsonl"
    gateway_config = tmp_path / "gateway-test.json"
    gateway_config.write_text(json.dumps({
        "root": str(gateway_root), "endpoint": gateway_endpoint, "events": str(events),
        "keys": {name: [item.decode("ascii") for item in pair] for name, pair in keys.items()},
    }), encoding="utf-8")
    gateway_config.chmod(0o600)
    logs = (tmp_path / "gateway.log").open("wb")
    risk_logs = (tmp_path / "risk.log").open("wb")
    gateway = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--synthetic-gateway", str(gateway_config)],
        cwd=str(SDK_ROOT), stdin=subprocess.PIPE, stdout=logs, stderr=subprocess.STDOUT,
    )
    risk = direct = web = None
    web_uncertain = False
    try:
        ready = _wait_for(lambda: next((e for e in _events(events) if e["event"] in {"ready", "error"}), None))
        assert ready == {"event": "ready", "pid": gateway.pid}
        config_path = _risk_files(risk_root, keys, gateway_endpoint, risk_endpoint)
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["principals"][1]["operations"] = sorted(WEB_OPS)
        config["principals"].append({
            "principal_ref": DIRECT.principal_ref, "role": "operator", "actor_ref": DIRECT.actor_ref,
            "strategy_refs": [], "operations": sorted(DIRECT_OPS), "public_key_file": "keys/independent.public",
        })
        config_path.write_text(json.dumps(config), encoding="utf-8")
        # This explicit policy preserves the default self-trade gate; no order is sent.
        policy_path = risk_root / "risk_policy.yaml"
        policy_text = policy_path.read_text(encoding="utf-8")
        original_self_trade = "self_trade: {enabled: false"
        assert policy_text.count(original_self_trade) == 1
        policy_path.write_text(policy_text.replace(
            original_self_trade, "self_trade: {enabled: true"), encoding="utf-8")
        ready_file = risk_root / "ready.json"
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        risk = subprocess.Popen([
            sys.executable, "-m", "bt_api_py.risk_service_host", "run", "--config", str(config_path),
            "--ready-file", str(ready_file),
        ], cwd=str(SDK_ROOT), env=env, stdout=risk_logs, stderr=subprocess.STDOUT)

        def risk_ready():
            if risk.poll() is not None:
                raise AssertionError("synthetic_risk_exited_before_ready")
            return ready_file.exists()

        _wait_for(risk_ready, seconds=60)
        assert json.loads(ready_file.read_text(encoding="utf-8"))["pid"] == risk.pid
        direct = _direct_admin(risk_endpoint, keys)
        direct.start()
        assert direct.healthy
        before = _admin(direct, "risk.admin.status")
        assert before["status"] == "OBSERVED"
        initial_policy = _admin(direct, "risk.policy.read")
        assert initial_policy["version"] == 1
        assert initial_policy["document"]["self_trade"]["enabled"] is True
        before_available = before["summary"]["available"]
        assert type(before_available) is bool
        check_available(before_available)
        # Deliberately use the authenticated lower-level transport to bypass
        # the SDK's local operation filter and exercise the Registry's ACL.
        # This is only a synthetic request against this test's isolated host.
        (direct_binding,) = direct.bindings
        with pytest.raises(ServiceRuntimeError) as denied:
            direct._rpc(
                direct_binding.service_id, direct_binding.transport_ref, direct_binding.scope,
                "risk.policy.publish", {
                    "registration_id": direct_binding.registration_id,
                    "service_epoch": direct_binding.service_epoch,
                    "instance_id": direct_binding.instance_id,
                    "arguments": {"expected_version": 1, "document": initial_policy["document"],
                                  "reason": "synthetic unauthorized", "exchange_rules_text": None},
                }, control=False, timeout=5,
            )
        assert denied.value.code == "SCOPE_DENIED"
        assert denied.value.service_reply_code == "SCOPE_DENIED"
        assert _admin(direct, "risk.policy.read")["version"] == 1

        listener = _listener()
        origin = "http://127.0.0.1:" + str(listener.getsockname()[1])
        web_path = tmp_path / "web.json"
        _web_config(web_path, origin, risk_endpoint, keys)
        web = _HttpWeb(web_path, listener)
        try:
            web.start()
        except Exception:
            web_uncertain = True
            raise
        with httpx.Client(base_url=origin, timeout=10, trust_env=False) as http:
            assert http.get("/api/v1/risk/status").status_code == 401
            assert http.post("/login", data={"token": LOGIN}).status_code == 403
            login = http.post("/login", data={"token": LOGIN}, headers={"Origin": origin})
            assert login.status_code == 200
            headers = {"X-Risk-Session": login.json()["session"]}
            session = http.get("/api/v1/risk/session", headers=headers).json()
            status = http.get("/api/v1/risk/status", headers=headers)
            assert status.status_code == 200 and status.json()["status"] == "OBSERVED"
            for field in ("available", "policy_version", "policy_id", "policy_digest"):
                assert status.json()["summary"][field] == before["summary"][field]
            read = http.get("/api/v1/risk/policy", headers=headers)
            assert read.status_code == 200 and read.json() == _admin(direct, "risk.policy.read")
            document = read.json()["document"]
            document["per_order"]["max_quantity"]["strategy-a"]["rb"] = "1"
            publish = {"expected_version": 1, "document": document, "reason": "synthetic tighten"}
            assert http.post("/api/v1/risk/policy", json=publish,
                             headers={**headers, "Origin": origin}).status_code == 403
            assert _admin(direct, "risk.policy.read")["version"] == 1
            write = {**headers, "Origin": origin, "X-CSRF-Token": session["csrf_token"]}
            changed = http.post("/api/v1/risk/policy", json=publish, headers=write)
            assert changed.status_code == 200 and changed.json()["version"] == 2, changed.text
            assert _admin(direct, "risk.policy.read")["version"] == 2
            denied = http.post("/api/v1/risk/latches/" + "0f" * 16 + "/release",
                               json={"expected_version": 1, "reason": "synthetic"}, headers=write)
            assert denied.status_code == 403
            audit = _admin(direct, "risk.audit.read", {"after": 0, "limit": 100})
            assert audit["status"] == "OBSERVED"
            assert any(row["kind"] == "policy_publish" and row["policy_version"] == 2
                       and row["detail"]["operator"] == OPERATOR.principal_ref
                       for row in audit["records"])
        try:
            web.close()
        except Exception:
            web_uncertain = True
            raise
        web = None
        assert risk.poll() is None and _admin(direct, "risk.policy.read")["version"] == 2

        listener = _listener()
        origin = "http://127.0.0.1:" + str(listener.getsockname()[1])
        _web_config(web_path, origin, risk_endpoint, keys)
        web = _HttpWeb(web_path, listener)
        try:
            web.start()
        except Exception:
            web_uncertain = True
            raise
        with httpx.Client(base_url=origin, timeout=10, trust_env=False) as http:
            login = http.post("/login", data={"token": LOGIN}, headers={"Origin": origin})
            assert login.status_code == 200
            headers = {"X-Risk-Session": login.json()["session"]}
            assert http.get("/api/v1/risk/policy", headers=headers).json()["version"] == 2
            web_available = http.get("/api/v1/risk/status", headers=headers).json()["summary"]["available"]
            assert type(web_available) is bool and web_available is before_available
            check_available(web_available)
        try:
            web.close()
        except Exception:
            web_uncertain = True
            raise
        web = None
        assert risk.poll() is None
        final_available = _admin(direct, "risk.admin.status")["summary"]["available"]
        assert type(final_available) is bool and final_available is before_available
        check_available(final_available)
    finally:
        if web is not None and not web_uncertain:
            try:
                web.close()
            except Exception:
                web_uncertain = True
                print(f"TEST_CLEANUP web=uncertain risk_pid={None if risk is None else risk.pid} "
                      f"gateway_pid={gateway.pid}", flush=True)
        if not web_uncertain and direct is not None:
            try:
                direct.close()
                if (direct.state != "closed" or direct.cleanup_failures or
                        direct.unregister_uncertainties):
                    web_uncertain = True
            except Exception:
                web_uncertain = True
            if web_uncertain:
                print(f"TEST_CLEANUP independent=uncertain risk_pid={None if risk is None else risk.pid} "
                      f"gateway_pid={gateway.pid}", flush=True)
        if not web_uncertain and risk is not None and risk.poll() is None:
            risk.send_signal(signal.SIGTERM)  # only the exact child created by this test
            try:
                risk.wait(timeout=30)
            except subprocess.TimeoutExpired:
                web_uncertain = True
                print(f"TEST_CLEANUP risk=uncertain risk_pid={risk.pid} gateway_pid={gateway.pid}", flush=True)
        if not web_uncertain and gateway.poll() is None:
            assert gateway.stdin is not None
            gateway.stdin.write(b"stop\n")
            gateway.stdin.flush()
            try:
                gateway.wait(timeout=30)
            except subprocess.TimeoutExpired:
                web_uncertain = True
                print(f"TEST_CLEANUP gateway=uncertain gateway_pid={gateway.pid}", flush=True)
        if not web_uncertain:
            assert gateway.returncode == 0
            assert {"event": "closed", "pid": gateway.pid} in _events(events)
            print(f"TEST_CLEANUP complete risk_pid={None if risk is None else risk.pid} "
                  f"risk_exit={None if risk is None else risk.returncode} "
                  f"gateway_pid={gateway.pid} gateway_exit={gateway.returncode}", flush=True)
            assert risk is None or risk.returncode == 0
        else:
            print(f"TEST_CLEANUP retained risk_pid={None if risk is None else risk.pid} "
                  f"gateway_pid={gateway.pid}", flush=True)
        logs.close()
        risk_logs.close()
        assert not web_uncertain, "synthetic_host_cleanup_uncertain; retained host was not stopped"


if __name__ == "__main__" and len(sys.argv) == 3 and sys.argv[1] == "--synthetic-gateway":
    _gateway_child(sys.argv[2])
