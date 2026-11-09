"""Explicitly granted SERVICE delegation onto one Gateway account owner."""
from __future__ import annotations

import threading
from collections.abc import Mapping
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from typing import Callable

from bt_api_base.gateway_delegation_protocol import GatewayDelegatedCommand
from bt_api_base.service_protocol import WireRole
from bt_api_gateway.direct_delegation import GatewayDelegateGrant, authorize_delegated_command
from bt_api_gateway.direct_command_journal import DirectCommandJournalError
from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDispatchCommand, GatewayOwnerServiceCallError
from bt_api_py.service_registry import ServiceCallContext, ServiceDispatchAdmission

OPERATIONS = frozenset({'direct.delegated_reserve','direct.delegated_dispatch'})


@dataclass(frozen=True)
class GatewayDelegationSettings:
    grants: tuple[GatewayDelegateGrant, ...]
    instrument_recheck: Callable = field(repr=False, compare=False)
    parent_recheck: Callable | None = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        if (type(self.grants) is not tuple or not self.grants
                or any(type(g) is not GatewayDelegateGrant or g.identity.role is not WireRole.SERVICE
                       or g.identity.delegate_ref is not None for g in self.grants)
                or len({(g.identity.principal_ref, g.account_scope, g.strategy_scope) for g in self.grants}) != len(self.grants)
                or not callable(self.instrument_recheck)
                or (self.parent_recheck is not None and not callable(self.parent_recheck))):
            raise ValueError('INVALID_GATEWAY_DELEGATION')


class GatewayDelegationService:
    def __init__(self, settings, *, owner, journal, instance_id, scope_ref, market_gate_required=False):
        if (type(settings) is not GatewayDelegationSettings or type(owner) is not GatewayDirectOwner
                or not owner.is_bound_to(journal) or journal._schema_version < 12
                or type(market_gate_required) is not bool
                or any(g.account_scope != journal.account_scope for g in settings.grants)):
            raise ValueError('INVALID_GATEWAY_DELEGATION')
        self.settings, self.owner, self.journal = settings, owner, journal
        self.instance_id, self.scope_ref = instance_id, scope_ref
        self._market_gate_required = market_gate_required
        self._market_gate = None

    def configure(self, registry):
        registry.configure_gateway_delegates(instance_id=self.instance_id, scope_ref=self.scope_ref,
                                             grants=self.settings.grants)

    def install_market_gate(self, gate):
        from bt_api_py.gateway_market_dispatch_gate import GatewayMarketDispatchGate
        from bt_api_py.gateway_owner_market import GatewayOwnerMarketProvider
        provider = getattr(getattr(gate, '_hub', None), '_provider', None)
        if (not self._market_gate_required or self._market_gate is not None
                or type(gate) is not GatewayMarketDispatchGate or gate._account != self.journal.account_scope
                or gate._generation != self.journal.generation or type(provider) is not GatewayOwnerMarketProvider
                or provider._owner is not self.owner or getattr(gate._native_source_check, '__self__', None) is not provider
                or getattr(gate._native_source_check, '__func__', None) is not GatewayOwnerMarketProvider.recheck_market_tick_on_native_owner):
            raise ValueError('INVALID_MARKET_GATE_BINDING')
        self._market_gate = gate

    def handle(self, operation, context, arguments):
        if (operation not in OPERATIONS or type(context) is not ServiceCallContext
                or not isinstance(arguments, Mapping) or context.identity.role is not WireRole.SERVICE):
            return {'status':'REQUEST_REJECTED','code':'SCOPE_DENIED'}
        admission = context.dispatch_admission
        if (type(admission) is not ServiceDispatchAdmission or not admission.matches_context(context)
                or not admission.matches_operation(operation) or not admission.matches(arguments)):
            return {'status':'REQUEST_REJECTED','code':'ADMISSION_DENIED'}
        expected = ({'command','payload','ttl_seconds'} if operation == 'direct.delegated_reserve'
                    else {'command','payload','reservation_id','expected_version'})
        allowed = expected | {'market_ref'} if operation == 'direct.delegated_dispatch' else expected
        if set(arguments) not in (expected, allowed) or not isinstance(arguments.get('payload'), Mapping):
            admission.close()
            return {'status':'REQUEST_REJECTED','code':'INVALID_REQUEST'}
        try:
            command = GatewayDelegatedCommand.from_mapping(arguments['command'])
            payload = dict(arguments['payload'])
            from bt_api_py.gateway_order_contract import decode_gateway_order_request
            decoded = decode_gateway_order_request(payload, self.journal.account_scope)
            if decoded.idempotency_key != command.request_key:
                raise ValueError('DELEGATED_REQUEST_MISMATCH')
            grant = next(g for g in self.settings.grants if g.identity == context.identity
                         and g.account_scope == context.scope.account_ref and g.strategy_scope == context.scope.strategy_ref)
            if not admission.matches_delegate_grant(grant):
                raise ValueError('DELEGATION_GRANT_CHANGED')
        except Exception:
            admission.close()
            return {'status':'REQUEST_REJECTED','code':'SCOPE_DENIED'}
        logical = 'direct.reserve' if operation == 'direct.delegated_reserve' else 'direct.dispatch'
        transferred = False
        active = threading.local()
        def registry_recheck(_context):
            host_check = getattr(active, 'host_check', None)
            if host_check is not None:
                host_check()
                return True
            check = getattr(active, 'check', None)
            if check is None:
                with admission.scope() as check:
                    check()
            else:
                check()
            return True
        def authorize():
            return authorize_delegated_command(context, grant, command, logical, payload,
                registry_recheck=registry_recheck, current_grant=lambda:grant,
                instrument_recheck=self.settings.instrument_recheck, parent_recheck=self.settings.parent_recheck)
        def build_permit():
            return authorize()
        def project(row):
            return {'status':'OBSERVED','schema_version':1,'account_scope':row.account_scope,
                'strategy_scope':row.strategy_scope,'actor_principal':row.principal,
                'request_key':row.request_key,'reservation_id':row.reservation_id,'digest':row.digest,
                'state':row.status,'version':row.version,'owner_generation':self.journal.generation,
                'reservation_generation':row.generation}
        try:
            if operation == 'direct.delegated_reserve':
                ttl = arguments['ttl_seconds']
                if type(ttl) not in (int,float) or not 0 < ttl <= 30:
                    return {'status':'REQUEST_REJECTED','code':'INVALID_REQUEST'}
                def reserve():
                    permit = authorize()
                    with admission.scope() as check:
                        active.check = check
                        try:
                            try:
                                row = self.journal.reserve(command.strategy_scope,command.request_key,command.actor_principal,
                                    payload,ttl_seconds=ttl,reservation_fence=nullcontext,delegation_permit=permit)
                            except DirectCommandJournalError as error:
                                if error.code not in {'SYSTEM_ORDER_UNKNOWN', 'UNKNOWN_SCOPE_UNAVAILABLE'}:
                                    raise
                                check()
                                return {'status':'REQUEST_REJECTED','code':error.code}
                            check()
                            return project(row)
                        finally:
                            active.check = None
                return self.owner.submit_service_call(reserve).result(timeout_seconds=1.0)
            if (type(arguments['reservation_id']) is not str or not arguments['reservation_id']
                    or type(arguments['expected_version']) is not int or arguments['expected_version'] < 1):
                return {'status':'REQUEST_REJECTED','code':'INVALID_REQUEST'}
            permit = self.owner.submit_service_call(build_permit).result(timeout_seconds=1.0)
            row = self.journal.get(command.strategy_scope,command.request_key,command.actor_principal)
            if row.reservation_id != arguments['reservation_id'] or row.digest != command.payload_digest:
                return {'status':'REQUEST_REJECTED','code':'REQUEST_CONFLICT'}
            if row.status != 'RESERVED':
                return project(row)
            if row.version != arguments['expected_version']:
                return {'status':'REQUEST_REJECTED','code':'VERSION_CONFLICT'}
            def unavailable():
                # The permit was checked on the native owner above; the
                # Registry admission is bound to that same owner thread.
                with admission.scope() as check:
                    check()
                    return {'status':'REQUEST_REJECTED','code':'DISPATCH_CLAIM_UNAVAILABLE'}
            return self.owner.submit_service_call(unavailable).result(timeout_seconds=1.0)
            if self._market_gate_required and self._market_gate is None:
                return {'status':'REQUEST_REJECTED','code':'MARKET_GATE_UNAVAILABLE'}
            market_gate = market_permit = market_registration = None
            if self._market_gate_required:
                if 'market_ref' not in arguments:
                    return {'status':'REQUEST_REJECTED','code':'MARKET_GATE_UNAVAILABLE'}
                try:
                    from bt_api_py.gateway_execution_stream_client import _detach_wire
                    registry = admission._registry
                    market_gate = self._market_gate
                    with registry._read_snapshot():
                        def host_check():
                            code = registry._direct_dispatch_admission_error(context.registration_id,
                                admission._binding.service_epoch, self.instance_id, context.principal_ref,
                                context.identity, context.scope, operation)
                            current = registry.gateway_delegate_grant(instance_id=self.instance_id,
                                scope_ref=self.scope_ref, identity=context.identity, scope=context.scope, operation=operation)
                            if code is not None or current != grant or not admission.matches_delegate_grant(grant):
                                raise ValueError('DELEGATION_CHANGED')
                        host_check()
                        market_registration = registry.gateway_market_registration_current(context, instance_id=self.instance_id)
                        if market_registration is None:
                            raise ValueError('MARKET_REGISTRATION_UNAVAILABLE')
                        active.host_check = host_check
                        try:
                            market_permit = market_gate.issue(context, _detach_wire(arguments['market_ref']),
                                market_registration_id=market_registration, request_key=command.request_key,
                                reservation_id=row.reservation_id, digest=row.digest, delegation_permit=permit)
                        finally:
                            active.host_check = None
                except Exception:
                    return {'status':'REQUEST_REJECTED','code':'MARKET_GATE_UNAVAILABLE'}
            @contextmanager
            def request_fence(current):
                with admission.scope() as check:
                    active.check = check
                    try:
                        if (current.account_scope != command.account_scope or current.strategy_scope != command.strategy_scope
                                or current.request_key != command.request_key or current.principal != command.actor_principal
                                or current.reservation_id != row.reservation_id or current.digest != command.payload_digest):
                            raise ValueError('DELEGATED_BINDING_CHANGED')
                        def recheck_all():
                            check()
                            if (market_gate is not None and registry.gateway_market_registration_current(
                                    context, instance_id=self.instance_id,
                                    registration_id=market_registration) != market_registration):
                                raise ValueError('MARKET_REGISTRATION_CHANGED')
                        recheck_all()
                        yield recheck_all
                    finally:
                        active.check = None
            queued = self.owner.enqueue(GatewayDispatchCommand(command.strategy_scope,command.request_key,
                command.actor_principal,row.reservation_id,row.digest,arguments['expected_version']),
                request_fence=request_fence,finalize=admission.close,delegation_permit=permit,
                **({'market_gate': market_gate, 'market_permit': market_permit} if market_gate is not None else {}))
            if queued in {'QUEUED','DUPLICATE'}:
                transferred = queued == 'QUEUED'
                return {'status':'QUEUED' if queued == 'QUEUED' else 'ALREADY_QUEUED',
                        'request_key':command.request_key,'reservation_id':row.reservation_id}
            return {'status':'REQUEST_REJECTED','code':'DISPATCH_UNAVAILABLE'}
        except GatewayOwnerServiceCallError:
            return {'status':'UNKNOWN'}
        except Exception:
            return {'status':'UNKNOWN'}
        finally:
            if not transferred:
                admission.close()
