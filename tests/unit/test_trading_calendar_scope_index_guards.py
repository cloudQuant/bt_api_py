"""Regression tests for local index sequencing, barriers and link guards."""

import os
import sqlite3

import pytest

from tests.unit.test_trading_calendar_daily_batch import _initialized_batch_root
from tests.unit.test_trading_calendar_mock_cli_integration import command
from tests.unit.test_trading_calendar_mock_persistent import operation
from tests.unit.test_trading_calendar_scope_checks import batch_args
from tests.unit.test_trading_calendar_scope_index_e2e import migrate

_PENDING_FAULT = """
from bt_api_py import trading_calendar_observation_run as runs
_real_scope_write = runs._write_once
def failed_scope_pending(fd, chain, name, data):
    if name == 'scope-check-index-pending.json' and os.environ.get('PENDING_FAULT'):
        if os.environ['PENDING_FAULT'] == 'BEFORE':
            return ('NOT_COMMITTED', 'IO_ERROR')
        result = _real_scope_write(fd, chain, name, data)
        if result[0] == 'COMMITTED':
            return ('COMMIT_UNKNOWN', 'IO_ERROR')
        return result
    return _real_scope_write(fd, chain, name, data)
runs._write_once = failed_scope_pending
"""


def test_sqlite_cannot_roll_back_success_or_skip_audit_page(tmp_path):
    root, env, run_id = _initialized_batch_root(tmp_path, "auto")
    ready = migrate(root, env, run_id)
    rc, first, err = command(env, *batch_args(root, run_id))
    assert rc == 0, (first, err)
    rc, second, err = command(env, *batch_args(root, run_id))
    assert second[-1]["status"] == "SAME", (second, err)
    audit_id = "c" * 32
    rc, started, err = command(
        env, *operation(root, run_id, "mock-scope-audit-begin", "--audit-id", audit_id)
    )
    assert rc == 0, (started, err)
    db = root / "observation-runs" / f"scope-check-index-{ready['database_id']}.sqlite3"
    with sqlite3.connect(str(db)) as raw:
        with pytest.raises(sqlite3.DatabaseError):
            raw.execute("UPDATE scope_success SET sequence=1 WHERE sequence=2")
        with pytest.raises(sqlite3.DatabaseError):
            raw.execute("DELETE FROM scope_success WHERE sequence=2")
        with pytest.raises(sqlite3.DatabaseError):
            raw.execute("UPDATE audits SET head_sequence=1 WHERE audit_id=?", (audit_id,))
        with pytest.raises(sqlite3.DatabaseError):
            raw.execute(
                "INSERT INTO audit_pages VALUES(?,?,?,?,?,?)", (audit_id, 1, 1, 3, None, "bogus")
            )
    rc, checked, err = command(
        env, *operation(root, run_id, "mock-batch-show", "--batch-id", second[-1]["batch_id"])
    )
    assert rc == 0, (checked, err)
    assert all(
        item["last_success"]["sequence"] == 2 for item in checked[-1]["scope_checks"]["scopes"]
    )
    rc, page, err = command(
        env,
        *operation(root, run_id, "mock-scope-audit-page", "--audit-id", audit_id, "--limit", "1"),
    )
    assert rc == 0 and page[-1]["audit_status"] == "IN_PROGRESS", (page, err)
    with sqlite3.connect(str(db)) as raw:
        with pytest.raises(sqlite3.DatabaseError):
            raw.execute("UPDATE audit_pages SET next_sequence=3 WHERE audit_id=?", (audit_id,))
        with pytest.raises(sqlite3.DatabaseError):
            raw.execute("DELETE FROM audit_pages WHERE audit_id=?", (audit_id,))


@pytest.mark.parametrize("fault", ["BEFORE", "AFTER"])
def test_atomic_pending_barrier_does_not_leave_orphan_intent(tmp_path, fault):
    root, env, run_id = _initialized_batch_root(tmp_path, "auto", _PENDING_FAULT)
    migrate(root, env, run_id)
    env["PENDING_FAULT"] = fault
    before = (root / "CURRENT.json").read_bytes()
    rc, rows, err = command(env, *batch_args(root, run_id))
    assert rc != 0 and rows[-1]["status"] == "UNKNOWN", (rows, err)
    batch_id = rows[-1]["batch_id"]
    directory = root / "observation-runs"
    assert not list(directory.glob("scope-check-index-intent-*.json"))
    pending = directory / "scope-check-index-pending.json"
    assert pending.exists() == (fault == "AFTER")
    env.pop("PENDING_FAULT")
    if fault == "AFTER":
        calls = (tmp_path / "calls").read_bytes()
        rc, later, err = command(env, *batch_args(root, run_id))
        assert rc != 0 and later[-1]["status"] == "UNKNOWN", (later, err)
        assert later[-1]["reason"] == "SCOPE_INDEX_RECONCILE_ORIGINAL_ID"
        assert later[-1]["old_writers"] == "OLD_WRITERS_STOPPED_DECLARED_NOT_VERIFIED"
        assert later[-1]["batch_id"] == batch_id and (tmp_path / "calls").read_bytes() == calls
        assert (root / "CURRENT.json").read_bytes() == before
        rc, recovered, err = command(
            env,
            *operation(
                root, run_id, "mock-batch-show", "--batch-id", batch_id, "--reconcile-scope-checks"
            ),
        )
        assert rc == 0 and recovered[-1]["scope_check_reconciliation"]["reason"] == "RECONCILED", (
            recovered,
            err,
        )
    else:
        rc, absent, err = command(
            env,
            *operation(
                root, run_id, "mock-batch-show", "--batch-id", batch_id, "--reconcile-scope-checks"
            ),
        )
        assert (
            rc != 0
            and absent[-1]["scope_check_reconciliation"]["reason"] == "ORIGINAL_RECORD_NOT_OBSERVED"
        ), (absent, err)
    assert (root / "CURRENT.json").read_bytes() == before


@pytest.mark.parametrize("suffix", ["", "-journal", "-wal", "-shm"])
def test_hard_linked_sqlite_files_are_rejected(tmp_path, suffix):
    root, env, run_id = _initialized_batch_root(tmp_path, "auto")
    ready = migrate(root, env, run_id)
    rc, first, err = command(env, *batch_args(root, run_id))
    assert rc == 0, (first, err)
    db = root / "observation-runs" / f"scope-check-index-{ready['database_id']}.sqlite3"
    path = db.with_name(db.name + suffix)
    outside = tmp_path / ("outside" + (suffix or "-db"))
    if suffix:
        outside.write_bytes(b"not a journal")
        outside.chmod(0o600)
        os.link(outside, path)
    else:
        os.link(path, outside)
    assert path.stat().st_nlink == 2
    before = (root / "CURRENT.json").read_bytes()
    calls = (tmp_path / "calls").read_bytes()
    rc, stopped, err = command(env, *batch_args(root, run_id))
    assert rc != 0 and stopped[-1]["status"] == "UNKNOWN", (stopped, err)
    assert (tmp_path / "calls").read_bytes() == calls
    rc, denied, err = command(
        env, *operation(root, run_id, "mock-batch-show", "--batch-id", first[-1]["batch_id"])
    )
    assert rc != 0 and denied[-1]["status"] == "UNKNOWN", (denied, err)
    assert (root / "CURRENT.json").read_bytes() == before
