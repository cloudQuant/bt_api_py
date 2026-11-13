"""Offline notification adapter tests using the actual persistent simulation store."""

import fcntl
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest

from bt_api_py import trading_calendar_notify_cli as cli
from bt_api_py.trading_calendar_notifications import BaseEmailReceipt, EmailConfig
from tests.unit.test_trading_calendar_mock_cli_integration import SMALL_MOCK, command
from tests.unit.test_trading_calendar_mock_persistent import operation, run_args, settings
from tests.unit.test_trading_calendar_observation_run import env_without_network


@pytest.fixture
def setup_store(tmp_path):
    def create(mode):
        root, config = settings(tmp_path, mode)
        env = env_without_network(tmp_path, SMALL_MOCK)
        rc, lines, err = command(env, *run_args(root, config))
        assert rc == (10 if mode == "manual" else 0), (lines, err)
        run = next(row for row in lines if row.get("candidate_state") == "COMMITTED")
        return root, config, env, run["run_id"], lines[-1]

    return create


def email():
    return EmailConfig(
        enabled=True,
        host="smtp.invalid",
        port=587,
        timeout=2,
        sender="sender@example.invalid",
        recipients=("to@example.invalid",),
    )


def attempt(root, run_id, result, tmp_path, *, kind="manual", sender=None, **kwargs):
    args = {
        "root": str(root),
        "run_id": run_id,
        "kind": kind,
        "event_id": result["review_id"] if kind == "manual" else result["publication_id"],
        "review_sha256": result.get("review_payload_sha256"),
        "config": email(),
        "journal_path": tmp_path / "notification.sqlite",
        "sender": sender or (lambda c: BaseEmailReceipt(c.recipients, {})),
        "clock_instant": datetime(2026, 11, 13, 4, tzinfo=UTC),
        "clock_error_bound": timedelta(seconds=1),
        "clock_evidence": "PROVEN",
    }
    args.update(kwargs)
    return cli.run(**args)


def test_manual_pending_dedup_and_confirmed_invalid(setup_store, tmp_path):
    root, config, env, run_id, pending = setup_store("manual")
    called = []

    def sender(config):
        called.append(config.body)
        return BaseEmailReceipt(config.recipients, {})

    assert attempt(root, run_id, pending, tmp_path, sender=sender).status == "ACCEPTED"
    assert attempt(root, run_id, pending, tmp_path, sender=sender).status == "DUPLICATE"
    assert len(called) == 1 and "SIMULATION_ONLY" in called[0]
    rc, lines, err = command(
        env,
        *operation(
            root,
            run_id,
            "mock-confirm",
            "--review-id",
            pending["review_id"],
            "--review-sha256",
            pending["review_payload_sha256"],
            "--reviewer",
            "MOCK_reviewer",
            "--confirmed-at",
            "2026-11-13T00:00:00.000000Z",
        ),
    )
    assert rc == 0, (lines, err)
    assert attempt(root, run_id, pending, tmp_path).status == "NOT_SENT"


def test_manual_digest_clock_dry_run_and_mode_change(setup_store, tmp_path):
    root, config, env, run_id, pending = setup_store("manual")
    assert attempt(root, run_id, pending, tmp_path, dry_run=True).reason == "DRY_RUN_ELIGIBLE"
    assert not (tmp_path / "notification.sqlite").exists()
    assert (
        attempt(root, run_id, pending, tmp_path, clock_evidence="UNKNOWN").reason == "CLOCK_UNKNOWN"
    )
    assert not (tmp_path / "notification.sqlite").exists()
    assert attempt(root, run_id, pending, tmp_path, review_sha256="a" * 64).status == "NOT_SENT"
    config.write_text(json.dumps({"calendars": {"root": str(root), "review_mode": "auto"}}))
    assert attempt(root, run_id, pending, tmp_path).status == "NOT_SENT"


def test_auto_commit_dedup_and_no_change(setup_store, tmp_path):
    root, config, env, run_id, first = setup_store("auto")
    operator = cli.MockCalendarPublication(root=str(root), run_id=run_id)
    cli._auto(operator, first["publication_id"])
    outcome = attempt(root, run_id, first, tmp_path, kind="auto")
    assert outcome.status == "ACCEPTED", outcome
    assert attempt(root, run_id, first, tmp_path, kind="auto").status == "DUPLICATE"
    (root / "PENDING.json").write_text("{}")
    assert attempt(root, run_id, first, tmp_path, kind="auto").status == "NOT_SENT"
    (root / "PENDING.json").unlink()
    from tests.unit.test_trading_calendar_mock_persistent import create_baseline_supplement

    create_baseline_supplement(root)
    rc, lines, err = command(env, *operation(root, run_id, "mock-update", "--variant", "BASE"))
    assert lines[-1]["status"] == "SAME", (lines, err)
    assert attempt(root, lines[-1]["run_id"], lines[-1], tmp_path, kind="auto").status == "NOT_SENT"
    rc, lines, err = command(
        env, *operation(root, run_id, "mock-update", "--variant", "AFTERNOON_CHANGE")
    )
    assert lines[-1]["status"] == "COMMITTED", (lines, err)
    changed = lines[-1]
    assert attempt(root, changed["run_id"], changed, tmp_path, kind="auto").status == "ACCEPTED"
    assert (
        attempt(
            root, run_id, first, tmp_path, kind="auto", journal_path=tmp_path / "historical.sqlite"
        ).status
        == "ACCEPTED"
    )
    from tests.unit.test_trading_calendar_mock_policy_transition import begin, policy, set_mode

    rc, lines, err = begin(env, root, run_id, "manual", "MOCK_notify_policy")
    assert rc == 10 and lines[-1]["status"] == "PENDING_CONFIG", (lines, err)
    set_mode(config, root, "manual")
    rc, lines, err = command(
        env, *policy(root, run_id, "resume", "--operation-id", "MOCK_notify_policy")
    )
    assert rc == 0, (lines, err)
    assert (
        attempt(
            root, run_id, first, tmp_path, kind="auto", journal_path=tmp_path / "after_mode.sqlite"
        ).status
        == "ACCEPTED"
    )


def test_manual_supplement_change_blocks_pending(setup_store, tmp_path):
    root, _, _, run_id, pending = setup_store("manual")
    (root / "supplement.yaml").write_text("{}")
    assert attempt(root, run_id, pending, tmp_path).status == "NOT_SENT"
    assert not (tmp_path / "notification.sqlite").exists()


def test_legacy_review_without_input_binding_stays_unknown(setup_store, tmp_path):
    root, _, _, run_id, pending = setup_store("manual")
    operator = cli.MockCalendarPublication(root=str(root), run_id=run_id)
    original = cli.mock_cli._read_pending(operator, pending["review_id"])["body"]
    ids = cli.mock_cli.allocate(candidate_id=original["candidate_id"])
    saved = cli.mock_cli._retain_pending(operator, ids, original["proposal"])
    assert saved["status"] == "COMMITTED"
    legacy = {**ids, "review_payload_sha256": saved["review_payload_sha256"]}
    assert attempt(root, run_id, legacy, tmp_path).status == "NOT_SENT"
    assert not (tmp_path / "notification.sqlite").exists()


def test_private_journal_required(setup_store, tmp_path):
    root, _, _, run_id, pending = setup_store("manual")
    target = tmp_path / "target"
    target.write_text("untouched")
    target.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(target)
    hardlink = tmp_path / "hardlink"
    os.link(target, hardlink)
    public = tmp_path / "public"
    public.mkdir(mode=0o755)
    sent = []
    for path in (link, hardlink, public / "events.sqlite"):
        result = attempt(
            root, run_id, pending, tmp_path, journal_path=path, sender=lambda c: sent.append(c)
        )
        assert result.status == "NOT_SENT"
    assert (
        not sent and target.read_text() == "untouched" and not (public / "events.sqlite").exists()
    )


@pytest.mark.parametrize("interleave", ["prepare", "supplement"])
def test_confirmation_is_excluded_during_review_observation(
    setup_store, tmp_path, monkeypatch, interleave
):
    root, _, env, run_id, pending = setup_store("manual")
    count = []

    def confirmation_attempt():
        count.append(1)
        if len(count) == (3 if interleave == "prepare" else 6):
            supplement = root / "supplement.yaml"
            supplement.write_text("{}")
            rc, lines, err = command(
                env,
                *operation(
                    root,
                    run_id,
                    "mock-confirm",
                    "--review-id",
                    pending["review_id"],
                    "--review-sha256",
                    pending["review_payload_sha256"],
                    "--reviewer",
                    "MOCK_reviewer",
                    "--confirmed-at",
                    "2026-11-13T00:00:00.000000Z",
                ),
            )
            assert rc != 0 and lines[-1]["reason"] == "REVIEW_LOCK_UNAVAILABLE", (lines, err)
            supplement.unlink()

    if interleave == "prepare":
        original = cli.MockCalendarPublication.prepare

        def concurrent_confirmation(operator, **kwargs):
            value = original(operator, **kwargs)
            confirmation_attempt()
            return value

        monkeypatch.setattr(cli.MockCalendarPublication, "prepare", concurrent_confirmation)
    else:
        original = cli.mock_cli._verify_supplement

        def concurrent_verification(*args):
            value = original(*args)
            confirmation_attempt()
            return value

        monkeypatch.setattr(cli.mock_cli, "_verify_supplement", concurrent_verification)

    sent = []

    def sender(config):
        lock_path = root / "review-bundles" / f".mock-confirm-{pending['review_id']}.lock"
        fd = os.open(lock_path, os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
        sent.append(1)
        return BaseEmailReceipt(config.recipients, {})

    result = attempt(root, run_id, pending, tmp_path, sender=sender)
    assert result.status == "ACCEPTED" and sent == [1]
    assert len(count) == (3 if interleave == "prepare" else 6)


def test_review_lock_unavailable_fails_closed(setup_store, tmp_path):
    root, _, _, run_id, pending = setup_store("manual")
    path = root / "review-bundles" / f".mock-confirm-{pending['review_id']}.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        sent = []
        result = attempt(root, run_id, pending, tmp_path, sender=lambda value: sent.append(value))
        assert result.status == "NOT_SENT" and not sent
        assert not (tmp_path / "notification.sqlite").exists()
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def test_state_change_and_unknown_intent_do_not_send(setup_store, tmp_path, monkeypatch):
    root, config, env, run_id, pending = setup_store("manual")
    original = cli._manual
    reads = []

    def changed(*args):
        reads.append(1)
        if len(reads) == 3:
            raise ValueError("sensitive path")
        return original(*args)

    monkeypatch.setattr(cli, "_manual", changed)
    sent = []
    result = attempt(root, run_id, pending, tmp_path, sender=lambda c: sent.append(c))
    assert result.status == "NOT_SENT" and not sent
    monkeypatch.setattr(cli, "_manual", original)
    connection = sqlite3.connect(tmp_path / "notification.sqlite")
    connection.execute("UPDATE notifications SET status='IN_FLIGHT'")
    connection.commit()
    connection.close()
    result = attempt(root, run_id, pending, tmp_path, sender=lambda c: sent.append(c))
    assert result.status == "DUPLICATE" and result.journal_status == "UNKNOWN" and not sent


def test_cli_private_errors_and_default_no_smtp(setup_store, tmp_path, capsys):
    root, config, env, run_id, pending = setup_store("manual")
    private = tmp_path / "secret_smtp_path"
    private.write_text("secret password")
    os.chmod(private, 0o644)
    args = [
        "--calendar-root",
        str(root),
        "--run-id",
        run_id,
        "--review-id",
        pending["review_id"],
        "--review-sha256",
        pending["review_payload_sha256"],
        "--trusted-local-posix",
    ]
    assert cli.main(args + ["--smtp-config", str(private)]) == 1
    output = capsys.readouterr().out
    assert "secret" not in output and str(root) not in output
    assert cli.main(args) == 1
    assert json.loads(capsys.readouterr().out)["reason"] == "DISABLED"
    assert cli.main(args + ["--dry-run", "--smtp-config", str(private)]) == 0
    assert json.loads(capsys.readouterr().out)["reason"] == "DRY_RUN_ELIGIBLE"


def test_sender_exception_is_sanitized(setup_store, tmp_path):
    root, _, _, run_id, pending = setup_store("manual")

    def sender(_):
        raise ValueError("password recipient@example.invalid /sensitive/path")

    result = attempt(root, run_id, pending, tmp_path, sender=sender)
    assert result.status == "UNKNOWN" and result.reason == "SMTP_OUTCOME"
    journal = (tmp_path / "notification.sqlite").read_bytes()
    assert b"password" not in journal and b"recipient@example.invalid" not in journal
    assert b"/sensitive/path" not in journal


@pytest.mark.parametrize("failure", ["journal_close", "sqlite_close"])
def test_cleanup_failure_after_sender_is_unknown(setup_store, tmp_path, monkeypatch, failure):
    root, _, _, run_id, pending = setup_store("manual")
    if failure == "journal_close":
        original_journal = cli._protected_journal

        @contextmanager
        def failing_journal(path):
            with original_journal(path) as verify:
                yield verify
            raise OSError("private cleanup detail")

        monkeypatch.setattr(cli, "_protected_journal", failing_journal)
    else:
        original_connect = sqlite3.connect

        class CloseFailure:
            def __init__(self, connection):
                self.connection = connection

            def __getattr__(self, name):
                return getattr(self.connection, name)

            def close(self):
                self.connection.close()
                raise OSError("private cleanup detail")

        monkeypatch.setattr(
            sqlite3,
            "connect",
            lambda *args, **kwargs: CloseFailure(original_connect(*args, **kwargs)),
        )
    sent = []

    def sender(value):
        sent.append(1)
        return BaseEmailReceipt(value.recipients, {})

    result = attempt(root, run_id, pending, tmp_path, sender=sender)
    assert sent == [1] and result.status == "UNKNOWN"
    assert result.reason == "POST_SEND_OUTCOME_UNKNOWN" and result.journal_status == "UNKNOWN"


def test_cli_explicit_clock_declaration_and_rechecked_day(
    setup_store, tmp_path, monkeypatch, capsys
):
    root, _, _, run_id, pending = setup_store("manual")
    config = tmp_path / "smtp.json"
    config.write_text(
        json.dumps(
            {
                "enabled": True,
                "host": "smtp.invalid",
                "port": 587,
                "timeout": 2,
                "sender": "sender@example.invalid",
                "recipients": ["to@example.invalid"],
            }
        )
    )
    clock = tmp_path / "clock.json"
    clock.write_text(
        json.dumps(
            {
                "environment": "SIMULATION_ONLY",
                "evidence": "CALLER_DECLARED",
                "error_bound_seconds": 1,
            }
        )
    )
    config.chmod(0o600)
    clock.chmod(0o600)
    sent = []

    def fake_sender(value):
        sent.append(value)
        return BaseEmailReceipt(value.recipients, {})

    monkeypatch.setattr(cli, "send_via_base", fake_sender)
    args = [
        "--calendar-root",
        str(root),
        "--run-id",
        run_id,
        "--review-id",
        pending["review_id"],
        "--review-sha256",
        pending["review_payload_sha256"],
        "--trusted-local-posix",
        "--smtp-config",
        str(config),
        "--journal-path",
        str(tmp_path / "cli.sqlite"),
        "--simulation-clock-config",
        str(clock),
    ]
    assert cli.main(args) == 0
    output = json.loads(capsys.readouterr().out)
    assert (
        output["status"] == "ACCEPTED"
        and output["clock_evidence"] == "CALLER_DECLARED_NOT_AUTHENTICATED"
    )
    assert len(sent) == 1
    times = iter(
        [
            datetime(2026, 11, 13, 15, 59, 57, tzinfo=UTC),
            datetime(2026, 11, 13, 15, 59, 58, tzinfo=UTC),
            datetime(2026, 11, 13, 16, 0, 0, tzinfo=UTC),
        ]
    )
    result = attempt(
        root, run_id, pending, tmp_path, sender=fake_sender, clock_source=lambda: next(times)
    )
    assert result.status == "NOT_SENT" and len(sent) == 1


@pytest.mark.parametrize("kind", ["smtp", "clock"])
def test_config_replacement_before_send_blocks(setup_store, tmp_path, monkeypatch, capsys, kind):
    root, _, _, run_id, pending = setup_store("manual")
    config = tmp_path / "smtp.json"
    config.write_text(
        json.dumps(
            {
                "enabled": True,
                "host": "smtp.invalid",
                "port": 587,
                "timeout": 2,
                "sender": "sender@example.invalid",
                "recipients": ["to@example.invalid"],
            }
        )
    )
    clock = tmp_path / "clock.json"
    clock.write_text(
        json.dumps(
            {
                "environment": "SIMULATION_ONLY",
                "evidence": "CALLER_DECLARED",
                "error_bound_seconds": 1,
            }
        )
    )
    config.chmod(0o600)
    clock.chmod(0o600)
    name, path = ("_read_config", config) if kind == "smtp" else ("_read_clock_declaration", clock)
    original = getattr(cli, name)
    calls = []

    def changing(pathname):
        calls.append(1)
        if len(calls) == 3:
            replacement = tmp_path / "replacement"
            replacement.write_bytes(path.read_bytes())
            replacement.chmod(0o600)
            os.replace(replacement, path)
        return original(pathname)

    monkeypatch.setattr(cli, name, changing)
    sent = []
    monkeypatch.setattr(cli, "send_via_base", lambda value: sent.append(value))
    args = [
        "--calendar-root",
        str(root),
        "--run-id",
        run_id,
        "--review-id",
        pending["review_id"],
        "--review-sha256",
        pending["review_payload_sha256"],
        "--trusted-local-posix",
        "--smtp-config",
        str(config),
        "--journal-path",
        str(tmp_path / "cli.sqlite"),
        "--simulation-clock-config",
        str(clock),
    ]
    assert cli.main(args) == 1 and not sent
    assert json.loads(capsys.readouterr().out)["status"] == "NOT_SENT"


def test_config_changed_during_read_is_rejected(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    path.write_text('{"enabled":true}')
    path.chmod(0o600)
    original = os.read

    def changing(fd, length):
        value = original(fd, length)
        replacement = tmp_path / "replacement"
        replacement.write_text('{"enabled":false}')
        replacement.chmod(0o600)
        os.replace(replacement, path)
        return value

    monkeypatch.setattr(cli.os, "read", changing)
    with pytest.raises(ValueError):
        cli._read_protected_json(str(path))


def test_duplicate_unknown_exit_is_nonzero(monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "run", lambda **_: cli.NotificationResult("DUPLICATE", "ALREADY_RESERVED", "UNKNOWN")
    )
    assert (
        cli.main(
            [
                "--calendar-root",
                "/ignored",
                "--run-id",
                "id",
                "--review-id",
                "id",
                "--trusted-local-posix",
            ]
        )
        == 1
    )
    assert json.loads(capsys.readouterr().out)["journal_status"] == "UNKNOWN"


@pytest.mark.parametrize("mode", ["auto", "manual"])
def test_mixed_notification_original_association_and_semantic_change(tmp_path, mode):
    from datetime import date

    from bt_api_ctp.calendar_akshare_fetch import collect_akshare_contract

    from bt_api_py import trading_calendar_akshare_observation as observation
    from bt_api_py import trading_calendar_detail_collection as storage
    from bt_api_py.trading_calendar_mock import SOURCE
    from tests.unit.test_trading_calendar_mixed_simulation_version import capture, template_fixture

    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"calendars": {"root": str(root), "review_mode": mode}}))
    env = env_without_network(
        tmp_path,
        """
from tests.unit.test_trading_calendar_mixed_simulation_version import template_fixture
from bt_api_py import trading_calendar_mock as mock
mock.build = lambda year: (template_fixture(), {'sources': [mock.SOURCE], 'trading_days': [template_fixture()['shards'][0]['layers']['trading_days'][0]], 'trading_hours': [], 'exceptions': []})
""",
    )
    code = """
import json, sys
from bt_api_py import trading_calendar_observation_run as runs
result = runs.run_once(root=sys.argv[1], config=sys.argv[2], supplement=None, budget_seconds=30,
 enable_shfe=False, filesystem='TRUSTED_LOCAL_POSIX', run_id='a'*32, capture_id='b'*32,
 parent_id='c'*32, derived_id='d'*32, mock_year=2026, emit=lambda _:None)
print(json.dumps(result))
"""
    process = subprocess.run(
        [sys.executable, "-c", code, str(root), str(config)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert process.returncode == 0 and json.loads(process.stdout)["candidate_state"] == "COMMITTED"
    operator = cli.MockCalendarPublication(root=str(root), run_id="a" * 32)
    assert operator.initialize(mode=mode)["status"] == "INITIALIZED"
    supplement = {
        "sources": [SOURCE],
        "trading_days": [template_fixture()["shards"][0]["layers"]["trading_days"][0]],
        "trading_hours": [],
        "exceptions": [],
    }
    (root / "supplement.yaml").write_text(json.dumps(supplement))
    capture(root, "e" * 32, "a" * 32, "日盘 09:00-10:00")
    flags = (
        "--mixed-observation-id",
        "e" * 32,
        "--mixed-observation-run-id",
        "a" * 32,
        "--mixed-venue",
        "SHFE",
        "--mixed-product",
        "pfuture",
        "--mixed-contract",
        "mock_shfe_future_contract",
        "--mixed-valid-from",
        "2026-12-31",
        "--mixed-valid-to",
        "2027-01-04",
        "--mixed-day-phase",
        "continuous",
        "--mixed-night-phase",
        "continuous",
        "--mixed-timezone",
        "Asia/Shanghai",
        "--mixed-alignment",
        "REPEAT_OVER_MOCK_OWNER_DAY_MAPPING",
    )
    rc, rows, err = command(env, *operation(root, "a" * 32, "mock-update", *flags))
    assert rc == (0 if mode == "auto" else 10), (rows, err)
    first = rows[-1]
    assert first["status"] == ("COMMITTED" if mode == "auto" else "PENDING_REVIEW")
    sent = []

    def sender(value):
        sent.append(value.body)
        return BaseEmailReceipt(value.recipients, {})

    assert (
        attempt(root, first["run_id"], first, tmp_path, kind=mode, dry_run=True).reason
        == "DRY_RUN_ELIGIBLE"
    )
    assert (
        attempt(root, first["run_id"], first, tmp_path, kind=mode, sender=sender).status
        == "ACCEPTED"
    )
    assert "SIMULATION_ONLY / AKSHARE_MOCK" in sent[-1]
    assert "=AKSHARE" in sent[-1] and "=MOCK" in sent[-1] and "Date alignment UNKNOWN" in sent[-1]
    if mode == "manual":
        rc, rows, err = command(
            env,
            *operation(
                root,
                first["run_id"],
                "mock-confirm",
                "--review-id",
                first["review_id"],
                "--review-sha256",
                first["review_payload_sha256"],
                "--reviewer",
                "MOCK_reviewer",
                "--confirmed-at",
                "2026-11-13T00:00:00.000000Z",
            ),
        )
        assert rc == 0, (rows, err)

    def collector(**kwargs):
        return collect_akshare_contract(
            **kwargs,
            _runner=lambda *_: (
                "CHILD_OK",
                {"status": "OK", "rows": [["交易时间", "日盘 09:00-10:00"]]},
            ),
        )

    observed = observation.observe(
        root=str(root),
        observation_id="9" * 32,
        run_id="a" * 32,
        venue="SHFE",
        contract="RB2702",
        requested_date=date(2026, 11, 13),
        timeout=1,
        filesystem="TRUSTED_LOCAL_POSIX",
        _collector=collector,
    )
    assert observed["record_state"] == "COMMITTED"
    next_flags = (flags[0], "9" * 32, *flags[2:])
    rc, rows, err = command(env, *operation(root, "a" * 32, "mock-update", *next_flags))
    assert rc == (0 if mode == "auto" else 10), (rows, err)
    changed = rows[-1]
    if mode == "manual":
        difference = changed["proposal"]["difference"]
    else:
        difference = cli.MockCalendarPublication(
            root=str(root), run_id=changed["run_id"]
        )._store.read_publication_policy_readonly(changed["publication_id"], budget=cli._BUDGET)[
            "difference"
        ]
    assert difference["schema"] == "bt_api.calendar-full-declaration-difference/2"
    assert not any(difference["changes"].values())
    assert (
        difference["simulation"]["before"]["semantic_sha256"]
        != difference["simulation"]["after"]["semantic_sha256"]
    )
    evidence_path = root / "review-bundles" / f"mock-mixed-{changed['candidate_id']}.json"
    raw = evidence_path.read_bytes()
    body, _ = storage._unwrap(raw)
    body["provenance"]["evidence_locator"]["parent_candidate_id"] = "0" * 32
    evidence_path.write_bytes(storage._envelope(body))
    assert (
        attempt(root, changed["run_id"], changed, tmp_path, kind=mode, sender=sender).status
        == "NOT_SENT"
    )
    evidence_path.write_bytes(raw)
    assert (
        attempt(root, changed["run_id"], changed, tmp_path, kind=mode, sender=sender).status
        == "ACCEPTED"
    )
    assert "Simulation semantics: CHANGED" in sent[-1] and "SHFE" in sent[-1]
