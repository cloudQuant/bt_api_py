"""Current SERVICE actor's read/cancel of its own delegated Gateway command."""
from __future__ import annotations

import json
import hashlib
import threading
from collections.abc import Mapping
from contextlib import contextmanager, nullcontext

from bt_api_base.direct_write_protocol import NativeOrderIdentity
from bt_api_base.gateway_delegation_protocol import GatewayDelegatedCommand
from bt_api_base.service_protocol import WireRole
from bt_api_gateway.direct_cancel_journal import GatewayDirectCancelJournal
from bt_api_gateway.direct_command_journal import DirectCommandJournalError
from bt_api_gateway.direct_delegation import GatewayDelegationDenied, authorize_delegated_command
from bt_api_py.gateway_delegation_service import GatewayDelegationService
from bt_api_py.gateway_direct_owner import GatewayCancelCommand
from bt_api_py.gateway_direct_service import _ensure_reply_fits, _identity_public
from bt_api_py.service_registry import ServiceCallContext, ServiceDispatchAdmission

OPERATIONS = frozenset({'direct.delegate_actor_status', 'direct.delegate_actor_cancel', 'direct.delegate_actor_cancel_status',
                        'direct.delegate_actor_release', 'direct.delegate_actor_release_status'})
_LOGICAL = {'direct.delegate_actor_status': 'direct.command_status',
            'direct.delegate_actor_cancel': 'direct.cancel',
            'direct.delegate_actor_cancel_status': 'direct.cancel_status', 'direct.delegate_actor_release': 'direct.release',
            'direct.delegate_actor_release_status': 'direct.command_status'}


class GatewayDelegateActorService:
    """Only handles a separately admitted actor and exact persisted envelope."""
    def __init__(self, delegation: GatewayDelegationService, *, release_target_recheck=None,
                 parent_cancel_recheck=None, parent_grant_resolver=None):
        if type(delegation) is not GatewayDelegationService or not delegation.owner.supports_cancel:
            raise ValueError('INVALID_DELEGATE_ACTOR_SERVICE')
        if any(value is not None and not callable(value) for value in
               (release_target_recheck, parent_cancel_recheck, parent_grant_resolver)):
            raise ValueError('INVALID_DELEGATE_ACTOR_SERVICE')
        self._delegation = delegation
        self.owner, self.journal = delegation.owner, delegation.journal
        self.instance_id, self.scope_ref = delegation.instance_id, delegation.scope_ref
        self._cancel = GatewayDirectCancelJournal(self.journal)
        self._release_target_recheck = release_target_recheck
        self._parent_cancel_recheck = parent_cancel_recheck
        self._parent_grant_resolver = parent_grant_resolver

    def handle(self, operation, context, arguments):
        denied = {'status': 'REQUEST_REJECTED', 'code': 'SCOPE_DENIED'}
        if (operation not in OPERATIONS or type(context) is not ServiceCallContext
                or context.identity.role is not WireRole.SERVICE or not isinstance(arguments, Mapping)):
            return denied
        admission = context.dispatch_admission
        if (type(admission) is not ServiceDispatchAdmission or not admission.matches_context(context)
                or not admission.matches_operation(operation) or not admission.matches(arguments)):
            return denied
        expected = {'command', 'payload'}
        if operation in {'direct.delegate_actor_cancel', 'direct.delegate_actor_cancel_status', 'direct.delegate_actor_release', 'direct.delegate_actor_release_status'}:
            expected.add('operation_id')
        if operation in {'direct.delegate_actor_cancel', 'direct.delegate_actor_release'}:
            expected.add('reservation_id')
        if operation == 'direct.delegate_actor_release':
            expected.add('expected_version')
        transferred = False
        active = threading.local()
        try:
            if set(arguments) != expected or not isinstance(arguments.get('payload'), Mapping):
                return {'status': 'REQUEST_REJECTED', 'code': 'INVALID_REQUEST'}
            command = GatewayDelegatedCommand.from_mapping(arguments['command'])
            payload = dict(arguments['payload'])
            if any(type(arguments[name]) is not str or not arguments[name] or arguments[name] != arguments[name].strip()
                   or len(arguments[name]) > 256 for name in expected & {'operation_id', 'reservation_id'}):
                return {'status': 'REQUEST_REJECTED', 'code': 'INVALID_REQUEST'}
            if operation == 'direct.delegate_actor_release' and (type(arguments['expected_version']) is not int
                    or arguments['expected_version'] < 1):
                return {'status': 'REQUEST_REJECTED', 'code': 'INVALID_REQUEST'}
            settings = self._delegation.settings
            grant = next(g for g in settings.grants if g.identity == context.identity
                and g.account_scope == context.scope.account_ref and g.strategy_scope == context.scope.strategy_ref)
            if not admission.matches_delegate_grant(grant):
                return denied
            parent_grant = None
            parent_operation = ('direct.parent.query' if operation == 'direct.delegate_actor_release_status'
                                else 'direct.parent.cancel')
            if command.parent_ref is not None and operation in {'direct.delegate_actor_cancel', 'direct.delegate_actor_release', 'direct.delegate_actor_release_status'}:
                if self._parent_grant_resolver is None:
                    return denied
                parent_grant = self._parent_grant_resolver(context, parent_operation)
                if parent_grant is None or not admission.matches_parent_grant(parent_grant):
                    return denied
            def registry_recheck(_):
                check = getattr(active, 'check', None)
                if check is None:
                    with admission.scope() as check:
                        check()
                else:
                    check()
                return (admission.matches_delegate_grant(grant) and (parent_grant is None
                    or (self._parent_grant_resolver(context, parent_operation) == parent_grant
                        and admission.matches_parent_grant(parent_grant))))
            def authorize():
                return authorize_delegated_command(context, grant, command, _LOGICAL[operation], payload,
                    registry_recheck=registry_recheck, current_grant=lambda: grant,
                    instrument_recheck=settings.instrument_recheck, parent_recheck=settings.parent_recheck)
            @contextmanager
            def fence():
                with admission.scope() as check:
                    active.check = check
                    try:
                        yield check
                    finally:
                        active.check = None
            def read_target():
                # A refusal here is read-only: this request wrote nothing.
                try:
                    permit = authorize()
                    with fence():
                        row = self.journal.read_delegate_command(delegation_permit=permit, admission_fence=nullcontext)
                except GatewayDelegationDenied:
                    return None
                except DirectCommandJournalError as error:
                    # The key belongs to another principal, or this actor's key
                    # carries another envelope: never this actor's command.
                    if error.code in {'REQUEST_CONFLICT', 'DELEGATION_MISMATCH'}:
                        return None
                    raise
                return permit, row
            found = self.owner.submit_service_call(read_target).result(timeout_seconds=1.0)
            if found is None:
                # Read-only refusal: nothing was written by this request.
                return {'status': 'REQUEST_REJECTED', 'code': 'DELEGATION_DENIED'}
            permit, target = found
            release_expected = None
            if operation == 'direct.delegate_actor_release_status':
                def read_release():
                    with fence():
                        return self.journal.read_delegate_release(arguments['operation_id'],
                            delegation_permit=permit, admission_fence=nullcontext)
                target, release_expected = self.owner.submit_service_call(read_release).result(timeout_seconds=1.0)
            if operation == 'direct.delegate_actor_release':
                if target.reservation_id != arguments['reservation_id']:
                    return {'status': 'REQUEST_REJECTED', 'code': 'REQUEST_CONFLICT'}
                def release():
                    with fence():
                        return self.journal.release(command.strategy_scope, command.request_key, target.reservation_id,
                            command.payload_digest, command.actor_principal, arguments['operation_id'],
                            arguments['expected_version'], delegation_permit=permit, delegation_fence=nullcontext,
                            release_target_recheck=self._release_target_recheck)
                target = self.owner.submit_service_call(release).result(timeout_seconds=1.0)
            if operation in {'direct.delegate_actor_status', 'direct.delegate_actor_release', 'direct.delegate_actor_release_status'}:
                result = {'status': 'OBSERVED', 'schema_version': 1, 'account_scope': target.account_scope,
                    'strategy_scope': target.strategy_scope, 'actor_principal': target.principal,
                    'request_key': target.request_key, 'reservation_id': target.reservation_id, 'digest': target.digest,
                    'state': target.status, 'version': target.version, 'owner_generation': self.journal.generation,
                    'reservation_generation': target.generation,
                    'native_identity_digest': None if target.native_identity_json is None else
                        hashlib.sha256(target.native_identity_json.encode('ascii')).hexdigest()}
                if operation == 'direct.delegate_actor_release_status':
                    result.update(release_operation_id=arguments['operation_id'], release_expected_version=release_expected)
                return result
            def project(row):
                return {'account_scope': row.account_scope, 'strategy_scope': row.strategy_scope,
                    'requester_principal': row.principal, 'target_actor_principal': row.target_principal,
                    'request_key': row.request_key, 'operation_id': row.operation_id, 'attempt_id': row.attempt_id,
                    'target_reservation_id': row.target_reservation_id, 'target_digest': row.target_digest,
                    'state': row.status, 'version': row.version, 'owner_generation': self.journal.generation,
                    'attempt_generation': row.generation}
            if operation == 'direct.delegate_actor_cancel_status':
                def read_attempt():
                    with fence() as check:
                        permit.recheck()
                        row = self._cancel.get(command.strategy_scope, arguments['operation_id'], command.actor_principal)
                        if (row.target_principal != command.actor_principal or row.request_key != command.request_key
                                or row.target_reservation_id != target.reservation_id or row.target_digest != command.payload_digest):
                            raise ValueError('CANCEL_MISMATCH')
                        check()
                        permit.recheck()
                        return row
                row = self.owner.submit_service_call(read_attempt).result(timeout_seconds=1.0)
                return {'status': 'OK', 'record': project(row)}
            if target.reservation_id != arguments['reservation_id'] or target.status not in {'DISPATCHING', 'SENT', 'UNKNOWN'}:
                return {'status': 'REQUEST_REJECTED', 'code': 'CANCEL_UNAVAILABLE'}
            native, _ = _identity_public(target)
            if native is None:
                return {'status': 'REQUEST_REJECTED', 'code': 'CANCEL_UNAVAILABLE'}
            symbol = json.loads(target.payload_json)['symbol']
            def create_attempt():
                with fence():
                    permit.recheck()
                    return self._cancel.request(command.strategy_scope, arguments['operation_id'], command.actor_principal,
                        command.request_key, target.reservation_id, command.payload_digest,
                        target_identity=NativeOrderIdentity.from_dict(native), symbol=symbol, before_commit=permit.recheck,
                        parent_command=command if command.parent_ref is not None else None,
                        parent_cancel_recheck=self._parent_cancel_recheck if command.parent_ref is not None else None)
            attempt = self.owner.submit_service_call(create_attempt).result(timeout_seconds=1.0)
            response = {'status': 'OK', 'record': project(attempt)}
            if attempt.status != 'REQUESTED':
                return {**response, 'cancel_queue_state': 'OBSERVED'}
            for state in ('QUEUED', 'ALREADY_QUEUED'):
                _ensure_reply_fits(context, {**response, 'cancel_queue_state': state})
            @contextmanager
            def request_fence(current):
                with fence() as check:
                    permit.recheck()
                    if (current.principal != command.actor_principal or current.target_principal != command.actor_principal
                            or current.request_key != command.request_key or current.target_reservation_id != target.reservation_id
                            or current.target_digest != command.payload_digest or current.attempt_id != attempt.attempt_id):
                        raise ValueError('CANCEL_MISMATCH')
                    yield lambda: (check(), permit.recheck())
            queued = self.owner.enqueue_cancel(GatewayCancelCommand(command.strategy_scope, attempt.operation_id,
                command.actor_principal, attempt.version), request_fence=request_fence, finalize=admission.close,
                parent_command=command if command.parent_ref is not None else None,
                parent_cancel_recheck=self._parent_cancel_recheck if command.parent_ref is not None else None)
            transferred = queued == 'QUEUED'
            if queued in {'QUEUED', 'DUPLICATE'}:
                return {**response, 'cancel_queue_state': 'QUEUED' if queued == 'QUEUED' else 'ALREADY_QUEUED'}
            return {'status': 'REQUEST_REJECTED', 'code': 'CANCEL_UNAVAILABLE'}
        except Exception:
            return {'status': 'UNKNOWN'}
        finally:
            if not transferred:
                admission.close()
