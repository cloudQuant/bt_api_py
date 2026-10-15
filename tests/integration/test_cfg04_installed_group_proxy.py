"""Installed SDK-only client reaches the CFG04 order-group proxy.

Run this node in the same fresh installed-client environment as the pinned
CFG02 market and CFG04 five-mount nodes. The group service is a separate
installed-domain process with three real CURVE ServiceHosts behind its
coordinator; only provider leaves are synthetic.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py.service_config import parse_client_config
from bt_api_py.service_runtime import ClientCurveKeys, ServiceClientRuntime, ZmqTransportFactory

_SDK_SOURCE_COMMIT = "9d1dc457af0e0e4e94895ca215a27137e86fa20f"
_TEST_SUPPORT_BASE_COMMIT = "213f1114f69416bbad250f1a226e5fa1ccc2638e"
_DOMAIN_PACKAGES = (
    "bt_api_gateway",
    "bt_api_execution",
    "bt_api_risk",
    "bt_api_monitor",
)
_CLIENT_DISTRIBUTIONS = {
    "bt_api_py": "0.15.5",
    "bt_api_base": "0.15.8",
    "bt_api_transport_zmq": "0.1.0",
}
_SERVER_DISTRIBUTIONS = {
    **_CLIENT_DISTRIBUTIONS,
    "bt_api_gateway": "0.1.0",
    "bt_api_execution": "0.2.0",
    "bt_api_risk": "0.1.0",
    "bt_api_monitor": "0.1.0",
}
_SUPPORT_FILES = frozenset(
    {
        "tests/__init__.py",
        "tests/integration/cfg04_group_server.py",
        "tests/integration/test_cfg04_installed_group_proxy.py",
        "tests/integration/test_service_execution_group_local_process.py",
        "tests/unit/_service_group_provider_fixtures.py",
        "tests/unit/test_iteration10_optional_install_matrix.py",
    }
)
_SUPPORT_OVERLAYS = frozenset(
    {
        "tests/integration/cfg04_group_server.py",
        "tests/integration/test_cfg04_installed_group_proxy.py",
    }
)
_GROUP_SECURITY_REF = "strategy-client-security-v1"
_GROUP_SERVICE_ID = "group-coordinator-service"
_GROUP_SERVICE_PRINCIPAL = "group-coordinator-principal"
_GROUP_INSTANCE = "group-coordinator"
_GROUP_SCOPE_REF = "group-strategy-route-v1"
_GROUP_DOMAIN = "paper:group-process"
_GROUP_STRATEGY = "strategy-group-process"
_GROUP_PRINCIPAL = "group-strategy-principal"
_GROUP_PORTFOLIO = "portfolio-group-process"
_GROUP_RUN = "run-group-process-001"
_GROUP_ACCOUNTS = ("account-a", "account-b")
_GROUP_ONE = "group-paired-process-01"
_GROUP_TWO = "group-unknown-process-02"
_GROUP_OPERATIONS = frozenset({"groups.submit", "groups.query"})


def _encode_key(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _encode_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _endpoint() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return f"tcp://127.0.0.1:{probe.getsockname()[1]}"


def _distribution_versions(names: tuple[str, ...]) -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for name in names:
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result[name] = None
    return result


def _domain_presence() -> dict[str, dict[str, bool]]:
    versions = _distribution_versions(_DOMAIN_PACKAGES)
    return {
        package: {
            "distribution_installed": versions[package] is not None,
            "import_spec_available": importlib.util.find_spec(package) is not None,
            "module_loaded": any(
                name == package or name.startswith(package + ".") for name in sys.modules
            ),
        }
        for package in _DOMAIN_PACKAGES
    }


def _assert_client_installation() -> dict[str, str | None]:
    versions = _distribution_versions((*_CLIENT_DISTRIBUTIONS, *_DOMAIN_PACKAGES))
    for name, expected in _CLIENT_DISTRIBUTIONS.items():
        assert versions[name] == expected, (name, versions[name], expected)
    assert all(versions[name] is None for name in _DOMAIN_PACKAGES), versions
    assert all(
        row
        == {
            "distribution_installed": False,
            "import_spec_available": False,
            "module_loaded": False,
        }
        for row in _domain_presence().values()
    )
    return versions


def _assert_installed_origin(module_name: str) -> str:
    spec = importlib.util.find_spec(module_name)
    assert spec is not None and spec.origin
    origin = Path(spec.origin).resolve()
    site_packages = Path(sys.prefix).resolve() / "Lib" / "site-packages"
    assert origin.is_relative_to(site_packages), (module_name, origin, site_packages)
    return str(origin)


def _cold_import_probe(cwd: Path) -> dict[str, Any]:
    source = r"""
import importlib.abc
import importlib.metadata
import importlib.util
import json
import sys

domains = ("bt_api_gateway", "bt_api_execution", "bt_api_risk", "bt_api_monitor")
attempted = []

class Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        del path, target
        if any(fullname == item or fullname.startswith(item + ".") for item in domains):
            attempted.append(fullname)
            raise AssertionError("strategy-side domain import blocked")
        return None

sys.meta_path.insert(0, Blocker())
from bt_api_py.service_account import ServiceAccountClient
from bt_api_py.service_config import parse_client_config
from bt_api_py.service_execution import ServiceExecutionClient
from bt_api_py.service_market import ServiceMarketClient
from bt_api_py.service_runtime import ServiceClientRuntime
from bt_api_py.service_telemetry import ServiceTelemetryEmitter
from bt_api_base.service_protocol import WireScope

loaded = sorted(
    name for name in sys.modules
    if any(name == item or name.startswith(item + ".") for item in domains)
)
versions = {}
for item in domains:
    try:
        versions[item] = importlib.metadata.version(item)
    except importlib.metadata.PackageNotFoundError:
        versions[item] = None
assert attempted == [], attempted
assert loaded == [], loaded
assert all(value is None for value in versions.values()), versions
print(json.dumps({
    "attempted": attempted,
    "loaded": loaded,
    "domain_versions": versions,
    "client_symbols": sorted([
        ServiceAccountClient.__name__,
        ServiceClientRuntime.__name__,
        ServiceExecutionClient.__name__,
        ServiceMarketClient.__name__,
        ServiceTelemetryEmitter.__name__,
        parse_client_config.__name__,
        WireScope.__name__,
    ]),
}, sort_keys=True, separators=(",", ":")))
"""
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    result = subprocess.run(
        [sys.executable, "-c", source],
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="backslashreplace",
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }
    return json.loads(result.stdout)


def _support_payload_map(support_root: Path) -> dict[str, dict[str, Any]]:
    manifest_path = support_root / "cfg04_group_support_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert set(manifest) == {
        "sdk_wheel_source_commit",
        "test_support_base_commit",
        "candidate_overlays",
        "files",
    }
    assert manifest["sdk_wheel_source_commit"] == _SDK_SOURCE_COMMIT
    assert manifest["test_support_base_commit"] == _TEST_SUPPORT_BASE_COMMIT
    assert set(manifest["candidate_overlays"]) == _SUPPORT_OVERLAYS
    artifact_root = Path(sys.prefix).resolve().parent
    assert support_root.resolve().is_relative_to(artifact_root)
    files = manifest["files"]
    assert isinstance(files, dict) and set(files) == _SUPPORT_FILES
    payloads: dict[str, dict[str, Any]] = {}
    for relative, recorded in files.items():
        assert set(recorded) == {"bytes", "sha256"}
        path = (support_root / relative).resolve(strict=True)
        assert path.is_relative_to(support_root.resolve())
        payload = path.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        assert len(payload) == recorded["bytes"], relative
        assert digest == recorded["sha256"], relative
        payloads[relative] = {"bytes": len(payload), "sha256": digest}
    return payloads


def _plain_runtime_json(value: object) -> object:
    """Thaw only authenticated immutable JSON containers for DTO assertions."""

    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise TypeError("runtime JSON object keys must be strings")
        return {key: _plain_runtime_json(item) for key, item in value.items()}
    if type(value) is tuple:
        return [_plain_runtime_json(item) for item in value]
    return value


def _assert_group_reply(reply: Mapping[str, Any], group_id: str) -> dict[str, Any]:
    assert reply["ok"] is True, reply
    assert reply["code"] == "OK", reply
    result = _plain_runtime_json(reply["result"])
    assert isinstance(result, dict) and result["found"] is True, result
    snapshot = result["snapshot"]
    assert snapshot["request"]["group_id"] == group_id
    return snapshot


class _LineServer:
    def __init__(self, python: Path, script: Path, stderr_path: Path, cwd: Path) -> None:
        self.python = python
        self.script = script
        self.stderr_path = stderr_path
        self.cwd = cwd
        self.process: subprocess.Popen[str] | None = None
        self.reader: threading.Thread | None = None
        self.stderr_file = None
        self.output: queue.Queue[object] = queue.Queue()
        self.close_requested = False
        self.closed_normally = False

    def start(self, payload: Mapping[str, object]) -> dict[str, Any]:
        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)
        environment.pop("PYTHONHOME", None)
        self.stderr_file = self.stderr_path.open("w", encoding="utf-8", errors="backslashreplace")
        self.process = subprocess.Popen(
            [str(self.python), str(self.script)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.stderr_file,
            text=True,
            encoding="utf-8",
            errors="backslashreplace",
            bufsize=1,
            cwd=self.cwd,
            env=environment,
        )
        assert self.process.stdin is not None and self.process.stdout is not None
        self.reader = threading.Thread(
            target=self._read_output,
            name=f"cfg04-group-output-{self.process.pid}",
            daemon=True,
        )
        self.reader.start()
        self._send(payload)
        return self._receive()

    def _read_output(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        for line in self.process.stdout:
            try:
                self.output.put(json.loads(line))
            except (TypeError, ValueError):
                self.output.put({"kind": "invalid_server_output"})

    def _send(self, value: Mapping[str, object]) -> None:
        if self.process is None or self.process.poll() is not None:
            raise RuntimeError("group server is not running")
        assert self.process.stdin is not None
        self.process.stdin.write(_encode_json(dict(value)) + "\n")
        self.process.stdin.flush()

    def _receive(self, timeout: float = 30.0) -> dict[str, Any]:
        try:
            value = self.output.get(timeout=timeout)
        except queue.Empty as error:
            raise TimeoutError("group server did not answer within its bound") from error
        if not isinstance(value, dict):
            raise AssertionError("group server control record is not an object")
        if value.get("kind") == "server_error":
            raise AssertionError(
                "group server failed: "
                f"{value.get('error_code') or value.get('error_type')}; "
                f"cleanup={value.get('cleanup_failures', [])}"
            )
        if (
            self.process is not None
            and self.process.poll() is not None
            and value.get("kind") != "closed"
        ):
            raise AssertionError("group server exited before its terminal close reply")
        return value

    def command(self, value: Mapping[str, object], *, timeout: float = 30.0) -> dict[str, Any]:
        self._send(value)
        return self._receive(timeout=timeout)

    def close_normally(self, *, timeout: float = 30.0) -> dict[str, Any]:
        self.close_requested = True
        result = self.command({"action": "close"}, timeout=timeout)
        assert result.get("kind") == "closed", result
        assert self.process is not None and self.process.stdin is not None
        self.process.stdin.close()
        self.process.wait(timeout=30)
        assert self.process.returncode == 0
        assert self.reader is not None
        self.reader.join(timeout=5)
        assert not self.reader.is_alive()
        assert self.process.stdout is not None
        self.process.stdout.close()
        assert self.stderr_file is not None
        self.stderr_file.close()
        self.closed_normally = True
        return result

    def finish_after_failure(self) -> list[str]:
        failures: list[str] = []
        if self.process is None:
            if self.stderr_file is not None and not self.stderr_file.closed:
                self.stderr_file.close()
            return failures
        if self.process.poll() is None and not self.close_requested:
            try:
                self.close_normally(timeout=60.0)
            except BaseException as error:
                failures.append(f"normal_close:{type(error).__name__}")
        if self.process.poll() is None:
            try:
                self.process.wait(timeout=60)
            except subprocess.TimeoutExpired:
                failures.append("server_required_terminate")
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    failures.append("server_required_kill")
                    self.process.kill()
                    self.process.wait(timeout=5)
        if self.process.returncode != 0:
            failures.append("server_exit_nonzero")
        if self.reader is not None:
            self.reader.join(timeout=3)
            if self.reader.is_alive():
                failures.append("server_output_reader_alive")
        if self.process.stdout is not None and not self.process.stdout.closed:
            self.process.stdout.close()
        if self.process.stdin is not None and not self.process.stdin.closed:
            self.process.stdin.close()
        if self.stderr_file is not None and not self.stderr_file.closed:
            self.stderr_file.close()
        return failures


def _runtime(endpoint: str, keys: ClientCurveKeys) -> ServiceClientRuntime:
    return ServiceClientRuntime(
        parse_client_config(
            {
                "client_config_schema_version": 1,
                "process_role": "strategy_client",
                "registration": {"strategy_ref": _GROUP_STRATEGY},
                "clients": {
                    "execution": {
                        "config_schema_version": 1,
                        "settings": {
                            "bindings": [
                                {
                                    "mount": "broker.order_groups",
                                    "service_id": _GROUP_SERVICE_ID,
                                    "instance_id": _GROUP_INSTANCE,
                                    "scope_ref": _GROUP_SCOPE_REF,
                                    "portfolio_ref": _GROUP_PORTFOLIO,
                                    "transport_ref": _GROUP_SECURITY_REF,
                                }
                            ],
                            "options": {
                                "request_timeout_ms": 5_000,
                                "heartbeat_interval_seconds": 5,
                            },
                        },
                    }
                },
                "communication": {
                    "config_schema_version": 1,
                    "profiles": {
                        _GROUP_SECURITY_REF: {
                            "backend": "zmq",
                            "config_schema_version": 1,
                            "endpoints": {"rpc_connect": endpoint},
                            "security_profile_ref": _GROUP_SECURITY_REF,
                            "options": {
                                "request_timeout_ms": 5_000,
                                "high_water_mark": 32,
                                "max_message_bytes": 65_536,
                            },
                        }
                    },
                },
            }
        ),
        identity=WireIdentity(WireRole.STRATEGY_CLIENT, _GROUP_PRINCIPAL, _GROUP_STRATEGY),
        scope_resolver=lambda _ref: WireScope(
            _GROUP_DOMAIN,
            account_refs=_GROUP_ACCOUNTS,
            strategy_ref=_GROUP_STRATEGY,
            portfolio_ref=_GROUP_PORTFOLIO,
        ),
        peer_resolver=lambda _service_id: WireIdentity(
            WireRole.SERVICE, _GROUP_SERVICE_PRINCIPAL, _GROUP_SERVICE_ID
        ),
        transport_factory=ZmqTransportFactory({_GROUP_SECURITY_REF: keys}),
        run_id=_GROUP_RUN,
        requested_operations={"broker.order_groups": _GROUP_OPERATIONS},
    )


@pytest.mark.integration
def test_isolated_client_uses_order_group_proxy_with_installed_server(tmp_path: Path) -> None:
    """Submit two real group legs and retain an UNKNOWN risk hold across query."""

    assert sys.prefix != sys.base_prefix, "CFG04 client must use its fresh isolated venv"
    client_distributions = _assert_client_installation()
    client_origins = {
        name: _assert_installed_origin(name)
        for name in (
            "bt_api_base.service_protocol",
            "bt_api_py.service_config",
            "bt_api_py.service_runtime",
            "bt_api_transport_zmq.client",
        )
    }
    cold_import = _cold_import_probe(tmp_path)
    assert cold_import["attempted"] == []
    assert cold_import["loaded"] == []
    assert all(value is None for value in cold_import["domain_versions"].values())

    server_python_raw = os.environ.get("BT_API_ITERATION10_CFG04_SERVER_PYTHON")
    assert server_python_raw, "Root must provide the separate seven-package server venv"
    server_python = Path(server_python_raw).resolve()
    assert server_python.is_file()
    support_root = Path(__file__).resolve().parents[2]
    support_payloads = _support_payload_map(support_root)
    server_script = support_root / "tests" / "integration" / "cfg04_group_server.py"
    endpoint = _endpoint()
    account_endpoints = {account: _endpoint() for account in _GROUP_ACCOUNTS}
    account_server_keys = {account: zmq.curve_keypair() for account in _GROUP_ACCOUNTS}
    coordinator_server_public, coordinator_server_secret = zmq.curve_keypair()
    coordinator_client_public, coordinator_client_secret = zmq.curve_keypair()
    strategy_public, strategy_secret = zmq.curve_keypair()
    server = _LineServer(
        server_python,
        server_script,
        tmp_path / "cfg04-group-server-stderr.txt",
        tmp_path,
    )
    runtime: ServiceClientRuntime | None = None
    primary_error: BaseException | None = None
    cleanup_failures: list[str] = []
    normal_server_close: dict[str, Any] | None = None
    try:
        ready = server.start(
            {
                "root": str(tmp_path / "group-server-data"),
                "test_support_root": str(support_root),
                "support_payloads": support_payloads,
                "account_endpoints": account_endpoints,
                "coordinator_endpoint": endpoint,
                "account_server_keys": {
                    account: {
                        "public": _encode_key(keys[0]),
                        "secret": _encode_key(keys[1]),
                    }
                    for account, keys in account_server_keys.items()
                },
                "coordinator_server": {
                    "public": _encode_key(coordinator_server_public),
                    "secret": _encode_key(coordinator_server_secret),
                },
                "coordinator_client": {
                    "public": _encode_key(coordinator_client_public),
                    "secret": _encode_key(coordinator_client_secret),
                },
                "strategy_client": {"public": _encode_key(strategy_public)},
                "metadata_reference_ns": time.time_ns(),
            }
        )
        assert ready["kind"] == "ready", ready
        server_prefix = Path(ready["prefix"]).resolve()
        server_base_prefix = Path(ready["base_prefix"]).resolve()
        assert server_prefix != Path(sys.prefix).resolve()
        assert server_prefix != server_base_prefix
        assert Path(ready["working_directory"]).resolve() == tmp_path.resolve()
        allowed_server_path_roots = (
            server_prefix,
            server_base_prefix,
            support_root.resolve(),
            tmp_path.resolve(),
        )
        assert all(
            any(
                Path(item).resolve() == allowed_root
                or Path(item).resolve().is_relative_to(allowed_root)
                for allowed_root in allowed_server_path_roots
            )
            for item in ready["sys_path"]
        ), ready["sys_path"]
        assert ready["source_support"] == support_payloads
        assert ready["service_id"] == _GROUP_SERVICE_ID
        assert ready["service_principal"] == _GROUP_SERVICE_PRINCIPAL
        assert ready["instance_id"] == _GROUP_INSTANCE
        assert ready["scope_ref"] == _GROUP_SCOPE_REF
        assert ready["domain"] == _GROUP_DOMAIN
        assert ready["strategy_ref"] == _GROUP_STRATEGY
        assert ready["strategy_principal"] == _GROUP_PRINCIPAL
        assert ready["portfolio_ref"] == _GROUP_PORTFOLIO
        assert ready["run_id"] == _GROUP_RUN
        assert tuple(ready["accounts"]) == _GROUP_ACCOUNTS
        assert ready["pid"] != os.getpid()
        all_host_pids = {
            ready["pid"],
            ready["coordinator_host_pid"],
            *ready["account_host_pids"].values(),
        }
        assert len(all_host_pids) == 4
        assert all(type(pid) is int and pid > 0 for pid in all_host_pids)
        assert all(pid != os.getpid() for pid in all_host_pids)
        assert all(
            pid != ready["pid"]
            for pid in (
                ready["coordinator_host_pid"],
                *ready["account_host_pids"].values(),
            )
        )
        assert ready["distributions"] == _SERVER_DISTRIBUTIONS
        assert all(
            row
            == {
                "distribution_installed": True,
                "import_spec_available": True,
                "module_loaded": True,
            }
            for row in ready["domain_presence"].values()
        )
        server_site = Path(ready["prefix"]).resolve() / "Lib" / "site-packages"
        assert all(
            Path(origin).resolve().is_relative_to(server_site)
            for origin in ready["origins"].values()
        ), ready["origins"]
        assert Path(ready["support_origin"]).resolve().is_relative_to(support_root.resolve())
        assert not ready["direct_url_editable"]

        account_routes = ready["upstream_bindings"]
        assert len(account_routes) == 6
        expected_routes = (
            {
                (
                    "upstream.orders",
                    account,
                    f"order-entry-route-{account}",
                    ("orders.resolve_group_leg", "orders.submit_group_leg"),
                )
                for account in _GROUP_ACCOUNTS
            }
            | {
                (
                    "upstream.order_query",
                    account,
                    f"order-query-route-{account}",
                    ("orders.query_group_leg",),
                )
                for account in _GROUP_ACCOUNTS
            }
            | {
                (
                    "upstream.account_reservations",
                    account,
                    f"reservation-route-{account}",
                    ("reservations.query", "reservations.release", "reservations.reserve"),
                )
                for account in _GROUP_ACCOUNTS
            }
        )
        actual_routes = {
            (
                item["mount"],
                item["account_ref"],
                item["scope_ref"],
                tuple(item["operations"]),
            )
            for item in account_routes
        }
        assert actual_routes == expected_routes, actual_routes

        runtime = _runtime(
            endpoint,
            ClientCurveKeys(
                server_key=coordinator_server_public,
                public_key=strategy_public,
                secret_key=strategy_secret,
            ),
        )
        registration = runtime.start()
        assert registration.degraded_count == 0
        assert len(registration.bindings) == 1
        binding = registration.bindings[0]
        assert binding.mount == "broker.order_groups"
        assert binding.scope.account_refs == _GROUP_ACCOUNTS
        assert binding.scope.strategy_ref == _GROUP_STRATEGY
        assert binding.scope.portfolio_ref == _GROUP_PORTFOLIO
        assert binding.scope.run_id == _GROUP_RUN
        assert binding.operations == _GROUP_OPERATIONS

        first_request_record = server.command({"action": "request", "group_id": _GROUP_ONE})
        assert first_request_record["kind"] == "request"
        first_request = first_request_record["request"]
        assert first_request["group_id"] == _GROUP_ONE
        first_reply = runtime.call(
            "broker.order_groups",
            "groups.submit",
            first_request,
            portfolio_ref=_GROUP_PORTFOLIO,
        )
        paired = _assert_group_reply(first_reply, _GROUP_ONE)
        paired_query = runtime.call(
            "broker.order_groups",
            "groups.query",
            {"group_id": _GROUP_ONE},
            portfolio_ref=_GROUP_PORTFOLIO,
        )
        assert _assert_group_reply(paired_query, _GROUP_ONE) == paired
        assert paired["state"] == "PAIRED"
        assert paired["error_code"] is None
        assert len(paired["reservations"]) == len(paired["legs"]) == 2
        assert all(item["child_status"]["state"] == "FILLED" for item in paired["legs"])
        assert all(item["claim_receipt"] is not None for item in paired["legs"])
        assert all(
            item["resolution_receipt"]["resolution_kind"] == "TERMINAL_EXPOSURE_RESOLVED"
            and item["resolution_receipt"]["trade_count"] == 1
            and item["resolution_receipt"]["dispatch_latch_active"] is False
            for item in paired["legs"]
        )

        assert server.command({"action": "arm_unknown"}) == {
            "kind": "armed",
            "account_ref": "account-b",
        }
        second_request_record = server.command({"action": "request", "group_id": _GROUP_TWO})
        assert second_request_record["kind"] == "request"
        second_request = second_request_record["request"]
        assert second_request["group_id"] == _GROUP_TWO
        unknown_reply = runtime.call(
            "broker.order_groups",
            "groups.submit",
            second_request,
            portfolio_ref=_GROUP_PORTFOLIO,
        )
        unknown = _assert_group_reply(unknown_reply, _GROUP_TWO)
        assert unknown["state"] == "RECOVERY_REQUIRED"
        leg_a = next(item for item in unknown["legs"] if item["leg_id"] == "leg-a")
        leg_b = next(item for item in unknown["legs"] if item["leg_id"] == "leg-b")
        assert leg_a["child_status"]["state"] == "FILLED"
        assert leg_a["resolution_receipt"] is not None
        assert leg_b["child_status"]["state"] == "UNKNOWN"
        assert leg_b["phase"] == "UNKNOWN"
        assert leg_b["resolution_receipt"] is None
        unknown_query = runtime.call(
            "broker.order_groups",
            "groups.query",
            {"group_id": _GROUP_TWO},
            portfolio_ref=_GROUP_PORTFOLIO,
        )
        assert _assert_group_reply(unknown_query, _GROUP_TWO) == unknown

        runtime.close()
        assert runtime.state == "closed"
        assert runtime.cleanup_failures == 0
        assert runtime.unregister_uncertainties == 0
        status = server.command({"action": "status"})
        assert status["kind"] == "status"
        assert status["accounts"]["account-b"]["pending_holds"]
        assert status["accounts"]["account-a"]["pending_holds"] == []

        normal_server_close = server.close_normally()
        assert len(normal_server_close["normal_closes"]) == 3
        assert all(
            item["normal_close"] and item["exitcode"] == 0
            for item in normal_server_close["normal_closes"]
        )
        durable = normal_server_close["durable"]
        assert durable["paired"]["state"] == "PAIRED"
        assert durable["unknown"]["state"] == "RECOVERY_REQUIRED"
        assert "GROUP_PAIRED" in durable["paired_event_types"]
        assert "GROUP_RECOVERY_REQUIRED" in durable["unknown_event_types"]
        account_a = durable["accounts"]["account-a"]
        account_b = durable["accounts"]["account-b"]
        assert all(item["claimed"] and item["resolved"] for item in account_a["paired_risk_legs"])
        assert all(item["claimed"] and item["resolved"] for item in account_b["paired_risk_legs"])
        assert account_a["authority_pending_hold_intents"] == []
        assert len(account_b["authority_pending_hold_intents"]) == 1
        assert account_b["authority_pending_hold_intents"] == [
            f"group:{_GROUP_TWO}:leg:leg-b"
        ]
        assert account_b["unknown_risk_legs"] == [
            {"leg_id": "leg-b", "claimed": True, "resolved": False}
        ]
        assert account_a["execution"]["unknown"] == "FILLED"
        assert account_b["execution"]["unknown"] == "UNKNOWN"
        assert account_a["execution"]["paired_dispatch_attempts"] == 1
        assert account_b["execution"]["paired_dispatch_attempts"] == 1
        assert len(account_a["provider_writes"]) == len(account_b["provider_writes"]) == 2
        assert (
            account_a["provider_counters"]
            == account_b["provider_counters"]
            == {
                "order_queries": 2,
                "fill_queries": 2,
            }
        )
        assert _domain_presence() == {
            package: {
                "distribution_installed": False,
                "import_spec_available": False,
                "module_loaded": False,
            }
            for package in _DOMAIN_PACKAGES
        }
    except BaseException as error:
        primary_error = error
    finally:
        if runtime is not None and runtime.state != "closed":
            try:
                runtime.close()
            except BaseException as error:
                cleanup_failures.append(f"client_runtime_close:{type(error).__name__}")
        cleanup_failures.extend(server.finish_after_failure())

    if primary_error is not None:
        if cleanup_failures:
            raise AssertionError(f"cleanup failures: {cleanup_failures}") from primary_error
        raise primary_error
    assert cleanup_failures == [], cleanup_failures
    assert normal_server_close is not None
    direct_url = importlib.metadata.distribution("bt_api_py").read_text("direct_url.json")
    if direct_url is not None:
        assert not json.loads(direct_url).get("dir_info", {}).get("editable", False)
    print(
        _encode_json(
            {
                "status": "PASS_CFG04_INSTALLED_ORDER_GROUP_PROXY",
                "client_python": sys.executable,
                "client_prefix": sys.prefix,
                "client_distributions": client_distributions,
                "client_origins": client_origins,
                "client_domain_presence": _domain_presence(),
                "cold_import": cold_import,
                "server_python": str(server_python),
                "server_distributions": ready["distributions"],
                "server_origins": ready["origins"],
                "test_support_payloads": support_payloads,
                "upstream_routes": account_routes,
                "paired_group_state": paired["state"],
                "unknown_group_state": unknown["state"],
                "unknown_account_hold_count": len(account_b["authority_pending_hold_intents"]),
                "normal_host_closes": len(normal_server_close["normal_closes"]),
                "client_runtime_cleanup_failures": runtime.cleanup_failures,
                "client_unregister_uncertainties": runtime.unregister_uncertainties,
            }
        )
    )
