"""Fake senders and local test journals only; no SMTP or real recipients."""

import multiprocessing
import os
import sqlite3
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

import pytest

from bt_api_py import trading_calendar_notifications as notify


@dataclass
class Receipt:
    accepted_recipients: tuple[str, ...]
    refused_recipients: dict


def config():
    return notify.EmailConfig(
        enabled=True,
        host="smtp.invalid",
        port=465,
        timeout=2,
        sender="sender@example.test",
        recipients=("reviewer1@example.test", "reviewer2@example.test"),
        subject="private-subject",
        body="private-body",
        username="private-user",
        password="private-password",
        security="ssl",
    )


def state(kind, scope, event):
    if kind == "manual":
        return notify.NotificationState(scope, event, "manual", "PROVEN", candidate_state="PENDING")
    return notify.NotificationState(
        scope, event, "auto", "PROVEN", publication_state="COMMITTED", content_change="CHANGED"
    )


def call(
    path,
    *,
    kind="manual",
    event="candidate-1",
    email=None,
    reader=state,
    sender=None,
    instant=datetime(2026, 11, 11, 12, tzinfo=UTC),
    bound=timedelta(0),
    clock_evidence="PROVEN",
):
    email = email if email is not None else config()
    if sender is None:

        def sender(settings):
            return Receipt(settings.recipients, {})

    return notify.notify_calendar_event(
        journal_path=path,
        kind=kind,
        scope_id="scope-1",
        event_id=event,
        config=email,
        state_reader=reader,
        sender=sender,
        clock_instant=instant,
        clock_error_bound=bound,
        clock_evidence=clock_evidence,
    )


def test_manual_dedupes_same_shanghai_day_and_sends_next_day_and_new_candidate(tmp_path):
    path = tmp_path / "journal.db"
    calls = []

    def sender(settings):
        calls.append(True)
        return Receipt(settings.recipients, {})

    first = call(path, sender=sender)
    same = call(path, sender=sender, instant=datetime(2026, 11, 11, 15, 59, tzinfo=UTC))
    next_day = call(path, sender=sender, instant=datetime(2026, 11, 11, 16, 0, tzinfo=UTC))
    changed = call(path, sender=sender, event="candidate-2")
    assert first.status == next_day.status == changed.status == "ACCEPTED"
    assert same.status == "DUPLICATE" and same.journal_status == "ACCEPTED"
    assert len(calls) == 3


def test_auto_only_proven_changed_committed_publication_once(tmp_path):
    path = tmp_path / "journal.db"
    calls = []

    def sender(cfg):
        calls.append(True)
        return Receipt(cfg.recipients, {})

    assert call(path, kind="auto", event="pub-1", sender=sender).status == "ACCEPTED"
    assert call(path, kind="auto", event="pub-1", sender=sender).status == "DUPLICATE"
    assert call(path, kind="auto", event="pub-2", sender=sender).status == "ACCEPTED"
    for index, change in enumerate(
        (
            {"publication_state": "UNKNOWN"},
            {"publication_state": "NOT_COMMITTED"},
            {"content_change": "UNCHANGED"},
            {"content_change": "UNKNOWN"},
            {"evidence": "UNKNOWN"},
            {"mode": "manual"},
        )
    ):

        def reader(k, s, e, change=change):
            return replace(state(k, s, e), **change)

        assert (
            call(path, kind="auto", event=f"bad-{index}", reader=reader, sender=sender).status
            == "NOT_SENT"
        )
    assert len(calls) == 2


def test_disabled_or_incomplete_never_calls_state_or_sender_or_creates_journal(tmp_path):
    def forbidden(*args):
        pytest.fail("must not access state or send")

    path = tmp_path / "journal.db"
    result = call(path, email=notify.EmailConfig(), reader=forbidden, sender=forbidden)
    assert result.status == "NOT_SENT" and result.reason == "DISABLED"
    assert (
        call(path, email=replace(config(), host=""), reader=forbidden, sender=forbidden).reason
        == "INVALID_CONFIG"
    )
    assert not path.exists()
    assert "private-password" not in repr(config()) and "reviewer1" not in repr(config())


@pytest.mark.parametrize(
    "changes",
    [
        {"sender": "bad-address"},
        {"recipients": ("bad-address",)},
        {"host": "bad host"},
        {"subject": "bad\ninjected"},
        {"username": "bad\nuser"},
        {"password": None},
        {"port": True},
        {"timeout": float("inf")},
    ],
)
def test_malformed_transport_configuration_is_rejected_before_state_or_send(tmp_path, changes):
    def forbidden(*args):
        pytest.fail("must not access state or send")

    path = tmp_path / "journal.db"
    result = call(path, email=replace(config(), **changes), reader=forbidden, sender=forbidden)
    assert result.status == "NOT_SENT" and result.reason == "INVALID_CONFIG"
    assert not path.exists()


def test_invalid_manual_clock_and_ambiguous_midnight_do_not_send(tmp_path):
    path = tmp_path / "journal.db"
    assert call(path, clock_evidence="UNKNOWN").reason == "CLOCK_UNKNOWN"
    assert call(path, bound=None).reason == "CLOCK_UNKNOWN"
    assert (
        call(
            path, instant=datetime(2026, 11, 11, 15, 59, 59, tzinfo=UTC), bound=timedelta(seconds=1)
        ).reason
        == "CLOCK_UNKNOWN"
    )
    assert not path.exists()


@pytest.mark.parametrize(
    "changes",
    [
        {"candidate_state": "APPROVED"},
        {"candidate_state": "INVALID"},
        {"candidate_state": "UNKNOWN"},
        {"mode": "auto"},
        {"evidence": "UNKNOWN"},
        {"event_id": "other"},
        {"scope_id": "other"},
    ],
)
def test_manual_ineligible_states_are_never_sent(tmp_path, changes):
    def sender(_cfg):
        pytest.fail("must not send")

    def reader(k, s, e):
        return replace(state(k, s, e), **changes)

    assert (
        call(tmp_path / "journal.db", reader=reader, sender=sender).reason == "STATE_NOT_ELIGIBLE"
    )


def test_state_changes_between_checks_records_not_sent_and_can_safely_retry(tmp_path):
    path = tmp_path / "journal.db"
    counter = []

    def reader(kind, scope, event):
        counter.append(True)
        candidate = state(kind, scope, event)
        return candidate if len(counter) == 1 else replace(candidate, mode="auto")

    result = call(path, reader=reader, sender=lambda _cfg: pytest.fail("must not send"))
    assert result.status == "NOT_SENT" and len(counter) == 2
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT status FROM notifications").fetchone() == ("NOT_SENT",)
    assert call(path).status == "ACCEPTED"
    assert call(path).status == "DUPLICATE"


def test_state_reader_exception_before_or_after_intent_is_not_sent(tmp_path):
    path = tmp_path / "journal.db"

    def failed(*args):
        raise RuntimeError("private raw state")

    result = call(path, reader=failed)
    assert result.reason == "STATE_UNAVAILABLE" and "private" not in repr(result)
    assert not path.exists()
    calls = []

    def later(kind, scope, event):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("private state")
        return state(kind, scope, event)

    result = call(path, reader=later)
    assert result.status == "NOT_SENT" and result.reason == "STATE_UNAVAILABLE"
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT status FROM notifications").fetchone() == ("NOT_SENT",)


def test_sender_exception_unknown_and_no_automatic_resend(tmp_path):
    path = tmp_path / "journal.db"
    calls = []

    def sender(_config):
        calls.append(True)
        raise ValueError("private smtp reply")

    result = call(path, sender=sender)
    assert result.status == "UNKNOWN" and "private" not in repr(result)
    repeat = call(path, sender=sender)
    assert repeat.status == "DUPLICATE" and repeat.journal_status == "UNKNOWN"
    assert len(calls) == 1
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT status FROM notifications").fetchone() == ("UNKNOWN",)


def test_partial_acceptance_is_persisted_without_addresses_or_responses(tmp_path):
    path = tmp_path / "journal.db"

    def sender(settings):
        return Receipt((settings.recipients[0],), {settings.recipients[1]: (550, b"private-reply")})

    result = call(path, sender=sender)
    assert result.status == "PARTIAL_REFUSAL"
    assert call(path, sender=sender).journal_status == "PARTIAL_REFUSAL"
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT status FROM notifications").fetchone() == ("PARTIAL_REFUSAL",)
    raw = path.read_bytes()
    for secret in (
        b"reviewer1",
        b"reviewer2",
        b"private-body",
        b"private-subject",
        b"private-user",
        b"private-password",
        b"private-reply",
    ):
        assert secret not in raw


def test_malformed_sender_result_is_unknown_not_success(tmp_path):
    result = call(
        tmp_path / "journal.db", sender=lambda _cfg: Receipt(("unexpected@example.test",), {})
    )
    assert result.status == "UNKNOWN"


def test_reported_full_refusal_is_persisted_without_automatic_resend(tmp_path):
    path = tmp_path / "journal.db"

    def sender(settings):
        return Receipt((), dict.fromkeys(settings.recipients, (550, b"private reply")))

    result = call(path, sender=sender)
    assert result.status == "REFUSED"
    assert (
        call(path, sender=lambda _cfg: pytest.fail("must not resend")).journal_status == "REFUSED"
    )


def test_journal_failure_before_sender_is_not_sent(tmp_path):
    result = call(tmp_path / "missing" / "journal.db", sender=lambda _cfg: pytest.fail("sent"))
    assert result.status == "NOT_SENT" and result.reason == "JOURNAL_UNAVAILABLE"


def test_nonunique_journal_schema_is_rejected(tmp_path):
    path = tmp_path / "journal.db"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE notifications (kind TEXT, scope_id TEXT, event_id TEXT, "
            "period TEXT, owner TEXT, status TEXT)"
        )
    result = call(path, sender=lambda _cfg: pytest.fail("sent"))
    assert result.status == "NOT_SENT" and result.reason == "JOURNAL_INVALID"


def test_outcome_persistence_failure_after_call_is_unknown_and_not_retried(tmp_path, monkeypatch):
    path = tmp_path / "journal.db"
    monkeypatch.setattr(notify, "_set_outcome", lambda *args: False)
    assert call(path).status == "UNKNOWN"
    duplicate = call(path)
    assert duplicate.status == "DUPLICATE" and duplicate.journal_status == "UNKNOWN"


def process_sender(path, ready, release):
    def sender(cfg):
        ready.set()
        if not release.wait(5):
            raise RuntimeError("test timeout")
        return Receipt(cfg.recipients, {})

    call(path, sender=sender)


@pytest.mark.skipif(os.name != "posix", reason="local process test only; Windows NOT_RUN")
def test_cross_process_race_has_one_sender_and_unknown_inflight(tmp_path):
    context = multiprocessing.get_context("fork")
    ready, release = context.Event(), context.Event()
    path = tmp_path / "journal.db"
    child = context.Process(target=process_sender, args=(path, ready, release))
    child.start()
    try:
        assert ready.wait(5)
        second = call(path, sender=lambda _cfg: pytest.fail("duplicate sent"))
        assert second.status == "DUPLICATE" and second.journal_status == "UNKNOWN"
    finally:
        release.set()
        child.join(5)
        if child.is_alive():
            child.terminate()
            child.join(5)
    assert child.exitcode == 0
    assert call(path).journal_status == "ACCEPTED"


def crashing_process(path):
    def sender(_cfg):
        os._exit(27)

    call(path, sender=sender)


@pytest.mark.skipif(os.name != "posix", reason="local process test only; Windows NOT_RUN")
def test_crash_after_sender_started_leaves_inflight_as_unknown(tmp_path):
    context = multiprocessing.get_context("fork")
    path = tmp_path / "journal.db"
    child = context.Process(target=crashing_process, args=(path,))
    child.start()
    child.join(5)
    if child.is_alive():
        child.terminate()
        child.join(5)
        pytest.fail("child did not exit")
    assert child.exitcode == 27
    duplicate = call(path, sender=lambda _cfg: pytest.fail("must not resend"))
    assert duplicate.status == "DUPLICATE" and duplicate.journal_status == "UNKNOWN"
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT status FROM notifications").fetchone() == ("IN_FLIGHT",)
