"""Authenticated access to the independent, incomplete cancel-attempt stream."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from contextlib import ExitStack

from bt_api_base.service_protocol import WireRole
from bt_api_gateway import account_cancel_attempt_stream as stream
from bt_api_gateway.direct_command_journal import DirectCommandJournalError
from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayOwnerServiceCallError
from bt_api_py.service_registry import ServiceCallContext, ServiceDispatchAdmission, ServiceResultTooLargeError

OPERATIONS = frozenset({'account.cancel_attempts.read_after', 'account.cancel_attempts.progress',
                        'account.cancel_attempts.ack'})


def _ref(parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=True, separators=(',', ':')).encode('ascii')).hexdigest()


class GatewayAccountCancelAttempts:
    def __init__(self, owner, journal, *, account_scope, execution_domain_ref, principals):
        if (type(owner) is not GatewayDirectOwner or not owner.is_bound_to(journal)
                or journal.account_scope != account_scope
                or not getattr(journal, '_cancel_attempt_stream_enabled', False)
                or type(execution_domain_ref) is not str or not execution_domain_ref
                or type(principals) is not frozenset or not principals
                or any(type(p) is not str or not p or p != p.strip() for p in principals)):
            raise ValueError('INVALID_CANCEL_STREAM_BINDING')
        self.owner, self.journal = owner, journal
        self.account_scope, self.execution_domain_ref, self.principals = account_scope, execution_domain_ref, principals

    def handle(self, operation, context, arguments):
        reject = lambda code: {'status': 'REQUEST_REJECTED', 'code': code}
        if type(context) is not ServiceCallContext or not isinstance(arguments, Mapping):
            return reject('INVALID_REQUEST')
        identity, scope = context.identity, context.scope
        if (operation not in OPERATIONS or context.principal_ref not in self.principals
                or identity.principal_ref != context.principal_ref or identity.delegate_ref is not None
                or identity.role not in {WireRole.SERVICE, WireRole.APPLICATION_CLIENT}
                or scope.execution_domain_ref != self.execution_domain_ref or scope.account_ref != self.account_scope
                or scope.account_refs or scope.portfolio_ref is not None or scope.manual_book_ref is not None
                or scope.group_id is not None or scope.leg_id is not None
                or scope.strategy_ref is not None or scope.run_id is not None):
            return reject('SCOPE_DENIED')
        fields = {'account.cancel_attempts.read_after': {'after_sequence', 'expected_epoch', 'limit'},
                  'account.cancel_attempts.progress': set(),
                  'account.cancel_attempts.ack': {'sequence', 'expected_epoch'}}[operation]
        if set(arguments) != fields:
            return reject('INVALID_REQUEST')
        admission = context.dispatch_admission
        if (type(admission) is not ServiceDispatchAdmission or not admission.matches_context(context)
                or not admission.matches_operation(operation) or not admission.matches(arguments)
                or not callable(context.result_fits) or admission.service_epoch is None):
            return reject('SCOPE_DENIED')
        consumer = 'cancel-attempts:' + _ref([context.principal_ref, identity.role.value, identity.actor_ref,
                                               scope.execution_domain_ref, scope.account_ref])
        owner = _ref([context.registration_id, admission.service_epoch])

        def execute():
            try:
                with ExitStack() as stack:
                    manager = admission.scope()
                    check = type(manager).__enter__(manager)
                    exit_ = type(manager).__exit__
                    stack.push(lambda *exc: (exit_(manager, *exc), False)[1])
                    if not callable(check):
                        raise DirectCommandJournalError('INVALID_ADMISSION')
                    db = stack.enter_context(self.journal._transaction())
                    check()
                    common = (db, self.account_scope, context.principal_ref, consumer, owner)
                    qualified = getattr(self.journal, '_cancel_attempt_stream_qualified', False) is True
                    if operation.endswith('.read_after'):
                        page = stream.read_after(*common, **arguments,
                            result_fits=lambda p: context.result_fits({'status': 'OK', 'page': p}),
                            **({'qualified': True} if qualified else {}))
                        result = {'status': 'OK', 'page': page}
                        self.journal._bump(db)
                    elif operation.endswith('.ack'):
                        progress = stream.acknowledge(*common, **arguments,
                            result_fits=lambda p: context.result_fits({'status': 'OK', 'progress': p}),
                            **({'qualified': True} if qualified else {}))
                        result = {'status': 'OK', 'progress': progress}
                        self.journal._bump(db)
                    else:
                        progress = stream.progress(*common, **({'qualified': True} if qualified else {}))
                        result = {'status': 'OK', 'progress': progress}
                        fits = context.result_fits(result)
                        if type(fits) is not bool:
                            raise DirectCommandJournalError('INVALID_INPUT')
                        if not fits:
                            raise ServiceResultTooLargeError()
                    check()
                    return result
            except DirectCommandJournalError as error:
                if error.code == 'PAGE_TOO_LARGE':
                    raise ServiceResultTooLargeError() from None
                if error.code in {'INVALID_INPUT', 'INVALID_SEQUENCE', 'EPOCH_MISMATCH',
                                  'CONSUMER_CAPACITY_EXCEEDED', 'CONSUMER_REPLAY_REQUIRED'}:
                    return reject(error.code)
                raise
        try:
            return self.owner.submit_service_call(execute).result(timeout_seconds=1.0)
        except GatewayOwnerServiceCallError:
            return {'status': 'UNKNOWN'}
        finally:
            admission.close()
