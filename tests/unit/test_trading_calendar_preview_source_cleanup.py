"""Cleanup-uncertain source failures using test-owned synthetic children."""

import builtins
import json
import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from tests.unit.test_trading_calendar_preview import holiday, source
from tests.unit.test_trading_calendar_preview import run as local_run
from tests.unit.test_trading_calendar_preview_catalog import command, run, setup_preview


@pytest.mark.parametrize("failure", ["runtime", "import", "protocol"])
def test_source_exception_or_bad_protocol_stops_other_source(failure):
    calls = []

    def bad(**kwargs):
        if failure == "runtime":
            raise RuntimeError("private")
        if failure == "import":
            raise ImportError("private")
        return SimpleNamespace(status="private")

    report = local_run(
        enable_shfe=True,
        collector=bad,
        holiday_collector=lambda **kwargs: calls.append(True),
    )
    assert report.reason == report.source_status == "CHILD_CLEANUP_UNCERTAIN"
    assert report.attempts[0].parse_status == "UNKNOWN" and not calls
    assert report.holiday_status == "NOT_ATTEMPTED" and not report.holiday_attempts
    assert "private" not in json.dumps(report.to_dict())


@pytest.mark.parametrize("failure", ["runtime", "import", "protocol"])
def test_holiday_exception_preserves_preceding_hours(failure):
    def bad(**kwargs):
        if failure == "runtime":
            raise RuntimeError("private")
        if failure == "import":
            raise ImportError("private")
        return SimpleNamespace(status="private")

    report = local_run(
        enable_shfe=True, collector=lambda **kw: source("HTTP_OK"), holiday_collector=bad
    )
    assert report.reason == report.holiday_status == "CHILD_CLEANUP_UNCERTAIN"
    assert report.source_status == "CANDIDATE" and len(report.attempts) == 1
    assert report.holiday_attempts[0].parse_status == "UNKNOWN"


@pytest.mark.parametrize("error", [SystemExit(41), KeyboardInterrupt("injected")])
def test_library_preserves_baseexception_without_calling_another_source(error):
    calls = []

    def bad(**kwargs):
        raise error

    with pytest.raises(type(error)) as raised:
        local_run(
            enable_shfe=True, collector=bad, holiday_collector=lambda **kw: calls.append(True)
        )
    assert raised.value is error and error.child_cleanup == "CHILD_CLEANUP_UNCERTAIN" and not calls


@pytest.mark.parametrize("error", [SystemExit(41), KeyboardInterrupt("injected")])
def test_candidate_writer_library_preserves_baseexception(error):
    def writer(**kwargs):
        raise error

    with pytest.raises(type(error)) as raised:
        local_run(candidate_id="a" * 32, candidate_writer=writer)
    assert raised.value is error and error.child_cleanup == "CHILD_CLEANUP_UNCERTAIN"


@pytest.mark.parametrize("error", [SystemExit(41), KeyboardInterrupt("injected")])
def test_product_observation_library_preserves_baseexception(monkeypatch, error):
    from bt_api_ctp import calendar_shfe_product_fetch as fetch

    from bt_api_py import trading_calendar_product_fetch_observation as observation

    def bad(**kwargs):
        raise error

    monkeypatch.setattr(fetch, "collect_product_page", bad)
    with pytest.raises(type(error)) as raised:
        observation.fetch_observation("CU_FUTURE", total_timeout=1)
    assert raised.value is error and error.child_cleanup == "CHILD_CLEANUP_UNCERTAIN"


def test_import_failure_before_invocation_is_ctp_unavailable(monkeypatch):
    original = builtins.__import__

    def missing(name, *args, **kwargs):
        if name == "bt_api_ctp.calendar_shfe_fetch":
            raise ImportError("before call")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing)
    report = local_run(
        enable_shfe=True, collector=None, holiday_collector=lambda **kw: holiday("TLS_ERROR")
    )
    assert report.source_status == "CTP_UNAVAILABLE"
    assert report.holiday_status == "TLS_ERROR" and report.reason != "CHILD_CLEANUP_UNCERTAIN"


def test_cleanup_unknown_cannot_be_hidden_by_later_progress_or_clock_errors():
    progress = []

    def emit(report):
        progress.append(report.reason)
        if len(progress) > 2:
            raise RuntimeError("would hide cleanup")

    class Clock:
        calls = 0

        def now(self):
            self.calls += 1
            if self.calls > 2:
                raise RuntimeError("would hide cleanup")
            return 0.0

    report = local_run(
        enable_shfe=True,
        collector=lambda **kw: source("CHILD_CLEANUP_UNCERTAIN"),
        progress=emit,
        monotonic=Clock().now,
    )
    assert report.reason == "CHILD_CLEANUP_UNCERTAIN"
    assert len(progress) == 2 and report.holiday_status == "NOT_ATTEMPTED"


def inject(tmp_path, stage, failure, output_broken):
    folder = tmp_path / "inject-source"
    folder.mkdir()
    (folder / "sitecustomize.py").write_text("""
import os, sys, socket
if "--_worker" in sys.argv:
    import subprocess
    from pathlib import Path
    from types import SimpleNamespace
    import bt_api_ctp.calendar_shfe_fetch as fetch
    marker = Path(os.environ["SOURCE_CHILD_MARKER"])
    calls = Path(os.environ["SOURCE_CALL_MARKER"])
    def record(stage):
        calls.write_text((calls.read_text() if calls.exists() else "") + stage + "\\n")
    def good_hours(**kwargs):
        record("hours")
        page = SimpleNamespace(status="CANDIDATE", verification="UNVERIFIED", products=("synthetic",))
        return SimpleNamespace(status="CANDIDATE", transport_status="HTTP_OK", verification="UNVERIFIED", page=page)
    def bad(stage):
        def collect(**kwargs):
            record(stage)
            child_code = "import time; time.sleep(2)" if os.environ.get("SHORT_LIVED_CHILD") else "import time; time.sleep(30)"
            child = subprocess.Popen([sys.executable, "-c", child_code], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            marker.write_text(str(child.pid) + " " + str(os.getpgid(child.pid)) + " " + str(os.getpgrp()))
            mode = os.environ["SOURCE_FAILURE"]
            if mode == "runtime": raise RuntimeError("after child start")
            if mode == "import": raise ImportError("after child start")
            if mode == "systemexit": raise SystemExit(41)
            if mode == "keyboard": raise KeyboardInterrupt("after child start")
            return SimpleNamespace(status="invalid")
        return collect
    fetch.collect_shfe_page = bad("hours") if os.environ["SOURCE_STAGE"] == "hours" else good_hours
    fetch.collect_shfe_holiday = bad("holiday")
    if os.environ.get("BREAK_TERMINAL_OUTPUT"):
        previous = sys.stdout
        class BrokenTerminal:
            def write(self, text):
                if '"reason": "CHILD_CLEANUP_UNCERTAIN"' in text: raise BrokenPipeError("injected")
                if os.environ.get("DROP_SOURCE_PROGRESS") and '"reason": "IN_PROGRESS"' in text: return len(text)
                return previous.write(text)
            def flush(self): return previous.flush()
            def __getattr__(self, key): return getattr(previous, key)
        sys.stdout = BrokenTerminal()
class NoNetwork(socket.socket):
    def connect(self, *a, **kw): raise AssertionError("no network")
socket.socket = NoNetwork
""")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(folder) + os.pathsep + env.get("PYTHONPATH", "")
    env["SOURCE_CHILD_MARKER"] = str(tmp_path / "child")
    env["SOURCE_CALL_MARKER"] = str(tmp_path / "calls")
    env["SOURCE_STAGE"], env["SOURCE_FAILURE"] = stage, failure
    if output_broken:
        env["BREAK_TERMINAL_OUTPUT"] = "1"
    return env


@pytest.mark.parametrize("stage", ["hours", "holiday"])
@pytest.mark.parametrize("failure", ["runtime", "import", "protocol", "systemexit", "keyboard"])
@pytest.mark.parametrize("output_broken", [False, True])
def test_owned_worker_group_cleanup_after_collector_failure(
    tmp_path, stage, failure, output_broken
):
    root, _, config, supplement = setup_preview(tmp_path)
    env = inject(tmp_path, stage, failure, output_broken)
    cmd = command(root, config, supplement, "--enable-shfe")
    cmd[cmd.index("60")] = "3"
    result = run(cmd, env=env)
    assert result.returncode == 23, (result.stdout, result.stderr)
    report = json.loads(result.stdout)
    assert report["reason"] == "WORKER_CLEANUP_UNCERTAIN"
    calls = (tmp_path / "calls").read_text().splitlines()
    assert calls == (["hours"] if stage == "hours" else ["hours", "holiday"])
    if stage == "holiday":
        assert report["source_status"] == "CANDIDATE"
    pid, child_group, worker_group = map(int, (tmp_path / "child").read_text().split())
    assert child_group == worker_group
    for _ in range(30):
        proc = subprocess.run(
            ["/bin/ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False
        )
        if proc.returncode != 0 or not proc.stdout.strip() or proc.stdout.strip().startswith("Z"):
            break
        time.sleep(0.1)
    else:
        pytest.fail("test-owned provider descendant remained live")
    assert str(root) not in result.stdout + result.stderr
    assert not (root / "CURRENT.json").exists()


def inject_candidate_baseexception(tmp_path, stage, exception, output_broken):
    folder = tmp_path / "inject-candidate"
    folder.mkdir()
    (folder / "sitecustomize.py").write_text("""
import os, sys, socket
if "--_worker" in sys.argv:
    import subprocess
    from pathlib import Path
    import bt_api_ctp.calendar_shfe_product_fetch as fetch
    from bt_api_py import trading_calendar_candidate_inbox as inbox
    def bad(**kwargs):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        Path(os.environ["CHILD_MARKER"]).write_text(str(child.pid) + " " + str(os.getpgid(child.pid)) + " " + str(os.getpgrp()))
        if os.environ["FAIL_KIND"] == "systemexit": raise SystemExit(41)
        raise KeyboardInterrupt("injected")
    if os.environ["FAIL_STAGE"] == "product": fetch.collect_product_page = bad
    else: inbox.retain_candidate = bad
    if os.environ.get("BREAK_TERMINAL_OUTPUT"):
        previous = sys.stdout
        class BrokenTerminal:
            def write(self, text):
                if '"reason": "CHILD_CLEANUP_UNCERTAIN"' in text: raise BrokenPipeError("injected")
                return previous.write(text)
            def flush(self): return previous.flush()
            def __getattr__(self, key): return getattr(previous, key)
        sys.stdout = BrokenTerminal()
class NoNetwork(socket.socket):
    def connect(self, *a, **kw): raise AssertionError("no network")
socket.socket = NoNetwork
""")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(folder) + os.pathsep + env.get("PYTHONPATH", "")
    env["CHILD_MARKER"] = str(tmp_path / "candidate-child")
    env["FAIL_STAGE"], env["FAIL_KIND"] = stage, exception
    if output_broken:
        env["BREAK_TERMINAL_OUTPUT"] = "1"
    return env


@pytest.mark.parametrize("stage", ["product", "candidate"])
@pytest.mark.parametrize("exception", ["systemexit", "keyboard"])
@pytest.mark.parametrize("output_broken", [False, True])
def test_candidate_baseexception_waits_for_parent_cleanup(
    tmp_path, stage, exception, output_broken
):
    root, _, config, supplement = setup_preview(tmp_path)
    env = inject_candidate_baseexception(tmp_path, stage, exception, output_broken)
    flags = ["--retain-candidate", "--trusted-local-posix"]
    if stage == "product":
        flags += ["--product-source", "CU_FUTURE"]
    cmd = command(root, config, supplement, *flags)
    cmd[cmd.index("60")] = "3"
    result = run(cmd, env=env)
    assert result.returncode == 25, (result.stdout, result.stderr)
    report = json.loads(result.stdout)
    assert report["reason"] == "WORKER_CLEANUP_UNCERTAIN"
    assert report["candidate_status"] == "COMMIT_UNKNOWN"
    pid, child_group, worker_group = map(int, (tmp_path / "candidate-child").read_text().split())
    assert child_group == worker_group
    for _ in range(30):
        proc = subprocess.run(
            ["/bin/ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False
        )
        if proc.returncode != 0 or not proc.stdout.strip() or proc.stdout.strip().startswith("Z"):
            break
        time.sleep(0.1)
    else:
        pytest.fail("test-owned provider child remained live")
    assert str(root) not in result.stdout + result.stderr
    assert not (root / "CURRENT.json").exists()


def test_worker_stays_alive_beyond_supervisor_deadline_for_cleanup(tmp_path):
    root, _, config, supplement = setup_preview(tmp_path)
    env = inject(tmp_path, "hours", "runtime", True)
    deadline = time.monotonic() + 1.0
    args = {
        "config_file": str(config),
        "calendar_root": str(root),
        "supplement_file": str(supplement),
        "budget_seconds": 3.0,
        "enable_shfe": True,
        "gateway_enabled": False,
        "direct_write_enabled": False,
        "max_retries": 0,
        "retry_interval_seconds": 0.0,
        "supervisor_deadline": deadline,
    }
    worker = subprocess.Popen(
        [sys.executable, "-m", "bt_api_py.trading_calendar_preview", "--_worker"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=env,
        start_new_session=True,
    )
    child_pid = None
    try:
        worker.stdin.write(json.dumps(args).encode())
        worker.stdin.close()
        marker = tmp_path / "child"
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert marker.exists()
        child_pid, child_group, worker_group = map(int, marker.read_text().split())
        assert child_group == worker_group == worker.pid
        while time.monotonic() < deadline + 0.2:
            time.sleep(0.01)
        assert worker.poll() is None
    finally:
        if worker.poll() is None:
            os.killpg(worker.pid, 9)
            worker.wait(timeout=3)
        if child_pid is not None:
            # This PID belongs to the test-created worker group; no unrelated host is touched.
            for _ in range(30):
                proc = subprocess.run(
                    ["/bin/ps", "-o", "stat=", "-p", str(child_pid)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if (
                    proc.returncode != 0
                    or not proc.stdout.strip()
                    or proc.stdout.strip().startswith("Z")
                ):
                    break
                time.sleep(0.1)
            else:
                pytest.fail("test-owned child remained live")


def test_direct_worker_preserves_systemexit_code_after_bounded_wait(tmp_path):
    root, _, config, supplement = setup_preview(tmp_path)
    env = inject(tmp_path, "hours", "systemexit", False)
    env["SHORT_LIVED_CHILD"] = "1"
    args = {
        "config_file": str(config),
        "calendar_root": str(root),
        "supplement_file": str(supplement),
        "budget_seconds": 3.0,
        "enable_shfe": True,
        "gateway_enabled": False,
        "direct_write_enabled": False,
        "max_retries": 0,
        "retry_interval_seconds": 0.0,
    }
    worker = subprocess.Popen(
        [sys.executable, "-m", "bt_api_py.trading_calendar_preview", "--_worker"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        start_new_session=True,
    )
    pid = None
    try:
        stdout, _ = worker.communicate(input=json.dumps(args).encode(), timeout=10)
        assert worker.returncode == 41
        messages = [json.loads(line) for line in stdout.splitlines()]
        assert any(item["reason"] == "CHILD_CLEANUP_UNCERTAIN" for item in messages)
        pid, child_group, worker_group = map(int, (tmp_path / "child").read_text().split())
        assert child_group == worker_group == worker.pid
        os.kill(pid, 0)  # direct worker never signals its surrounding group
    finally:
        if worker.poll() is None:
            worker.kill()
            worker.wait(timeout=3)
        if pid is not None:
            for _ in range(50):
                proc = subprocess.run(
                    ["/bin/ps", "-o", "stat=", "-p", str(pid)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if (
                    proc.returncode != 0
                    or not proc.stdout.strip()
                    or proc.stdout.strip().startswith("Z")
                ):
                    break
                time.sleep(0.1)
            else:
                pytest.fail("short-lived test child did not exit")


def test_lost_source_progress_and_terminal_output_remain_cleanup_unknown(tmp_path):
    root, _, config, supplement = setup_preview(tmp_path)
    env = inject(tmp_path, "hours", "runtime", True)
    env["DROP_SOURCE_PROGRESS"] = "1"
    cmd = command(root, config, supplement, "--enable-shfe")
    cmd[cmd.index("60")] = "3"
    result = run(cmd, env=env)
    assert result.returncode == 23, (result.stdout, result.stderr)
    report = json.loads(result.stdout)
    assert report["reason"] == "WORKER_CLEANUP_UNCERTAIN" and report["candidate_id"] == ""
    assert (tmp_path / "calls").read_text().splitlines() == ["hours"]
    pid, child_group, worker_group = map(int, (tmp_path / "child").read_text().split())
    assert child_group == worker_group
    for _ in range(30):
        proc = subprocess.run(
            ["/bin/ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False
        )
        if proc.returncode != 0 or not proc.stdout.strip() or proc.stdout.strip().startswith("Z"):
            break
        time.sleep(0.1)
    else:
        pytest.fail("test-owned provider descendant remained live")
    assert not (root / "CURRENT.json").exists()


_COMMUNICATION_SCRIPT = r"""
import os, signal, subprocess, sys, time
from pathlib import Path
from bt_api_py import trading_calendar_preview as preview
mode, marker_path = sys.argv[1:]
marker = Path(marker_path)
original_popen, original_killpg = subprocess.Popen, os.killpg
children, calls = [], []
preview.os.killpg = lambda *args: calls.append(args)
def spawn(command, **kwargs):
    if mode == "alive":
        code = "import os,subprocess,sys,time; from pathlib import Path; c=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); Path(sys.argv[1]).write_text(str(c.pid)+' '+str(os.getpgid(c.pid))); time.sleep(30)"
        child = original_popen([sys.executable, "-c", code, marker_path], **kwargs)
    else:
        child = original_popen([sys.executable, "-c", "pass"], **kwargs)
    children.append(child)
    def failed_communication(*args, **kw):
        if mode == "alive":
            deadline = time.monotonic() + 3
            while not marker.exists() and time.monotonic() < deadline: time.sleep(.01)
            assert marker.exists()
        else:
            child.wait(timeout=3)
            child.returncode = None  # simulate an exception before returncode publication
        raise OSError("injected communication failure")
    child.communicate = failed_communication
    return child
preview.subprocess.Popen = spawn
try:
    report = preview._supervise({"budget_seconds": 3, "enable_shfe": True})
    assert report.reason == "WORKER_CLEANUP_UNCERTAIN" and report.exit_code == 23
    assert not calls
    if mode == "alive":
        assert children[0].poll() is None
        descendant, pgid = map(int, marker.read_text().split())
        assert pgid == children[0].pid
        os.kill(descendant, 0)
    else:
        children[0].returncode = 0
finally:
    for child in children:
        if child.poll() is None:
            # This session leader and descendant are created solely by this test.
            if os.getpgid(child.pid) == child.pid:
                original_killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=3)
"""


@pytest.mark.parametrize("mode", ["alive", "reaped"])
def test_communication_exception_never_signals_an_unconfirmed_group(tmp_path, mode):
    result = subprocess.run(
        [sys.executable, "-c", _COMMUNICATION_SCRIPT, mode, str(tmp_path / "child")],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
