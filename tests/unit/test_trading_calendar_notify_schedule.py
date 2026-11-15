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
from tests.unit.test_trading_calendar_schedule_once import (
    _schedule_subprocess,
    _snapshot_tree,
    execute,
    setup_root,
)


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


def notify_publication(root, operation_id, tmp_path, calls, *, sender=None, **kwargs):
    def accepted(value):
        calls.append(value.body)
        return BaseEmailReceipt(value.recipients, {})

    return notify.run_scheduled_publication(
        root=str(root),
        operation_id=operation_id,
        config=config(),
        journal_path=tmp_path / "notify.sqlite",
        sender=sender or accepted,
        **kwargs,
    )


def test_scheduled_publication_revalidates_and_deduplicates(tmp_path):
    root, env, _, path = setup_root(tmp_path, mode="auto")
    first = execute(env, _schedule_subprocess(env, path))
    assert first["status"] == "COMMITTED"
    calls = []
    assert notify_publication(root, first["operation_id"], tmp_path, calls).status == "ACCEPTED"
    assert notify_publication(root, first["operation_id"], tmp_path, calls).status == "DUPLICATE"
    assert len(calls) == 1 and "SIMULATION_ONLY" in calls[0]
    next_day = execute(env, _schedule_subprocess(env, path, date_utc="2026-11-15T00:00:00+00:00"))
    assert next_day["status"] == "SAME"
    assert notify_publication(root, next_day["operation_id"], tmp_path, calls).status == "NOT_SENT"
    assert len(calls) == 1 and len((tmp_path / "calls").read_text().splitlines()) == 4
    receipt = root / "receipts" / (first["identity_bundle"]["publication_id"] + ".json")
    receipt.unlink()
    assert notify_publication(root, first["operation_id"], tmp_path, calls).status == "NOT_SENT"
    assert len(calls) == 1


def test_scheduled_publication_unknown_sender_never_retries(tmp_path):
    root, env, _, path = setup_root(tmp_path, mode="auto")
    first = execute(env, _schedule_subprocess(env, path))
    assert first["status"] == "COMMITTED"
    calls = []

    def unknown(_):
        calls.append(1)
        raise OSError("secret /private/path")

    result = notify_publication(root, first["operation_id"], tmp_path, calls, sender=unknown)
    assert result.status == result.journal_status == "UNKNOWN"
    repeated = notify_publication(root, first["operation_id"], tmp_path, calls, sender=unknown)
    assert repeated.status == "DUPLICATE" and repeated.journal_status == "UNKNOWN"
    assert calls == [1] and "secret" not in repr(result)


def test_scheduled_publication_missing_scope_head_never_sends(tmp_path):
    root, env, _, path = setup_root(tmp_path, mode="auto")
    first = execute(env, _schedule_subprocess(env, path))
    assert first["status"] == "COMMITTED"
    (root / "observation-runs" / "scope-check-head.json").unlink()
    repeated = execute(env, _schedule_subprocess(env, path))
    assert repeated["status"] == "UNKNOWN"
    assert repeated["reason"] == "ORIGINAL_TERMINAL_EVIDENCE_UNAVAILABLE"
    calls = []
    result = notify_publication(root, first["operation_id"], tmp_path, calls)
    assert result.status == "NOT_SENT" and not calls
    assert not (tmp_path / "notify.sqlite").exists()


def test_scheduled_publication_scope_head_lost_between_checks_never_sends(tmp_path, monkeypatch):
    root, env, _, path = setup_root(tmp_path, mode="auto")
    first = execute(env, _schedule_subprocess(env, path))
    original = schedule._batch_proof
    head = root / "observation-runs" / "scope-check-head.json"

    def remove_after_proof(*args, **kwargs):
        proof = original(*args, **kwargs)
        head.unlink()
        return proof

    monkeypatch.setattr(schedule, "_batch_proof", remove_after_proof)
    calls = []
    result = notify_publication(root, first["operation_id"], tmp_path, calls)
    assert result.status == "NOT_SENT" and not calls and not head.exists()
    assert not (tmp_path / "notify.sqlite").exists()


def test_scheduled_publication_dry_run_revalidation_is_read_only(tmp_path):
    root, env, _, path = setup_root(tmp_path, mode="auto")
    first = execute(env, _schedule_subprocess(env, path))
    before = _snapshot_tree(root)
    calls = []
    result = notify_publication(root, first["operation_id"], tmp_path, calls, dry_run=True)
    assert result.reason == "DRY_RUN_ELIGIBLE" and not calls
    assert _snapshot_tree(root) == before and not (tmp_path / "notify.sqlite").exists()


def test_scheduled_publication_guard_interrupt_stays_unknown(tmp_path, monkeypatch):
    root, env, _, path = setup_root(tmp_path, mode="auto")
    first = execute(env, _schedule_subprocess(env, path))
    original = notify._revalidate_schedule_publication
    seen = []

    def interrupt(*args):
        seen.append(1)
        if len(seen) == 3:
            raise KeyboardInterrupt()
        return original(*args)

    monkeypatch.setattr(notify, "_revalidate_schedule_publication", interrupt)
    calls = []
    with pytest.raises(KeyboardInterrupt):
        notify_publication(root, first["operation_id"], tmp_path, calls)
    assert len(seen) == 3 and not calls
    monkeypatch.setattr(notify, "_revalidate_schedule_publication", original)
    repeated = notify_publication(root, first["operation_id"], tmp_path, calls)
    assert repeated.status == "DUPLICATE" and repeated.journal_status == "UNKNOWN" and not calls


@pytest.mark.parametrize("change_at", [2, 3])
def test_scheduled_publication_identity_changes_never_send(tmp_path, monkeypatch, change_at):
    root, env, _, path = setup_root(tmp_path, mode="auto")
    first = execute(env, _schedule_subprocess(env, path))
    assert first["status"] == "COMMITTED"
    original = schedule.inspect
    seen = []

    def changed(**kwargs):
        result = original(**kwargs)
        seen.append(1)
        if len(seen) == change_at:
            result = dict(result)
            result["terminal"] = dict(result["terminal"], batch_status="altered")
        return result

    monkeypatch.setattr(schedule, "inspect", changed)
    calls = []
    result = notify_publication(root, first["operation_id"], tmp_path, calls)
    assert result.status == "NOT_SENT" and not calls and len(seen) == change_at


def test_scheduled_publication_manual_and_missing_evidence_never_send(tmp_path):
    root, env, first = manual_root(tmp_path)
    calls = []
    assert notify_publication(root, first["operation_id"], tmp_path, calls).status == "NOT_SENT"
    confirm(env, root, first)
    reconciled = execute(
        env, _schedule_subprocess(env, root / "unused", code="reconcile")
        + [str(root), first["operation_id"]]
    )
    assert reconciled["status"] == "COMMITTED"
    assert notify_publication(root, first["operation_id"], tmp_path, calls).status == "NOT_SENT"
    receipt = root / "receipts" / (first["identity_bundle"]["publication_id"] + ".json")
    receipt.unlink()
    assert notify_publication(root, first["operation_id"], tmp_path, calls).status == "NOT_SENT"
    assert not calls


def test_scheduled_publication_cli_identity_and_argument_handling(tmp_path, capsys):
    root, env, _, path = setup_root(tmp_path, mode="auto")
    first = execute(env, _schedule_subprocess(env, path))
    args = ["--calendar-root", str(root), "--trusted-local-posix"]
    assert notify.main(args + ["--schedule-operation-id", first["operation_id"], "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["reason"] == "DRY_RUN_ELIGIBLE"
    assert notify.main(args + ["--schedule-operation-id", "private-invalid/value", "--dry-run"]) == 1
    output = capsys.readouterr().out
    assert "private-invalid" not in output
    assert json.loads(output)["status"] == "NOT_SENT"
    assert notify.main(args + ["--schedule-operation-id", first["operation_id"], "--run-id", "x"]) == 1
    assert json.loads(capsys.readouterr().out)["reason"] == "ARGUMENT_OR_CONFIG_UNAVAILABLE"


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
