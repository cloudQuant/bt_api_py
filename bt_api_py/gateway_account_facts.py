"""Authorized account fact observations on a separate durable cursor.

The BOOTSTRAP phase reports current state at stream creation, not history.  A
fact projection redacts native identifiers and the provider's private source.
It is not itself proof that the provider observation is complete.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from bt_api_base.gateway_execution_stream_protocol import project_gateway_execution_observation
from bt_api_base.service_protocol import WireRole
from bt_api_gateway.account_fact_consumer_ownership import AccountFactConsumerOwner
from bt_api_gateway.direct_command_journal import DirectCommandJournalError
from bt_api_gateway.direct_execution_journal import GatewayDirectExecutionLedger
from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayOwnerServiceCallError
from bt_api_py.service_registry import ServiceCallContext, ServiceDispatchAdmission, ServiceResultTooLargeError

ACCOUNT_FACTS_OPERATIONS = frozenset({
    'account.facts.read_after', 'account.facts.progress', 'account.facts.ack', 'account.facts.status',
    'account.facts.snapshot_info', 'account.facts.snapshot_read', 'account.facts.snapshot_confirm',
})


def _epoch(value):
    return type(value) is str and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def _sequence(value):
    return type(value) is int and 0 <= value < 2**63


def account_facts_consumer_ref(*, principal_ref, identity, scope):
    """Derive a stable fact cursor only from the authenticated caller."""
    return 'account-facts:' + hashlib.sha256(json.dumps([
        principal_ref, identity.role.value, identity.actor_ref, scope.execution_domain_ref,
        scope.account_ref, scope.strategy_ref,
    ], separators=(',', ':'), ensure_ascii=True).encode('ascii')).hexdigest()


class GatewayAccountFactsObserver:
    """Read-only fact observations via the existing account owner and journal."""

    def __init__(self, owner, journal, ledger, *, account_scope, execution_domain_ref, principals, status_source=None):
        if (type(owner) is not GatewayDirectOwner or not owner.is_bound_to(journal)
                or type(ledger) is not GatewayDirectExecutionLedger or not ledger.is_bound_to(journal)
                or journal._schema_version < 18 or journal.account_scope != account_scope
                or type(execution_domain_ref) is not str or not execution_domain_ref
                or type(principals) is not frozenset or not principals
                or any(type(p) is not str or not p or p != p.strip() for p in principals)):
            raise ValueError('INVALID_ACCOUNT_FACTS_OBSERVER')
        if status_source is not None:
            from bt_api_py.ctp_gateway_return_maintenance import CtpGatewayReturnMaintenance
            if type(status_source) is not CtpGatewayReturnMaintenance or status_source._ledger is not ledger:
                raise ValueError('INVALID_ACCOUNT_FACTS_STATUS_SOURCE')
        self.status_source = status_source
        self.owner, self.journal, self.ledger = owner, journal, ledger
        self.account_scope, self.execution_domain_ref, self.principals = account_scope, execution_domain_ref, principals

    def handle(self, operation, context, arguments):
        def reject(code):
            return {'status': 'REQUEST_REJECTED', 'code': code}

        if type(context) is not ServiceCallContext or not isinstance(arguments, Mapping):
            return reject('INVALID_REQUEST')
        identity, scope = context.identity, context.scope
        if (operation not in ACCOUNT_FACTS_OPERATIONS or context.principal_ref not in self.principals
                or identity.principal_ref != context.principal_ref or identity.delegate_ref is not None
                or identity.role not in {WireRole.STRATEGY_CLIENT, WireRole.SERVICE, WireRole.APPLICATION_CLIENT}
                or scope.execution_domain_ref != self.execution_domain_ref or scope.account_ref != self.account_scope
                or scope.account_refs or scope.portfolio_ref is not None or scope.manual_book_ref is not None
                or scope.group_id is not None or scope.leg_id is not None
                or (identity.role is WireRole.STRATEGY_CLIENT and
                    (scope.strategy_ref != identity.actor_ref or not scope.run_id))
                or (identity.role is not WireRole.STRATEGY_CLIENT and
                    (scope.strategy_ref is not None or scope.run_id is not None))):
            return reject('SCOPE_DENIED')
        fields = {'account.facts.read_after': {'after_sequence', 'expected_epoch', 'limit'},
                  'account.facts.progress': set(), 'account.facts.status': set(),
                  'account.facts.ack': {'sequence', 'expected_epoch'},
                  'account.facts.snapshot_info': set(),
                  'account.facts.snapshot_read': {'snapshot_id', 'after_ordinal', 'limit'},
                  'account.facts.snapshot_confirm': {'snapshot_id', 'snapshot_digest'}}[operation]
        if set(arguments) != fields:
            return reject('INVALID_REQUEST')
        if operation == 'account.facts.read_after':
            seq, epoch, limit = arguments['after_sequence'], arguments['expected_epoch'], arguments['limit']
            if (not _sequence(seq) or (epoch is not None and not _epoch(epoch)) or (seq > 0 and epoch is None)
                    or type(limit) is not int or not 1 <= limit <= 256):
                return reject('INVALID_REQUEST')
        if operation == 'account.facts.ack' and (not _sequence(arguments['sequence'])
                                                  or not _epoch(arguments['expected_epoch'])):
            return reject('INVALID_REQUEST')
        if operation == 'account.facts.snapshot_read' and (not _epoch(arguments['snapshot_id'])
                or not _sequence(arguments['after_ordinal']) or type(arguments['limit']) is not int
                or not 1 <= arguments['limit'] <= 256):
            return reject('INVALID_REQUEST')
        if operation == 'account.facts.snapshot_confirm' and (not _epoch(arguments['snapshot_id'])
                or not _epoch(arguments['snapshot_digest'])):
            return reject('INVALID_REQUEST')
        admission = context.dispatch_admission
        if (type(admission) is not ServiceDispatchAdmission or not admission.matches_context(context)
                or not admission.matches_operation(operation) or not admission.matches(arguments)
                or not callable(context.result_fits)):
            return reject('SCOPE_DENIED')
        consumer = account_facts_consumer_ref(principal_ref=context.principal_ref, identity=identity, scope=scope)
        service_epoch = admission.service_epoch
        if service_epoch is None:
            return reject('SCOPE_DENIED')
        owner = AccountFactConsumerOwner(context.registration_id, service_epoch, scope.run_id or '')

        def progress_value(progress):
            return {'schema_version': 1, 'account_scope': self.account_scope, 'consumer_ref': consumer,
                    'epoch': progress.epoch, 'offered_sequence': progress.offered_sequence,
                    'acknowledged_sequence': progress.acknowledged_sequence,
                    'bootstrap_count': progress.bootstrap_count}

        def event_value(event):
            return {'sequence': event.sequence, 'fact_digest': event.fact_digest,
                    'observation': project_gateway_execution_observation(event.fact).to_mapping(),
                    'disposition': event.disposition, 'reason': event.reason,
                    'strategy_scope': event.strategy_scope, 'execution_sequence': event.execution_sequence,
                    'reservation_id': event.reservation_id, 'origin': event.origin,
                    'parent_ref': event.parent_ref, 'child_id': event.child_id,
                    'external_receipt_digest': event.external_receipt_digest,
                    'phase': event.phase, 'event_digest': event.event_digest}

        def page_value(page):
            return {'schema_version': 1, 'account_scope': self.account_scope, 'consumer_ref': consumer,
                    'epoch': page.epoch, 'offered_sequence': page.offered_sequence,
                    'acknowledged_sequence': page.acknowledged_sequence,
                    'bootstrap_count': page.bootstrap_count, 'has_more': page.has_more,
                    'events': [event_value(event) for event in page.events]}

        def metadata_value(meta):
            if meta is None:
                return None
            return {'schema_version': 1, 'account_scope': self.account_scope, 'consumer_ref': consumer,
                    'epoch': meta.epoch, 'high_sequence': meta.high_sequence, 'retained_floor': meta.retained_floor,
                    'snapshot_id': meta.snapshot_id, 'snapshot_digest': meta.snapshot_digest,
                    'snapshot_count': meta.snapshot_count}

        def snapshot_value(page):
            return {'schema_version': 1, 'account_scope': self.account_scope, 'consumer_ref': consumer,
                    'epoch': page.epoch, 'snapshot_id': page.snapshot_id, 'snapshot_digest': page.snapshot_digest,
                    'watermark': page.watermark, 'total': page.total, 'after_ordinal': page.after_ordinal,
                    'events': [event_value(event) for event in page.events], 'has_more': page.has_more}

        def execute():
            kwargs = dict(principal_id=context.principal_ref, consumer_ref=consumer,
                          admission_fence=admission.scope, consumer_owner=owner)
            try:
                if operation == 'account.facts.status':
                    with admission.scope() as check:
                        check()
                        try:
                            status = ({'status': 'UNAVAILABLE', 'unattributed': None, 'conflict': None,
                                       'verified_external': None} if self.status_source is None
                                      else self.status_source.fact_status())
                        except Exception:
                            status = {'status': 'UNAVAILABLE', 'unattributed': None, 'conflict': None,
                                      'verified_external': None}
                        keys = {'status', 'unattributed', 'conflict', 'verified_external'}
                        if (type(status) is not dict or set(status) != keys
                                or status['status'] not in {'UNAVAILABLE', 'CONFLICT', 'INCOMPLETE',
                                                            'NO_UNRESOLVED_OBSERVED'}
                                or (status['status'] == 'UNAVAILABLE' and any(status[k] is not None
                                    for k in keys - {'status'}))
                                or (status['status'] != 'UNAVAILABLE' and any(
                                    type(status[k]) is not int or status[k] < 0 for k in keys - {'status'}))
                                or (status['status'] == 'CONFLICT' and not status['conflict'])
                                or (status['status'] == 'INCOMPLETE' and
                                    (status['conflict'] or not status['unattributed']))
                                or (status['status'] == 'NO_UNRESOLVED_OBSERVED' and
                                    (status['conflict'] or status['unattributed']))):
                            status = {'status': 'UNAVAILABLE', 'unattributed': None, 'conflict': None,
                                      'verified_external': None}
                        result = {'status': 'OK', 'fact_status': status}
                        if not context.result_fits(result):
                            raise ServiceResultTooLargeError()
                        check()
                        return result
                if operation == 'account.facts.snapshot_info':
                    meta = self.ledger.account_fact_snapshot_info(**kwargs)
                    result = {'status': 'OK', 'snapshot_info': metadata_value(meta)}
                    if not context.result_fits(result):
                        raise ServiceResultTooLargeError()
                    return result
                if operation == 'account.facts.snapshot_read':
                    page = self.ledger.read_account_fact_snapshot(**kwargs, snapshot_id=arguments['snapshot_id'],
                        after_ordinal=arguments['after_ordinal'], limit=arguments['limit'],
                        result_fits=lambda p: context.result_fits({'status': 'OK', 'snapshot': snapshot_value(p)}))
                    return {'status': 'OK', 'snapshot': snapshot_value(page)}
                if operation == 'account.facts.snapshot_confirm':
                    # Confirm is idempotent for the same snapshot/owner. A lost
                    # response is UNKNOWN and retried with the original ID.
                    meta = self.ledger.confirm_account_fact_snapshot(**kwargs, snapshot_id=arguments['snapshot_id'],
                        snapshot_digest=arguments['snapshot_digest'])
                    result = {'status': 'OK', 'snapshot_info': metadata_value(meta)}
                    if not context.result_fits(result):
                        raise ServiceResultTooLargeError()
                    return result
                if operation == 'account.facts.read_after':
                    page = self.ledger.read_account_facts(**kwargs, after_sequence=seq, expected_epoch=epoch,
                        limit=limit, result_fits=lambda p: context.result_fits({'status': 'OK', 'page': page_value(p)}))
                    return {'status': 'OK', 'page': page_value(page)}
                if operation == 'account.facts.ack':
                    progress = self.ledger.acknowledge_account_facts(**kwargs, sequence=arguments['sequence'],
                        expected_epoch=arguments['expected_epoch'], result_fits=lambda p: context.result_fits(
                            {'status': 'OK', 'progress': progress_value(p)}))
                else:
                    progress = self.ledger.account_fact_progress(**kwargs)
                    if not context.result_fits({'status': 'OK', 'progress': progress_value(progress)}):
                        raise ServiceResultTooLargeError()
                return {'status': 'OK', 'progress': progress_value(progress)}
            except DirectCommandJournalError as error:
                if error.code == 'PAGE_TOO_LARGE':
                    raise ServiceResultTooLargeError() from None
                if error.code in {'INVALID_SEQUENCE', 'EPOCH_MISMATCH', 'CONSUMER_CAPACITY_EXCEEDED',
                                  'ACCOUNT_FACT_STREAM_UNAVAILABLE', 'CONSUMER_REPLAY_REQUIRED',
                                  'RECOVERY_REQUIRED', 'SNAPSHOT_REPLACED', 'SNAPSHOT_NOT_OFFERED'}:
                    return reject(error.code)
                raise
        try:
            return self.owner.submit_service_call(execute).result(timeout_seconds=1.0)
        except GatewayOwnerServiceCallError:
            return {'status': 'UNKNOWN'}
        finally:
            admission.close()
