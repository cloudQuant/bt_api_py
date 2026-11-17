"""Same-owner, one-shot Risk-first Gateway receipt bridge.

This is explicit composition for a future Risk handler. It does not bypass the
Risk source guard or Gateway write gate, authorize dispatch, or prove that a
remote handler has drained. No receipt or result survives process restart.
"""
from __future__ import annotations

import math
import os
import threading
from dataclasses import replace

from bt_api_base.gateway_direct_protocol import canonical_gateway_payload
from bt_api_base.gateway_risk_first_reserve_v2 import (
    GatewayRiskFirstReserveRequestV2, GatewayRiskFirstReserveResultV2,
)
from bt_api_base.service_protocol import WireRole
from bt_api_py.risk_first_payload_consistency import validate_risk_first_payload_consistency
from bt_api_py.service_registry import ServiceCallContext, ServiceDispatchAdmission
from bt_api_py.service_runtime import BoundServiceTarget, ServiceClientRuntime, _BOUND_MONOTONIC, _bound_clock_current
from bt_api_py.service_runtime_thread import _copy_wire, _timeout


class RiskGatewayFirstReserveError(RuntimeError):
    """Finite, value-free refusal or unknown outcome."""


class _Attempt:
    __slots__ = ()
    def __copy__(self):
        raise TypeError('attempt cannot be copied')
    def __deepcopy__(self, _memo):
        raise TypeError('attempt cannot be copied')
    def __reduce_ex__(self, _protocol):
        raise TypeError('attempt cannot be serialized')


class VerifiedRiskFirstResult:
    """Opaque one-use local historical observation, not permission to dispatch."""
    __slots__ = ()
    def __copy__(self):
        raise TypeError('result cannot be copied')
    def __deepcopy__(self, _memo):
        raise TypeError('result cannot be copied')
    def __reduce_ex__(self, _protocol):
        raise TypeError('result cannot be serialized')


class RiskGatewayFirstReserveBridge:
    """Bind a single durable Risk first attempt to one strict Gateway call.

    The Risk ledger, admission and dedicated ServiceClientRuntime must already
    belong to this exact thread. ``prepare`` commits the first-attempt row;
    ``send_once`` runs without holding the Registry scope or a ledger transaction;
    ``consume_once`` rechecks both original local owners and yields an opaque
    historical receipt. Uncertainty consumes the attempt and never resends it.
    """

    def __init__(self, *, ledger, runtime, context, arguments, binding, target, request, payload):
        from bt_api_risk.pretrade.ledger import DispatchV2Binding, PretradeLedger
        try:
            if (type(ledger) is not PretradeLedger or type(runtime) is not ServiceClientRuntime
                    or type(context) is not ServiceCallContext
                    or type(context.dispatch_admission) is not ServiceDispatchAdmission
                    or type(binding) is not DispatchV2Binding or type(target) is not BoundServiceTarget
                    or type(request) is not GatewayRiskFirstReserveRequestV2
                    or context.identity.role is not WireRole.STRATEGY_CLIENT
                    or not context._is_registry_wire_arguments(arguments)):
                raise ValueError
            ledger._check()
            runtime._assert_owner_thread()
            admission = context.dispatch_admission
            request_snapshot = GatewayRiskFirstReserveRequestV2.from_mapping(request.to_mapping())
            target_snapshot = replace(target, scope=replace(target.scope), identity=replace(target.identity),
                                      expected_peer=replace(target.expected_peer))
            target_snapshot.__post_init__()
            payload_snapshot = _copy_wire(payload)
            validate_risk_first_payload_consistency(request_snapshot, payload_snapshot)
            command = request_snapshot.command
            inbound = _copy_wire(arguments)
            if (not admission.matches_context(context) or not admission.matches_operation('risk.order.reserve.v2')
                    or not admission.matches(arguments) or request_snapshot.risk_service_id != admission._registry.service_id
                    or request_snapshot.risk_instance_id != admission._binding.instance_id
                    or request_snapshot.strategy_principal != context.principal_ref
                    or request_snapshot.risk_registration_id != context.registration_id
                    or request_snapshot.risk_service_epoch != admission._binding.service_epoch
                    or request_snapshot.risk_permit_id != binding.permit_id
                    or request_snapshot.risk_view_digest != binding.view_digest
                    or binding.principal_ref != context.principal_ref
                    or binding.registration_id != context.registration_id
                    or binding.service_epoch != admission._binding.service_epoch
                    or (binding.account_scope, binding.strategy_ref, binding.request_key, binding.run_id,
                        binding.payload_digest) != (command.account_scope, command.strategy_scope, command.request_key,
                                                   command.run_id, command.payload_digest)
                    or (context.scope.account_ref, context.scope.strategy_ref, context.scope.run_id,
                        context.scope.execution_domain_ref) != (command.account_scope, command.strategy_scope,
                                                               command.run_id, command.execution_domain_ref)
                    or target_snapshot.scope != context.scope or target_snapshot.run_id != command.run_id
                    or target_snapshot.identity.role is not WireRole.SERVICE
                    or (target_snapshot.identity.principal_ref, target_snapshot.identity.actor_ref)
                    != (command.actor_principal, command.actor_ref)
                    or type(inbound) is not dict
                    or set(inbound) != {'request_key', 'payload', 'parent_ref', 'child_id'}
                    or inbound['request_key'] != command.request_key
                    or inbound['parent_ref'] != command.parent_ref or inbound['child_id'] != command.child_id
                    or canonical_gateway_payload(inbound['payload']) != canonical_gateway_payload(payload_snapshot)):
                raise ValueError
            if not _bound_clock_current():
                raise ValueError
        except Exception:
            raise RiskGatewayFirstReserveError('FIRST_ATTEMPT_UNAVAILABLE') from None
        self._ledger, self._journal, self._fence = ledger, ledger._journal, ledger._fence
        self._runtime, self._context, self._admission, self._arguments = runtime, context, admission, arguments
        self._binding, self._target, self._target_snapshot = binding, target, target_snapshot
        self._binding_snapshot = replace(binding)
        self._request, self._request_snapshot = request, request_snapshot
        self._payload, self._payload_json = payload, canonical_gateway_payload(payload_snapshot)
        self._context_identity, self._context_scope = replace(context.identity), replace(context.scope)
        self._admission_binding = admission._binding
        self._pid, self._thread = os.getpid(), threading.current_thread()
        self._state, self._attempt, self._snapshot, self._ticket = 'new', None, None, None
        self._verified, self._verified_value, self._verified_at = None, None, None
        self._verified_gateway = None
        self._consume_marker = None

    def _same_owner(self):
        if (os.getpid() != self._pid or threading.current_thread() is not self._thread
                or self._ledger._journal is not self._journal or self._ledger._fence is not self._fence
                or self._admission._binding is not self._admission_binding
                or self._binding != self._binding_snapshot
                or self._context.identity != self._context_identity or self._context.scope != self._context_scope
                or not self._admission.matches_context(self._context)
                or not self._admission.matches_operation('risk.order.reserve.v2')
                or not self._context._is_registry_wire_arguments(self._arguments)
                or not self._admission.matches(self._arguments)
                or type(self._target) is not BoundServiceTarget
                or replace(self._target, scope=replace(self._target.scope), identity=replace(self._target.identity),
                           expected_peer=replace(self._target.expected_peer)) != self._target_snapshot
                or type(self._request) is not GatewayRiskFirstReserveRequestV2
                or self._request.to_mapping() != self._request_snapshot.to_mapping()
                or canonical_gateway_payload(_copy_wire(self._payload)) != self._payload_json
                or not _bound_clock_current()):
            raise RiskGatewayFirstReserveError('FIRST_ATTEMPT_UNAVAILABLE')
        self._ledger._check()
        self._runtime._assert_owner_thread()

    def _record_current(self, txn):
        from bt_api_risk.pretrade.ledger import _source_unverified
        if _source_unverified(txn._c):
            raise ValueError
        snapshot = txn._v4_snapshot(self._binding.permit_id)
        permit = txn.permit(self._binding.permit_id)
        row = txn._v2_record(self._binding)
        if (snapshot != self._snapshot or permit is None or row is None
                or permit.state != 'RESERVING' or permit.row_version != 1
                or row['status'] != 'PENDING' or type(permit.expires_ns) is not int):
            raise ValueError
        now = txn._v2_now()
        txn._v2_check_now(now, permit.expires_ns, previous=permit.updated_ns)
        if txn._v4_snapshot(self._binding.permit_id) != self._snapshot:
            raise ValueError

    def prepare(self, values):
        if self._state != 'new':
            raise RiskGatewayFirstReserveError('FIRST_ATTEMPT_UNAVAILABLE')
        self._state = 'unknown'
        try:
            self._same_owner()
            with self._admission.scope() as check:
                check()
                txn_ref = [None]
                def final_check():
                    self._same_owner()
                    check()
                    self._record_current(txn_ref[0])
                    check()
                with self._ledger.transaction(_before_commit=final_check) as txn:
                    txn_ref[0] = txn
                    txn.insert_first_attempt_v4(values, self._binding, self._request_snapshot,
                        gateway_service_id=self._target_snapshot.service_id,
                        gateway_instance_id=self._target_snapshot.instance_id, recheck=check)
                    self._snapshot = txn._v4_snapshot(self._binding.permit_id)
                self._same_owner()
                check()
            self._attempt = _Attempt()
            self._state = 'prepared'
            return self._attempt
        except Exception:
            raise RiskGatewayFirstReserveError('FIRST_ATTEMPT_UNAVAILABLE') from None

    def _check_attempt(self, attempt):
        if type(attempt) is not _Attempt or attempt is not self._attempt:
            raise ValueError
        self._same_owner()
        with self._admission.scope() as check:
            check()
            with self._ledger.transaction() as txn:
                self._record_current(txn)
                self._same_owner()
                check()
            check()

    def _take_send(self, attempt, runtime, target, request, payload):
        if (self._state != 'prepared' or runtime is not self._runtime
                or target is not self._target_snapshot or request is not self._request_snapshot
                or canonical_gateway_payload(_copy_wire(payload)) != self._payload_json):
            raise RiskGatewayFirstReserveError('FIRST_ATTEMPT_UNAVAILABLE')
        self._check_attempt(attempt)
        self._state = 'sent'

    def _check_sent(self, attempt, runtime, target, request, payload):
        self._same_owner()
        if (self._state != 'sent' or attempt is not self._attempt or runtime is not self._runtime
                or target is not self._target_snapshot or request is not self._request_snapshot
                or canonical_gateway_payload(_copy_wire(payload)) != self._payload_json):
            raise ValueError

    def _check_consume_marker(self, marker):
        active = self._consume_marker
        if (type(active) is not tuple or len(active) != 2 or active[0] is not marker
                or not self._admission.matches_scope_recheck(active[1])):
            raise ValueError
        self._same_owner()

    def send_once(self, attempt, *, timeout_seconds):
        if self._state != 'prepared' or attempt is not self._attempt:
            raise RiskGatewayFirstReserveError('FIRST_ATTEMPT_UNAVAILABLE')
        try:
            self._same_owner()
            timeout = _timeout(timeout_seconds)
            deadline = _BOUND_MONOTONIC() + timeout
            if not math.isfinite(deadline):
                raise ValueError
            ticket = self._runtime._capture_risk_first(
                self, attempt, self._target_snapshot, self._request_snapshot,
                _copy_wire(self._payload), deadline_monotonic=deadline)
            self._ticket, self._state = ticket, 'captured'
            return ticket
        except BaseException as error:
            self._state = 'unknown'
            if not isinstance(error, Exception):
                raise
            raise RiskGatewayFirstReserveError('FIRST_ATTEMPT_UNKNOWN') from None

    def consume_once(self, ticket):
        if self._state != 'captured' or ticket is not self._ticket:
            raise RiskGatewayFirstReserveError('FIRST_RECEIPT_UNAVAILABLE')
        self._state = 'unknown'
        try:
            self._same_owner()
            begin = _BOUND_MONOTONIC()
            with self._admission.scope() as check:
                check()
                proof, gateway_binding, gateway_snapshot = self._runtime._risk_first_binding(self._target_snapshot)
                txn_ref = [None]
                def final_check():
                    self._same_owner()
                    check()
                    self._record_current(txn_ref[0])
                    current, binding, snapshot = self._runtime._risk_first_binding(self._target_snapshot)
                    if (any(current[index] is not proof[index] for index in range(3))
                            or binding is not gateway_binding or snapshot != gateway_snapshot):
                        raise ValueError
                    check()
                with self._ledger.transaction(_before_commit=final_check) as txn:
                    txn_ref[0] = txn
                    self._record_current(txn)
                    marker = object()
                    self._consume_marker = (marker, check)
                    try:
                        value = self._runtime._consume_risk_first(self, self._attempt, ticket, marker)
                    finally:
                        self._consume_marker = None
                    final_check()
                self._same_owner()
                check()
                current, binding, snapshot = self._runtime._risk_first_binding(self._target_snapshot)
                if (any(current[index] is not proof[index] for index in range(3))
                        or binding is not gateway_binding or snapshot != gateway_snapshot):
                    raise ValueError
                remaining = value.remaining_ttl_ms - math.ceil((_BOUND_MONOTONIC() - begin) * 1000)
                mapped = value.to_mapping()
                mapped['remaining_ttl_ms'] = remaining
                value = GatewayRiskFirstReserveResultV2.from_mapping_for_request(mapped, self._request_snapshot)
                check()
            result = VerifiedRiskFirstResult()
            self._verified, self._verified_value = result, value
            self._verified_gateway = (proof, gateway_binding, gateway_snapshot)
            self._verified_at, self._state = _BOUND_MONOTONIC(), 'verified'
            return result
        except Exception:
            raise RiskGatewayFirstReserveError('FIRST_RECEIPT_UNKNOWN') from None

    def take_verified(self, result):
        """Consume the opaque historical observation once; it is not a send grant."""
        if type(result) is not VerifiedRiskFirstResult or result is not self._verified or self._state != 'verified':
            raise RiskGatewayFirstReserveError('FIRST_RECEIPT_UNAVAILABLE')
        self._state = 'unknown'
        try:
            self._same_owner()
            if type(self._verified_gateway) is not tuple or len(self._verified_gateway) != 3:
                raise ValueError
            proof, gateway_binding, gateway_snapshot = self._verified_gateway
            def check_gateway():
                current, binding, snapshot = self._runtime._risk_first_binding(self._target_snapshot)
                if (any(current[index] is not proof[index] for index in range(3))
                        or binding is not gateway_binding or snapshot != gateway_snapshot):
                    raise ValueError
            with self._admission.scope() as check:
                txn_ref = [None]
                def final_check():
                    self._same_owner()
                    check()
                    self._record_current(txn_ref[0])
                    check_gateway()
                    check()
                with self._ledger.transaction(_before_commit=final_check) as txn:
                    txn_ref[0] = txn
                    final_check()
                self._same_owner()
                check()
                check_gateway()
                value = self._verified_value
                if type(value) is not GatewayRiskFirstReserveResultV2:
                    raise ValueError
                now = _BOUND_MONOTONIC()
                if type(self._verified_at) is not float or now < self._verified_at:
                    raise ValueError
                mapped = value.to_mapping()
                mapped['remaining_ttl_ms'] -= math.ceil((now - self._verified_at) * 1000)
                value = GatewayRiskFirstReserveResultV2.from_mapping_for_request(mapped, self._request_snapshot)
                check_gateway()
                check()
            self._state = 'consumed'
            return value
        except Exception:
            raise RiskGatewayFirstReserveError('FIRST_RECEIPT_UNKNOWN') from None
        finally:
            self._verified = self._verified_value = self._verified_gateway = None
