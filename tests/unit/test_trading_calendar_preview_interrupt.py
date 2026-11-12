"""Signal behavior using isolated interpreters and test-owned children only."""

import json
import os
import signal
import subprocess
import sys
import time

import pytest

from tests.unit.test_trading_calendar_observation_run import cli, env_without_network, show
from tests.unit.test_trading_calendar_preview_catalog import setup_preview

_SCRIPT = r"""
import os, signal, subprocess, sys, threading, time
from bt_api_py import trading_calendar_preview as preview
mode = sys.argv[1]
real_popen = subprocess.Popen
real_killpg = os.killpg
calls = []
original_handler = signal.getsignal(signal.SIGINT)

def reject(*args, **kwargs):
    raise AssertionError("unsupported host started a worker")

if mode in ("ignored", "handler", "custom_int", "thread", "multithread"):
    if mode == "ignored": signal.signal(signal.SIGCHLD, signal.SIG_IGN)
    if mode == "handler": signal.signal(signal.SIGCHLD, lambda *_: None)
    if mode == "custom_int": signal.signal(signal.SIGINT, lambda *_: None)
    preview.subprocess.Popen = reject
    results = []
    def run(): results.append(preview._supervise({"budget_seconds": 1}))
    if mode == "thread":
        t = threading.Thread(target=run); t.start(); t.join()
    elif mode == "multithread":
        event = threading.Event()
        t = threading.Thread(target=event.wait); t.start()
        try: run()
        finally: event.set(); t.join()
    else: run()
    assert results[0].reason == "SUPERVISOR_UNSUPPORTED"
elif mode == "reaped":
    class Fake:
        returncode = 0
        def communicate(self, **kwargs):
            os.kill(os.getpid(), signal.SIGINT)
            return b"", b""
    preview.subprocess.Popen = lambda *a, **k: Fake()
    preview.os.killpg = lambda *a: calls.append(a)
    try: preview._supervise({"budget_seconds": 1})
    except KeyboardInterrupt: pass
    else: raise AssertionError("interrupt swallowed")
    assert not calls
    assert signal.getsignal(signal.SIGINT) == original_handler
elif mode in {"restore_window", "restore_after"}:
    class Fake:
        returncode = 0
        def communicate(self, **kwargs): return b"", b""
    preview.subprocess.Popen = lambda *a, **k: Fake()
    preview.os.killpg = lambda *a: calls.append(a)
    real_signal = signal.signal
    def during_restore(signum, handler):
        restoring = signum == signal.SIGINT and handler is original_handler
        if restoring and mode == "restore_window":
            os.kill(os.getpid(), signal.SIGINT)
        value = real_signal(signum, handler)
        if restoring and mode == "restore_after":
            os.kill(os.getpid(), signal.SIGINT)
        return value
    preview.signal.signal = during_restore
    try: preview._supervise({"budget_seconds": 1})
    except KeyboardInterrupt as exc:
        assert exc.worker_cleanup == "WORKER_CLEANUP_UNCERTAIN"
    else: raise AssertionError("interrupt swallowed")
    assert not calls and signal.getsignal(signal.SIGINT) == original_handler
elif mode == "during_popen":
    children = []
    def spawn(command, **kwargs):
        child = real_popen([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
        children.append(child)
        os.kill(os.getpid(), signal.SIGINT)
        return child
    preview.subprocess.Popen = spawn
    try:
        try: preview._supervise({"budget_seconds": 3})
        except KeyboardInterrupt: pass
        else: raise AssertionError("interrupt swallowed")
        assert len(children) == 1
        assert children[0].poll() is not None
        assert signal.getsignal(signal.SIGINT) == original_handler
    finally:
        for child in children:
            if child.poll() is None:
                # Only this subprocess, started in its own group by this test.
                if os.getpgid(child.pid) == child.pid: real_killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=3)
"""


@pytest.mark.parametrize(
    "mode",
    [
        "ignored",
        "handler",
        "custom_int",
        "thread",
        "multithread",
        "reaped",
        "restore_window",
        "restore_after",
        "during_popen",
    ],
)
def test_isolated_supervisor_signal_contract(mode):
    result = subprocess.run(
        [sys.executable, "-c", _SCRIPT, mode], capture_output=True, text=True, timeout=15
    )
    assert result.returncode == 0, result.stderr


def test_run_sigint_during_worker_preserves_intent_and_run_id(tmp_path):
    root, _, config, supplement = setup_preview(tmp_path)
    marker = tmp_path / "worker-pid"
    # The fake child is created in the worker session, with no network or credentials.
    extra = f"""
import os, signal, subprocess, sys
import bt_api_py.trading_calendar_preview as preview
_real_popen = subprocess.Popen
def fake_popen(command, **kwargs):
    if command[1:] == ["-m", "bt_api_py.trading_calendar_preview", "--_worker"]:
        child = _real_popen([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
        with open({str(marker)!r}, "w") as output: output.write(str(child.pid))
        return child
    return _real_popen(command, **kwargs)
preview.subprocess.Popen = fake_popen
"""
    env = env_without_network(tmp_path, extra)
    process = subprocess.Popen(
        cli(root, config, supplement, "--enable-shfe"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    worker_pid = None
    try:
        first = process.stdout.readline()
        rid = json.loads(first)["run_id"]
        deadline = time.monotonic() + 10
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.exists(), "test worker did not start"
        worker_pid = int(marker.read_text())
        os.kill(process.pid, signal.SIGINT)
        out, err = process.communicate(timeout=10)
        assert process.returncode == 23, (out, err)
        event = json.loads(out)
        assert event["run_id"] == rid
        assert event["reason"] == "PREVIEW_INTERRUPTED_CLEANUP_UNKNOWN"
        assert event["intent_installation"] == "COMMITTED"
        assert event["record_installation"] == "NOT_ATTEMPTED"
        assert show(root, rid)["intent"] == "VISIBLE_UNAUTHENTICATED"
        assert show(root, rid)["record"] == "NOT_OBSERVED"
        assert not (root / "CURRENT.json").exists()
        assert all(str(p) not in first + out + err for p in (root, config, supplement))
        # The supervisor waited for this specific test child; PID is no longer live.
        with pytest.raises(ProcessLookupError):
            os.kill(worker_pid, 0)
        worker_pid = None
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
        if worker_pid is not None:
            try:
                # Never signal a group except the still-live test-owned leader.
                if os.getpgid(worker_pid) == worker_pid:
                    os.killpg(worker_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


_HANDLER_SCRIPT = r"""
import json, os, signal, subprocess, sys
from bt_api_py import trading_calendar_preview as preview
mode = sys.argv[1]
original = subprocess.Popen
real_killpg = os.killpg
children, signals = [], []
external = lambda *_: None
preview.os.killpg = lambda pid, sig: signals.append((pid, sig))
def spawn(command, **kwargs):
    if mode in {"completed", "completed_candidate"}:
        fields = {"candidate_id": "a" * 32, "candidate_status": "COMMITTED"} if mode == "completed_candidate" else {}
        report = preview.PreviewReport("INCOMPLETE", "SHFE_NOT_REQUESTED", 10, input_status="CHECKED", input_code="SUPPLEMENT_UNVERIFIED", supplement_status="CANDIDATE", **fields)
        payload = json.dumps(report.to_dict()).encode()
        child = original([sys.executable, "-c", f"import sys; sys.stdout.buffer.write({payload!r}); sys.exit(10)"], **kwargs)
        wait = child.communicate
        def changed(*args, **kw):
            value = wait(*args, **kw)
            signal.signal(signal.SIGINT, external)
            return value
        child.communicate = changed
    else:
        child = original([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
        if mode == "interrupt": os.kill(os.getpid(), signal.SIGINT)
        signal.signal(signal.SIGINT, external)
        if mode == "systemexit":
            def exit_handler(*_): raise SystemExit(37)
            signal.signal(signal.SIGUSR1, exit_handler)
            signal.signal(signal.SIGALRM, lambda *_: os.kill(os.getpid(), signal.SIGUSR1))
            signal.setitimer(signal.ITIMER_REAL, .1)
    children.append(child)
    return child
preview.subprocess.Popen = spawn
try:
    if mode in {"interrupt", "systemexit"}:
        try: preview._supervise({"budget_seconds": 2})
        except BaseException as exc:
            assert type(exc) is (KeyboardInterrupt if mode == "interrupt" else SystemExit)
            assert exc.worker_cleanup == "WORKER_CLEANUP_UNCERTAIN"
            if isinstance(exc, SystemExit): assert exc.code == 37
        else: raise AssertionError("interruption swallowed")
    else:
        result = preview._supervise({"budget_seconds": .3})
        assert result.reason == "WORKER_CLEANUP_UNCERTAIN" and result.exit_code == 23
        if mode == "completed_candidate":
            assert result.candidate_id == "a" * 32 and result.candidate_status == "COMMIT_UNKNOWN", result
            assert result.candidate_reason == "IO_ERROR"
    assert signal.getsignal(signal.SIGINT) is external
    assert not signals
    if mode in {"completed", "completed_candidate"}: assert children[0].poll() is not None
    else: assert children[0].poll() is None
finally:
    signal.setitimer(signal.ITIMER_REAL, 0)
    for child in children:
        if child.poll() is None:
            # The process group was created by this test, not an external service.
            if os.getpgid(child.pid) == child.pid:
                real_killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=3)
"""


@pytest.mark.parametrize(
    "mode", ["timeout", "completed", "completed_candidate", "interrupt", "systemexit"]
)
def test_changed_sigint_handler_is_preserved_and_supervision_unknown(mode):
    result = subprocess.run(
        [sys.executable, "-c", _HANDLER_SCRIPT, mode], capture_output=True, text=True, timeout=15
    )
    assert result.returncode == 0, result.stderr
