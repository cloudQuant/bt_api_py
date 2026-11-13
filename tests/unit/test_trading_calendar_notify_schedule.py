"""Offline integration of schedule-owned review discovery and notifications."""

import fcntl
import json
import os
from datetime import UTC, datetime, timedelta

import pytest

from bt_api_py import trading_calendar_notify_cli as notify
from bt_api_py import trading_calendar_schedule_once as schedule
from bt_api_py.trading_calendar_notifications import BaseEmailReceipt, EmailConfig
from bt_api_py.trading_calendar_store import TradingCalendarStore
from tests.unit.test_trading_calendar_mock_cli_integration import command
from tests.unit.test_trading_calendar_mock_persistent import operation
from tests.unit.test_trading_calendar_schedule_once import _schedule_subprocess, execute, setup_root


def config():
    return EmailConfig(
        enabled=True,
        host="smtp.invalid",
        port=587,
        timeout=2,
        sender="sender@example.invalid",
        recipients=("to@example.invalid",),
    )


def manual_root(tmp_path):
    root, env, _, path = setup_root(tmp_path, mode="manual")
    first = execute(env, _schedule_subprocess(env, path))
    assert first["status"] == "PENDING_REVIEW", first
    return root, env, first


def notify_once(root, tmp_path, calls, *, day=13, **kwargs):
    def sender(value):
        calls.append(value.body)
        return BaseEmailReceipt(value.recipients, {})

    params = {
        "root": str(root),
        "config": config(),
        "journal_path": tmp_path / "notify.sqlite",
        "sender": sender,
        "clock_instant": datetime(2026, 11, day, 4, tzinfo=UTC),
        "clock_error_bound": timedelta(seconds=1),
        "clock_evidence": "PROVEN",
    }
    params.update(kwargs)
    return notify.run_scheduled(**params)


def confirm(env, root, pending):
    ids = pending["identity_bundle"]
    rc, rows, err = command(
        env,
        *operation(
            root,
            ids["run_id"],
            "mock-confirm",
            "--review-id",
            ids["review_id"],
            "--review-sha256",
            pending["review_payload_sha256"],
            "--reviewer",
            "MOCK_reviewer",
            "--confirmed-at",
            "2026-11-13T00:00:00.000000Z",
        ),
    )
    assert rc == 0 and rows[-1]["status"] == "COMMITTED", (rows, err)


def test_scheduled_dry_run_dedup_next_day_and_confirm(tmp_path):
    root, env, first = manual_root(tmp_path)
    calls = []
    dry = notify_once(root, tmp_path, calls, dry_run=True)
    assert dry.reason == "DRY_RUN_ELIGIBLE" and not calls
    assert not (tmp_path / "notify.sqlite").exists()
    assert notify_once(root, tmp_path, calls).status == "ACCEPTED"
    assert notify_once(root, tmp_path, calls).status == "DUPLICATE"
    assert notify_once(root, tmp_path, calls, day=14).status == "ACCEPTED"
    assert len(calls) == 2 and all("SIMULATION_ONLY" in text for text in calls)
    confirm(env, root, first)
    assert notify_once(root, tmp_path, calls, day=15).status == "NOT_SENT"
    assert len(calls) == 2 and len((tmp_path / "calls").read_text().splitlines()) == 2


@pytest.mark.parametrize("status", ["NONE", "UNKNOWN", "COMMITTED"])
def test_schedule_nonpending_status_never_sends(tmp_path, monkeypatch, status):
    monkeypatch.setattr(schedule, "inspect_active", lambda **kw: {"status": status})
    calls = []
    result = notify_once(tmp_path / "not-a-root", tmp_path, calls)
    assert result.status == "NOT_SENT" and not calls
    assert not (tmp_path / "notify.sqlite").exists()


@pytest.mark.parametrize("field", ["operation_id", "run_id", "review_id", "review_payload_sha256"])
def test_schedule_final_check_rejects_identity_change(tmp_path, monkeypatch, field):
    root, _, _ = manual_root(tmp_path)
    original = schedule.inspect_active
    observed = []

    def changed(**kwargs):
        value = original(**kwargs)
        observed.append(value)
        if len(observed) == 3:
            value = dict(value)
            value[field] = "f" * (64 if field == "review_payload_sha256" else 32)
        return value

    monkeypatch.setattr(schedule, "inspect_active", changed)
    calls = []
    result = notify_once(root, tmp_path, calls)
    assert result.status == "NOT_SENT" and len(observed) == 3 and not calls


@pytest.mark.parametrize("missing", ["review", "observation"])
def test_schedule_deleted_evidence_never_sends(tmp_path, missing):
    root, _, first = manual_root(tmp_path)
    ids = first["identity_bundle"]
    path = (
        root / "review-bundles" / ("mock-review-" + ids["review_id"] + ".json")
        if missing == "review"
        else root
        / "akshare-observations"
        / ("result-" + ids["observations"][0]["observation_id"] + ".json")
    )
    path.unlink()
    calls = []
    assert notify_once(root, tmp_path, calls).status == "NOT_SENT" and not calls


def test_schedule_final_discovery_runs_outside_review_and_store_locks(tmp_path, monkeypatch):
    root, _, first = manual_root(tmp_path)
    original = schedule.inspect_active
    calls = []
    inspected = []
    review_id = first["identity_bundle"]["review_id"]

    def check_locks(**kwargs):
        if inspected:
            with TradingCalendarStore(root)._writer_lock():
                pass
            path = root / "review-bundles" / (".mock-confirm-" + review_id + ".lock")
            fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
        inspected.append(1)
        return original(**kwargs)

    monkeypatch.setattr(schedule, "inspect_active", check_locks)
    result = notify_once(root, tmp_path, calls)
    assert result.status == "ACCEPTED" and len(calls) == 1 and len(inspected) == 3


def test_schedule_cli_explicit_mode_and_legacy_requirements(tmp_path, capsys):
    root, _, _ = manual_root(tmp_path)
    base = ["--calendar-root", str(root), "--trusted-local-posix"]
    assert notify.main(base + ["--schedule-active", "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["reason"] == "DRY_RUN_ELIGIBLE"
    assert notify.main(base + ["--schedule-active"]) == 1
    assert json.loads(capsys.readouterr().out)["reason"] == "DISABLED"
    for extra in (["--schedule-active", "--run-id", "x"], ["--review-id", "x"]):
        assert notify.main(base + extra) == 1
        assert json.loads(capsys.readouterr().out)["reason"] == "ARGUMENT_OR_CONFIG_UNAVAILABLE"


@pytest.mark.parametrize("change", ["policy", "supplement", "current"])
def test_scheduled_changed_dependencies_never_send(tmp_path, change):
    root, _, _ = manual_root(tmp_path)
    if change == "policy":
        (tmp_path / "config.json").write_text(
            json.dumps({"calendars": {"root": str(root), "review_mode": "auto"}})
        )
    elif change == "supplement":
        (root / "supplement.yaml").write_text("{}")
    else:
        (root / "CURRENT.json").unlink()
    calls = []
    assert notify_once(root, tmp_path, calls).status == "NOT_SENT" and not calls


def test_schedule_final_discovery_exception_never_sends(tmp_path, monkeypatch):
    root, _, _ = manual_root(tmp_path)
    original = schedule.inspect_active
    count = []

    def unavailable(**kwargs):
        count.append(1)
        if len(count) == 3:
            raise OSError("private failure")
        return original(**kwargs)

    monkeypatch.setattr(schedule, "inspect_active", unavailable)
    calls = []
    result = notify_once(root, tmp_path, calls)
    assert result.status == "NOT_SENT" and len(count) == 3 and not calls
    assert "private" not in result.reason


def test_schedule_final_guard_rechecks_actual_observation(tmp_path, monkeypatch):
    root, _, first = manual_root(tmp_path)
    original = notify._manual
    observations = []
    oid = first["identity_bundle"]["observations"][0]["observation_id"]
    result_path = root / "akshare-observations" / ("result-" + oid + ".json")

    def remove_after_last_manual(*args):
        result = original(*args)
        observations.append(1)
        if len(observations) == 3:
            result_path.unlink()
        return result

    monkeypatch.setattr(notify, "_manual", remove_after_last_manual)
    calls = []
    result = notify_once(root, tmp_path, calls)
    assert len(observations) == 3 and not result_path.exists()
    assert result.status == "NOT_SENT" and not calls
