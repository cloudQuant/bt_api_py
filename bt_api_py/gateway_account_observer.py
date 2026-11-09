"""Explicitly authorized account event observation on an independent cursor."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from bt_api_base.gateway_execution_stream_protocol import (
    GatewayExecutionStreamEvent, project_gateway_execution_observation,
)
from bt_api_base.service_protocol import WireRole
from bt_api_gateway.direct_command_journal import DirectCommandJournalError
from bt_api_gateway.direct_execution_journal import GatewayDirectExecutionLedger
from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayOwnerServiceCallError
from bt_api_py.service_registry import ServiceCallContext, ServiceDispatchAdmission, ServiceResultTooLargeError

ACCOUNT_OBSERVER_OPERATIONS = frozenset({
    'account.events.read_after', 'account.events.progress', 'account.events.ack',
})


def _epoch(value):
    return type(value) is str and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def account_observer_consumer_ref(*, principal_ref, identity, scope):
    """Stable observer identity derived exclusively from an authenticated binding."""
    return 'account-observer:' + hashlib.sha256(json.dumps([
        principal_ref, identity.role.value, identity.actor_ref,
        scope.execution_domain_ref, scope.account_ref, scope.strategy_ref,
    ], separators=(',', ':'), ensure_ascii=True).encode('ascii')).hexdigest()


class GatewayAccountObserver:
    def __init__(self, owner, journal, ledger, *, account_scope, execution_domain_ref, principals):
        if (type(owner) is not GatewayDirectOwner or not owner.is_bound_to(journal)
                or type(ledger) is not GatewayDirectExecutionLedger or not ledger.is_bound_to(journal)
                or journal._schema_version < 12 or journal.account_scope != account_scope
                or type(execution_domain_ref) is not str or not execution_domain_ref
                or type(principals) is not frozenset or not principals
                or any(type(p) is not str or not p or p != p.strip() for p in principals)):
            raise ValueError('INVALID_ACCOUNT_OBSERVER')
        self.owner, self.journal, self.ledger = owner, journal, ledger
        self.account_scope, self.execution_domain_ref = account_scope, execution_domain_ref
        self.principals = principals

    def handle(self, operation, context, arguments):
        reject = lambda code: {'status': 'REQUEST_REJECTED', 'code': code}
        if type(context) is not ServiceCallContext or not isinstance(arguments, Mapping):
            return reject('INVALID_REQUEST')
        identity, scope = context.identity, context.scope
        if (operation not in ACCOUNT_OBSERVER_OPERATIONS or context.principal_ref not in self.principals
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
        fields = {'account.events.read_after': {'after_sequence', 'expected_epoch', 'limit'},
                  'account.events.progress': set(),
                  'account.events.ack': {'sequence', 'expected_epoch'}}[operation]
        if set(arguments) != fields:
            return reject('INVALID_REQUEST')
        if operation == 'account.events.read_after':
            seq, epoch, limit = arguments['after_sequence'], arguments['expected_epoch'], arguments['limit']
            if (type(seq) is not int or not 0 <= seq < 2**63 or
                    (epoch is not None and not _epoch(epoch)) or (seq > 0 and epoch is None)
                    or type(limit) is not int or not 1 <= limit <= 256):
                return reject('INVALID_REQUEST')
        if operation == 'account.events.ack' and (type(arguments['sequence']) is not int
                or not 0 <= arguments['sequence'] < 2**63 or not _epoch(arguments['expected_epoch'])):
            return reject('INVALID_REQUEST')
        admission = context.dispatch_admission
        if (type(admission) is not ServiceDispatchAdmission or not admission.matches_context(context)
                or not admission.matches_operation(operation) or not admission.matches(arguments)
                or not callable(context.result_fits)):
            return reject('SCOPE_DENIED')
        if operation == 'account.events.ack' and self.journal._schema_version < 19:
            # The legacy cursor does not persist its registration owner.  An
            # offered sequence could therefore belong to an earlier session.
            # Keep observation available, but never advance that cursor until
            # a durable handoff binds the offer to the authenticated owner.
            admission.close()
            return reject('ACCOUNT_OBSERVER_HANDOFF_UNAVAILABLE')
        consumer = account_observer_consumer_ref(principal_ref=context.principal_ref, identity=identity, scope=scope)
        consumer_owner = None
        if self.journal._schema_version >= 19:
            from bt_api_gateway.account_event_consumer_ownership import AccountEventConsumerOwner
            try:
                consumer_owner = AccountEventConsumerOwner(
                    context.registration_id, admission.service_epoch, scope.run_id or '')
            except (DirectCommandJournalError, ValueError, TypeError):
                admission.close()
                return reject('SCOPE_DENIED')

        def progress_value(progress):
            return {'schema_version': 1, 'account_scope': self.account_scope, 'consumer_ref': consumer,
                    'epoch': progress.epoch, 'offered_sequence': progress.offered_sequence,
                    'acknowledged_sequence': progress.acknowledged_sequence}

        def page_value(page):
            events = []
            for event in page.events:
                projected = GatewayExecutionStreamEvent(
                    event.sequence, event.request_key, event.reservation_id, event.command_digest,
                    event.command_principal, event.fact_digest,
                    project_gateway_execution_observation(event.fact, trade_metadata_only=event.trade_metadata_only,
                                                          trade_attribution=event.trade_attribution),
                    cancel_attribution=event.cancel_attribution, place_attribution=event.place_attribution,
                ).to_mapping()
                projected['strategy_scope'] = event.strategy_scope
                events.append(projected)
            return {'schema_version': 1, 'account_scope': self.account_scope, 'consumer_ref': consumer,
                    'epoch': page.epoch, 'offered_sequence': page.offered_sequence,
                    'acknowledged_sequence': page.acknowledged_sequence, 'has_more': page.has_more,
                    'events': events}

        def execute():
            kwargs = dict(principal_id=context.principal_ref, consumer_ref=consumer, admission_fence=admission.scope)
            if consumer_owner is not None:
                kwargs['consumer_owner'] = consumer_owner
            try:
                if operation == 'account.events.read_after':
                    page = self.ledger.read_account_observer(**kwargs, after_sequence=seq, expected_epoch=epoch,
                        limit=limit, result_fits=lambda p: context.result_fits({'status': 'OK', 'page': page_value(p)}))
                    return {'status': 'OK', 'page': page_value(page)}
                if operation == 'account.events.ack':
                    progress = self.ledger.acknowledge_account_observer(**kwargs, sequence=arguments['sequence'],
                        expected_epoch=arguments['expected_epoch'], result_fits=lambda p: context.result_fits(
                            {'status': 'OK', 'progress': progress_value(p)}))
                else:
                    progress = self.ledger.account_observer_progress(**kwargs)
                    if not context.result_fits({'status': 'OK', 'progress': progress_value(progress)}):
                        raise ServiceResultTooLargeError()
                return {'status': 'OK', 'progress': progress_value(progress)}
            except DirectCommandJournalError as error:
                if error.code == 'PAGE_TOO_LARGE':
                    raise ServiceResultTooLargeError() from None
                if error.code in {'INVALID_SEQUENCE', 'EPOCH_MISMATCH', 'CONSUMER_CAPACITY_EXCEEDED', 'CONSUMER_REPLAY_REQUIRED'}:
                    return reject(error.code)
                raise
        try:
            return self.owner.submit_service_call(execute).result(timeout_seconds=1.0)
        except GatewayOwnerServiceCallError:
            return {'status': 'UNKNOWN'}
        finally:
            admission.close()
