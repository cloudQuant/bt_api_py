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
    "mode", ["ignored", "handler", "custom_int", "thread", "multithread", "reaped", "during_popen"]
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
