"""MIG03 gates durable strategy claims, including direct group bindings."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import sqlite3
from threading import Event

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_py.service_migration_gate import MigrationGateError, arm_migration_cutover, read_migration_marker
from bt_api_py.service_migration_boundary import capture_sqlite_participant
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant, ServiceCapability, ServiceRegistry
from tests.unit.test_iter65_canonical_strategy_owner import (
    register as register_owner, scope as owner_scope, setup_registry as owner_registry,
)
from tests.unit.test_service_group_registration_lifecycle import _new_registry, _register_group
from tests.unit.test_service_migration_write_barrier import (
    _DESTINATION_EPOCH, _IDENTITY, _PRINCIPAL, _SCOPE, _SOURCE_EPOCH, _deny_marker_stat, _enable_registry, _registry,
)
from tests.unit.test_service_registry import _register


def register(registry, key):
    return _register(registry, principal=_PRINCIPAL, identity=_IDENTITY, scope=_SCOPE,
        key=key, client_session_id=key, instance_id='account-instance', port='service.account_read',
        scope_ref='account-grant-a', operations=('orders.submit',))


def claims(registry):
    return registry._db.execute('SELECT COUNT(*) FROM strategy_write_claims').fetchone()[0]


def gateway_registry(path, clock, epoch):
    identity = WireIdentity(WireRole.STRATEGY_CLIENT, 'principal', 'strategy')
    wire = WireScope('domain', account_ref='account', strategy_ref='strategy', run_id='run')
    ops = frozenset({'direct.reserve'})
    resource = ResourceGrant('gateway', 'service.direct_write', 'scope',
        WireScope('domain', account_ref='account', strategy_ref='strategy'), ops)
    registry = ServiceRegistry(path, service_id='gateway-service',
        capabilities=(ServiceCapability('gateway', 'service.direct_write', ops, ops),),
        grants={'principal': (PrincipalGrant(identity, (resource,)),)},
        policy_version='p', requirements_version='r', lease_seconds=10, clock=lambda: clock[0],
        migration_deployment_epoch=epoch)
    return registry, identity, wire


def configure_gateway(registry):
    registry.configure_gateway_lease_drain(instance_id='gateway', account_ref='account',
        strategies=frozenset({'strategy'}), expiry_seconds=30)


def gateway_register(instance, identity, wire, key):
    return _register(instance, principal='principal', identity=identity, scope=wire, key=key,
        client_session_id=key, instance_id='gateway', port='service.direct_write',
        scope_ref='scope', operations=('direct.reserve',))


def test_committed_migration_blocks_new_claim_until_enabled_then_seals(tmp_path):
    path = tmp_path / 'registry.sqlite'
    arm_migration_cutover(path, source_epoch=_SOURCE_EPOCH, new_epoch=_DESTINATION_EPOCH)
    registry = _registry(path, deployment_epoch=_DESTINATION_EPOCH)
    try:
        denied = register(registry, 'denied')
        assert denied['code'] == 'MIGRATION_WRITE_DISABLED' and not denied['ok']
        assert claims(registry) == 0
        assert registry._db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0] == 0
        read_registration = _register(registry, principal=_PRINCIPAL, identity=_IDENTITY, scope=_SCOPE,
            key='read', client_session_id='read', instance_id='account-instance', port='service.account_read',
            scope_ref='account-grant-a', operations=('account.read',))
        assert read_registration['ok']
        assert registry._db.execute('SELECT COUNT(*) FROM registration_keys').fetchone()[0] == 0
        assert read_migration_marker(path).status == 'COMMITTED'
        assert read_migration_marker(path).status == 'COMMITTED'
        _enable_registry(registry, path)
        assert register(registry, 'after-enable')['ok']
        marker = read_migration_marker(path)
        assert marker.sealed and marker.first_write_fact_kind == 'ownership'
        assert claims(registry) == 1
    finally:
        registry.close()


def test_committed_migration_blocks_existing_run_reconnection(tmp_path):
    path = tmp_path / 'registry.sqlite'
    registry = _registry(path, deployment_epoch=None)
    assert register(registry, 'initial')['ok']
    registry.close()
    arm_migration_cutover(path, source_epoch=_SOURCE_EPOCH, new_epoch=_DESTINATION_EPOCH)
    registry = _registry(path, deployment_epoch=_DESTINATION_EPOCH)
    try:
        assert not registry.migration_writes_enabled
        before_keys = tuple(registry._db.execute('SELECT * FROM registration_keys').fetchall())
        before_claim = tuple(registry._db.execute('SELECT * FROM strategy_write_claims').fetchall())
        assert register(registry, 'reconnect')['code'] == 'MIGRATION_WRITE_DISABLED'
        assert claims(registry) == 1
        assert tuple(registry._db.execute('SELECT * FROM registration_keys').fetchall()) == before_keys
        assert tuple(registry._db.execute('SELECT * FROM strategy_write_claims').fetchall()) == before_claim
        assert registry._db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0] == 0
        assert read_migration_marker(path).status == 'COMMITTED'
        _enable_registry(registry, path)
        assert register(registry, 'reconnect')['ok']
        assert read_migration_marker(path).first_write_fact_kind == 'ownership'
    finally:
        registry.close()


def test_concurrent_registration_seals_before_publishing_new_claim(tmp_path, monkeypatch):
    path = tmp_path / 'registry.sqlite'
    arm_migration_cutover(path, source_epoch=_SOURCE_EPOCH, new_epoch=_DESTINATION_EPOCH)
    registry = _registry(path, deployment_epoch=_DESTINATION_EPOCH)
    marked, release, competitor_done = Event(), Event(), Event()
    try:
        _enable_registry(registry, path)
        original = registry._record_migration_write_attempt
        def paused_gate(*, fact_kind):
            result = original(fact_kind=fact_kind)
            marked.set()
            assert release.wait(timeout=5)
            return result
        monkeypatch.setattr(registry, '_record_migration_write_attempt', paused_gate)
        def competitor():
            try:
                return register(registry, 'second')
            finally:
                competitor_done.set()
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(register, registry, 'first')
            assert marked.wait(timeout=5)
            marker = read_migration_marker(path)
            assert marker.sealed and marker.first_write_fact_kind == 'ownership'
            second = pool.submit(competitor)
            assert not competitor_done.wait(timeout=0.2)
            release.set()
            assert first.result(timeout=5)['ok']
            assert second.result(timeout=5)['code'] == 'DUPLICATE_ACTIVE_RUN'
        assert claims(registry) == 1
    finally:
        release.set()
        registry.close()


def test_direct_group_binding_checks_closed_migration_gate(tmp_path):
    path = tmp_path / 'registry.sqlite'
    registry, identity, group_scope, _ = _new_registry(path)
    try:
        registration = _register_group(registry, identity, group_scope)
        registry._migration_writes_enabled = False  # Isolate the group binding's own gate.
        with pytest.raises(MigrationGateError, match='MIGRATION_WRITE_DISABLED'):
            registry.bind_trusted_group_run('live:venue-a', 'strategy-a', 'account-a',
                registration_id=registration['registration_id'])
        assert claims(registry) == 0
        registry._migration_writes_enabled = True
        assert registry.bind_trusted_group_run('live:venue-a', 'strategy-a', 'account-a',
            registration_id=registration['registration_id']) == 'run-a'
    finally:
        registry.close()


def test_unreadable_ownership_marker_does_not_publish_claim_or_session(tmp_path, monkeypatch):
    registry = _registry(tmp_path / 'registry.sqlite', deployment_epoch=None)
    def unavailable(*, fact_kind):
        raise OSError('synthetic marker failure')
    monkeypatch.setattr(registry, '_record_migration_write_attempt', unavailable)
    try:
        result = register(registry, 'unavailable')
        assert result['code'] == 'UNAVAILABLE' and not result['ok']
        assert claims(registry) == 0
        assert registry._db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0] == 0
    finally:
        registry.close()


def test_active_identical_registration_is_read_only(tmp_path, monkeypatch):
    registry = _registry(tmp_path / 'registry.sqlite', deployment_epoch=None)
    try:
        initial = register(registry, 'same')
        assert initial['ok']
        before_keys = tuple(registry._db.execute('SELECT * FROM registration_keys').fetchall())
        before_claim = tuple(registry._db.execute('SELECT * FROM strategy_write_claims').fetchall())
        before_sessions = tuple(registry._db.execute('SELECT * FROM sessions').fetchall())
        def unavailable(*, fact_kind):
            raise OSError('synthetic marker failure')
        monkeypatch.setattr(registry, '_record_migration_write_attempt', unavailable)
        repeated = register(registry, 'same')
        assert repeated['ok'] and repeated['result']['registration_id'] == initial['result']['registration_id']
        assert tuple(registry._db.execute('SELECT * FROM registration_keys').fetchall()) == before_keys
        assert tuple(registry._db.execute('SELECT * FROM strategy_write_claims').fetchall()) == before_claim
        assert tuple(registry._db.execute('SELECT * FROM sessions').fetchall()) == before_sessions
    finally:
        registry.close()


def test_read_only_registration_is_epoch_local_and_does_not_seal_enabled_marker(tmp_path):
    path = tmp_path / 'registry.sqlite'
    arm_migration_cutover(path, source_epoch=_SOURCE_EPOCH, new_epoch=_DESTINATION_EPOCH)
    registry = _registry(path, deployment_epoch=_DESTINATION_EPOCH)
    def read(key, session):
        return _register(registry, principal=_PRINCIPAL, identity=_IDENTITY, scope=_SCOPE,
            key=key, client_session_id=session, instance_id='account-instance', port='service.account_read',
            scope_ref='account-grant-a', operations=('account.read',))
    try:
        before = capture_sqlite_participant('registry', path, configuration_sha256='a' * 64).evidence
        first = read('read-key', 'read-session')
        assert first['ok'] and read('read-key', 'read-session')['result']['registration_id'] == first['result']['registration_id']
        assert read('read-key', 'other-session')['code'] == 'CONFLICT'
        assert registry._db.execute('SELECT COUNT(*) FROM registration_keys').fetchone()[0] == 0
        assert read_migration_marker(path).status == 'COMMITTED'
        after_read = capture_sqlite_participant('registry', path, configuration_sha256='a' * 64).evidence
        assert before.identity_sha256 == after_read.identity_sha256
        _enable_registry(registry, path)
        assert read('other-key', 'other-session')['ok']
        assert read_migration_marker(path).status == 'ENABLED'
        assert registry._db.execute('SELECT COUNT(*) FROM registration_keys').fetchone()[0] == 0
    finally:
        registry.close()


def test_conflicting_run_does_not_seal_enabled_migration(tmp_path):
    path = tmp_path / 'registry.sqlite'
    registry = _registry(path, deployment_epoch=None)
    assert register(registry, 'initial')['ok']
    registry.close()
    arm_migration_cutover(path, source_epoch=_SOURCE_EPOCH, new_epoch=_DESTINATION_EPOCH)
    registry = _registry(path, deployment_epoch=_DESTINATION_EPOCH)
    try:
        _enable_registry(registry, path)
        before_keys = tuple(registry._db.execute('SELECT * FROM registration_keys').fetchall())
        before_claims = tuple(registry._db.execute('SELECT * FROM strategy_write_claims').fetchall())
        result = _register(registry, principal=_PRINCIPAL, identity=_IDENTITY,
            scope=replace(_SCOPE, run_id='different-run'), key='conflicting', client_session_id='conflicting',
            instance_id='account-instance', port='service.account_read', scope_ref='account-grant-a',
            operations=('orders.submit',))
        assert result['code'] == 'DUPLICATE_ACTIVE_RUN'
        assert tuple(registry._db.execute('SELECT * FROM registration_keys').fetchall()) == before_keys
        assert tuple(registry._db.execute('SELECT * FROM strategy_write_claims').fetchall()) == before_claims
        assert registry._db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0] == 0
        assert read_migration_marker(path).status == 'ENABLED'
    finally:
        registry.close()


@pytest.mark.parametrize('operations', [
    ('account.read', 'orders.submit'),
    ('account.read', 'account.ack'),
    ('account.ack',),
])
def test_mixed_or_cursor_registrations_remain_durable_and_gated(tmp_path, operations):
    path = tmp_path / 'registry.sqlite'
    arm_migration_cutover(path, source_epoch=_SOURCE_EPOCH, new_epoch=_DESTINATION_EPOCH)
    registry = _registry(path, deployment_epoch=_DESTINATION_EPOCH)
    try:
        result = _register(registry, principal=_PRINCIPAL, identity=_IDENTITY, scope=_SCOPE,
            key='mixed', client_session_id='mixed', instance_id='account-instance', port='service.account_read',
            scope_ref='account-grant-a', operations=operations)
        assert result['code'] == 'MIGRATION_WRITE_DISABLED'
        assert registry._db.execute('SELECT COUNT(*) FROM registration_keys').fetchone()[0] == 0
        assert registry._db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0] == 0
        assert read_migration_marker(path).status == 'COMMITTED'
    finally:
        registry.close()


@pytest.mark.parametrize(('port', 'operation'), [
    ('service.market_source', 'market.snapshot'),
    ('service.monitor_query', 'monitor.query'),
    ('service.order_query', 'orders.query'),
])
def test_audited_other_component_reads_do_not_seal(tmp_path, port, operation):
    path = tmp_path / 'registry.sqlite'
    arm_migration_cutover(path, source_epoch=_SOURCE_EPOCH, new_epoch=_DESTINATION_EPOCH)
    resource = ResourceGrant('instance', port, 'scope', replace(_SCOPE, run_id=None), frozenset({operation}))
    registry = ServiceRegistry(path, service_id='service',
        capabilities=(ServiceCapability('instance', port, frozenset({operation})),),
        grants={_PRINCIPAL: (PrincipalGrant(_IDENTITY, (resource,)),)},
        policy_version='p', requirements_version='r', migration_deployment_epoch=_DESTINATION_EPOCH)
    try:
        result = _register(registry, principal=_PRINCIPAL, identity=_IDENTITY, scope=_SCOPE,
            key='read', client_session_id='read', instance_id='instance', port=port,
            scope_ref='scope', operations=(operation,))
        assert result['ok']
        assert registry._db.execute('SELECT COUNT(*) FROM registration_keys').fetchone()[0] == 0
        assert read_migration_marker(path).status == 'COMMITTED'
    finally:
        registry.close()


def test_unlisted_read_named_operation_is_not_assumed_safe(tmp_path):
    path = tmp_path / 'registry.sqlite'
    arm_migration_cutover(path, source_epoch=_SOURCE_EPOCH, new_epoch=_DESTINATION_EPOCH)
    operation = 'orders.unverified_query'
    resource = ResourceGrant('instance', 'service.order_query', 'scope', replace(_SCOPE, run_id=None),
                             frozenset({operation}))
    registry = ServiceRegistry(path, service_id='service',
        capabilities=(ServiceCapability('instance', 'service.order_query', frozenset({operation})),),
        grants={_PRINCIPAL: (PrincipalGrant(_IDENTITY, (resource,)),)},
        policy_version='p', requirements_version='r', migration_deployment_epoch=_DESTINATION_EPOCH)
    try:
        result = _register(registry, principal=_PRINCIPAL, identity=_IDENTITY, scope=_SCOPE,
            key='unknown', client_session_id='unknown', instance_id='instance', port='service.order_query',
            scope_ref='scope', operations=(operation,))
        assert result['code'] == 'MIGRATION_WRITE_DISABLED'
        assert registry._db.execute('SELECT COUNT(*) FROM registration_keys').fetchone()[0] == 0
    finally:
        registry.close()


def test_legacy_durable_key_is_checked_per_principal_for_read_only_registration(tmp_path):
    path = tmp_path / 'registry.sqlite'
    first = WireIdentity(WireRole.STRATEGY_CLIENT, 'first', 'actor-first')
    second = WireIdentity(WireRole.STRATEGY_CLIENT, 'second', 'actor-second')
    ops = frozenset({'account.read', 'orders.submit'})
    resource = ResourceGrant('instance', 'service.account_read', 'scope', replace(_SCOPE, run_id=None), ops)
    def create(epoch):
        return ServiceRegistry(path, service_id='service',
            capabilities=(ServiceCapability('instance', 'service.account_read', ops, frozenset({'orders.submit'})),),
            grants={identity.principal_ref: (PrincipalGrant(identity, (resource,)),) for identity in (first, second)},
            policy_version='p', requirements_version='r', migration_deployment_epoch=epoch)
    def reg(registry, identity, operations):
        return _register(registry, principal=identity.principal_ref, identity=identity, scope=_SCOPE,
            key='shared', client_session_id='client', instance_id='instance', port='service.account_read',
            scope_ref='scope', operations=operations)
    registry = create(None)
    assert reg(registry, first, ('orders.submit',))['ok']
    legacy_read = _register(registry, principal=first.principal_ref, identity=first, scope=_SCOPE,
        key='legacy-read', client_session_id='legacy-client', instance_id='instance', port='service.account_read',
        scope_ref='scope', operations=('account.read',))
    assert legacy_read['ok']
    # Fixture an older release that persisted keys even for read-only sessions.
    registry._db.execute('INSERT INTO registration_keys SELECT principal,registration_key,request_digest,'
        'registration_id FROM sessions WHERE principal=? AND registration_key=?', ('first', 'legacy-read'))
    previous_legacy = tuple(registry._db.execute("SELECT * FROM registration_keys WHERE registration_key='legacy-read'").fetchone())
    registry.close()
    arm_migration_cutover(path, source_epoch=_SOURCE_EPOCH, new_epoch=_DESTINATION_EPOCH)
    registry = create(_DESTINATION_EPOCH)
    try:
        assert reg(registry, first, ('account.read',))['code'] == 'CONFLICT'
        assert reg(registry, second, ('account.read',))['ok']
        def legacy_read_again(client):
            return _register(registry, principal=first.principal_ref, identity=first, scope=_SCOPE,
                key='legacy-read', client_session_id=client, instance_id='instance', port='service.account_read',
                scope_ref='scope', operations=('account.read',))
        assert legacy_read_again('legacy-client')['ok']
        assert legacy_read_again('different-client')['code'] == 'CONFLICT'
        second_same_key = _register(registry, principal=second.principal_ref, identity=second, scope=_SCOPE,
            key='legacy-read', client_session_id='second-client', instance_id='instance', port='service.account_read',
            scope_ref='scope', operations=('account.read',))
        assert second_same_key['ok']
        assert tuple(registry._db.execute("SELECT * FROM registration_keys WHERE registration_key='legacy-read'").fetchone()) == previous_legacy
        assert registry._db.execute('SELECT COUNT(*) FROM registration_keys').fetchone()[0] == 2
        assert read_migration_marker(path).status == 'COMMITTED'
    finally:
        registry.close()


def test_read_only_key_no_longer_survives_service_epoch(tmp_path):
    path = tmp_path / 'registry.sqlite'
    registry = _registry(path, deployment_epoch=None)
    def reg(client):
        return _register(registry, principal=_PRINCIPAL, identity=_IDENTITY, scope=_SCOPE,
            key='read-key', client_session_id=client, instance_id='account-instance', port='service.account_read',
            scope_ref='account-grant-a', operations=('account.read',))
    assert reg('first')['ok']
    registry.close()
    registry = _registry(path, deployment_epoch=None)
    try:
        assert reg('different-after-restart')['ok']
        assert registry._db.execute('SELECT COUNT(*) FROM registration_keys').fetchone()[0] == 0
    finally:
        registry.close()


def test_expiration_cleanup_continues_when_new_ownership_is_blocked(tmp_path):
    path = tmp_path / 'registry.sqlite'
    scopes = (owner_scope('domain-a'), owner_scope('domain-a', account='other-account'))
    registry, identities = owner_registry(path, scopes)
    assert register_owner(registry, identities[0], scopes[0], 'old')['ok']
    old_session = dict(registry._db.execute('SELECT * FROM sessions').fetchone())
    registry.close()
    arm_migration_cutover(path, source_epoch=_SOURCE_EPOCH, new_epoch=_DESTINATION_EPOCH)
    registry, identities = owner_registry(path, scopes, migration_deployment_epoch=_DESTINATION_EPOCH)
    try:
        # Represent a stale current-epoch row without bypassing the production
        # registration gate; ordinary startup normally clears old sessions.
        old_session['service_epoch'] = registry.service_epoch
        columns = tuple(old_session)
        registry._db.execute('INSERT INTO sessions (' + ','.join(columns) + ') VALUES ('
            + ','.join('?' for _ in columns) + ')', tuple(old_session.values()))
        registry._clock = lambda: 111.0
        denied = register_owner(registry, identities[1], scopes[1], 'new')
        assert denied['code'] == 'MIGRATION_WRITE_DISABLED'
        assert registry._db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0] == 0
        assert claims(registry) == 1
        assert read_migration_marker(path).status == 'COMMITTED'
    finally:
        registry.close()


def test_unreadable_marker_blocks_existing_run_reconnection(tmp_path, monkeypatch):
    path = tmp_path / 'registry.sqlite'
    registry = _registry(path, deployment_epoch=None)
    assert register(registry, 'initial')['ok']
    registry.close()
    arm_migration_cutover(path, source_epoch=_SOURCE_EPOCH, new_epoch=_DESTINATION_EPOCH)
    registry = _registry(path, deployment_epoch=_DESTINATION_EPOCH)
    try:
        _enable_registry(registry, path)
        before_keys = tuple(registry._db.execute('SELECT * FROM registration_keys').fetchall())
        before_claim = tuple(registry._db.execute('SELECT * FROM strategy_write_claims').fetchall())
        with monkeypatch.context() as patch:
            _deny_marker_stat(patch, path)
            assert register(registry, 'reconnect')['code'] == 'UNAVAILABLE'
        assert tuple(registry._db.execute('SELECT * FROM registration_keys').fetchall()) == before_keys
        assert tuple(registry._db.execute('SELECT * FROM strategy_write_claims').fetchall()) == before_claim
        assert registry._db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0] == 0
        assert read_migration_marker(path).status == 'ENABLED'
    finally:
        registry.close()


@pytest.mark.parametrize('now', [120.0, 150.0])
def test_waiting_gateway_expiration_is_not_changed_before_migration_gate(tmp_path, monkeypatch, now):
    path = tmp_path / 'registry.sqlite'
    clock = [100.0]
    registry, identity, wire = gateway_registry(path, clock, None)
    configure_gateway(registry)
    assert gateway_register(registry, identity, wire, 'old')['ok']
    clock[0] = 111.0
    assert registry.pending_gateway_lease_expirations(instance_id='gateway', account_ref='account') == ()
    assert registry._db.execute('SELECT state FROM gateway_lease_expirations').fetchone()[0] == 'WAITING'
    registry.close()
    arm_migration_cutover(path, source_epoch=_SOURCE_EPOCH, new_epoch=_DESTINATION_EPOCH)
    clock[0] = now
    registry, identity, wire = gateway_registry(path, clock, _DESTINATION_EPOCH)
    configure_gateway(registry)
    try:
        assert gateway_register(registry, identity, wire, 'resume')['code'] == 'MIGRATION_WRITE_DISABLED'
        assert registry._db.execute('SELECT state FROM gateway_lease_expirations').fetchone()[0] == 'WAITING'
        assert read_migration_marker(path).status == 'COMMITTED'
        _enable_registry(registry, path)
        with monkeypatch.context() as patch:
            _deny_marker_stat(patch, path)
            assert gateway_register(registry, identity, wire, 'resume')['code'] == 'UNAVAILABLE'
        assert registry._db.execute('SELECT state FROM gateway_lease_expirations').fetchone()[0] == 'WAITING'
        assert read_migration_marker(path).status == 'ENABLED'
        assert gateway_register(registry, identity, wire, 'resume')['ok']
        state = registry._db.execute('SELECT state FROM gateway_lease_expirations').fetchone()[0]
        assert state == ('RETURNED' if now < 140 else 'PENDING')
        assert read_migration_marker(path).first_write_fact_kind == 'ownership'
    finally:
        registry.close()


@pytest.mark.parametrize('legacy', [False, True])
def test_gateway_lease_schema_changes_require_migration_gate(tmp_path, legacy):
    path = tmp_path / 'registry.sqlite'
    clock = [100.0]
    registry, _, _ = gateway_registry(path, clock, None)
    if legacy:
        registry._db.execute('CREATE TABLE gateway_lease_expirations ('
            'event_id TEXT PRIMARY KEY,instance_id TEXT NOT NULL,account_ref TEXT NOT NULL,'
            'strategy_ref TEXT NOT NULL,run_id TEXT NOT NULL,principal TEXT NOT NULL,'
            'registration_id TEXT NOT NULL,service_epoch TEXT NOT NULL,expires_at REAL NOT NULL,'
            'observed_at REAL NOT NULL,state TEXT NOT NULL)')
    registry.close()
    arm_migration_cutover(path, source_epoch=_SOURCE_EPOCH, new_epoch=_DESTINATION_EPOCH)
    registry, _, _ = gateway_registry(path, clock, _DESTINATION_EPOCH)
    try:
        with pytest.raises(MigrationGateError, match='MIGRATION_WRITE_DISABLED'):
            configure_gateway(registry)
        schema = registry._db.execute("SELECT sql FROM sqlite_master WHERE type='table' "
            "AND name='gateway_lease_expirations'").fetchone()
        assert (schema is not None) is legacy
        if legacy:
            assert 'apply_at' not in schema[0]
        assert read_migration_marker(path).status == 'COMMITTED'
        _enable_registry(registry, path)
        configure_gateway(registry)
        assert 'apply_at' in registry._db.execute("SELECT sql FROM sqlite_master WHERE type='table' "
            "AND name='gateway_lease_expirations'").fetchone()[0]
        assert read_migration_marker(path).first_write_fact_kind == 'ownership'
    finally:
        registry.close()


def test_pending_acknowledgement_is_gated_and_concurrent_retry_is_idempotent(tmp_path, monkeypatch):
    path = tmp_path / 'registry.sqlite'
    clock = [100.0]
    registry, identity, wire = gateway_registry(path, clock, None)
    configure_gateway(registry)
    assert gateway_register(registry, identity, wire, 'old')['ok']
    clock[0] = 141.0
    event, = registry.pending_gateway_lease_expirations(instance_id='gateway', account_ref='account')
    assert event['state'] == 'PENDING'
    registry.close()
    arm_migration_cutover(path, source_epoch=_SOURCE_EPOCH, new_epoch=_DESTINATION_EPOCH)
    registry, _, _ = gateway_registry(path, clock, _DESTINATION_EPOCH)
    configure_gateway(registry)
    def ack():
        return registry.acknowledge_gateway_lease_expiration(instance_id='gateway',
            account_ref='account', event_id=event['event_id'])
    marked, release, competitor_done = Event(), Event(), Event()
    try:
        with pytest.raises(MigrationGateError, match='MIGRATION_WRITE_DISABLED'):
            ack()
        assert registry._db.execute('SELECT state FROM gateway_lease_expirations').fetchone()[0] == 'PENDING'
        assert read_migration_marker(path).status == 'COMMITTED'
        _enable_registry(registry, path)
        with monkeypatch.context() as patch:
            _deny_marker_stat(patch, path)
            with pytest.raises((MigrationGateError, OSError)):
                ack()
        assert registry._db.execute('SELECT state FROM gateway_lease_expirations').fetchone()[0] == 'PENDING'
        assert read_migration_marker(path).status == 'ENABLED'
        original = registry._record_migration_write_attempt
        calls = []
        def paused_gate(*, fact_kind):
            calls.append(fact_kind)
            result = original(fact_kind=fact_kind)
            marked.set()
            assert release.wait(timeout=5)
            return result
        monkeypatch.setattr(registry, '_record_migration_write_attempt', paused_gate)
        def competitor():
            try:
                return ack()
            finally:
                competitor_done.set()
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(ack)
            assert marked.wait(timeout=5)
            assert read_migration_marker(path).first_write_fact_kind == 'ownership'
            with sqlite3.connect(path) as observer:
                assert observer.execute('SELECT state FROM gateway_lease_expirations').fetchone()[0] == 'PENDING'
            second = pool.submit(competitor)
            assert not competitor_done.wait(timeout=0.2)
            release.set()
            assert first.result(timeout=5) is None and second.result(timeout=5) is None
        assert calls == ['ownership']
        assert registry._db.execute('SELECT state FROM gateway_lease_expirations').fetchone()[0] == 'APPLIED'
    finally:
        release.set()
        registry.close()
