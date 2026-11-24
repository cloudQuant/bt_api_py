"""Cleanup retains the original Monitor owners until close is confirmed."""

import os
import select
import signal
import subprocess
import sys

import pytest

from bt_api_py import monitor_service_host as module
from bt_api_py.monitor_service_config import load_monitor_service_config
from bt_api_py.monitor_service_host import MonitorServiceError, MonitorServiceHost
from bt_api_py.service_host import ServiceHost
from tests.integration.test_monitor_service_process import deployment


def owner(state="started"):
    host = ServiceHost.__new__(ServiceHost)
    host._state = state
    host._last_cleanup_failures = ()
    return host


def test_close_retries_only_unconfirmed_original_owner(monkeypatch):
    first, second = owner(), owner("failed")
    host = MonitorServiceHost(None)
    host._hosts = [first, second]
    calls = []

    def close(item):
        calls.append(item)
        if item is second and calls.count(second) == 1:
            item._state = "close_failed"
            raise RuntimeError("private exception")
        item._state = "closed"
        item._last_cleanup_failures = ()

    monkeypatch.setattr(ServiceHost, "close", close)
    with pytest.raises(MonitorServiceError, match="MONITOR_CLOSE_FAILED"):
        host.close()
    assert host._hosts == [first, second]
    assert host in module._RETAINED_HOSTS
    assert calls == [second, first]
    host.close()
    assert calls == [second, first, second]
    assert host.state == "closed"
    assert host not in module._RETAINED_HOSTS


@pytest.mark.parametrize("bad_close", ["value", "state", "failures", "fake"])
def test_unverified_close_cannot_confirm_owner(monkeypatch, bad_close):
    host = MonitorServiceHost(None)
    item = owner()
    if bad_close == "fake":
        class Fake:
            state = "closed"

            def close(self):
                return None

        item = Fake()
    host._hosts = [item]

    def close(item):
        item._state = "started" if bad_close == "state" else "closed"
        item._last_cleanup_failures = ("unknown",) if bad_close == "failures" else ()
        return False if bad_close == "value" else None

    monkeypatch.setattr(ServiceHost, "close", close)
    with pytest.raises(MonitorServiceError, match="MONITOR_CLOSE_FAILED"):
        host.close()
    assert host.state == "close_failed"
    assert host._hosts == [item]
    assert host in module._RETAINED_HOSTS
    # These fabricated objects did not start resources.
    host._hosts.clear()
    host.close()


def test_partial_start_rollback_retains_resources_and_owners(tmp_path, monkeypatch):
    path, _, _ = deployment(tmp_path)
    host = MonitorServiceHost(load_monitor_service_config(path))
    starts = []
    closes = []

    def start(item):
        starts.append(item)
        item._state = "started"
        if len(starts) == 2:
            item._state = "failed"
            raise RuntimeError("private start detail")

    def close(item):
        closes.append(item)
        if item is starts[1] and closes.count(item) == 1:
            item._state = "close_failed"
            raise RuntimeError("private cleanup detail")
        item._state = "closed"
        item._last_cleanup_failures = ()

    monkeypatch.setattr(ServiceHost, "start", start)
    monkeypatch.setattr(ServiceHost, "close", close)
    with pytest.raises(MonitorServiceError, match="MONITOR_START_CLEANUP_FAILED"):
        host.start()
    assert host.state == "close_failed"
    assert host in module._RETAINED_HOSTS
    assert host._hosts == starts
    assert len(host._resources) == 4
    host.close()
    assert closes == [starts[1], starts[0], starts[1]]
    assert host.state == "closed"
    assert host not in module._RETAINED_HOSTS


def read_line(process):
    assert select.select([process.stdout], [], [], 20)[0], "child output timed out"
    return process.stdout.readline().strip()


def test_owner_stop_requires_fresh_marker_after_unconfirmed_close(tmp_path, monkeypatch, capsys):
    path, _, _ = deployment(tmp_path)

    class Host:
        state = "running"

        def start(self):
            pass

    host = Host()
    attempts, markers = [], []

    def close_confirmed(candidate):
        assert candidate is host
        attempts.append(candidate)
        return len(attempts) == 2

    def marker_requested(marker):
        markers.append(marker)
        if len(markers) == 1:
            return True
        assert marker != markers[0]
        assert attempts == [host] and host in module._RETAINED_HOSTS
        return len(markers) == 3

    monkeypatch.setattr(module, "MonitorServiceHost", lambda config: host)
    monkeypatch.setattr(module, "_close_confirmed", close_confirmed)
    monkeypatch.setattr(module, "marker_requested", marker_requested)
    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)
    assert module.main(["run", "--config", str(path), "--enable-owner-stop-file"]) == 0
    assert len(markers) == 3 and markers[1] == markers[2]
    assert attempts == [host, host] and host not in module._RETAINED_HOSTS
    assert capsys.readouterr().out.splitlines() == [
        "MONITOR_LISTENERS_READY",
        f"MONITOR_STOP_FILE {markers[0].name}",
        "MONITOR_CLEANUP_PENDING",
        f"MONITOR_STOP_FILE {markers[1].name}",
        "MONITOR_STOPPED",
    ]


def test_real_process_retains_owner_and_handlers_until_signal_retry(tmp_path):
    path, _, _ = deployment(tmp_path)
    script = r'''
import signal
import sys
from bt_api_py import monitor_service_host as monitor
from bt_api_py.service_host import ServiceHost
original = ServiceHost.close
attempts = {}
first_owner = None

def close(self):
    global first_owner
    assert signal.getsignal(signal.SIGTERM) not in (signal.SIG_DFL, signal.SIG_IGN)
    assert signal.getsignal(signal.SIGINT) not in (signal.SIG_DFL, signal.SIG_IGN)
    if first_owner is None:
        first_owner = self
    if self is first_owner:
        attempts[self] = attempts.get(self, 0) + 1
        if attempts[self] <= 2:
            self._state = "close_failed"
            if attempts[self] == 2:
                print("TEST_RETRY_FAILED", flush=True)
            raise RuntimeError("TOP_SECRET /private/path identity")
    return original(self)

ServiceHost.close = close
sys.exit(monitor.main(["run", "--config", sys.argv[1]]))
'''
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", script, str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert read_line(process) == "MONITOR_LISTENERS_READY"
        process.send_signal(signal.SIGTERM)
        assert read_line(process) == "MONITOR_CLEANUP_PENDING"
        assert process.poll() is None
        process.send_signal(signal.SIGINT)
        assert read_line(process) == "TEST_RETRY_FAILED"
        assert process.poll() is None
        process.send_signal(signal.SIGTERM)
        output, error = process.communicate(timeout=15)
        assert process.returncode == 0
        assert output.strip() == "MONITOR_STOPPED"
        assert error == ""
        assert "TOP_SECRET" not in output + error
    finally:
        if process.poll() is None:
            # This is solely the synthetic child created by this test.
            os.kill(process.pid, signal.SIGKILL)
            process.wait(timeout=5)


def test_real_process_retains_unknown_start_rollback_until_retry(tmp_path):
    path, _, _ = deployment(tmp_path)
    script = r'''
import sys
from bt_api_py import monitor_service_host as monitor
from bt_api_py.service_host import ServiceHost
original_start = ServiceHost.start
original_close = ServiceHost.close
started = []
failed_owner = None

def start(self):
    result = original_start(self)
    started.append(self)
    if len(started) == 2:
        raise RuntimeError("TOP_SECRET start /private/path")
    return result

def close(self):
    global failed_owner
    if failed_owner is None:
        failed_owner = self
        raise RuntimeError("TOP_SECRET rollback /private/path")
    if self is failed_owner:
        print("TEST_ROLLBACK_RETRIED", flush=True)
    return original_close(self)

ServiceHost.start = start
ServiceHost.close = close
sys.exit(monitor.main(["run", "--config", sys.argv[1]]))
'''
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", script, str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert read_line(process) == "MONITOR_CLEANUP_PENDING"
        assert process.poll() is None
        process.send_signal(signal.SIGTERM)
        assert read_line(process) == "TEST_ROLLBACK_RETRIED"
        output, error = process.communicate(timeout=15)
        assert process.returncode == 1
        assert output.strip() == "MONITOR_FAILED"
        assert error == ""
        assert "TOP_SECRET" not in output + error
    finally:
        if process.poll() is None:
            os.kill(process.pid, signal.SIGKILL)
            process.wait(timeout=5)


def test_aggregate_confirmation_rejects_forged_result_and_missing_owner(monkeypatch):
    host = MonitorServiceHost(None)
    item = owner()
    host._hosts = [item]

    def owner_close(item):
        item._state = "closed"
        item._last_cleanup_failures = ()

    original = MonitorServiceHost.close

    def false_result(item):
        original(item)
        return False

    monkeypatch.setattr(ServiceHost, "close", owner_close)
    monkeypatch.setattr(MonitorServiceHost, "close", false_result)
    assert not module._close_confirmed(host)
    monkeypatch.setattr(MonitorServiceHost, "close", lambda item: None)
    host._closed_hosts.clear()
    assert host.state == "closed"
    assert not module._close_confirmed(host)
    monkeypatch.setattr(MonitorServiceHost, "close", original)
    assert module._close_confirmed(host)
    item._state = "close_failed"
    monkeypatch.setattr(MonitorServiceHost, "close", lambda item: None)
    assert not module._close_confirmed(host)
    monkeypatch.setattr(MonitorServiceHost, "close", original)
    assert module._close_confirmed(host)


def test_closed_diagnostic_stream_does_not_release_pending_process(tmp_path):
    path, _, _ = deployment(tmp_path)
    script = r'''
import builtins
import sys
from bt_api_py import monitor_service_host as monitor
from bt_api_py.service_host import ServiceHost
original_close = ServiceHost.close
failed_owner = None

def close(self):
    global failed_owner
    if failed_owner is None:
        failed_owner = self
        print("TEST_CLOSE_FAILED", flush=True)
        raise RuntimeError("TOP_SECRET")
    if self is failed_owner:
        print("TEST_ORIGINAL_RETRIED", flush=True)
    return original_close(self)

def diagnostic(*args, **kwargs):
    if args and args[0] == "MONITOR_CLEANUP_PENDING":
        raise ValueError("closed stream /private/path")
    return builtins.print(*args, **kwargs)

monitor.print = diagnostic
ServiceHost.close = close
sys.exit(monitor.main(["run", "--config", sys.argv[1]]))
'''
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", script, str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert read_line(process) == "MONITOR_LISTENERS_READY"
        process.send_signal(signal.SIGTERM)
        assert read_line(process) == "TEST_CLOSE_FAILED"
        assert process.poll() is None
        process.send_signal(signal.SIGINT)
        assert read_line(process) == "TEST_ORIGINAL_RETRIED"
        output, error = process.communicate(timeout=15)
        assert process.returncode == 0
        assert output.strip() == "MONITOR_STOPPED"
        assert error == ""
    finally:
        if process.poll() is None:
            os.kill(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
