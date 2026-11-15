"""Unsupported platforms fail closed before local state or sender access."""

import json
import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize("platform_case", ["missing_fcntl", "non_posix"])
def test_unsupported_platform_import_public_entries_and_module_cli(tmp_path, platform_case):
    injection = tmp_path / "injection"
    injection.mkdir()
    (injection / "sitecustomize.py").write_text(
        """import builtins, os, socket, smtplib
# Load platform-independent notification results before simulating os.name.
import bt_api_py.trading_calendar_notifications
real_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name == "fcntl" and os.environ["NOTIFY_PLATFORM_CASE"] == "missing_fcntl":
        raise ModuleNotFoundError("disabled for test", name="fcntl")
    if name.startswith("bt_api_py.trading_calendar_") and name not in {
        "bt_api_py.trading_calendar_notifications", "bt_api_py.trading_calendar_notify_cli"
    }:
        raise AssertionError("operational dependency imported")
    return real_import(name, *args, **kwargs)
builtins.__import__ = guarded_import
if os.environ["NOTIFY_PLATFORM_CASE"] == "non_posix":
    os.name = "nt"
def forbidden(*args, **kwargs):
    raise AssertionError("external access attempted")
builtins.open = forbidden
os.open = forbidden
socket.socket = forbidden
smtplib.SMTP = forbidden
"""
    )
    env = {
        **os.environ,
        "NOTIFY_PLATFORM_CASE": platform_case,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": os.pathsep.join((str(injection), os.environ.get("PYTHONPATH", ""))),
    }
    root = str(tmp_path / "SENSITIVE_ROOT")
    journal = str(tmp_path / "SENSITIVE_JOURNAL")
    config = str(tmp_path / "SENSITIVE_CONFIG")
    script = r"""
import json, sys
from dataclasses import asdict
from bt_api_py import trading_calendar_notify_cli as module
root, journal = sys.argv[1:]
def forbidden(*args, **kwargs):
    raise AssertionError("sender called")
common = dict(root=root, journal_path=journal, sender=forbidden, config=object(), dry_run=True)
results = [
    module.run(**common, run_id="SENSITIVE_RUN", kind="manual", event_id="SENSITIVE_EVENT"),
    module.run_scheduled(**common),
    module.run_scheduled_publication(**common, operation_id="SENSITIVE_OPERATION"),
]
print(json.dumps([asdict(result) for result in results]))
"""
    public = subprocess.run(
        [sys.executable, "-c", script, root, journal], env=env, capture_output=True, text=True
    )
    assert public.returncode == 0, public.stderr
    assert (
        json.loads(public.stdout)
        == [{"status": "NOT_SENT", "reason": "UNSUPPORTED_FILESYSTEM", "journal_status": None}] * 3
    )
    assert "SENSITIVE" not in public.stdout + public.stderr
    command = subprocess.run(
        [
            sys.executable,
            "-m",
            "bt_api_py.trading_calendar_notify_cli",
            "--calendar-root",
            root,
            "--schedule-active",
            "--smtp-config",
            config,
            "--journal-path",
            journal,
            "--trusted-local-posix",
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    assert command.returncode == 1, command.stderr
    result = json.loads(command.stdout)
    assert (result["status"], result["reason"]) == ("NOT_SENT", "UNSUPPORTED_FILESYSTEM")
    assert result["journal_status"] is None
    assert "SENSITIVE" not in command.stdout + command.stderr
    assert sorted(p.name for p in tmp_path.iterdir()) == ["injection"]
