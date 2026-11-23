"""Explicitly granted SERVICE delegation onto one Gateway account owner."""
from __future__ import annotations

import logging
import threading
import math
from collections.abc import Mapping
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from typing import Callable

from bt_api_base.gateway_delegation_protocol import GatewayDelegatedCommand
from bt_api_base.service_protocol import WireRole
from bt_api_gateway.direct_delegation import (GatewayDelegateGrant, authorize_delegated_command,
                                              delegated_request_matches)
from bt_api_gateway.direct_command_journal import DirectCommandJournalError
from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDispatchCommand, GatewayOwnerServiceCallError
from bt_api_py.gateway_direct_service import _reservation_admission, _ReservationAdmissionRejected
from bt_api_py.service_registry import ServiceCallContext, ServiceDispatchAdmission

_LOG = logging.getLogger(__name__)
OPERATIONS = frozenset({'direct.delegated_reserve','direct.delegated_dispatch'})
RISK_FIRST_RESERVE_OPERATION = 'direct.risk_first_reserve.v2'

# Journal refusals raised inside the owner's reserve transaction before it commits: the
# transaction rolled back, so the request is answered as a refusal, exactly as the
# strategy's own reserve answers them (GatewayDirectServiceBinding._handle).
_DEFINITE_RESERVE_REFUSALS = frozenset({
    'SYSTEM_ORDER_UNKNOWN', 'UNKNOWN_SCOPE_UNAVAILABLE', 'STRATEGY_LEDGER_UNAVAILABLE',
    'STRATEGY_CLOSE_EXCEEDS', 'CONTROL_FREEZE', 'CONTROL_STOP_NEW', 'CONTROL_DRAIN'})


_SUBMIT = frozenset({'direct.reserve', 'direct.dispatch'})


@dataclass(frozen=True)
class GatewayDelegationSettings:
    """Installed delegate grants (D63-13: static registration).

    ``risk_only_submit_strategies`` is the optional D63-37 setting, empty by
    default: for each listed strategy SUBMIT (reserve and dispatch) belongs only
    to its Risk delegate. The strategy itself is refused SUBMIT and no other
    delegate may hold it; the strategy keeps its other operations, and its run
    needs at least one write operation (a cancel, for example) so that the Risk
    delegate can register for it. This is an ordinary grant, not a Risk approval
    check: the Gateway never asks Risk whether an order was approved.
    """
    grants: tuple[GatewayDelegateGrant, ...]
    instrument_recheck: Callable = field(repr=False, compare=False)
    parent_recheck: Callable | None = field(default=None, repr=False, compare=False)
    risk_only_submit_strategies: frozenset[str] = frozenset()
    risk_first_enabled: bool = False

    def __post_init__(self):
        if (type(self.grants) is not tuple or not self.grants
                or any(type(g) is not GatewayDelegateGrant or g.identity.role is not WireRole.SERVICE
                       or g.identity.delegate_ref is not None for g in self.grants)
                or len({(g.identity.principal_ref, g.account_scope, g.strategy_scope) for g in self.grants}) != len(self.grants)
                or not callable(self.instrument_recheck)
                or (self.parent_recheck is not None and not callable(self.parent_recheck))
                or type(self.risk_first_enabled) is not bool
                or (self.risk_first_enabled and not any(g.actor_kind == 'risk' for g in self.grants))):
            raise ValueError('INVALID_GATEWAY_DELEGATION')
        only = self.risk_only_submit_strategies
        if (type(only) is not frozenset
                or any(type(s) is not str or not s or s != s.strip() for s in only)
                or any(not any(g.strategy_scope == s and g.actor_kind == 'risk' and g.operations >= _SUBMIT
                               for g in self.grants)
                       or any(g.strategy_scope == s and g.actor_kind != 'risk' and g.operations & _SUBMIT
                              for g in self.grants)
                       for s in only)):
            raise ValueError('INVALID_GATEWAY_DELEGATION')


class GatewayDelegationService:
    def __init__(self, settings, *, owner, journal, instance_id, scope_ref, market_gate_required=False,
                 reservation_fence=None, risk_first_owner_binding=None):
        # ``reservation_fence`` is the trading fence of the strategies' own reservations
        # (``GatewayDirectServiceBinding``): a delegated reservation goes through it too, so a
        # delegate is never admitted where the represented strategy would not be (G64-09).
        if (type(settings) is not GatewayDelegationSettings or type(owner) is not GatewayDirectOwner
                or not owner.is_bound_to(journal) or journal._schema_version < 12
                or type(market_gate_required) is not bool
                or (reservation_fence is not None and not callable(reservation_fence))
                or any(g.account_scope != journal.account_scope for g in settings.grants)):
            raise ValueError('INVALID_GATEWAY_DELEGATION')
        self.settings, self.owner, self.journal = settings, owner, journal
        self.instance_id, self.scope_ref = instance_id, scope_ref
        self._market_gate_required = market_gate_required
        self._market_gate = None
        self._reservation_fence = reservation_fence
        from bt_api_py._gateway_owner_guard_binding import _GatewayOwnerGuardBinding
        if ((settings.risk_first_enabled and type(risk_first_owner_binding) is not _GatewayOwnerGuardBinding)
                or (not settings.risk_first_enabled and risk_first_owner_binding is not None)):
            raise ValueError('INVALID_GATEWAY_DELEGATION')
        self._risk_first_owner_binding = risk_first_owner_binding
        self._risk_first_registry = None
        self._risk_first_host_check = None

    def configure(self, registry, *, risk_first_host_check=None):
        if (self.settings.risk_first_enabled != callable(risk_first_host_check)):
            raise ValueError('INVALID_GATEWAY_DELEGATION')
        if self.settings.risk_first_enabled:
            from bt_api_py.service_registry import ServiceRegistry
            if type(registry) is not ServiceRegistry or self._risk_first_registry is not None:
                raise ValueError('INVALID_GATEWAY_DELEGATION')
        registry.configure_gateway_delegates(instance_id=self.instance_id, scope_ref=self.scope_ref,
                                             grants=self.settings.grants)
        if self.settings.risk_first_enabled:
            self._risk_first_registry = registry
            self._risk_first_host_check = risk_first_host_check

    def _handle_risk_first(self, context, arguments):
        """Return RESERVED plus the exact v2 result, or UNKNOWN for any uncertainty.

        A response never certifies an earlier attempt was not sent. Only the
        journal's current first-insert result can be projected; old rows cannot
        be used to reconstruct a first-reservation response.
        """
        from bt_api_base.gateway_risk_first_reserve_v2 import (
            GatewayRiskFirstReserveRequestV2, GatewayRiskFirstReserveResultV2,
        )
        from bt_api_gateway.direct_command_journal import FirstDelegatedReservation
        from bt_api_py._gateway_risk_first_scope import _plain, assert_in_active_scope

        if (not self.settings.risk_first_enabled or self._risk_first_registry is None
                or not callable(self._risk_first_host_check)
                or type(context) is not ServiceCallContext):
            return {'status': 'UNKNOWN'}
        admission = context.dispatch_admission
        if type(admission) is not ServiceDispatchAdmission:
            return {'status': 'UNKNOWN'}
        try:
            def reserve():
                with admission.scope() as check:
                    def recheck(_context=None):
                        if self._risk_first_host_check() is not True:
                            raise ValueError('GATEWAY_OWNER_GUARD_BINDING_INVALID')
                        assert_in_active_scope(
                            registry=self._risk_first_registry, service_id=self._risk_first_registry.service_id,
                            instance_id=self.instance_id, context=context, arguments=arguments,
                            admission=admission, recheck=check, owner=self.owner, journal=self.journal,
                            owner_binding=self._risk_first_owner_binding,
                        )
                        if self._risk_first_host_check() is not True:
                            raise ValueError('GATEWAY_OWNER_GUARD_BINDING_INVALID')
                        return True

                    recheck()
                    values = _plain(arguments)
                    request = GatewayRiskFirstReserveRequestV2.from_mapping(values['request'])
                    payload, command = values['payload'], request.command
                    grant = admission._binding.delegate_grant
                    permit = authorize_delegated_command(
                        context, grant, command, 'direct.reserve', payload, registry_recheck=recheck,
                        current_grant=lambda: grant, instrument_recheck=self.settings.instrument_recheck,
                        parent_recheck=self.settings.parent_recheck,
                    )
                    from bt_api_py.gateway_order_contract import decode_gateway_order_request
                    decoded = decode_gateway_order_request(payload, self.journal.account_scope)
                    trading = (None if self._reservation_fence is None else
                               lambda: _reservation_admission(self._reservation_fence, context, decoded))
                    first = self._risk_first_owner_binding.reserve_first_risk(
                        self.owner, self.journal, request, payload, permit=permit, trading_fence=trading,
                    )
                    recheck()
                    if (type(first) is not FirstDelegatedReservation
                            or first.account_scope != command.account_scope
                            or first.strategy_scope != command.strategy_scope
                            or first.request_key != command.request_key
                            or first.actor_principal != command.actor_principal
                            or first.run_id != command.run_id
                            or first.payload_digest != command.payload_digest
                            or first.delegation_digest != command.digest
                            or first.journal_id != self.journal.journal_id
                            or first.owner_generation != self.journal.generation
                            or type(first.version) is not int or first.version != 1
                            or type(first.remaining_ttl_seconds) not in (int, float)
                            or not math.isfinite(first.remaining_ttl_seconds)):
                        raise ValueError('FIRST_RESERVATION_UNAVAILABLE')
                    remaining = math.floor(self._risk_first_owner_binding.first_risk_remaining(
                        self.owner, self.journal, first) * 1000)
                    result = GatewayRiskFirstReserveResultV2(
                        request_commitment_digest=request.digest, journal_id=first.journal_id,
                        owner_generation=first.owner_generation, reservation_generation=first.owner_generation,
                        account_scope=first.account_scope, strategy_scope=first.strategy_scope,
                        execution_domain_ref=command.execution_domain_ref, run_id=first.run_id,
                        request_key=first.request_key, reservation_id=first.reservation_id,
                        payload_digest=first.payload_digest, remaining_ttl_ms=remaining,
                    )
                    if not result.matches_request(request):
                        raise ValueError('FIRST_RESERVATION_UNAVAILABLE')
                    recheck()
                    return {'status': 'RESERVED', 'result': result.to_mapping()}
            return self.owner.submit_service_call(reserve).result(timeout_seconds=1.0)
        except Exception:
            return {'status': 'UNKNOWN'}
        finally:
            admission.close()

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
        if operation == RISK_FIRST_RESERVE_OPERATION:
            return self._handle_risk_first(context, arguments)
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
        if not delegated_request_matches(context, grant, command, logical, payload):
            # Another strategy, actor or instrument than this registration and
            # grant cover: refused here, before the owner, so nothing is written.
            admission.close()
            return {'status':'REQUEST_REJECTED','code':'DELEGATION_DENIED'}
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
                fence = self._reservation_fence
                trading = ({} if fence is None else
                           {'origin_trading_fence': lambda: _reservation_admission(fence, context, decoded)})
                def reserve():
                    permit = authorize()
                    with admission.scope() as check:
                        active.check = check
                        try:
                            try:
                                row = self.journal.reserve(command.strategy_scope,command.request_key,command.actor_principal,
                                    payload,ttl_seconds=ttl,reservation_fence=nullcontext,delegation_permit=permit,
                                    **trading)
                            except _ReservationAdmissionRejected:
                                # Refused at the fence's entry: the journal rolled back (I63-E141).
                                check()
                                return {'status':'REQUEST_REJECTED','code':'ADMISSION_DENIED'}
                            except DirectCommandJournalError as error:
                                # Raised inside the owner transaction, which rolled back: no
                                # command and no delegation envelope exist. The same codes the
                                # strategy's own reserve answers as a refusal (G64-07).
                                if error.code not in _DEFINITE_RESERVE_REFUSALS:
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
            if not self.owner.supports_dispatch_claims():
                return self.owner.submit_service_call(unavailable).result(timeout_seconds=1.0)
            if self._market_gate_required and self._market_gate is None:
                return {'status':'REQUEST_REJECTED','code':'MARKET_GATE_UNAVAILABLE'}
            market_gate = market_permit = market_registration = None
            if self._market_gate_required:
                if 'market_ref' not in arguments:
                    _LOG.warning("delegated_dispatch rejected: MARKET_GATE_UNAVAILABLE (missing_market_ref)")
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
                except Exception as exc:
                    _LOG.warning(
                        "delegated_dispatch rejected: MARKET_GATE_UNAVAILABLE (%s)",
                        getattr(exc, "code", None) or type(exc).__name__,
                    )
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
