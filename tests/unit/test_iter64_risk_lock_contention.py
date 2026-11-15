"""Bounded fake proof of actual SDK-lock contention, not all thread schedules.

Each case starts a fresh synthetic subprocess. The native facade lock and
host/facade methods are never replaced. This does not prove that the second
call entered blocking acquire, or establish source or account authority. The
second call first encounters the shared lock inside facade ``_check``.
"""
from __future__ import annotations

import ast
import faulthandler
import hashlib
import inspect
import json
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import textwrap
import threading
import uuid
from contextlib import suppress
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_CASES = frozenset({"dispatch", "release"})
_BLOCKED_EVENTS = frozenset({
    "socket.connect", "socket.connect_ex", "socket.sendto", "socket.sendmsg",
    "subprocess.Popen", "os.system", "os.fork", "os.forkpty", "os.exec",
    "os.spawn", "os.posix_spawn",
})


def _group_members(group_id: int, ps: str) -> list[dict]:
    result = subprocess.run(
        [ps, "-A", "-o", "pid=", "-o", "ppid=", "-o", "pgid=", "-o", "comm="],
        text=True, capture_output=True, timeout=5, check=True,
    )
    members = []
    for line in result.stdout.splitlines():
        fields = line.strip().split(None, 3)
        if len(fields) != 4 or not all(re.fullmatch(r"\d+", part) for part in fields[:3]):
            continue
        pid, ppid, pgid = map(int, fields[:3])
        if pgid != group_id:
            continue
        try:
            sid = os.getsid(pid)
        except ProcessLookupError:
            continue
        members.append({"pid": pid, "ppid": ppid, "pgid": pgid, "sid": sid, "comm": fields[3]})
    return members


def _runtime_or_skip():
    """Use only a verifiable conda base and a usable POSIX process listing."""
    if os.name != "posix" or any(not hasattr(os, name) for name in ("getpgid", "getpgrp", "getsid", "killpg")):
        pytest.skip("NOT_RUN: POSIX process-group controls are unavailable")
    conda = os.environ.get("CONDA_EXE") or shutil.which("conda")
    ps = shutil.which("ps")
    if not conda or not ps or not Path(conda).is_file() or not os.access(conda, os.X_OK):
        pytest.skip("NOT_RUN: conda or compatible ps is unavailable")
    conda_prefix = os.environ.get("CONDA_PREFIX")
    if (not conda_prefix or os.environ.get("CONDA_DEFAULT_ENV") != "base"
            or Path(conda_prefix).resolve() != Path(sys.prefix).resolve()
            or not (Path(sys.prefix) / "conda-meta" / "history").is_file()):
        pytest.skip("NOT_RUN: current Python is not verifiably conda base")
    executable = Path(sys.executable).resolve()
    if not executable.is_relative_to(Path(sys.prefix).resolve()):
        pytest.skip("NOT_RUN: interpreter is outside the active conda prefix")
    try:
        members = _group_members(os.getpgrp(), ps)
    except (OSError, ValueError, subprocess.SubprocessError):
        pytest.skip("NOT_RUN: ps does not support required process-group inspection")
    if not any(item["pid"] == os.getpid() for item in members):
        pytest.skip("NOT_RUN: current process group could not be identified")
    if "HOME" not in os.environ:
        pytest.skip("NOT_RUN: conda base requires an existing HOME environment")
    return str(Path(conda).resolve()), str(Path(ps).resolve()), str(Path(sys.prefix).resolve()), str(executable)


def _known_own_group(proc, marker, token, members):
    """Best-effort identity check; a later group signal still has a TOCTOU gap."""
    if proc.poll() is not None or not marker.is_file():
        return False
    identity = json.loads(marker.read_text(encoding="utf-8"))
    if (identity.get("token") != token or identity.get("pgid") != proc.pid
            or identity.get("sid") != proc.pid
            or identity.get("pid") not in {item["pid"] for item in members}):
        return False
    descendants = {proc.pid}
    for _ in range(len(members) + 1):
        descendants.update(item["pid"] for item in members if item["ppid"] in descendants)
    allowed = {"python", "python3", "bash", "sh", "zsh", "conda",
               Path(sys.executable).name, Path(sys.executable).resolve().name}
    return all(item["pid"] in descendants and item["sid"] == proc.pid
               and Path(item["comm"]).name in allowed for item in members)


def _pid_absent(pid):
    try:
        os.getpgid(pid)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return False


def _child(case: str, token: str, identity_file: Path, expected_prefix: str, expected_python: str) -> None:
    if case not in _CASES:
        raise ValueError("unexpected case")
    if (Path(sys.prefix).resolve() != Path(expected_prefix)
            or Path(sys.executable).resolve() != Path(expected_python)
            or os.environ.get("CONDA_DEFAULT_ENV") != "base"):
        raise RuntimeError("child is not running the verified conda base interpreter")
    identity = {"token": token, "pid": os.getpid(), "ppid": os.getppid(),
                "pgid": os.getpgrp(), "sid": os.getsid(0)}
    with identity_file.open("x", encoding="utf-8") as stream:
        json.dump(identity, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    print("CHILD_ID " + json.dumps(identity, sort_keys=True), flush=True)
    faulthandler.dump_traceback_later(30, exit=True)

    prefixes = ("bt_api_py", "bt_api_base", "bt_api_risk", "backtrader")
    if any(name == prefix or name.startswith(prefix + ".") for name in sys.modules for prefix in prefixes):
        raise RuntimeError("product imported before isolation")
    blocked = []

    def audit(event, args):
        if event in _BLOCKED_EVENTS:
            blocked.append(event)
            raise PermissionError("synthetic isolation blocked " + event)

    sys.addaudithook(audit)
    print("ISOLATION_ARMED_BEFORE_PRODUCT_IMPORT", flush=True)
    audit_self_test = ["socket.connect", "socket.sendto", "subprocess.Popen", "os.fork", "os.exec"]
    for event in audit_self_test:
        try:
            # Emitting an audit event does not perform the operation.
            sys.audit(event)
        except PermissionError:
            pass
        else:
            raise AssertionError("synthetic isolation negative control failed")
    assert blocked == audit_self_test
    blocked.clear()
    from bt_api_py.gateway_api import GatewayBtApi
    from tests.unit.test_iter63_gateway_api import order
    from tests.unit.test_iter64_gateway_existing_permit_unknown import KEY, ROUTE
    from tests.unit.test_iter64_risk_service_client import Host, answer, facade

    patcher = pytest.MonkeyPatch()
    api, host, wire, direct = facade(patcher, replies=[answer("RESERVED")])
    real_lock = api._lock
    assert type(real_lock) is type(threading.Lock())
    def lock_lines(method):
        source, first_line = inspect.getsourcelines(method)
        tree = ast.parse(textwrap.dedent("".join(source)))
        blocks = [node for node in ast.walk(tree) if isinstance(node, ast.With)
                  and any(isinstance(item.context_expr, ast.Attribute)
                          and isinstance(item.context_expr.value, ast.Name)
                          and item.context_expr.value.id == "self"
                          and item.context_expr.attr == "_lock" for item in node.items)]
        assert len(blocks) == 1 and blocks[0].body
        return first_line + blocks[0].lineno - 1, first_line + blocks[0].body[0].lineno - 1

    _, inside_line = lock_lines(GatewayBtApi._risk_reserve)
    check_with_line, _ = lock_lines(GatewayBtApi._check)
    code = GatewayBtApi._risk_reserve.__code__
    check_code = GatewayBtApi._check.__code__
    create_code = Host.create_risk_reserve_intent.__code__
    consume_code = Host.consume_risk_reserve_ticket.__code__
    a_held = threading.Event()
    b_contended = threading.Event()
    release_a = threading.Event()
    events = queue.SimpleQueue()
    results = queue.SimpleQueue()
    problems = queue.SimpleQueue()

    def worker(label):
        old_trace, old_profile = sys.gettrace(), sys.getprofile()
        touched = False

        def trace(frame, event, arg):
            nonlocal touched
            if event == "line" and not touched:
                if label == "A" and frame.f_code is code and frame.f_lineno == inside_line:
                    touched = True
                    assert frame.f_locals.get("self") is api and api._lock is real_lock
                    assert real_lock.locked() and not host.intents and not host._risk_tickets
                    events.put(("A_HELD_BEFORE_INTENT", label))
                    a_held.set()
                    if not release_a.wait(12):
                        raise TimeoutError("A release not received")
                elif label == "B" and frame.f_code is check_code and frame.f_lineno == check_with_line:
                    touched = True
                    assert frame.f_locals.get("self") is api and api._lock is real_lock
                    acquired = real_lock.acquire(blocking=False)
                    if acquired:
                        real_lock.release()
                        raise AssertionError("B did not observe contention")
                    events.put(("B_OBSERVED_ORIGINAL_LOCK_CONTENDED", label))
                    b_contended.set()
            return trace

        def profile(frame, event, arg):
            if frame.f_locals.get("self") is host:
                if frame.f_code is create_code and event in ("call", "return"):
                    value = frame.f_locals["intent"].request_key if event == "call" else arg
                    events.put(("CREATE_" + event.upper(), value))
                elif frame.f_code is consume_code and event in ("call", "return"):
                    value = ((frame.f_locals["intent"].request_key, frame.f_locals["ticket"])
                             if event == "call" else arg)
                    events.put(("CONSUME_" + event.upper(), value))

        try:
            sys.setprofile(profile)
            sys.settrace(trace)
            result = api.reserve_order(ROUTE, order())
            results.put((label, result.outcome))
            if not touched:
                problems.put(label + " did not reach expected line")
        except BaseException as error:
            problems.put(label + ":" + type(error).__name__)
        finally:
            sys.settrace(old_trace)
            sys.setprofile(old_profile)
            if sys.gettrace() is not old_trace or sys.getprofile() is not old_profile:
                problems.put(label + " trace/profile not restored")

    thread_a = threading.Thread(target=worker, args=("A",), name="synthetic-risk-A", daemon=True)
    thread_b = threading.Thread(target=worker, args=("B",), name="synthetic-risk-B", daemon=True)
    try:
        thread_a.start()
        assert a_held.wait(6), "A did not enter real locked section"
        thread_b.start()
        assert b_contended.wait(6), "B did not observe the original lock held"
    finally:
        release_a.set()
        thread_a.join(timeout=6)
        if thread_b.ident is not None:
            thread_b.join(timeout=6)
    assert not thread_a.is_alive() and not thread_b.is_alive(), "synthetic worker timeout"
    assert problems.empty(), list(problems.queue) if hasattr(problems, "queue") else "worker error"
    assert sorted((results.get_nowait(), results.get_nowait())) == [("A", "UNKNOWN"), ("B", "UNKNOWN")]
    records = []
    while not events.empty():
        records.append(events.get_nowait())
    labels = [item[0] for item in records]
    assert labels.index("A_HELD_BEFORE_INTENT") < labels.index("B_OBSERVED_ORIGINAL_LOCK_CONTENDED")
    assert labels.count("CREATE_CALL") == labels.count("CREATE_RETURN") == 1
    assert labels.count("CONSUME_CALL") == labels.count("CONSUME_RETURN") == 1
    assert next(value for name, value in records if name == "CREATE_CALL") == KEY
    created_ticket = next(value for name, value in records if name == "CREATE_RETURN")
    consumed_key, consumed_ticket = next(value for name, value in records if name == "CONSUME_CALL")
    assert created_ticket is not None and consumed_key == KEY and consumed_ticket is created_ticket
    assert next(value for name, value in records if name == "CONSUME_RETURN") is True
    assert set(host.intents) == {KEY} and host.load_intent(KEY) is not None
    assert host._risk_tickets == {} and KEY in api._risk_uncertain_keys and KEY not in api._risk_permits
    assert wire.calls == [] and len(wire.replies) == 1 and direct == []
    followup = (api.dispatch_order(ROUTE, KEY) if case == "dispatch"
                else api.release_order(ROUTE, KEY, operation_id="release-1"))
    assert followup.outcome == "UNKNOWN" and wire.calls == [] and direct == []
    assert blocked == [] and api._lock is real_lock
    patcher.undo()
    faulthandler.cancel_dump_traceback_later()
    result = {"case": case, "result": "PASS", "events": labels, "wire_calls": len(wire.calls),
              "direct_calls": len(direct), "blocked_events": blocked,
              "audit_self_test": audit_self_test,
              "non_daemon_threads": [item.name for item in threading.enumerate()
                                     if not item.daemon and item is not threading.current_thread()]}
    assert result["non_daemon_threads"] == []
    print("SYNTHETIC_RESULT " + json.dumps(result, sort_keys=True), flush=True)


@pytest.mark.parametrize("case", sorted(_CASES))
def test_same_key_observes_original_sdk_lock_contention(tmp_path, case):
    """Observe a failed try-lock at the actual line, not blocking acquisition."""
    conda, ps, prefix, interpreter = _runtime_or_skip()
    token = "iter64-lock-contention-" + uuid.uuid4().hex
    marker, log = tmp_path / "child-identity.json", tmp_path / "child.log"
    source_paths = [ROOT, ROOT / "bt_api/bt_api_base/src", ROOT / "bt_api/bt_api_risk/src",
                    ROOT / "bt_api/bt_api_risk/tests", ROOT / "bt_api/bt_api_execution/src",
                    ROOT / "bt_api/bt_api_gateway/src", ROOT / "bt_api/bt_api_transport_zmq/src"]
    executable_dirs = [str(Path(sys.executable).parent), str(Path(conda).parent), os.defpath]
    env = {"HOME": os.environ["HOME"], "PATH": os.pathsep.join(executable_dirs),
           "LANG": "en_US.UTF-8", "CONDARC": os.devnull, "CONDA_NO_PLUGINS": "true",
           "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONDONTWRITEBYTECODE": "1",
           "SKIP_LIVE_TESTS": "1", "PYTHONPATH": os.pathsep.join(map(str, source_paths))}
    command = [conda, "run", "--no-capture-output", "-n", "base", "python", str(Path(__file__).resolve()),
               "--synthetic-child", case, token, str(marker), prefix, interpreter]
    proof = {"case": case, "token": token, "parent_pid": os.getpid(), "parent_ppid": os.getppid(),
             "parent_pgid": os.getpgrp(), "parent_sid": os.getsid(0), "cleanup": "unknown",
             "cleanup_scope": "observed process group and recorded child PIDs, not the entire session",
             "identity_limit": "process-group inspection and later signaling have a TOCTOU gap"}
    with log.open("xb", buffering=0) as stream:
        proc = subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
        proof.update({"wrapper_pid": proc.pid, "wrapper_pgid": os.getpgid(proc.pid),
                      "wrapper_sid": os.getsid(proc.pid)})
        print("TEST_PROCESS_CREATED " + json.dumps(proof, sort_keys=True), flush=True)
        if proof["wrapper_pgid"] != proc.pid or proof["wrapper_sid"] != proc.pid:
            proof["cleanup"] = "cleanup_unknown; no signal sent"
            (tmp_path / "process-proof.json").write_text(
                json.dumps(proof, indent=2, sort_keys=True), encoding="utf-8"
            )
            pytest.fail("unexpected process identity; cleanup_unknown; no signal sent")
        try:
            proof["wrapper_exit"] = proc.wait(timeout=45)
        except subprocess.TimeoutExpired:
            try:
                members = _group_members(proc.pid, ps)
                proof["timeout_members"] = members
                if _known_own_group(proc, marker, token, members):
                    proof["signal"] = "SIGTERM best-effort verified synthetic test group"
                    os.killpg(proc.pid, signal.SIGTERM)
                    with suppress(subprocess.TimeoutExpired):
                        proof["wrapper_exit"] = proc.wait(timeout=5)
                    proof["remaining_group_members"] = _group_members(proc.pid, ps)
                    # A timeout is never a successful cleanup proof: PID and
                    # group observations can race with exit/reuse, and the
                    # entire session was not enumerated.
                    proof["cleanup"] = "cleanup_unknown"
                else:
                    proof["cleanup"] = "cleanup_unknown; no signal sent"
            except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
                proof["cleanup"] = "cleanup_unknown; identity or cleanup check failed"
                proof["cleanup_error_type"] = type(error).__name__
            (tmp_path / "process-proof.json").write_text(json.dumps(proof, indent=2, sort_keys=True), encoding="utf-8")
            pytest.fail("synthetic process timeout; " + proof["cleanup"])
    proof["child_identity"] = json.loads(marker.read_text(encoding="utf-8")) if marker.is_file() else None
    proof["remaining_group_members"] = _group_members(proc.pid, ps)
    identity = proof["child_identity"]
    proof["recorded_child_pids_absent"] = bool(
        identity and all(type(identity.get(key)) is int and _pid_absent(identity[key]) for key in ("pid", "ppid"))
    )
    proof["cleanup"] = ("observed_group_empty_and_child_pids_absent" if proc.poll() is not None
                        and not proof["remaining_group_members"] and proof["recorded_child_pids_absent"]
                        else "cleanup_unknown")
    payload = log.read_text(encoding="utf-8", errors="strict")
    proof["log_sha256"] = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    (tmp_path / "process-proof.json").write_text(json.dumps(proof, indent=2, sort_keys=True), encoding="utf-8")
    print("TEST_PROCESS_PROOF " + json.dumps(proof, sort_keys=True), flush=True)
    assert proof["wrapper_exit"] == 0, payload
    assert proof["cleanup"] == "observed_group_empty_and_child_pids_absent"
    assert identity is not None and identity["token"] == token
    assert identity["pgid"] == proc.pid and identity["sid"] == proc.pid
    assert "ISOLATION_ARMED_BEFORE_PRODUCT_IMPORT" in payload
    rows = re.findall(r"^SYNTHETIC_RESULT (.+)$", payload, flags=re.MULTILINE)
    assert len(rows) == 1
    result = json.loads(rows[0])
    assert result["case"] == case and result["result"] == "PASS"
    assert result["blocked_events"] == result["non_daemon_threads"] == []
    assert result["audit_self_test"] == [
        "socket.connect", "socket.sendto", "subprocess.Popen", "os.fork", "os.exec",
    ]


if __name__ == "__main__" and len(sys.argv) == 7 and sys.argv[1] == "--synthetic-child":
    _child(sys.argv[2], sys.argv[3], Path(sys.argv[4]), sys.argv[5], sys.argv[6])
