"""Focused tests for the MIG03 increment-C persistent identity mapping store.

Covers the iteration-plan row at L854 ("建立有冲突检查的持久映射及历史关联；
无法判定归属保留未分配/待对账，不改名后重新授信或重新下单") against real SQLite
files under a tmpdir:

* positive mapping of all five legacy identity kinds (old strategy_id, bare
  symbol, bare order_id, in-process run, account config) with historical
  association retained across close/reopen under a new epoch;
* contradictory re-registration refused with a typed error and zero mutation
  (no second order partition, no second target minted);
* ambiguous exclusive-target registration refused with a typed error (two
  legacy identities can never share one strategy/order/run ref, so no
  duplicate credit or order target exists);
* conflict refusal and the partition invariant stay intact under incr-B's
  sealed cutover epoch facts (real ``service_migration_gate`` marker enabled
  on the same state path);
* unmapped identities resolve to UNALLOCATED/待对账 and stay unallocated
  across reopen: never auto re-credit, never re-order, never
  rename-then-recredit;
* idempotent replay of the same registration keeps exactly one row with the
  first recording's provenance;
* an unreadable store file fails closed with a typed error and invalid
  arguments are rejected with the typed argument code.

No monkeypatch of the logic under test, no skip/xfail; the store runs on real
SQLite database files.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from bt_api_py.service_migration_gate import (
    REFUSAL_REVERSE_MIGRATION_REFUSED,
    MigrationGateError,
    arm_migration_cutover,
    enable_migration_cutover,
    enforce_migration_start,
    record_migration_first_write,
)
from bt_api_py.service_migration_map import (
    CODE_INVALID_MIGRATION_MAP_ARGUMENT,
    CODE_MIGRATION_MAP_UNREADABLE,
    CODE_SOURCE_ALREADY_MAPPED,
    CODE_TARGET_ALREADY_BOUND,
    EXCLUSIVE_TARGET_KINDS,
    LEGACY_SOURCE_KINDS,
    RECONCILIATION_QUEUE,
    RESOLUTION_RESOLVED,
    RESOLUTION_UNALLOCATED,
    SOURCE_ACCOUNT_CONFIG,
    SOURCE_BARE_ORDER_ID,
    SOURCE_BARE_SYMBOL,
    SOURCE_IN_PROCESS_RUN,
    SOURCE_LEGACY_STRATEGY_ID,
    TARGET_ACCOUNT_KEY,
    TARGET_BOOKED_INSTRUMENT,
    TARGET_KIND_BY_SOURCE,
    TARGET_ORDER_REF,
    TARGET_RUN_REF,
    TARGET_STRATEGY_REF,
    MigrationIdentityStore,
    MigrationMapError,
)

# Synthetic epoch facts in the shape used by the incr-A/incr-B migration
# evidence: the old deployment epoch and the cutover/new owner epoch.
_OLD_EPOCH = "4b1f0c9d8e7a605142b3c4d5e6f70819"
_NEW_EPOCH = "9c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f"

# The five L854 legacy identity kinds and their fixed new-identity targets.
_CASES = (
    (SOURCE_LEGACY_STRATEGY_ID, "legacy-strategy-alpha", TARGET_STRATEGY_REF, "strategy-ref-alpha"),
    (SOURCE_BARE_SYMBOL, "rb2310", TARGET_BOOKED_INSTRUMENT, "SHFE.rb2410"),
    (SOURCE_BARE_ORDER_ID, "7788990011", TARGET_ORDER_REF, "order-ref-7788990011"),
    (SOURCE_IN_PROCESS_RUN, "run-20261016-0007", TARGET_RUN_REF, "run-ref-0007"),
    (SOURCE_ACCOUNT_CONFIG, "cfg-digest-simnow-a", TARGET_ACCOUNT_KEY, "account-key-simnow-a"),
)


def _store_path(tmp_path: Path) -> Path:
    return tmp_path / "registry.sqlite3"


def test_positive_mapping_of_all_five_legacy_kinds_with_history_across_reopen(tmp_path: Path) -> None:
    path = _store_path(tmp_path)
    records = {}
    with MigrationIdentityStore(path) as store:
        assert {
            SOURCE_LEGACY_STRATEGY_ID,
            SOURCE_BARE_SYMBOL,
            SOURCE_BARE_ORDER_ID,
            SOURCE_IN_PROCESS_RUN,
            SOURCE_ACCOUNT_CONFIG,
        } == LEGACY_SOURCE_KINDS
        for source_kind, source_value, target_kind, target_value in _CASES:
            record = store.register(
                source_kind,
                source_value,
                target_value,
                recorded_epoch=_OLD_EPOCH,
                note="migrated from old deployment",
            )
            assert record.source_kind == source_kind
            assert record.source_value == source_value
            assert record.target_kind == target_kind == TARGET_KIND_BY_SOURCE[source_kind]
            assert record.target_value == target_value
            assert record.recorded_epoch == _OLD_EPOCH
            assert record.recorded_at
            resolution = store.resolve(source_kind, source_value)
            assert resolution.status == RESOLUTION_RESOLVED
            assert resolution.resolved is True
            assert resolution.pending_reconciliation is False
            assert resolution.target_kind == target_kind
            assert resolution.target_value == target_value
            assert resolution.reconciliation_queue is None
            assert store.history(source_kind, source_value) == (record,)
            records[source_value] = record
        assert store.registration_count() == len(_CASES)

    # Reopen (new epoch context): the old identities still resolve to the same
    # new identities and the exact historical records are retained.
    with MigrationIdentityStore(path) as reopened:
        for source_kind, source_value, target_kind, target_value in _CASES:
            resolution = reopened.resolve(source_kind, source_value)
            assert resolution.status == RESOLUTION_RESOLVED
            assert resolution.target_kind == target_kind
            assert resolution.target_value == target_value
            assert resolution.mapping == records[source_value]
        # New-epoch registrations coexist; fan-in kinds may alias many-to-one.
        reopened.register(
            SOURCE_BARE_SYMBOL,
            "rb2310.SHFE",
            "SHFE.rb2410",
            recorded_epoch=_NEW_EPOCH,
        )
        reopened.register(
            SOURCE_ACCOUNT_CONFIG,
            "cfg-digest-simnow-b",
            "account-key-simnow-a",
            recorded_epoch=_NEW_EPOCH,
        )
        bindings = reopened.bindings_for_target(TARGET_BOOKED_INSTRUMENT, "SHFE.rb2410")
        assert {binding.source_value for binding in bindings} == {"rb2310", "rb2310.SHFE"}
        assert reopened.bound_source_count(TARGET_ACCOUNT_KEY, "account-key-simnow-a") == 2
        assert reopened.registration_count() == len(_CASES) + 2


def test_conflicting_source_registration_refused_without_mutation_no_second_partition(
    tmp_path: Path,
) -> None:
    path = _store_path(tmp_path)
    with MigrationIdentityStore(path) as store:
        original = store.register(
            SOURCE_BARE_ORDER_ID, "oid-0001", "order-ref-0001", recorded_epoch=_OLD_EPOCH
        )
        with pytest.raises(MigrationMapError) as exc:
            store.register(
                SOURCE_BARE_ORDER_ID,
                "oid-0001",
                "order-ref-0001-renamed",
                recorded_epoch=_NEW_EPOCH,
            )
        assert exc.value.code == CODE_SOURCE_ALREADY_MAPPED
        resolution = store.resolve(SOURCE_BARE_ORDER_ID, "oid-0001")
        assert resolution.status == RESOLUTION_RESOLVED
        assert resolution.target_value == "order-ref-0001"
        assert resolution.mapping.recorded_epoch == _OLD_EPOCH
        assert store.history(SOURCE_BARE_ORDER_ID, "oid-0001") == (original,)
        # Nothing mutated: still exactly one partition (target) for the order
        # and the renamed ref was never minted.
        assert store.bound_target_count(SOURCE_BARE_ORDER_ID, "oid-0001") == 1
        assert store.bound_source_count(TARGET_ORDER_REF, "order-ref-0001-renamed") == 0
        assert store.bindings_for_target(TARGET_ORDER_REF, "order-ref-0001-renamed") == ()
        assert store.registration_count() == 1


def test_ambiguous_exclusive_target_refused_no_duplicate_target_minted(tmp_path: Path) -> None:
    path = _store_path(tmp_path)
    with MigrationIdentityStore(path) as store:
        assert {
            TARGET_STRATEGY_REF,
            TARGET_ORDER_REF,
            TARGET_RUN_REF,
        } == EXCLUSIVE_TARGET_KINDS
        store.register(SOURCE_LEGACY_STRATEGY_ID, "legacy-strategy-alpha", "strategy-ref-alpha")
        with pytest.raises(MigrationMapError) as exc:
            store.register(
                SOURCE_LEGACY_STRATEGY_ID, "legacy-strategy-beta", "strategy-ref-alpha"
            )
        assert exc.value.code == CODE_TARGET_ALREADY_BOUND
        beta = store.resolve(SOURCE_LEGACY_STRATEGY_ID, "legacy-strategy-beta")
        assert beta.status == RESOLUTION_UNALLOCATED
        assert beta.reconciliation_queue == RECONCILIATION_QUEUE
        assert store.bound_source_count(TARGET_STRATEGY_REF, "strategy-ref-alpha") == 1

        store.register(SOURCE_BARE_ORDER_ID, "oid-0001", "order-ref-0001")
        with pytest.raises(MigrationMapError) as exc:
            store.register(SOURCE_BARE_ORDER_ID, "oid-0002", "order-ref-0001")
        assert exc.value.code == CODE_TARGET_ALREADY_BOUND
        assert store.resolve(SOURCE_BARE_ORDER_ID, "oid-0002").status == RESOLUTION_UNALLOCATED

        store.register(SOURCE_IN_PROCESS_RUN, "run-0007", "run-ref-0007")
        with pytest.raises(MigrationMapError) as exc:
            store.register(SOURCE_IN_PROCESS_RUN, "run-0008", "run-ref-0007")
        assert exc.value.code == CODE_TARGET_ALREADY_BOUND
        assert store.registration_count() == 3

        # Fan-in kinds are not exclusive: two bare spellings may share one
        # booked instrument, and config revisions may share one account key.
        store.register(SOURCE_BARE_SYMBOL, "rb2310", "SHFE.rb2410")
        store.register(SOURCE_BARE_SYMBOL, "rb2310.SHFE", "SHFE.rb2410")
        assert store.bound_source_count(TARGET_BOOKED_INSTRUMENT, "SHFE.rb2410") == 2
        assert store.registration_count() == 5


def test_conflict_refusal_and_partition_invariant_under_sealed_cutover_epoch(
    tmp_path: Path,
) -> None:
    path = _store_path(tmp_path)
    store = MigrationIdentityStore(path)
    try:
        marker = arm_migration_cutover(path, source_epoch=_OLD_EPOCH, new_epoch=_NEW_EPOCH)
        assert marker.sealed is False
        marker = enable_migration_cutover(
            path,
            migration_id=marker.migration_id,
            deployment_epoch=_NEW_EPOCH,
        )
        assert record_migration_first_write(path, fact_kind="ownership") is True

        # The new epoch registers its authoritative mapping...
        store.register(
            SOURCE_BARE_ORDER_ID, "oid-0009", "order-ref-0009", recorded_epoch=_NEW_EPOCH
        )
        # ...but the old epoch can neither rename it nor mint a second partition.
        with pytest.raises(MigrationMapError) as exc:
            store.register(
                SOURCE_BARE_ORDER_ID,
                "oid-0009",
                "order-ref-0009-b",
                recorded_epoch=_OLD_EPOCH,
            )
        assert exc.value.code == CODE_SOURCE_ALREADY_MAPPED
        assert store.resolve(SOURCE_BARE_ORDER_ID, "oid-0009").target_value == "order-ref-0009"
        assert store.bound_target_count(SOURCE_BARE_ORDER_ID, "oid-0009") == 1
        assert store.bound_source_count(TARGET_ORDER_REF, "order-ref-0009-b") == 0
        assert store.registration_count() == 1

        # The sealed incr-B cutover gate independently refuses the old epoch.
        with pytest.raises(MigrationGateError) as gate_exc:
            enforce_migration_start(path, deployment_epoch=_OLD_EPOCH)
        assert gate_exc.value.code == REFUSAL_REVERSE_MIGRATION_REFUSED
        enforce_migration_start(path, deployment_epoch=_NEW_EPOCH)

        # Mapping and refusal survive the store reopening.
        store.close()
        store = MigrationIdentityStore(path)
        assert store.resolve(SOURCE_BARE_ORDER_ID, "oid-0009").status == RESOLUTION_RESOLVED
        with pytest.raises(MigrationMapError) as reopen_exc:
            store.register(
                SOURCE_BARE_ORDER_ID, "oid-0009", "order-ref-0009-b", recorded_epoch=_NEW_EPOCH
            )
        assert reopen_exc.value.code == CODE_SOURCE_ALREADY_MAPPED
        assert store.registration_count() == 1
    finally:
        store.close()


def test_unmapped_identity_stays_unallocated_pending_reconciliation_across_reopen(
    tmp_path: Path,
) -> None:
    path = _store_path(tmp_path)
    unknowns = (
        (SOURCE_LEGACY_STRATEGY_ID, "legacy-strategy-ghost"),
        (SOURCE_BARE_SYMBOL, "zz9999"),
        (SOURCE_BARE_ORDER_ID, "not-an-order"),
        (SOURCE_IN_PROCESS_RUN, "run-ghost"),
        (SOURCE_ACCOUNT_CONFIG, "cfg-ghost"),
    )
    with MigrationIdentityStore(path) as store:
        for source_kind, source_value in unknowns:
            first = store.resolve(source_kind, source_value)
            second = store.resolve(source_kind, source_value)
            assert first == second
            assert first.status == RESOLUTION_UNALLOCATED
            assert first.resolved is False
            assert first.pending_reconciliation is True
            assert first.reconciliation_queue == RECONCILIATION_QUEUE == "待对账"
            assert first.mapping is None
            assert first.target_kind is None
            assert first.target_value is None
            assert store.history(source_kind, source_value) == ()
        # No re-credit and no re-order: resolving never registers anything.
        assert store.registration_count() == 0

    with MigrationIdentityStore(path) as reopened:
        for source_kind, source_value in unknowns:
            resolution = reopened.resolve(source_kind, source_value)
            assert resolution.status == RESOLUTION_UNALLOCATED
            assert resolution.reconciliation_queue == RECONCILIATION_QUEUE
        assert reopened.registration_count() == 0


def test_idempotent_replay_of_same_registration_keeps_single_row(tmp_path: Path) -> None:
    path = _store_path(tmp_path)
    with MigrationIdentityStore(path) as store:
        first = store.register(
            SOURCE_IN_PROCESS_RUN,
            "run-0007",
            "run-ref-0007",
            recorded_epoch=_OLD_EPOCH,
            note="initial",
            recorded_at="2026-10-16T05:00:00Z",
        )
        replay = store.register(
            SOURCE_IN_PROCESS_RUN,
            "run-0007",
            "run-ref-0007",
            recorded_epoch=_OLD_EPOCH,
            note="initial",
            recorded_at="2026-10-16T05:30:00Z",
        )
        assert replay == first
        assert replay.recorded_at == "2026-10-16T05:00:00Z"
        assert store.registration_count() == 1

    with MigrationIdentityStore(path) as reopened:
        replay_after_reopen = reopened.register(
            SOURCE_IN_PROCESS_RUN,
            "run-0007",
            "run-ref-0007",
            recorded_epoch=_NEW_EPOCH,
            note="replayed by the new epoch",
        )
        # Replay is not an update: provenance of the first recording wins.
        assert replay_after_reopen.recorded_at == "2026-10-16T05:00:00Z"
        assert replay_after_reopen.recorded_epoch == _OLD_EPOCH
        assert replay_after_reopen.note == "initial"
        assert reopened.registration_count() == 1


def test_unreadable_store_fails_closed_and_rejects_invalid_arguments(tmp_path: Path) -> None:
    garbage = _store_path(tmp_path)
    payload = b"this is not a sqlite database"
    garbage.write_bytes(payload)
    with pytest.raises(MigrationMapError) as exc:
        MigrationIdentityStore(garbage)
    assert exc.value.code == CODE_MIGRATION_MAP_UNREADABLE
    # The unreadable file was not silently replaced or repaired.
    assert garbage.read_bytes() == payload

    # An unrelated valid SQLite database is not adopted as a mapping store.
    alien = tmp_path / "alien.sqlite3"
    connection = sqlite3.connect(str(alien))
    connection.execute("CREATE TABLE alien (id INTEGER)")
    connection.commit()
    connection.close()
    with pytest.raises(MigrationMapError) as alien_exc:
        MigrationIdentityStore(alien)
    assert alien_exc.value.code == CODE_MIGRATION_MAP_UNREADABLE

    with MigrationIdentityStore(tmp_path / "fresh.sqlite3") as store:
        for bad_kind in ("", "unknown_kind", None, 5):
            with pytest.raises(MigrationMapError) as arg_exc:
                store.register(bad_kind, "value", "target")
            assert arg_exc.value.code == CODE_INVALID_MIGRATION_MAP_ARGUMENT
        with pytest.raises(MigrationMapError) as arg_exc:
            store.resolve(SOURCE_BARE_SYMBOL, "")
        assert arg_exc.value.code == CODE_INVALID_MIGRATION_MAP_ARGUMENT
        with pytest.raises(MigrationMapError) as arg_exc:
            store.bindings_for_target("not_a_target_kind", "value")
        assert arg_exc.value.code == CODE_INVALID_MIGRATION_MAP_ARGUMENT
        assert store.registration_count() == 0
