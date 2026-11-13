"""Synthetic, owner-local checks for a possible Risk-first reservation.

This private primitive is not installed in a handler. Its argument shape is a
test candidate, not a wire contract. Success is not a permit, source proof, or
authority to write. Risk service/epoch and full instrument identity are claims.
Registry, service, instance and holder are caller-supplied and are checked for
mutual consistency only; no production Host anchor is installed here.
"""

import threading
from types import MappingProxyType

from bt_api_base.gateway_risk_first_reserve_v2 import GatewayRiskFirstReserveRequestV2
from bt_api_base.service_protocol import WireRole
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_delegation import GatewayDelegateGrant, delegated_request_matches

from ._gateway_owner_guard_binding import _GatewayOwnerGuardBinding
from .gateway_direct_owner import GatewayDirectOwner
from .risk_first_payload_consistency import validate_risk_first_payload_consistency
from .service_registry import ServiceCallContext, ServiceDispatchAdmission, ServiceRegistry


class _RiskFirstScopeError(RuntimeError):
    """Value-free refusal of the synthetic classification."""


def _plain(value):
    # Registry wire values are recursively frozen before a handler receives
    # them. Restrict this copy to built-in wire types, with no user callbacks.
    if type(value) is MappingProxyType:
        return {key: _plain(item) for key, item in value.items()}
    if type(value) is tuple:
        return [_plain(item) for item in value]
    if value is None or type(value) in (str, bool, int, float):
        return value
    raise ValueError


def assert_in_active_scope(*, registry, service_id, instance_id, context, arguments,
                           admission, recheck, owner, journal, owner_binding):
    """Assert current consistency on the owner, returning no durable result.

    Caller enters the exact Registry admission scope on the owner thread before
    calling. Never dispatch work or perform provider/network I/O under it. The
    owner condition is acquired without waiting: a competing close or another
    owner operation causes refusal instead of a Registry/owner lock wait cycle.
    These checks are sequential observations, not atomic authorization. Owner
    or Registry state may change during or after them. A future transaction
    requires its own atomic design and same-scope final recheck.
    """
    try:
        if (type(registry) is not ServiceRegistry or type(service_id) is not str
                or not service_id or registry.service_id != service_id
                or type(instance_id) is not str or not instance_id
                or type(context) is not ServiceCallContext
                or type(admission) is not ServiceDispatchAdmission
                or type(owner) is not GatewayDirectOwner
                or type(journal) is not GatewayDirectCommandJournal
                or type(owner_binding) is not _GatewayOwnerGuardBinding
                or admission._registry is not registry
                or admission._binding.instance_id != instance_id
                or owner._thread is not threading.current_thread()
                or not admission.matches_scope_recheck(recheck)):
            raise ValueError

        def check():
            if (type(arguments) is not MappingProxyType or set(arguments) != {"request", "payload"}
                    or type(arguments["request"]) is not MappingProxyType
                    or type(arguments["payload"]) is not MappingProxyType
                    or not context._is_registry_wire_arguments(arguments)
                    or not admission.matches_context(context)
                    or not admission.matches_operation("direct.risk_first_reserve.v2")
                    or not admission.matches(arguments)):
                raise ValueError
            identity, scope = context.identity, context.scope
            grant = admission._binding.delegate_grant
            snapshot = _plain(arguments)
            request = GatewayRiskFirstReserveRequestV2.from_mapping(snapshot["request"])
            command = request.command
            if (identity.role is not WireRole.SERVICE or identity.delegate_ref is not None
                    or type(grant) is not GatewayDelegateGrant
                    or not admission.matches_delegate_grant(grant)
                    or command.parent_ref is not None or command.child_id is not None
                    or command.actor_kind != "risk" or grant.actor_kind != "risk"
                    or scope.account_ref != journal.account_scope
                    or not delegated_request_matches(context, grant, command,
                                                     "direct.reserve", snapshot["payload"])):
                raise ValueError
            validate_risk_first_payload_consistency(request, snapshot["payload"])
            if not admission.matches(arguments):
                raise ValueError

        def check_owner():
            # The binding uses the same reentrant condition. Never wait for it
            # under the Registry lock or retain it during a Registry recheck.
            if not owner._condition.acquire(blocking=False):
                raise ValueError
            try:
                owner_binding.assert_instance(instance_id)
                owner_binding.assert_bound(owner, journal)
            finally:
                owner._condition.release()

        recheck()
        check_owner()
        check()
        recheck()
        check_owner()
        check()
        recheck()
        check_owner()
        if not admission.matches_scope_recheck(recheck):
            raise ValueError
        return None
    except Exception:  # noqa: S110 - keep the fixed error free of input-bearing context
        pass
    raise _RiskFirstScopeError("RISK_FIRST_SCOPE_UNAVAILABLE")
