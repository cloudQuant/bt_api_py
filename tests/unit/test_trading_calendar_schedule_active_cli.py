"""Read-only CLI coverage for the existing active schedule inspection."""

import json
import subprocess
import sys

import pytest

from bt_api_py import trading_calendar_schedule_once as schedule
from tests.unit.test_trading_calendar_schedule_once import (
    _schedule_subprocess,
    _snapshot_tree,
    execute,
    setup_root,
)


def inspect_cli(root, capsys):
    rc = schedule.main(["--action", "inspect-active", "--root", str(root), "--trusted-local-posix"])
    output = capsys.readouterr()
    assert output.err == ""
    return rc, json.loads(output.out)


def test_active_pending_unknown_and_none_are_read_only(tmp_path, capsys):
    manual = tmp_path / "manual"
    manual.mkdir()
    root, env, _, path = setup_root(manual, mode="manual")
    pending = execute(env, _schedule_subprocess(env, path))
    assert pending["status"] == "PENDING_REVIEW"
    before = _snapshot_tree(root)
    rc, result = inspect_cli(root, capsys)
    assert rc == 10 and result["status"] == "PENDING_REVIEW"
    assert result["operation_id"] == pending["operation_id"]
    assert result["source_authentication"] == "NOT_PROVEN"
    assert result["state_freshness"] == "NOT_GUARANTEED_AFTER_LOCK_RELEASE"
    assert _snapshot_tree(root) == before
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "bt_api_py.trading_calendar_schedule_once",
            "--action",
            "inspect-active",
            "--root",
            str(root),
            "--trusted-local-posix",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert process.returncode == 10 and not process.stderr
    actual = json.loads(process.stdout)
    assert actual["status"] == "PENDING_REVIEW"
    assert actual["operation_id"] == pending["operation_id"]
    assert _snapshot_tree(root) == before
    observation = (
        root
        / "akshare-observations"
        / ("result-" + pending["identity_bundle"]["observations"][0]["observation_id"] + ".json")
    )
    observation.unlink()
    before = _snapshot_tree(root)
    rc, result = inspect_cli(root, capsys)
    assert rc == 25 and result["status"] == "UNKNOWN"
    assert result["operation_id"] == pending["operation_id"] and _snapshot_tree(root) == before

    automatic = tmp_path / "auto"
    automatic.mkdir()
    root, env, _, path = setup_root(automatic, mode="auto")
    assert execute(env, _schedule_subprocess(env, path))["status"] == "COMMITTED"
    before = _snapshot_tree(root)
    rc, result = inspect_cli(root, capsys)
    assert rc == 0 and result["status"] == "NONE"
    assert result["state_freshness"] == "NOT_GUARANTEED_AFTER_LOCK_RELEASE"
    assert _snapshot_tree(root) == before


@pytest.mark.parametrize(
    "args",
    [
        ["--action", "SENSITIVE_ACTION", "--root", "/SENSITIVE_PATH"],
        ["--action", "inspect-active", "--root", "/SENSITIVE_PATH", "--config", "SENSITIVE"],
        ["--action", "inspect-active", "--root", "/SENSITIVE_PATH", "--operation-id", "SENSITIVE"],
        ["--action", "inspect-active", "--root", "/SENSITIVE_PATH", "--config", ""],
        ["--action", "inspect-active", "--root", "/SENSITIVE_PATH", "--operation-id", ""],
        ["--action", "inspect-active", "--root"],
        ["--action", "inspect-active", "--root", "/SENSITIVE_PATH", "--SENSITIVE_OPTION"],
        ["--action", "run", "--config", "SENSITIVE", "--root", "/SENSITIVE_PATH"],
        ["--action", "inspect", "--root", "/SENSITIVE_PATH"],
    ],
)
def test_invalid_arguments_are_redacted_and_do_not_dispatch(args, monkeypatch, capsys):
    def forbidden(**kwargs):
        pytest.fail("invalid arguments dispatched")

    for name in ("inspect_active", "run_once", "inspect", "reconcile"):
        monkeypatch.setattr(schedule, name, forbidden)
    with pytest.raises(SystemExit) as error:
        schedule.main([*args, "--trusted-local-posix"])
    assert error.value.code == 2
    output = capsys.readouterr()
    assert "SENSITIVE" not in output.out + output.err
    assert output.out == "" and output.err == "invalid arguments\n"


def test_help_and_existing_exit_codes(monkeypatch, capsys):
    with pytest.raises(SystemExit) as error:
        schedule.main(["--help"])
    assert error.value.code == 0 and "--action" in capsys.readouterr().out
    monkeypatch.setattr(schedule, "run_once", lambda **kwargs: {"status": "NONE"})
    assert schedule.main(["--config", "/unused", "--trusted-local-posix"]) == 25
    assert json.loads(capsys.readouterr().out)["status"] == "NONE"
    monkeypatch.setattr(schedule, "inspect", lambda **kwargs: {"status": "PENDING_REVIEW"})
    assert (
        schedule.main(
            [
                "--action",
                "inspect",
                "--root",
                "/unused",
                "--operation-id",
                "id",
                "--trusted-local-posix",
            ]
        )
        == 10
    )
    assert json.loads(capsys.readouterr().out)["status"] == "PENDING_REVIEW"
    monkeypatch.setattr(schedule, "reconcile", lambda **kwargs: {"status": "COMMITTED"})
    assert (
        schedule.main(
            [
                "--action",
                "reconcile",
                "--root",
                "/unused",
                "--operation-id",
                "id",
                "--trusted-local-posix",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["status"] == "COMMITTED"
