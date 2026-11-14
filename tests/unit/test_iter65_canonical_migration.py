"""MIG03 conservation for configured strategy alias ownership."""
import sqlite3
from dataclasses import replace

import pytest
from bt_api_execution.contracts import ExecutionScope
from bt_api_py.service_migration_boundary import (
    CODE_BOUNDARY_NOT_CONSERVED, MigrationBoundaryError,
    assert_participant_states_conserved, capture_sqlite_participant,
)
from bt_api_py.service_migration_gate import MigrationGateError, arm_migration_cutover, read_migration_marker
from tests.unit.test_iter65_canonical_strategy_owner import register, scope, setup_registry
from tests.unit.test_service_migration_boundary import _participants
from tests.unit.test_service_migration_write_barrier import (
    _DESTINATION_EPOCH, _SOURCE_EPOCH, _enable_registry, _registry,
)


def snapshot(path):
    return capture_sqlite_participant('registry', path, configuration_sha256='a' * 64).evidence


def participants(registry_evidence):
    return tuple(registry_evidence if item.participant_id == 'registry' else item for item in _participants())


@pytest.mark.parametrize('change', ['quarantine', 'canonical', 'delete'])
def test_migration_conservation_detects_canonical_binding_changes(tmp_path, change):
    path = tmp_path / 'registry.sqlite'
    wire = scope('domain-a')
    target = ExecutionScope('FAKE', 'test', 'account', 'strategy')
    registry, _ = setup_registry(path, (wire,))
    try:
        registry.configure_strategy_canonical_targets(targets={wire: target})
        before = snapshot(path)
        if change == 'delete':
            registry._db.execute('DELETE FROM strategy_canonical_bindings')
        elif change == 'quarantine':
            registry._db.execute('UPDATE strategy_canonical_bindings SET quarantined=1')
        else:
            registry._db.execute("UPDATE strategy_canonical_bindings SET canonical_strategy_id='changed'")
        after = snapshot(path)
        assert before.schema_sha256 == after.schema_sha256
        assert before.identity_sha256 != after.identity_sha256
        with pytest.raises(MigrationBoundaryError) as error:
            assert_participant_states_conserved(participants(before), participants(after))
        assert error.value.code == CODE_BOUNDARY_NOT_CONSERVED
    finally:
        registry.close()


def test_old_schema_snapshot_is_preserved_but_upgrade_is_not_silently_conserved(tmp_path):
    path = tmp_path / 'registry.sqlite'
    wire = scope('domain-a')
    target = ExecutionScope('FAKE', 'test', 'account', 'strategy')
    registry, _ = setup_registry(path, (wire,))
    registry._db.execute('INSERT INTO strategy_write_claims '
        '(execution_domain_ref,strategy_ref,account_ref,run_id,created_at) VALUES(?,?,?,?,?)',
        (wire.execution_domain_ref, wire.strategy_ref, wire.account_ref, wire.run_id, 100.0))
    registry.close()
    with sqlite3.connect(path) as connection:
        connection.execute('DROP TABLE strategy_canonical_bindings')
    before = snapshot(path)
    registry, _ = setup_registry(path, (wire,))
    try:
        after = snapshot(path)
        assert before.schema_sha256 != after.schema_sha256
        with pytest.raises(MigrationBoundaryError) as error:
            assert_participant_states_conserved(participants(before), participants(after))
        assert error.value.code == CODE_BOUNDARY_NOT_CONSERVED
        registry.configure_strategy_canonical_targets(targets={wire: target})
        assert registry._db.execute('SELECT quarantined FROM strategy_canonical_bindings').fetchone()[0] == 1
        assert registry.strategy_write_run_id('domain-a', 'wire-strategy', 'wire-account') == 'run-a'
    finally:
        registry.close()


def test_binding_insertion_obeys_migration_write_gate(tmp_path):
    path = tmp_path / 'registry.sqlite'
    arm_migration_cutover(path, source_epoch=_SOURCE_EPOCH, new_epoch=_DESTINATION_EPOCH)
    registry = _registry(path, deployment_epoch=_DESTINATION_EPOCH)
    wire = scope('domain-a')
    target = ExecutionScope('FAKE', 'test', 'account', 'strategy')
    try:
        with pytest.raises(MigrationGateError):
            registry.configure_strategy_canonical_targets(targets={wire: target})
        assert registry._db.execute('SELECT COUNT(*) FROM strategy_canonical_bindings').fetchone()[0] == 0
        _enable_registry(registry, path)
        registry.configure_strategy_canonical_targets(targets={wire: target})
        marker = read_migration_marker(path)
        assert marker.sealed and marker.first_write_fact_kind == 'ownership'
        registry.configure_strategy_canonical_targets(targets={wire: replace(target)})
        assert registry._db.execute('SELECT COUNT(*) FROM strategy_canonical_bindings').fetchone()[0] == 1
    finally:
        registry.close()


def test_reinstalling_identical_binding_does_not_seal_committed_migration(tmp_path):
    path = tmp_path / 'registry.sqlite'
    wire = scope('domain-a')
    target = ExecutionScope('FAKE', 'test', 'account', 'strategy')
    registry = _registry(path, deployment_epoch=None)
    registry.configure_strategy_canonical_targets(targets={wire: target})
    registry.close()
    arm_migration_cutover(path, source_epoch=_SOURCE_EPOCH, new_epoch=_DESTINATION_EPOCH)
    registry = _registry(path, deployment_epoch=_DESTINATION_EPOCH)
    try:
        assert not registry.migration_writes_enabled
        registry.configure_strategy_canonical_targets(targets={wire: target})
        assert read_migration_marker(path).status == 'COMMITTED'
    finally:
        registry.close()


def test_snapshot_pins_registration_digest_and_gateway_lease_state(tmp_path):
    path = tmp_path / 'registry.sqlite'
    wire = scope('domain-a')
    registry, identities = setup_registry(path, (wire,))
    try:
        registry._db.execute('CREATE TABLE gateway_lease_expirations ('
            'event_id TEXT PRIMARY KEY,instance_id TEXT NOT NULL,account_ref TEXT NOT NULL,'
            'strategy_ref TEXT NOT NULL,run_id TEXT NOT NULL,principal TEXT NOT NULL,'
            'registration_id TEXT NOT NULL,service_epoch TEXT NOT NULL,expires_at REAL NOT NULL,'
            'observed_at REAL NOT NULL,state TEXT NOT NULL,apply_at REAL)')
        assert register(registry, identities[0], wire, 'initial')['ok']
        registry._db.execute("INSERT INTO gateway_lease_expirations "
            "(event_id,instance_id,account_ref,strategy_ref,run_id,principal,registration_id,"
            "service_epoch,expires_at,observed_at,state,apply_at) "
            "VALUES ('event','gateway','account','strategy','run','principal','reg','epoch',1,2,'WAITING',3)")
        before = snapshot(path)
        registry._db.execute("UPDATE registration_keys SET registration_id='new-epoch-id'")
        ephemeral = snapshot(path)
        assert ephemeral.identity_sha256 == before.identity_sha256
        registry._db.execute("UPDATE registration_keys SET request_digest='changed'")
        digest_changed = snapshot(path)
        assert digest_changed.identity_sha256 != before.identity_sha256
        registry._db.execute("UPDATE gateway_lease_expirations SET state='RETURNED'")
        lease_changed = snapshot(path)
        assert lease_changed.identity_sha256 != digest_changed.identity_sha256
    finally:
        registry.close()
