"""Loopback integration tests for Iteration 72 SessionSupervisor adapters (GAP 4).

Evidence level: LOCAL_LOOPBACK / INTEGRATION_SIM.
Exercises:
  1. Real LocalComponentProcessPort spawning real lightweight worker subprocesses
     for all 9 COMPONENT_START_ORDER services, waiting on readiness, and stopping
     cooperatively in reverse COMPONENT_STOP_ORDER via zero-byte O_EXCL .stop
     markers with exit_code == 0 (never calling terminate/kill).
  2. Real RiskOrderService + real bt_api_risk.web.app ASGI app served over
     loopback HTTP, consumed by RiskWebHttpPort(..., include_proof=True) for both
     R-05 admission gates (risk_r05_before_execution and risk_r05_before_strategy_group).
  3. Real RuntimeHttpPort.from_http(...) talking over loopback HTTP to a strict
     bt_api_runtime.control/v1 control server enforcing exact 11-unit preflight,
     two-step CONFIRMATION_REQUIRED -> APPLIED start_all, status_group, and
     reverse-order cooperative-only unit stops verifying exit_code_observed == 0.
  4. Fail-closed refusal tests: Gateway generation mismatch, R-05 unready,
     pre-existing stale .stop marker, non-zero exit, stop timeout without kill,
     and duplicate start/stop attempts.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

import pytest
import uvicorn

from bt_api_base.risk_service_protocol import RISK_ADMIN_OPERATIONS
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_py.risk_service_component import RiskOrderService
from bt_api_py.risk_service_config import RiskPrincipal
from bt_api_py.risk_service_host import (
    gateway_generation_reference,
    gateway_server_key_fingerprint,
)
from bt_api_risk.web.app import AuthenticatedRiskSession, create_app
from bt_api_runtime.control_http import CONTROL_ENDPOINT

REPO_ROOT = Path(__file__).resolve().parents[2]
SUPERVISOR_PATH = REPO_ROOT / "deploy" / "iteration72" / "session_supervisor.py"
ADAPTERS_PATH = REPO_ROOT / "deploy" / "iteration72" / "session_supervisor_adapters.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


supervisor_module = _load_module("iter72_loopback_session_supervisor", SUPERVISOR_PATH)
adapters_module = _load_module("iter72_loopback_session_supervisor_adapters", ADAPTERS_PATH)

COMPONENT_START_ORDER = supervisor_module.COMPONENT_START_ORDER
COMPONENT_STOP_ORDER = supervisor_module.COMPONENT_STOP_ORDER
GroupResult = supervisor_module.GroupResult
JsonlReceipt = supervisor_module.JsonlReceipt
RiskGateObservation = supervisor_module.RiskGateObservation
SessionPlan = supervisor_module.SessionPlan
SessionSupervisor = supervisor_module.SessionSupervisor
StepResult = supervisor_module.StepResult

ComponentGroupSpec = adapters_module.ComponentGroupSpec
ComponentSpec = adapters_module.ComponentSpec
LocalComponentProcessPort = adapters_module.LocalComponentProcessPort
RiskWebHttpPort = adapters_module.RiskWebHttpPort
RuntimeHttpPort = adapters_module.RuntimeHttpPort

SESSION_ID = "12345678-1234-5678-1234-567812345678"
UNITS = tuple(f"strat_{idx:02d}" for idx in range(1, 12))
RISK_SESSION = "a" * 64
RISK_CSRF = "c" * 64
RUNTIME_TOKEN = "runtime-bearer-token-0123456789abcdef"

_WORKER_SCRIPT = (
    "import os, pathlib, sys, time\n"
    "ready = pathlib.Path(sys.argv[1])\n"
    "stop = pathlib.Path(sys.argv[2])\n"
    "exit_code = int(sys.argv[3])\n"
    "ignore_stop = sys.argv[4] == 'ignore'\n"
    "ready.write_text(str(os.getpid()), encoding='ascii')\n"
    "deadline = time.monotonic() + 30.0\n"
    "while time.monotonic() < deadline:\n"
    "    if (not ignore_stop) and stop.exists():\n"
    "        sys.exit(exit_code)\n"
    "    time.sleep(0.02)\n"
    "sys.exit(99)\n"
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _LoopbackRiskWebServer:
    """Real bt_api_risk.web.app ASGI server backed by real RiskOrderService."""

    def __init__(self, proof_provider) -> None:
        self.port = _free_port()
        self.origin = f"http://127.0.0.1:{self.port}"
        self.proof_provider = proof_provider
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

        class _Engine:
            def status_summary(self):
                return {"available": True, "policy_version": 1}

            def record_admin_refusal(self, *args, **kwargs):
                return None

        principal = RiskPrincipal(
            principal_ref="operator-1",
            role="operator",
            actor_ref="operator-actor-1",
            strategy_refs=frozenset(),
            operations=RISK_ADMIN_OPERATIONS,
            public_key=b"0" * 40,
        )
        self._risk_service = RiskOrderService(
            _Engine(),
            object(),
            account_scope="acct-scope-1",
            route="ctp_simnow",
            execution_domain_ref="exec-domain-1",
            principals={"operator-1": principal},
            instrument_for=lambda *_args: None,
            ready=lambda: True,
            order_cut_required=True,
            order_cut_admission_ready=lambda: bool(self.proof_provider()[0]),
            order_cut_admission_observe=lambda: bool(self.proof_provider()[0]),
            order_cut_admission_proof=lambda: self.proof_provider(),
        )
        self._context = type(
            "Ctx",
            (),
            {
                "principal_ref": "operator-1",
                "identity": WireIdentity(
                    WireRole.APPLICATION_CLIENT,
                    "operator-1",
                    "operator-actor-1",
                ),
                "scope": WireScope(
                    "exec-domain-1",
                    account_ref="acct-scope-1",
                    strategy_ref=None,
                    run_id=None,
                ),
                "operations": RISK_ADMIN_OPERATIONS,
                "registration_id": "reg-1",
            },
        )()

        session = AuthenticatedRiskSession(
            "operator-1", "operator-actor-1", RISK_CSRF, RISK_ADMIN_OPERATIONS
        )

        def authenticate(request):
            if request.headers.get("x-risk-session") != RISK_SESSION:
                raise ValueError("unauthorized")
            return session

        def admin_caller(operation: str, arguments: dict, _timeout: float):
            self.calls.append((operation, dict(arguments)))
            return self._risk_service.handle(
                operation, context=self._context, arguments=arguments
            )

        self._app = create_app(
            origin=self.origin,
            authenticate=authenticate,
            admin_caller=admin_caller,
        )

    def start(self) -> None:
        config = uvicorn.Config(
            self._app,
            host="127.0.0.1",
            port=self.port,
            log_level="error",
            lifespan="off",
        )
        self._server = uvicorn.Server(config)
        self._server.install_signal_handlers = lambda: None

        def _run() -> None:
            asyncio.run(self._server.serve())

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 10.0
        while not self._server.started:
            if time.monotonic() >= deadline:
                raise RuntimeError("risk_web_loopback_start_timeout")
            time.sleep(0.02)

    def close(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5.0)


class _LoopbackRuntimeServer:
    """Strict bt_api_runtime.control/v1 loopback server for RuntimeHttpPort.from_http."""

    def __init__(self, units: tuple[str, ...]) -> None:
        self.units = units
        self.states: dict[str, str] = {u: "INITIALIZED_NO_RUN" for u in units}
        self.run_ids: dict[str, str | None] = {u: None for u in units}
        self.exit_codes: dict[str, int | None] = {u: None for u in units}
        self.pending_tokens: dict[str, tuple[str, str | None, bool]] = {}
        self.actions_log: list[dict[str, Any]] = []
        self.stop_order: list[str] = []
        self._lock = threading.Lock()

        parent = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, _fmt: str, *_args: Any) -> None:
                return

            def _send_json(self, code: int, payload: dict[str, Any]) -> None:
                raw = json.dumps(payload, ensure_ascii=True).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(raw)

            def _check_auth(self) -> bool:
                return self.headers.get("Authorization") == f"Bearer {RUNTIME_TOKEN}"

            def _unit_row(self, name: str) -> dict[str, Any]:
                state = parent.states[name]
                if state == "INITIALIZED_NO_RUN":
                    return {
                        "unit": name,
                        "access": "gateway",
                        "state": "INITIALIZED_NO_RUN",
                        "code": "identity_present",
                        "identity_state": "present",
                        "phase": None,
                        "heartbeat_age_s": None,
                        "heartbeat_fresh": False,
                        "process_alive": False,
                        "run_id": None,
                        "stop_step": None,
                        "clean": False,
                        "pending_fields": [],
                        "exit_code_observed": None,
                        "detail": {},
                    }
                if state == "RUNNING":
                    return {
                        "unit": name,
                        "access": "gateway",
                        "state": "RUNNING",
                        "code": "running",
                        "identity_state": "present",
                        "phase": "LIVE",
                        "heartbeat_age_s": 0.1,
                        "heartbeat_fresh": True,
                        "process_alive": True,
                        "run_id": parent.run_ids[name],
                        "stop_step": "NONE",
                        "clean": False,
                        "pending_fields": [],
                        "exit_code_observed": None,
                        "detail": {
                            "last_error_code": None,
                            "write_block_reasons": [],
                        },
                    }
                return {
                    "unit": name,
                    "access": "gateway",
                    "state": "STOPPED",
                    "code": "stopped",
                    "identity_state": "present",
                    "phase": "STOPPED",
                    "heartbeat_age_s": None,
                    "heartbeat_fresh": False,
                    "process_alive": False,
                    "run_id": parent.run_ids[name],
                    "stop_step": "DONE",
                    "clean": True,
                    "pending_fields": [],
                    "exit_code_observed": parent.exit_codes[name],
                    "detail": {},
                }

            def do_POST(self) -> None:
                if not self._check_auth():
                    self._send_json(401, {"status": "REJECTED", "code": "unauthorized"})
                    return
                if self.path != CONTROL_ENDPOINT:
                    self._send_json(404, {"status": "REJECTED", "code": "not_found"})
                    return
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                with parent._lock:
                    operation = body.get("operation")
                    if operation == "runtime.units.list":
                        rows = [self._unit_row(u) for u in parent.units]
                        self._send_json(
                            200,
                            {
                                "schema": adapters_module.CONTROL_RESPONSE_SCHEMA,
                                "status": "OK",
                                "code": "ok",
                                "count": len(rows),
                                "units": rows,
                            },
                        )
                        return

                    parent.actions_log.append(dict(body))
                    command_id = body.get("command_id")
                    confirm = body.get("confirm")
                    all_units = body.get("all_units") is True
                    unit = body.get("unit")
                    params = body.get("parameters") or {}

                    if operation == "runtime.unit.start" and all_units:
                        if confirm is None:
                            token = "tok_start_" + hashlib.sha256(
                                command_id.encode("ascii")
                            ).hexdigest()[:32]
                            parent.pending_tokens[token] = (command_id, None, True)
                            self._send_json(
                                200,
                                {
                                    "schema": adapters_module.CONTROL_RESPONSE_SCHEMA,
                                    "status": "CONFIRMATION_REQUIRED",
                                    "code": "confirmation_required",
                                    "action": "start",
                                    "all_units": True,
                                    "command_id": command_id,
                                    "confirmation_token": token,
                                },
                            )
                            return
                        expected = parent.pending_tokens.pop(confirm, None)
                        if expected != (command_id, None, True):
                            self._send_json(
                                400,
                                {
                                    "schema": adapters_module.CONTROL_RESPONSE_SCHEMA,
                                    "status": "REJECTED",
                                    "code": "confirmation_invalid",
                                },
                            )
                            return
                        unit_results = []
                        for name in parent.units:
                            parent.states[name] = "RUNNING"
                            parent.run_ids[name] = str(uuid5(UUID(SESSION_ID), name))
                            parent.exit_codes[name] = None
                            unit_results.append(
                                {
                                    "unit": name,
                                    "command_id": str(uuid5(UUID(command_id), name)),
                                    "status": "APPLIED",
                                    "code": "started",
                                    "state": "STARTING",
                                }
                            )
                        self._send_json(
                            200,
                            {
                                "schema": adapters_module.CONTROL_RESPONSE_SCHEMA,
                                "status": "APPLIED",
                                "code": "started",
                                "action": "start",
                                "all_units": True,
                                "command_id": command_id,
                                "started_units": list(parent.units),
                                "not_started_units": [],
                                "uncertain_units": [],
                                "units": unit_results,
                            },
                        )
                        return

                    if (
                        operation == "runtime.unit.stop"
                        and not all_units
                        and unit in parent.states
                    ):
                        assert params == {"cooperative_only": True}
                        if confirm is None:
                            token = "tok_stop_" + hashlib.sha256(
                                (command_id + ":" + unit).encode("ascii")
                            ).hexdigest()[:32]
                            parent.pending_tokens[token] = (command_id, unit, False)
                            self._send_json(
                                200,
                                {
                                    "schema": adapters_module.CONTROL_RESPONSE_SCHEMA,
                                    "status": "CONFIRMATION_REQUIRED",
                                    "code": "confirmation_required",
                                    "action": "stop",
                                    "all_units": False,
                                    "unit": unit,
                                    "command_id": command_id,
                                    "confirmation_token": token,
                                },
                            )
                            return
                        expected = parent.pending_tokens.pop(confirm, None)
                        if expected != (command_id, unit, False):
                            self._send_json(
                                400,
                                {
                                    "schema": adapters_module.CONTROL_RESPONSE_SCHEMA,
                                    "status": "REJECTED",
                                    "code": "confirmation_invalid",
                                },
                            )
                            return
                        parent.states[unit] = "STOPPED"
                        parent.exit_codes[unit] = 0
                        parent.stop_order.append(unit)
                        self._send_json(
                            200,
                            {
                                "schema": adapters_module.CONTROL_RESPONSE_SCHEMA,
                                "status": "APPLIED",
                                "code": "stopped",
                                "action": "stop",
                                "all_units": False,
                                "unit": unit,
                                "command_id": command_id,
                                "state": "STOPPED",
                            },
                        )
                        return

                self._send_json(404, {"status": "REJECTED", "code": "not_found"})

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = int(self._httpd.server_address[1])
        self.origin = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5.0)


def _build_component_specs(
    tmp_path: Path,
    *,
    gateway_reference: str,
    exit_codes: dict[str, int] | None = None,
    ignore_stop: set[str] | None = None,
    stop_timeout: float = 10.0,
) -> dict[str, Any]:
    exit_codes = exit_codes or {}
    ignore_stop = ignore_stop or set()
    specs: dict[str, Any] = {}
    stop_dir = tmp_path / "stops"
    ready_dir = tmp_path / "ready"
    stop_dir.mkdir(exist_ok=True)
    ready_dir.mkdir(exist_ok=True)
    for name in COMPONENT_START_ORDER:
        ready_file = ready_dir / f"{name}.ready"
        stop_file = stop_dir / f"{name}.stop"
        code = exit_codes.get(name, 0)
        mode = "ignore" if name in ignore_stop else "coop"
        if name == "gateway":
            ready_check = lambda rf=ready_file, ref=gateway_reference: (
                ref if rf.exists() else False
            )
        else:
            ready_check = lambda rf=ready_file: rf.exists()
        specs[name] = ComponentSpec(
            argv=(
                sys.executable,
                "-c",
                _WORKER_SCRIPT,
                str(ready_file),
                str(stop_file),
                str(code),
                mode,
            ),
            stop_file=stop_file,
            ready_check=ready_check,
            cwd=tmp_path,
            start_timeout_seconds=10.0,
            stop_timeout_seconds=stop_timeout,
            poll_interval_seconds=0.02,
        )
    return specs


def test_full_session_supervisor_loopback_start_status_and_cooperative_stop(
    tmp_path: Path,
) -> None:
    gw_ref = gateway_generation_reference(
        journal_id="gw-journal-001",
        owner_generation="gen-20261126-01",
        service_id="gateway-simnow",
        instance_id="gw-inst-01",
        server_key_fingerprint=gateway_server_key_fingerprint(b"k" * 40),
    )
    obs_ref = "sha256:" + hashlib.sha256(b"r05-verified-cut-v1").hexdigest()
    risk_server = _LoopbackRiskWebServer(lambda: (True, gw_ref, obs_ref))
    runtime_server = _LoopbackRuntimeServer(UNITS)
    risk_server.start()
    runtime_server.start()
    try:
        specs = _build_component_specs(tmp_path, gateway_reference=gw_ref)
        components = LocalComponentProcessPort(specs, step_factory=StepResult)
        runtime_port = RuntimeHttpPort.from_http(
            host="127.0.0.1",
            port=runtime_server.port,
            token=RUNTIME_TOKEN,
            step_result_factory=StepResult,
            group_result_factory=GroupResult,
            timeout=5.0,
        )
        risk_port = RiskWebHttpPort(
            origin=risk_server.origin,
            session_token=RISK_SESSION,
            gateway_reference=gw_ref,
            observation_factory=RiskGateObservation,
            timeout=5.0,
            include_proof=True,
        )
        receipt_path = tmp_path / "session_receipt.jsonl"
        with JsonlReceipt(receipt_path, SESSION_ID) as receipt:
            supervisor = SessionSupervisor(
                SessionPlan(SESSION_ID, UNITS),
                components=components,
                runtime=runtime_port,
                risk=risk_port,
                receipt=receipt,
            )

            pre = supervisor.preflight()
            assert (pre.status, pre.code, pre.exit_code) == (
                "APPLIED",
                "session_preflight_ready",
                0,
            )

            started = supervisor.start_session()
            assert (started.status, started.code, started.exit_code) == (
                "APPLIED",
                "session_ready",
                0,
            )

            status = supervisor.status()
            assert (status.status, status.code, status.exit_code) == (
                "APPLIED",
                "session_running",
                0,
            )

            stopped = supervisor.stop_session()
            assert (stopped.status, stopped.code, stopped.exit_code) == (
                "APPLIED",
                "session_stopped",
                0,
            )

        # Verify both R-05 gates queried real Risk Web with order_cut_proof=True.
        assert risk_server.calls == [
            ("risk.admin.status", {"order_cut_admission": True, "order_cut_proof": True}),
            ("risk.admin.status", {"order_cut_admission": True, "order_cut_proof": True}),
        ]
        # Verify strategies stopped in exact reverse order with cooperative_only=True.
        assert runtime_server.stop_order == list(reversed(UNITS))
        assert all(runtime_server.exit_codes[u] == 0 for u in UNITS)

        # Verify zero-byte cooperative .stop markers were created for all 9 components.
        for name in COMPONENT_START_ORDER:
            marker = specs[name].stop_file
            assert marker.exists() and marker.stat().st_size == 0

        # Inspect persisted JSONL receipt rows.
        raw_text = receipt_path.read_text(encoding="utf-8")
        assert RISK_SESSION not in raw_text
        assert RUNTIME_TOKEN not in raw_text
        assert "tok_start_" not in raw_text
        assert "tok_stop_" not in raw_text
        rows = [json.loads(line) for line in raw_text.splitlines() if line.strip()]
        assert [row["sequence"] for row in rows] == list(range(1, len(rows) + 1))

        started_subjects = [
            row["subject"]
            for row in rows
            if row["action"] == "component_start" and row["status"] == "APPLIED"
        ]
        stopped_rows = [
            row
            for row in rows
            if row["action"] == "component_stop" and row["status"] == "APPLIED"
        ]
        assert started_subjects == list(COMPONENT_START_ORDER)
        assert [row["subject"] for row in stopped_rows] == list(COMPONENT_STOP_ORDER)
        assert all(row["exit_code"] == 0 for row in stopped_rows)

        r05_ready = [
            row
            for row in rows
            if row["action"] == "risk_r05" and row["code"] == "risk_r05_ready"
        ]
        assert [row["subject"] for row in r05_ready] == [
            "risk_r05_before_execution",
            "risk_r05_before_strategy_group",
        ]
        assert all(row["reference"] == obs_ref for row in r05_ready)
    finally:
        risk_server.close()
        runtime_server.close()


def test_session_supervisor_blocks_execution_when_risk_gateway_generation_mismatches(
    tmp_path: Path,
) -> None:
    gw_ref_expected = gateway_generation_reference(
        journal_id="gw-journal-001",
        owner_generation="gen-new",
        service_id="gateway-simnow",
        instance_id="gw-inst-01",
        server_key_fingerprint=gateway_server_key_fingerprint(b"k" * 40),
    )
    gw_ref_stale = gateway_generation_reference(
        journal_id="gw-journal-001",
        owner_generation="gen-old",
        service_id="gateway-simnow",
        instance_id="gw-inst-01",
        server_key_fingerprint=gateway_server_key_fingerprint(b"k" * 40),
    )
    obs_ref = "sha256:" + "d" * 64
    risk_server = _LoopbackRiskWebServer(lambda: (True, gw_ref_stale, obs_ref))
    runtime_server = _LoopbackRuntimeServer(UNITS)
    risk_server.start()
    runtime_server.start()
    specs = _build_component_specs(tmp_path, gateway_reference=gw_ref_expected)
    components = LocalComponentProcessPort(specs, step_factory=StepResult)
    try:
        runtime_port = RuntimeHttpPort.from_http(
            host="127.0.0.1",
            port=runtime_server.port,
            token=RUNTIME_TOKEN,
            step_result_factory=StepResult,
            group_result_factory=GroupResult,
        )
        risk_port = RiskWebHttpPort(
            origin=risk_server.origin,
            session_token=RISK_SESSION,
            gateway_reference=gw_ref_expected,
            observation_factory=RiskGateObservation,
            include_proof=True,
        )
        receipt_path = tmp_path / "mismatch_receipt.jsonl"
        with JsonlReceipt(receipt_path, SESSION_ID) as receipt:
            supervisor = SessionSupervisor(
                SessionPlan(SESSION_ID, UNITS),
                components=components,
                runtime=runtime_port,
                risk=risk_port,
                receipt=receipt,
            )
            outcome = supervisor.start_session()
            assert (outcome.status, outcome.code, outcome.exit_code) == (
                "UNKNOWN",
                "session_unknown",
                3,
            )
            # Execution, runtime, web, and strategies must NEVER have started.
            assert "execution" not in components._processes
            assert runtime_server.actions_log == []
            rows = [
                json.loads(line)
                for line in receipt_path.read_text(encoding="utf-8").splitlines()
            ]
            assert any(row["code"] == "risk_gateway_mismatch" for row in rows)
    finally:
        # Cleanly stop the 6 components started before execution using markers.
        for name in COMPONENT_START_ORDER[:6]:
            if name in components._processes:
                components.stop_cooperatively(name)
        risk_server.close()
        runtime_server.close()


def test_session_supervisor_returns_blocked_when_risk_r05_is_unready_for_matching_gateway(
    tmp_path: Path,
) -> None:
    gw_ref = gateway_generation_reference(
        journal_id="gw-journal-001",
        owner_generation="gen-1",
        service_id="gateway-simnow",
        instance_id="gw-inst-01",
        server_key_fingerprint=gateway_server_key_fingerprint(b"k" * 40),
    )
    risk_server = _LoopbackRiskWebServer(lambda: (False, gw_ref, None))
    runtime_server = _LoopbackRuntimeServer(UNITS)
    risk_server.start()
    runtime_server.start()
    specs = _build_component_specs(tmp_path, gateway_reference=gw_ref)
    components = LocalComponentProcessPort(specs, step_factory=StepResult)
    try:
        runtime_port = RuntimeHttpPort.from_http(
            host="127.0.0.1",
            port=runtime_server.port,
            token=RUNTIME_TOKEN,
            step_result_factory=StepResult,
            group_result_factory=GroupResult,
        )
        risk_port = RiskWebHttpPort(
            origin=risk_server.origin,
            session_token=RISK_SESSION,
            gateway_reference=gw_ref,
            observation_factory=RiskGateObservation,
            include_proof=True,
        )
        receipt_path = tmp_path / "unready_receipt.jsonl"
        with JsonlReceipt(receipt_path, SESSION_ID) as receipt:
            supervisor = SessionSupervisor(
                SessionPlan(SESSION_ID, UNITS),
                components=components,
                runtime=runtime_port,
                risk=risk_port,
                receipt=receipt,
            )
            outcome = supervisor.start_session()
            assert (outcome.status, outcome.code, outcome.exit_code) == (
                "BLOCKED",
                "session_blocked",
                2,
            )
            # Clean cooperative teardown of the 6 already-started components works from BLOCKED.
            stopped = supervisor.stop_session()
            assert (stopped.status, stopped.code, stopped.exit_code) == (
                "APPLIED",
                "session_stopped",
                0,
            )
            assert "execution" not in components._processes
            assert runtime_server.actions_log == []
    finally:
        risk_server.close()
        runtime_server.close()


def test_local_component_port_fail_closed_stale_marker_nonzero_exit_and_timeout_no_kill(
    tmp_path: Path,
) -> None:
    gw_ref = "sha256:" + "e" * 64
    specs = _build_component_specs(
        tmp_path,
        gateway_reference=gw_ref,
        exit_codes={"monitor": 7},
        ignore_stop={"risk"},
        stop_timeout=0.4,
    )
    # 1. Pre-existing stale .stop marker blocks preflight and is NOT deleted.
    specs["web"].stop_file.write_bytes(b"")
    port = LocalComponentProcessPort(specs, step_factory=StepResult)
    pre_web = port.preflight("web")
    assert (pre_web.status, pre_web.code) == ("BLOCKED", "stop_marker_present")
    assert specs["web"].stop_file.exists()

    # 2. Non-zero exit on cooperative stop returns UNKNOWN with exact exit_code; duplicate refused.
    assert port.preflight("monitor").status == "APPLIED"
    assert port.start("monitor").status == "APPLIED"
    dup_start = port.start("monitor")
    assert (dup_start.status, dup_start.code) == (
        "UNKNOWN",
        "component_start_already_attempted",
    )
    stop_mon = port.stop_cooperatively("monitor")
    assert (stop_mon.status, stop_mon.code, stop_mon.exit_code) == (
        "UNKNOWN",
        "component_stop_nonzero_exit",
        7,
    )
    dup_stop = port.stop_cooperatively("monitor")
    assert (dup_stop.status, dup_stop.code) == (
        "UNKNOWN",
        "component_stop_already_attempted",
    )

    # 3. Cooperative stop timeout returns UNKNOWN WITHOUT calling terminate() or kill().
    assert port.start("risk").status == "APPLIED"
    proc = port._processes["risk"]
    try:
        stop_risk = port.stop_cooperatively("risk")
        assert (stop_risk.status, stop_risk.code) == (
            "UNKNOWN",
            "component_stop_timeout",
        )
        assert proc.poll() is None  # Proves adapter NEVER killed the process!
    finally:
        # Test harness cleanup only.
        proc.terminate()
        proc.wait(timeout=5.0)


def test_session_supervisor_supports_multiprocess_execution_group_and_dynamic_cold_start(
    tmp_path: Path,
) -> None:
    """Exercise cold-start SessionSupervisor with:
    - ComponentGroupSpec for multi-process 'execution' (3 members, reverse cooperative stop);
    - Dynamic stop_file resolver resolved after start;
    - Dynamic RiskWebHttpPort(session_token=callable, gateway_reference=None, include_proof=True);
    - RuntimeHttpPort offline preflight_units_provider before Runtime starts, then live HTTP checks.
    """
    gw_ref = gateway_generation_reference(
        journal_id="gw-journal-cold",
        owner_generation="gen-cold-01",
        service_id="gateway-simnow",
        instance_id="gw-inst-01",
        server_key_fingerprint=gateway_server_key_fingerprint(b"m" * 40),
    )
    obs_ref = "sha256:" + hashlib.sha256(b"r05-cold-verified").hexdigest()
    risk_server = _LoopbackRiskWebServer(lambda: (True, gw_ref, obs_ref))
    runtime_server = _LoopbackRuntimeServer(UNITS)
    risk_server.start()
    runtime_server.start()
    try:
        specs: dict[str, Any] = _build_component_specs(tmp_path, gateway_reference=gw_ref)
        # Replace execution with a 3-process ComponentGroupSpec using dynamic stop_file callables.
        exec_members = []
        exec_stop_files: list[Path] = []
        for idx in range(3):
            ready_file = tmp_path / f"exec_{idx}.ready"
            stop_file = tmp_path / f"exec_{idx}.stop"
            exec_stop_files.append(stop_file)
            resolved_stop: list[Path | None] = [None]

            def _ready(rf=ready_file, sf=stop_file, holder=resolved_stop):
                if rf.exists():
                    holder[0] = sf
                    return True
                return False

            exec_members.append(
                ComponentSpec(
                    argv=(
                        sys.executable,
                        "-c",
                        _WORKER_SCRIPT,
                        str(ready_file),
                        str(stop_file),
                        "0",
                        "normal",
                    ),
                    stop_file=lambda holder=resolved_stop: holder[0],
                    ready_check=_ready,
                    cwd=tmp_path,
                    start_timeout_seconds=10.0,
                    stop_timeout_seconds=5.0,
                    poll_interval_seconds=0.02,
                )
            )
        specs["execution"] = ComponentGroupSpec(tuple(exec_members))
        components = LocalComponentProcessPort(specs, step_factory=StepResult)
        runtime_port = RuntimeHttpPort.from_http(
            host="127.0.0.1",
            port=runtime_server.port,
            token=RUNTIME_TOKEN,
            step_result_factory=StepResult,
            group_result_factory=GroupResult,
            timeout=5.0,
            preflight_units_provider=lambda: UNITS,
        )
        risk_port = RiskWebHttpPort(
            origin=risk_server.origin,
            session_token=lambda: RISK_SESSION,
            gateway_reference=None,
            observation_factory=RiskGateObservation,
            timeout=5.0,
            include_proof=True,
        )
        receipt_path = tmp_path / "cold_start_receipt.jsonl"
        with JsonlReceipt(receipt_path, SESSION_ID) as receipt:
            supervisor = SessionSupervisor(
                SessionPlan(SESSION_ID, UNITS),
                components=components,
                runtime=runtime_port,
                risk=risk_port,
                receipt=receipt,
            )
            assert supervisor.preflight().exit_code == 0
            assert supervisor.start_session().exit_code == 0
            assert len(components._group_processes["execution"]) == 3
            assert supervisor.status().exit_code == 0
            stopped = supervisor.stop_session()
            assert (stopped.status, stopped.code, stopped.exit_code) == (
                "APPLIED",
                "session_stopped",
                0,
            )
        assert all(sf.exists() and sf.stat().st_size == 0 for sf in exec_stop_files)
    finally:
        risk_server.close()
        runtime_server.close()


def test_local_component_port_shared_stdout_stderr_does_not_overwrite(tmp_path: Path) -> None:
    """When stdout_path == stderr_path, stderr must share stdout (STDOUT) without clobbering."""
    log_path = tmp_path / "shared.log"
    stop_file = tmp_path / "shared.stop"
    script = (
        "import sys, time; from pathlib import Path; "
        "print('STDOUT_READY_MARKER', flush=True); "
        "print('STDERR_WARNING_LINE_' + 'X' * 120, file=sys.stderr, flush=True); "
        "sf = Path(sys.argv[1]); "
        "deadline = time.monotonic() + 10.0\n"
        "while time.monotonic() < deadline and not sf.exists():\n"
        "    time.sleep(0.02)\n"
        "print('STDOUT_STOPPED_MARKER', flush=True)\n"
    )
    spec = ComponentSpec(
        argv=(sys.executable, "-u", "-c", script, str(stop_file)),
        stop_file=stop_file,
        ready_check=lambda: log_path.is_file()
        and "STDOUT_READY_MARKER" in log_path.read_text(encoding="utf-8", errors="replace"),
        cwd=tmp_path,
        start_timeout_seconds=10.0,
        stop_timeout_seconds=5.0,
        poll_interval_seconds=0.02,
        stdout_path=log_path,
        stderr_path=log_path,
    )
    port = LocalComponentProcessPort({"monitor": spec}, step_factory=StepResult)
    assert port.preflight("monitor").status == "APPLIED"
    assert port.start("monitor").status == "APPLIED"
    stopped = port.stop_cooperatively("monitor")
    assert stopped.status == "APPLIED" and stopped.exit_code == 0
    text = log_path.read_text(encoding="utf-8")
    assert "STDOUT_READY_MARKER" in text
    assert "STDERR_WARNING_LINE_" in text
    assert "STDOUT_STOPPED_MARKER" in text
