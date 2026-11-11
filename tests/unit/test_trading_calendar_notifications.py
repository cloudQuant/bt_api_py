"""Fake senders and local test journals only; no SMTP or real recipients."""

import builtins
import multiprocessing
import os
import sqlite3
import traceback
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

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


def install_fake_base(monkeypatch, fake_send):
    original = builtins.__import__
    imports = []

    def fake_import(name, *args, **kwargs):
        if name == "bt_api_base.email_sender":
            imports.append(name)
            return SimpleNamespace(send_email=fake_send)
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    return imports


def test_base_adapter_is_lazy_and_rejects_disabled_or_invalid_config(monkeypatch):
    imports = install_fake_base(monkeypatch, lambda **kwargs: pytest.fail("must not send"))
    with pytest.raises(ValueError, match="disabled or invalid"):
        notify.send_via_base(notify.EmailConfig())
    with pytest.raises(ValueError, match="disabled or invalid"):
        notify.send_via_base(replace(config(), sender="invalid"))
    assert imports == []


def test_base_adapter_explicitly_maps_config_and_preserves_acceptance_result(monkeypatch):
    calls = []
    expected = config()
    result = Receipt(
        (expected.recipients[0],), {expected.recipients[1]: (550, b"private-password")}
    )

    def fake_send(**kwargs):
        calls.append(kwargs)
        return result

    imports = install_fake_base(monkeypatch, fake_send)
    assert imports == []
    receipt = notify.send_via_base(expected)
    assert receipt.accepted_recipients == result.accepted_recipients
    assert receipt.refused_recipients == {expected.recipients[1]: None}
    assert "private-password" not in repr(receipt) and "reviewer" not in repr(receipt)
    assert imports == ["bt_api_base.email_sender"]
    assert calls == [
        {
            "host": expected.host,
            "port": expected.port,
            "timeout": expected.timeout,
            "sender": expected.sender,
            "recipients": expected.recipients,
            "subject": expected.subject,
            "body": expected.body,
            "username": expected.username,
            "password": expected.password,
            "security": expected.security,
        }
    ]


def test_base_adapter_errors_hide_underlying_sensitive_details(monkeypatch):
    def fake_send(**kwargs):
        raise RuntimeError("private-password private-reply")

    install_fake_base(monkeypatch, fake_send)
    with pytest.raises(RuntimeError, match="outcome unknown") as exc:
        notify.send_via_base(config())
    rendered = "".join(traceback.format_exception(exc.type, exc.value, exc.tb))
    assert "private-password" not in rendered and "private-reply" not in rendered


def test_base_adapter_rejects_invalid_receipt_as_unknown(monkeypatch):
    install_fake_base(monkeypatch, lambda **kwargs: Receipt(("unexpected@example.test",), {}))
    with pytest.raises(RuntimeError, match="outcome unknown"):
        notify.send_via_base(config())


def test_missing_base_import_is_sanitized_and_journal_remains_unknown(tmp_path, monkeypatch):
    original = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "bt_api_base.email_sender":
            raise ModuleNotFoundError("private module location")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(RuntimeError, match="transport unavailable") as exc:
        notify.send_via_base(config())
    assert "private" not in "".join(traceback.format_exception(exc.type, exc.value, exc.tb))
    path = tmp_path / "journal.db"
    result = call(path, sender=notify.send_via_base)
    assert result.status == "UNKNOWN"
    assert call(path, sender=notify.send_via_base).journal_status == "UNKNOWN"


def test_base_adapter_integrates_with_deduplication_without_network(tmp_path, monkeypatch):
    calls = []

    def fake_send(**kwargs):
        calls.append(1)
        return Receipt((kwargs["recipients"][0],), {kwargs["recipients"][1]: (550, b"detail")})

    install_fake_base(monkeypatch, fake_send)
    path = tmp_path / "journal.db"
    first = call(path, sender=notify.send_via_base)
    duplicate = call(path, sender=notify.send_via_base)
    assert first.status == "PARTIAL_REFUSAL"
    assert duplicate.status == "DUPLICATE" and duplicate.journal_status == "PARTIAL_REFUSAL"
    assert len(calls) == 1
    assert b"private-password" not in path.read_bytes()


def test_actual_isolated_base_email_sender_integrates_with_adapter_without_network(monkeypatch):
    import importlib.util
    import socket
    import ssl
    import sys
    from pathlib import Path
    from types import ModuleType

    base_dir = (
        Path(__file__).resolve().parents[2] / "bt_api" / "bt_api_base" / "src" / "bt_api_base"
    )
    spec = importlib.util.spec_from_file_location(
        "bt_api_base.email_sender", base_dir / "email_sender.py"
    )
    assert spec is not None and spec.loader is not None
    package = ModuleType("bt_api_base")
    package.__path__ = [str(base_dir)]
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "bt_api_base", package)
    monkeypatch.setitem(sys.modules, "bt_api_base.email_sender", module)
    spec.loader.exec_module(module)

    calls = []

    def no_network(*args, **kwargs):
        pytest.fail("network connection must not be attempted")

    class FakeSMTP:
        def __init__(self, host, port, *, timeout, context):
            assert (host, port, timeout) == ("smtp.invalid", 465, 2)
            assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
            calls.append("connect-fake")

        def ehlo(self):
            calls.append("ehlo")
            return 250, b"fake"

        def login(self, username, password):
            assert (username, password) == ("private-user", "private-password")
            calls.append("login")

        def send_message(self, message, *, from_addr, to_addrs):
            assert message["Subject"] == "private-subject"
            assert message.get_content().strip() == "private-body"
            assert from_addr == "sender@example.test"
            assert to_addrs == ("reviewer1@example.test", "reviewer2@example.test")
            calls.append("send-fake")
            return {"reviewer2@example.test": (550, b"private-response")}

        def close(self):
            calls.append("close")

    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(module.smtplib, "SMTP", no_network)
    monkeypatch.setattr(module.smtplib, "SMTP_SSL", FakeSMTP)
    receipt = notify.send_via_base(config())
    assert receipt.accepted_recipients == ("reviewer1@example.test",)
    assert receipt.refused_recipients == {"reviewer2@example.test": None}
    assert "private" not in repr(receipt) and "reviewer" not in repr(receipt)
    assert calls == ["connect-fake", "ehlo", "login", "send-fake", "close"]


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
