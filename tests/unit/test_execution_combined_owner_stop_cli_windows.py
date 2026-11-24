"""Windows-only synthetic proof of cooperative combined-host CLI shutdown."""

import json
import os
import queue
import re
import stat
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from bt_api_py._win32_security import _set_owner_only_acl, inspect_owner
from tests.unit.test_execution_combined_service_host import seed, setup_combined


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows owner-stop CLI proof")
_EOF = object()


def _drain_lines(stream, output):
    try:
        for line in iter(stream.readline, ""):
            output.put(line.rstrip("\r\n"))
    finally:
        output.put(_EOF)


def _next_line(output, process, *, timeout=20):
    try:
        line = output.get(timeout=timeout)
    except queue.Empty as error:
        raise AssertionError("combined CLI did not report its next event") from error
    if line is _EOF:
        raise AssertionError(f"combined CLI exited early with code {process.poll()}")
    return line


def _remaining_lines(output):
    lines = []
    while not output.empty():
        line = output.get_nowait()
        if line is not _EOF:
            lines.append(line)
    return lines


def _create_owner_stop_marker(path):
    with path.open("xb"):
        pass
    _set_owner_only_acl(path)


def test_combined_cli_exits_after_protected_owner_stop_marker(tmp_path):
    raw, _, _, _ = setup_combined(tmp_path)
    seed(raw)
    config_path = tmp_path / "combined.json"
    config_path.write_text(json.dumps(raw), encoding="utf-8")
    _set_owner_only_acl(config_path)

    command = [
        sys.executable,
        "-u",
        "-m",
        "bt_api_py.execution_combined_service_host",
        "run",
        "--config",
        str(config_path),
        "--enable-owner-stop-file",
    ]
    process = subprocess.Popen(
        command,
        cwd=str(Path(__file__).resolve().parents[2]),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="strict",
    )
    stdout_lines, stderr_lines = queue.Queue(), queue.Queue()
    readers = [
        threading.Thread(target=_drain_lines, args=(process.stdout, stdout_lines), daemon=True),
        threading.Thread(target=_drain_lines, args=(process.stderr, stderr_lines), daemon=True),
    ]
    for reader in readers:
        reader.start()

    stop_path = None
    observed = []
    try:
        parent_ready = _next_line(stdout_lines, process)
        management_ready = _next_line(stdout_lines, process)
        stop_announcement = _next_line(stdout_lines, process)
        observed.extend((parent_ready, management_ready, stop_announcement))
        assert parent_ready == "EXECUTION_COMBINED_PARENT_READY"
        assert management_ready == "EXECUTION_COMBINED_MANAGEMENT_READY"

        prefix = "EXECUTION_COMBINED_STOP_FILE "
        assert stop_announcement.startswith(prefix), stop_announcement
        stop_name = stop_announcement[len(prefix):]
        assert re.fullmatch(r"execution-[0-9a-f]{32}\.stop", stop_name), stop_name
        stop_path = config_path.parent / stop_name
        assert stop_path.parent == config_path.resolve().parent
        assert not stop_path.exists()

        _create_owner_stop_marker(stop_path)
        marker_stat = stop_path.lstat()
        owner = inspect_owner(stop_path)
        assert stat.S_ISREG(marker_stat.st_mode)
        assert not stop_path.is_symlink()
        assert marker_stat.st_size == 0 and stop_path.read_bytes() == b""
        assert owner.is_current_user and not owner.others_writable

        assert process.wait(timeout=20) == 0
        for reader in readers:
            reader.join(timeout=5)
            assert not reader.is_alive()
        tail = _remaining_lines(stdout_lines)
        stderr = _remaining_lines(stderr_lines)
        observed.extend(tail)
        assert observed == [
            "EXECUTION_COMBINED_PARENT_READY",
            "EXECUTION_COMBINED_MANAGEMENT_READY",
            stop_announcement,
            "EXECUTION_COMBINED_STOPPED",
        ]
        if stderr:
            warning, source = stderr
            assert "zmq/_future.py:" in warning.replace("\\", "/")
            assert "RuntimeWarning: Proactor event loop does not implement add_reader" in warning
            assert "Registering an additional selector thread for add_reader support" in warning
            assert source.strip() == "self._get_loop()"
    finally:
        # Failure cleanup is limited to the exact synthetic child and its
        # validated marker inside this test's temporary configuration folder.
        if process.poll() is None:
            if stop_path is not None and stop_path.parent == config_path.parent:
                if not stop_path.is_symlink():
                    try:
                        if not stop_path.exists():
                            _create_owner_stop_marker(stop_path)
                        process.wait(timeout=5)
                    except (OSError, subprocess.TimeoutExpired):
                        pass
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
        for reader in readers:
            reader.join(timeout=5)
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()
