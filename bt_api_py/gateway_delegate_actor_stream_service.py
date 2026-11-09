"""Authenticated SERVICE actor access to its independent execution stream."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from bt_api_base.gateway_execution_stream_protocol import GatewayExecutionStreamEvent, project_gateway_execution_observation
from bt_api_base.service_protocol import WireRole
from bt_api_gateway.delegate_actor_stream import GatewayDelegateActorStream
from bt_api_gateway.direct_command_journal import DirectCommandJournalError
from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayOwnerServiceCallError
from bt_api_py.gateway_delegation_service import GatewayDelegationSettings
from bt_api_py.service_registry import ServiceCallContext, ServiceDispatchAdmission, ServiceResultTooLargeError

ACTOR_STREAM_OPERATIONS = frozenset({
    'direct.delegate_events.read_after', 'direct.delegate_events.progress', 'direct.delegate_events.ack',
})


def actor_stream_consumer(identity, scope, grant):
    raw = [identity.principal_ref, identity.role.value, identity.actor_ref, scope.execution_domain_ref,
           scope.account_ref, scope.strategy_ref, scope.run_id, grant.revision]
    return 'delegate-actor:' + hashlib.sha256(json.dumps(raw, separators=(',', ':'), ensure_ascii=True).encode('ascii')).hexdigest()


def _seq(value):
    return type(value) is int and 0 <= value < 2**63


def _epoch(value):
    return type(value) is str and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


class GatewayDelegateActorStreamService:
    def __init__(self, owner, journal, ledger, settings):
        if (type(owner) is not GatewayDirectOwner or not owner.is_bound_to(journal)
                or type(settings) is not GatewayDelegationSettings
                or any(g.account_scope != journal.account_scope for g in settings.grants)):
            raise ValueError('INVALID_ACTOR_STREAM')
        self.owner, self.journal, self.settings = owner, journal, settings
        self.stream = GatewayDelegateActorStream(ledger, journal)

    def handle(self, operation, context, arguments):
        denied = {'status': 'REQUEST_REJECTED', 'code': 'SCOPE_DENIED'}
        if (operation not in ACTOR_STREAM_OPERATIONS or type(context) is not ServiceCallContext
                or context.identity.role is not WireRole.SERVICE or not isinstance(arguments, Mapping)):
            return denied
        admission, identity, scope = context.dispatch_admission, context.identity, context.scope
        if (type(admission) is not ServiceDispatchAdmission or not admission.matches_context(context)
                or not admission.matches_operation(operation) or not admission.matches(arguments)
                or not callable(context.result_fits)):
            return denied
        try:
            grant = next(g for g in self.settings.grants if g.identity == identity
                         and g.account_scope == scope.account_ref and g.strategy_scope == scope.strategy_ref
                         and g.execution_domain_ref == scope.execution_domain_ref)
            if (identity.principal_ref != context.principal_ref or identity.delegate_ref is not None
                    or scope.account_ref != self.journal.account_scope or not scope.run_id
                    or scope.account_refs or scope.manual_book_ref is not None or scope.portfolio_ref is not None
                    or scope.group_id is not None or scope.leg_id is not None
                    or 'direct.command_status' not in grant.operations or not admission.matches_delegate_grant(grant)):
                return denied
            fields = {'direct.delegate_events.progress': set(),
                      'direct.delegate_events.read_after': {'after_sequence', 'expected_epoch', 'limit'},
                      'direct.delegate_events.ack': {'sequence', 'expected_epoch'}}[operation]
            if set(arguments) != fields:
                return {'status': 'REQUEST_REJECTED', 'code': 'INVALID_REQUEST'}
            if operation == 'direct.delegate_events.read_after':
                seq, epoch, limit = arguments['after_sequence'], arguments['expected_epoch'], arguments['limit']
                if (not _seq(seq) or (epoch is not None and not _epoch(epoch)) or (seq and epoch is None)
                        or type(limit) is not int or not 1 <= limit <= 256):
                    return {'status': 'REQUEST_REJECTED', 'code': 'INVALID_REQUEST'}
            if operation == 'direct.delegate_events.ack' and (not _seq(arguments['sequence'])
                                                               or not _epoch(arguments['expected_epoch'])):
                return {'status': 'REQUEST_REJECTED', 'code': 'INVALID_REQUEST'}
            consumer = actor_stream_consumer(identity, scope, grant)
            def progress_value(progress):
                return {'schema_version': 1, 'account_scope': scope.account_ref, 'strategy_scope': scope.strategy_ref,
                        'actor_principal': identity.principal_ref, 'consumer_ref': consumer, 'epoch': progress.epoch,
                        'offered_sequence': progress.offered_sequence, 'acknowledged_sequence': progress.acknowledged_sequence}
            def page_value(page):
                result = progress_value(page.progress)
                result['has_more'] = page.has_more
                result['events'] = []
                for item in page.events:
                    event, command = item.event, item.command
                    projected = GatewayExecutionStreamEvent(event.sequence, event.request_key, event.reservation_id,
                        event.command_digest, event.command_principal, event.fact_digest,
                        project_gateway_execution_observation(event.fact, trade_metadata_only=event.trade_metadata_only,
                                                              trade_attribution=event.trade_attribution),
                        cancel_attribution=event.cancel_attribution, place_attribution=event.place_attribution).to_mapping()
                    projected.update(origin=command.origin, on_behalf_of=command.strategy_scope,
                                     parent_ref=command.parent_ref, child_id=command.child_id)
                    result['events'].append(projected)
                return result
            def execute():
                kwargs = dict(principal=identity.principal_ref, consumer=consumer, strategy=scope.strategy_ref,
                              actor_ref=identity.actor_ref, run_id=scope.run_id, revision=grant.revision,
                              admission_fence=admission.scope)
                try:
                    if operation == 'direct.delegate_events.read_after':
                        def check_command(command):
                            return (admission.matches_delegate_grant(grant) and command.actor_kind == grant.actor_kind
                                    and command.execution_domain_ref == scope.execution_domain_ref
                                    and command.instrument in grant.instruments
                                    and self.settings.instrument_recheck(command.instrument) is True)
                        page = self.stream.read(**kwargs, after_sequence=seq, expected_epoch=epoch, limit=limit,
                            authorize_command=check_command,
                            result_fits=lambda p: context.result_fits({'status': 'OK', 'page': page_value(p)}))
                        return {'status': 'OK', 'page': page_value(page)}
                    if operation == 'direct.delegate_events.ack':
                        progress = self.stream.acknowledge(**kwargs, sequence=arguments['sequence'],
                            expected_epoch=arguments['expected_epoch'],
                            result_fits=lambda p: context.result_fits({'status': 'OK', 'progress': progress_value(p)}))
                    else:
                        progress = self.stream.progress(**kwargs)
                        if not context.result_fits({'status': 'OK', 'progress': progress_value(progress)}):
                            raise ServiceResultTooLargeError()
                    return {'status': 'OK', 'progress': progress_value(progress)}
                except DirectCommandJournalError as error:
                    if error.code == 'PAGE_TOO_LARGE':
                        raise ServiceResultTooLargeError() from None
                    if error.code in {'INVALID_SEQUENCE', 'EPOCH_MISMATCH', 'CONSUMER_CAPACITY_EXCEEDED',
                                      'ACTOR_STREAM_GAP', 'ACTOR_STREAM_UNAVAILABLE'}:
                        return {'status': 'REQUEST_REJECTED', 'code': error.code}
                    raise
            return self.owner.submit_service_call(execute).result(timeout_seconds=1.0)
        except GatewayOwnerServiceCallError:
            return {'status': 'UNKNOWN'}
        except StopIteration:
            return denied
        finally:
            admission.close()
