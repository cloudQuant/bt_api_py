"""Local CURRENT simulation and fake notification sender only."""

import json
import os
import threading
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from bt_api_py import trading_calendar_coverage_notification_simulation as module
from bt_api_py.trading_calendar_coverage_status import VENUES
from bt_api_py.trading_calendar_notifications import EmailConfig
from tests.calendar_test_store import FixtureCalendarStore
from tests.unit.test_trading_calendar_coverage_status import bundle, publish

NOW = datetime(2025, 12, 31, 16, tzinfo=UTC)


def config():
    return EmailConfig(
        enabled=True,
        host="smtp.invalid",
        port=465,
        timeout=2,
        sender="sender@example.test",
        recipients=("reviewer@example.test",),
        username="private-user",
        password="private-password",
        security="ssl",
    )


def send(root, journal, sender, *, clock=lambda: NOW, **changes):
    return module.notify_current_coverage(
        str(root),
        journal_path=journal,
        mode="SIMULATION_ONLY",
        clock_error_seconds=0,
        config=config(),
        sender=sender,
        _clock=clock,
        **changes,
    )


def accepted(config):
    return SimpleNamespace(accepted_recipients=config.recipients, refused_recipients={})


def statuses(result):
    return {row["venue"]: row["status"] for row in result["venues"]}


def test_real_current_warning_days_daily_dedupe_and_no_secrets(tmp_path):
    root = tmp_path / "store"
    root.mkdir()
    starts = dict.fromkeys(VENUES, date(2025, 1, 1))
    starts["GFEX"] = date(2026, 1, 2)
    ends = dict(
        zip(
            VENUES,
            (
                date(2026, 2, 1),
                date(2026, 1, 31),
                date(2026, 1, 1),
                date(2025, 12, 31),
                date(2026, 12, 31),
                date(2026, 12, 31),
            ),
            strict=True,
        )
    )
    publish(FixtureCalendarStore(root), bundle(ends, starts=starts), 1)
    calls = []

    def sender(cfg):
        calls.append(cfg)
        assert "SIMULATION_ONLY" in cfg.subject and "CURRENT" in cfg.subject
        assert "NOT_PROVEN" in cfg.body and "real health is UNKNOWN" in cfg.body
        return accepted(cfg)

    journal = tmp_path / "journal.db"
    first = send(root, journal, sender)
    assert statuses(first) == {
        "SHFE": "NOT_SENT",
        "INE": "ACCEPTED",
        "CZCE": "ACCEPTED",
        "DCE": "ACCEPTED",
        "CFFEX": "NOT_SENT",
        "GFEX": "UNKNOWN",
    }
    assert first["scope"] == "CURRENT" and first["clock_authentication"] == "NOT_PROVEN"
    assert len(calls) == 3
    again = send(root, journal, sender)
    assert [statuses(again)[v] for v in ("INE", "CZCE", "DCE")] == ["DUPLICATE"] * 3
    assert len(calls) == 3
    raw = journal.read_bytes() + json.dumps(first).encode()
    for value in (str(root).encode(), b"reviewer", b"private-password", b"private-user"):
        assert value not in raw


def test_same_identity_at_distinct_roots_does_not_dedupe(tmp_path):
    journal = tmp_path / "journal.db"
    calls = []
    for name in ("first", "second"):
        root = tmp_path / name
        root.mkdir()
        publish(FixtureCalendarStore(root), bundle(date(2026, 1, 31), ("SHFE",)), 1)
        result = send(root, journal, lambda cfg: (calls.append(1), accepted(cfg))[1])
        assert statuses(result)["SHFE"] == "ACCEPTED"
    assert len(calls) == 2
    assert b"first" not in journal.read_bytes() and b"second" not in journal.read_bytes()


@pytest.mark.parametrize("change_at", [2, 3])
def test_changed_current_before_or_after_reservation_never_sends(tmp_path, monkeypatch, change_at):
    root = tmp_path / "store"
    root.mkdir()
    store = FixtureCalendarStore(root)
    publish(store, bundle(date(2026, 1, 31), ("SHFE",)), 1)
    original = module.report_coverage
    counter = []

    def reader(*args, **kwargs):
        counter.append(1)
        if len(counter) == change_at:
            publish(store, bundle(date(2026, 1, 30), ("SHFE",)), 2)
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "report_coverage", reader)
    result = send(root, tmp_path / "journal.db", lambda cfg: pytest.fail("must not send"))
    assert statuses(result)["SHFE"] == "NOT_SENT"
    assert len(counter) == change_at


def test_midnight_uncertainty_unknown_missing_and_busy_do_not_send(tmp_path):
    fcntl = pytest.importorskip("fcntl")
    root = tmp_path / "store"
    root.mkdir()
    publish(FixtureCalendarStore(root), bundle(date(2026, 1, 31), ("SHFE",)), 1)
    journal = tmp_path / "journal.db"

    def forbidden(cfg):
        pytest.fail("must not send")

    values = iter(
        (
            datetime(2026, 1, 1, 15, 59, 59, tzinfo=UTC),
            datetime(2026, 1, 1, 15, 59, 59, tzinfo=UTC),
            datetime(2026, 1, 1, 16, 0, 0, tzinfo=UTC),
        )
    )
    assert (
        statuses(send(root, journal, forbidden, clock=lambda: next(values)))["SHFE"] == "NOT_SENT"
    )
    with (root / ".lock").open("rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert statuses(send(root, journal, forbidden))["SHFE"] == "UNKNOWN"
        fcntl.flock(lock, fcntl.LOCK_UN)
    missing = send(tmp_path / "missing", journal, forbidden)
    assert set(statuses(missing).values()) == {"UNKNOWN"}
    assert not (tmp_path / "missing").exists()
    ambiguous = module.notify_current_coverage(
        str(root),
        journal_path=journal,
        mode="SIMULATION_ONLY",
        clock_error_seconds=1,
        config=config(),
        sender=forbidden,
        _clock=lambda: datetime(2026, 1, 1, 15, 59, 59, 500000, tzinfo=UTC),
    )
    assert statuses(ambiguous)["SHFE"] == "UNKNOWN"


def test_sender_exception_unknown_deduplicated_and_concurrent(tmp_path):
    root = tmp_path / "store"
    root.mkdir()
    publish(FixtureCalendarStore(root), bundle(date(2026, 1, 31), ("SHFE",)), 1)
    journal = tmp_path / "journal.db"
    entered, release = threading.Event(), threading.Event()
    results = []

    def delayed(cfg):
        entered.set()
        assert release.wait(5)
        raise RuntimeError("private-reply")

    worker = threading.Thread(target=lambda: results.append(send(root, journal, delayed)))
    worker.start()
    try:
        assert entered.wait(5)
        duplicate = send(root, journal, lambda cfg: pytest.fail("duplicate sent"))
        assert statuses(duplicate)["SHFE"] == "DUPLICATE"
        assert (
            next(row for row in duplicate["venues"] if row["venue"] == "SHFE")["journal_status"]
            == "UNKNOWN"
        )
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert statuses(results[0])["SHFE"] == "UNKNOWN"
    duplicate = send(root, journal, lambda cfg: pytest.fail("must not resend"))
    assert statuses(duplicate)["SHFE"] == "DUPLICATE"
    assert b"private-reply" not in journal.read_bytes()


def test_new_day_and_changed_threshold_make_distinct_simulation_events(tmp_path):
    root = tmp_path / "store"
    root.mkdir()
    publish(FixtureCalendarStore(root), bundle(date(2026, 1, 31), ("SHFE",)), 1)
    journal = tmp_path / "journal.db"
    calls = []

    def sender(cfg):
        calls.append(cfg)
        return accepted(cfg)

    assert statuses(send(root, journal, sender))["SHFE"] == "ACCEPTED"
    assert statuses(send(root, journal, sender))["SHFE"] == "DUPLICATE"
    assert statuses(send(root, journal, sender, threshold_days=31))["SHFE"] == "ACCEPTED"
    next_day = datetime(2026, 1, 1, 16, tzinfo=UTC)
    assert statuses(send(root, journal, sender, clock=lambda: next_day))["SHFE"] == "ACCEPTED"
    assert len(calls) == 3


def test_lexically_equivalent_roots_share_identity_without_resolving_symlinks(tmp_path):
    root = tmp_path / "store"
    root.mkdir()
    publish(FixtureCalendarStore(root), bundle(date(2026, 1, 31), ("SHFE",)), 1)
    journal = tmp_path / "journal.db"
    calls = []

    def sender(cfg):
        calls.append(1)
        return accepted(cfg)

    assert statuses(send(root, journal, sender))["SHFE"] == "ACCEPTED"
    for equivalent in (str(root) + "/", str(root) + "/./", str(root.parent) + "/./store"):
        assert statuses(send(equivalent, journal, sender))["SHFE"] == "DUPLICATE"
    assert len(calls) == 1
    rejected = send(str(root) + "/../store", journal, lambda cfg: pytest.fail("must not send"))
    assert rejected["status"] == "UNKNOWN"
    assert len(calls) == 1


@pytest.mark.skipif(os.name != "posix", reason="POSIX filesystem alias test only")
def test_double_leading_slash_root_is_unknown_and_never_sends(tmp_path):
    root = tmp_path / "store"
    root.mkdir()
    publish(FixtureCalendarStore(root), bundle(date(2026, 1, 31), ("SHFE",)), 1)
    journal = tmp_path / "journal.db"
    calls = []

    def sender(cfg):
        calls.append(1)
        return accepted(cfg)

    assert statuses(send(root, journal, sender))["SHFE"] == "ACCEPTED"
    alias = "/" + str(root)
    assert os.path.samefile(alias, root)
    result = send(alias, journal, sender)
    assert result["status"] == "UNKNOWN" and result["venues"] == []
    assert len(calls) == 1
    assert alias not in json.dumps(result)
    assert alias.encode() not in journal.read_bytes()


def test_later_venue_exception_preserves_prior_result_and_continues(tmp_path, monkeypatch):
    from bt_api_py import trading_calendar_notifications as notify

    root = tmp_path / "store"
    root.mkdir()
    publish(FixtureCalendarStore(root), bundle(date(2026, 1, 31), ("SHFE", "INE", "CZCE")), 1)
    journal = tmp_path / "journal.db"
    calls = []
    original_uuid = notify.uuid.uuid4
    count = []

    def sometimes_fail():
        count.append(1)
        if len(count) == 2:
            raise RuntimeError("private-uuid-exception")
        return original_uuid()

    def sender(cfg):
        calls.append(cfg.subject)
        return accepted(cfg)

    with monkeypatch.context() as patch:
        patch.setattr(notify.uuid, "uuid4", sometimes_fail)
        result = send(root, journal, sender)
    assert result["status"] == "PARTIAL"
    assert statuses(result)["SHFE"] == "ACCEPTED"
    assert statuses(result)["INE"] == "UNKNOWN"
    assert statuses(result)["CZCE"] == "ACCEPTED"
    assert len(calls) == 2
    retry = send(root, journal, sender)
    assert statuses(retry)["SHFE"] == "DUPLICATE"
    assert statuses(retry)["INE"] == "ACCEPTED"
    assert statuses(retry)["CZCE"] == "DUPLICATE"
    assert len(calls) == 3
    assert b"private-uuid-exception" not in (journal.read_bytes() + json.dumps(result).encode())


def test_journal_unknown_after_send_preserves_other_result_and_no_resend(tmp_path, monkeypatch):
    from bt_api_py import trading_calendar_notifications as notify

    root = tmp_path / "store"
    root.mkdir()
    publish(FixtureCalendarStore(root), bundle(date(2026, 1, 31), ("SHFE", "INE")), 1)
    journal = tmp_path / "journal.db"
    original = notify._set_outcome
    attempts = []
    calls = []

    def save(connection, key, owner, outcome):
        attempts.append(1)
        if len(attempts) == 1:
            return False
        return original(connection, key, owner, outcome)

    def sender(cfg):
        calls.append(cfg.subject)
        return accepted(cfg)

    with monkeypatch.context() as patch:
        patch.setattr(notify, "_set_outcome", save)
        result = send(root, journal, sender)
    assert result["status"] == "PARTIAL"
    assert statuses(result)["SHFE"] == "UNKNOWN"
    assert statuses(result)["INE"] == "ACCEPTED"
    assert len(calls) == 2
    retry = send(root, journal, sender)
    shfe = next(row for row in retry["venues"] if row["venue"] == "SHFE")
    assert shfe["status"] == "DUPLICATE" and shfe["journal_status"] == "UNKNOWN"
    assert statuses(retry)["INE"] == "DUPLICATE"
    assert len(calls) == 2
