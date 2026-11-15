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
import re
import secrets
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import suppress
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
from bt_api_py.service_runtime_thread import RuntimeSnapshot
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

_UNCERTAIN_REASONS = frozenset({
    "RUN_FAILED", "RESOURCE_UNVERIFIED", "HOST_UNVERIFIED", "OWNER_UNVERIFIED",
    "CLEANUP_EXCEPTION", "MESSAGE_UNVERIFIED",
})


class _SyntheticKeys:
    """Keep synthetic key material out of object representations."""

    def __init__(self, values):
        self._values = values

    def __getitem__(self, name):
        return self._values[name]

    def items(self):
        return self._values.items()

    def __repr__(self):
        return "<synthetic-key-material-redacted>"


class _SyntheticConfig(dict):
    def __repr__(self):
        return "<synthetic-configuration-redacted>"


class _RetainedFixture:
    """Keep uncertain test resources referenced while this process lives."""

    def __init__(self, *resources):
        self._resources = resources

    def __repr__(self):
        return "<synthetic-fixture-retained>"


_RETAINED_FIXTURES: dict[str, _RetainedFixture] = {}


def _private_write(path: Path, data: str) -> None:
    """Create or replace a test-only file without a world-readable interval."""
    descriptor, temporary = tempfile.mkstemp(prefix=".synthetic-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        with suppress(OSError):
            os.close(descriptor)
        raise RuntimeError("SYNTHETIC_FILE_UNCERTAIN") from None


def _append_gateway_event(path: Path, event: str, pid: int, reason: str | None = None) -> None:
    """Persist only fixed fixture metadata; never serialize a queue payload."""
    if event not in {"ready", "closed", "error", "cleanup_uncertain", "health"} or type(pid) is not int or pid <= 0:
        raise ValueError("SYNTHETIC_EVENT_INVALID")
    row = {"event": event, "pid": pid}
    if event in {"error", "cleanup_uncertain"}:
        row["reason"] = reason if reason in _UNCERTAIN_REASONS else "UNKNOWN"
    elif event == "health":
        if type(reason) is not str or re.fullmatch(r"[0-9a-f]{32}", reason) is None:
            raise ValueError("SYNTHETIC_EVENT_INVALID")
        row["nonce"] = reason
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(json.dumps(row, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _report_gateway_messages(messages, events_path: Path, pid: int, finished, state) -> None:
    """Report fixed codes and require a consumed matching close marker."""
    try:
        while True:
            try:
                message = messages.get(timeout=0.05)
            except queue.Empty:
                if finished.is_set():
                    return
                continue
            if type(message) is not tuple or not message or type(message[0]) is not str:
                state["failed"] = True
                _append_gateway_event(events_path, "cleanup_uncertain", pid)
                return
            kind = message[0]
            if kind == "ready":
                if len(message) < 2 or type(message[1]) is not int or message[1] != pid or state["ready_seen"]:
                    state["failed"] = True
                    _append_gateway_event(events_path, "cleanup_uncertain", pid)
                    return
                _append_gateway_event(events_path, "ready", pid)
                state["ready_seen"] = True
            elif kind == "closed":
                if (len(message) != 2 or type(message[1]) is not int or message[1] != pid
                        or state["closed_seen"] or state["failed"] or not state["ready_seen"]):
                    state["failed"] = True
                    _append_gateway_event(events_path, "cleanup_uncertain", pid)
                    return
                _append_gateway_event(events_path, "closed", pid)
                state["closed_seen"] = True
            elif kind in {"error", "cleanup_uncertain"}:
                state["failed"] = True
                reason = message[1] if len(message) == 2 and type(message[1]) is str else None
                _append_gateway_event(events_path, kind, pid, reason)
            elif kind == "health":
                if (len(message) != 2 or not state["ready_seen"] or state["closed_seen"]
                        or state["failed"]):
                    state["failed"] = True
                    _append_gateway_event(events_path, "cleanup_uncertain", pid)
                    return
                _append_gateway_event(events_path, "health", pid, message[1])
            else:
                state["failed"] = True
                _append_gateway_event(events_path, "cleanup_uncertain", pid)
                return
    except BaseException:
        # Even the exception text may contain synthetic credentials.
        state["failed"] = True


def _gateway_event_check(events_path: Path, gateway, *, closed: bool = False,
                         _health_timeout: float = 3) -> bool:
    """Challenge this fixture's reporter; not remote ACK or an atomic fence."""
    try:
        pid = gateway.pid
        if type(pid) is not int or pid <= 0:
            return False

        def valid(events, *, final):
            if not 1 <= len(events) <= 128:
                return False
            if any(type(event) is not dict or event.get("pid") != pid for event in events):
                return False
            kinds = [event.get("event") for event in events]
            if kinds[0] != "ready" or kinds.count("ready") != 1:
                return False
            if any(kind not in {"ready", "health", "closed"} for kind in kinds):
                return False
            if any(set(event) != ({"event", "pid", "nonce"} if event["event"] == "health"
                                  else {"event", "pid"}) for event in events):
                return False
            return (kinds.count("closed") == 1 and kinds[-1] == "closed") if final else "closed" not in kinds

        events = _events(events_path)
        if not valid(events, final=closed):
            return False
        if closed:
            exit_code = gateway.poll()
            return type(exit_code) is int and exit_code == 0
        if gateway.poll() is not None or gateway.stdin is None:
            return False
        nonce = secrets.token_hex(16)
        if not _write_gateway_control(gateway, ("health:" + nonce + "\n").encode("ascii")):
            return False
        deadline = time.monotonic() + _health_timeout
        while time.monotonic() < deadline:
            events = _events(events_path)
            if not valid(events, final=False) or gateway.poll() is not None:
                return False
            if sum(event.get("event") == "health" and event.get("nonce") == nonce for event in events) == 1:
                return True
            time.sleep(0.01)
        return False
    except BaseException:
        return False


def _write_gateway_control(gateway, payload: bytes) -> bool:
    """Use a bounded nonblocking write only on this fixture's Popen pipe."""
    descriptor = previous = None
    try:
        if gateway.stdin is None or type(payload) is not bytes:
            return False
        descriptor = gateway.stdin.fileno()
        previous = os.get_blocking(descriptor)
        os.set_blocking(descriptor, False)
        written = os.write(descriptor, payload)
        os.set_blocking(descriptor, previous)
        return written == len(payload)
    except BaseException:
        if descriptor is not None and previous is not None:
            with suppress(OSError):
                os.set_blocking(descriptor, previous)
        return False


def _gateway_child(config_path: str) -> None:
    """Run only this test's Gateway; stdin requests graceful shutdown."""
    try:
        data = json.loads(Path(config_path).read_text(encoding="utf-8"))
        keys = _SyntheticKeys({name: tuple(value.encode("ascii") for value in pair)
                               for name, pair in data["keys"].items()})
        publics = {keys["strategy"][0]: STRATEGY.principal_ref,
                   keys["risk_delegate"][0]: RISK.principal_ref,
                   keys["risk_control"][0]: CONTROL.principal_ref}
        messages, controls = queue.Queue(), queue.Queue()
        finished = threading.Event()
        state = {"ready_seen": False, "closed_seen": False, "failed": False}

        def thread_failure(_arguments):
            state["failed"] = True
            with suppress(BaseException):
                _append_gateway_event(Path(data["events"]), "cleanup_uncertain", os.getpid())

        threading.excepthook = thread_failure
        reporter = threading.Thread(
            target=_report_gateway_messages,
            args=(messages, Path(data["events"]), os.getpid(), finished, state),
            daemon=True,
        )
        reporter.start()

        def receive_stop():
            try:
                for line in sys.stdin:
                    command = line.strip()
                    if command == "stop":
                        controls.put("stop")
                        return
                    if command.startswith("health:") and re.fullmatch(r"[0-9a-f]{32}", command[7:]):
                        messages.put(("health", command[7:]))
                    else:
                        state["failed"] = True
                        _append_gateway_event(Path(data["events"]), "cleanup_uncertain", os.getpid())
                        return
                state["failed"] = True
                _append_gateway_event(Path(data["events"]), "cleanup_uncertain", os.getpid())
            except BaseException:
                state["failed"] = True
                with suppress(BaseException):
                    _append_gateway_event(Path(data["events"]), "cleanup_uncertain", os.getpid())

        threading.Thread(target=receive_stop, daemon=True).start()
        try:
            _gateway(data["root"], data["endpoint"], keys["gateway_server"], publics, messages, controls)
        except BaseException:
            state["failed"] = True
            with suppress(BaseException):
                _append_gateway_event(Path(data["events"]), "cleanup_uncertain", os.getpid())
        finally:
            finished.set()
        reporter.join(timeout=5)
        if not reporter.is_alive() and state["ready_seen"] and state["closed_seen"] and not state["failed"]:
            return
    except BaseException:
        # Do not print a traceback containing synthetic configuration or keys.
        print("SYNTHETIC_GATEWAY_FAILURE", flush=True)
    # Keep this test-owned process and its identity for follow-up; no implicit
    # close, retry, or signal. OS/process failure can still end it independently.
    print("SYNTHETIC_GATEWAY_CLEANUP_UNCERTAIN", flush=True)
    threading.Event().wait()


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
    config = _SyntheticConfig({"schema_version": 1, "origin": origin, "operator": {
        "principal_ref": OPERATOR.principal_ref, "actor_ref": OPERATOR.actor_ref,
        "login_token_sha256": hashlib.sha256(LOGIN.encode("ascii")).hexdigest(),
        "execution_domain_ref": DOMAIN, "account_ref": ACCOUNT, "scope_ref": "risk-scope",
        "service_id": RISK_HOST.actor_ref, "service_principal_ref": RISK_HOST.principal_ref,
        "instance_id": "risk-a", "endpoint": endpoint,
        "server_public_key": keys["risk_server"][0].decode("ascii"),
        "client_public_key": keys["operator"][0].decode("ascii"),
        "client_secret_key": keys["operator"][1].decode("ascii"),
        "operations": sorted(WEB_OPS),
    }})
    _private_write(path, json.dumps(config))


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

    def close(self, *, pre_close):
        if pre_close() is not True or not _web_client_state(self, "running"):
            raise RuntimeError("SYNTHETIC_GATEWAY_UNCERTAIN")
        self.server.should_exit = True
        self.thread.join(10)
        if self.thread.is_alive():
            raise RuntimeError("test_web_thread_cleanup_uncertain")
        if pre_close() is not True or not _web_client_state(self, "running"):
            raise RuntimeError("SYNTHETIC_GATEWAY_UNCERTAIN")
        self.host.close()
        if not _web_client_state(self, "closed"):
            raise RuntimeError("test_web_runtime_cleanup_uncertain")
        if pre_close() is not True or not _web_client_state(self, "closed"):
            raise RuntimeError("SYNTHETIC_GATEWAY_UNCERTAIN")
        self.sock.close()


def _listener():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(32)
    return sock


def _zero(value) -> bool:
    return type(value) is int and value == 0


def _direct_client_state(direct, expected: str) -> bool:
    try:
        return (direct._require_unregister_ack is True and type(direct.state) is str
                and direct.state == expected
                and (expected != "registered" or direct.healthy is True)
                and _zero(direct.cleanup_failures)
                and _zero(direct.unregister_uncertainties)
                and _zero(direct.unknown_registration_count))
    except BaseException:
        return False


def _web_client_state(web, expected: str) -> bool:
    try:
        runtime = web.host._runtime
        snapshot = runtime.status()
        return (type(snapshot) is RuntimeSnapshot and snapshot.state == expected
                and snapshot.thread_alive is (expected == "running")
                and snapshot.healthy is (expected == "running")
                and snapshot.error_code is None
                and runtime.strict_cleanup_required is True
                and snapshot.strict_unregister_ack is True
                and _zero(snapshot.cleanup_failures)
                and _zero(snapshot.unregister_uncertainties)
                and _zero(snapshot.owner_cleanup_uncertainties)
                and _zero(snapshot.unknown_registration_count))
    except BaseException:
        return False


def _close_direct_client(direct) -> bool:
    try:
        if not _direct_client_state(direct, "registered"):
            return False
        direct.close()
        return _direct_client_state(direct, "closed")
    except BaseException:
        return False


def _fixture_live(gateway, risk, events: Path, *, gateway_ready: bool, risk_ready: bool) -> bool:
    try:
        return (gateway_ready is True and _gateway_event_check(events, gateway)
                and risk is not None and risk_ready is True and risk.poll() is None)
    except BaseException:
        return False


def _fixture_env(root: Path) -> dict:
    """Keep test children on explicit source paths without inherited credentials."""
    sources = [SDK_ROOT, SDK_ROOT / "bt_api/bt_api_base/src", SDK_ROOT / "bt_api/bt_api_risk/src",
               SDK_ROOT / "bt_api/bt_api_gateway/src", SDK_ROOT / "bt_api/bt_api_execution/src",
               SDK_ROOT / "bt_api/bt_api_transport_zmq/src"]
    return {
        "HOME": str(root),
        "PATH": str(Path(sys.executable).parent) + os.pathsep + os.defpath,
        "PYTHONPATH": os.pathsep.join(map(str, sources)),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUTF8": "1",
    }


def _cleanup_owned_fixture(gateway, risk, direct, web, events: Path, *,
                           gateway_ready: bool, risk_ready: bool, client_uncertain: bool,
                           web_absent_proven: bool = False, pre_action=None):
    """Close only this fixture's confirmed resources; uncertain means stop.

    Each marker/process check is a snapshot; asynchronous changes after a
    check remain possible. No result here proves remote unregister ACK.
    """
    direct_closed = web_closed = risk_stopped = False

    def all_live():
        try:
            risk_code = risk.poll() if risk is not None else None
            return (risk is not None and direct is not None
                    and (web is not None or web_absent_proven is True)
                    and _direct_client_state(direct, "closed" if direct_closed else "registered")
                    and (web is None or _web_client_state(web, "closed" if web_closed else "running"))
                    and gateway_ready is True and risk_ready is True
                    and ((type(risk_code) is int and risk_code == 0) if risk_stopped else risk_code is None)
                    and _gateway_event_check(events, gateway)
                    and (pre_action is None or pre_action() is True))
        except BaseException:
            return False

    if client_uncertain is not False or not all_live():
        return False
    if web is not None:
        if not all_live():
            return False
        try:
            def web_pre_close():
                try:
                    return (risk is not None and direct is not None
                            and _direct_client_state(direct, "registered")
                            and _fixture_live(gateway, risk, events, gateway_ready=gateway_ready,
                                              risk_ready=risk_ready)
                            and (pre_action is None or pre_action() is True))
                except BaseException:
                    return False
            web.close(pre_close=web_pre_close)
            web_closed = True
        except BaseException:
            return False
    if direct is not None and (not all_live() or not _close_direct_client(direct)):
        return False
    direct_closed = True
    if risk is not None:
        if not all_live():
            return False
        try:
            risk.send_signal(signal.SIGTERM)
            risk_exit = risk.wait(timeout=30)
            if type(risk_exit) is not int or risk_exit != 0:
                return False
            risk_stopped = True
        except BaseException:
            return False
    if not all_live():
        return False
    try:
        if not _write_gateway_control(gateway, b"stop\n"):
            return False
        gateway_exit = gateway.wait(timeout=30)
        if type(gateway_exit) is not int or gateway_exit != 0:
            return False
    except BaseException:
        return False
    return _gateway_event_check(events, gateway, closed=True)


def _fixture_root(base: Path | None = None) -> Path:
    """Create a fresh private identity outside pytest's managed temporary tree."""
    if os.name != "posix" or not hasattr(os, "getuid"):
        raise RuntimeError("SYNTHETIC_PRIVATE_ROOT_UNSUPPORTED")
    if base is None:
        base = Path.home() / ".cache" / "bt-api-py-iter64-test-identities"
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = base.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o077):
        raise RuntimeError("SYNTHETIC_PRIVATE_ROOT_UNSAFE")
    return Path(tempfile.mkdtemp(prefix="web-", dir=base))


def _record_fixture_outcome(root: Path, complete: bool, gateway, risk, direct, web, listener, keys) -> bool:
    try:
        _private_write(root / "fixture-identity.json", json.dumps({
            "gateway_pid": gateway.pid, "risk_pid": None if risk is None else risk.pid,
            "state": "closed" if complete else "cleanup_unknown",
        }))
    except BaseException:
        complete = False
    if not complete:
        # Keep object references for this Python process; an external OS or
        # process exit can still independently release resources.
        _RETAINED_FIXTURES[str(root)] = _RetainedFixture(gateway, risk, direct, web, listener, keys)
    return complete


def exercise_web_admin_scenario(tmp_path, *, check_available):
    # This private identity directory is never automatically reused or removed,
    # including after successful cleanup. Users/OS may still remove it later.
    del tmp_path
    root = _fixture_root()
    print("SYNTHETIC_FIXTURE_ROOT " + str(root), flush=True)
    try:
        return _exercise_web_admin_scenario(root, check_available=check_available)
    except KeyboardInterrupt:
        raise KeyboardInterrupt() from None
    except SystemExit:
        raise SystemExit(1) from None
    except BaseException:
        # Suppress exception text, object reprs and traceback frames containing
        # synthetic credentials. The original fixed stage is printed separately.
        raise pytest.fail.Exception("SYNTHETIC_WEB_SCENARIO_FAILED", pytrace=False) from None


def _exercise_web_admin_scenario(tmp_path, *, check_available):
    keys = _SyntheticKeys({name: zmq.curve_keypair() for name in (
        "gateway_server", "strategy", "risk_delegate", "risk_control", "risk_server",
        "operator", "independent")})
    gateway_root, risk_root = tmp_path / "gateway", tmp_path / "risk"
    gateway_root.mkdir()
    risk_root.mkdir()
    gateway_endpoint, risk_endpoint = _free_endpoint(), _free_endpoint()
    events = tmp_path / "gateway-events.jsonl"
    gateway_config = tmp_path / "gateway-test.json"
    _private_write(gateway_config, json.dumps({
        "root": str(gateway_root), "endpoint": gateway_endpoint, "events": str(events),
        "keys": {name: [item.decode("ascii") for item in pair] for name, pair in keys.items()},
    }))
    env = _fixture_env(tmp_path)
    gateway = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--synthetic-gateway", str(gateway_config)],
        cwd=str(SDK_ROOT), env=env, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    risk = direct = web = listener = None
    web_uncertain = False
    web_absent_proven = False
    gateway_ready_verified = risk_ready_verified = False
    stage = "GATEWAY_READY"
    try:
        _private_write(tmp_path / "fixture-identity.json", json.dumps({
            "gateway_pid": gateway.pid, "risk_pid": None, "state": "initializing",
        }))
        ready = _wait_for(lambda: next((e for e in _events(events)
                                       if e["event"] in {"ready", "error", "cleanup_uncertain"}), None))
        assert ready == {"event": "ready", "pid": gateway.pid}
        gateway_ready_verified = _gateway_event_check(events, gateway)
        assert gateway_ready_verified
        stage = "RISK_START"
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
        web_uncertain = True
        risk = subprocess.Popen([
            sys.executable, "-m", "bt_api_py.risk_service_host", "run", "--config", str(config_path),
            "--ready-file", str(ready_file),
        ], cwd=str(SDK_ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _private_write(tmp_path / "fixture-identity.json", json.dumps({
            "gateway_pid": gateway.pid, "risk_pid": risk.pid, "state": "initializing",
        }))

        def risk_ready():
            if risk.poll() is not None:
                raise AssertionError("synthetic_risk_exited_before_ready")
            return ready_file.exists()

        _wait_for(risk_ready, seconds=60)
        assert json.loads(ready_file.read_text(encoding="utf-8"))["pid"] == risk.pid
        risk_ready_verified = True
        web_uncertain = False
        stage = "DIRECT_CLIENT_START"
        web_uncertain = True
        direct = _direct_admin(risk_endpoint, keys)
        direct.start()
        assert direct.healthy
        web_uncertain = False
        before = _admin(direct, "risk.admin.status")
        assert before["status"] == "OBSERVED"
        initial_policy = _admin(direct, "risk.policy.read")
        assert initial_policy["version"] == 1
        assert initial_policy["document"]["self_trade"]["enabled"] is True
        before_available = before["summary"]["available"]
        assert type(before_available) is bool
        try:
            check_available(before_available)
        except BaseException:
            print("SYNTHETIC_AVAILABLE_CHECK_FAILED " + str(before_available).lower(), flush=True)
            raise
        stage = "DIRECT_ACL"
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

        web_uncertain = True
        listener = _listener()
        origin = "http://127.0.0.1:" + str(listener.getsockname()[1])
        web_path = tmp_path / "web.json"
        _web_config(web_path, origin, risk_endpoint, keys)
        web = _HttpWeb(web_path, listener)
        stage = "WEB_FIRST_START"
        try:
            web.start()
        except BaseException:
            web_uncertain = True
            raise
        web_uncertain = False
        stage = "WEB_FIRST_OPERATIONS"
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
            audit = _admin(direct, "risk.audit.read", {"after": 0, "limit": 100,
                                                       "through": None, "snapshot": None})
            assert audit["status"] == "OBSERVED"
            assert any(row["kind"] == "policy_publish" and row["policy_version"] == 2
                       and row["detail"]["operator"] == OPERATOR.principal_ref
                       for row in audit["records"])
        if not _fixture_live(gateway, risk, events, gateway_ready=gateway_ready_verified,
                             risk_ready=risk_ready_verified):
            web_uncertain = True
            raise RuntimeError("SYNTHETIC_GATEWAY_UNCERTAIN")
        stage = "WEB_FIRST_CLOSE"
        try:
            web.close(pre_close=lambda: _direct_client_state(direct, "registered") and _fixture_live(
                gateway, risk, events, gateway_ready=gateway_ready_verified,
                risk_ready=risk_ready_verified,
            ))
        except BaseException:
            web_uncertain = True
            raise
        web = None
        web_absent_proven = True
        assert risk.poll() is None and _admin(direct, "risk.policy.read")["version"] == 2

        web_uncertain = True
        web_absent_proven = False
        listener = _listener()
        origin = "http://127.0.0.1:" + str(listener.getsockname()[1])
        _web_config(web_path, origin, risk_endpoint, keys)
        web = _HttpWeb(web_path, listener)
        stage = "WEB_SECOND_START"
        try:
            web.start()
        except BaseException:
            web_uncertain = True
            raise
        web_uncertain = False
        stage = "WEB_SECOND_OPERATIONS"
        with httpx.Client(base_url=origin, timeout=10, trust_env=False) as http:
            login = http.post("/login", data={"token": LOGIN}, headers={"Origin": origin})
            assert login.status_code == 200
            headers = {"X-Risk-Session": login.json()["session"]}
            assert http.get("/api/v1/risk/policy", headers=headers).json()["version"] == 2
            web_available = http.get("/api/v1/risk/status", headers=headers).json()["summary"]["available"]
            assert type(web_available) is bool and web_available is before_available
            check_available(web_available)
        if not _fixture_live(gateway, risk, events, gateway_ready=gateway_ready_verified,
                             risk_ready=risk_ready_verified):
            web_uncertain = True
            raise RuntimeError("SYNTHETIC_GATEWAY_UNCERTAIN")
        stage = "WEB_SECOND_CLOSE"
        try:
            web.close(pre_close=lambda: _direct_client_state(direct, "registered") and _fixture_live(
                gateway, risk, events, gateway_ready=gateway_ready_verified,
                risk_ready=risk_ready_verified,
            ))
        except BaseException:
            web_uncertain = True
            raise
        web = None
        web_absent_proven = True
        assert risk.poll() is None
        final_available = _admin(direct, "risk.admin.status")["summary"]["available"]
        assert type(final_available) is bool and final_available is before_available
        check_available(final_available)
    except BaseException:
        print("SYNTHETIC_SCENARIO_FAILED_STAGE " + stage, flush=True)
        raise
    finally:
        complete = _cleanup_owned_fixture(
            gateway, risk, direct, web, events,
            gateway_ready=gateway_ready_verified, risk_ready=risk_ready_verified,
            client_uncertain=web_uncertain, web_absent_proven=web_absent_proven,
        )
        complete = _record_fixture_outcome(tmp_path, complete, gateway, risk, direct, web, listener, keys)
        print(f"TEST_CLEANUP {'complete' if complete else 'cleanup_unknown'} "
              f"risk_pid={None if risk is None else risk.pid} gateway_pid={gateway.pid}", flush=True)
        if not complete:
            raise RuntimeError("SYNTHETIC_HOST_CLEANUP_UNCERTAIN") from None


if __name__ == "__main__" and len(sys.argv) == 3 and sys.argv[1] == "--synthetic-gateway":
    _gateway_child(sys.argv[2])
