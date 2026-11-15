"""Local SQLite scope-index primitives; synthetic sequence is not source evidence."""

import sqlite3

import pytest

from bt_api_py import trading_calendar_detail_collection as storage
from bt_api_py import trading_calendar_observation_run as runs
from bt_api_py import trading_calendar_scope_index as index


def test_731_transactional_records_and_immutable_rows(tmp_path):
    root = tmp_path / "calendar"
    root.mkdir(mode=0o700)
    fds, chain = runs._open_runs(str(root), create=True)
    try:
        database_id, migration_id = "a" * 32, "b" * 32
        index.create(
            root=str(root),
            fd=fds[-1],
            chain=chain,
            database_id=database_id,
            migration_id=migration_id,
            records=[],
        )
        previous = None
        with index.connect(
            root=str(root),
            fd=fds[-1],
            chain=chain,
            database_id=database_id,
            migration_id=migration_id,
            write=True,
        ) as connection:
            for day in range(1, 732):
                record = {
                    "batch_id": f"{day:032x}",
                    "sequence": day,
                    "previous_sha256": previous,
                    "entries": [
                        {
                            "target": {"venue": "SHFE"},
                            "check_status": "FAILED" if day % 5 == 0 else "SUCCESS",
                            "success_proof": {"requested_contract": "RB2701"},
                        }
                    ],
                }
                digest = storage._unwrap(storage._envelope(record))[1]
                connection.execute("BEGIN IMMEDIATE")
                index.insert_record(connection, record, digest, legacy=False)
                connection.execute("COMMIT")
                previous = digest
            assert connection.execute("SELECT sequence FROM metadata").fetchone() == (731,)
            assert connection.execute("SELECT max(sequence) FROM scope_success").fetchone() == (
                731,
            )
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute("DELETE FROM records WHERE sequence=1")
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute("UPDATE records SET sequence=0 WHERE sequence=1")
        with index.connect(
            root=str(root),
            fd=fds[-1],
            chain=chain,
            database_id=database_id,
            migration_id=migration_id,
            write=False,
        ) as connection:
            assert connection.execute("SELECT count(*) FROM records").fetchone() == (731,)
    finally:
        runs._close(fds)


def test_index_rejects_symlink_and_schema_changes(tmp_path):
    root = tmp_path / "calendar"
    root.mkdir(mode=0o700)
    fds, chain = runs._open_runs(str(root), create=True)
    try:
        database_id, migration_id = "a" * 32, "b" * 32
        path = root / "observation-runs" / f"scope-check-index-{database_id}.sqlite3"
        path.symlink_to(tmp_path / "elsewhere")
        with (
            pytest.raises(ValueError),
            index.connect(
                root=str(root),
                fd=fds[-1],
                chain=chain,
                database_id=database_id,
                migration_id=migration_id,
                write=False,
            ),
        ):
            pass
        path.unlink()
        index.create(
            root=str(root),
            fd=fds[-1],
            chain=chain,
            database_id=database_id,
            migration_id=migration_id,
            records=[],
        )
        with sqlite3.connect(str(path)) as raw:
            raw.execute("DROP TRIGGER records_no_update")
        with (
            pytest.raises(ValueError),
            index.connect(
                root=str(root),
                fd=fds[-1],
                chain=chain,
                database_id=database_id,
                migration_id=migration_id,
                write=False,
            ),
        ):
            pass
    finally:
        runs._close(fds)


def test_declared_maintenance_migrates_legacy_and_updates_real_store(tmp_path):
    import json

    from tests.unit.test_trading_calendar_daily_batch import _initialized_batch_root
    from tests.unit.test_trading_calendar_mock_cli_integration import command
    from tests.unit.test_trading_calendar_mock_persistent import operation
    from tests.unit.test_trading_calendar_scope_checks import batch_args

    root, env, run_id = _initialized_batch_root(tmp_path, "auto")
    rc, first, err = command(env, *batch_args(root, run_id))
    assert rc == 0, (first, err)
    first_batch = first[-1]["batch_id"]
    path = root / "observation-runs" / f"scope-check-batch-{first_batch}.json"
    original = path.read_bytes()
    current = (root / "CURRENT.json").read_bytes()
    migration_id = "e" * 32
    begin_args = operation(
        root,
        run_id,
        "mock-scope-migrate-begin",
        "--migration-id",
        migration_id,
        "--legacy-scope-batch-id",
        first_batch,
    )
    rc, denied, err = command(env, *begin_args)
    assert rc != 0 and denied[-1]["old_writers"] == "NOT_DECLARED", (denied, err)
    rc, started, err = command(env, *begin_args, "--declare-old-writers-stopped")
    assert rc != 0 and started[-1]["status"] == "PENDING_MIGRATION", (started, err)
    assert started[-1]["old_writers"] == "OLD_WRITERS_STOPPED_DECLARED_NOT_VERIFIED"
    gate = json.loads((root / "observation-runs" / "scope-check-head.json").read_bytes())["body"]
    assert gate["state"] == "MIGRATING"
    rc, shown, err = command(
        env, *operation(root, run_id, "mock-scope-migrate-show", "--migration-id", migration_id)
    )
    assert shown[-1]["status"] == "PENDING_MIGRATION", (shown, err)
    rc, resumed, err = command(
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
    assert rc == 0 and resumed[-1]["reason"] == "MIGRATION_READY", (resumed, err)
    assert resumed[-1]["history_audit"] == "NOT_PERFORMED"
    assert path.read_bytes() == original and (root / "CURRENT.json").read_bytes() == current
    rc, update, err = command(env, *batch_args(root, run_id))
    assert update[-1]["status"] in {"COMMITTED", "SAME"}, (update, err)
    assert update[-1]["scope_checks"]["history_audit"] == "NOT_PERFORMED"
    rc, shown, err = command(
        env, *operation(root, run_id, "mock-batch-show", "--batch-id", first_batch)
    )
    assert (
        rc == 0
        and shown[-1]["scope_checks"]["current_scope_evidence"]
        == "REVALIDATED_LOCALLY_UNAUTHENTICATED"
    ), (shown, err)
