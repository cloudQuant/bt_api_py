"""Owner-side lease DRAIN recovery against a trusted, frozen provider cut.

This is an internal composition API: a trusted host installs the provider
source. No wire request or configuration value is a recovery proof. If no
source is installed, or the source cannot prove a comparable cut, recovery is
unavailable. The source's context must fence changes to that cut until exit.
"""
from __future__ import annotations

import hashlib
import json
import math
import threading
from dataclasses import dataclass
from typing import Protocol

from bt_api_base.service_protocol import WireRole
from bt_api_base.gateway_execution_stream_protocol import gateway_execution_consumer_ref


@dataclass(frozen=True)
class LeaseRecoveryBinding:
    instance_id: str
    account: str
    strategy: str
    run_id: str
    principal: str
    expired_registration: str
    registration: str
    service_epoch: str
    journal_id: str
    owner_generation: str
    cause_id: str
    cause_version: int
    journal_revision: int
    consumer_ref: str
    acknowledged_sequence: int


@dataclass(frozen=True)
class LeaseRecoveryEvidence:
    """Facts supplied by the installed source, never accepted from a client."""

    binding: LeaseRecoveryBinding
    evidence_id: str
    provider_cut: str
    valid_until_monotonic: float


class LeaseRecoverySource(Protocol):
    def hold_verified(self, binding: LeaseRecoveryBinding):
        """Yield evidence under its source/session fence, or None if unprovable.

        A source must prove completeness and a common provider/return-stream cut,
        and that the strategy has completed restoration through the bound ACK.
        It must keep the cut valid for the lifetime of this context. Credentials,
        config labels, and repeated empty queries are not such a proof.
        """

    def recheck(self, evidence: LeaseRecoveryEvidence) -> bool:
        """Return exactly True only if source authority and cut remain valid."""


class LeaseRecoveryUnavailable(RuntimeError):
    pass


def _text(value):
    return type(value) is str and bool(value) and value == value.strip() and len(value) <= 256


class GatewayLeaseRecovery:
    """Recover one actual expiry cause; all checks run on the account owner."""

    def __init__(self, *, registry, instance_id, account_ref, owner, control,
                 source: LeaseRecoverySource | None, clock):
        from bt_api_py.service_registry import ServiceRegistry
        from bt_api_py.gateway_direct_owner import GatewayDirectOwner
        from bt_api_gateway.direct_control import GatewayDirectControl

        journal = control._journal if type(control) is GatewayDirectControl else None
        if (type(registry) is not ServiceRegistry or type(owner) is not GatewayDirectOwner
                or journal is None or not owner.is_bound_to(journal)
                or journal.account_scope != account_ref or not _text(instance_id)
                or not _text(account_ref) or not callable(clock)
                or (source is not None and (not callable(getattr(source, 'hold_verified', None))
                                             or not callable(getattr(source, 'recheck', None))))):
            raise ValueError('INVALID_LEASE_RECOVERY')
        self.registry, self.instance_id, self.account = registry, instance_id, account_ref
        self.owner, self.control, self.journal = owner, control, journal
        self.source, self.clock = source, clock
        self.origin = 'system:lease:' + hashlib.sha256(
            (instance_id + '\0' + account_ref).encode()).hexdigest()

    def recover_auto(self, cause_id):
        """Run on the owner queue; an uncertain timeout remains NOT_COMPARABLE."""
        if not _text(cause_id):
            raise ValueError('INVALID_LEASE_RECOVERY')
        if self.source is None:
            return 'NOT_COMPARABLE'
        try:
            result = self.owner.submit_service_call(
                lambda: self._recover_auto_owned(cause_id)).result(timeout_seconds=1.0)
            return 'RECOVERED' if result is not None else 'NOT_COMPARABLE'
        except Exception:
            # A timeout can have committed. Observe the same cause; do not
            # issue a new operation or infer a provider outcome.
            return 'NOT_COMPARABLE'

    def _recover_auto_owned(self, cause_id):
        if threading.current_thread() is not self.owner._thread:
            raise LeaseRecoveryUnavailable('OWNER_THREAD')
        with self.registry._read_snapshot():
            return self._recover_in_snapshot(cause_id)

    def recover_manual_owned(self, cause_id, expected_version, operation_id, *, admission, context, arguments):
        """Use the operator's actual Registry-issued permit on the owner thread.

        Call outside an admission scope: this method holds that scope from the
        authenticated read through the same-journal commit. Consumer identity
        is always derived from the current authenticated strategy registration.
        """
        from bt_api_py.service_registry import ServiceCallContext, ServiceDispatchAdmission
        if (threading.current_thread() is not self.owner._thread
                or type(admission) is not ServiceDispatchAdmission or admission._registry is not self.registry
                or type(context) is not ServiceCallContext
                or not admission.matches_operation('direct.control.resume')
                or not admission.matches_context(context) or not admission.matches(arguments)
                or not _text(cause_id) or type(expected_version) is not int or expected_version < 1
                or not _text(operation_id) or arguments.get('cause_id') != cause_id
                or arguments.get('expected_version') != expected_version
                or arguments.get('operation_id') != operation_id
                or set(arguments) not in ({'cause_id', 'expected_version', 'operation_id'},
                                          {'cause_id', 'expected_version', 'operation_id', 'expected_journal_id'})
                or context.identity.role is not WireRole.APPLICATION_CLIENT
                or context.scope.account_ref != self.account or context.scope.account_refs
                or not _text(context.scope.strategy_ref)
                or ('expected_journal_id' in arguments and
                    arguments['expected_journal_id'] != self.journal.journal_id)):
            raise ValueError('INVALID_LEASE_RECOVERY_ADMISSION')
        try:
            with admission.scope() as recheck:
                return self._recover_in_snapshot(cause_id, expected_version=expected_version,
                    operation_id=operation_id, strategy=context.scope.strategy_ref, recheck=recheck)
        except LeaseRecoveryUnavailable:
            return None

    def _registration(self, event):
        r = self.registry
        rows = r._db.execute(
            'SELECT * FROM sessions WHERE instance_id=? AND port=? AND service_epoch=? AND principal=?',
            (self.instance_id, 'service.direct_write', r._epoch, event['principal'])).fetchall()
        matches = []
        for row in rows:
            identity, scope = r._stored_identity(row['identity_json']), r._stored_scope(row['scope_json'])
            operations = frozenset(json.loads(row['write_operations_json']))
            if (not r._ready or row['registration_id'] == event['registration_id'] or row['expires_at'] <= r._now()
                    or identity.role is not WireRole.STRATEGY_CLIENT or identity.delegate_ref is not None
                    or identity.actor_ref != event['strategy_ref'] or identity.principal_ref != event['principal']
                    or scope.account_ref != self.account or scope.account_refs
                    or scope.manual_book_ref is not None or scope.group_id is not None or scope.leg_id is not None
                    or scope.strategy_ref != event['strategy_ref'] or scope.run_id != event['run_id']
                    or row['policy_version'] != r.policy_version
                    or row['requirements_version'] != r.requirements_version
                    or not r._identity_is_trusted(event['principal'], identity)
                    or not r._registration_policy_matches(row)
                    or not r._strategy_claim_exists(scope, row['registration_id'])
                    or not any(r._current_resource_allows(event['principal'], identity,
                        instance_id=self.instance_id, port=row['port'], scope_ref=row['scope_ref'],
                        scope=scope, operation=op)
                        for op in operations.intersection({'direct.reserve', 'direct.dispatch'}))):
                continue
            matches.append(row)
        if len(matches) != 1:
            raise LeaseRecoveryUnavailable('REGISTRATION')
        return matches[0]

    def _snapshot(self, db, event, registration, cause_id, consumer_ref, *, resumed=False):
        account, strategy = self.account, event['strategy_ref']
        meta = db.execute('SELECT journal_id,revision FROM direct_journal_meta WHERE account=?', (account,)).fetchone()
        cause = db.execute('SELECT * FROM direct_control_causes WHERE account=? AND cause_id=?', (account, cause_id)).fetchone()
        consumer = db.execute('SELECT acknowledged_sequence FROM direct_execution_consumers WHERE account=? AND principal=? AND consumer=? AND strategy=?',
                              (account, event['principal'], consumer_ref, strategy)).fetchone()
        latest = db.execute('SELECT coalesce(max(sequence),0) FROM direct_execution_events WHERE account=? AND strategy=?', (account, strategy)).fetchone()[0]
        pending_command = db.execute("SELECT 1 FROM direct_journal_commands WHERE account=? AND strategy=? AND status IN ('DISPATCHING','UNKNOWN') LIMIT 1", (account, strategy)).fetchone()
        pending_cancel = db.execute("SELECT 1 FROM direct_cancel_attempts WHERE account=? AND strategy=? AND status IN ('REQUESTED','DISPATCHING','UNKNOWN') LIMIT 1", (account, strategy)).fetchone()
        binding = self.owner.binding()
        if (meta is None or binding is None or not self.owner.is_bound_to(self.journal)
                or (binding.account_scope, binding.journal_id, binding.generation) !=
                   (account, self.journal.journal_id, self.journal.generation)
                or meta['journal_id'] != binding.journal_id or cause is None
                or cause['active'] != (0 if resumed else 1) or cause['mode'] != 'DRAIN' or cause['origin'] != self.origin
                or cause['scope_kind'] != 'strategy' or cause['scope_value'] != strategy
                or consumer is None
                or consumer['acknowledged_sequence'] != latest or pending_command or pending_cancel):
            raise LeaseRecoveryUnavailable('LOCAL_STATE')
        return LeaseRecoveryBinding(self.instance_id, account, strategy, event['run_id'], event['principal'],
            event['registration_id'], registration['registration_id'], registration['service_epoch'],
            binding.journal_id, binding.generation, cause_id, cause['version'] - (1 if resumed else 0), meta['revision'], consumer_ref, latest)

    def _recover_in_snapshot(self, cause_id, *, expected_version=None, operation_id=None,
                             strategy=None, recheck=None):
        r, j = self.registry, self.journal
        event = r._db.execute('SELECT * FROM gateway_lease_expirations WHERE event_id=? AND instance_id=? AND account_ref=? AND state=?',
                              (cause_id, self.instance_id, self.account, 'APPLIED')).fetchone()
        if event is None:
            return None
        expected = 'lease:' + hashlib.sha256(json.dumps(
            [self.instance_id, event['registration_id'], event['service_epoch']],
            separators=(',', ':')).encode()).hexdigest()
        if cause_id != expected:
            return None
        if strategy is not None and event['strategy_ref'] != strategy:
            return None
        if expected_version is not None:
            self.control._owned()
            with j._transaction() as db:
                prior = db.execute('SELECT * FROM direct_control_causes WHERE account=? AND cause_id=?',
                                   (self.account, cause_id)).fetchone()
                if (prior is not None and prior['origin'] == self.origin
                        and prior['scope_kind'] == 'strategy' and prior['scope_value'] == strategy
                        and prior['active'] == 0 and prior['version'] == expected_version + 1
                        and prior['resume_id'] == operation_id):
                    recheck()
                    return dict(prior)
        if self.source is None:
            return None
        registration = self._registration(event)
        identity = r._stored_identity(registration['identity_json'])
        scope = r._stored_scope(registration['scope_json'])
        consumer_ref = gateway_execution_consumer_ref(principal_ref=registration['principal'],
            actor_ref=identity.actor_ref, execution_domain_ref=scope.execution_domain_ref,
            account_scope=self.account, strategy_scope=scope.strategy_ref)
        with j._transaction() as db:
            binding = self._snapshot(db, event, registration, cause_id, consumer_ref)
        if expected_version is not None and binding.cause_version != expected_version:
            return None
        with self.source.hold_verified(binding) as evidence:
            if (type(evidence) is not LeaseRecoveryEvidence or evidence.binding != binding
                    or not _text(evidence.evidence_id) or not _text(evidence.provider_cut)
                    or type(evidence.valid_until_monotonic) not in (int, float)
                    or not math.isfinite(evidence.valid_until_monotonic)):
                return None
            def before_commit_db(db):
                if recheck is not None:
                    recheck()
                self.control._owned()
                current_registration = self._registration(event)
                if current_registration['registration_id'] != binding.registration:
                    raise LeaseRecoveryUnavailable('REGISTRATION_CHANGED')
                # resume has already toggled this cause inside the same SQL
                # transaction; verify the pre-state by checking its exact
                # pending update while preserving the original revision.
                row = db.execute('SELECT * FROM direct_control_causes WHERE account=? AND cause_id=?',
                                 (self.account, cause_id)).fetchone()
                if (row is None or row['active'] != 0 or row['version'] != binding.cause_version + 1
                        or row['origin'] != self.origin or row['scope_value'] != binding.strategy):
                    raise LeaseRecoveryUnavailable('CAUSE_CHANGED')
                if self._snapshot(db, event, current_registration, cause_id, consumer_ref,
                                  resumed=True) != binding:
                    raise LeaseRecoveryUnavailable('RECOVERY_CHANGED')
                now = self.clock()
                if type(now) not in (int, float) or not math.isfinite(now) or now >= evidence.valid_until_monotonic:
                    raise LeaseRecoveryUnavailable('PROOF_EXPIRED')
                if self.source.recheck(evidence) is not True:
                    raise LeaseRecoveryUnavailable('SOURCE_CHANGED')
                self.control._owned()
                if self._registration(event)['registration_id'] != binding.registration:
                    raise LeaseRecoveryUnavailable('REGISTRATION_CHANGED')
                now = self.clock()
                if type(now) not in (int, float) or not math.isfinite(now) or now >= evidence.valid_until_monotonic:
                    raise LeaseRecoveryUnavailable('PROOF_EXPIRED')
                if recheck is not None:
                    recheck()
                return True
            operation = operation_id or 'lease-resume:' + hashlib.sha256(json.dumps(
                [binding.journal_id, binding.cause_id, binding.cause_version], separators=(',', ':')).encode()).hexdigest()
            return self.control.resume(cause_id, expected_version=binding.cause_version,
                                       operation_id=operation, before_commit_db=before_commit_db)
