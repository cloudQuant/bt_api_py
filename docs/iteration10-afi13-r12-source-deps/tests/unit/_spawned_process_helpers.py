"""Standard-library helpers for spawned service integration tests."""

from __future__ import annotations

import time
from typing import Any


def _start_process(context, target, args, label: str) -> dict[str, Any]:
    parent, child = context.Pipe(duplex=True)
    runtime_role = _runtime_receipt_role(target)
    if runtime_role is None:
        process_target = target
        process_args = (child, *args)
    else:
        process_target = _runtime_receipt_process_entry
        process_args = (runtime_role, target, child, args)
    process = context.Process(target=process_target, args=process_args, name=label)
    record = {
        "process": process,
        "pipe": parent,
        "label": label,
        "closed": False,
        "runtime_role": runtime_role,
        "spawn_started_monotonic_ns": time.monotonic_ns(),
    }
    try:
        process.start()
    except BaseException:
        parent.close()
        child.close()
        process.close()
        record["closed"] = True
        raise
    child.close()
    record["process_pid"] = process.pid
    record["pipe"] = _ReceiptParentPipeProxy(parent, record)
    return record


def _receive(pipe, timeout: float = 15.0):
    assert pipe.poll(timeout), "spawned account integration process missed its response bound"
    return pipe.recv()


def _client_command(record: dict[str, Any], *command):
    process = record["process"]
    assert process.is_alive(), f"{record['label']} exited before its command"
    record["pipe"].send(command)
    result = _receive(record["pipe"])
    assert result[0] != "error", result
    return result


def _close_normally(record: dict[str, Any], *, expected_tag: str) -> tuple[Any, ...]:
    process = record["process"]
    assert process.is_alive(), f"{record['label']} exited before normal close"
    record["pipe"].send(("close",))
    result = _receive(record["pipe"], timeout=15.0)
    assert result[0] == expected_tag, result
    record["normal_close_ack"] = True
    record["cleanup_mode"] = "normal-protocol"
    process.join(15)
    assert not process.is_alive(), f"{record['label']} did not exit normally within its bound"
    assert process.exitcode == 0, f"{record['label']} exited with code {process.exitcode}"
    record["observed_exit_code"] = process.exitcode
    record["pipe"].close()
    process.close()
    record["closed"] = True
    _write_process_exit_receipt(record, forced=False)
    return result


def _bounded_reap_spawned_process(record: dict[str, Any], forced, failures) -> None:
    """Try protocol close and join before fallback, for this test's process only."""

    if record["closed"]:
        return
    process, pipe = record["process"], record["pipe"]
    record["cleanup_mode"] = "fallback"
    record["termination_attempted"] = False

    def note_cleanup_failure(phase: str, error: BaseException) -> None:
        if record.get("cleanup_error_code") is not None:
            return
        error_name = type(error).__name__
        if (
            type(error_name) is not str
            or len(error_name) > 64
            or not error_name.isascii()
            or not error_name.isidentifier()
        ):
            error_name = "UNCLASSIFIED_EXCEPTION"
        record["cleanup_error_code"] = f"CLEANUP_{phase.upper()}_{error_name}"

    def alive() -> bool:
        try:
            return process.is_alive()
        except Exception as exc:
            note_cleanup_failure("state", exc)
            failures.append(f"{record['label']}:state:{type(exc).__name__}")
            return False

    if alive():
        try:
            pipe.send(("close",))
        except Exception as exc:
            note_cleanup_failure("normal_close", exc)
            failures.append(f"{record['label']}:normal_close:{type(exc).__name__}")
    try:
        process.join(5)
    except Exception as exc:
        note_cleanup_failure("normal_join", exc)
        failures.append(f"{record['label']}:normal_join:{type(exc).__name__}")
    if alive():
        forced.append(record["label"])
        record["termination_attempted"] = True
        try:
            process.terminate()
        except Exception as exc:
            note_cleanup_failure("terminate", exc)
            failures.append(f"{record['label']}:terminate:{type(exc).__name__}")
        try:
            process.join(5)
        except Exception as exc:
            note_cleanup_failure("terminate_join", exc)
            failures.append(f"{record['label']}:terminate_join:{type(exc).__name__}")
    if alive():
        record["termination_attempted"] = True
        try:
            process.kill()
        except Exception as exc:
            note_cleanup_failure("kill", exc)
            failures.append(f"{record['label']}:kill:{type(exc).__name__}")
        try:
            process.join(5)
        except Exception as exc:
            note_cleanup_failure("kill_join", exc)
            failures.append(f"{record['label']}:kill_join:{type(exc).__name__}")
    still_alive = alive()
    try:
        record["observed_exit_code"] = process.exitcode
    except Exception as exc:
        note_cleanup_failure("exitcode", exc)
        failures.append(f"{record['label']}:exitcode:{type(exc).__name__}")
    try:
        pipe.close()
    except Exception as exc:
        note_cleanup_failure("pipe_close", exc)
        failures.append(f"{record['label']}:pipe_close:{type(exc).__name__}")
    if still_alive:
        failures.append(f"{record['label']}:still_alive_after_kill")
        _write_process_exit_receipt(record, forced=True)
        return
    try:
        process.close()
    except Exception as exc:
        note_cleanup_failure("process_close", exc)
        failures.append(f"{record['label']}:process_close:{type(exc).__name__}")
    else:
        record["closed"] = True
    _write_process_exit_receipt(record, forced=True)


def _write_process_exit_receipt(record: dict[str, Any], *, forced: bool) -> None:
    if record.get("runtime_role") is None:
        return
    from tests.unit._runtime_origin_receipts import persist_process_exit

    _load_worker_instance_id(record)
    persist_process_exit(record, forced=forced)


def _assert_no_fallback_cleanup(forced, failures) -> None:
    if forced or failures:
        raise AssertionError(
            f"spawned account integration needed fallback cleanup: "
            f"forced={tuple(forced)}, failures={tuple(failures)}"
        )


def _runtime_receipt_role(target) -> str | None:
    module_name = getattr(target, "__module__", None)
    target_name = getattr(target, "__name__", None)
    if (
        module_name == "tests.integration.test_afi13_strategy_paper_risk_monitor_http"
        and target_name == "_strategy_owner_host_process"
    ):
        return "strategy-owner"
    if (
        module_name == "tests.unit.test_service_execution_paper_risk_monitor_host_integration"
        and target_name == "_monitor_host_process"
    ):
        return "monitor-worker"
    return None


class _ReceiptControlProxy:
    def __init__(self, control, role: str) -> None:
        self._control = control
        self._role = role

    def __getattr__(self, name):
        return getattr(self._control, name)

    def send(self, message) -> None:
        if type(message) is tuple and message and message[0] == "ready":
            from tests.unit._runtime_origin_receipts import (
                capture_runtime_origins,
                write_runtime_receipt,
            )

            payload = capture_runtime_origins(self._role, phase="ready")
            payload["schema"] = "afi13.paper.worker.v1"
            payload["runtime_role"] = self._role
            write_runtime_receipt(self._role, "ready", payload)
        self._control.send(message)


class _ReceiptParentPipeProxy:
    def __init__(self, pipe, record: dict[str, Any]) -> None:
        self._pipe = pipe
        self._record = record

    def __getattr__(self, name):
        return getattr(self._pipe, name)

    def recv(self):
        message = self._pipe.recv()
        if (
            type(message) is tuple
            and message
            and message[0] == "ready"
            and self._record.get("runtime_role") is not None
        ):
            _load_worker_instance_id(self._record)
        return message


def _runtime_receipt_process_entry(role, target, control, args) -> None:
    from tests.unit._runtime_origin_receipts import persist_worker_error

    try:
        target(_ReceiptControlProxy(control, role), *args)
    except BaseException as error:
        try:
            persist_worker_error(role, error)
        except BaseException:
            pass
        raise


def _load_worker_instance_id(record: dict[str, Any]) -> None:
    role = record.get("runtime_role")
    pid = record.get("process_pid")
    if type(role) is not str or type(pid) is not int:
        return
    from pathlib import Path
    import json
    import os

    from tests.unit._runtime_origin_receipts import _INSTANCE_PATTERN

    run_id = os.environ.get("AFI13_PAPER_RUNTIME_RUN_ID")
    receipt_root = os.environ.get("AFI13_PAPER_RUNTIME_RECEIPT_DIR")
    if type(run_id) is not str or type(receipt_root) is not str:
        return
    root = Path(receipt_root)
    started_ns = record.get("spawn_started_monotonic_ns")
    if type(started_ns) is not int:
        return
    ready_candidates = tuple(root.glob(f"{run_id}-{role}-{pid}-*-ready.json"))
    candidates = ready_candidates or tuple(
        root.glob(f"{run_id}-{role}-{pid}-*-error.json")
    )
    receipts = []
    for path in candidates:
        try:
            candidate = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (
            candidate.get("role") == role
            and candidate.get("pid") == pid
            and type(candidate.get("monotonic_ns")) is int
            and candidate["monotonic_ns"] >= started_ns
        ):
            receipts.append(candidate)
    if len(receipts) != 1:
        return
    instance_id = receipts[0].get("process_instance_id")
    if (
        type(instance_id) is str
        and _INSTANCE_PATTERN.fullmatch(instance_id) is not None
    ):
        record["process_instance_id"] = instance_id
