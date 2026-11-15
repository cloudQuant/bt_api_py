"""Local isolated authority/journal evidence; no socket or provider access."""
import os
import sqlite3
import threading
import time
from contextlib import contextmanager, suppress

import pytest
from bt_api_base.account_protocol import AccountKey
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_gateway.direct_command_journal import (
    DirectCommandJournalError,
    GatewayDirectCommandJournal,
    inspect_unresolved_dispatch,
)
from bt_api_gateway.direct_dispatch import (
    DirectDispatchError,
    GatewayDirectDispatcher,
    _read_only_snapshot,
)
from bt_api_gateway.direct_parent_authority import GatewayParentAuthority
from bt_api_gateway.session_guard import LocalAccountSessionGuard

from bt_api_py.account_coordination import AccountCoordinationBinding, canonical_coordination_path
from bt_api_py.account_owner_handoff import HandoffError
from bt_api_py.account_writer_authority import RemoteAccountOwnerHandoff, WriterAuthorityServer
from bt_api_py.gateway_continuity_witness import (
    GatewayContinuityWitness,
    GatewayWitnessInitializationError,
    create_witnessed_gateway_journal,
)

BINDING = AccountCoordinationBinding('acct', 'fake:route')


def authority_harness(root):
    root.mkdir(parents=True, exist_ok=True)
    server = WriterAuthorityServer(root / 'independent-authority', clients={'client': b'x' * 32})
    def call(op, **args):
        return server._dispatch('client', {'op': op, 'args': args,
            'binding': {'account_ref': 'acct', 'provider_route': 'fake:route'}})
    call('open', initial_owner='owner')
    call('acquire_writer_lease', owner_id='owner', holder_token='holder', host_id='host', provider_session_ref='fake-session')
    remote = object.__new__(RemoteAccountOwnerHandoff)
    remote.binding, remote._holder_token = BINDING, 'holder'
    remote._state_lock, remote._lease_owner = threading.Lock(), 'owner'
    remote._mono, remote._deadline = time.monotonic, time.monotonic() + 100
    remote._call = call
    return server, remote


def harness(root, *, parents=True):
    server, remote = authority_harness(root)
    guard_dir = root / 'guard'
    guard_dir.mkdir()
    guard = LocalAccountSessionGuard(guard_dir, 'acct', 'owner')
    guard.acquire()
    journal = create_witnessed_gateway_journal(root / 'journal.sqlite.witness-required', 'acct', guard=guard,
        authority=remote, owner_id='owner', owner_generation=1)
    parent = None
    if parents:
        journal.enable_controls(guard)
        journal.enable_strategy_ledger(guard)
        journal.enable_account_extensions(guard)
        journal.enable_parent_authority(guard)
        parent = GatewayParentAuthority(journal, guard, now_ns=lambda: 100, trading_day=lambda: '20261109')
    return server, remote, guard, journal, parent


@contextmanager
def fence():
    yield


def spec():
    key = MarketInstrumentKey('ctp', 'SHFE', 'simnow', 'future', 'rb2601', 'rb2601')
    return {'account_scope': 'acct', 'strategy_scope': 'strategy', 'parent_ref': 'parent',
        'registration_operation_id': 'register', 'execution_domain_ref': 'domain', 'instrument': key.to_dict(),
        'quantity': '10', 'quantity_unit': 'lots', 'side': 'buy', 'offset': 'open', 'limit_price': '100',
        'creation_trading_day': '20261109', 'allowed_trading_days': ['20261109'], 'expires_at_ns': 1000,
        'grant_revision': 'v1', 'target_position_ref': None, 'plan_digest': 'a' * 64,
        'plan_version': 'v1', 'time_in_force': 'GTD'}


def register(parent):
    return parent.register_parent(spec(), principal='exec', actor_ref='actor', fence=fence, recheck=lambda: True)


def stored(root):
    path = canonical_coordination_path(root / 'independent-authority', BINDING)
    with sqlite3.connect(path) as db:
        return db.execute('SELECT revision,pending_revision FROM gateway_continuity_witness_v2').fetchone()


def test_ordinary_journal_concurrent_wait_respects_sqlite_timeout(tmp_path):
    journal = GatewayDirectCommandJournal.create(tmp_path / 'normal.sqlite', 'acct',
                                                 generation='generation', timeout_seconds=0.1)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    errors = []
    def holder():
        with journal._transaction():
            entered.set()
            release.wait(timeout=3)
    def contender():
        try:
            with journal._transaction():
                pass
        except Exception as error:
            errors.append(error)
        finally:
            finished.set()
    first, second = threading.Thread(target=holder), threading.Thread(target=contender)
    first.start()
    try:
        assert entered.wait(timeout=2)
        second.start()
        assert finished.wait(timeout=1)
        assert len(errors) == 1 and isinstance(errors[0], sqlite3.OperationalError)
        assert 'locked' in str(errors[0]).lower()
    finally:
        release.set()
        first.join(timeout=2)
        if second.ident is not None:
            second.join(timeout=2)
    assert not first.is_alive() and not second.is_alive()


def test_witness_installation_rejects_existing_unwitnessed_transaction(tmp_path):
    directory = tmp_path / 'guard'
    directory.mkdir()
    guard = LocalAccountSessionGuard(directory, 'acct', 'owner')
    journal = GatewayDirectCommandJournal.create(tmp_path / 'init.witness-required', 'acct',
        generation=guard.acquire(), _continuity_required=True)
    entered, release = threading.Event(), threading.Event()
    class Witness:
        def matches_owner(self, account, journal_id, generation): return True
        def verify(self, *args): pass
        def prepare(self, *args): pass
        def seal(self, *args): pass
        def freeze(self): pass
    # Simulate an initialization transaction started just before installation.
    journal._continuity_initializing = True
    def transaction():
        with journal._transaction():
            entered.set()
            release.wait(timeout=3)
    worker = threading.Thread(target=transaction)
    worker.start()
    try:
        assert entered.wait(timeout=2)
        journal._continuity_initializing = False
        with pytest.raises(DirectCommandJournalError, match='CONTINUITY_WITNESS_BUSY'):
            journal.install_continuity_witness(guard=guard, witness=Witness())
        assert journal._continuity_witness is None
    finally:
        release.set()
        worker.join(timeout=2)
        guard.release()
    assert not worker.is_alive()


@pytest.mark.parametrize('fault', ['authority', 'account', 'owner', 'generation', 'path', 'options', 'exists'])
def test_factory_static_failures_do_not_acquire_guard_or_create_journal(tmp_path, fault):
    server, remote = authority_harness(tmp_path / 'authority-root')
    guard_dir = tmp_path / 'guard'
    guard_dir.mkdir()
    guard = LocalAccountSessionGuard(guard_dir, 'acct', 'owner')
    path = tmp_path / 'new.witness-required'
    options = {}
    authority, account, owner, generation = remote, 'acct', 'owner', 1
    if fault == 'authority':
        authority = None
    if fault == 'account':
        account = 'other'
    if fault == 'owner':
        owner = 'other'
    if fault == 'generation':
        generation = 0
    if fault == 'path':
        path = tmp_path / 'bad-suffix.sqlite'
    if fault == 'options':
        options['max_records'] = 0
    if fault == 'exists':
        path.write_bytes(b'preexisting')
    try:
        with pytest.raises(HandoffError):
            create_witnessed_gateway_journal(path, account, guard=guard, authority=authority,
                owner_id=owner, owner_generation=generation, **options)
        assert guard._phase == 'idle' and not guard.lock_path.exists()
        if fault == 'exists':
            assert path.read_bytes() == b'preexisting'
        else:
            assert not path.exists()
    finally:
        server.stop()


def test_factory_static_failure_keeps_caller_preheld_guard(tmp_path):
    directory = tmp_path / 'guard'
    directory.mkdir()
    guard = LocalAccountSessionGuard(directory, 'acct', 'owner')
    guard.acquire()
    path = tmp_path / 'new.witness-required'
    try:
        with pytest.raises(HandoffError, match='GATEWAY_WITNESS_BINDING_INVALID'):
            create_witnessed_gateway_journal(path, 'acct', guard=guard, authority=None,
                                             owner_id='owner', owner_generation=1)
        guard.assert_owned()
        assert not path.exists()
    finally:
        guard.release()


def test_factory_create_failure_reports_uncertain_file_and_keeps_guard(tmp_path, monkeypatch):
    server, remote = authority_harness(tmp_path / 'authority-root')
    directory = tmp_path / 'guard'
    directory.mkdir()
    guard = LocalAccountSessionGuard(directory, 'acct', 'owner')
    path = tmp_path / 'new.witness-required'
    def interrupted_create(cls, database_path, account_scope, *, generation, max_records=100000,
                           execution_max_facts=100000, clock=time.monotonic,
                           timeout_seconds=5.0, _continuity_required=False):
        database_path.write_bytes(b'partial')
        raise OSError('synthetic create failure')
    monkeypatch.setattr(GatewayDirectCommandJournal, 'create', classmethod(interrupted_create))
    try:
        with pytest.raises(GatewayWitnessInitializationError) as failure:
            create_witnessed_gateway_journal(path, 'acct', guard=guard, authority=remote,
                                             owner_id='owner', owner_generation=1)
        error = failure.value
        assert error.stage == 'CREATE' and not error.guard_was_active and error.guard_may_be_held
        assert error.journal_may_exist and not error.genesis_may_have_committed
        assert path.read_bytes() == b'partial'
        guard.assert_owned()
    finally:
        if guard._phase == 'active':
            guard.release()
        server.stop()


@pytest.mark.parametrize('preheld', [False, True])
@pytest.mark.parametrize('point', ['before_genesis', 'lost_genesis_reply'])
def test_factory_dynamic_failure_preserves_resources_and_reports_uncertainty(tmp_path, monkeypatch, preheld, point):
    from bt_api_gateway import journal_state_digest as digest_module
    server, remote = authority_harness(tmp_path / 'authority-root')
    guard_dir = tmp_path / 'guard'
    guard_dir.mkdir()
    guard = LocalAccountSessionGuard(guard_dir, 'acct', 'owner')
    path = tmp_path / 'new.witness-required'
    if preheld:
        guard.acquire()
    if point == 'before_genesis':
        def fail_digest(db):
            raise RuntimeError('synthetic digest failure')
        monkeypatch.setattr(digest_module, 'journal_state_digest', fail_digest)
    else:
        original = remote._call
        def lose_reply(op, **args):
            result = original(op, **args)
            if op == 'gateway_witness_genesis':
                raise HandoffError('HANDOFF_AUTHORITY_UNAVAILABLE')
            return result
        remote._call = lose_reply
    try:
        with pytest.raises(GatewayWitnessInitializationError) as failure:
            create_witnessed_gateway_journal(path, 'acct', guard=guard, authority=remote,
                                             owner_id='owner', owner_generation=1)
        error = failure.value
        assert error.stage == ('PRE_GENESIS' if point == 'before_genesis' else 'GENESIS')
        assert error.guard_was_active is preheld and error.guard_may_be_held
        assert error.journal_may_exist and error.genesis_may_have_committed == (point == 'lost_genesis_reply')
        guard.assert_owned()
        assert path.exists()
        with pytest.raises(DirectCommandJournalError, match='CONTINUITY_EVIDENCE_REQUIRED'):
            inspect_unresolved_dispatch(path, 'acct')
        if point == 'lost_genesis_reply':
            assert stored(tmp_path / 'authority-root') == (0, None)
    finally:
        if guard._phase == 'active':
            guard.release()
        server.stop()


def test_actual_parent_commit_and_rollback_detection(tmp_path):
    server, remote, guard, journal, parent = harness(tmp_path)
    try:
        with sqlite3.connect(tmp_path / 'journal.sqlite.witness-required') as db, sqlite3.connect(tmp_path / 'old.sqlite') as old:
            db.backup(old)
        before = stored(tmp_path)[0]
        assert register(parent)['state'] == 'ACTIVE'
        assert stored(tmp_path)[0] > before and stored(tmp_path)[1] is None
        with sqlite3.connect(tmp_path / 'old.sqlite') as old, sqlite3.connect(tmp_path / 'journal.sqlite.witness-required') as db:
            old.backup(db)
        # Attack the old implementation: copy the current witnessed revision
        # into the restored snapshot while retaining its older parent contents.
        revision, _ = stored(tmp_path)
        with sqlite3.connect(tmp_path / 'journal.sqlite.witness-required') as db:
            db.execute('UPDATE direct_journal_meta SET revision=?', (revision,))
        with pytest.raises(HandoffError, match='GATEWAY_WITNESS_FROZEN'):
            parent.query_parent('acct', 'strategy', 'parent', 'register', principal='exec', actor_ref='actor', fence=fence, recheck=lambda: True)
        assert journal._continuity_witness._frozen
    finally:
        guard.release()
        server.stop()


def test_existing_database_never_gets_genesis(tmp_path):
    server, remote, guard, journal, _ = harness(tmp_path)
    try:
        other = GatewayContinuityWitness(remote, owner_id='owner', owner_generation=1,
            account='acct', journal_id=journal.journal_id, gateway_generation=journal.generation)
        with pytest.raises(HandoffError, match='GATEWAY_WITNESS_GENESIS_REFUSED'):
            other.genesis('a' * 64)
    finally:
        guard.release()
        server.stop()


def test_returned_parent_source_is_the_sealed_revision(tmp_path):
    server, remote, guard, journal, parent = harness(tmp_path)
    try:
        journal.enable_parent_original_runs(guard)
        minimum, pending = stored(tmp_path)
        assert pending is None
        source = {'account_scope': 'acct', 'journal_id': journal.journal_id,
                      'gateway_generation': journal.generation, 'minimum_revision': minimum}
        result = parent.register_parent_v2(spec(), source, principal='exec', actor_ref='actor',
                                           fence=fence, recheck=lambda: True,
                                           original_run=lambda: 'original-run')
        assert result['result']['state'] == 'ACTIVE'
        revision, pending = stored(tmp_path)
        assert pending is None and result['source']['journal_revision'] == revision
    finally:
        guard.release()
        server.stop()


def test_raw_reconciliation_rejects_suffix_or_marker_and_keeps_legacy(tmp_path):
    # A configured witness path refuses even before a database is created.
    pending = tmp_path / 'pending.witness-required'
    with pytest.raises(DirectCommandJournalError, match='CONTINUITY_EVIDENCE_REQUIRED'):
        inspect_unresolved_dispatch(pending, 'acct')
    server, remote, guard, journal, parent = harness(tmp_path / 'witness')
    try:
        path = tmp_path / 'witness' / 'journal.sqlite.witness-required'
        with pytest.raises(DirectCommandJournalError, match='CONTINUITY_EVIDENCE_REQUIRED'):
            inspect_unresolved_dispatch(path, 'acct')
        # Copy to a normal-looking name: the independent marker still refuses.
        copy = tmp_path / 'marked.sqlite'
        with sqlite3.connect(path) as src, sqlite3.connect(copy) as dst:
            src.backup(dst)
        with pytest.raises(DirectCommandJournalError, match='CONTINUITY_EVIDENCE_REQUIRED'):
            inspect_unresolved_dispatch(copy, 'acct')
        for candidate in (path, copy):
            with pytest.raises(DirectCommandJournalError, match='CONTINUITY_EVIDENCE_REQUIRED'):
                GatewayDirectCommandJournal(candidate, 'acct', generation=journal.generation,
                    expected_journal_id=journal.journal_id, minimum_revision=0,
                    controls_enabled=True, strategy_ledger_enabled=True,
                    account_extensions_enabled=True, parent_authority_enabled=True)
    finally:
        guard.release()
        server.stop()
    directory = tmp_path / 'legacy-guard'
    directory.mkdir()
    legacy_guard = LocalAccountSessionGuard(directory, 'legacy', 'owner')
    generation = legacy_guard.acquire()
    try:
        path = tmp_path / 'legacy.sqlite'
        GatewayDirectCommandJournal.create(path, 'legacy', generation=generation)
        assert all(value == 0 for value in inspect_unresolved_dispatch(path, 'legacy').values())
        unbound = GatewayDirectCommandJournal.create(tmp_path / 'unbound.witness-required',
            'legacy', generation=generation, _continuity_required=True)
        with pytest.raises(DirectCommandJournalError, match='CONTINUITY_EVIDENCE_REQUIRED'):
            unbound.reserve('s', 'request', 'principal', {'symbol': 'X'})
        with pytest.raises(DirectCommandJournalError, match='CONTINUITY_EVIDENCE_REQUIRED'):
            unbound.settlement_account_frozen()
    finally:
        legacy_guard.release()


@pytest.mark.skipif(not hasattr(os, 'fork'), reason='requires process termination')
@pytest.mark.parametrize('point', ['before_genesis', 'after_genesis'])
def test_creation_crash_never_makes_raw_recovery_available(tmp_path, point):
    child = os.fork()
    if child == 0:
        original = GatewayContinuityWitness.genesis
        def genesis(witness, digest):
            if point == 'before_genesis':
                os._exit(61)
            original(witness, digest)
            os._exit(62)
        GatewayContinuityWitness.genesis = genesis
        try:
            harness(tmp_path)
        except BaseException:
            os._exit(90)
        os._exit(90)
    _, status = os.waitpid(child, 0)
    assert os.waitstatus_to_exitcode(status) == (61 if point == 'before_genesis' else 62)
    path = tmp_path / 'journal.sqlite.witness-required'
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA application_id').fetchone()[0] == 0x47575432
    with pytest.raises(DirectCommandJournalError, match='CONTINUITY_EVIDENCE_REQUIRED'):
        inspect_unresolved_dispatch(path, 'acct')


@pytest.mark.skipif(not hasattr(os, 'fork'), reason='requires process termination')
def test_actual_ctp_operator_helper_refuses_witnessed_path(tmp_path):
    from bt_api_py.ctp_gateway_trading import (
        CtpGatewayTradingSettings,
        reconcile_ctp_gateway_account_session,
    )
    key = AccountKey('ctp', 'simnow', 'simnow', 'synthetic-account', 'synthetic-subaccount')
    guard_dir = tmp_path / 'ctp-guard'
    flow_dir = tmp_path / 'flow'
    guard_dir.mkdir(mode=0o700)
    flow_dir.mkdir(mode=0o700)
    child = os.fork()
    if child == 0:
        guard = LocalAccountSessionGuard(guard_dir, key.account_key_id, 'owner')
        guard.acquire()
        os._exit(0)
    _, status = os.waitpid(child, 0)
    assert os.waitstatus_to_exitcode(status) == 0
    settings = CtpGatewayTradingSettings(account_key=key, account_scope=key.account_key_id,
        guard_root=guard_dir, owner_id='owner', journal_path=tmp_path / 'incomplete.witness-required',
        expected_journal_id='synthetic', minimum_revision=0, trusted_flow_root=flow_dir,
        market_source_ref='source', market_entitlement_ref='entitlement')
    with pytest.raises(DirectCommandJournalError, match='CONTINUITY_EVIDENCE_REQUIRED'):
        reconcile_ctp_gateway_account_session(settings, tmp_path / 'registry.sqlite',
                                               operator_ref='operator', reason='synthetic test')
    # It must not close the prior active guard or report NOT_REQUIRED on retry.
    with pytest.raises(DirectCommandJournalError, match='CONTINUITY_EVIDENCE_REQUIRED'):
        reconcile_ctp_gateway_account_session(settings, tmp_path / 'registry.sqlite',
                                               operator_ref='operator', reason='synthetic test')


def test_direct_reads_refuse_frozen_witness(tmp_path):
    server, remote, guard, journal, parent = harness(tmp_path)
    try:
        assert journal.settlement_account_frozen() is False
        with _read_only_snapshot(journal) as db:
            assert db.execute('SELECT count(*) FROM direct_execution_parents').fetchone()[0] == 0
        journal._continuity_witness.freeze()
        with pytest.raises(DirectCommandJournalError, match='CONTINUITY_WITNESS_UNAVAILABLE'):
            journal.settlement_account_frozen()
        with pytest.raises(DirectCommandJournalError, match='CONTINUITY_WITNESS_UNAVAILABLE'):
            journal.recheck_dispatch_controls('strategy', 'request', 'principal', 'reservation', 'digest')
        with (pytest.raises(DirectCommandJournalError, match='CONTINUITY_WITNESS_UNAVAILABLE'),
              _read_only_snapshot(journal)):
            pass
    finally:
        guard.release()
        server.stop()


def test_reads_preserve_revision_and_unversioned_write_is_refused(tmp_path):
    server, remote, guard, journal, _ = harness(tmp_path, parents=False)
    try:
        first = journal.reserve('strategy', 'one', 'principal', {'symbol': 'X'})
        journal.reserve('strategy', 'two', 'principal', {'symbol': 'Y'})
        revision, pending = stored(tmp_path)
        page = journal.recovery_page(limit=1)
        assert page.next_cursor is not None
        assert journal.recovery_page(limit=1, cursor=page.next_cursor).records
        assert journal.get('strategy', 'one', 'principal') == first
        assert stored(tmp_path) == (revision, pending)
        # Even when a callback swallows SQLite's refusal, it cannot commit a
        # write outside the PREPARE/SEAL transaction boundary.
        with (pytest.raises(DirectCommandJournalError, match='CONTINUITY_TRANSACTION_VIOLATION'),
              journal._transaction() as db):
            db.execute("UPDATE direct_journal_commands SET payload_json='{}' WHERE request_key='one'")
            with suppress(sqlite3.DatabaseError):
                db.execute('COMMIT')
        assert journal.get('strategy', 'one', 'principal') == first
        assert stored(tmp_path) == (revision, pending)
        with (pytest.raises(DirectCommandJournalError, match='CONTINUITY_REVISION_REQUIRED'),
              journal._transaction() as db):
            db.execute("UPDATE direct_journal_commands SET payload_json='{}' WHERE request_key='one'")
        assert journal.get('strategy', 'one', 'principal') == first
        assert stored(tmp_path) == (revision, pending)
    finally:
        guard.release()
        server.stop()


def test_non_owner_thread_refuses_before_serial_lock(tmp_path):
    server, remote, guard, journal, parent = harness(tmp_path)
    errors = []
    try:
        def read():
            try:
                journal.settlement_account_frozen()
            except Exception as exc:
                errors.append(str(exc))
        with journal._continuity_serial_lock:
            worker = threading.Thread(target=read)
            worker.start()
            worker.join(timeout=2)
            assert not worker.is_alive()
        assert len(errors) == 1 and 'restricted to the creating process and thread' in errors[0]
    finally:
        guard.release()
        server.stop()


def test_change_port_failure_after_seal_freezes_journal(tmp_path):
    server, remote, guard, journal, parent = harness(tmp_path)
    class Port:
        poisoned = False
        def matches_owner(self, account, generation): return True
        def begin_change(self, domain): return 'token'
        def finish_change(self, token, state): raise RuntimeError('finish failed')
        def poison(self, token): self.poisoned = True
        def poison_domain(self, domain): self.poisoned = True
    port = Port()
    try:
        journal.install_dispatch_change_port(guard=guard, port=port)
        with pytest.raises(RuntimeError, match='finish failed'):
            register(parent)
        assert port.poisoned and journal._continuity_witness._frozen
        revision, pending = stored(tmp_path)
        with sqlite3.connect(tmp_path / 'journal.sqlite.witness-required') as db:
            assert db.execute('SELECT count(*) FROM direct_execution_parents').fetchone()[0] == 1
            assert db.execute('SELECT revision FROM direct_journal_meta').fetchone()[0] == revision
        assert pending is None
    finally:
        guard.release()
        server.stop()


def test_witnessed_prototype_cannot_construct_provider_dispatch(tmp_path):
    from bt_api_py.gateway_dispatch_claim import (
        GATEWAY_DISPATCH_COVERAGE,
        GatewayDispatchClaimCoordinator,
        GatewayJournalDispatchChangePort,
        GatewayNativeDispatchClaimPort,
        seal_gateway_dispatch_coverage,
    )

    server, remote, guard, journal, _ = harness(tmp_path, parents=False)
    calls = []
    claims, sealer = GatewayDispatchClaimCoordinator.with_coverage_sealer('acct', journal.generation)
    journal.install_dispatch_change_port(guard=guard, port=GatewayJournalDispatchChangePort(claims))
    wired = frozenset(label for labels in GATEWAY_DISPATCH_COVERAGE.values() for label in labels)
    seal_gateway_dispatch_coverage(claims, sealer, wired=wired)
    def native(payload, port, scope):
        calls.append('synthetic-send')
    try:
        row = journal.reserve('strategy', 'request', 'principal', {'symbol': 'X', 'qty': '1'})
        before = claims.capture()
        assert journal.get('strategy', 'request', 'principal') == row
        after = claims.capture()
        assert before.versions == after.versions
        claims.discard_pair(before, after)
        with pytest.raises(DirectDispatchError, match='CONTINUITY_DISPATCH_UNAVAILABLE'):
            GatewayDirectDispatcher(journal, 'TEST', guard, native,
                lambda identity: ('TEST:' + identity.client_order_id,), lambda row: True,
                claim_port=GatewayNativeDispatchClaimPort(claims, journal_id=journal.journal_id))
        assert calls == []
    finally:
        guard.release()
        server.stop()


@pytest.mark.parametrize('operation', ['gateway_witness_prepare', 'gateway_witness_seal'])
def test_lost_authority_reply_freezes_session(tmp_path, operation):
    server, remote, guard, journal, parent = harness(tmp_path)
    original = remote._call
    def lose_reply(op, **args):
        result = original(op, **args)
        if op == operation:
            raise HandoffError('HANDOFF_AUTHORITY_UNAVAILABLE')
        return result
    remote._call = lose_reply
    try:
        with pytest.raises(HandoffError, match='HANDOFF_AUTHORITY_UNAVAILABLE'):
            register(parent)
        assert journal._continuity_witness._frozen
        with pytest.raises(DirectCommandJournalError, match='CONTINUITY_WITNESS_UNAVAILABLE'):
            register(parent)
        revision, pending = stored(tmp_path)
        with sqlite3.connect(tmp_path / 'journal.sqlite.witness-required') as db:
            count = db.execute('SELECT count(*) FROM direct_execution_parents').fetchone()[0]
        assert (pending is not None, count) == ((True, 0) if operation.endswith('prepare') else (False, 1))
    finally:
        guard.release()
        server.stop()


@pytest.mark.skipif(not hasattr(os, 'fork'), reason='requires process termination')
@pytest.mark.parametrize('point', ['before_commit', 'after_commit', 'after_seal'])
def test_process_crash_freezes_original_identity(tmp_path, point):
    child = os.fork()
    if child == 0:
        try:
            server, remote, guard, journal, parent = harness(tmp_path)
            original_commit = journal._commit
            original_seal = journal._continuity_witness.seal
            def commit(db):
                if point == 'before_commit':
                    os._exit(51)
                original_commit(db)
                if point == 'after_commit':
                    os._exit(52)
            def seal(before, before_digest, after, after_digest):
                original_seal(before, before_digest, after, after_digest)
                if point == 'after_seal':
                    os._exit(53)
            journal._commit = commit
            journal._continuity_witness.seal = seal
            register(parent)
            os._exit(90)
        except BaseException:
            os._exit(91)
    _, status = os.waitpid(child, 0)
    assert os.waitstatus_to_exitcode(status) == {'before_commit': 51, 'after_commit': 52, 'after_seal': 53}[point]
    revision, pending = stored(tmp_path)
    with sqlite3.connect(tmp_path / 'journal.sqlite.witness-required') as db:
        count = db.execute('SELECT count(*) FROM direct_execution_parents').fetchone()[0]
        actual = db.execute('SELECT revision FROM direct_journal_meta').fetchone()[0]
    if point == 'before_commit':
        assert pending is not None and actual == revision and count == 0
    elif point == 'after_commit':
        assert pending == actual and count == 1
    else:
        assert pending is None and revision == actual and count == 1
        # A restarted authority rearms the old lease; another process cannot
        # silently replace the original witness session after reply loss.
        server = WriterAuthorityServer(tmp_path / 'independent-authority', clients={'client': b'x' * 32})
        binding = {'account_ref': 'acct', 'provider_route': 'fake:route'}
        def call(op, **args):
            return server._dispatch('client', {'op': op, 'args': args, 'binding': binding})
        try:
            call('open', initial_owner='owner')
            call('acquire_writer_lease', owner_id='owner', holder_token='holder', host_id='host', provider_session_ref='fake-session')
            with sqlite3.connect(canonical_coordination_path(tmp_path / 'independent-authority', BINDING)) as db:
                journal_id, generation, digest = db.execute('SELECT journal_id,gateway_generation,digest FROM gateway_continuity_witness_v2').fetchone()
            with pytest.raises(HandoffError, match='GATEWAY_WITNESS_IDENTITY_MISMATCH'):
                call('gateway_witness_verify', owner_id='owner', generation=1, holder_token='holder',
                     journal_id=journal_id, gateway_generation=generation, session='new-process', revision=revision, digest=digest)
        finally:
            server.stop()
