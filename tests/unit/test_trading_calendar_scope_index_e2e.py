"""Offline real-Store checks of maintenance migration and indexed history."""

import pytest

from tests.unit.test_trading_calendar_daily_batch import _initialized_batch_root
from tests.unit.test_trading_calendar_mock_cli_integration import command
from tests.unit.test_trading_calendar_mock_persistent import operation
from tests.unit.test_trading_calendar_scope_checks import (
    _FAIL_DIRECTORY_FSYNC,
    _FAIL_HEAD,
    batch_args,
)

_SQL_FAILURE = """
from bt_api_py import trading_calendar_scope_index as scope_index
_scope_real_apply = scope_index._apply_intent
def uncertain_apply(connection, intent):
    if os.environ.get('SCOPE_SQL_FAILURE') == 'BEFORE':
        raise OSError('injected before commit')
    value = _scope_real_apply(connection, intent)
    if os.environ.get('SCOPE_SQL_FAILURE') == 'AFTER':
        raise OSError('injected after commit')
    return value
scope_index._apply_intent = uncertain_apply
"""


def migrate(root, env, run_id, *, legacy=(), migration_id="e" * 32):
    ids = tuple(value for item in legacy for value in ("--legacy-scope-batch-id", item))
    rc, started, err = command(
        env,
        *operation(
            root,
            run_id,
            "mock-scope-migrate-begin",
            "--migration-id",
            migration_id,
            "--declare-old-writers-stopped",
            *ids,
        ),
    )
    assert started[-1]["status"] == "PENDING_MIGRATION", (started, err)
    rc, ready, err = command(
        env,
        *operation(
            root,
            run_id,
            "mock-scope-migrate-resume",
            "--migration-id",
            migration_id,
            "--declare-old-writers-stopped",
        ),
    )
    assert rc == 0 and ready[-1]["reason"] == "MIGRATION_READY", (ready, err)
    return ready[-1]


def test_paginated_audit_and_current_source_revalidation(tmp_path):
    root, env, run_id = _initialized_batch_root(tmp_path, "auto")
    rc, first, err = command(env, *batch_args(root, run_id))
    assert rc == 0, (first, err)
    migrate(root, env, run_id, legacy=[first[-1]["batch_id"]])
    env["FAKE_AK_END"] = "10:30"
    rc, second, err = command(env, *batch_args(root, run_id))
    assert rc == 0 and second[-1]["status"] == "COMMITTED", (second, err)
    audit_id = "d" * 32

    def audit(action):
        return command(
            env,
            *operation(
                root, run_id, "mock-scope-audit-" + action, "--audit-id", audit_id, "--limit", "1"
            ),
        )

    rc, begun, err = audit("begin")
    assert rc == 0 and begun[-1]["history_audit"] == "FULL_HISTORY_NOT_AUDITED", (begun, err)
    rc, page1, err = audit("page")
    assert rc == 0 and page1[-1]["audit_status"] == "IN_PROGRESS", (page1, err)
    rc, page2, err = audit("page")
    assert rc == 0 and page2[-1]["history_audit"] == "PAGED_AUDIT_COMPLETED_NOT_CONTINUOUS", (
        page2,
        err,
    )
    current_oid = second[-1]["observations"][0]["observation_id"]
    current_path = root / "akshare-observations" / f"result-{current_oid}.json"
    current_content = current_path.read_bytes()
    current_path.unlink()
    rc, repeated, err = audit("page")
    assert rc == 0 and repeated[-1]["reason"] == "ALREADY_COMPLETE", (repeated, err)
    assert repeated[-1]["show_assurance"] == "LOCAL_PROGRESS_NOT_REPLAYED"
    current_path.write_bytes(current_content)
    current_path.chmod(0o600)
    old_path = root / "observation-runs" / f"scope-check-batch-{first[-1]['batch_id']}.json"
    old_path.unlink()
    rc, current, err = command(
        env, *operation(root, run_id, "mock-batch-show", "--batch-id", second[-1]["batch_id"])
    )
    assert rc == 0 and current[-1]["scope_checks"]["history_audit"] == "NOT_PERFORMED", (
        current,
        err,
    )
    new_audit_id = "c" * 32
    rc, begun, err = command(
        env, *operation(root, run_id, "mock-scope-audit-begin", "--audit-id", new_audit_id)
    )
    assert rc == 0, (begun, err)
    rc, corrupt, err = command(
        env,
        *operation(
            root, run_id, "mock-scope-audit-page", "--audit-id", new_audit_id, "--limit", "1"
        ),
    )
    assert rc != 0 and corrupt[-1]["history_audit"] == "FULL_HISTORY_NOT_AUDITED", (corrupt, err)
    oid = second[-1]["observations"][0]["observation_id"]
    (root / "akshare-observations" / f"result-{oid}.json").unlink()
    rc, missing, err = command(
        env, *operation(root, run_id, "mock-batch-show", "--batch-id", second[-1]["batch_id"])
    )
    assert rc != 0 and missing[-1]["status"] == "UNKNOWN", (missing, err)


@pytest.mark.parametrize("fault", ["BEFORE", "AFTER"])
def test_index_transaction_unknown_recovers_by_original_id(tmp_path, fault):
    root, env, run_id = _initialized_batch_root(tmp_path, "auto", _SQL_FAILURE)
    migrate(root, env, run_id)
    before = (root / "CURRENT.json").read_bytes()
    env["SCOPE_SQL_FAILURE"] = fault
    rc, rows, err = command(env, *batch_args(root, run_id))
    assert rc != 0 and rows[-1]["reason"] == "SCOPE_CHECK_RECONCILE_FIRST", (rows, err)
    result = rows[-1]
    calls = (tmp_path / "calls").read_bytes()
    env.pop("SCOPE_SQL_FAILURE")
    rc, recovered, err = command(
        env,
        *operation(
            root,
            run_id,
            "mock-batch-show",
            "--batch-id",
            result["batch_id"],
            "--reconcile-scope-checks",
        ),
    )
    assert rc == 0 and recovered[-1]["scope_check_reconciliation"]["reason"] in {
        "RECONCILED",
        "ALREADY_RECORDED",
    }, (recovered, err)
    assert (tmp_path / "calls").read_bytes() == calls
    assert (root / "CURRENT.json").read_bytes() == before


@pytest.mark.parametrize("fault", ["BEFORE", "AFTER", "FSYNC"])
def test_migration_gate_uncertain_recovers_same_id(tmp_path, fault):
    root, env, run_id = _initialized_batch_root(
        tmp_path, "auto", _FAIL_HEAD + _FAIL_DIRECTORY_FSYNC
    )
    before = (root / "CURRENT.json").read_bytes()
    migration_id = "f" * 32
    if fault == "FSYNC":
        env["FAIL_SCOPE_DIR_FSYNC"] = "1"
    else:
        env["SCOPE_HEAD_FAILURE"] = fault
    args = operation(
        root,
        run_id,
        "mock-scope-migrate-begin",
        "--migration-id",
        migration_id,
        "--declare-old-writers-stopped",
    )
    rc, unknown, err = command(env, *args)
    assert rc != 0 and unknown[-1]["status"] == "UNKNOWN", (unknown, err)
    assert unknown[-1]["old_writers"] == "OLD_WRITERS_STOPPED_DECLARED_NOT_VERIFIED"
    env.pop("FAIL_SCOPE_DIR_FSYNC", None)
    env.pop("SCOPE_HEAD_FAILURE", None)
    rc, shown, err = command(
        env, *operation(root, run_id, "mock-scope-migrate-show", "--migration-id", migration_id)
    )
    assert shown[-1]["reason"] == (
        "MIGRATION_GATE_NOT_OBSERVED" if fault == "BEFORE" else "MIGRATION_GATE_VISIBLE"
    ), (shown, err)
    rc, ready, err = command(
        env,
        *operation(
            root,
            run_id,
            "mock-scope-migrate-resume",
            "--migration-id",
            migration_id,
            "--declare-old-writers-stopped",
        ),
    )
    assert rc == 0 and ready[-1]["reason"] == "MIGRATION_READY", (ready, err)
    assert (root / "CURRENT.json").read_bytes() == before


@pytest.mark.parametrize("fault", ["DB_LINK", "JOURNAL_LINK", "WAL_LINK"])
def test_index_rejects_linked_db_or_journal_on_restart(tmp_path, fault):
    root, env, run_id = _initialized_batch_root(tmp_path, "auto")
    rc, first, err = command(env, *batch_args(root, run_id))
    assert rc == 0, (first, err)
    ready = migrate(root, env, run_id, legacy=[first[-1]["batch_id"]])
    db = root / "observation-runs" / f"scope-check-index-{ready['database_id']}.sqlite3"
    if fault == "DB_LINK":
        db.rename(db.with_suffix(".saved"))
        db.symlink_to(tmp_path / "missing")
    else:
        db.with_name(db.name + ("-journal" if fault == "JOURNAL_LINK" else "-wal")).symlink_to(
            tmp_path / "missing"
        )
    before = (root / "CURRENT.json").read_bytes()
    rc, shown, err = command(
        env, *operation(root, run_id, "mock-batch-show", "--batch-id", first[-1]["batch_id"])
    )
    assert rc != 0 and shown[-1]["status"] == "UNKNOWN", (shown, err)
    assert (root / "CURRENT.json").read_bytes() == before
