"""Finite runtime-origin and process-lifecycle receipts for AFI13 host tests."""

from __future__ import annotations

import hashlib
import json
import marshal
import multiprocessing
import os
import re
import sys
import time
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any


_RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,79}\Z")
_TOKEN_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}\Z")
_ROLE_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{0,63}\Z")
_PHASE_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{0,47}\Z")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_MAX_CONTEXT = 3
_PROCESS_INSTANCE_ID = uuid.uuid4().hex
_INSTANCE_PATTERN = re.compile(r"[0-9a-f]{32}\Z")

_ROLE_MODULES: dict[str, tuple[str, ...]] = {
    "pytest-parent": (
        "tests.integration.test_afi13_managed_paper_multiworker_http",
        "tests.integration.test_afi13_ai_paper_risk_monitor_http",
        "tests.unit.test_service_execution_paper_risk_monitor_host_integration",
        "tests.unit.test_service_execution_paper_host_integration",
        "tests.unit._spawned_process_helpers",
        "tests.unit._runtime_origin_receipts",
        "tests.unit._runtime_origin_pytest_plugin",
        "bt_api_execution",
        "bt_api_execution.contracts",
        "bt_api_execution.journal_reader",
        "bt_api_monitor",
        "bt_api_monitor.durable",
    ),
    "http-worker": (
        "tests.integration.test_afi13_managed_paper_multiworker_http",
        "tests.unit._runtime_origin_receipts",
        "bt_api_execution.contracts",
        "app.main",
        "app.api.router",
        "app.api.paper_trading",
        "app.api.service_read",
        "app.services.managed_paper_adapter",
        "app.services.service_read.client",
        "app.services.service_read.deployment",
        "app.services.service_read.session",
        "app.services.service_read.worker",
        "bt_api_py.service_config",
        "bt_api_base.service_protocol",
        "bt_api_transport_zmq.service_v2",
    ),
    "execution-owner": (
        "tests.integration.test_afi13_ai_paper_risk_monitor_http",
        "tests.unit.test_service_execution_paper_risk_monitor_host_integration",
        "tests.unit._runtime_origin_receipts",
        "bt_api_base.service_protocol",
        "bt_api_py.service_config",
        "bt_api_py.service_host",
        "bt_api_py.service_components",
        "bt_api_py.service_registry",
        "bt_api_py.service_runtime",
        "bt_api_py.service_execution_account_books",
        "bt_api_py.service_risk_position_snapshot",
        "bt_api_py.service_execution_monitor",
        "bt_api_execution",
        "bt_api_execution.contracts",
        "bt_api_execution.service_order_entry",
        "bt_api_execution.facade",
        "bt_api_execution.paper_execution",
        "bt_api_execution.paper_execution_risk",
        "bt_api_execution.paper_risk",
        "bt_api_execution.store",
        "bt_api_execution.journal_reader",
        "bt_api_risk",
        "bt_api_risk.core.admission",
        "bt_api_gateway",
        "bt_api_gateway.account_position_snapshot",
        "bt_api_monitor",
        "bt_api_monitor.durable",
        "bt_api_transport_zmq.service_v2",
    ),
    "monitor-worker": (
        "tests.unit.test_service_execution_paper_risk_monitor_host_integration",
        "tests.unit._runtime_origin_receipts",
        "bt_api_base.service_protocol",
        "bt_api_py.service_config",
        "bt_api_py.service_host",
        "bt_api_py.service_components",
        "bt_api_py.service_registry",
        "bt_api_py.service_runtime",
        "bt_api_monitor",
        "bt_api_monitor.durable",
        "bt_api_monitor.service_ingress",
        "bt_api_transport_zmq.service_v2",
    ),
}

_ROLE_CALLABLES: dict[str, dict[str, tuple[str, ...]]] = {
    "pytest-parent": {
        "tests.integration.test_afi13_managed_paper_multiworker_http": (
            "test_managed_paper_http_same_key_is_single_fill_across_two_workers_and_restart",
        ),
        "tests.unit._spawned_process_helpers": ("_start_process", "_close_normally"),
        "tests.unit._runtime_origin_pytest_plugin": (
            "pytest_sessionfinish",
            "pytest_runtest_makereport",
        ),
    },
    "http-worker": {
        "tests.integration.test_afi13_managed_paper_multiworker_http": (
            "_managed_paper_http_app_process",
        ),
        "app.main": ("lifespan",),
        "app.api.paper_trading": ("submit_paper_order",),
        "app.services.service_read.deployment": ("start_service_read_deployment",),
        "app.services.managed_paper_adapter": ("_stable_intent_id",),
    },
    "execution-owner": {
        "tests.integration.test_afi13_ai_paper_risk_monitor_http": (
            "_ai_owner_host_process",
        ),
        "bt_api_py.service_host": ("ServiceHost.start",),
        "bt_api_execution.service_order_entry": ("ExecutionOrderEntryService.__init__",),
        "bt_api_execution.paper_execution": ("PaperMatchingPort.submit",),
        "bt_api_execution.paper_risk": ("PaperRiskAdmissionAdapter.reserve_for_execution",),
        "bt_api_py.service_execution_monitor": ("ServiceExecutionMonitorRelay.poll",),
        "bt_api_py.service_execution_account_books": (
            "ExecutionAccountOwnerService.handle",
        ),
    },
    "monitor-worker": {
        "tests.unit.test_service_execution_paper_risk_monitor_host_integration": (
            "_monitor_host_process",
        ),
        "bt_api_py.service_host": ("ServiceHost.start",),
        "bt_api_monitor.service_ingress": ("MonitorIngressService.handle",),
        "bt_api_monitor.durable": ("DurableOutbox.read_page_types",),
    },
}

_REQUIRED_MODULES: dict[str, frozenset[str]] = {
    "pytest-parent": frozenset(
        {
            "tests.integration.test_afi13_managed_paper_multiworker_http",
            "tests.integration.test_afi13_ai_paper_risk_monitor_http",
            "tests.unit.test_service_execution_paper_risk_monitor_host_integration",
            "tests.unit.test_service_execution_paper_host_integration",
            "tests.unit._spawned_process_helpers",
            "tests.unit._runtime_origin_receipts",
            "tests.unit._runtime_origin_pytest_plugin",
            "bt_api_execution",
            "bt_api_execution.contracts",
            "bt_api_execution.journal_reader",
            "bt_api_monitor.durable",
        }
    ),
    "http-worker": frozenset(
        {
            "tests.integration.test_afi13_managed_paper_multiworker_http",
            "tests.unit._runtime_origin_receipts",
            "bt_api_execution.contracts",
            "app.main",
            "app.api.router",
            "app.api.paper_trading",
            "app.services.managed_paper_adapter",
            "app.services.service_read.deployment",
            "app.services.service_read.worker",
            "bt_api_py.service_config",
            "bt_api_base.service_protocol",
            "bt_api_transport_zmq.service_v2",
        }
    ),
    "execution-owner": frozenset(
        {
            "tests.integration.test_afi13_ai_paper_risk_monitor_http",
            "tests.unit._runtime_origin_receipts",
            "bt_api_py.service_host",
            "bt_api_py.service_components",
            "bt_api_py.service_execution_account_books",
            "bt_api_py.service_risk_position_snapshot",
            "bt_api_execution.paper_execution_risk",
            "bt_api_execution.contracts",
            "bt_api_execution.service_order_entry",
            "bt_api_execution.paper_risk",
            "bt_api_risk.core.admission",
            "bt_api_gateway.account_position_snapshot",
            "bt_api_py.service_execution_monitor",
            "bt_api_transport_zmq.service_v2",
        }
    ),
    "monitor-worker": frozenset(
        {
            "tests.unit.test_service_execution_paper_risk_monitor_host_integration",
            "tests.unit._runtime_origin_receipts",
            "bt_api_py.service_host",
            "bt_api_monitor.durable",
            "bt_api_monitor.service_ingress",
            "bt_api_transport_zmq.service_v2",
        }
    ),
}


def _code_hash(code: Any) -> str | None:
    if getattr(code, "__class__", None) is not type((lambda: None).__code__):
        return None
    try:
        return hashlib.sha256(marshal.dumps(code)).hexdigest()
    except (TypeError, ValueError):
        return None


def _resolve_callable(module: ModuleType, dotted_name: str) -> dict[str, Any]:
    value: Any = module
    try:
        for part in dotted_name.split("."):
            value = getattr(value, part)
    except BaseException as error:
        return {"lookup_error_type": _safe_exception_type(error)}
    code = getattr(value, "__code__", None)
    digest = _code_hash(code)
    if code is None or digest is None:
        return {"code_available": False}
    return {
        "code_available": True,
        "co_filename": code.co_filename[:1024],
        "co_firstlineno": int(code.co_firstlineno),
        "code_sha256": digest,
    }


def capture_runtime_origins(
    role: str,
    *,
    phase: str,
    check_required: bool = True,
) -> dict[str, Any]:
    if role not in _ROLE_MODULES or _PHASE_PATTERN.fullmatch(phase) is None:
        raise ValueError("RUNTIME_RECEIPT_ROLE_OR_PHASE_INVALID")
    modules: dict[str, Any] = {}
    for name in _ROLE_MODULES[role]:
        module = sys.modules.get(name)
        if not isinstance(module, ModuleType):
            modules[name] = {"loaded": False}
            continue
        raw_path = getattr(module, "__file__", None)
        row: dict[str, Any] = {"loaded": True, "file": raw_path if type(raw_path) is str else None}
        if type(raw_path) is str:
            try:
                path = Path(raw_path).resolve(strict=True)
                content = path.read_bytes()
                row.update(
                    {
                        "file": str(path),
                        "sha256": hashlib.sha256(content).hexdigest(),
                        "size": len(content),
                    }
                )
            except OSError as error:
                row["source_read_error_type"] = _safe_exception_type(error)
        else:
            row["source_read_error_type"] = "SOURCE_FILE_UNAVAILABLE"
        functions = {
            dotted: _resolve_callable(module, dotted)
            for dotted in _ROLE_CALLABLES.get(role, {}).get(name, ())
        }
        if functions:
            row["functions"] = functions
        modules[name] = row
    missing = (
        sorted(name for name in _REQUIRED_MODULES[role] if not modules[name].get("loaded"))
        if check_required
        else []
    )
    missing_sources = (
        sorted(
            name
            for name in _REQUIRED_MODULES[role]
            if modules[name].get("loaded") is not True
            or type(modules[name].get("size")) is not int
            or modules[name]["size"] < 0
            or type(modules[name].get("sha256")) is not str
            or _SHA256_PATTERN.fullmatch(modules[name]["sha256"]) is None
        )
        if check_required
        else []
    )
    return {
        "schema": "afi13.paper.runtime-origin.v1",
        "role": role,
        "phase": phase,
        "pid": os.getpid(),
        "process_instance_id": _PROCESS_INSTANCE_ID,
        "error_code": None,
        "process_name": multiprocessing.current_process().name,
        "monotonic_ns": time.monotonic_ns(),
        "modules": modules,
        "missing_required_modules": missing,
        "missing_required_sources": missing_sources,
    }


def _safe_token(value: object) -> str | None:
    if type(value) is str and _TOKEN_PATTERN.fullmatch(value) is not None:
        return value
    return None


def _safe_exception_type(error: BaseException) -> str:
    name = type(error).__name__
    return name if _safe_token(name) is not None else "UNCLASSIFIED_EXCEPTION"


def _exception_code(error: BaseException) -> str:
    try:
        attributes = object.__getattribute__(error, "__dict__")
    except BaseException:
        attributes = None
    value = attributes.get("code") if type(attributes) is dict else None
    return _safe_token(value) or _safe_exception_type(error)


def _ownline(error: BaseException) -> dict[str, Any] | None:
    traceback = error.__traceback__
    last = None
    while traceback is not None:
        last = traceback
        traceback = traceback.tb_next
    if last is None:
        return None
    code = last.tb_frame.f_code
    return {
        "co_filename": code.co_filename[:1024],
        "co_name": code.co_name[:128],
        "line": int(last.tb_lineno),
    }


def _exception_node(error: BaseException) -> dict[str, Any]:
    return {
        "type": _safe_exception_type(error),
        "code": _exception_code(error),
        "ownline": _ownline(error),
    }


def summarize_exception(error: BaseException) -> dict[str, Any]:
    context: list[dict[str, Any]] = []
    seen = {id(error)}
    current = error.__context__
    truncated = False
    while current is not None:
        if len(context) >= _MAX_CONTEXT:
            truncated = True
            break
        if id(current) in seen:
            context.append({"cycle": True})
            break
        seen.add(id(current))
        context.append(_exception_node(current))
        current = current.__context__
    return {
        **_exception_node(error),
        "context": context,
        "context_truncated": truncated,
    }


def _receipt_location(role: str, phase: str, pid: int, instance_id: str) -> Path:
    if _ROLE_PATTERN.fullmatch(role) is None or _PHASE_PATTERN.fullmatch(phase) is None:
        raise ValueError("RUNTIME_RECEIPT_NAME_INVALID")
    if _INSTANCE_PATTERN.fullmatch(instance_id) is None:
        raise ValueError("RUNTIME_RECEIPT_INSTANCE_INVALID")
    run_id = os.environ.get("AFI13_PAPER_RUNTIME_RUN_ID")
    raw_root = os.environ.get("AFI13_PAPER_RUNTIME_RECEIPT_DIR")
    if type(run_id) is not str or _RUN_ID_PATTERN.fullmatch(run_id) is None:
        raise RuntimeError("RUNTIME_RECEIPT_RUN_ID_UNAVAILABLE")
    if type(raw_root) is not str or not raw_root:
        raise RuntimeError("RUNTIME_RECEIPT_DIRECTORY_UNAVAILABLE")
    root = Path(raw_root)
    if not root.is_absolute():
        raise RuntimeError("RUNTIME_RECEIPT_DIRECTORY_NOT_ABSOLUTE")
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{run_id}-{role}-{pid}-{instance_id}-{phase}.json"


def write_runtime_receipt(role: str, phase: str, payload: dict[str, Any]) -> Path:
    pid = payload.get("pid", os.getpid())
    if type(pid) is not int or pid <= 0:
        raise ValueError("RUNTIME_RECEIPT_PID_INVALID")
    instance_id = payload.get("process_instance_id", _PROCESS_INSTANCE_ID)
    if type(instance_id) is not str or _INSTANCE_PATTERN.fullmatch(instance_id) is None:
        raise ValueError("RUNTIME_RECEIPT_INSTANCE_INVALID")
    target = _receipt_location(role, phase, pid, instance_id)
    temporary = target.with_name(target.name + ".tmp")
    if target.exists() or temporary.exists():
        raise FileExistsError("RUNTIME_RECEIPT_ALREADY_EXISTS")
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    with temporary.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(body)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, target)
    return target


def persist_worker_error(role: str, error: BaseException) -> dict[str, Any]:
    summary = summarize_exception(error)
    origins = capture_runtime_origins(role, phase="error")
    receipt = {
        "schema": "afi13.paper.worker.v1",
        "role": role,
        "phase": "error",
        "pid": os.getpid(),
        "process_instance_id": _PROCESS_INSTANCE_ID,
        "process_name": multiprocessing.current_process().name,
        "monotonic_ns": time.monotonic_ns(),
        "error": summary,
        "error_code": summary["code"],
        "origins": origins["modules"],
        "missing_required_modules": origins["missing_required_modules"],
        "missing_required_sources": origins["missing_required_sources"],
    }
    write_runtime_receipt(role, "error", receipt)
    return receipt


def persist_parent_worker_receipt(
    role: str,
    phase: str,
    receipt: dict[str, Any],
    *,
    expected_pid: int,
) -> None:
    if receipt.get("role") != role or receipt.get("phase") != phase:
        raise ValueError("RUNTIME_RECEIPT_ROLE_PHASE_MISMATCH")
    if type(expected_pid) is not int or receipt.get("pid") != expected_pid:
        raise ValueError("RUNTIME_RECEIPT_PID_MISMATCH")
    payload = dict(receipt)
    payload["receipt_captured_by_parent_pid"] = os.getpid()
    write_runtime_receipt(role, phase, payload)


def persist_process_exit(record: dict[str, Any], *, forced: bool) -> None:
    role = record.get("runtime_role")
    pid = record.get("process_pid")
    if type(role) is not str or type(pid) is not int:
        return
    exit_code = record.get("observed_exit_code")
    instance_id = record.get("process_instance_id")
    instance_observed = (
        type(instance_id) is str and _INSTANCE_PATTERN.fullmatch(instance_id) is not None
    )
    if not instance_observed:
        instance_id = _PROCESS_INSTANCE_ID
    receipt = {
        "schema": "afi13.paper.worker.v1",
        "role": role,
        "phase": "process-exit",
        "pid": pid,
        "process_instance_id": instance_id,
        "worker_instance_observed": instance_observed,
        "parent_pid": os.getpid(),
        "monotonic_ns": time.monotonic_ns(),
        "exit_code": exit_code if type(exit_code) is int else None,
        "error_code": record.get("cleanup_error_code"),
        "normal_close_ack": record.get("normal_close_ack") is True,
        "cleanup_mode": record.get("cleanup_mode", "fallback-incomplete"),
        "fallback_cleanup_attempted": forced,
        "termination_attempted": record.get("termination_attempted") is True,
        "closed": record.get("closed") is True,
    }
    write_runtime_receipt(role, "process-exit", receipt)


