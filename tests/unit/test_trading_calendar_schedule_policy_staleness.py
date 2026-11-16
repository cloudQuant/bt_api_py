"""D67-27: a manual-to-auto policy change never publishes the old pending review.

Every collector here is the synthetic offline mock; no account, provider or SMTP
path is touched.
"""

import json
import sqlite3
import subprocess
import sys
import threading

import pytest

from bt_api_py import trading_calendar_schedule_once as schedule
from tests.unit.test_trading_calendar_mock_cli_integration import command
from tests.unit.test_trading_calendar_mock_policy_transition import begin, policy, set_mode
from tests.unit.test_trading_calendar_schedule_once import (
    INSTANT,
    _active_read_command,
    _confirm_original_review,
    _schedule_subprocess,
    _snapshot_tree,
    execute,
    setup_root,
)

CHANGED = "POLICY_CHANGED_REQUIRES_FRESH_UPDATE"


def start_change(env, root, original, mode, operation_id):
    rc, rows, err = begin(env, root, original, mode, operation_id)
    assert rc == 10 and rows[-1]["status"] == "PENDING_CONFIG", (rows, err)


def finish_change(tmp_path, env, root, original, mode, operation_id):
    set_mode(tmp_path / "config.json", root, mode)
    rc, rows, err = command(env, *policy(root, original, "resume", "--operation-id", operation_id))
    assert rc == 0 and rows[-1]["policy"]["mode"] == mode, (rows, err)


def switch_mode(tmp_path, env, root, original, mode, operation_id):
    start_change(env, root, original, mode, operation_id)
    finish_change(tmp_path, env, root, original, mode, operation_id)


def folder(root):
    return root / "observation-runs"


def bundles(root):
    return root / "review-bundles"


def ledger_row(root, operation_id):
    path = folder(root) / schedule._LEDGER_FILE
    db = sqlite3.connect(str(path))
    try:
        db.execute("PRAGMA query_only=ON")
        return db.execute(
            "SELECT state,effective,returned_sha256,terminal_sha256,requested_date "
            "FROM operations WHERE operation_id=?",
            (operation_id,),
        ).fetchone()
    finally:
        db.close()


def definitions(root):
    path = folder(root) / schedule._LEDGER_FILE
    db = sqlite3.connect(str(path))
    try:
        return dict(
            db.execute("SELECT name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")
        )
    finally:
        db.close()


def marker_body(root):
    raw = (folder(root) / schedule._LEDGER_MARKER).read_bytes()
    return schedule.storage._unwrap(raw)[0]


def records(root, operation_id, kind):
    path = folder(root) / schedule._name(kind, operation_id)
    return schedule.storage._unwrap(path.read_bytes())


def retained_files(root, result):
    ids = result["identity_bundle"]
    names = [
        f"mock-daily-batch-intent-{ids['batch_id']}.json",
        f"mock-daily-batch-{ids['batch_id']}.json",
    ]
    paths = [folder(root) / name for name in names]
    paths += [bundles(root) / name for name in (
        f"mock-mixed-{ids['candidate_id']}.json",
        f"mock-review-{ids['review_id']}.json",
    )]
    paths += [
        root / "akshare-observations" / f"result-{row['observation_id']}.json"
        for row in ids["observations"]
    ]
    return {str(path): path.read_bytes() for path in paths}


def test_policy_change_closes_review_non_effectively_and_republishes(tmp_path, capsys):
    root, env, config, path = setup_root(tmp_path, mode="manual")
    first = execute(env, _schedule_subprocess(env, path))
    assert first["status"] == "PENDING_REVIEW", first
    ids = first["identity_bundle"]
    assert len((tmp_path / "calls").read_text().splitlines()) == 2
    current = (root / "CURRENT.json").read_bytes()
    kept = retained_files(root, first)

    switch_mode(tmp_path, env, root, config["original_run_id"], "auto", "MOCK_stale_to_auto")

    quiet = _snapshot_tree(root)
    active = execute(env, _active_read_command(root))
    assert active["status"] == "UNKNOWN", active
    assert active["reason"] == CHANGED, active
    assert active["operation_id"] == first["operation_id"]
    assert active["identity_bundle"] == ids
    assert _snapshot_tree(root) == quiet
    assert (
        schedule.main(
            ["--action", "inspect-active", "--root", str(root), "--trusted-local-posix"]
        )
        == 25
    )
    cli = json.loads(capsys.readouterr().out)
    assert cli["status"] == "UNKNOWN" and cli["reason"] == CHANGED
    assert cli["operation_id"] == first["operation_id"] and cli["identity_bundle"] == ids
    assert _snapshot_tree(root) == quiet
    assert len((tmp_path / "calls").read_text().splitlines()) == 2

    second = execute(env, _schedule_subprocess(env, path))
    assert second["status"] == "COMMITTED", second
    assert second["operation_id"] != first["operation_id"]
    assert second["requested_date"] == first["requested_date"]
    assert len((tmp_path / "calls").read_text().splitlines()) == 4
    for name, data in kept.items():
        with open(name, "rb") as stream:
            assert stream.read() == data, name
    assert (root / "CURRENT.json").read_bytes() != current

    state, effective, returned_sha, terminal_sha, day = ledger_row(root, first["operation_id"])
    assert (state, effective, returned_sha, terminal_sha) == (
        "TERMINAL",
        0,
        records(root, first["operation_id"], "returned")[1],
        records(root, first["operation_id"], "terminal")[1],
    )
    terminal, terminal_sha = records(root, first["operation_id"], "terminal")
    assert terminal["outcome"] == "STALE_REVIEW_CLOSED"
    assert terminal["proof"]["publication"] == "NOT_ATTEMPTED"
    assert terminal["identity_bundle"] == ids
    disposition, _ = records(root, first["operation_id"], "disposition")
    returned, _ = records(root, first["operation_id"], "returned")
    assert disposition["schema"] == schedule._DISPOSITION
    assert terminal["proof"]["batch_evidence_sha256"] == returned["batch_evidence_sha256"]
    assert disposition["batch_evidence_sha256"] == returned["batch_evidence_sha256"]
    assert len(disposition["candidate_body_sha256"]) == 64
    assert disposition["effective"] is False and disposition["outcome"] == "STALE_REVIEW_CLOSED"
    assert disposition["current_policy"]["mode"] == "auto"
    assert disposition["previous_policy"]["mode"] == "manual"
    assert disposition["previous_policy"]["revision"] != disposition["current_policy"]["revision"]
    index, _ = schedule.storage._unwrap(
        (folder(root) / schedule._date_name(config["job_key"], day)).read_bytes()
    )
    assert index["operation_id"] == second["operation_id"]
    assert marker_body(root)["schema"] == schedule._LEDGER_SCHEMA

    shown = execute(
        env,
        _schedule_subprocess(env, path, code="inspect") + [str(root), first["operation_id"]],
    )
    assert shown["status"] == "LOCAL_HISTORICAL_RECORD_ONLY", shown
    assert shown["effective"] is False and shown["date_completed"] is False
    assert shown["terminal"]["outcome"] == "STALE_REVIEW_CLOSED"
    refused = execute(
        env,
        _schedule_subprocess(env, path, code="reconcile") + [str(root), first["operation_id"]],
    )
    assert refused["status"] == "UNKNOWN"
    assert refused["reason"] == "NON_EFFECTIVE_REVIEW_CLOSED", refused
    assert len((tmp_path / "calls").read_text().splitlines()) == 4

    repeated = execute(env, _schedule_subprocess(env, path))
    assert repeated["status"] == "ALREADY_COMPLETED", repeated
    assert repeated["operation_id"] == second["operation_id"]
    assert len((tmp_path / "calls").read_text().splitlines()) == 4
    assert execute(env, _active_read_command(root))["status"] == "NONE"


def test_ledger_guards_reject_reactivation_and_early_demotion(tmp_path):
    root, env, config, path = setup_root(tmp_path, mode="manual")
    first = execute(env, _schedule_subprocess(env, path))
    assert first["status"] == "PENDING_REVIEW"
    switch_mode(tmp_path, env, root, config["original_run_id"], "auto", "MOCK_stale_guard")
    second = execute(env, _schedule_subprocess(env, path))
    assert second["status"] == "COMMITTED", second
    db = sqlite3.connect(str(folder(root) / schedule._LEDGER_FILE), isolation_level=None)
    try:
        for statement, args in (
            ("UPDATE operations SET effective=1 WHERE operation_id=?", (first["operation_id"],)),
            ("UPDATE operations SET effective=0 WHERE operation_id=?", (second["operation_id"],)),
            ("DELETE FROM operations WHERE operation_id=?", (first["operation_id"],)),
        ):
            with pytest.raises(sqlite3.DatabaseError):
                db.execute(statement, args)
    finally:
        db.close()
    assert ledger_row(root, first["operation_id"])[:2] == ("TERMINAL", 0)
    assert ledger_row(root, second["operation_id"])[:2] == ("TERMINAL", 1)


def test_confirmed_review_is_not_closed_after_mode_change(tmp_path):
    root, env, config, path = setup_root(tmp_path, mode="manual")
    first = execute(env, _schedule_subprocess(env, path))
    assert first["status"] == "PENDING_REVIEW"
    before = (root / "CURRENT.json").read_bytes()
    _confirm_original_review(env, root, first)
    published = (root / "CURRENT.json").read_bytes()
    assert published != before
    switch_mode(tmp_path, env, root, config["original_run_id"], "auto", "MOCK_stale_confirmed")
    result = execute(env, _schedule_subprocess(env, path))
    assert result["status"] == "UNKNOWN", result
    assert result["operation_id"] == first["operation_id"]
    assert result["identity_bundle"] == first["identity_bundle"]
    assert (root / "CURRENT.json").read_bytes() == published
    assert len((tmp_path / "calls").read_text().splitlines()) == 2
    assert ledger_row(root, first["operation_id"])[:2] == ("RETURNED", 1)
    reconciled = execute(
        env,
        _schedule_subprocess(env, path, code="reconcile") + [str(root), first["operation_id"]],
    )
    assert reconciled["status"] == "COMMITTED", reconciled
    assert reconciled["operation_id"] == first["operation_id"]
    assert ledger_row(root, first["operation_id"])[:2] == ("TERMINAL", 1)
    assert (root / "CURRENT.json").read_bytes() == published


def test_stale_review_kept_when_publication_evidence_is_unverifiable(tmp_path):
    root, env, config, path = setup_root(tmp_path, mode="manual")
    first = execute(env, _schedule_subprocess(env, path))
    assert first["status"] == "PENDING_REVIEW"
    ids = first["identity_bundle"]
    switch_mode(tmp_path, env, root, config["original_run_id"], "auto", "MOCK_stale_loss")
    current = (root / "CURRENT.json").read_bytes()
    (bundles(root) / f"mock-review-{ids['review_id']}.json").unlink()
    before = _snapshot_tree(root)
    for result in (
        execute(env, _active_read_command(root)),
        execute(env, _schedule_subprocess(env, path)),
    ):
        assert result["status"] == "UNKNOWN", result
        assert result["operation_id"] == first["operation_id"]
        assert result["identity_bundle"] == ids
        assert result["reason"] != CHANGED
    assert _snapshot_tree(root) == before
    assert (root / "CURRENT.json").read_bytes() == current
    assert len((tmp_path / "calls").read_text().splitlines()) == 2
    state, effective = ledger_row(root, first["operation_id"])[:2]
    assert (state, effective) == ("RETURNED", 1)


@pytest.mark.parametrize("fault", ["invalid_config", "policy_record", "review"])
def test_inspect_active_policy_detection_requires_committed_chain(tmp_path, fault):
    root, env, config, path = setup_root(tmp_path, mode="manual")
    first = execute(env, _schedule_subprocess(env, path))
    assert first["status"] == "PENDING_REVIEW"
    ids = first["identity_bundle"]
    switch_mode(tmp_path, env, root, config["original_run_id"], "auto", "MOCK_stale_chain")
    record = root / "policy_changes" / "MOCK_stale_chain.json"
    config_path = tmp_path / "config.json"
    restore = None
    if fault == "invalid_config":
        saved = config_path.read_bytes()

        def restore():
            config_path.write_bytes(saved)

        config_path.write_text("invalid: [")
    elif fault == "policy_record":
        saved, saved_mode = record.read_bytes(), record.stat().st_mode & 0o777
        record.unlink()

        def restore():
            record.write_bytes(saved)
            record.chmod(saved_mode)

    else:
        (bundles(root) / f"mock-review-{ids['review_id']}.json").unlink()
    before = _snapshot_tree(root)
    result = execute(env, _active_read_command(root))
    assert result["status"] == "UNKNOWN" and result["reason"] != CHANGED, result
    assert result["operation_id"] == first["operation_id"]
    assert result["identity_bundle"] == ids
    assert _snapshot_tree(root) == before
    assert len((tmp_path / "calls").read_text().splitlines()) == 2
    if restore is not None:
        restore()
        detected = execute(env, _active_read_command(root))
        assert detected["status"] == "UNKNOWN" and detected["reason"] == CHANGED, detected
        assert detected["operation_id"] == first["operation_id"]


def test_policy_change_between_proof_and_locked_recheck_is_read_only(tmp_path):
    root, env, config, path = setup_root(tmp_path, mode="manual")
    first = execute(env, _schedule_subprocess(env, path))
    assert first["status"] == "PENDING_REVIEW"
    code = r"""
import json,sys
from bt_api_py import trading_calendar_schedule_once as s
original=s._batch_proof
def pause(*args, **kwargs):
    result=original(*args, **kwargs)
    if kwargs.get("locked") is None:
        print("ready", flush=True)
        sys.stdin.readline()
    return result
s._batch_proof=pause
print(json.dumps(s.inspect_active(root=sys.argv[1],filesystem=s.FS)),flush=True)
"""
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(root)],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout.readline().strip() == "ready"
        switch_mode(tmp_path, env, root, config["original_run_id"], "auto", "MOCK_stale_race")
        stdout, stderr = process.communicate("continue\n", timeout=60)
        assert process.returncode == 0, stderr
        result = json.loads(stdout.splitlines()[-1])
        assert result["status"] == "UNKNOWN" and result["reason"] == CHANGED, result
        assert result["operation_id"] == first["operation_id"]
        assert result["identity_bundle"] == first["identity_bundle"]
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    assert len((tmp_path / "calls").read_text().splitlines()) == 2


def test_operation_change_between_snapshots_keeps_original_identity(tmp_path, monkeypatch):
    """A closed A plus an active B must never splice A's ID onto B's identity bundle."""
    root, env, config, path = setup_root(tmp_path, mode="manual")
    first = execute(env, _schedule_subprocess(env, path))
    assert first["status"] == "PENDING_REVIEW"
    switch_mode(tmp_path, env, root, config["original_run_id"], "auto", "MOCK_stale_splice")
    original_proof = schedule._batch_proof
    entered, release = threading.Event(), threading.Event()
    seen, output = [], []
    worker = []

    def blocking_batch(**kwargs):
        seen.append(kwargs["identity_bundle"])
        entered.set()
        assert release.wait(30)
        return {
            "status": "UNKNOWN",
            "identity_bundle": kwargs["identity_bundle"],
            "requested_date": kwargs["requested_date"].isoformat(),
        }

    def trigger(*args, **kwargs):
        proof = original_proof(*args, **kwargs)
        if kwargs.get("locked") is None and not worker:
            thread = threading.Thread(
                target=lambda: output.append(
                    schedule.run_once(config_file=str(path), filesystem=schedule.FS)
                )
            )
            worker.append(thread)
            thread.start()
            assert entered.wait(30)
        return proof

    monkeypatch.setattr(schedule.batch, "run", blocking_batch)
    monkeypatch.setattr(schedule, "_batch_proof", trigger)
    try:
        result = schedule.inspect_active(root=str(root), filesystem=schedule.FS)
    finally:
        release.set()
        for thread in worker:
            thread.join(30)

    assert seen and seen[0] != first["identity_bundle"]
    assert result["status"] == "UNKNOWN", result
    assert result["reason"] == "ACTIVE_OPERATION_UNAVAILABLE", result
    assert result["operation_id"] == first["operation_id"]
    assert result["identity_bundle"] == first["identity_bundle"], result
    assert output and output[0]["operation_id"] != first["operation_id"]
    assert output[0]["identity_bundle"] == seen[0]


def test_locked_reverification_failure_keeps_original_review(tmp_path, monkeypatch):
    root, env, config, path = setup_root(tmp_path, mode="manual")
    first = execute(env, _schedule_subprocess(env, path))
    assert first["status"] == "PENDING_REVIEW"
    switch_mode(tmp_path, env, root, config["original_run_id"], "auto", "MOCK_stale_lock")
    original = schedule._review_policy_divergence_locked
    calls = []

    def fail_after_detection(*args):
        calls.append(1)
        if len(calls) == 2:
            raise ValueError("review policy unprovable under the single-writer lock")
        return original(*args)

    monkeypatch.setattr(schedule, "_review_policy_divergence_locked", fail_after_detection)
    monkeypatch.setattr(schedule, "_now_utc", lambda: INSTANT)
    result = schedule.run_once(config_file=str(path), filesystem=schedule.FS)
    assert result["status"] == "UNKNOWN", result
    assert result["operation_id"] == first["operation_id"]
    assert result["identity_bundle"] == first["identity_bundle"]
    assert calls == [1, 1]
    state, effective = ledger_row(root, first["operation_id"])[:2]
    assert (state, effective) == ("RETURNED", 1)
    assert (folder(root) / schedule._PENDING_FILE).exists()
    assert len((tmp_path / "calls").read_text().splitlines()) == 2
    for kind in ("terminal", "disposition"):
        assert not list(folder(root).glob(f"schedule-once-{kind}-*.json"))


def test_stale_closure_interrupted_before_ledger_transition_recovers(tmp_path, monkeypatch):
    root, env, config, path = setup_root(tmp_path, mode="manual")
    first = execute(env, _schedule_subprocess(env, path))
    assert first["status"] == "PENDING_REVIEW"
    switch_mode(tmp_path, env, root, config["original_run_id"], "auto", "MOCK_stale_stage")
    original = schedule._ledger_terminal

    def interrupted(*args, **kwargs):
        if kwargs.get("effective") is False:
            raise OSError("interrupted before the non-effective transition")
        return original(*args, **kwargs)

    monkeypatch.setattr(schedule, "_ledger_terminal", interrupted)
    monkeypatch.setattr(schedule, "_now_utc", lambda: INSTANT)
    failed = schedule.run_once(config_file=str(path), filesystem=schedule.FS)
    assert failed["status"] == "UNKNOWN" and failed["operation_id"] == first["operation_id"]
    state, effective = ledger_row(root, first["operation_id"])[:2]
    assert (state, effective) == ("RETURNED", 1)
    assert (folder(root) / schedule._name("terminal", first["operation_id"])).exists()
    assert (folder(root) / schedule._name("disposition", first["operation_id"])).exists()
    assert len((tmp_path / "calls").read_text().splitlines()) == 2
    monkeypatch.setattr(schedule, "_ledger_terminal", original)
    recovered = execute(env, _schedule_subprocess(env, path))
    assert recovered["status"] == "COMMITTED", recovered
    assert recovered["operation_id"] != first["operation_id"]
    assert ledger_row(root, first["operation_id"])[:2] == ("TERMINAL", 0)
    assert len((tmp_path / "calls").read_text().splitlines()) == 4


def test_stale_closure_interrupted_before_pending_release_recovers(tmp_path, monkeypatch):
    root, env, config, path = setup_root(tmp_path, mode="manual")
    first = execute(env, _schedule_subprocess(env, path))
    assert first["status"] == "PENDING_REVIEW"
    switch_mode(tmp_path, env, root, config["original_run_id"], "auto", "MOCK_stale_release")
    original = schedule._release_pending

    def interrupted(*args):
        raise OSError("interrupted before the pending release")

    monkeypatch.setattr(schedule, "_release_pending", interrupted)
    monkeypatch.setattr(schedule, "_now_utc", lambda: INSTANT)
    failed = schedule.run_once(config_file=str(path), filesystem=schedule.FS)
    assert failed["status"] == "UNKNOWN" and failed["operation_id"] == first["operation_id"]
    assert ledger_row(root, first["operation_id"])[:2] == ("TERMINAL", 0)
    assert (folder(root) / schedule._PENDING_FILE).exists()
    assert len((tmp_path / "calls").read_text().splitlines()) == 2
    monkeypatch.setattr(schedule, "_release_pending", original)
    recovered = execute(env, _schedule_subprocess(env, path))
    assert recovered["status"] == "COMMITTED", recovered
    assert recovered["operation_id"] != first["operation_id"]
    assert not (folder(root) / schedule._PENDING_FILE).exists()
    assert len((tmp_path / "calls").read_text().splitlines()) == 4


def _downgrade_ledger(root):
    path = folder(root) / schedule._LEDGER_FILE
    db = sqlite3.connect(str(path), isolation_level=None)
    try:
        db.execute("PRAGMA journal_mode")
        db.execute("BEGIN IMMEDIATE")
        db.execute("DROP INDEX operations_one_active")
        db.execute("DROP INDEX operations_one_effective_date")
        for name in (
            "operations_no_delete",
            "operations_update_guard",
            "ledger_state_no_delete",
            "ledger_state_update_guard",
        ):
            db.execute("DROP TRIGGER " + name)
        db.execute("ALTER TABLE operations RENAME TO operations_v2")
        db.execute(schedule._LEDGER_DDL_V1[1])
        db.execute(
            "INSERT INTO operations (operation_id,job_key,requested_date,pending,pending_sha256,"
            "state,returned_sha256,terminal_sha256) SELECT operation_id,job_key,requested_date,"
            "pending,pending_sha256,state,returned_sha256,terminal_sha256 FROM operations_v2"
        )
        db.execute("DROP TABLE operations_v2")
        for statement in schedule._LEDGER_DDL_V1[2:]:
            db.execute(statement)
        db.execute("COMMIT")
    finally:
        db.close()
    marker = folder(root) / schedule._LEDGER_MARKER
    body, _ = schedule.storage._unwrap(marker.read_bytes())
    body["schema"] = schedule._LEDGER_SCHEMA_V1
    marker.write_bytes(schedule.storage._envelope(body))
    marker.chmod(0o600)


def test_v1_ledger_refuses_readonly_migration_and_upgrades_on_write(tmp_path):
    root, env, config, path = setup_root(tmp_path)
    first = execute(env, _schedule_subprocess(env, path))
    assert first["status"] == "COMMITTED", first
    _downgrade_ledger(root)
    assert definitions(root) == schedule._expected_definitions(schedule._LEDGER_DDL_V1)
    before = _snapshot_tree(root)
    shown = execute(
        env,
        _schedule_subprocess(env, path, code="inspect") + [str(root), first["operation_id"]],
    )
    assert shown["status"] == "UNKNOWN", shown
    assert _snapshot_tree(root) == before
    assert marker_body(root)["schema"] == schedule._LEDGER_SCHEMA_V1

    again = execute(env, _schedule_subprocess(env, path))
    assert again["status"] == "ALREADY_COMPLETED", again
    assert again["operation_id"] == first["operation_id"]
    assert definitions(root) == schedule._expected_definitions(schedule._LEDGER_DDL)
    assert marker_body(root)["schema"] == schedule._LEDGER_SCHEMA
    assert ledger_row(root, first["operation_id"])[1] == 1
    assert len((tmp_path / "calls").read_text().splitlines()) == 2


def test_interrupted_marker_rewrite_completes_on_next_writable_open(tmp_path, monkeypatch):
    root, env, config, path = setup_root(tmp_path)
    first = execute(env, _schedule_subprocess(env, path))
    assert first["status"] == "COMMITTED", first
    _downgrade_ledger(root)
    monkeypatch.setattr(schedule, "_now_utc", lambda: INSTANT)
    original = schedule._replace

    def interrupted(*args):
        raise OSError("marker rewrite interrupted")

    monkeypatch.setattr(schedule, "_replace", interrupted)
    assert schedule.run_once(config_file=str(path), filesystem=schedule.FS)["status"] == "UNKNOWN"
    assert definitions(root) == schedule._expected_definitions(schedule._LEDGER_DDL)
    assert marker_body(root)["schema"] == schedule._LEDGER_SCHEMA_V1
    monkeypatch.setattr(schedule, "_replace", original)
    again = schedule.run_once(config_file=str(path), filesystem=schedule.FS)
    assert again["status"] == "ALREADY_COMPLETED", again
    assert again["operation_id"] == first["operation_id"]
    assert marker_body(root)["schema"] == schedule._LEDGER_SCHEMA
    assert definitions(root) == schedule._expected_definitions(schedule._LEDGER_DDL)
    assert len((tmp_path / "calls").read_text().splitlines()) == 2
