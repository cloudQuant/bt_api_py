"""Offline CLI checks for the conventional supplement and guarded capture."""

import json
import subprocess

import pytest

from bt_api_py import trading_calendar_observation_run as runs
from bt_api_py import trading_calendar_supplement_capture as captures
from tests.unit.test_trading_calendar_observation_run import cli, env_without_network
from tests.unit.test_trading_calendar_preview_catalog import setup_preview

FS = "TRUSTED_LOCAL_POSIX"


def invoke(tmp_path, root, config, supplement, *flags, default=False, injection=""):
    command = cli(root, config, supplement, *flags)
    if default:
        index = command.index("--supplement")
        del command[index : index + 2]
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=40,
        env=env_without_network(tmp_path, injection),
        check=False,
    )


def observed(root, result, expected_code):
    assert result.returncode == expected_code, (result.stdout, result.stderr)
    allocation, event = [json.loads(line) for line in result.stdout.splitlines()]
    shown = runs.show_once(root=str(root), run_id=allocation["run_id"], filesystem=FS)
    assert shown["record"] == "VISIBLE_UNAUTHENTICATED"
    return allocation, event, shown["observation"]["preview"]


@pytest.mark.parametrize("capture_flag", [False, True])
def test_default_supplement_is_captured_and_current_is_unchanged(tmp_path, capture_flag):
    root, _, config, supplement = setup_preview(tmp_path)
    supplement.rename(root / "supplement.yaml")
    current = root / "CURRENT.json"
    current.write_bytes(b"original-current")
    flags = ("--capture-supplement",) if capture_flag else ()
    result = invoke(tmp_path, root, config, supplement, *flags, default=True)
    allocation, event, preview = observed(root, result, 10)
    assert allocation["capture_id"] != allocation["run_id"]
    assert event["capture_state"] == "COMMITTED"
    assert preview["input_code"] == "SUPPLEMENT_UNVERIFIED"
    capture = captures.read_capture(
        root=str(root), capture_id=allocation["capture_id"], filesystem=FS, include_content=True
    )
    assert capture["status"] == "VISIBLE_UNAUTHENTICATED"
    assert capture["run_id"] == allocation["run_id"] and capture["content"]
    assert current.read_bytes() == b"original-current"


@pytest.mark.parametrize("capture_flag", [False, True])
def test_explicit_supplement_keeps_prior_capture_behavior(tmp_path, capture_flag):
    root, _, config, supplement = setup_preview(tmp_path)
    (root / "supplement.yaml").write_text("invalid: [", encoding="utf-8")
    flags = ("--capture-supplement",) if capture_flag else ()
    allocation, event, preview = observed(
        root, invoke(tmp_path, root, config, supplement, *flags), 10
    )
    assert preview["input_code"] == "SUPPLEMENT_UNVERIFIED"
    assert ("capture_id" in allocation) is capture_flag
    if capture_flag:
        assert event["capture_state"] == "COMMITTED"
    else:
        assert "capture_state" not in event
    assert not (root / "CURRENT.json").exists()


@pytest.mark.parametrize(
    "failure,expected",
    [
        ("missing", "SUPPLEMENT_MISSING"),
        ("invalid", "SUPPLEMENT_INVALID_CONTENT"),
        ("symlink", "SUPPLEMENT_SYMLINK"),
        ("root_mismatch", "CALENDAR_ROOT_MISMATCH"),
    ],
)
def test_default_failure_is_reported_and_never_captured(tmp_path, failure, expected):
    root, _, config, supplement = setup_preview(tmp_path)
    default = root / "supplement.yaml"
    if failure == "invalid":
        default.write_text("not: [valid", encoding="utf-8")
    elif failure == "symlink":
        default.symlink_to(supplement)
    elif failure == "root_mismatch":
        supplement.rename(default)
        config.write_text(f"calendars:\n  root: {tmp_path / 'other'}\n", encoding="utf-8")
    allocation, event, preview = observed(
        root, invoke(tmp_path, root, config, supplement, default=True), 24
    )
    assert preview["input_code"] == expected
    assert event["capture_state"] == "NOT_ATTEMPTED"
    assert not (root / "supplement-captures" / f"capture-{allocation['capture_id']}.json").exists()
    assert not (root / "CURRENT.json").exists()


def test_default_detects_concurrent_edit(tmp_path):
    root, _, config, supplement = setup_preview(tmp_path)
    supplement.rename(root / "supplement.yaml")
    injection = """
from bt_api_ctp import calendar_supplement_reader as reader
original = reader.read_supplement
def changed(path):
    result = original(path)
    with open(path, "ab") as output:
        output.write(b"\\n")
    return result
reader.read_supplement = changed
"""
    allocation, event, preview = observed(
        root, invoke(tmp_path, root, config, supplement, default=True, injection=injection), 24
    )
    assert preview["input_code"] == "SUPPLEMENT_CHANGED"
    assert event["capture_state"] == "NOT_ATTEMPTED"
    assert not (root / "supplement-captures" / f"capture-{allocation['capture_id']}.json").exists()
    assert not (root / "CURRENT.json").exists()


def test_default_requires_absolute_root_without_creating_run(tmp_path, monkeypatch):
    root, _, config, supplement = setup_preview(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = invoke(tmp_path, "calendar", config, supplement, default=True)
    assert result.returncode == 2
    assert json.loads(result.stdout)["reason"] == "INVALID_ARGUMENTS"
    assert not (root / "observation-runs").exists()
