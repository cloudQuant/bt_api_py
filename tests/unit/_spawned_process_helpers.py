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
    process.join(15)
    assert not process.is_alive(), f"{record['label']} did not exit normally within its bound"
    assert process.exitcode == 0, f"{record['label']} exited with code {process.exitcode}"
    record["pipe"].close()
    process.close()
    record["closed"] = True
    return result


def _bounded_reap_spawned_process(record: dict[str, Any], forced, failures) -> None:
    """Try protocol close and join before fallback, for this test's process only."""

    if record["closed"]:
        return
    process, pipe = record["process"], record["pipe"]

    def alive() -> bool:
        try:
            return process.is_alive()
        except Exception as exc:
            failures.append(f"{record['label']}:state:{type(exc).__name__}")
            return False

    if alive():
        try:
            pipe.send(("close",))
        except Exception as exc:
            failures.append(f"{record['label']}:normal_close:{type(exc).__name__}")
    try:
        process.join(5)
    except Exception as exc:
        failures.append(f"{record['label']}:normal_join:{type(exc).__name__}")
    if alive():
        forced.append(record["label"])
        try:
            process.terminate()
        except Exception as exc:
            failures.append(f"{record['label']}:terminate:{type(exc).__name__}")
        try:
            process.join(5)
        except Exception as exc:
            failures.append(f"{record['label']}:terminate_join:{type(exc).__name__}")
    if alive():
        try:
            process.kill()
        except Exception as exc:
            failures.append(f"{record['label']}:kill:{type(exc).__name__}")
        try:
            process.join(5)
        except Exception as exc:
            failures.append(f"{record['label']}:kill_join:{type(exc).__name__}")
    still_alive = alive()
    try:
        pipe.close()
    except Exception as exc:
        failures.append(f"{record['label']}:pipe_close:{type(exc).__name__}")
    if still_alive:
        failures.append(f"{record['label']}:still_alive_after_kill")
        return
    try:
        process.close()
    except Exception as exc:
        failures.append(f"{record['label']}:process_close:{type(exc).__name__}")
    record["closed"] = True


def _assert_no_fallback_cleanup(forced, failures) -> None:
    if forced or failures:
        raise AssertionError(
            f"spawned account integration needed fallback cleanup: "
            f"forced={tuple(forced)}, failures={tuple(failures)}"
        )
