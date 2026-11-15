"""Explicit missed-notification scan and resend for schedule-owned events."""

import json
from datetime import UTC, datetime, timedelta

from bt_api_py import trading_calendar_notify_cli as notify
from bt_api_py import trading_calendar_schedule_once as schedule
from bt_api_py.trading_calendar_notifications import BaseEmailReceipt
from tests.unit.test_trading_calendar_notify_schedule import (
    config,
    confirm,
    manual_root,
)
from tests.unit.test_trading_calendar_schedule_once import (
    _schedule_subprocess,
    execute,
    setup_root,
)


def sender_ok(calls):
    def sender(value):
        calls.append(value.body)
        return BaseEmailReceipt(value.recipients, {})

    return sender


def scan(root, tmp_path, calls, *, day=13, **kwargs):
    params = {
        "root": str(root),
        "config": config(),
        "journal_path": tmp_path / "notify.sqlite",
        "sender": sender_ok(calls),
        "clock_instant": datetime(2026, 11, day, 4, tzinfo=UTC),
        "clock_error_bound": timedelta(seconds=1),
        "clock_evidence": "PROVEN",
    }
    params.update(kwargs)
    return notify.scan_missed(**params)


def auto_root(tmp_path):
    root, env, _, path = setup_root(tmp_path, mode="auto")
    first = execute(env, _schedule_subprocess(env, path))
    assert first["status"] == "COMMITTED", first
    return root, env, path, first


def test_scan_pending_reminder_dedupes_same_beijing_day_and_resends_next_day(tmp_path):
    root, _, _ = manual_root(tmp_path)
    calls = []
    report = scan(root, tmp_path, calls)
    assert report["status"] == "COMPLETE", report
    assert report["pending_review"]["status"] == "SENT", report
    assert report["dedupe"] == "ORIGINAL_ID_AND_BEIJING_DAY"
    assert report["counts"]["sent"] == 1 and len(calls) == 1
    repeated = scan(root, tmp_path, calls)
    assert repeated["pending_review"]["status"] == "ALREADY_SENT"
    assert repeated["counts"]["already_sent"] == 1 and len(calls) == 1
    next_day = scan(root, tmp_path, calls, day=14)
    assert next_day["pending_review"]["status"] == "SENT" and len(calls) == 2
    assert next_day["pending_review"]["event_id"] == report["pending_review"]["event_id"]


def test_scan_dry_run_reports_due_without_sending_or_creating_journal(tmp_path):
    root, _, _ = manual_root(tmp_path)
    calls = []
    report = scan(root, tmp_path, calls, dry_run=True)
    assert report["pending_review"]["status"] == "DUE"
    assert report["status"] == "DUE" and not calls
    assert not (tmp_path / "notify.sqlite").exists()


def test_scan_suppresses_completed_review_and_policy_change(tmp_path, monkeypatch):
    root, env, first = manual_root(tmp_path)
    confirm(env, root, first)
    calls = []
    report = scan(root, tmp_path, calls, day=15)
    assert report["pending_review"]["status"] == "SUPPRESSED"
    assert report["pending_review"]["reason"] == "ORIGINAL_REVIEW_STARTED_OR_COMPLETED"
    assert not calls
    monkeypatch.setattr(
        schedule,
        "inspect_active",
        lambda **kw: {
            "status": "UNKNOWN",
            "reason": "POLICY_CHANGED_REQUIRES_FRESH_UPDATE",
        },
    )
    suppressed = scan(root, tmp_path, calls, day=15)
    assert suppressed["pending_review"]["status"] == "SUPPRESSED"
    assert suppressed["pending_review"]["reason"] == "POLICY_CHANGED_REQUIRES_FRESH_UPDATE"
    assert not calls


def test_scan_auto_publication_summary_sends_once_and_skips_same(tmp_path):
    root, env, path, first = auto_root(tmp_path)
    calls = []
    report = scan(root, tmp_path, calls)
    assert report["status"] == "COMPLETE", report
    assert [item["operation_id"] for item in report["publications"]] == [first["operation_id"]]
    assert report["publications"][0]["status"] == "SENT"
    assert report["counts"]["sent"] == 1 and len(calls) == 1
    same_day = execute(env, _schedule_subprocess(env, path, date_utc="2026-11-15T00:00:00+00:00"))
    assert same_day["status"] == "SAME"
    repeated = scan(root, tmp_path, calls)
    assert [item["operation_id"] for item in repeated["publications"]] == [first["operation_id"]]
    assert repeated["publications"][0]["status"] == "ALREADY_SENT"
    assert len(calls) == 1


def test_scan_reports_send_failure_and_result_unknown_separately(tmp_path):
    root, _, _, first = auto_root(tmp_path)
    calls = []

    def refused(value):
        calls.append(value.body)
        return BaseEmailReceipt((), dict.fromkeys(value.recipients))

    report = notify.scan_missed(
        root=str(root),
        config=config(),
        journal_path=tmp_path / "notify.sqlite",
        sender=refused,
    )
    assert report["status"] == "INCOMPLETE", report
    assert len(report["send_failures"]) == 1
    assert report["send_failures"][0]["event_id"] == first["identity_bundle"]["publication_id"]
    assert report["counts"]["send_failed"] == 1 and len(calls) == 1
    repeated = notify.scan_missed(
        root=str(root),
        config=config(),
        journal_path=tmp_path / "notify.sqlite",
        sender=refused,
    )
    assert len(repeated["send_failures"]) == 1 and len(calls) == 1


def test_scan_unknown_result_is_never_marked_sent_or_retried(tmp_path):
    root, _, _, first = auto_root(tmp_path)
    calls = []

    def unknown(value):
        calls.append(value.body)
        raise OSError("private failure")

    report = notify.scan_missed(
        root=str(root),
        config=config(),
        journal_path=tmp_path / "notify.sqlite",
        sender=unknown,
    )
    assert report["status"] == "UNKNOWN", report
    assert len(report["result_unknown"]) == 1
    assert report["counts"]["result_unknown"] == 1
    repeated = notify.scan_missed(
        root=str(root),
        config=config(),
        journal_path=tmp_path / "notify.sqlite",
        sender=unknown,
    )
    assert repeated["result_unknown"][0]["event_id"] == first["identity_bundle"]["publication_id"]
    assert repeated["counts"]["result_unknown"] == 1 and len(calls) == 1


def test_scan_publication_scan_incomplete_is_reported_not_hidden(tmp_path, monkeypatch):
    root, _, _, _ = auto_root(tmp_path)
    monkeypatch.setattr(
        schedule,
        "scan_publications",
        lambda **kw: {"status": "INCOMPLETE", "reason": "PUBLICATION_EVIDENCE_UNAVAILABLE"},
    )
    calls = []
    report = scan(root, tmp_path, calls)
    assert report["status"] == "UNKNOWN"
    assert report["publication_scan"]["reason"] == "PUBLICATION_EVIDENCE_UNAVAILABLE"
    assert not calls


def test_scan_cli_dry_run_and_argument_isolation(tmp_path, capsys):
    root, _, path, _ = auto_root(tmp_path)
    base = ["--calendar-root", str(root), "--trusted-local-posix"]
    assert notify.main(base + ["--scan-missed", "--dry-run"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "DUE" and output["environment"] == "SIMULATION_ONLY"
    assert output["delivery"] == "NOT_PROVEN"
    assert notify.main(base + ["--scan-missed", "--review-id", "x", "--dry-run"]) == 1
    assert json.loads(capsys.readouterr().out)["reason"] == "ARGUMENT_OR_CONFIG_UNAVAILABLE"
