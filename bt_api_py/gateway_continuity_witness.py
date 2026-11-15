"""Explicit, fail-closed Gateway journal witness hosted by writer authority.

A trusted deployment must isolate the authority store from the journal failure
 domain and fence all earlier/uninstrumented writers. A pending transaction is
never guessed or automatically repaired. A process restart needs an explicit
recovery protocol, which this initial implementation deliberately lacks.
"""
from __future__ import annotations

import hashlib
import inspect
import os
import secrets
from pathlib import Path

from .account_owner_handoff import HandoffError


class GatewayWitnessInitializationError(HandoffError):
    """Initialization failed after resource acquisition began; no cleanup is implied."""

    def __init__(self, *, stage, guard_was_active, journal_may_exist, genesis_may_have_committed):
        super().__init__('GATEWAY_WITNESS_INITIALIZATION_FAILED')
        self.stage = stage
        self.guard_was_active = guard_was_active
        self.guard_may_be_held = True
        self.journal_may_exist = journal_may_exist
        self.genesis_may_have_committed = genesis_may_have_committed


def _preflight(path, account, guard, authority, owner_id, owner_generation, options):
    """Check static bindings and constructor options before acquiring resources."""
    from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
    from bt_api_gateway.session_guard import LocalAccountSessionGuard

    from .account_writer_authority import RemoteAccountOwnerHandoff, _text

    if (type(guard) is not LocalAccountSessionGuard or
            type(authority) is not RemoteAccountOwnerHandoff or
            not _text(account) or not _text(owner_id) or type(owner_generation) is not int or
            not 1 <= owner_generation < 2**63 or authority.binding.account_ref != account or
            authority.holds_owner_lock != owner_id or not _text(authority._holder_token) or
            guard.account_key_digest != hashlib.sha256(account.encode('utf-8')).hexdigest()):
        raise HandoffError('GATEWAY_WITNESS_BINDING_INVALID')
    guard._check_owner()
    if guard._phase not in ('idle', 'active'):
        raise HandoffError('GATEWAY_WITNESS_BINDING_INVALID')
    try:
        candidate = Path(path)
        if not os.fspath(candidate).endswith('.witness-required'):
            raise HandoffError('GATEWAY_WITNESS_PATH_INVALID')
        if os.path.lexists(candidate):
            raise HandoffError('GATEWAY_WITNESS_DATABASE_EXISTS')
        if not candidate.parent.is_dir():
            raise HandoffError('GATEWAY_WITNESS_PATH_INVALID')
        params = inspect.signature(GatewayDirectCommandJournal.create).bind(
            candidate, account, generation='preflight', _continuity_required=True, **options)
        params.apply_defaults()
        args = params.arguments
        if not callable(args['clock']):
            raise HandoffError('GATEWAY_WITNESS_OPTIONS_INVALID')
        probe = GatewayDirectCommandJournal.__new__(GatewayDirectCommandJournal)
        probe._configure(candidate, account, 'preflight', args['max_records'],
                         args['execution_max_facts'], args['clock'], args['timeout_seconds'])
    except HandoffError:
        raise
    except (TypeError, ValueError, OSError, RuntimeError):
        raise HandoffError('GATEWAY_WITNESS_OPTIONS_INVALID') from None


def authority_operation(engine, operation, args):
    """Execute witness state transitions under the authority's own writer lock."""
    text_fields = ('owner_id', 'holder_token', 'journal_id', 'gateway_generation', 'session')
    if any(type(args.get(k)) is not str or not args[k] or len(args[k]) > 256 for k in text_fields):
        raise HandoffError('GATEWAY_WITNESS_INVALID')
    generation, revision = args['generation'], args['revision']
    if type(generation) is not int or generation < 1 or type(revision) is not int or not 0 <= revision < 2**63 - 1:
        raise HandoffError('GATEWAY_WITNESS_INVALID')
    digest = args['digest']
    if type(digest) is not str or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
        raise HandoffError('GATEWAY_WITNESS_INVALID')
    with engine._atomic() as db:
        refusal = engine._dispatch_refusal(db, args['owner_id'], generation, args['holder_token'])
        if refusal is not None:
            raise HandoffError(refusal)
        db.execute('CREATE TABLE IF NOT EXISTS gateway_continuity_witness_v2 ('
                   'account TEXT PRIMARY KEY, journal_id TEXT NOT NULL, gateway_generation TEXT NOT NULL, '
                   'session TEXT NOT NULL, revision INTEGER NOT NULL, digest TEXT NOT NULL, '
                   'pending_revision INTEGER, pending_digest TEXT)')
        row = db.execute('SELECT journal_id,gateway_generation,session,revision,digest,pending_revision,pending_digest '
                         'FROM gateway_continuity_witness_v2 WHERE account=?', (engine.account_key,)).fetchone()
        identity = (args['journal_id'], args['gateway_generation'], args['session'])
        if operation == 'gateway_witness_genesis':
            old_table = db.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='gateway_continuity_witness'").fetchone()
            old_row = (None if old_table is None else db.execute(
                'SELECT 1 FROM gateway_continuity_witness WHERE account=?', (engine.account_key,)).fetchone())
            if row is not None or old_row is not None or revision != 0:
                raise HandoffError('GATEWAY_WITNESS_GENESIS_REFUSED')
            db.execute('INSERT INTO gateway_continuity_witness_v2 VALUES (?,?,?,?,?,?,NULL,NULL)',
                       (engine.account_key, *identity, 0, digest))
        elif row is None or tuple(row[:3]) != identity:
            raise HandoffError('GATEWAY_WITNESS_IDENTITY_MISMATCH')
        elif operation == 'gateway_witness_verify':
            if row[5] is not None or row[6] is not None or row[3] != revision or row[4] != digest:
                raise HandoffError('GATEWAY_WITNESS_FROZEN')
        elif operation == 'gateway_witness_prepare':
            target = args['target_revision']
            target_digest = args['target_digest']
            if (type(target) is not int or not revision < target < 2**63 - 1 or row[5] is not None
                    or row[6] is not None or row[3] != revision or row[4] != digest
                    or type(target_digest) is not str or len(target_digest) != 64
                    or any(c not in '0123456789abcdef' for c in target_digest)):
                raise HandoffError('GATEWAY_WITNESS_FROZEN')
            db.execute('UPDATE gateway_continuity_witness_v2 SET pending_revision=?,pending_digest=? WHERE account=?',
                       (target, target_digest, engine.account_key))
        elif operation == 'gateway_witness_seal':
            target = args['target_revision']
            target_digest = args['target_digest']
            if (type(target) is not int or row[3] != revision or row[4] != digest
                    or row[5] != target or row[6] != target_digest or target <= revision):
                raise HandoffError('GATEWAY_WITNESS_FROZEN')
            db.execute('UPDATE gateway_continuity_witness_v2 SET revision=?,digest=?,pending_revision=NULL,pending_digest=NULL WHERE account=?',
                       (target, target_digest, engine.account_key))
        else:
            raise HandoffError('GATEWAY_WITNESS_INVALID')
    return True


class GatewayContinuityWitness:
    """Process-bound opt-in adapter over an authenticated writer-authority client.

    Use only with a new, exclusively created journal; never bootstrap an old
    database. Both authority storage isolation and complete writer fencing are
    trusted deployment requirements, not inferred from pathnames.
    """
    def __init__(self, authority, *, owner_id, owner_generation, account, journal_id, gateway_generation):
        from .account_writer_authority import RemoteAccountOwnerHandoff
        if (type(authority) is not RemoteAccountOwnerHandoff or authority.binding.account_ref != account
                or authority.holds_owner_lock != owner_id or type(owner_generation) is not int):
            raise HandoffError('GATEWAY_WITNESS_BINDING_INVALID')
        self._authority, self._owner, self._owner_generation = authority, owner_id, owner_generation
        self._account, self._journal_id, self._gateway_generation = account, journal_id, gateway_generation
        self._session, self._pid, self._frozen = secrets.token_hex(32), os.getpid(), False

    def matches_owner(self, account, journal_id, generation):
        return (not self._frozen and os.getpid() == self._pid and
                (account, journal_id, generation) == (self._account, self._journal_id, self._gateway_generation))

    def _call(self, op, revision, digest, target=None, target_digest=None):
        if not self.matches_owner(self._account, self._journal_id, self._gateway_generation):
            raise HandoffError('GATEWAY_WITNESS_FROZEN')
        args = {'owner_id': self._owner, 'generation': self._owner_generation,
                    'holder_token': self._authority._holder_token, 'journal_id': self._journal_id,
                    'gateway_generation': self._gateway_generation, 'session': self._session, 'revision': revision,
                    'digest': digest}
        if target is not None:
            args['target_revision'] = target
            args['target_digest'] = target_digest
        try:
            if self._authority._call(op, **args) is not True:
                raise HandoffError('GATEWAY_WITNESS_UNAVAILABLE')
        except BaseException:
            self._frozen = True
            raise

    def genesis(self, digest):
        self._call('gateway_witness_genesis', 0, digest)

    def verify(self, revision, digest):
        self._call('gateway_witness_verify', revision, digest)

    def prepare(self, revision, digest, target, target_digest):
        self._call('gateway_witness_prepare', revision, digest, target, target_digest)

    def seal(self, revision, digest, target, target_digest):
        self._call('gateway_witness_seal', revision, digest, target, target_digest)

    def freeze(self):
        self._frozen = True


def create_witnessed_gateway_journal(path, account, *, guard, authority, owner_id, owner_generation, **options):
    """Exclusively create, establish genesis and install before returning a journal.

    This deliberately does not attach to or migrate an existing journal. The
    trusted deployment must keep its ``.witness-required`` path fixed and must
    not rename or move the database to a normal path. The suffix and initial
    database marker block the legacy raw recovery helper under that condition;
    neither proves independent storage or resists joint path/marker rollback.
    Static failures occur before guard acquisition or file creation. Later
    failures raise GatewayWitnessInitializationError with their resource and
    genesis uncertainty; the caller retains the guard and any created files.
    """
    from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
    _preflight(path, account, guard, authority, owner_id, owner_generation, options)
    guard_was_active = guard._phase == 'active'
    stage, journal_may_exist, genesis_may_have_committed = 'GUARD_ACQUIRE', False, False
    try:
        generation = guard.acquire()
        stage, journal_may_exist = 'CREATE', True
        journal = GatewayDirectCommandJournal.create(path, account, generation=generation,
                                                      _continuity_required=True, **options)
        stage = 'PRE_GENESIS'
        journal._assert_account_guard(guard)
        witness = GatewayContinuityWitness(authority, owner_id=owner_id, owner_generation=owner_generation,
            account=account, journal_id=journal.journal_id, gateway_generation=generation)
        from bt_api_gateway.journal_state_digest import journal_state_digest
        with journal._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                digest = journal_state_digest(db)
            finally:
                db.execute('ROLLBACK')
        stage, genesis_may_have_committed = 'GENESIS', True
        witness.genesis(digest)
        stage = 'INSTALL'
        journal.install_continuity_witness(guard=guard, witness=witness)
        return journal
    except BaseException as exc:
        raise GatewayWitnessInitializationError(stage=stage, guard_was_active=guard_was_active,
            journal_may_exist=journal_may_exist, genesis_may_have_committed=genesis_may_have_committed) from exc
