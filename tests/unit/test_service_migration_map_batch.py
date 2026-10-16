"""Focused real-SQLite tests for atomic MIG03 identity-map registration."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from bt_api_py.service_migration_map import (
    CODE_INVALID_MIGRATION_MAP_ARGUMENT,
    CODE_MIGRATION_MAP_BACKEND_FAILURE,
    CODE_SOURCE_ALREADY_MAPPED,
    CODE_TARGET_ALREADY_BOUND,
    SOURCE_ACCOUNT_CONFIG,
    SOURCE_BARE_ORDER_ID,
    SOURCE_BARE_SYMBOL,
    SOURCE_IN_PROCESS_RUN,
    SOURCE_LEGACY_STRATEGY_ID,
    TARGET_ACCOUNT_KEY,
    TARGET_BOOKED_INSTRUMENT,
    TARGET_ORDER_REF,
    TARGET_RUN_REF,
    TARGET_STRATEGY_REF,
    IdentityRegistration,
    MigrationIdentityStore,
    MigrationMapError,
)


_OLD_EPOCH = "old-deployment-epoch"
_NEW_EPOCH = "new-deployment-epoch"
_RECORDED_AT = "2026-10-16T00:00:00Z"


def _path(tmp_path: Path) -> Path:
    return tmp_path / "identity-map.sqlite3"


def test_batch_registers_all_identity_kinds_in_input_order_and_survives_reopen(
    tmp_path: Path,
) -> None:
    path = _path(tmp_path)
    registrations = (
        IdentityRegistration(
            SOURCE_ACCOUNT_CONFIG,
            "config-revision-a",
            "account-key-a",
            _OLD_EPOCH,
            "captured from the old deployment",
            _RECORDED_AT,
        ),
        IdentityRegistration(
            SOURCE_BARE_ORDER_ID,
            "old-order-17",
            "order-ref-17",
            _OLD_EPOCH,
            recorded_at=_RECORDED_AT,
        ),
        IdentityRegistration(
            SOURCE_BARE_SYMBOL,
            "rb2310",
            "SHFE.rb2310",
            _OLD_EPOCH,
            recorded_at=_RECORDED_AT,
        ),
        IdentityRegistration(
            SOURCE_LEGACY_STRATEGY_ID,
            "strategy-legacy-a",
            "strategy-ref-a",
            _OLD_EPOCH,
            recorded_at=_RECORDED_AT,
        ),
        IdentityRegistration(
            SOURCE_BARE_SYMBOL,
            "rb2310.SHFE",
            "SHFE.rb2310",
            _OLD_EPOCH,
            recorded_at=_RECORDED_AT,
        ),
        IdentityRegistration(
            SOURCE_IN_PROCESS_RUN,
            "run-legacy-17",
            "run-ref-17",
            _OLD_EPOCH,
            recorded_at=_RECORDED_AT,
        ),
    )
    expected_kinds = (
        TARGET_ACCOUNT_KEY,
        TARGET_ORDER_REF,
        TARGET_BOOKED_INSTRUMENT,
        TARGET_STRATEGY_REF,
        TARGET_BOOKED_INSTRUMENT,
        TARGET_RUN_REF,
    )

    with MigrationIdentityStore(path) as store:
        records = store.register_batch(registrations)
        assert len(records) == len(registrations)
        assert tuple(record.target_kind for record in records) == expected_kinds
        assert tuple(record.source_value for record in records) == tuple(
            item.source_value for item in registrations
        )
        assert tuple(record.target_value for record in records) == tuple(
            item.target_value for item in registrations
        )
        assert all(record.recorded_epoch == _OLD_EPOCH for record in records)
        assert all(record.recorded_at == _RECORDED_AT for record in records)
        assert store.registration_count() == len(registrations)
        assert store.bindings_for_target(TARGET_BOOKED_INSTRUMENT, "SHFE.rb2310") == (
            records[2],
            records[4],
        )

    with MigrationIdentityStore(path) as reopened:
        assert reopened.registration_count() == len(registrations)
        for registration, record in zip(registrations, records, strict=True):
            reopened_record = reopened.resolve(
                registration.source_kind, registration.source_value
            ).mapping
            assert reopened_record == record


def test_batch_conflicts_leave_all_new_sources_unallocated(tmp_path: Path) -> None:
    with MigrationIdentityStore(_path(tmp_path)) as store:
        original = store.register(
            SOURCE_BARE_ORDER_ID,
            "old-order-existing",
            "order-ref-existing",
            recorded_epoch=_OLD_EPOCH,
        )

        with pytest.raises(MigrationMapError) as source_conflict:
            store.register_batch(
                (
                    IdentityRegistration(SOURCE_ACCOUNT_CONFIG, "new-config", "account-a"),
                    IdentityRegistration(
                        SOURCE_BARE_ORDER_ID,
                        "old-order-existing",
                        "order-ref-renamed",
                    ),
                )
            )
        assert source_conflict.value.code == CODE_SOURCE_ALREADY_MAPPED
        assert store.registration_count() == 1
        assert store.resolve(SOURCE_ACCOUNT_CONFIG, "new-config").pending_reconciliation
        assert store.history(SOURCE_BARE_ORDER_ID, "old-order-existing") == (original,)

        with pytest.raises(MigrationMapError) as batch_target_conflict:
            store.register_batch(
                (
                    IdentityRegistration(
                        SOURCE_BARE_ORDER_ID, "new-order-a", "shared-order-ref"
                    ),
                    IdentityRegistration(
                        SOURCE_BARE_ORDER_ID, "new-order-b", "shared-order-ref"
                    ),
                )
            )
        assert batch_target_conflict.value.code == CODE_TARGET_ALREADY_BOUND
        assert store.resolve(SOURCE_BARE_ORDER_ID, "new-order-a").pending_reconciliation
        assert store.resolve(SOURCE_BARE_ORDER_ID, "new-order-b").pending_reconciliation
        assert store.registration_count() == 1

        with pytest.raises(MigrationMapError) as existing_target_conflict:
            store.register_batch(
                (
                    IdentityRegistration(
                        SOURCE_IN_PROCESS_RUN,
                        "new-run",
                        "run-ref-new",
                    ),
                    IdentityRegistration(
                        SOURCE_BARE_ORDER_ID,
                        "new-order-c",
                        "order-ref-existing",
                    ),
                )
            )
        assert existing_target_conflict.value.code == CODE_TARGET_ALREADY_BOUND
        assert store.resolve(SOURCE_IN_PROCESS_RUN, "new-run").pending_reconciliation
        assert store.resolve(SOURCE_BARE_ORDER_ID, "new-order-c").pending_reconciliation
        assert store.registration_count() == 1


def test_batch_replay_preserves_existing_and_first_duplicate_provenance(
    tmp_path: Path,
) -> None:
    with MigrationIdentityStore(_path(tmp_path)) as store:
        existing = store.register(
            SOURCE_ACCOUNT_CONFIG,
            "config-existing",
            "account-key-shared",
            recorded_epoch=_OLD_EPOCH,
            note="first row",
            recorded_at=_RECORDED_AT,
        )
        records = store.register_batch(
            (
                IdentityRegistration(
                    SOURCE_ACCOUNT_CONFIG,
                    "config-existing",
                    "account-key-shared",
                    _NEW_EPOCH,
                    "replay must not rewrite provenance",
                    "2026-10-16T01:00:00Z",
                ),
                IdentityRegistration(
                    SOURCE_ACCOUNT_CONFIG,
                    "config-new",
                    "account-key-shared",
                    _OLD_EPOCH,
                    "first batch record",
                    _RECORDED_AT,
                ),
                IdentityRegistration(
                    SOURCE_ACCOUNT_CONFIG,
                    "config-new",
                    "account-key-shared",
                    _NEW_EPOCH,
                    "duplicate input record",
                    "2026-10-16T02:00:00Z",
                ),
            )
        )

        assert records[0] == existing
        assert records[1] == records[2]
        assert records[1].recorded_epoch == _OLD_EPOCH
        assert records[1].note == "first batch record"
        assert records[1].recorded_at == _RECORDED_AT
        assert store.registration_count() == 2


def test_sqlite_insert_failure_rolls_back_the_entire_batch(tmp_path: Path) -> None:
    path = _path(tmp_path)
    with MigrationIdentityStore(path) as store:
        schema_connection = sqlite3.connect(str(path))
        try:
            schema_connection.execute(
                "CREATE TRIGGER fail_selected_batch_insert "
                "BEFORE INSERT ON migration_identity_map "
                "WHEN NEW.source_value = 'forced-backend-failure' "
                "BEGIN SELECT RAISE(ABORT, 'forced backend failure'); END"
            )
            schema_connection.commit()
        finally:
            schema_connection.close()

        with pytest.raises(MigrationMapError) as exc:
            store.register_batch(
                (
                    IdentityRegistration(
                        SOURCE_ACCOUNT_CONFIG, "config-before-failure", "account-a"
                    ),
                    IdentityRegistration(
                        SOURCE_IN_PROCESS_RUN,
                        "forced-backend-failure",
                        "run-ref-failure",
                    ),
                )
            )
        assert exc.value.code == CODE_MIGRATION_MAP_BACKEND_FAILURE
        assert store.registration_count() == 0
        assert store.resolve(
            SOURCE_ACCOUNT_CONFIG, "config-before-failure"
        ).pending_reconciliation

    with MigrationIdentityStore(path) as reopened:
        assert reopened.registration_count() == 0


def test_batch_rejects_invalid_inputs_before_mutation(tmp_path: Path) -> None:
    with MigrationIdentityStore(_path(tmp_path)) as store:
        assert store.register_batch(()) == ()
        invalid_batches = (
            [],
            ("not-a-registration",),
            (IdentityRegistration("unknown-source", "source", "target"),),
            (IdentityRegistration(SOURCE_BARE_SYMBOL, "source", ""),),
            (
                IdentityRegistration(
                    SOURCE_ACCOUNT_CONFIG,
                    "source",
                    "account",
                    recorded_epoch=1,  # type: ignore[arg-type]
                ),
            ),
        )
        for registrations in invalid_batches:
            with pytest.raises(MigrationMapError) as exc:
                store.register_batch(registrations)  # type: ignore[arg-type]
            assert exc.value.code == CODE_INVALID_MIGRATION_MAP_ARGUMENT
        assert store.registration_count() == 0
