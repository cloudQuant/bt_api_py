"""Standard-library helpers for spawned service integration tests."""

from __future__ import annotations

from typing import Any


def _start_process(context, target, args, label: str) -> dict[str, Any]:
    parent, child = context.Pipe(duplex=True)
    process = context.Process(target=target, args=(child, *args), name=label)
    record = {"process": process, "pipe": parent, "label": label, "closed": False}
    try:
        process.start()
    except BaseException:
        parent.close()
        child.close()
        process.close()
        record["closed"] = True
        raise
    child.close()
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

    persist_process_exit(record, forced=forced)


def _assert_no_fallback_cleanup(forced, failures) -> None:
    if forced or failures:
        raise AssertionError(
            f"spawned account integration needed fallback cleanup: "
            f"forced={tuple(forced)}, failures={tuple(failures)}"
        )
