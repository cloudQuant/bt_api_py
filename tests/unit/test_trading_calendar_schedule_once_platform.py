"""Offline checks for the schedule entry point without POSIX locking support."""

import json
import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize("platform_case", ["missing_fcntl", "non_posix"])
def test_unsupported_platform_is_importable_and_refuses_without_side_effects(tmp_path, platform_case):
    root = tmp_path / "private-root"
    code = r'''
import builtins
import contextlib
import io
import json
import os
import pathlib
import smtplib
import socket
import sys
import argparse
import zoneinfo
import bt_api_py

real_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name == "fcntl" and sys.argv[1] == "missing_fcntl":
        raise ModuleNotFoundError("disabled for test", name="fcntl")
    if name.startswith("bt_api_py.trading_calendar_") and name != "bt_api_py.trading_calendar_schedule_once":
        raise AssertionError("unsupported platform imported an operational dependency")
    return real_import(name, *args, **kwargs)
builtins.__import__ = guarded_import
if sys.argv[1] == "non_posix":
    os.name = "nt"
def forbidden(*args, **kwargs):
    raise AssertionError("external operation attempted")
socket.socket = forbidden
smtplib.SMTP = forbidden
from bt_api_py import trading_calendar_schedule_once as schedule
root = sys.argv[2]
results = [
    schedule.run_once(config_file=root + "/private-config", filesystem=schedule.FS),
    schedule.inspect(root=root, operation_id="private-id", filesystem=schedule.FS),
    schedule.inspect_active(root=root, filesystem=schedule.FS),
    schedule.reconcile(root=root, operation_id="private-id", filesystem=schedule.FS),
]
cli = []
for action in ("run", "inspect", "reconcile"):
    arguments = ["--trusted-local-posix", "--action", action]
    arguments += (["--config", root + "/private-config"] if action == "run" else
                  ["--root", root, "--operation-id", "private-id"])
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        exit_code = schedule.main(arguments)
    cli.append([exit_code, json.loads(output.getvalue())])
output = io.StringIO()
try:
    with contextlib.redirect_stdout(output):
        schedule.main(["--help"])
except SystemExit as exc:
    assert exc.code == 0
else:
    raise AssertionError("help did not exit")
print(json.dumps({"results": results, "cli": cli}))
'''
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run(
        [sys.executable, "-c", code, platform_case, str(root)],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert [item["status"] for item in data["results"]] == [
        "NOT_COMMITTED", "UNKNOWN", "UNKNOWN", "UNKNOWN"
    ]
    for item in data["results"]:
        assert item["reason"] == "UNSUPPORTED_FILESYSTEM"
    for index, (exit_code, item) in enumerate(data["cli"]):
        assert exit_code == 25
        assert item == data["results"][[0, 1, 3][index]]
    assert "private" not in result.stdout + result.stderr
    assert not root.exists()
