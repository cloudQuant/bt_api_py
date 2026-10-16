"""Fixed, lazy factories for the SDK's selected service components.

This module contains no domain-package imports at module load time.  A
component package is imported only when its fixed name appears in a parsed
service manifest.  The manifest can select a built-in component; it cannot
name Python modules, factories, handlers, or backend objects.

The current runtime exposes implemented read adapters and explicitly
selected monitor ingress roles. Other manifest ports are either
internal objects supplied by the trusted resource provider or fail closed
before the listener is opened.
"""

from __future__ import annotations

import errno
import importlib
import os
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, Protocol

from bt_api_base.account_protocol import AccountResourceKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from .service_registry import (
    AccountRiskPolicyBinding,
    ActiveRegistrationContext,
    ServiceCallContext,
    ServiceCapability,
    ServiceOperationDeniedError,
    ServiceResultTooLargeError,
    TrustedGroupReservationPolicy,
    TrustedGroupResolutionPolicy,
    TrustedGroupWritePolicy,
)

if TYPE_CHECKING:
    from bt_api_risk.core.admission import AccountScope
    from .service_risk_order_entry import RiskOrderTarget


class ServiceComponentError(ValueError):
    """Finite component-construction error without resource or config values."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ResourceOwnership(str, Enum):  # noqa: UP042 -- Python 3.9 compatibility.
    """Whether a trusted provider resource is owned by the host."""

    OWNED = "owned"
    BORROWED = "borrowed"


@dataclass(frozen=True, slots=True)
class TrustedRiskPolicyBinding:
    """Trusted alias from a configured reference to one actual Risk policy.

    The policy object is resolved by the operator resource provider and must
    be the exact object exposed by the gate passed to the Risk adapter.
    """

    reference: str
    scope: WireScope
    policy: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if (
            type(self.reference) is not str
            or not self.reference
            or self.reference != self.reference.strip()
            or len(self.reference.encode("utf-8", errors="strict")) > 128
        ):
            raise ServiceComponentError("INVALID_RISK_POLICY_BINDING")
        if type(self.scope) is not WireScope or self.scope.run_id is not None:
            raise ServiceComponentError("INVALID_RISK_POLICY_BINDING")
        if self.policy is None:
            raise ServiceComponentError("INVALID_RISK_POLICY_BINDING")


@dataclass(frozen=True, slots=True)
class TrustedSharedCollateralDomain:
    """Operator-resolved collateral-domain declaration for one Host.

    The manifest labels and expected members describe the trusted deployment
    configuration; they do not authenticate an owner.  ``owner_gate`` and
    every target in ``member_targets`` are object-identity pins supplied by
    the trusted resource provider and are checked against the fully built
    component graph before the Registry or listener starts.
    """

    domain_id: str
    revision: str
    member_scopes: tuple[AccountScope, ...]
    member_targets: tuple[tuple[WireScope, RiskOrderTarget], ...]
    max_notional: Decimal | None
    owner_instance_id: str | None
    owner_gate: object | None = field(default=None, repr=False, compare=False)


class BorrowedServiceClient:
    """Call-only view of the Host's already-started upstream runtime.

    The Host retains lifecycle ownership. Component adapters receive no
    ``start``, ``maintain``, ``close`` or recovery API, and this facade is
    pinned to the constructing owner thread.
    """

    __slots__ = ("_runtime", "_owner_thread")

    def __init__(self, runtime: object) -> None:
        call = getattr(runtime, "call", None)
        state = getattr(runtime, "state", None)
        if not callable(call) or state != "registered":
            raise ServiceComponentError("UPSTREAM_CLIENT_NOT_READY")
        try:
            healthy = runtime.healthy  # type: ignore[attr-defined]
        except Exception:
            healthy = False
        if healthy is not True:
            raise ServiceComponentError("UPSTREAM_CLIENT_NOT_READY")
        self._runtime = runtime
        self._owner_thread = threading.get_ident()

    def call(
        self,
        mount: str,
        operation: str,
        arguments: Mapping[str, Any],
        *,
        account_ref: str | None = None,
        portfolio_ref: str | None = None,
        scope_ref: str | None = None,
        max_reply_message_bytes: int | None = None,
    ) -> Mapping[str, Any]:
        if threading.get_ident() != self._owner_thread:
            raise ServiceComponentError("OWNER_THREAD_REQUIRED")
        return self._runtime.call(  # type: ignore[attr-defined, no-any-return]
            mount,
            operation,
            arguments,
            account_ref=account_ref,
            portfolio_ref=portfolio_ref,
            scope_ref=scope_ref,
            max_reply_message_bytes=max_reply_message_bytes,
        )


def _borrowed_noop() -> None:
    """Explicit release callback for resources whose lifetime is borrowed."""


@dataclass(frozen=True, slots=True)
class ComponentResources:
    """Trusted resources resolved by the operator when a host starts.

    ``values`` contains only the exact constructor inputs for the selected
    built-in adapter. ``internal_ports`` contains trusted in-process objects;
    these are never converted into RPC capabilities. Borrowed resources must
    be created with :meth:`borrowed`, which installs an explicit no-op release
    callback. Owned resources require a provider-supplied close callback. An
    owned callback must be safe to retry if it raises; the host retains that
    resource and may call it again during a later cleanup attempt.
    """

    values: Mapping[str, object]
    ready: Callable[[], bool] = field(repr=False, compare=False)
    close_resource: Callable[[], None] = field(repr=False, compare=False)
    internal_ports: Mapping[str, object] = field(default_factory=dict)
    ownership: ResourceOwnership = ResourceOwnership.BORROWED
    risk_policy_bindings: tuple[TrustedRiskPolicyBinding, ...] = ()
    shared_collateral_domains: tuple[TrustedSharedCollateralDomain, ...] = ()
    _release_once: Callable[[], None] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        values = _copy_string_mapping(self.values, "INVALID_RESOURCE_VALUES")
        internal = _copy_string_mapping(self.internal_ports, "INVALID_INTERNAL_PORTS")
        if not callable(self.ready):
            raise ServiceComponentError("INVALID_READINESS_CHECK")
        if type(self.ownership) is not ResourceOwnership:
            raise ServiceComponentError("INVALID_RESOURCE_OWNERSHIP")
        if not callable(self.close_resource):
            raise ServiceComponentError("INVALID_RESOURCE_CLOSE")
        if type(self.risk_policy_bindings) is not tuple or any(
            type(binding) is not TrustedRiskPolicyBinding
            for binding in self.risk_policy_bindings
        ):
            raise ServiceComponentError("INVALID_RISK_POLICY_BINDING")
        if type(self.shared_collateral_domains) is not tuple or any(
            type(domain) is not TrustedSharedCollateralDomain
            for domain in self.shared_collateral_domains
        ):
            raise ServiceComponentError("INVALID_SHARED_COLLATERAL_DECLARATION")
        if self.ownership is ResourceOwnership.BORROWED:
            if self.close_resource is not _borrowed_noop:
                raise ServiceComponentError("BORROWED_RESOURCE_REQUIRES_EXPLICIT_NOOP")
        elif self.close_resource is _borrowed_noop:
            raise ServiceComponentError("OWNED_RESOURCE_REQUIRES_CLOSE")

        object.__setattr__(self, "values", MappingProxyType(values))
        object.__setattr__(self, "internal_ports", MappingProxyType(internal))
        close = self.close_resource
        closed = False
        close_lock = threading.Lock()

        def release_once() -> None:
            nonlocal closed
            with close_lock:
                if closed:
                    return
                close()
                closed = True

        object.__setattr__(self, "_release_once", release_once)

    @classmethod
    def borrowed(
        cls,
        values: Mapping[str, object],
        *,
        ready: Callable[[], bool],
        internal_ports: Mapping[str, object] | None = None,
        risk_policy_bindings: tuple[TrustedRiskPolicyBinding, ...] = (),
        shared_collateral_domains: tuple[TrustedSharedCollateralDomain, ...] = (),
    ) -> ComponentResources:
        """Describe operator-owned objects whose lifetime remains external."""

        return cls(
            values=values,
            ready=ready,
            internal_ports={} if internal_ports is None else internal_ports,
            ownership=ResourceOwnership.BORROWED,
            risk_policy_bindings=risk_policy_bindings,
            shared_collateral_domains=shared_collateral_domains,
            close_resource=_borrowed_noop,
        )

    @classmethod
    def owned(
        cls,
        values: Mapping[str, object],
        *,
        ready: Callable[[], bool],
        close: Callable[[], None],
        internal_ports: Mapping[str, object] | None = None,
        risk_policy_bindings: tuple[TrustedRiskPolicyBinding, ...] = (),
        shared_collateral_domains: tuple[TrustedSharedCollateralDomain, ...] = (),
    ) -> ComponentResources:
        """Describe host-owned objects and their explicit cleanup callback."""

        return cls(
            values=values,
            ready=ready,
            internal_ports={} if internal_ports is None else internal_ports,
            ownership=ResourceOwnership.OWNED,
            risk_policy_bindings=risk_policy_bindings,
            shared_collateral_domains=shared_collateral_domains,
            close_resource=close,
        )

    def close(self) -> None:
        """Release owned resources once; borrowed resources use their no-op."""

        self._release_once()


class ServiceResourceProvider(Protocol):
    """Trusted operator hook for fixed component resources, called at start."""

    def __call__(
        self,
        component: str,
        instance_id: str,
        options: Mapping[str, object],
        scope: WireScope,
    ) -> ComponentResources:
        """Resolve trusted resources for one selected manifest instance."""


@dataclass(frozen=True, slots=True)
class ComponentSpec:
    """One normalized parser-created component selection."""

    component: str
    instance_id: str
    bind_ports: tuple[str, ...]
    options: Mapping[str, object]

    def __post_init__(self) -> None:
        if type(self.component) is not str or type(self.instance_id) is not str:
            raise ServiceComponentError("INVALID_COMPONENT_SPEC")
        if type(self.bind_ports) is not tuple or not self.bind_ports:
            raise ServiceComponentError("INVALID_COMPONENT_SPEC")
        if any(type(port) is not str for port in self.bind_ports):
            raise ServiceComponentError("INVALID_COMPONENT_SPEC")
        if not isinstance(self.options, Mapping):
            raise ServiceComponentError("INVALID_COMPONENT_SPEC")
        object.__setattr__(self, "options", MappingProxyType(dict(self.options)))


@dataclass(frozen=True, slots=True)
class PreparedComponent:
    """A selected component whose pure package options check has passed."""

    spec: ComponentSpec
    options: Mapping[str, object]
    module: object = field(repr=False, compare=False)
    operations_by_port: Mapping[str, frozenset[str]]
    write_operations_by_port: Mapping[str, frozenset[str]]


@dataclass(frozen=True, slots=True)
class ServiceComponent:
    """A concrete component with exact public handlers and internal objects."""

    component: str
    instance_id: str
    capabilities: tuple[ServiceCapability, ...]
    handlers: Mapping[
        str,
        Callable[[ServiceCallContext, Mapping[str, Any]], Mapping[str, Any]],
    ]
    internal_ports: Mapping[str, object]
    readiness: Callable[[], bool] = field(repr=False, compare=False)
    close: Callable[[], None] = field(repr=False, compare=False)
    maintain: Callable[[frozenset[str]], object] | None = field(
        default=None, repr=False, compare=False
    )
    maintain_registrations: Callable[[tuple[ActiveRegistrationContext, ...]], object] | None = (
        field(default=None, repr=False, compare=False)
    )
    account_risk_policy_bindings: tuple[AccountRiskPolicyBinding, ...] = ()
    account_risk_policy_required_ports: frozenset[str] = frozenset()
    shared_collateral_domains: tuple[TrustedSharedCollateralDomain, ...] = field(
        default=(), repr=False, compare=False
    )
    risk_owner_gate: object | None = field(default=None, repr=False, compare=False)
    risk_owner_targets: Mapping[WireScope, object] = field(
        default_factory=dict, repr=False, compare=False
    )
    risk_owner_admission: Mapping[WireScope, object] = field(
        default_factory=dict, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if type(self.capabilities) is not tuple or any(
            type(capability) is not ServiceCapability for capability in self.capabilities
        ):
            raise ServiceComponentError("INVALID_COMPONENT_CAPABILITIES")
        if any(capability.instance_id != self.instance_id for capability in self.capabilities):
            raise ServiceComponentError("INVALID_COMPONENT_CAPABILITIES")
        handlers = _copy_callable_mapping(self.handlers, "INVALID_COMPONENT_HANDLERS")
        declared = frozenset(
            operation for capability in self.capabilities for operation in capability.operations
        )
        if set(handlers) != set(declared):
            raise ServiceComponentError("HANDLER_CAPABILITY_MISMATCH")
        internal = _copy_string_mapping(self.internal_ports, "INVALID_INTERNAL_PORTS")
        if not callable(self.readiness) or not callable(self.close):
            raise ServiceComponentError("INVALID_COMPONENT_LIFECYCLE")
        if self.maintain is not None and not callable(self.maintain):
            raise ServiceComponentError("INVALID_COMPONENT_LIFECYCLE")
        if self.maintain_registrations is not None and not callable(self.maintain_registrations):
            raise ServiceComponentError("INVALID_COMPONENT_LIFECYCLE")
        if type(self.account_risk_policy_bindings) is not tuple or any(
            type(binding) is not AccountRiskPolicyBinding
            for binding in self.account_risk_policy_bindings
        ):
            raise ServiceComponentError("INVALID_ACCOUNT_RISK_POLICY_BINDINGS")
        if type(self.account_risk_policy_required_ports) is not frozenset or any(
            type(port) is not str or not port for port in self.account_risk_policy_required_ports
        ):
            raise ServiceComponentError("INVALID_ACCOUNT_RISK_POLICY_BINDINGS")
        capability_ports = {capability.port for capability in self.capabilities}
        if not self.account_risk_policy_required_ports.issubset(capability_ports):
            raise ServiceComponentError("INVALID_ACCOUNT_RISK_POLICY_BINDINGS")
        if any(binding.instance_id != self.instance_id for binding in self.account_risk_policy_bindings):
            raise ServiceComponentError("INVALID_ACCOUNT_RISK_POLICY_BINDINGS")
        binding_scopes = [binding.scope for binding in self.account_risk_policy_bindings]
        if len(binding_scopes) != len(set(binding_scopes)):
            raise ServiceComponentError("INVALID_ACCOUNT_RISK_POLICY_BINDINGS")
        if self.account_risk_policy_required_ports - {"service.order_entry"}:
            raise ServiceComponentError("INVALID_ACCOUNT_RISK_POLICY_BINDINGS")
        if self.account_risk_policy_required_ports and not self.account_risk_policy_bindings:
            raise ServiceComponentError("INVALID_ACCOUNT_RISK_POLICY_BINDINGS")
        if type(self.shared_collateral_domains) is not tuple or any(
            type(domain) is not TrustedSharedCollateralDomain
            for domain in self.shared_collateral_domains
        ):
            raise ServiceComponentError("INVALID_SHARED_COLLATERAL_DECLARATION")
        if not isinstance(self.risk_owner_targets, Mapping) or not isinstance(
            self.risk_owner_admission, Mapping
        ):
            raise ServiceComponentError("INVALID_SHARED_COLLATERAL_OWNER")
        owner_targets = dict(self.risk_owner_targets)
        owner_admission = dict(self.risk_owner_admission)
        if any(
            type(scope) is not WireScope or target is None
            for scope, target in owner_targets.items()
        ) or any(
            type(scope) is not WireScope or admission is None
            for scope, admission in owner_admission.items()
        ):
            raise ServiceComponentError("INVALID_SHARED_COLLATERAL_OWNER")
        if self.risk_owner_gate is None:
            if owner_targets or owner_admission:
                raise ServiceComponentError("INVALID_SHARED_COLLATERAL_OWNER")
        elif not owner_targets or set(owner_targets) != set(owner_admission):
            raise ServiceComponentError("INVALID_SHARED_COLLATERAL_OWNER")
        object.__setattr__(self, "handlers", MappingProxyType(handlers))
        object.__setattr__(self, "internal_ports", MappingProxyType(internal))
        object.__setattr__(self, "risk_owner_targets", MappingProxyType(owner_targets))
        object.__setattr__(self, "risk_owner_admission", MappingProxyType(owner_admission))


@dataclass(frozen=True, slots=True)
class _Builtin:
    module_name: str
    operations_attribute: tuple[str, ...]
    resource_keys: frozenset[str]
    public_ports: Mapping[str, frozenset[str]]
    internal_ports: frozenset[str]
    write_operations_by_port: Mapping[str, frozenset[str]] = field(
        default_factory=lambda: MappingProxyType({})
    )
    validator_module_name: str | None = None
    optional_resource_keys: frozenset[str] = frozenset()


_BUILTINS: Mapping[str, _Builtin] = MappingProxyType(
    {
        "gateway": _Builtin(
            module_name="bt_api_gateway.service_component",
            operations_attribute=("SUPPORTED_OPERATIONS",),
            resource_keys=frozenset({"journal", "accounts", "principals"}),
            public_ports=MappingProxyType(
                {
                    "service.account_read": frozenset(
                        {
                            "account.read_after",
                            "account.snapshot",
                            "account.head",
                            "account.ack",
                            "account.progress",
                        }
                    )
                }
            ),
            internal_ports=frozenset({"service.provider_session", "service.owner_control"}),
        ),
        "execution": _Builtin(
            module_name="bt_api_execution.service_component",
            operations_attribute=("SUPPORTED_OPERATIONS",),
            resource_keys=frozenset({"store", "targets"}),
            public_ports=MappingProxyType({"service.order_query": frozenset({"orders.query"})}),
            internal_ports=frozenset({"service.owner_control"}),
        ),
        "monitor": _Builtin(
            module_name="bt_api_monitor.service_component",
            operations_attribute=("MONITOR_READ_OPERATIONS",),
            resource_keys=frozenset({"outbox", "targets"}),
            public_ports=MappingProxyType(
                {"service.monitor_query": frozenset({"monitor.query", "monitor.read_after"})}
            ),
            internal_ports=frozenset(),
        ),
        "risk": _Builtin(
            module_name="bt_api_risk.service_component",
            operations_attribute=("RiskReadService", "SUPPORTED_OPERATIONS"),
            resource_keys=frozenset({"gate", "targets"}),
            public_ports=MappingProxyType({"service.risk_query": frozenset({"risk.snapshot"})}),
            internal_ports=frozenset(
                {
                    "service.group_reservations",
                    "service.order_admission",
                    "service.owner_control",
                    "service.group_admission",
                }
            ),
        ),
    }
)

_ROLE_BUILTINS: Mapping[tuple[str, str], _Builtin] = MappingProxyType(
    {
        (
            "gateway",
            "market_hub",
        ): _Builtin(
            module_name="bt_api_gateway.market_hub",
            operations_attribute=("ALL_MARKET_OPERATIONS",),
            resource_keys=frozenset({"hub"}),
            public_ports=MappingProxyType(
                {
                    "service.market_source": frozenset(
                        {
                            "market.subscribe",
                            "market.unsubscribe",
                            "market.snapshot",
                            "market.head",
                            "market.read_after",
                            "market.history",
                        }
                    )
                }
            ),
            internal_ports=frozenset(),
            validator_module_name="bt_api_gateway.service_component",
        ),
        (
            "monitor",
            "durable_ingress",
        ): _Builtin(
            module_name="bt_api_monitor.service_ingress",
            operations_attribute=("MONITOR_INGRESS_OPERATIONS",),
            resource_keys=frozenset({"outbox", "targets"}),
            public_ports=MappingProxyType(
                {"service.telemetry_ingress": frozenset({"events.append"})}
            ),
            internal_ports=frozenset(),
        ),
        ("monitor", "telemetry_ingress"): _Builtin(
            module_name="bt_api_monitor.service_telemetry",
            operations_attribute=("TELEMETRY_OPERATIONS",),
            resource_keys=frozenset({"targets"}),
            public_ports=MappingProxyType(
                {
                    "service.telemetry_ingress": frozenset({"telemetry.append"}),
                    "service.monitor_query": frozenset({"telemetry.snapshot"}),
                }
            ),
            internal_ports=frozenset(),
        ),
        ("monitor", "control"): _Builtin(
            module_name="bt_api_py.service_monitor_control",
            operations_attribute=("MonitorControlRuntime", "OPERATIONS"),
            resource_keys=frozenset({"service", "owners"}),
            public_ports=MappingProxyType(
                {"service.monitor_control": frozenset({"monitor.control"})}
            ),
            internal_ports=frozenset(),
            validator_module_name="bt_api_monitor.service_control",
        ),
        ("execution", "account_owner"): _Builtin(
            module_name="bt_api_py.service_execution_account_books",
            operations_attribute=("SUPPORTED_OPERATIONS",),
            resource_keys=frozenset({"store", "targets", "dispatch", "admission"}),
            public_ports=MappingProxyType(
                {
                    "service.order_entry": frozenset(
                        {
                            "orders.submit",
                            "orders.cancel",
                            "orders.submit_approved_target",
                        }
                    ),
                    "service.order_query": frozenset(
                        {"orders.query", "execution.account_books.query"}
                    ),
                }
            ),
            internal_ports=frozenset({"service.owner_control"}),
            write_operations_by_port=MappingProxyType(
                {
                    "service.order_entry": frozenset(
                        {
                            "orders.submit",
                            "orders.cancel",
                            "orders.submit_approved_target",
                        }
                    )
                }
            ),
            validator_module_name="bt_api_execution.service_component",
        ),
        ("execution", "group_account_owner"): _Builtin(
            module_name="bt_api_py.service_execution_group",
            operations_attribute=("GROUP_ACCOUNT_OWNER_OPERATIONS",),
            resource_keys=frozenset(
                {
                    "store",
                    "dispatch",
                    "accepted_group_store",
                    "group_authority",
                    "group_runtime_bundle",
                }
            ),
            public_ports=MappingProxyType(
                {
                    "service.order_entry": frozenset(
                        {"orders.submit_group_leg", "orders.resolve_group_leg"}
                    ),
                    "service.order_query": frozenset({"orders.query_group_leg"}),
                }
            ),
            internal_ports=frozenset(),
            write_operations_by_port=MappingProxyType(
                {
                    "service.order_entry": frozenset(
                        {"orders.submit_group_leg", "orders.resolve_group_leg"}
                    )
                }
            ),
        ),
        ("execution", "group_coordinator"): _Builtin(
            module_name="bt_api_py.service_execution_group",
            operations_attribute=("GROUP_COORDINATOR_OPERATIONS",),
            resource_keys=frozenset({"store", "targets", "run_bindings"}),
            public_ports=MappingProxyType(
                {"service.order_group_entry": frozenset({"groups.submit", "groups.query"})}
            ),
            internal_ports=frozenset(),
            write_operations_by_port=MappingProxyType(
                {"service.order_group_entry": frozenset({"groups.submit"})}
            ),
        ),
        ("risk", "group_reservations"): _Builtin(
            module_name="bt_api_py.service_group_reservations",
            operations_attribute=("SUPPORTED_OPERATIONS",),
            resource_keys=frozenset({"accepted_group_store", "group_runtime_bundle"}),
            public_ports=MappingProxyType(
                {
                    "service.group_reservations": frozenset(
                        {
                            "reservations.reserve",
                            "reservations.query",
                            "reservations.release",
                        }
                    )
                }
            ),
            internal_ports=frozenset(),
            write_operations_by_port=MappingProxyType(
                {
                    "service.group_reservations": frozenset(
                        {"reservations.reserve", "reservations.release"}
                    )
                }
            ),
        ),
        ("risk", "policy_catalog"): _Builtin(
            module_name="bt_api_risk.service_component",
            operations_attribute=("SUPPORTED_OPERATIONS",),
            resource_keys=frozenset({"catalog"}),
            optional_resource_keys=frozenset({"confirmed_policy_store"}),
            public_ports=MappingProxyType(
                {
                    "service.risk_policy": frozenset(
                        {
                            "policy.read",
                            "policy.draft",
                            "policy.approve",
                            "policy.publish",
                            "policy.receipt",
                            "policy.budget.propose",
                            "policy.budget.approve",
                            "policy.budget.publish_member",
                            "policy.budget.confirm_member",
                            "policy.budget.reject_member",
                            "policy.budget.mark_unknown",
                            "policy.budget.retract",
                            "policy.budget.read",
                        }
                    )
                }
            ),
            internal_ports=frozenset(),
            write_operations_by_port=MappingProxyType(
                {
                    "service.risk_policy": frozenset(
                        {
                            "policy.draft", "policy.approve", "policy.publish",
                            "policy.budget.propose", "policy.budget.approve",
                            "policy.budget.publish_member", "policy.budget.confirm_member",
                            "policy.budget.reject_member", "policy.budget.mark_unknown",
                            "policy.budget.retract",
                        }
                    )
                }
            ),
        ),
        ("risk", "portfolio_analytics"): _Builtin(
            module_name="bt_api_risk.service_analytics",
            operations_attribute=("SUPPORTED_OPERATIONS",),
            resource_keys=frozenset({"analysis"}),
            public_ports=MappingProxyType(
                {
                    "service.risk_analysis": frozenset(
                        {
                            "analysis.submit",
                            "analysis.query",
                            "analysis.cancel",
                            "analysis.input_upload",
                        }
                    )
                }
            ),
            internal_ports=frozenset(),
            write_operations_by_port=MappingProxyType(
                {
                    "service.risk_analysis": frozenset(
                        {"analysis.submit", "analysis.cancel", "analysis.input_upload"}
                    )
                }
            ),
            validator_module_name="bt_api_risk.service_component",
        ),
        ("risk", "group_policy"): _Builtin(
            module_name="bt_api_risk.service_component",
            operations_attribute=("GroupPolicyService", "SUPPORTED_OPERATIONS"),
            resource_keys=frozenset(),
            public_ports=MappingProxyType({}),
            internal_ports=frozenset({"service.group_admission"}),
        ),
        ("risk", "account_owner"): _Builtin(
            module_name="bt_api_py.service_risk_order_entry",
            operations_attribute=("SUPPORTED_OPERATIONS",),
            resource_keys=frozenset(
                {"bt_api", "targets", "admission", "journal", "gate", "provider_contracts"}
            ),
            public_ports=MappingProxyType(
                {
                    "service.order_entry": frozenset({"orders.submit"}),
                    "service.order_query": frozenset({"orders.query"}),
                }
            ),
            internal_ports=frozenset(),
            write_operations_by_port=MappingProxyType(
                {"service.order_entry": frozenset({"orders.submit"})}
            ),
        ),
    }
)


_ADMISSION_KEYS: Final[frozenset[str]] = frozenset(
    {"order_gate", "cancel_gate", "basic_admission_gate", "allow_unprotected"}
)

_EXECUTION_OWNER_LOCK_GUARD = threading.Lock()
_EXECUTION_OWNER_LOCK_PATHS: set[str] = set()
_EXECUTION_OWNER_LOCK_FILES: dict[str, Any] = {}
_EXECUTION_OWNER_MEMORY_STORES: dict[int, object] = {}


def _forget_execution_owner_path(key: str, owner_file: Any) -> None:
    if not owner_file.closed:
        return
    with _EXECUTION_OWNER_LOCK_GUARD:
        if _EXECUTION_OWNER_LOCK_FILES.get(key) is owner_file:
            _EXECUTION_OWNER_LOCK_FILES.pop(key, None)
        if key not in _EXECUTION_OWNER_LOCK_FILES:
            _EXECUTION_OWNER_LOCK_PATHS.discard(key)


class _ExecutionOwnerFileLock:
    """Retain advisory ownership of one resolved execution-store pathname."""

    def __init__(self, owner_file: Any, key: str) -> None:
        self._owner_file = owner_file
        self._key = key

    def close(self) -> None:
        owner_file = self._owner_file
        if owner_file is None:
            return
        try:
            # Closing the still-locked descriptor releases the OS lock. Do not
            # explicitly unlock first: if close then fails, another process
            # must not enter while this owner still has an open descriptor.
            owner_file.close()
        except BaseException:
            if owner_file.closed:
                self._owner_file = None
            _forget_execution_owner_path(self._key, owner_file)
            raise
        if not owner_file.closed:
            raise ServiceComponentError("ORDER_OWNER_LOCK_RELEASE_FAILED")
        self._owner_file = None
        _forget_execution_owner_path(self._key, owner_file)


class _ExecutionOwnerMemoryLock:
    """Exclude another local owner reusing the same in-memory store object."""

    def __init__(self, store: object) -> None:
        self._store: object | None = store

    def close(self) -> None:
        store = self._store
        if store is None:
            return
        key = id(store)
        with _EXECUTION_OWNER_LOCK_GUARD:
            if _EXECUTION_OWNER_MEMORY_STORES.get(key) is store:
                _EXECUTION_OWNER_MEMORY_STORES.pop(key, None)
        self._store = None


def _acquire_execution_owner_lock(
    store: object,
) -> _ExecutionOwnerFileLock | _ExecutionOwnerMemoryLock:
    """Lock the resolved database pathname, or object identity for memory stores.

    The sidecar protocol is advisory and pathname-based. Symlink aliases resolve
    to one key; hard links and replacement or unlinking of the sidecar are
    outside its exclusion guarantee.
    """

    raw_path = getattr(store, "path", None)
    if type(raw_path) is not str or not raw_path:
        raise ServiceComponentError("ORDER_OWNER_LOCK_UNAVAILABLE")
    if raw_path == ":memory:":
        memory_lock = _ExecutionOwnerMemoryLock(store)
        key = id(store)
        with _EXECUTION_OWNER_LOCK_GUARD:
            if key in _EXECUTION_OWNER_MEMORY_STORES:
                raise ServiceComponentError("ORDER_OWNER_ALREADY_ACTIVE")
            _EXECUTION_OWNER_MEMORY_STORES[key] = store
        return memory_lock
    try:
        key = os.path.normcase(str(Path(raw_path).expanduser().resolve(strict=True)))
    except (OSError, RuntimeError, ValueError):
        raise ServiceComponentError("ORDER_OWNER_LOCK_UNAVAILABLE") from None

    with _EXECUTION_OWNER_LOCK_GUARD:
        if key in _EXECUTION_OWNER_LOCK_PATHS:
            raise ServiceComponentError("ORDER_OWNER_ALREADY_ACTIVE")
        _EXECUTION_OWNER_LOCK_PATHS.add(key)

    owner_file = None
    try:
        # Match ServiceRegistry's portable lifetime-lock protocol while using
        # the resolved database pathname, independent of registry_path.
        # This sidecar does not coordinate hard-link aliases or a sidecar
        # replaced or unlinked by another process with directory write access.
        owner_file = open(key + ".service_execution_owner.lock", "a+b")  # noqa: SIM115
        with _EXECUTION_OWNER_LOCK_GUARD:
            _EXECUTION_OWNER_LOCK_FILES[key] = owner_file
        owner_file.seek(0, os.SEEK_END)
        if owner_file.tell() == 0:
            owner_file.write(b"\0")
            owner_file.flush()
        owner_file.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(owner_file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(owner_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return _ExecutionOwnerFileLock(owner_file, key)
    except BaseException as error:
        if owner_file is not None:
            try:
                owner_file.close()
            except BaseException:
                # Keep both the in-process claim and descriptor reference until
                # a retry confirms that close actually completed.
                _forget_execution_owner_path(key, owner_file)
                raise ServiceComponentError("ORDER_OWNER_LOCK_RELEASE_FAILED") from None
            _forget_execution_owner_path(key, owner_file)
        else:
            with _EXECUTION_OWNER_LOCK_GUARD:
                _EXECUTION_OWNER_LOCK_PATHS.discard(key)
        if not isinstance(error, Exception):
            raise
        if isinstance(error, OSError) and error.errno in {
            errno.EACCES,
            errno.EAGAIN,
            errno.EDEADLK,
        }:
            raise ServiceComponentError("ORDER_OWNER_ALREADY_ACTIVE") from None
        raise ServiceComponentError("ORDER_OWNER_LOCK_UNAVAILABLE") from None


def _resources_with_execution_owner_lock(
    resources: ComponentResources,
    owner_lock: _ExecutionOwnerFileLock | _ExecutionOwnerMemoryLock | None,
) -> ComponentResources:
    if owner_lock is None:
        return resources

    def close_owned_resources() -> None:
        # Release the writer exclusion only after trusted provider cleanup has
        # completed. If cleanup fails, a later host retry still owns the lock.
        resources.close()
        owner_lock.close()

    return ComponentResources.owned(
        resources.values,
        ready=resources.ready,
        close=close_owned_resources,
        internal_ports=resources.internal_ports,
    )


def _builtin_for_spec(spec: ComponentSpec) -> _Builtin | None:
    role = spec.options.get("role")
    if type(role) is str:
        role_builtin = _ROLE_BUILTINS.get((spec.component, role))
        if role_builtin is not None:
            return role_builtin
    return _BUILTINS.get(spec.component)


def component_specs(components: Mapping[str, Any]) -> tuple[ComponentSpec, ...]:
    """Snapshot normalized component selections from a parsed service config."""

    if not isinstance(components, Mapping) or not components:
        raise ServiceComponentError("EMPTY_COMPONENTS")
    specs: list[ComponentSpec] = []
    seen_instances: set[str] = set()
    for component, raw in components.items():
        if type(component) is not str or not isinstance(raw, Mapping):
            raise ServiceComponentError("INVALID_COMPONENT_SPEC")
        settings = raw.get("settings")
        if not isinstance(settings, Mapping):
            raise ServiceComponentError("INVALID_COMPONENT_SPEC")
        instance_id = settings.get("instance_id")
        bind_ports = settings.get("bind_ports")
        options = settings.get("options", {})
        if (
            type(instance_id) is not str
            or type(bind_ports) not in (tuple, list)
            or not isinstance(options, Mapping)
        ):
            raise ServiceComponentError("INVALID_COMPONENT_SPEC")
        if instance_id in seen_instances:
            raise ServiceComponentError("DUPLICATE_COMPONENT_INSTANCE")
        seen_instances.add(instance_id)
        specs.append(
            ComponentSpec(
                component=component,
                instance_id=instance_id,
                bind_ports=tuple(bind_ports),
                options=options,
            )
        )
    return tuple(specs)


def validate_components(specs: Sequence[ComponentSpec]) -> tuple[PreparedComponent, ...]:
    """Validate every selected built-in before any resource provider call."""

    if type(specs) not in (tuple, list) or not specs:
        raise ServiceComponentError("EMPTY_COMPONENTS")
    prepared: list[PreparedComponent] = []
    seen_instances: set[str] = set()
    for spec in specs:
        if type(spec) is not ComponentSpec:
            raise ServiceComponentError("UNKNOWN_COMPONENT")
        if spec.instance_id in seen_instances:
            raise ServiceComponentError("DUPLICATE_COMPONENT_INSTANCE")
        seen_instances.add(spec.instance_id)
        builtin = _builtin_for_spec(spec)
        if builtin is None:
            raise ServiceComponentError("UNKNOWN_COMPONENT")
        try:
            module = importlib.import_module(builtin.module_name)
        except Exception:
            raise ServiceComponentError("BUILTIN_COMPONENT_UNAVAILABLE") from None
        validator_module = module
        if builtin.validator_module_name is not None:
            try:
                validator_module = importlib.import_module(builtin.validator_module_name)
            except Exception:
                raise ServiceComponentError("BUILTIN_COMPONENT_UNAVAILABLE") from None
        validator = getattr(validator_module, "validate_service_options", None)
        if not callable(validator):
            raise ServiceComponentError("BUILTIN_COMPONENT_INVALID")
        try:
            normalized = validator(spec.options, spec.bind_ports)
        except Exception:
            raise ServiceComponentError("COMPONENT_OPTIONS_INVALID") from None
        if not isinstance(normalized, Mapping):
            raise ServiceComponentError("BUILTIN_COMPONENT_INVALID")

        operations: object = module
        for attribute in builtin.operations_attribute:
            operations = getattr(operations, attribute, None)
        if type(operations) is not frozenset or any(type(item) is not str for item in operations):
            raise ServiceComponentError("BUILTIN_COMPONENT_INVALID")

        by_port: dict[str, frozenset[str]] = {}
        write_by_port: dict[str, frozenset[str]] = {}
        for port in spec.bind_ports:
            allowed = builtin.public_ports.get(port)
            if allowed is not None:
                supported = frozenset(allowed.intersection(operations))
                if not supported:
                    raise ServiceComponentError("PUBLIC_PORT_HAS_NO_SUPPORTED_OPERATIONS")
                write_operations = builtin.write_operations_by_port.get(port, frozenset())
                if type(write_operations) is not frozenset or not write_operations.issubset(
                    supported
                ):
                    raise ServiceComponentError("BUILTIN_COMPONENT_INVALID")
                by_port[port] = supported
                write_by_port[port] = write_operations
            elif port in builtin.internal_ports:
                continue
            else:
                raise ServiceComponentError("UNSUPPORTED_PUBLIC_PORT")
        prepared.append(
            PreparedComponent(
                spec=spec,
                options=MappingProxyType(dict(normalized)),
                module=module,
                operations_by_port=MappingProxyType(by_port),
                write_operations_by_port=MappingProxyType(write_by_port),
            )
        )
    return tuple(prepared)


def order_components(prepared: Sequence[PreparedComponent]) -> tuple[PreparedComponent, ...]:
    """Order the fixed same-service provider-session dependency first."""

    if type(prepared) not in (tuple, list) or not prepared:
        raise ServiceComponentError("EMPTY_COMPONENTS")
    by_instance = {item.spec.instance_id: item for item in prepared}
    if len(by_instance) != len(prepared):
        raise ServiceComponentError("DUPLICATE_COMPONENT_INSTANCE")
    dependencies: dict[str, set[str]] = {instance: set() for instance in by_instance}
    for item in prepared:
        if item.spec.component != "execution":
            continue
        dependency = item.options.get("provider_session_ref")
        if dependency is None:
            continue
        target = by_instance.get(dependency)
        if (
            target is None
            or target.spec.component != "gateway"
            or target.spec.options.get("role") != "account_session_worker"
        ):
            raise ServiceComponentError("PROVIDER_SESSION_REFERENCE_INVALID")
        dependencies[item.spec.instance_id].add(dependency)

    ordered: list[PreparedComponent] = []
    remaining = set(by_instance)
    while remaining:
        ready = sorted(
            instance for instance in remaining if not dependencies[instance].intersection(remaining)
        )
        if not ready:
            raise ServiceComponentError("COMPONENT_DEPENDENCY_CYCLE")
        for instance in ready:
            ordered.append(by_instance[instance])
            remaining.remove(instance)
    return tuple(ordered)


def _group_runtime_bundle(value: object) -> object:
    """Validate the provider-owned, group-only Risk/Execution composition."""

    try:
        module = importlib.import_module("bt_api_py.service_execution_group_proofs")
    except Exception:
        raise ServiceComponentError("GROUP_RUNTIME_BUNDLE_UNAVAILABLE") from None
    bundle_type = getattr(module, "GroupProofRuntimeBundle", None)
    if not isinstance(bundle_type, type) or type(value) is not bundle_type:
        raise ServiceComponentError("GROUP_RUNTIME_BUNDLE_INVALID")
    try:
        risk_admission = value.risk_admission  # type: ignore[attr-defined]
        account_scoped_admission = value.account_scoped_admission  # type: ignore[attr-defined]
        proof_source = value.proof_source  # type: ignore[attr-defined]
        reservation_targets = value.reservation_targets  # type: ignore[attr-defined]
        proof_targets = value.proof_targets  # type: ignore[attr-defined]
        reservation_target_for = value.reservation_target_for  # type: ignore[attr-defined]
        proof_target_for = value.proof_target_for  # type: ignore[attr-defined]
        execution_scope_for = value.execution_scope_for  # type: ignore[attr-defined]
        require_runtime_bundle = value.require_runtime_bundle  # type: ignore[attr-defined]
    except Exception:
        raise ServiceComponentError("GROUP_RUNTIME_BUNDLE_INVALID") from None
    if (
        type(risk_admission) is not getattr(module, "DurableRiskAdmissionAdapter", None)
        or type(account_scoped_admission)
        is not getattr(module, "AccountScopedGroupAdmissionAdapter", None)
        or type(proof_source) is not getattr(module, "GroupProofSource", None)
        or type(reservation_targets) is not MappingProxyType
        or type(proof_targets) is not MappingProxyType
        or not reservation_targets
        or set(reservation_targets) != set(proof_targets)
        or not callable(reservation_target_for)
        or not callable(proof_target_for)
        or not callable(execution_scope_for)
        or not callable(require_runtime_bundle)
    ):
        raise ServiceComponentError("GROUP_RUNTIME_BUNDLE_INVALID")
    target_for_template = getattr(proof_source, "target_for_template", None)
    reservation_target_type = getattr(module, "GroupReservationTarget", None)
    execution_scope_type = getattr(module, "ExecutionScope", None)
    account_scope_type = getattr(module, "AccountScope", None)
    risk_scope_fingerprint = getattr(module, "risk_account_scope_fingerprint", None)
    if (
        not callable(target_for_template)
        or not isinstance(reservation_target_type, type)
        or not isinstance(execution_scope_type, type)
        or not isinstance(account_scope_type, type)
        or not callable(risk_scope_fingerprint)
    ):
        raise ServiceComponentError("GROUP_RUNTIME_BUNDLE_INVALID")
    try:
        require_runtime_bundle(
            account_scoped_admission=account_scoped_admission,
            risk_admission=risk_admission,
        )
        account_scoped_admission.require_admission_gate(risk_admission)
        for wire_scope in reservation_targets:
            if (
                type(wire_scope) is not WireScope
                or wire_scope.group_id is not None
                or wire_scope.leg_id is not None
            ):
                raise TypeError("invalid group scope key")
            reservation_target = reservation_targets[wire_scope]
            if (
                type(reservation_target) is not reservation_target_type
                or reservation_target_for(wire_scope) is not reservation_target
                or reservation_target.admission is not risk_admission
                or type(reservation_target.account_scope) is not account_scope_type
                or account_scoped_admission.risk_scope_fingerprint
                != risk_scope_fingerprint(reservation_target.account_scope.key)
            ):
                raise TypeError("reservation target lookup changed identity")
            proof_target = proof_targets[wire_scope]
            if type(proof_target) is not getattr(module, "GroupProofTarget", None):
                raise TypeError("invalid group proof target")
            execution_scope = proof_target.execution_scope
            if (
                type(execution_scope) is not execution_scope_type
                or proof_target.account_scope != reservation_target.account_scope
                or execution_scope.account_ref != wire_scope.account_ref
                or execution_scope.strategy_id != wire_scope.strategy_ref
            ):
                raise TypeError("group proof target scope binding changed")
            # Bundle keys are stable account templates. The public
            # proof_target_for()/execution_scope_for() methods resolve a
            # per-leg child scope and correctly reject this key shape.
            if target_for_template(wire_scope) is not proof_target:
                raise TypeError("proof target template lookup changed identity")
    except Exception:
        raise ServiceComponentError("GROUP_RUNTIME_BUNDLE_INVALID") from None
    return value


def _account_risk_policy_metadata(
    resources: ComponentResources,
    *,
    spec: ComponentSpec,
    options: Mapping[str, object],
    module: object,
) -> tuple[tuple[AccountRiskPolicyBinding, ...], frozenset[str]]:
    """Bind an account-order route to the actual configured Risk policy.

    A configured account policy reference may be an operator alias. Aliases
    need an explicit resource-provider binding to the exact policy object on
    the gate; the policy fingerprint is always read from that object.
    """

    risk_owner = spec.component == "risk" and options.get("role") == "account_owner"
    if not risk_owner or "service.order_entry" not in spec.bind_ports:
        if resources.risk_policy_bindings:
            raise ServiceComponentError("UNEXPECTED_RISK_POLICY_BINDING")
        return (), frozenset()

    try:
        risk_module = importlib.import_module("bt_api_risk")
        gate_type = getattr(risk_module, "DurableRiskGate", None)
        policy_type = getattr(risk_module, "RiskPolicy", None)
        account_scope_type = getattr(risk_module, "AccountScope", None)
        target_type = getattr(module, "RiskOrderTarget", None)
        gate = resources.values["gate"]
        targets = resources.values["targets"]
        policy = gate.policy
        configured_ref = options["account_policy_ref"]
        policy_id = policy.policy_id
        fingerprint = policy.fingerprint
    except Exception:
        raise ServiceComponentError("RISK_POLICY_BINDING_UNAVAILABLE") from None

    if (
        not isinstance(gate_type, type)
        or type(gate) is not gate_type
        or not isinstance(policy_type, type)
        or type(policy) is not policy_type
        or not isinstance(account_scope_type, type)
        or not isinstance(target_type, type)
        or type(configured_ref) is not str
        or not configured_ref
        or type(policy_id) is not str
        or not policy_id
        or type(fingerprint) is not str
        or len(fingerprint) != 64
        or any(character not in "0123456789abcdef" for character in fingerprint)
        or not isinstance(targets, Mapping)
        or not targets
    ):
        raise ServiceComponentError("RISK_POLICY_BINDING_INVALID")

    target_scopes: set[WireScope] = set()
    for wire_scope, target in targets.items():
        try:
            account_scope = target.account_scope
            strategy_id = target.strategy_id
            provider_account_id = target.provider_account_id
        except Exception:
            raise ServiceComponentError("RISK_POLICY_BINDING_INVALID") from None
        if (
            type(wire_scope) is not WireScope
            or type(target) is not target_type
            or type(account_scope) is not account_scope_type
            or type(provider_account_id) is not str
            or account_scope.account_id != provider_account_id
            or wire_scope in target_scopes
            or not wire_scope.account_ref
            or wire_scope.account_refs
            or wire_scope.strategy_ref != strategy_id
            or wire_scope.run_id is not None
            or wire_scope.group_id is not None
            or wire_scope.leg_id is not None
            or wire_scope.manual_book_ref is not None
        ):
            raise ServiceComponentError("RISK_POLICY_BINDING_INVALID")
        target_scopes.add(wire_scope)

    supplied = resources.risk_policy_bindings
    if configured_ref != policy_id and not supplied:
        raise ServiceComponentError("RISK_POLICY_BINDING_REQUIRED")
    if supplied:
        by_scope: dict[WireScope, TrustedRiskPolicyBinding] = {}
        for binding in supplied:
            if (
                type(binding) is not TrustedRiskPolicyBinding
                or binding.reference != configured_ref
                or binding.policy is not policy
                or binding.scope not in target_scopes
                or binding.scope in by_scope
            ):
                raise ServiceComponentError("RISK_POLICY_BINDING_MISMATCH")
            by_scope[binding.scope] = binding
        if set(by_scope) != target_scopes:
            raise ServiceComponentError("RISK_POLICY_BINDING_MISMATCH")
    elif configured_ref != policy_id:
        raise ServiceComponentError("RISK_POLICY_BINDING_REQUIRED")

    return (
        tuple(
            AccountRiskPolicyBinding(spec.instance_id, target_scope, configured_ref, fingerprint)
            for target_scope in sorted(
                target_scopes,
                key=lambda scope: (
                    scope.execution_domain_ref,
                    scope.account_ref or "",
                    scope.strategy_ref or "",
                ),
            )
        ),
        frozenset({"service.order_entry"}),
    )


def build_component(
    prepared: PreparedComponent,
    *,
    scope: WireScope,
    resource_provider: ServiceResourceProvider,
    on_resources: Callable[[ComponentResources], None] | None = None,
    borrowed_upstream: BorrowedServiceClient | None = None,
    service_identity: WireIdentity | None = None,
    trusted_group_write_policies: tuple[TrustedGroupWritePolicy, ...] = (),
    trusted_group_reservation_policies: tuple[TrustedGroupReservationPolicy, ...] = (),
    trusted_group_resolution_policies: tuple[TrustedGroupResolutionPolicy, ...] = (),
) -> ServiceComponent:
    """Resolve trusted resources and construct one fixed adapter."""

    if type(prepared) is not PreparedComponent or type(scope) is not WireScope:
        raise ServiceComponentError("INVALID_COMPONENT_BUILD")
    if not callable(resource_provider):
        raise ServiceComponentError("RESOURCE_PROVIDER_REQUIRED")
    if borrowed_upstream is not None and type(borrowed_upstream) is not BorrowedServiceClient:
        raise ServiceComponentError("INVALID_BORROWED_UPSTREAM")
    if service_identity is not None and type(service_identity) is not WireIdentity:
        raise ServiceComponentError("INVALID_SERVICE_IDENTITY")
    if type(trusted_group_write_policies) is not tuple or any(
        type(policy) is not TrustedGroupWritePolicy for policy in trusted_group_write_policies
    ):
        raise ServiceComponentError("INVALID_GROUP_WRITE_POLICIES")
    if type(trusted_group_reservation_policies) is not tuple or any(
        type(policy) is not TrustedGroupReservationPolicy
        for policy in trusted_group_reservation_policies
    ):
        raise ServiceComponentError("INVALID_GROUP_RESERVATION_POLICIES")
    if type(trusted_group_resolution_policies) is not tuple or any(
        type(policy) is not TrustedGroupResolutionPolicy
        for policy in trusted_group_resolution_policies
    ):
        raise ServiceComponentError("INVALID_GROUP_RESOLUTION_POLICIES")
    spec = prepared.spec
    builtin = _builtin_for_spec(spec)
    if builtin is None:
        raise ServiceComponentError("UNKNOWN_COMPONENT")
    resources: ComponentResources | None = None
    tracked_by_caller = False
    execution_owner_lock: _ExecutionOwnerFileLock | _ExecutionOwnerMemoryLock | None = None
    execution_owner_lock_transferred = False
    defer_resource_tracking = (
        spec.component == "execution"
        and spec.options.get("role") in {"account_owner", "group_account_owner"}
        and "service.order_entry" in spec.bind_ports
    )
    try:
        resources = resource_provider(
            spec.component,
            spec.instance_id,
            prepared.options,
            scope,
        )
    except Exception:
        raise ServiceComponentError("RESOURCE_RESOLUTION_FAILED") from None
    if type(resources) is not ComponentResources:
        raise ServiceComponentError("INVALID_COMPONENT_RESOURCES")
    try:
        if on_resources is not None and not defer_resource_tracking:
            # Track before validation or adapter construction can fail.
            on_resources(resources)
            tracked_by_caller = True
        public_ports = frozenset(prepared.operations_by_port)
        expected_keys = builtin.resource_keys if public_ports else frozenset()
        expected_keys = expected_keys | builtin.optional_resource_keys.intersection(
            resources.values
        )
        if (
            spec.component == "execution"
            and spec.options.get("role") == "account_owner"
            and "service.order_query" in public_ports
            and "account_books" in resources.values
        ):
            if resources.values["account_books"] is None:
                raise ServiceComponentError("INVALID_ACCOUNT_BOOK_RESOURCES")
            expected_keys = expected_keys | {"account_books"}
        if (
            spec.component == "execution"
            and spec.options.get("role") == "account_owner"
            and "service.order_entry" in public_ports
            and "paper_owner_domain" in resources.values
        ):
            if resources.values["paper_owner_domain"] is None:
                raise ServiceComponentError("INVALID_PAPER_OWNER_DOMAIN_RESOURCE")
            expected_keys = expected_keys | {"paper_owner_domain"}
        if (
            spec.component == "execution"
            and spec.options.get("role") == "account_owner"
            and "service.order_entry" in public_ports
            and spec.options.get("approved_target_required") is True
            and "approved_target_authority" not in resources.values
        ):
            raise ServiceComponentError("APPROVED_TARGET_AUTHORITY_REQUIRED")
        if (
            spec.component == "execution"
            and spec.options.get("role") == "account_owner"
            and "service.order_entry" in public_ports
            and "approved_target_authority" in resources.values
        ):
            if resources.values["approved_target_authority"] is None:
                raise ServiceComponentError("INVALID_APPROVED_TARGET_AUTHORITY_RESOURCE")
            expected_keys = expected_keys | {"approved_target_authority"}
        if (
            spec.component == "execution"
            and spec.options.get("role") == "group_coordinator"
            and "group_admission" in resources.values
        ):
            if resources.values["group_admission"] is None:
                raise ServiceComponentError("GROUP_POLICY_RESOURCE_INVALID")
            expected_keys = expected_keys | {"group_admission"}
        if set(resources.values) != set(expected_keys):
            raise ServiceComponentError("RESOURCE_KEYS_MISMATCH")
        account_risk_policy_bindings, account_risk_policy_required_ports = (
            _account_risk_policy_metadata(
                resources,
                spec=spec,
                options=prepared.options,
                module=prepared.module,
            )
        )
        if any(port not in builtin.internal_ports for port in resources.internal_ports):
            raise ServiceComponentError("UNSUPPORTED_INTERNAL_PORT")
        if any(port not in spec.bind_ports for port in resources.internal_ports):
            raise ServiceComponentError("UNDECLARED_INTERNAL_PORT")

        adapter: object | None = None
        account_worker: object | None = None
        account_worker_selected = (
            spec.component == "gateway"
            and spec.options.get("role") == "account_session_worker"
            and "service.provider_session" in resources.internal_ports
        )
        if account_worker_selected:
            try:
                worker_module = importlib.import_module("bt_api_gateway.account_session_worker")
            except Exception:
                raise ServiceComponentError("ACCOUNT_SESSION_WORKER_UNAVAILABLE") from None
            worker_type = getattr(worker_module, "GatewayAccountSessionWorker", None)
            account_worker = resources.internal_ports["service.provider_session"]
            if not isinstance(worker_type, type) or type(account_worker) is not worker_type:
                raise ServiceComponentError("ACCOUNT_SESSION_WORKER_INVALID")
            start_worker = getattr(account_worker, "start", None)
            worker_maintain = getattr(account_worker, "maintain", None)
            if not callable(start_worker) or not callable(worker_maintain):
                raise ServiceComponentError("ACCOUNT_SESSION_WORKER_INVALID")

        if public_ports:
            values = resources.values
            module = prepared.module
            if spec.component == "gateway":
                if spec.options.get("role") == "market_hub":
                    constructor = getattr(module, "GatewayMarketService", None)
                    adapter = constructor(values["hub"])
                else:
                    constructor = getattr(module, "GatewayReadService", None)
                    constructor_args = {
                        "accounts": values["accounts"],
                        "principals": values["principals"],
                    }
                    if account_worker is not None:
                        constructor_args["state_worker"] = account_worker
                    adapter = constructor(values["journal"], **constructor_args)
            elif spec.component == "monitor":
                if spec.options.get("role") == "control":
                    constructor = getattr(module, "MonitorControlRuntime", None)
                    adapter = constructor(values["service"], values["owners"])
                elif spec.options.get("role") == "telemetry_ingress":
                    constructor = getattr(module, "MonitorTelemetryService", None)
                    constructor_options = dict(prepared.options)
                    constructor_options.pop("role", None)
                    adapter = constructor(values["targets"], **constructor_options)
                elif spec.options.get("role") == "durable_ingress":
                    constructor = getattr(module, "MonitorIngressService", None)
                    constructor_options = dict(prepared.options)
                    constructor_options.pop("role", None)
                    adapter = constructor(
                        values["outbox"], values["targets"], **constructor_options
                    )
                else:
                    constructor = getattr(module, "MonitorReadService", None)
                    adapter = constructor(values["outbox"], values["targets"])
            elif spec.component == "risk" and spec.options.get("role") == "group_reservations":
                if not trusted_group_reservation_policies or any(
                    policy.instance_id != spec.instance_id
                    for policy in trusted_group_reservation_policies
                ):
                    raise ServiceComponentError("GROUP_RESERVATION_POLICY_REQUIRED")
                bundle = _group_runtime_bundle(values["group_runtime_bundle"])
                targets = bundle.reservation_targets  # type: ignore[attr-defined]
                risk_admission = bundle.risk_admission  # type: ignore[attr-defined]
                if any(
                    getattr(target, "admission", None) is not risk_admission
                    or bundle.reservation_target_for(wire_scope) is not target  # type: ignore[attr-defined]
                    for wire_scope, target in targets.items()
                ):
                    raise ServiceComponentError("GROUP_RUNTIME_BUNDLE_INVALID")
                constructor = getattr(module, "GroupReservationsService", None)
                adapter = constructor(
                    targets,
                    values["accepted_group_store"],
                    trusted_group_reservation_policies,
                )
            elif spec.component == "risk" and spec.options.get("role") == "account_owner":
                constructor = getattr(module, "RiskOrderEntryService", None)
                adapter = constructor(
                    values["bt_api"],
                    values["targets"],
                    values["admission"],
                    values["journal"],
                    values["gate"],
                    values["provider_contracts"],
                )
                if "service.order_entry" in spec.bind_ports:
                    targets = values["targets"]
                    capability_state = getattr(adapter, "capability_state", None)
                    if (
                        not isinstance(targets, Mapping)
                        or not targets
                        or not callable(capability_state)
                    ):
                        raise ServiceComponentError("ORDER_CAPABILITY_UNAVAILABLE")
                    for target_scope in targets:
                        state = capability_state(target_scope)
                        if not isinstance(state, Mapping) or state.get("orders.submit") is not True:
                            raise ServiceComponentError("ORDER_CAPABILITY_UNAVAILABLE")
                    recover = getattr(adapter, "recover_interrupted_dispatches", None)
                    if not callable(recover):
                        raise ServiceComponentError("ORDER_RECOVERY_UNAVAILABLE")
                    try:
                        # The Risk journal owns the account-wide writer fence.
                        # Recovery completes before capabilities become ready.
                        recovered = recover()
                    except Exception:
                        raise ServiceComponentError("ORDER_RECOVERY_FAILED") from None
                    record_type = getattr(module, "RiskOrderCommand", None)
                    if type(recovered) is not tuple or not isinstance(record_type, type):
                        raise ServiceComponentError("ORDER_RECOVERY_INVALID")
                    try:
                        expected_scope_keys = {
                            target.account_scope.key for target in targets.values()
                        }
                        # The trusted adapter can finish a retained PROOF_READY
                        # command using its exact durable Risk receipt. Both
                        # that ACK and a still-UNKNOWN result are valid startup
                        # outcomes; this projection check grants no new proof.
                        recovery_result_valid = all(
                            type(record) is record_type
                            and record.state in {"UNKNOWN", "ACKED_TRACKED"}
                            and record.scope.key in expected_scope_keys
                            and type(record.principal_ref) is str
                            and bool(record.principal_ref)
                            and isinstance(record.latest_event, Mapping)
                            and record.latest_event.get("public_state") == record.state
                            and (
                                record.state == "UNKNOWN"
                                or (
                                    type(record.latest_event.get("dispatch_attempt_count")) is int
                                    and record.latest_event["dispatch_attempt_count"] == 1
                                    and type(record.latest_event.get("provider_order_id")) is str
                                    and bool(record.latest_event["provider_order_id"])
                                    and type(record.permit_id) is str
                                    and bool(record.permit_id)
                                    and record.latest_event.get("permit_id") == record.permit_id
                                    and record.latest_event.get("risk_intent_id")
                                    == record.risk_intent_id
                                    and type(record.latest_event.get("proof_sha256")) is str
                                    and len(record.latest_event["proof_sha256"]) == 64
                                    and all(
                                        char in "0123456789abcdef"
                                        for char in record.latest_event["proof_sha256"]
                                    )
                                )
                            )
                            for record in recovered
                        )
                    except Exception:
                        recovery_result_valid = False
                    if not recovery_result_valid:
                        raise ServiceComponentError("ORDER_RECOVERY_INVALID")
            elif spec.component == "risk" and spec.options.get("role") == "policy_catalog":
                store_type = getattr(module, "PolicyCatalogStore", None)
                catalog = values["catalog"]
                if not isinstance(store_type, type) or type(catalog) is not store_type:
                    raise ServiceComponentError("POLICY_CATALOG_STORE_INVALID")
                constructor = getattr(module, "PolicyCatalogService", None)
                confirmation_store = None
                if "confirmed_policy_store" in values:
                    confirmation_store_type = getattr(module, "ConfirmedPolicyStore", None)
                    confirmation_store = values["confirmed_policy_store"]
                    if (
                        not isinstance(confirmation_store_type, type)
                        or type(confirmation_store) is not confirmation_store_type
                    ):
                        raise ServiceComponentError("CONFIRMED_POLICY_STORE_INVALID")
                adapter = constructor(catalog, confirmation_store=confirmation_store)
            elif (
                spec.component == "risk"
                and spec.options.get("role") == "portfolio_analytics"
            ):
                store_type = getattr(module, "AnalysisTaskStore", None)
                analysis = values["analysis"]
                if not isinstance(store_type, type) or type(analysis) is not store_type:
                    raise ServiceComponentError("ANALYSIS_STORE_INVALID")
                try:
                    profile_ref = analysis.model_profile_ref
                    modes = analysis.allowed_source_modes
                    max_tasks = analysis.max_concurrent_tasks
                    profile = analysis.model_profile
                    max_input_bytes = profile.max_input_bytes
                except Exception:
                    raise ServiceComponentError("ANALYSIS_STORE_INVALID") from None
                configured_modes = prepared.options["allowed_source_modes"]
                if (
                    type(profile_ref) is not str
                    or profile_ref != prepared.options["model_profile_ref"]
                    or type(modes) is not tuple
                    or sorted(modes) != sorted(configured_modes)
                    or type(max_tasks) is not int
                    or not 1 <= max_tasks <= 64
                    or type(max_input_bytes) is not int
                    or (
                        "max_concurrent_tasks" in prepared.options
                        and max_tasks != prepared.options["max_concurrent_tasks"]
                    )
                    or (
                        "max_input_bytes" in prepared.options
                        and max_input_bytes != prepared.options["max_input_bytes"]
                    )
                ):
                    raise ServiceComponentError("ANALYSIS_STORE_INVALID")
                constructor = getattr(module, "PortfolioAnalyticsTaskService", None)
                adapter = constructor(analysis)
            elif spec.component == "risk":
                constructor = getattr(module, "RiskReadService", None)
                adapter = constructor(values["gate"], values["targets"])
            elif spec.component == "execution" and spec.options.get("role") == "group_coordinator":
                if (
                    borrowed_upstream is None
                    or type(service_identity) is not WireIdentity
                    or service_identity.role is not WireRole.SERVICE
                ):
                    raise ServiceComponentError("GROUP_COORDINATOR_AUTHORITY_REQUIRED")
                constructor = getattr(module, "GroupCoordinatorService", None)
                adapter = constructor(
                    values["store"],
                    values["targets"],
                    values["run_bindings"],
                    borrowed_upstream,
                    service_identity,
                    group_admission=values.get("group_admission"),
                    instance_id=spec.instance_id,
                )
            elif (
                spec.component == "execution" and spec.options.get("role") == "group_account_owner"
            ):
                if (
                    not trusted_group_write_policies
                    or not trusted_group_resolution_policies
                    or any(
                        policy.instance_id != spec.instance_id
                        for policy in trusted_group_write_policies
                    )
                    or any(
                        policy.instance_id != spec.instance_id
                        for policy in trusted_group_resolution_policies
                    )
                ):
                    raise ServiceComponentError("GROUP_ACCOUNT_OWNER_POLICIES_REQUIRED")
                writes = {
                    (
                        policy.principal_ref,
                        policy.identity,
                        policy.scope_ref,
                        policy.scope,
                        policy.run_id,
                        policy.scope.account_ref,
                    )
                    for policy in trusted_group_write_policies
                    if policy.instance_id == spec.instance_id
                }
                resolutions = {
                    (
                        policy.principal_ref,
                        policy.identity,
                        policy.scope_ref,
                        policy.scope,
                        policy.run_id,
                        policy.account_ref,
                    )
                    for policy in trusted_group_resolution_policies
                    if policy.instance_id == spec.instance_id
                }
                if not writes or writes != resolutions:
                    raise ServiceComponentError("GROUP_ACCOUNT_OWNER_POLICIES_MISMATCH")
                bundle = _group_runtime_bundle(values["group_runtime_bundle"])
                execution_targets = MappingProxyType(
                    {
                        wire_scope: proof_target.execution_scope
                        for wire_scope, proof_target in bundle.proof_targets.items()  # type: ignore[attr-defined]
                    }
                )
                constructor = getattr(module, "ExecutionGroupOrderEntryService", None)
                adapter = constructor(
                    values["store"],
                    execution_targets,
                    values["dispatch"],
                    bundle.account_scoped_admission,  # type: ignore[attr-defined]
                    values["accepted_group_store"],
                    values["group_authority"],
                    bundle.proof_source,  # type: ignore[attr-defined]
                    trusted_group_write_policies,
                    trusted_group_resolution_policies,
                    writer_id=spec.instance_id,
                )
                if "service.order_entry" in spec.bind_ports:
                    execution_owner_lock = _acquire_execution_owner_lock(values["store"])
                    recover = getattr(adapter, "recover_interrupted_dispatches", None)
                    if not callable(recover):
                        raise ServiceComponentError("ORDER_RECOVERY_UNAVAILABLE")
                    try:
                        recovered = recover()
                    except Exception:
                        raise ServiceComponentError("ORDER_RECOVERY_FAILED") from None
                    try:
                        from bt_api_execution import ExecutionRecord, ExecutionState

                        expected_scope_keys = {target.key for target in execution_targets.values()}
                        recovery_result_valid = type(recovered) is tuple and all(
                            type(record) is ExecutionRecord
                            and record.state is ExecutionState.UNKNOWN
                            and record.unknown_reason == "interrupted_order_dispatch"
                            and record.review_required is True
                            and record.scope_key in expected_scope_keys
                            for record in recovered
                        )
                    except Exception:
                        recovery_result_valid = False
                    if not recovery_result_valid:
                        raise ServiceComponentError("ORDER_RECOVERY_INVALID")
            elif spec.component == "execution" and spec.options.get("role") == "account_owner":
                constructor = getattr(module, "ExecutionOrderEntryService", None)
                admission = values["admission"]
                if not isinstance(admission, Mapping) or set(admission) - _ADMISSION_KEYS:
                    raise ServiceComponentError("INVALID_ADMISSION_RESOURCES")
                order_gate = admission.get("order_gate")
                cancel_gate = admission.get("cancel_gate")
                basic_gate = admission.get("basic_admission_gate")
                allow_unprotected = admission.get("allow_unprotected", False)
                if type(allow_unprotected) is not bool:
                    raise ServiceComponentError("INVALID_ADMISSION_RESOURCES")
                if (
                    order_gate is None
                    and cancel_gate is None
                    and basic_gate is None
                    and not allow_unprotected
                ):
                    # A provider-capable write route must not silently become
                    # unprotected; the composition root states its policy.
                    raise ServiceComponentError("ADMISSION_POLICY_REQUIRED")
                constructor_args = {
                    "admission_gate": order_gate,
                    "cancel_admission_gate": cancel_gate,
                    "basic_admission_gate": basic_gate,
                    "allow_unprotected": allow_unprotected,
                    "account_books_source": values.get("account_books"),
                }
                if "paper_owner_domain" in values:
                    constructor_args["paper_owner_domain"] = values["paper_owner_domain"]
                if "approved_target_authority" in values:
                    constructor_args["approved_target_authority"] = values[
                        "approved_target_authority"
                    ]
                adapter = constructor(
                    values["store"],
                    values["targets"],
                    values["dispatch"],
                    **constructor_args,
                )
                if "service.order_entry" in spec.bind_ports:
                    recover = getattr(adapter, "recover_interrupted_dispatches", None)
                    if not callable(recover):
                        raise ServiceComponentError("ORDER_RECOVERY_UNAVAILABLE")
                    execution_owner_lock = _acquire_execution_owner_lock(values["store"])
                    try:
                        recovered = recover()
                    except Exception:
                        raise ServiceComponentError("ORDER_RECOVERY_FAILED") from None
                    record_type = getattr(module, "ExecutionRecord", None)
                    state_type = getattr(module, "ExecutionState", None)
                    targets = values.get("targets")
                    if (
                        type(recovered) is not tuple
                        or not isinstance(record_type, type)
                        or not isinstance(state_type, type)
                        or not isinstance(targets, Mapping)
                    ):
                        raise ServiceComponentError("ORDER_RECOVERY_INVALID")
                    try:
                        expected_scope_keys = {target.key for target in targets.values()}
                        recovery_result_valid = all(
                            type(record) is record_type
                            and record.state is state_type.UNKNOWN
                            and record.unknown_reason == "interrupted_order_dispatch"
                            and record.review_required is True
                            and record.scope_key in expected_scope_keys
                            for record in recovered
                        )
                    except Exception:
                        recovery_result_valid = False
                    if not recovery_result_valid:
                        raise ServiceComponentError("ORDER_RECOVERY_INVALID")
            elif spec.component == "execution":
                constructor = getattr(module, "ExecutionReadService", None)
                adapter = constructor(values["store"], values["targets"])
            else:  # pragma: no cover - fixed table and branch must stay aligned.
                raise ServiceComponentError("UNKNOWN_COMPONENT")
            if not callable(getattr(adapter, "handle", None)):
                raise ServiceComponentError("BUILTIN_COMPONENT_INVALID")

        internal_ports = dict(resources.internal_ports)
        if (
            spec.component == "risk"
            and "service.order_admission" in spec.bind_ports
            and "service.order_admission" not in internal_ports
        ):
            gate = resources.values.get("gate")
            admission = getattr(adapter, "order_admission", None)
            if gate is None or admission is None:
                raise ServiceComponentError("INTERNAL_PORT_UNAVAILABLE")
            internal_ports["service.order_admission"] = admission

        missing_internal = {
            port
            for port in spec.bind_ports
            if port in builtin.internal_ports and port not in internal_ports
        }
        if missing_internal:
            raise ServiceComponentError("INTERNAL_PORT_UNAVAILABLE")

        if spec.component == "risk" and spec.options.get("role") == "group_policy":
            admission = internal_ports.get("service.group_admission")
            gate_type = getattr(prepared.module, "GroupPolicyService", None)
            if not isinstance(gate_type, type) or type(admission) is not gate_type:
                raise ServiceComponentError("GROUP_POLICY_RESOURCE_INVALID")
            if admission.group_policy_ref != prepared.options.get("group_policy_ref"):
                raise ServiceComponentError("GROUP_POLICY_REF_MISMATCH")
            if admission.ready is not True:
                raise ServiceComponentError("GROUP_POLICY_UNAVAILABLE")

        capabilities: list[ServiceCapability] = []
        all_operations: set[str] = set()
        for port, operations in prepared.operations_by_port.items():
            if spec.component == "execution" and spec.options.get("role") == "account_owner":
                configured_operations = getattr(adapter, "supported_operations", None)
                expected_operations = frozenset({"orders.submit", "orders.cancel", "orders.query"})
                if "account_books" in resources.values:
                    expected_operations = expected_operations | {"execution.account_books.query"}
                if "approved_target_authority" in resources.values:
                    expected_operations = expected_operations | {"orders.submit_approved_target"}
                if (
                    type(configured_operations) is not frozenset
                    or configured_operations != expected_operations
                ):
                    raise ServiceComponentError("BUILTIN_COMPONENT_INVALID")
                operations = operations.intersection(configured_operations)
            if spec.component == "risk" and spec.options.get("role") == "group_reservations":
                configured_operations = getattr(adapter, "supported_operations", None)
                if type(
                    configured_operations
                ) is not frozenset or configured_operations != frozenset(
                    prepared.module.SUPPORTED_OPERATIONS
                ):
                    raise ServiceComponentError("BUILTIN_COMPONENT_INVALID")
                operations = operations.intersection(configured_operations)
            if spec.component == "risk" and spec.options.get("role") == "policy_catalog":
                configured_operations = getattr(adapter, "supported_operations", None)
                if type(
                    configured_operations
                ) is not frozenset or configured_operations != frozenset(
                    prepared.module.SUPPORTED_OPERATIONS
                ):
                    raise ServiceComponentError("BUILTIN_COMPONENT_INVALID")
                operations = operations.intersection(configured_operations)
            if (
                spec.component == "risk"
                and spec.options.get("role") == "portfolio_analytics"
            ):
                configured_operations = getattr(adapter, "supported_operations", None)
                if type(
                    configured_operations
                ) is not frozenset or configured_operations != frozenset(
                    prepared.module.SUPPORTED_OPERATIONS
                ):
                    raise ServiceComponentError("BUILTIN_COMPONENT_INVALID")
                operations = operations.intersection(configured_operations)
            if spec.component == "execution" and spec.options.get("role") in {
                "group_coordinator",
                "group_account_owner",
            }:
                configured_operations = getattr(adapter, "supported_operations", None)
                expected_operations = frozenset(
                    getattr(
                        prepared.module,
                        "GROUP_COORDINATOR_OPERATIONS"
                        if spec.options.get("role") == "group_coordinator"
                        else "GROUP_ACCOUNT_OWNER_OPERATIONS",
                        frozenset(),
                    )
                )
                if (
                    type(configured_operations) is not frozenset
                    or configured_operations != expected_operations
                ):
                    raise ServiceComponentError("BUILTIN_COMPONENT_INVALID")
                operations = operations.intersection(configured_operations)
            if spec.component == "gateway" and spec.options.get("role") == "market_hub":
                configured_operations = getattr(adapter, "supported_operations", None)
                if type(configured_operations) is not frozenset or configured_operations not in (
                    operations,
                    operations - {"market.history"},
                ):
                    raise ServiceComponentError("BUILTIN_COMPONENT_INVALID")
                operations = configured_operations
            if operations.intersection(all_operations):
                raise ServiceComponentError("DUPLICATE_COMPONENT_OPERATION")
            all_operations.update(operations)
            write_operations = prepared.write_operations_by_port[port]
            if (
                spec.component == "execution"
                and spec.options.get("role") == "account_owner"
                and port == "service.order_entry"
            ):
                write_operations = write_operations.intersection(operations)
            if not write_operations.issubset(operations):
                raise ServiceComponentError("BUILTIN_COMPONENT_INVALID")
            capabilities.append(
                ServiceCapability(spec.instance_id, port, operations, write_operations)
            )

        handlers: dict[
            str,
            Callable[[ServiceCallContext, Mapping[str, Any]], Mapping[str, Any]],
        ] = {}
        registration_bound = (
            spec.component == "gateway" and spec.options.get("role") == "market_hub"
        )
        market_registration_type = (
            getattr(prepared.module, "GatewayMarketRegistration", None)
            if registration_bound
            else None
        )
        market_authorization_error_types = tuple(
            error_type
            for error_type in (
                getattr(prepared.module, "GatewayMarketHubError", None)
                if registration_bound
                else None,
                getattr(prepared.module, "GatewayMarketServiceError", None)
                if registration_bound
                else None,
            )
            if isinstance(error_type, type) and issubclass(error_type, Exception)
        )
        gateway_event_journal_error_type = (
            getattr(prepared.module, "GatewayEventJournalError", None)
            if spec.component == "gateway"
            else None
        )
        if registration_bound and (
            market_registration_type is None
            or len(market_authorization_error_types) != 2
            or len(set(market_authorization_error_types)) != 2
            or not callable(getattr(adapter, "register_registration", None))
        ):
            raise ServiceComponentError("BUILTIN_COMPONENT_INVALID")
        for operation in all_operations:

            def handler(
                context: ServiceCallContext,
                arguments: Mapping[str, Any],
                *,
                _adapter: object = adapter,
                _operation: str = operation,
            ) -> Mapping[str, Any]:
                assert _adapter is not None
                if registration_bound:
                    try:
                        registration = market_registration_type(
                            principal_ref=context.principal_ref,
                            execution_domain_ref=context.scope.execution_domain_ref,
                            strategy_ref=context.scope.strategy_ref,
                            run_id=context.scope.run_id,
                            account_ref=context.scope.account_ref,
                        )
                        _adapter.register_registration(  # type: ignore[attr-defined]
                            context.registration_id, registration
                        )
                        return _adapter.handle(  # type: ignore[attr-defined]
                            _operation,
                            principal_ref=context.principal_ref,
                            scope=context.scope,
                            arguments=arguments,
                            registration_id=context.registration_id,
                        )
                    except Exception as error:
                        if (
                            _operation == "market.subscribe"
                            and type(error) in market_authorization_error_types
                            and getattr(error, "code", None)
                            in {"MARKET_KEY_DENIED", "MARKET_KEY_NOT_EXPORTED"}
                        ):
                            raise ServiceOperationDeniedError() from None
                        raise
                if spec.component == "gateway":
                    try:
                        return _adapter.handle(  # type: ignore[attr-defined]
                            _operation,
                            principal_ref=context.principal_ref,
                            identity=context.identity,
                            scope=context.scope,
                            arguments=arguments,
                            result_fits=context.result_fits,
                        )
                    except Exception as error:
                        if (
                            _operation == "account.read_after"
                            and isinstance(gateway_event_journal_error_type, type)
                            and type(error) is gateway_event_journal_error_type
                            and getattr(error, "code", None) == "CONSUMER_PAGE_TOO_LARGE"
                        ):
                            raise ServiceResultTooLargeError() from None
                        raise
                if spec.component == "monitor" and spec.options.get("role") == "control":
                    try:
                        return _adapter.handle(  # type: ignore[attr-defined]
                            _operation,
                            principal_ref=context.principal_ref,
                            identity=context.identity,
                            scope=context.scope,
                            arguments=arguments,
                        )
                    except Exception as error:
                        if type(error) is getattr(
                            prepared.module, "MonitorControlServiceError", None
                        ) and error.code in {
                            "CONTROL_NOT_AUTHORIZED",
                            "CALLER_DELEGATION_NOT_GRANTED",
                            "APPLICATION_ROLE_REQUIRED",
                            "INVALID_IDENTITY",
                            "IDENTITY_MISMATCH",
                            "INVALID_SCOPE",
                            "UNSUPPORTED_SCOPE",
                            "SCOPE_NOT_GRANTED",
                        }:
                            # These failures precede ledger admission/effects.
                            # Owner, storage and ACK failures stay UNKNOWN.
                            raise ServiceOperationDeniedError() from None
                        raise
                if spec.component == "monitor" and spec.options.get("role") == "durable_ingress":
                    return _adapter.handle(  # type: ignore[attr-defined]
                        _operation,
                        principal_ref=context.principal_ref,
                        identity=context.identity,
                        scope=context.scope,
                        arguments=arguments,
                    )
                if spec.component == "monitor" and spec.options.get("role") == "telemetry_ingress":
                    try:
                        return _adapter.handle(  # type: ignore[attr-defined]
                            _operation,
                            principal_ref=context.principal_ref,
                            identity=context.identity,
                            scope=context.scope,
                            arguments=arguments,
                            result_fits=context.result_fits,
                        )
                    except Exception as error:
                        if (
                            type(error)
                            is getattr(prepared.module, "MonitorTelemetryServiceError", None)
                            and error.code == "SNAPSHOT_TOO_LARGE"
                        ):
                            raise ServiceResultTooLargeError() from None
                        raise
                if spec.component == "risk" and spec.options.get("role") == "group_reservations":
                    return _adapter.handle(  # type: ignore[attr-defined]
                        _operation,
                        context=context,
                        arguments=arguments,
                    )
                if spec.component == "execution" and spec.options.get("role") in {
                    "group_coordinator",
                    "group_account_owner",
                }:
                    return _adapter.handle(  # type: ignore[attr-defined]
                        _operation,
                        context=context,
                        arguments=arguments,
                    )
                if (
                    spec.component in {"execution", "risk"}
                    and spec.options.get("role") == "account_owner"
                ):
                    return _adapter.handle(  # type: ignore[attr-defined]
                        _operation,
                        principal_ref=context.principal_ref,
                        identity=context.identity,
                        scope=context.scope,
                        arguments=arguments,
                    )
                return _adapter.handle(  # type: ignore[attr-defined]
                    _operation,
                    principal_ref=context.principal_ref,
                    scope=context.scope,
                    arguments=arguments,
                )

            handlers[operation] = handler

        maintain = None
        maintain_registrations = None
        readiness = resources.ready
        if registration_bound:
            candidate_maintain = getattr(adapter, "maintain", None)
            if not callable(candidate_maintain):
                raise ServiceComponentError("BUILTIN_COMPONENT_INVALID")
            maintain = candidate_maintain

            def readiness(
                *,
                resource_ready: Callable[[], bool] = resources.ready,
                market_adapter: object = adapter,
            ) -> bool:
                try:
                    return resource_ready() is True and market_adapter.ready is True  # type: ignore[attr-defined]
                except Exception:
                    return False

        if account_worker is not None:

            def maintain_account_worker(
                _active_registration_ids: frozenset[str],
                *,
                maintain_worker: Callable[[], object] = worker_maintain,
            ) -> object:
                # Provider ownership belongs to the service instance, not to
                # any strategy lease. Maintain it even when no clients remain.
                return maintain_worker()

            maintain = maintain_account_worker

            def account_worker_readiness(
                *,
                resource_ready: Callable[[], bool] = resources.ready,
                session_worker: object = account_worker,
            ) -> bool:
                try:
                    return resource_ready() is True and session_worker.ready is True
                except Exception:
                    return False

            readiness = account_worker_readiness

        if (
            spec.component == "risk"
            and spec.options.get("role") == "portfolio_analytics"
        ):
            # The analysis role executes its durable task queue on the host's
            # owner thread; a fixed-table role without the executor hook is a
            # build-time fault instead of a silently idle service.
            candidate_maintain = getattr(adapter, "maintain", None)
            if not callable(candidate_maintain):
                raise ServiceComponentError("BUILTIN_COMPONENT_INVALID")
            maintain = candidate_maintain

        if (
            spec.component == "execution"
            and spec.options.get("role")
            in {
                "group_coordinator",
                "group_account_owner",
            }
        ) or (spec.component == "risk" and spec.options.get("role") == "group_reservations"):
            candidate_maintain_contexts = getattr(adapter, "maintain_registrations", None)
            if not callable(candidate_maintain_contexts):
                raise ServiceComponentError("GROUP_REGISTRATION_LIFECYCLE_UNAVAILABLE")
            maintain_registrations = candidate_maintain_contexts

            def group_component_readiness(
                *,
                resource_ready: Callable[[], bool] = resources.ready,
                group_adapter: object = adapter,
            ) -> bool:
                try:
                    return resource_ready() is True and group_adapter.ready is True  # type: ignore[attr-defined]
                except Exception:
                    return False

            readiness = group_component_readiness

        component_resources = _resources_with_execution_owner_lock(resources, execution_owner_lock)
        risk_owner_gate: object | None = None
        risk_owner_targets: Mapping[WireScope, object] = {}
        risk_owner_admission: Mapping[WireScope, object] = {}
        if spec.component == "risk" and spec.options.get("role") == "account_owner":
            if "service.order_entry" in spec.bind_ports:
                risk_owner_gate = resources.values.get("gate")
                risk_owner_targets = resources.values.get("targets", {})  # type: ignore[assignment]
                risk_owner_admission = resources.values.get("admission", {})  # type: ignore[assignment]
        component = ServiceComponent(
            component=spec.component,
            instance_id=spec.instance_id,
            capabilities=tuple(capabilities),
            handlers=handlers,
            internal_ports=internal_ports,
            readiness=readiness,
            close=component_resources.close,
            maintain=maintain,
            maintain_registrations=maintain_registrations,
            account_risk_policy_bindings=account_risk_policy_bindings,
            account_risk_policy_required_ports=account_risk_policy_required_ports,
            shared_collateral_domains=resources.shared_collateral_domains,
            risk_owner_gate=risk_owner_gate,
            risk_owner_targets=risk_owner_targets,
            risk_owner_admission=risk_owner_admission,
        )
        if on_resources is not None and defer_resource_tracking:
            # The owner lock and resource close callback must be tracked as one
            # lifecycle unit if a later host-start step needs to retry cleanup.
            on_resources(component_resources)
            tracked_by_caller = True
        execution_owner_lock_transferred = execution_owner_lock is not None
        if account_worker is not None:
            # Adapter construction above validates the provider session's
            # trusted account/journal/ACL scope bindings. Do not connect to
            # the provider until that pure construction has succeeded.
            try:
                start_worker()
            except Exception:
                raise ServiceComponentError("ACCOUNT_SESSION_WORKER_START_FAILED") from None
            try:
                if account_worker.ready is not True:
                    raise ServiceComponentError("ACCOUNT_SESSION_WORKER_NOT_READY")
            except ServiceComponentError:
                raise
            except Exception:
                raise ServiceComponentError("ACCOUNT_SESSION_WORKER_NOT_READY") from None
        return component
    except BaseException:
        if defer_resource_tracking and on_resources is not None and not tracked_by_caller:
            pending_resources = _resources_with_execution_owner_lock(
                resources, execution_owner_lock
            )
            on_resources(pending_resources)
            tracked_by_caller = True
            execution_owner_lock_transferred = execution_owner_lock is not None
        if not tracked_by_caller:
            try:
                _resources_with_execution_owner_lock(resources, execution_owner_lock).close()
                execution_owner_lock_transferred = execution_owner_lock is not None
            except Exception:
                raise ServiceComponentError("RESOURCE_CLEANUP_FAILED") from None
        if execution_owner_lock is not None and not execution_owner_lock_transferred:
            execution_owner_lock.close()
        raise


def _copy_string_mapping(value: Mapping[str, object], code: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ServiceComponentError(code)
    copied = dict(value)
    if any(type(key) is not str or not key for key in copied):
        raise ServiceComponentError(code)
    return copied


def _copy_callable_mapping(
    value: Mapping[str, Callable[..., Any]], code: str
) -> dict[str, Callable[..., Any]]:
    copied = _copy_string_mapping(value, code)
    if any(not callable(item) for item in copied.values()):
        raise ServiceComponentError(code)
    return copied  # type: ignore[return-value]


__all__ = [
    "BorrowedServiceClient",
    "ComponentResources",
    "ComponentSpec",
    "PreparedComponent",
    "ResourceOwnership",
    "ServiceComponent",
    "ServiceComponentError",
    "ServiceResourceProvider",
    "TrustedRiskPolicyBinding",
    "TrustedSharedCollateralDomain",
    "build_component",
    "component_specs",
    "order_components",
    "validate_components",
]
