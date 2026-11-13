"""Scoped, Registry-backed classification of a direct Gateway reservation.

This is an owner-local assembly primitive, not a provider fact, a cross-host
fence or a separately usable write grant.  It deliberately has no effect until
the Gateway owner installs the matching v2 classification issuer.
"""

from __future__ import annotations

import math
import os
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager

from bt_api_base.gateway_direct_protocol import gateway_payload_digest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal

from bt_api_py.gateway_direct_owner import GatewayDirectOwner
from bt_api_py.gateway_order_contract import decode_gateway_order_request
from bt_api_py.service_registry import ServiceCallContext, ServiceDispatchAdmission, ServiceRegistry


class GatewayRiskV2DirectOriginError(RuntimeError):
    """Finite diagnostic that does not expose request data."""


_ISSUER = object()


class GatewayRiskV2DirectAuthority:
    """One trusted assembly's Registry and owner, with an explicit lifetime.

    Construction is an owner-side assembly boundary, never wire configuration.
    This object alone does not install a Gateway issuer or enable v2 writes.
    """

    __slots__ = ("_registry", "_service_id", "_instance_id", "_owner", "_pid", "_closed")

    def __init__(self, *, registry, service_id, instance_id, owner):
        if (
            type(registry) is not ServiceRegistry
            or type(owner) is not GatewayDirectOwner
            or type(service_id) is not str
            or not service_id
            or registry.service_id != service_id
            or type(instance_id) is not str
            or not instance_id
        ):
            raise GatewayRiskV2DirectOriginError("DIRECT_ORIGIN_UNAVAILABLE")
        for name, value in (
            ("_registry", registry),
            ("_service_id", service_id),
            ("_instance_id", instance_id),
            ("_owner", owner),
            ("_pid", os.getpid()),
            ("_closed", False),
        ):
            object.__setattr__(self, name, value)

    def __setattr__(self, name, value):
        raise AttributeError("direct origin authority is immutable")

    def __reduce_ex__(self, _protocol):
        raise TypeError("direct origin authority cannot be serialized")

    def __copy__(self):
        raise TypeError("direct origin authority cannot be copied")

    def __deepcopy__(self, _memo):
        raise TypeError("direct origin authority cannot be copied")

    def close(self):
        object.__setattr__(self, "_closed", True)

    def validate(self, admission, binding) -> bool:
        """Validate only admissions issued under this installed authority."""
        if (
            type(self) is not GatewayRiskV2DirectAuthority
            or type(admission) is not GatewayRiskV2DirectAdmission
        ):
            return False
        try:
            return (
                admission._authority is self
                and admission._current(binding)
                and admission._authority is self
            )
        except Exception:
            return False

    def admission_identity(self, admission):
        """Return the live Registry-issued identity for owner-local deduplication."""
        try:
            if (
                type(admission) is not GatewayRiskV2DirectAdmission
                or admission._authority is not self
            ):
                raise ValueError
            identity = admission._admission
            if (
                type(identity) is not ServiceDispatchAdmission
                or not self.validate(admission, admission._binding)
                or admission._admission is not identity
                or admission._authority is not self
            ):
                raise ValueError
            return identity
        except Exception:
            raise GatewayRiskV2DirectOriginError("DIRECT_ORIGIN_UNAVAILABLE") from None

    def _matches(self, admission, owner):
        return (
            not self._closed
            and os.getpid() == self._pid
            and owner is self._owner
            and owner._thread is threading.current_thread()
            and type(admission) is ServiceDispatchAdmission
            and admission._registry is self._registry
            and self._registry.service_id == self._service_id
            and admission._binding.instance_id == self._instance_id
        )


class GatewayRiskV2DirectAdmission:
    """A live Registry-scope classification, not a serializable capability."""

    __slots__ = (
        "__weakref__",
        "_admission",
        "_arguments",
        "_binding",
        "_context",
        "_journal",
        "_owner",
        "_pid",
        "_recheck",
        "_thread",
        "_closed",
        "_busy",
        "_authority",
    )

    def __init__(
        self, issuer, *, admission, arguments, authority, binding, context, journal, owner, recheck
    ):
        if issuer is not _ISSUER:
            raise GatewayRiskV2DirectOriginError("DIRECT_ORIGIN_NOT_ISSUED")
        for name, value in (
            ("_admission", admission),
            ("_arguments", arguments),
            ("_binding", binding),
            ("_context", context),
            ("_journal", journal),
            ("_owner", owner),
            ("_authority", authority),
            ("_recheck", recheck),
            ("_pid", os.getpid()),
            ("_thread", threading.current_thread()),
            ("_closed", False),
            ("_busy", False),
        ):
            object.__setattr__(self, name, value)

    def __setattr__(self, name, value):
        raise AttributeError("direct origin admission is immutable")

    def __repr__(self) -> str:
        return "<GatewayRiskV2DirectAdmission>"

    def __reduce_ex__(self, _protocol):
        raise TypeError("direct origin admission cannot be serialized")

    def __copy__(self):
        raise TypeError("direct origin admission cannot be copied")

    def __deepcopy__(self, _memo):
        raise TypeError("direct origin admission cannot be copied")

    @property
    def binding(self):
        """Return immutable claims; they are not provider facts or authorization."""
        if not self._current(self._binding):
            raise GatewayRiskV2DirectOriginError("DIRECT_ORIGIN_UNAVAILABLE")
        return self._binding

    def _current(self, binding) -> bool:
        if (
            self._closed
            or self._busy
            or os.getpid() != self._pid
            or threading.current_thread() is not self._thread
        ):
            return False
        object.__setattr__(self, "_busy", True)
        try:
            from bt_api_gateway.risk_v2_binding import RiskV2DirectBinding

            source = self._admission
            recheck = self._recheck

            def current_binding():
                return _derive_binding(
                    self._context,
                    self._arguments,
                    self._admission,
                    self._journal,
                    self._owner,
                    self._authority,
                )

            if type(binding) is not RiskV2DirectBinding or binding != self._binding:
                return False
            if not source.matches_scope_recheck(recheck) or current_binding() != binding:
                return False
            recheck()
            return (
                not self._closed
                and os.getpid() == self._pid
                and threading.current_thread() is self._thread
                and self._admission is source
                and self._recheck is recheck
                and source.matches_scope_recheck(recheck)
                and self._binding == binding
                and current_binding() == binding
            )
        except Exception:
            return False
        finally:
            object.__setattr__(self, "_busy", False)


def _derive_binding(context, arguments, admission, journal, owner, authority):
    if (
        type(context) is not ServiceCallContext
        or type(admission) is not ServiceDispatchAdmission
        or type(journal) is not GatewayDirectCommandJournal
        or type(owner) is not GatewayDirectOwner
        or type(authority) is not GatewayRiskV2DirectAuthority
        or not authority._matches(admission, owner)
        or not isinstance(arguments, Mapping)
        or not context._is_registry_wire_arguments(arguments)
        or context.dispatch_admission is not admission
        or not admission.matches_context(context)
        or not admission.matches_operation("direct.reserve")
        or not admission.matches(arguments)
    ):
        raise ValueError
    identity, scope = context.identity, context.scope
    if (
        type(identity) is not WireIdentity
        or type(scope) is not WireScope
        or identity.role is not WireRole.STRATEGY_CLIENT
        or identity.principal_ref != context.principal_ref
        or identity.actor_ref != scope.strategy_ref
        or identity.delegate_ref is not None
        or scope.account_ref != journal.account_scope
        or scope.strategy_ref is None
        or scope.run_id is None
        or scope.account_refs
        or scope.manual_book_ref is not None
        or scope.portfolio_ref is not None
        or scope.group_id is not None
        or scope.leg_id is not None
        or set(arguments) != {"request_key", "payload", "ttl_seconds"}
        or not isinstance(arguments["payload"], Mapping)
        or type(arguments["request_key"]) is not str
        or not arguments["request_key"]
        or type(arguments["ttl_seconds"]) not in (int, float)
        or not math.isfinite(arguments["ttl_seconds"])
        or not 0 < arguments["ttl_seconds"] <= 30
        or owner._thread is not threading.current_thread()
        or not owner.is_bound_to(journal)
    ):
        raise ValueError
    payload = dict(arguments["payload"])
    order = decode_gateway_order_request(payload, scope.account_ref)
    if order.idempotency_key != arguments["request_key"]:
        raise ValueError
    owner_binding = owner.binding()
    if owner_binding is None or (
        owner_binding.account_scope,
        owner_binding.journal_id,
        owner_binding.generation,
    ) != (journal.account_scope, journal.journal_id, journal.generation):
        raise ValueError
    from bt_api_gateway.risk_v2_binding import RiskV2DirectBinding

    return RiskV2DirectBinding(
        account_scope=scope.account_ref,
        journal_id=journal.journal_id,
        owner_generation=journal.generation,
        strategy_scope=scope.strategy_ref,
        execution_domain_ref=scope.execution_domain_ref,
        run_id=scope.run_id,
        principal_ref=context.principal_ref,
        actor_ref=identity.actor_ref,
        registration_id=context.registration_id,
        service_epoch=admission.service_epoch,
        request_key=arguments["request_key"],
        payload_digest=gateway_payload_digest(payload),
    )


@contextmanager
def risk_v2_direct_admission_scope(
    *,
    context: ServiceCallContext,
    arguments: Mapping,
    admission: ServiceDispatchAdmission,
    journal: GatewayDirectCommandJournal,
    owner: GatewayDirectOwner,
    authority: GatewayRiskV2DirectAuthority,
) -> Iterator[GatewayRiskV2DirectAdmission]:
    """Bind one authenticated reserve to a live Registry scope on its owner thread."""
    try:
        _derive_binding(context, arguments, admission, journal, owner, authority)
    except Exception:
        raise GatewayRiskV2DirectOriginError("DIRECT_ORIGIN_UNAVAILABLE") from None

    with admission.scope() as recheck, risk_v2_direct_existing_scope(
        context=context, arguments=arguments, admission=admission, journal=journal,
        owner=owner, authority=authority, recheck=recheck,
    ) as result:
        yield result


@contextmanager
def risk_v2_direct_existing_scope(
    *,
    context: ServiceCallContext,
    arguments: Mapping,
    admission: ServiceDispatchAdmission,
    journal: GatewayDirectCommandJournal,
    owner: GatewayDirectOwner,
    authority: GatewayRiskV2DirectAuthority,
    recheck,
) -> Iterator[GatewayRiskV2DirectAdmission]:
    """Bind to this admission's already-active owner-thread Registry scope.

    This does not enter, extend, or close that scope and does not enable writes.
    The Registry itself must have issued the exact active recheck callback.
    """
    try:
        if type(admission) is not ServiceDispatchAdmission or not admission.matches_scope_recheck(recheck):
            raise ValueError
        recheck()
        binding = _derive_binding(context, arguments, admission, journal, owner, authority)
        result = GatewayRiskV2DirectAdmission(
            _ISSUER,
            admission=admission,
            arguments=arguments,
            authority=authority,
            binding=binding,
            context=context,
            journal=journal,
            owner=owner,
            recheck=recheck,
        )
        if not result._current(binding):
            raise ValueError
    except Exception:
        raise GatewayRiskV2DirectOriginError("DIRECT_ORIGIN_UNAVAILABLE") from None
    try:
        yield result
    finally:
        object.__setattr__(result, "_closed", True)
