"""Fail-closed lifecycle owner for fixed SDK service components.

Construction is pure. ``start`` validates the trusted references, imports only
the selected fixed component packages, starts any explicitly wired upstream
service clients, resolves trusted resources, creates a not-ready SQLite
registry, and finally binds the CURVE-authenticated ZMQv2 listener on the
calling thread. Outbound wiring comes only from a trusted code bundle; the
manifest cannot name Python modules or executable factories.
"""

from __future__ import annotations

import logging
import re
import sys
import threading
import time
from hashlib import sha256
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field, replace
from math import floor, isfinite
from pathlib import Path
from types import FunctionType, MappingProxyType
from typing import Any

from bt_api_base.service_protocol import (
    RequestReplyTransport,
    WireIdentity,
    WireRole,
    WireScope,
)

from .service_components import (
    BorrowedServiceClient,
    ComponentSpec,
    ComponentResources,
    PreparedComponent,
    ServiceComponent,
    ServiceComponentError,
    ServiceResourceProvider,
    TrustedSharedCollateralDomain,
    _ROLE_BUILTINS,
    _ATOMIC_READINESS_RETRY,
    _reject_retired_execution_owner,
    build_component,
    component_specs,
    order_components,
    validate_components,
)
from .service_config import ServiceConfig, ServiceConfigError
from .service_deployment import validate_deployment
from .service_migration_gate import (
    MigrationGateError,
    enforce_migration_start,
    migration_start_guard,
    record_migration_write_attempt,
)
from .service_registry import (
    ActiveRegistrationContext,
    PrincipalGrant,
    ServiceCapability,
    ServiceRegistry,
    ServiceRegistrySchemaError,
    ServiceRegistryStartupCleanupError,
    TrustedGroupReservationPolicy,
    TrustedGroupResolutionPolicy,
    TrustedGroupWritePolicy,
    TrustedPaperLegacyImportPolicy,
    TrustedPaperOwnerWritePolicy,
    validate_capabilities_and_grants,
)
from .service_runtime import RegisteredBinding, RegistrationSummary, ServiceClientRuntime

_SAFE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_RECOVERY_CLOSE_OPERATION = "orders.submit_recovery_close"
_OWNER_CANCEL_OPERATION = "owner.cancel"

_LOG = logging.getLogger(__name__)


def _account_read_metadata_omission_allowed(
    component: str,
    role: object,
    resources: ComponentResources,
    *,
    instance_id: str | None = None,
    bind_ports: tuple[str, ...] | None = None,
    built_component: ServiceComponent | None = None,
) -> bool:
    """Allow the one account-read operation the resolved provider cannot serve.

    A real account-session worker may prove the omission from its provider. The
    built-in read adapter also omits metadata when no provider-session resource
    exists; in that case require the exact resource shape and the adapter held
    by the component handlers. Neither path trusts a manifest capability flag.
    """

    if (
        component != "gateway"
        or role != "account_session_worker"
        or type(resources) is not ComponentResources
    ):
        return False
    worker = resources.internal_ports.get("service.provider_session")
    module = sys.modules.get("bt_api_gateway.account_session_worker")
    worker_type = getattr(module, "GatewayAccountSessionWorker", None)
    provider_session_present = "service.provider_session" in resources.internal_ports
    provider_exposes_metadata = False
    if provider_session_present:
        if not isinstance(worker_type, type) or type(worker) is not worker_type:
            return False
        try:
            provider = worker._provider
            provider_exposes_metadata = callable(
                getattr(provider, "read_instrument_metadata", None)
            )
            metadata_available = worker.instrument_metadata_available
        except Exception:
            return False
        if type(metadata_available) is not bool or metadata_available is not False:
            return False
        if provider_exposes_metadata:
            return False
    elif built_component is None:
        # Resource omission alone is not enough: Host must see the exact
        # built-in read component whose handlers expose the narrowed contract.
        return False

    if built_component is None:
        # Preserve the resource-only helper behavior used by focused worker
        # tests. ServiceHost always supplies the post-build component below.
        return provider_session_present

    if (
        type(built_component) is not ServiceComponent
        or instance_id is None
        or built_component.component != component
        or built_component.instance_id != instance_id
        or type(built_component.capabilities) is not tuple
        or len(built_component.capabilities) != 1
        or type(built_component.handlers) is not MappingProxyType
        or type(built_component.internal_ports) is not MappingProxyType
    ):
        return False
    capability = built_component.capabilities[0]
    if (
        type(capability) is not ServiceCapability
        or capability.instance_id != instance_id
        or capability.port != "service.account_read"
        or capability.write_operations
        or capability.operations != frozenset(built_component.handlers)
        or not all(callable(handler) for handler in built_component.handlers.values())
        or set(built_component.internal_ports) != set(resources.internal_ports)
        or any(
            built_component.internal_ports[port] is not value
            for port, value in resources.internal_ports.items()
        )
    ):
        return False

    read_module = sys.modules.get("bt_api_gateway.service_component")
    read_service_type = getattr(read_module, "GatewayReadService", None)
    supported_operations = getattr(read_module, "SUPPORTED_OPERATIONS", None)
    if (
        not isinstance(read_service_type, type)
        or type(supported_operations) is not frozenset
        or "account.instrument_metadata" not in supported_operations
    ):
        return False

    adapter: object | None = None
    for operation, handler in built_component.handlers.items():
        if type(handler) is not FunctionType:
            return False
        keyword_defaults = handler.__kwdefaults__
        if (
            type(keyword_defaults) is not dict
            or keyword_defaults.get("_operation") != operation
            or type(keyword_defaults.get("_adapter")) is not read_service_type
        ):
            return False
        current_adapter = keyword_defaults["_adapter"]
        if adapter is None:
            adapter = current_adapter
        elif adapter is not current_adapter:
            return False

    if adapter is None:
        return False
    try:
        adapter_operations = adapter.supported_operations
        adapter_worker = adapter._state_worker
    except Exception:
        return False
    expected_operations = supported_operations - {"account.instrument_metadata"}
    if adapter_worker is None:
        # A worker-less reader has no typed snapshot source either.
        expected_operations = expected_operations - {"account.snapshot.typed"}
    if getattr(adapter, "_reconciliation", None) is not None:
        # Reconciliation evidence is opt-in (iteration 60, acceptance G2).
        expected_operations = expected_operations | {"account.reconciliation_cut"}
    if (
        type(adapter_operations) is not frozenset
        or adapter_operations != expected_operations
        or capability.operations != expected_operations
    ):
        return False

    if provider_session_present:
        return (
            adapter_worker is worker
            and "service.provider_session" in built_component.internal_ports
        )

    return (
        bind_ports == ("service.account_read",)
        and not resources.internal_ports
        and not built_component.internal_ports
        and set(resources.values) == {"journal", "accounts", "principals"}
        and "service.provider_session" not in built_component.internal_ports
        and adapter_worker is None
    )


def _matches_account_read_metadata_omission(
    actual: ServiceCapability,
    planned: ServiceCapability,
    allowed_instance_ids: set[str],
) -> bool:
    """Match only the exact read-only account capability omission.

    The omitted operations are instrument metadata and, for a reader without
    a state worker, the typed snapshot; nothing else may differ.
    """

    omitted = planned.operations - actual.operations
    return (
        actual.instance_id == planned.instance_id
        and actual.port == planned.port == "service.account_read"
        and planned.instance_id in allowed_instance_ids
        and "account.instrument_metadata" in omitted
        and omitted <= _OPTIONAL_ACCOUNT_READ_OPERATIONS
        and actual.operations <= planned.operations
        and actual.write_operations == planned.write_operations
        and actual.market_wire_version == planned.market_wire_version
    )


_OPTIONAL_ACCOUNT_READ_OPERATIONS = frozenset(
    {"account.instrument_metadata", "account.snapshot.typed"}
)


def _account_read_grants_for_capabilities(
    grants: Mapping[str, tuple[PrincipalGrant, ...]],
    actual: tuple[ServiceCapability, ...],
    planned: tuple[ServiceCapability, ...],
    allowed_instance_ids: set[str],
) -> Mapping[str, tuple[PrincipalGrant, ...]]:
    """Project only a provider-proven optional metadata omission into grants.

    The trusted authority is still validated against the complete plan before
    resource acquisition. Registry grants must describe the resolved provider,
    without changing any identity, scope, or other operation. A metadata-only
    grant remains invalid because removing its sole operation is not useful.
    """

    omitted_routes = {
        (resolved.instance_id, resolved.port): declared.operations - resolved.operations
        for resolved, declared in zip(actual, planned, strict=True)
        if _matches_account_read_metadata_omission(
            resolved, declared, allowed_instance_ids
        )
    }
    if not omitted_routes:
        return grants
    return {
        principal: tuple(
            replace(
                grant,
                resources=tuple(
                    replace(
                        resource,
                        operations=resource.operations
                        - omitted_routes[(resource.instance_id, resource.port)],
                    )
                    if (resource.instance_id, resource.port) in omitted_routes
                    and "account.instrument_metadata" in resource.operations
                    else resource
                    for resource in grant.resources
                ),
            )
            for grant in principal_grants
        )
        for principal, principal_grants in grants.items()
    }


class ServiceHostError(RuntimeError):
    """Finite, value-free host lifecycle error."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class TrustedServiceAuthority:
    """Operator-resolved service identity, CURVE keys, ACL, and exact refs.

    The service manifest is configuration, not authority. This object must be
    assembled by trusted application code; its grants and principal map are
    passed directly to the registry and transport and are never derived from
    manifest claims.
    """

    identity: WireIdentity
    scope: WireScope
    scope_ref: str
    scope_version: str
    security_ref: str
    security_version: str
    registration_policy_ref: str
    registration_policy_version: str
    service_requirements_ref: str
    service_requirements_version: str
    curve_public_key: bytes
    curve_secret_key: bytes = field(repr=False)
    principals: Mapping[bytes, str] = field(repr=False)
    grants: Mapping[str, tuple[PrincipalGrant, ...]] = field(repr=False)
    group_write_policies: tuple[TrustedGroupWritePolicy, ...] = ()
    group_reservation_policies: tuple[TrustedGroupReservationPolicy, ...] = ()
    group_resolution_policies: tuple[TrustedGroupResolutionPolicy, ...] = ()

    def __post_init__(self) -> None:
        if type(self.identity) is not WireIdentity or self.identity.role is not WireRole.SERVICE:
            raise ServiceHostError("SERVICE_IDENTITY_REQUIRED")
        if type(self.scope) is not WireScope:
            raise ServiceHostError("SERVICE_SCOPE_REQUIRED")
        for value in (
            self.scope_ref,
            self.scope_version,
            self.security_ref,
            self.security_version,
            self.registration_policy_ref,
            self.registration_policy_version,
            self.service_requirements_ref,
            self.service_requirements_version,
        ):
            if type(value) is not str or _SAFE_REF.fullmatch(value) is None:
                raise ServiceHostError("INVALID_AUTHORITY_REFERENCE")
        if type(self.curve_public_key) is not bytes or type(self.curve_secret_key) is not bytes:
            raise ServiceHostError("INVALID_CURVE_KEYS")
        if len(self.curve_public_key) != 40 or len(self.curve_secret_key) != 40:
            raise ServiceHostError("INVALID_CURVE_KEYS")

        principals = dict(self.principals) if isinstance(self.principals, Mapping) else None
        if not principals or any(
            type(key) is not bytes
            or len(key) != 40
            or type(value) is not str
            or _SAFE_REF.fullmatch(value) is None
            for key, value in principals.items()
        ):
            raise ServiceHostError("INVALID_PRINCIPAL_MAP")
        if len(set(principals.values())) != len(principals):
            raise ServiceHostError("DUPLICATE_AUTHENTICATED_PRINCIPAL")

        if not isinstance(self.grants, Mapping):
            raise ServiceHostError("INVALID_ACL_GRANTS")
        grants: dict[str, tuple[PrincipalGrant, ...]] = {}
        for principal_ref, entries in self.grants.items():
            if (
                type(principal_ref) is not str
                or _SAFE_REF.fullmatch(principal_ref) is None
                or type(entries) is not tuple
                or any(type(entry) is not PrincipalGrant for entry in entries)
            ):
                raise ServiceHostError("INVALID_ACL_GRANTS")
            grants[principal_ref] = entries
        if set(principals.values()) != set(grants):
            raise ServiceHostError("PRINCIPAL_GRANT_SET_MISMATCH")
        group_write_policies = self.group_write_policies
        if type(group_write_policies) is not tuple or any(
            type(policy) is not TrustedGroupWritePolicy for policy in group_write_policies
        ):
            raise ServiceHostError("INVALID_GROUP_WRITE_POLICIES")
        group_reservation_policies = self.group_reservation_policies
        if type(group_reservation_policies) is not tuple or any(
            type(policy) is not TrustedGroupReservationPolicy
            for policy in group_reservation_policies
        ):
            raise ServiceHostError("INVALID_GROUP_RESERVATION_POLICIES")
        group_resolution_policies = self.group_resolution_policies
        if type(group_resolution_policies) is not tuple or any(
            type(policy) is not TrustedGroupResolutionPolicy for policy in group_resolution_policies
        ):
            raise ServiceHostError("INVALID_GROUP_RESOLUTION_POLICIES")

        object.__setattr__(self, "principals", MappingProxyType(principals))
        object.__setattr__(self, "grants", MappingProxyType(grants))
        object.__setattr__(self, "group_write_policies", group_write_policies)
        object.__setattr__(self, "group_reservation_policies", group_reservation_policies)
        object.__setattr__(self, "group_resolution_policies", group_resolution_policies)


@dataclass(frozen=True, slots=True)
class TrustedServiceClients:
    """Trusted adapters for resolving and opening configured service clients.

    These callables are supplied by operator-owned application code. Parsed
    YAML can select service IDs, refs, and fixed ports, but cannot create or
    import a resolver, peer identity, or transport implementation.
    """

    scope_resolver: Callable[[str], WireScope]
    peer_resolver: Callable[[str], WireIdentity]
    transport_factory: Callable[[Mapping[str, Any], WireIdentity], RequestReplyTransport]
    requested_operations: Mapping[str, frozenset[str]] | None = None

    def __post_init__(self) -> None:
        if not callable(self.scope_resolver):
            raise ServiceHostError("INVALID_TRUSTED_CLIENTS")
        if not callable(self.peer_resolver):
            raise ServiceHostError("INVALID_TRUSTED_CLIENTS")
        if not callable(self.transport_factory):
            raise ServiceHostError("INVALID_TRUSTED_CLIENTS")
        requested = self.requested_operations
        if requested is None:
            return
        if not isinstance(requested, Mapping):
            raise ServiceHostError("INVALID_TRUSTED_CLIENTS")
        snapshot = dict(requested)
        if any(
            type(mount) is not str
            or type(operations) is not frozenset
            or not operations
            or any(type(operation) is not str for operation in operations)
            for mount, operations in snapshot.items()
        ):
            raise ServiceHostError("INVALID_TRUSTED_CLIENTS")
        object.__setattr__(self, "requested_operations", MappingProxyType(snapshot))


class ServiceHost:
    """Own one selected service's registry, fixed components, and listener.

    The constructor performs no filesystem, network, plugin, or domain-package
    work. ZMQ server creation, serving, and close happen on the thread calling
    :meth:`start`; subsequent ``serve_once`` and ``close`` calls must use that
    same thread.

    A resource provider can create its resources when called during ``start``,
    after Host's static component, authority and listener checks. In particular,
    fresh-only resources must not be constructed eagerly if those checks should
    precede their irreversible initialization. Later startup failures can still
    occur after resource creation; this ordering does not provide recovery.
    """

    # Iteration 63 Gateway resources. ``__init__`` installs per-instance
    # containers; these inert class defaults keep hosts assembled without
    # ``__init__`` (older unit fixtures) on their pre-iteration-63 paths.
    _gateway_lease_drains: tuple = ()
    _gateway_cancel_only_runs: tuple = ()
    _gateway_delegations: tuple = ()
    _gateway_parent_services: tuple = ()
    _gateway_lease_faults: frozenset = frozenset()
    _trusted_atomic_monitors: tuple = ()
    _readiness_diagnostic_lock = threading.Lock()
    _readiness_diagnostic = ("unknown", None, None)

    def __init__(
        self,
        config: ServiceConfig,
        authority: TrustedServiceAuthority,
        registry_path: str | Path,
        resource_provider: ServiceResourceProvider,
        *,
        service_directory: Mapping[str, ServiceConfig] | None = None,
        trusted_clients: TrustedServiceClients | None = None,
        migration_deployment_epoch: str | None = None,
        risk_admin_cancel_service: object | None = None,
        trusted_atomic_monitors: tuple[object, ...] = (),
        enable_risk_parent_execution_writes: bool = False,
    ) -> None:
        if type(config) is not ServiceConfig:
            raise ServiceHostError("PARSED_SERVICE_CONFIG_REQUIRED")
        try:
            config.to_public_dict()
        except Exception:
            raise ServiceHostError("PARSED_SERVICE_CONFIG_REQUIRED") from None
        if type(authority) is not TrustedServiceAuthority:
            raise ServiceHostError("TRUSTED_AUTHORITY_REQUIRED")
        if not callable(resource_provider):
            raise ServiceHostError("RESOURCE_PROVIDER_REQUIRED")
        if not isinstance(registry_path, (str, Path)) or not str(registry_path):
            raise ServiceHostError("REGISTRY_PATH_REQUIRED")
        if service_directory is not None and not isinstance(service_directory, Mapping):
            raise ServiceHostError("INVALID_SERVICE_DIRECTORY")
        if trusted_clients is not None and type(trusted_clients) is not TrustedServiceClients:
            raise ServiceHostError("INVALID_TRUSTED_CLIENTS")
        if migration_deployment_epoch is not None and (
            type(migration_deployment_epoch) is not str or not migration_deployment_epoch
        ):
            raise ServiceHostError("INVALID_DEPLOYMENT_EPOCH")
        if type(trusted_atomic_monitors) is not tuple or len(trusted_atomic_monitors) > 16:
            raise ServiceHostError("INVALID_ATOMIC_MONITOR_CONFIGURATION")
        if trusted_atomic_monitors:
            # Keep Monitor and Execution entirely optional on the ordinary path.
            from .service_atomic_monitor_assembly import TrustedAtomicMonitor
            if any(type(item) is not TrustedAtomicMonitor for item in trusted_atomic_monitors):
                raise ServiceHostError("INVALID_ATOMIC_MONITOR_CONFIGURATION")
            # Event streams are strategy scoped, independent of trading day or
            # alternate wire domains; never start two workers for one stream.
            sources = [(item.source_scope.account_key, item.source_scope.strategy_id)
                       for item in trusted_atomic_monitors]
            checkpoints = [str(Path(item.checkpoint_path).resolve()) for item in trusted_atomic_monitors]
            if len(set(sources)) != len(sources) or len(set(checkpoints)) != len(checkpoints):
                raise ServiceHostError("INVALID_ATOMIC_MONITOR_CONFIGURATION")

        self._config = config
        self._authority = authority
        self._registry_path = Path(registry_path)
        self._resource_provider = resource_provider
        self._service_directory = (
            None if service_directory is None else MappingProxyType(dict(service_directory))
        )
        self._trusted_clients = trusted_clients
        self._migration_deployment_epoch = migration_deployment_epoch
        self._trusted_atomic_monitors = trusted_atomic_monitors
        self._atomic_monitor_owners: list[object] = []
        self._atomic_monitor_pending = list(trusted_atomic_monitors)
        self._atomic_monitor_faults: set[str] = set()
        self._atomic_monitor_indices = {id(item): index for index, item in enumerate(trusted_atomic_monitors)}
        self._atomic_monitor_histories = [set() for _ in trusted_atomic_monitors]
        self._atomic_monitor_next_attempt = 0.0
        try:
            self._specs = component_specs(config.components)
        except ServiceComponentError as exc:
            raise ServiceHostError(exc.code) from None
        if risk_admin_cancel_service is not None:
            from bt_api_py.risk_service_component import RiskOrderService

            risk_specs = [spec for spec in self._specs if spec.component == "risk"
                          and spec.options.get("role") == "order_service"]
            if (type(risk_admin_cancel_service) is not RiskOrderService
                    or getattr(risk_admin_cancel_service, "_strict_admin_cancel", None) is not True
                    or len(risk_specs) != 1 or config.clients):
                raise ServiceHostError("RISK_ADMIN_CANCEL_COMPOSITION_INVALID")
        if type(enable_risk_parent_execution_writes) is not bool:
            raise ServiceHostError("INVALID_RISK_PARENT_EXECUTION_CONFIGURATION")
        self._risk_admin_cancel_service = risk_admin_cancel_service
        self._enable_risk_parent_execution_writes = enable_risk_parent_execution_writes
        self._state = "new"
        self._owner_thread: int | None = None
        self._prepared: tuple[PreparedComponent, ...] = ()
        self._components: list[ServiceComponent] = []
        self._gateway_lease_drains: list[object] = []
        self._gateway_cancel_only_runs: list[object] = []
        self._gateway_delegations: list[object] = []
        self._gateway_parent_services: list[object] = []
        self._gateway_owner_guard_lock = threading.Lock()
        self._gateway_owner_guard_active = True
        self._gateway_owner_guard_bindings: dict[str, tuple[object, ...]] = {}
        self._gateway_lease_faults: set[str] = set()
        self._pending_resources: list[tuple[str, str, ComponentResources]] = []
        self._group_runtime_resources: list[tuple[PreparedComponent, ComponentResources]] = []
        self._registry: ServiceRegistry | None = None
        self._reply_size_checker: Callable[[Any, Mapping[str, Any], int | None], bool] | None = None
        self._reply_result_budget_checker: Callable[[Any, int | None], int] | None = None
        self._server: Any = None
        self._upstream: ServiceClientRuntime | None = None
        self._upstream_registration_summary: RegistrationSummary | None = None
        self._upstream_faulted = False
        self._execution_parent_keys: frozenset[tuple[object, ...]] = frozenset()
        self._atomic_parent_keys: frozenset[tuple[object, ...]] = frozenset()
        self._atomic_action_grants: tuple[object, ...] = ()
        self._atomic_intake_services: tuple[object, ...] = ()
        self._execution_parent_active: dict[tuple[object, ...], object] = {}
        self._execution_submission_active = False
        self._last_upstream_cleanup: Mapping[str, Any] | None = None
        self._paper_composition_modes: dict[str, str] = {}
        self._paper_monitor_relays: list[tuple[str, object, object]] = []
        self._paper_monitor_faults: dict[str, str] = {}
        self._market_publisher: Any = None
        self._market_transport_module: Any = None
        self._market_startup_cleanup_error_type: type[BaseException] | None = None
        self._market_endpoint: str | None = None
        self._planned_market_wire_version: int | None = None
        self._market_active_registration_count = 0
        self._component_maintenance_faults: set[str] = set()
        self._component_delay_faults: set[str] = set()
        self._host_delay_faulted = False
        self._readiness_diagnostic_lock = threading.Lock()
        self._readiness_diagnostic = ("unknown", None, None)
        self._endpoint: str | None = None
        self._start_failure: str | None = None
        self._last_cleanup_failures: tuple[str, ...] = ()
        self._cleanup_history: list[str] = []

    @property
    def endpoint(self) -> str | None:
        """The transport's bound endpoint after successful startup."""

        return self._endpoint

    @property
    def market_endpoint(self) -> str | None:
        """The market PUB endpoint after successful market-hub startup."""

        return self._market_endpoint

    @property
    def state(self) -> str:
        return self._state

    @property
    def readiness_diagnostics(self) -> Mapping[str, str | None]:
        """Return the last readiness observation and most recent hard failure.

        Values are finite internal codes. This snapshot does not probe a
        component or assert current Registry admission or account authority.
        """
        with self._readiness_diagnostic_lock:
            state, reason, last_failure = self._readiness_diagnostic
        return MappingProxyType({
            "state": state,
            "reason": reason,
            "last_failure": last_failure,
        })

    @property
    def summary(self) -> Mapping[str, Any]:
        """Return selected instance and capability metadata without secrets."""

        by_instance = {item.instance_id: item for item in self._components}
        components = tuple(
            _component_summary(spec, by_instance.get(spec.instance_id)) for spec in self._specs
        )
        return MappingProxyType(
            {
                "service_id": self._config.service_id,
                "state": self._state,
                "endpoint": self._endpoint,
                "components": components,
                "start_failure": self._start_failure,
                "cleanup_failures": self._last_cleanup_failures,
                "cleanup_history": tuple(self._cleanup_history),
                "upstream": self._upstream_summary(),
                "last_upstream_cleanup": self._last_upstream_cleanup,
                # Last completed observation, not an additional readiness probe.
                "readiness": self.readiness_diagnostics,
                "atomic_monitor": MappingProxyType({
                    "selected": len(self._trusted_atomic_monitors),
                    "owners": len(self._atomic_monitor_owners),
                    "pending": len(self._atomic_monitor_pending),
                    # Historical finite codes do not assert present failure.
                    "fault_history": tuple(sorted(self._atomic_monitor_faults)),
                    # Indices refer to the caller's trusted selection tuple;
                    # sources with failed startup remain independently visible.
                    "selection_history": tuple(tuple(sorted(history)) for history in self._atomic_monitor_histories),
                    "pending_selections": tuple(self._atomic_monitor_indices[id(item)]
                                                for item in self._atomic_monitor_pending),
                    "states": tuple(MappingProxyType({
                        "selection": self._atomic_monitor_indices[id(assembly.selection)],
                        "outcome": status.outcome, "phase": status.phase,
                        "cleanup": status.cleanup,
                        "fault": None if assembly.resource is None else assembly.resource.fault_code,
                    }) for assembly in self._atomic_monitor_owners
                        for status in (assembly.owner.status(),)),
                }),
                "paper": MappingProxyType(
                    {
                        "compositions": MappingProxyType(dict(self._paper_composition_modes)),
                        "monitor_relays": len(self._paper_monitor_relays),
                        "monitor_faults": MappingProxyType(dict(self._paper_monitor_faults)),
                    }
                ),
                "market": MappingProxyType(
                    {
                        "endpoint": self._market_endpoint,
                        "active_registrations": self._market_active_registration_count,
                        "maintenance_failures": tuple(
                            sorted(
                                self._component_maintenance_faults
                                | self._component_delay_faults
                            )
                        ),
                    }
                ),
            }
        )

    def start(self) -> ServiceHost:
        """Validate, resolve, and start the selected service on this thread."""

        if self._state == "started":
            self._check_owner_thread()
            return self
        if self._state != "new":
            raise ServiceHostError("INVALID_HOST_STATE")
        self._owner_thread = threading.get_ident()
        migration_guard = None
        migration_start_snapshot = None
        try:
            # This must precede the migration lock and every startup side effect,
            # including for configurations constructed without the parser.
            for spec in self._specs:
                _reject_retired_execution_owner(spec)
            # Hold the persistent marker lock through every startup side
            # effect. Otherwise an old Host could pass a marker-absent check,
            # then race the first arm while it is still constructing its
            # Registry or provider resources. Already-running writers still
            # require the offline boundary's real quiescence proof.
            if str(self._registry_path) != ":memory:":
                migration_guard = migration_start_guard(
                    self._registry_path,
                    deployment_epoch=self._migration_deployment_epoch,
                )
                try:
                    migration_start_snapshot = migration_guard.__enter__()
                except MigrationGateError as exc:
                    migration_guard = None
                    raise ServiceHostError(exc.code) from None
            self._validate_authority_references()
            self._validate_outbound_bindings()

            # Package validators run for every selected component before the
            # first call to the trusted resource provider.
            ordered = order_components(validate_components(self._specs))
            market_items = tuple(
                item for item in ordered if "service.market_source" in item.operations_by_port
            )
            if len(market_items) > 1:
                raise ServiceHostError("MULTIPLE_MARKET_SOURCE_COMPONENTS")
            listener_profile = self._listener_profile()
            selected_endpoints = listener_profile["endpoints"]
            listener_options = listener_profile["options"]
            market_wire_version = listener_options.get("market_wire_version", 1)
            if type(market_wire_version) is not int or market_wire_version not in (1, 2):
                raise ServiceHostError("MARKET_WIRE_VERSION_INVALID")
            self._planned_market_wire_version = (
                market_wire_version if "market_bind" in selected_endpoints else None
            )
            if "market_bind" in selected_endpoints and not market_items:
                raise ServiceHostError("MARKET_ENDPOINT_WITHOUT_COMPONENT")
            if market_items and "market_bind" not in selected_endpoints:
                raise ServiceHostError("MARKET_ENDPOINT_MISSING")
            # Resolve the market hub first. Its trusted audience is checked
            # before opening either of the two service listeners.
            self._prepared = (
                (market_items[0],) + tuple(item for item in ordered if item not in market_items)
                if market_items
                else ordered
            )
            planned_capabilities = tuple(
                ServiceCapability(
                    item.spec.instance_id,
                    port,
                    operations,
                    item.write_operations_by_port[port],
                    market_wire_version=(
                        market_wire_version if port == "service.market_source" else None
                    ),
                )
                for item in self._prepared
                for port, operations in item.operations_by_port.items()
            )
            if not planned_capabilities:
                raise ServiceHostError("NO_SUPPORTED_PUBLIC_PORTS")
            try:
                validate_capabilities_and_grants(planned_capabilities, self._authority.grants)
            except (TypeError, ValueError):
                raise ServiceHostError("AUTHORITY_GRANTS_INVALID") from None

            # Load and validate the selected listener configuration before
            # asking the operator resource provider for any domain objects.
            # This imports the transport only during start and does not create
            # a context, socket, or authenticator.
            try:
                from bt_api_transport_zmq.service_v2 import (  # type: ignore[import-not-found]
                    CurveServerCredentials,
                    ServiceTransportStartupCleanupError,
                    ZmqServiceServer,
                    service_reply_fits,
                    service_reply_result_budget,
                    validate_service_server_configuration,
                )

                listener_profile = self._listener_profile()
                listener_endpoint = listener_profile["endpoints"]["rpc_bind"]
                listener_options = listener_profile["options"]
                server_credentials = CurveServerCredentials(
                    public_key=self._authority.curve_public_key,
                    secret_key=self._authority.curve_secret_key,
                )
                validate_service_server_configuration(
                    listener_endpoint,
                    self._authority.identity,
                    self._authority.principals,
                    credentials=server_credentials,
                    high_water_mark=listener_options["high_water_mark"],
                    max_message_bytes=listener_options["max_message_bytes"],
                )
                listener_max_message_bytes = listener_options["max_message_bytes"]

                def reply_size_checker(
                    request: Any,
                    payload: Mapping[str, Any],
                    client_limit: int | None,
                    *,
                    service_identity: WireIdentity = self._authority.identity,
                    server_limit: int = listener_max_message_bytes,
                    checker: Callable[..., bool] = service_reply_fits,
                ) -> bool:
                    effective_limit = (
                        server_limit if client_limit is None else min(server_limit, client_limit)
                    )
                    return checker(
                        service_identity,
                        request,
                        payload,
                        max_message_bytes=effective_limit,
                    )

                self._reply_size_checker = reply_size_checker

                def reply_result_budget_checker(
                    request: Any,
                    client_limit: int | None,
                    *,
                    service_identity: WireIdentity = self._authority.identity,
                    server_limit: int = listener_max_message_bytes,
                    budgeter: Callable[..., int] = service_reply_result_budget,
                ) -> int:
                    effective_limit = (
                        server_limit if client_limit is None else min(server_limit, client_limit)
                    )
                    return budgeter(
                        service_identity,
                        request,
                        max_message_bytes=effective_limit,
                    )

                self._reply_result_budget_checker = reply_result_budget_checker
            except ImportError:
                raise ServiceHostError("TRANSPORT_UNAVAILABLE") from None
            except Exception:
                raise ServiceHostError("TRANSPORT_CONFIGURATION_INVALID") from None

            market_transport_module = None
            if market_items:
                market_endpoint = listener_profile["endpoints"].get("market_bind")
                market_options = listener_profile["options"]
                if type(market_endpoint) is not str:
                    raise ServiceHostError("MARKET_ENDPOINT_MISSING")
                if self._authority.identity.delegate_ref is not None:
                    raise ServiceHostError("MARKET_IDENTITY_INVALID")
                if self._authority.scope != WireScope(
                    execution_domain_ref=self._authority.scope.execution_domain_ref
                ):
                    raise ServiceHostError("MARKET_SCOPE_INVALID")
                try:
                    from bt_api_transport_zmq import (
                        market_v2 as market_transport_module,  # type: ignore[import-not-found]
                    )

                    publisher_validation_options = {
                        "credentials": server_credentials,
                        "high_water_mark": market_options.get("high_water_mark", 1000),
                        "max_message_bytes": market_options.get("max_message_bytes", 1_048_576),
                    }
                    if market_wire_version == 2:
                        publisher_validation_options["wire_version"] = 2
                    market_transport_module.validate_publisher_configuration(
                        market_endpoint,
                        self._authority.identity,
                        self._authority.scope,
                        self._authority.principals,
                        **publisher_validation_options,
                    )
                except ImportError:
                    raise ServiceHostError("MARKET_TRANSPORT_UNAVAILABLE") from None
                except Exception:
                    raise ServiceHostError("MARKET_TRANSPORT_CONFIGURATION_INVALID") from None
                self._market_transport_module = market_transport_module
                self._market_startup_cleanup_error_type = ServiceTransportStartupCleanupError

            # Runtime construction is pure. Keep the runtime reference before
            # starting it so a partial registration/transport failure remains
            # available to the common retryable cleanup path.
            if self._config.clients:
                bundle = self._trusted_clients
                if bundle is None:
                    raise ServiceHostError("TRUSTED_CLIENTS_REQUIRED")
                execution_upstream = any(item.spec.component == 'execution'
                    and item.spec.options.get('role') == 'fresh_intake' for item in self._prepared)
                # One Host owns one Runtime. Mixed Risk and Execution need
                # independently isolated registration lifecycles first.
                if execution_upstream and any(item.spec.component == 'risk' for item in self._prepared):
                    raise ServiceHostError('EXECUTION_RISK_SHARED_UPSTREAM_UNSUPPORTED')
                try:
                    self._upstream = ServiceClientRuntime(
                        self._config,
                        identity=self._authority.identity,
                        scope_resolver=bundle.scope_resolver,
                        peer_resolver=bundle.peer_resolver,
                        transport_factory=bundle.transport_factory,
                        requested_operations=bundle.requested_operations,
                        execution_upstream_lease=execution_upstream,
                    )
                except Exception:
                    raise ServiceHostError("UPSTREAM_CONFIGURATION_INVALID") from None
                try:
                    self._upstream_registration_summary = self._upstream.start()
                    self._upstream_faulted = not self._upstream.healthy
                except BaseException as exc:
                    self._upstream_faulted = True
                    if not isinstance(exc, Exception):
                        raise
                    raise ServiceHostError("UPSTREAM_START_FAILED") from None

            built_by_instance: dict[str, ServiceComponent] = {}
            paper_owner_resources: list[
                tuple[PreparedComponent, ServiceComponent, ComponentResources]
            ] = []
            trusted_paper_owner_write_policies: list[TrustedPaperOwnerWritePolicy] = []
            trusted_paper_legacy_import_policies: list[
                TrustedPaperLegacyImportPolicy
            ] = []
            owner_reduce_omission_allowed: set[str] = set()
            owner_optional_entry_omission_allowed: set[str] = set()
            account_read_metadata_omission_allowed: set[str] = set()
            trading_account_optional_omissions: dict[str, frozenset[str]] = {}
            gateway_heartbeat_resources: list[tuple[str, object]] = []
            gateway_claim_resources: list[ComponentResources] = []
            gateway_calendar_resources: list[tuple[str, object]] = []
            gateway_owner_guard_resources: list[tuple[PreparedComponent, ServiceComponent, ComponentResources]] = []
            risk_admin_component = None
            execution_parent_grants: list[object] = []
            atomic_intake_services: list[object] = []
            gateway_subject_bindings: list[object] = []
            gateway_acceptance_services: list[object] = []
            for item in self._prepared:
                dependency_ref = (
                    item.options.get("provider_session_ref")
                    if item.spec.component in {"execution", "risk"}
                    else None
                )
                trusted_internal_ports: Mapping[str, object] | None = None
                if dependency_ref is not None:
                    dependency = built_by_instance.get(dependency_ref)
                    if (
                        dependency is None
                        or "service.provider_session" not in dependency.internal_ports
                    ):
                        raise ServiceHostError("PROVIDER_SESSION_UNAVAILABLE")
                    if (
                        item.spec.component == "risk"
                        and item.spec.options.get("role") == "account_owner"
                    ):
                        worker = dependency.internal_ports.get("service.provider_session")
                        if worker is None:
                            raise ServiceHostError("PROVIDER_SESSION_UNAVAILABLE")
                        trusted_internal_ports = {
                            "service.provider_session": worker
                        }

                borrowed_upstream = None
                if (
                    item.spec.component == "execution"
                    and item.spec.options.get("role") == "group_coordinator"
                ):
                    if self._upstream is None:
                        raise ServiceHostError("GROUP_COORDINATOR_UPSTREAM_REQUIRED")
                    try:
                        borrowed_upstream = BorrowedServiceClient(self._upstream)
                    except ServiceComponentError as exc:
                        raise ServiceHostError(exc.code) from None
                component = build_component(
                    item,
                    scope=self._authority.scope,
                    resource_provider=self._resource_provider,
                    on_resources=lambda resource, prepared_item=item: self._track_resources(
                        prepared_item, resource
                    ),
                    borrowed_upstream=borrowed_upstream,
                    service_identity=self._authority.identity,
                    trusted_group_write_policies=tuple(
                        policy
                        for policy in self._authority.group_write_policies
                        if policy.instance_id == item.spec.instance_id
                    ),
                    trusted_group_reservation_policies=tuple(
                        policy
                        for policy in self._authority.group_reservation_policies
                        if policy.instance_id == item.spec.instance_id
                    ),
                    trusted_group_resolution_policies=tuple(
                        policy
                        for policy in self._authority.group_resolution_policies
                        if policy.instance_id == item.spec.instance_id
                    ),
                    trusted_internal_ports=trusted_internal_ports,
                )
                self._components.append(component)
                if (
                    not self._pending_resources
                    or self._pending_resources[-1][0] != item.spec.component
                    or self._pending_resources[-1][1] != item.spec.instance_id
                ):
                    raise ServiceHostError("RESOURCE_TRACKING_FAILED")
                pending_resources = self._pending_resources[-1][2]
                if item.spec.component == "risk" and item.spec.options.get("role") == "order_service":
                    from bt_api_py.risk_service_component import RiskOrderService

                    adapter = pending_resources.values.get("service")
                    if (type(adapter) is not RiskOrderService
                            or type(getattr(adapter, "_strict_admin_cancel", None)) is not bool
                            or adapter._strict_admin_cancel is not (self._risk_admin_cancel_service is not None)
                            or (self._risk_admin_cancel_service is not None
                                and adapter is not self._risk_admin_cancel_service)):
                        raise ServiceHostError("RISK_ADMIN_CANCEL_COMPOSITION_INVALID")
                    if self._risk_admin_cancel_service is not None:
                        risk_admin_component = component
                if (item.spec.component == "gateway"
                        and item.spec.options.get("role") == "trading_owner"
                        and "owner_guard_binding" in pending_resources.values):
                    gateway_owner_guard_resources.append((item, component, pending_resources))
                if item.spec.component == "execution" and item.spec.options.get("role") == "fresh_intake":
                    service = pending_resources.values["service"]
                    # build_component has already checked its exact type and
                    # instance; preserve the same immutable grant objects.
                    if not self._execution_parent_gateway_ready(service.grants):
                        raise ServiceHostError("EXECUTION_GATEWAY_UNAVAILABLE")
                    execution_parent_grants.extend(service.grants)
                if item.spec.component == "execution" and item.spec.options.get("role") == "atomic_intake":
                    atomic_intake_services.append(pending_resources.values["service"])
                if item.spec.component == 'gateway' and item.spec.options.get('role') == 'trading_owner':
                    from bt_api_py import gateway_direct_service as direct_module
                    omitted = frozenset()
                    if 'instrument_service' not in pending_resources.values:
                        omitted |= direct_module.INSTRUMENT_OPERATIONS
                    if 'account_observer' not in pending_resources.values:
                        omitted |= direct_module.ACCOUNT_OBSERVER_OPERATIONS
                    if 'account_facts' not in pending_resources.values:
                        omitted |= direct_module.ACCOUNT_FACTS_OPERATIONS
                    if 'order_cut_source' not in pending_resources.values:
                        omitted |= direct_module.ACCOUNT_ORDER_CUT_OPERATIONS
                    if 'account_cancel_attempts' not in pending_resources.values:
                        omitted |= direct_module.ACCOUNT_CANCEL_ATTEMPT_OPERATIONS
                    trading_account_optional_omissions[item.spec.instance_id] = omitted
                    calendar = pending_resources.values.get('calendar_service')
                    if calendar is not None:
                        from bt_api_py.gateway_calendar_service import GatewayCalendarService
                        caps = [cap for cap in component.capabilities if cap.port == 'service.calendar_read']
                        if (type(calendar) is not GatewayCalendarService or len(caps) != 1
                                or caps[0].instance_id != item.spec.instance_id
                                or caps[0].operations != frozenset({'calendar.query.v1', 'calendar.windows.v1', 'calendar.monitor.windows.v1'})
                                or caps[0].write_operations or not {'calendar.query.v1', 'calendar.windows.v1', 'calendar.monitor.windows.v1'} <= component.handlers.keys()
                                or any(other is calendar for _, other in gateway_calendar_resources)):
                            raise ServiceHostError('GATEWAY_CALENDAR_INVALID')
                        gateway_calendar_resources.append((item.spec.instance_id, calendar))
                    subject_binding = pending_resources.values.get('execution_subject_binding')
                    if subject_binding is not None:
                        gateway_subject_bindings.append(subject_binding)
                if item.spec.component == 'gateway' and item.spec.options.get('role') == 'trading_owner' and 'delegation_service' in pending_resources.values:
                    from bt_api_py.gateway_delegation_service import GatewayDelegationService
                    delegation = pending_resources.values['delegation_service']
                    if type(delegation) is not GatewayDelegationService or delegation.instance_id != item.spec.instance_id:
                        raise ServiceHostError('GATEWAY_DELEGATION_INVALID')
                    self._gateway_delegations.append(delegation)
                if item.spec.component == 'gateway' and item.spec.options.get('role') == 'trading_owner' and 'parent_service' in pending_resources.values:
                    from bt_api_py.gateway_parent_service import GatewayParentService
                    parent_service = pending_resources.values['parent_service']
                    if type(parent_service) is not GatewayParentService or parent_service.instance_id != item.spec.instance_id:
                        raise ServiceHostError('GATEWAY_PARENT_INVALID')
                    binding = pending_resources.values.get('execution_subject_binding')
                    if binding is None or any(
                            WireScope(grant.execution_domain_ref, account_ref=grant.account_scope,
                                      strategy_ref=grant.strategy_scope) not in binding.declaration.aliases
                            for grant in parent_service.settings.grants):
                        raise ServiceHostError('EXECUTION_SUBJECT_SCOPES_INVALID')
                    self._gateway_parent_services.append(parent_service)
                if item.spec.component == 'gateway' and item.spec.options.get('role') == 'trading_owner' and 'parent_acceptance_service' in pending_resources.values:
                    from bt_api_py.gateway_parent_acceptance_service import GatewayParentAcceptanceService
                    acceptance = pending_resources.values['parent_acceptance_service']
                    if type(acceptance) is not GatewayParentAcceptanceService or acceptance.instance_id != item.spec.instance_id:
                        raise ServiceHostError('GATEWAY_ACCEPTANCE_INVALID')
                    gateway_acceptance_services.append(acceptance)
                if item.spec.component == 'gateway' and 'claim_coordinator' in pending_resources.values:
                    # Keep the claim resources: pending entries are popped below,
                    # before the Registry exists to be attached to them.
                    gateway_claim_resources.append(pending_resources)
                if item.spec.component == "gateway" and item.spec.options.get("role") == "trading_owner" and "lease_drain" in pending_resources.values:
                    from bt_api_py.gateway_lease_drain import GatewayLeaseDrain
                    lease_drain = pending_resources.values["lease_drain"]
                    if type(lease_drain) is not GatewayLeaseDrain or lease_drain.instance_id != item.spec.instance_id:
                        raise ServiceHostError("GATEWAY_LEASE_DRAIN_INVALID")
                    self._gateway_lease_drains.append(lease_drain)
                if item.spec.component == "gateway" and item.spec.options.get("role") == "trading_owner" and "cancel_only" in pending_resources.values:
                    from bt_api_py.gateway_cancel_only import GatewayCancelOnlyRun
                    cancel_only = pending_resources.values["cancel_only"]
                    if type(cancel_only) is not GatewayCancelOnlyRun or cancel_only.instance_id != item.spec.instance_id:
                        raise ServiceHostError("GATEWAY_CANCEL_ONLY_INVALID")
                    self._gateway_cancel_only_runs.append(cancel_only)
                if item.spec.component == "monitor" and item.spec.options.get("role") == "gateway_heartbeat":
                    heartbeat = pending_resources.values.get("service")
                    if getattr(heartbeat, "monitor_principal", None) != self._authority.identity.principal_ref:
                        raise ServiceHostError("GATEWAY_HEARTBEAT_IDENTITY_MISMATCH")
                    gateway_heartbeat_resources.append((item.spec.instance_id, heartbeat))
                if _account_read_metadata_omission_allowed(
                    item.spec.component,
                    item.spec.options.get("role"),
                    pending_resources,
                    instance_id=item.spec.instance_id,
                    bind_ports=item.spec.bind_ports,
                    built_component=component,
                ):
                    account_read_metadata_omission_allowed.add(item.spec.instance_id)
                if (
                    item.spec.component == "execution"
                    and item.spec.options.get("role") == "account_owner"
                ):
                    if "approved_target_authority" not in pending_resources.values:
                        owner_reduce_omission_allowed.add(item.spec.instance_id)
                    admission_resources = pending_resources.values.get("admission")
                    if (
                        "approved_target_authority" not in pending_resources.values
                        and _RECOVERY_CLOSE_OPERATION
                        not in pending_resources.operation_readiness
                        and isinstance(admission_resources, Mapping)
                        and "recovery_close_authorizer" not in admission_resources
                    ):
                        owner_optional_entry_omission_allowed.add(item.spec.instance_id)
                    paper_owner_resources.append((item, component, pending_resources))
                trusted_paper_owner_write_policies.extend(
                    self._paper_owner_write_policies(item, pending_resources)
                )
                trusted_paper_legacy_import_policies.extend(
                    self._paper_legacy_import_policies(item, pending_resources)
                )
                self._pending_resources.pop()
                built_by_instance[component.instance_id] = component

            self._configure_paper_compositions(paper_owner_resources)
            self._validate_group_runtime_bundle_sharing()
            self._validate_shared_collateral_composition()

            component_capabilities = tuple(
                capability
                for component in self._components
                for capability in component.capabilities
            )
            # The Risk-first capability is declared in the built-in catalog but
            # belongs to an explicitly composed delegation service, not to the
            # ordinary delegation configuration. Remove it from the startup plan
            # unless that exact instance installed the trusted holder.
            risk_first_instances = {
                item.instance_id for item in self._gateway_delegations
                if item.settings.risk_first_enabled
            }
            planned_capabilities = tuple(
                ServiceCapability(
                    cap.instance_id, cap.port,
                    cap.operations - {"direct.risk_first_reserve.v2"},
                    cap.write_operations - {"direct.risk_first_reserve.v2"},
                    market_wire_version=cap.market_wire_version,
                ) if (cap.port == "service.direct_write"
                      and "direct.risk_first_reserve.v2" in cap.operations
                      and cap.instance_id not in risk_first_instances) else cap
                for cap in planned_capabilities
            )
            # Optional read sources are known only after resources resolve.
            # All other planned operations must remain exact.
            if len(component_capabilities) != len(planned_capabilities):
                raise ServiceHostError("CAPABILITY_PLAN_MISMATCH")
            capabilities = tuple(
                ServiceCapability(
                    actual.instance_id,
                    actual.port,
                    actual.operations,
                    actual.write_operations,
                    market_wire_version=(
                        planned.market_wire_version
                        if planned.port == "service.market_source"
                        else None
                    ),
                )
                for actual, planned in zip(
                    component_capabilities, planned_capabilities, strict=True
                )
            )
            if any(
                actual.instance_id != planned.instance_id
                or actual.port != planned.port
                or (
                    actual.operations != planned.operations
                    or actual.write_operations != planned.write_operations
                )
                and not (
                    (
                        planned.port == "service.direct_write"
                        and actual.operations == planned.operations - {
                            "direct.events.read_after", "direct.events.progress", "direct.events.ack"
                        }
                        and actual.write_operations == planned.write_operations
                    )
                    or (
                        planned.port == "service.direct_write"
                        and actual.operations in (
                            planned.operations - {"direct.instrument_status"},
                            planned.operations - {"direct.instrument_status", "direct.events.read_after",
                                                  "direct.events.progress", "direct.events.ack"},
                        )
                        and actual.write_operations == planned.write_operations
                    )
                    or (
                        planned.port == "service.market_source"
                        and actual.operations in (
                            planned.operations - {"market.history"},
                            planned.operations - {
                                "market.bootstrap_begin", "market.bootstrap_status",
                                "market.bootstrap_cancel",
                            },
                            planned.operations - {
                                "market.history", "market.bootstrap_begin",
                                "market.bootstrap_status", "market.bootstrap_cancel",
                            },
                            planned.operations - {"market.clock_observation"},
                            planned.operations - {"market.clock_observation", "market.history"},
                            planned.operations - {"market.clock_observation", "market.bootstrap_begin",
                                                  "market.bootstrap_status", "market.bootstrap_cancel"},
                            planned.operations - {"market.clock_observation", "market.history", "market.bootstrap_begin",
                                                  "market.bootstrap_status", "market.bootstrap_cancel"},
                        )
                        and actual.write_operations == planned.write_operations
                    )
                    or _matches_account_read_metadata_omission(
                        actual,
                        planned,
                        account_read_metadata_omission_allowed,
                    )
                    or (
                        planned.port == actual.port == "service.account_read"
                        and actual.operations == planned.operations - {
                            "account.instrument_spec.begin", "account.instrument_spec.status",
                            "account.instrument_spec.cancel",
                        }
                        and actual.write_operations == planned.write_operations
                    )
                    or (
                        planned.port == actual.port == 'service.account_read'
                        and planned.instance_id in trading_account_optional_omissions
                        and actual.operations == planned.operations - trading_account_optional_omissions[planned.instance_id]
                        and actual.write_operations == planned.write_operations
                    )
                    or (
                        planned.port == "service.order_query"
                        and actual.operations
                        == planned.operations - {"execution.account_books.query"}
                        and actual.write_operations == planned.write_operations
                    )
                    or (
                        planned.port == "service.owner_control"
                        and planned.instance_id in owner_reduce_omission_allowed
                        and actual.operations
                        == planned.operations - {"owner.reduce"}
                        and actual.write_operations
                        == planned.write_operations - {"owner.reduce"}
                    )
                    or (
                        planned.port == "service.order_entry"
                        and actual.operations
                        == planned.operations - {"orders.submit_approved_target"}
                        and actual.write_operations
                        == planned.write_operations - {"orders.submit_approved_target"}
                    )
                    or (
                        planned.port == "service.order_entry"
                        and actual.operations
                        == planned.operations - {"orders.submit_group_compensation_close"}
                        and actual.write_operations
                        == planned.write_operations
                        - {"orders.submit_group_compensation_close"}
                    )
                    or (
                        planned.port == "service.order_query"
                        and actual.operations
                        == planned.operations
                        - {
                            "orders.prepare_group_compensation_close",
                            "orders.query_group_compensation_close",
                        }
                        and actual.write_operations == planned.write_operations
                    )
                    or (
                        planned.port == "service.order_entry"
                        and actual.operations
                        == planned.operations
                        - {
                            "orders.submit_group_compensation_close",
                            "orders.submit_group_roll_exit",
                        }
                        and actual.write_operations
                        == planned.write_operations
                        - {
                            "orders.submit_group_compensation_close",
                            "orders.submit_group_roll_exit",
                        }
                    )
                    or (
                        planned.port == "service.order_query"
                        and actual.operations
                        == planned.operations
                        - {
                            "orders.prepare_group_compensation_close",
                            "orders.query_group_compensation_close",
                            "orders.prepare_group_roll_exit",
                            "orders.query_group_roll_exit",
                        }
                        and actual.write_operations == planned.write_operations
                    )
                    or (
                        planned.port == "service.order_entry"
                        and actual.operations
                        == planned.operations - {"orders.submit_group_roll_exit"}
                        and actual.write_operations
                        == planned.write_operations - {"orders.submit_group_roll_exit"}
                    )
                    or (
                        planned.port == "service.order_entry"
                        and actual.operations
                        == planned.operations - {"orders.submit_recovery_close"}
                        and actual.write_operations
                        == planned.write_operations - {"orders.submit_recovery_close"}
                    )
                    or (
                        planned.port == "service.order_entry"
                        and planned.instance_id in owner_optional_entry_omission_allowed
                        and actual.operations
                        == planned.operations
                        - {
                            "orders.submit_approved_target",
                            "orders.submit_recovery_close",
                        }
                        and actual.write_operations
                        == planned.write_operations
                        - {
                            "orders.submit_approved_target",
                            "orders.submit_recovery_close",
                        }
                    )
                    or (
                        planned.port == "service.order_query"
                        and actual.operations
                        == planned.operations
                        - {
                            "orders.prepare_group_roll_exit",
                            "orders.query_group_roll_exit",
                        }
                        and actual.write_operations == planned.write_operations
                    )
                )
                or actual.market_wire_version != planned.market_wire_version
                for actual, planned in zip(capabilities, planned_capabilities, strict=True)
            ):
                raise ServiceHostError("CAPABILITY_PLAN_MISMATCH")

            account_risk_policy_bindings = tuple(
                binding
                for component in self._components
                for binding in component.account_risk_policy_bindings
            )
            account_risk_policy_required_routes = frozenset(
                (component.instance_id, port)
                for component in self._components
                for port in component.account_risk_policy_required_ports
            )

            try:
                if self._risk_admin_cancel_service is not None:
                    handler = None if risk_admin_component is None else risk_admin_component.handlers.get(
                        "risk.cancel.request"
                    )
                    defaults = getattr(handler, "__kwdefaults__", None)
                    if (self._risk_admin_cancel_service._strict_admin_cancel is not True
                            or type(handler) is not FunctionType or type(defaults) is not dict
                            or defaults.get("_adapter") is not self._risk_admin_cancel_service
                            or defaults.get("_operation") != "risk.cancel.request"):
                        raise ServiceHostError("RISK_ADMIN_CANCEL_COMPOSITION_INVALID")
                self._registry = ServiceRegistry(
                    self._registry_path,
                    service_id=self._config.service_id,
                    capabilities=capabilities,
                    grants=_account_read_grants_for_capabilities(
                        self._authority.grants,
                        capabilities,
                        planned_capabilities,
                        account_read_metadata_omission_allowed,
                    ),
                    policy_version=self._authority.registration_policy_version,
                    requirements_version=self._authority.service_requirements_version,
                    access_policy_ref=self._authority.registration_policy_ref,
                    requirements_ref=self._authority.service_requirements_ref,
                    ready=False,
                    reply_size_checker=self._reply_size_checker,
                    reply_result_budget_checker=self._reply_result_budget_checker,
                    trusted_group_write_policies=self._authority.group_write_policies,
                    trusted_group_reservation_policies=self._authority.group_reservation_policies,
                    trusted_group_resolution_policies=self._authority.group_resolution_policies,
                    account_risk_policy_bindings=account_risk_policy_bindings,
                    account_risk_policy_required_routes=account_risk_policy_required_routes,
                    trusted_paper_owner_write_policies=tuple(
                        trusted_paper_owner_write_policies
                    ),
                    trusted_paper_legacy_import_policies=tuple(
                        trusted_paper_legacy_import_policies
                    ),
                    migration_deployment_epoch=self._migration_deployment_epoch,
                    migration_start_guard=migration_start_snapshot,
                    enable_risk_v2_dispatch_admission=self._enable_risk_parent_execution_writes,
                    enable_risk_parent_execution_writes=self._enable_risk_parent_execution_writes,
                    enable_risk_admin_cancel_admission=self._risk_admin_cancel_service is not None,
                    enable_gateway_risk_first_reserve_admission=any(
                        item.settings.risk_first_enabled for item in self._gateway_delegations
                    ),
                    enable_gateway_retired_session_ack=any(
                        grant.actor_kind == "risk"
                        for item in self._gateway_delegations
                        for grant in item.settings.grants
                    ),
                )
            except ServiceRegistryStartupCleanupError as exc:
                self._registry = exc.registry
                raise ServiceHostError("REGISTRY_STARTUP_CLEANUP_FAILED") from None
            except ServiceRegistrySchemaError as exc:
                # The registry closed its database and refused to open.
                raise ServiceHostError(exc.code) from None
            registry = self._registry
            try:
                for instance_id, calendar in gateway_calendar_resources:
                    matches = [cap for cap in capabilities if cap.instance_id == instance_id and cap.port == "service.calendar_read"]
                    if len(matches) != 1 or matches[0].operations != frozenset({"calendar.query.v1", "calendar.windows.v1", "calendar.monitor.windows.v1"}):
                        raise ValueError
                    calendar.bind_registry(registry)
            except Exception:
                raise ServiceHostError("GATEWAY_CALENDAR_INVALID") from None
            for prepared, component, resources in gateway_owner_guard_resources:
                expected = tuple(cap for cap in capabilities if cap.instance_id == component.instance_id)
                self._bind_gateway_owner_guard(registry, prepared, component, resources, expected)
            try:
                if gateway_subject_bindings:
                    accounts, aliases = set(), {}
                    for subject in gateway_subject_bindings:
                        declaration = subject.declaration
                        if accounts.intersection(declaration.protected_accounts) or set(aliases).intersection(declaration.aliases):
                            raise ValueError('conflicting execution subject scopes')
                        accounts.update(declaration.protected_accounts)
                        aliases.update(declaration.aliases)
                    registry.configure_execution_subject_scopes(protected_accounts=frozenset(accounts), aliases=aliases)
                else:
                    registry.require_no_execution_subject_scopes()
            except Exception:
                raise ServiceHostError('EXECUTION_SUBJECT_SCOPES_INVALID') from None
            if atomic_intake_services:
                try:
                    for service in atomic_intake_services:
                        registry.configure_strategy_canonical_targets(targets=service.trusted_strategy_targets)
                except Exception:
                    raise ServiceHostError("CANONICAL_STRATEGY_TARGETS_INVALID") from None
            all_parent_grants = tuple(execution_parent_grants) + tuple(
                grant for service in atomic_intake_services for grant in service.grants
            )
            if all_parent_grants:
                try:
                    registry.configure_execution_parents(grants=all_parent_grants)
                except Exception:
                    raise ServiceHostError("EXECUTION_PARENT_GRANTS_INVALID") from None
            if execution_parent_grants:
                self._execution_parent_keys = frozenset(
                    self._execution_parent_key(grant) for grant in execution_parent_grants
                )
                self._execution_parent_active = {
                    self._execution_parent_key(grant): grant for grant in execution_parent_grants
                }
            if atomic_intake_services:
                try:
                    atomic_actions = tuple(
                        grant for service in atomic_intake_services for grant in service.action_grants
                    )
                    registry.configure_execution_actions(grants=atomic_actions)
                    for service in atomic_intake_services:
                        service.bind_registry(registry)
                        service.start_lease_maintenance()
                except Exception:
                    raise ServiceHostError("ATOMIC_INTAKE_AUTHORITY_INVALID") from None
                self._atomic_parent_keys = frozenset(
                    self._execution_parent_key(grant)
                    for service in atomic_intake_services for grant in service.grants
                )
                self._execution_parent_keys = self._execution_parent_keys | self._atomic_parent_keys
                self._atomic_action_grants = atomic_actions
            for resources in gateway_claim_resources:
                if 'claim_coordinator' in resources.values:
                    binding = resources.values['binding']
                    registry.attach_dispatch_claim_coordinator(
                        resources.values['claim_coordinator'],
                        account_ref=binding._journal.account_scope,
                        owner_generation=binding._journal.generation,
                    )
                    sealer = resources.values.get('claim_sealer')
                    wired = resources.values.get('claim_wired')
                    if sealer is not None and type(wired) is set:
                        from bt_api_py.gateway_dispatch_claim import (
                            DispatchClaimError,
                            seal_gateway_dispatch_coverage,
                        )

                        wired.update({'service_registry.transactions',
                                      'service_registry.locked_mutations'})
                        try:
                            # Seal only when every mandatory writer is attached;
                            # otherwise dispatch stays refused (fail closed).
                            seal_gateway_dispatch_coverage(
                                resources.values['claim_coordinator'], sealer,
                                wired=frozenset(wired))
                        except DispatchClaimError as error:
                            if error.code != 'COVERAGE_INCOMPLETE':
                                raise
            for delegation in self._gateway_delegations:
                if delegation.settings.risk_first_enabled:
                    delegation.configure(registry, risk_first_host_check=(
                        lambda service=delegation, expected_registry=registry:
                            self._gateway_risk_first_owner_current(expected_registry, service)
                    ))
                else:
                    delegation.configure(registry)
            for parent_service in self._gateway_parent_services:
                parent_service.configure(registry)
            for acceptance in gateway_acceptance_services:
                acceptance.configure(registry)
            for lease_drain in self._gateway_lease_drains:
                lease_drain.configure(registry)
            for heartbeat_instance, heartbeat in gateway_heartbeat_resources:
                def validate_heartbeat_epoch(identity, scope, epoch, *, instance=heartbeat_instance,
                                             expected_registry=registry):
                    try:
                        if self._registry is not expected_registry or not expected_registry.ready:
                            return False
                        matches = tuple(
                            registration for registration in expected_registry.active_registration_contexts(
                                instance_id=instance, port="service.telemetry_ingress"
                            )
                            if registration.context.identity == identity
                            and registration.context.scope == scope
                            and "monitor.heartbeat" in registration.operations
                        )
                        return len(matches) == 1 and matches[0].context.registration_id == epoch
                    except Exception:
                        return False

                try:
                    heartbeat.bind_current_epoch_validator(validate_heartbeat_epoch)
                except Exception:
                    raise ServiceHostError("GATEWAY_HEARTBEAT_REGISTRY_BIND_FAILED") from None
            for component in self._components:
                for operation, handler in component.handlers.items():
                    if (component.component == "execution" and operation == "execution.parent.submit"
                            and any(item.spec.instance_id == component.instance_id
                                    and item.spec.options.get("role") == "fresh_intake"
                                    for item in self._prepared)):
                        def guarded_submit(context, arguments, *, _handler=handler):
                            permit = context.dispatch_admission
                            if self._execution_submission_active:
                                if permit is not None:
                                    permit.close()
                                raise ServiceHostError("EXECUTION_SUBMISSION_ACTIVE")
                            self._execution_submission_active = True
                            try:
                                grant = permit.execution_parent_grant_for(context, "execution.parent.submit", arguments)
                                binding = None if grant is None else self._execution_parent_gateway_binding(
                                    grant, scope=context.scope)
                                if binding is None:
                                    raise ServiceHostError("EXECUTION_GATEWAY_UNAVAILABLE")
                                permit.bind_execution_upstream(context, arguments, self._upstream, binding)
                                result = _handler(context, arguments)
                                if self._execution_parent_gateway_binding(grant, scope=context.scope) is not binding:
                                    # The journal may already contain a receipt. A handler
                                    # failure keeps the remote outcome UNKNOWN.
                                    raise ServiceHostError("EXECUTION_GATEWAY_UNAVAILABLE")
                                return result
                            except BaseException:
                                if permit is not None:
                                    permit.close()
                                raise
                            finally:
                                self._execution_submission_active = False
                        handler = guarded_submit
                    operation_readiness = component.operation_readiness.get(operation)
                    restricted_readiness = None
                    if operation_readiness is not None:
                        restricted_readiness = lambda owner=component, check=operation_readiness, route=operation: (
                            self._restricted_operation_ready(owner, check, operation=route)
                        )
                    self._registry.register_handler(
                        component.instance_id,
                        operation,
                        handler,
                        restricted_readiness=restricted_readiness,
                    )
                    if (component.component == 'execution' and operation == 'execution.parent.query'
                            and any(item.spec.instance_id == component.instance_id
                                    and item.spec.options.get('role') == 'fresh_intake'
                                    for item in self._prepared)):
                        self._registry._enable_execution_parent_read_leases(component.instance_id)

            # Reconcile durable registration-bound component state before the
            # listener can accept a request.  A Registry file may contain
            # still-live leases from an earlier Host process; group owners
            # must seed/activate their exact trusted run before those routes
            # become visible again.  This also keeps startup fail-closed when
            # the read-only eligibility projection or owner reconciliation
            # fails.
            self._maintain_components(registrations_only=True)
            if self._component_maintenance_faults:
                raise ServiceHostError("COMPONENT_MAINTENANCE_FAILED")
            self._poll_paper_monitor_relays()
            if self._paper_monitor_faults:
                raise ServiceHostError("PAPER_MONITOR_RELAY_START_FAILED")

            self._check_gateway_owner_lifecycles()

            # The same validated inputs are used to create the owner-thread
            # listener. Its constructor owns partial socket/authenticator
            # cleanup if binding or ZAP startup fails.
            try:
                self._server = ZmqServiceServer(
                    listener_endpoint,
                    self._authority.identity,
                    self._authority.principals,
                    self._dispatch_request,
                    credentials=server_credentials,
                    high_water_mark=listener_options["high_water_mark"],
                    max_message_bytes=listener_options["max_message_bytes"],
                )
            except ServiceTransportStartupCleanupError as exc:
                self._server = exc.transport
                raise ServiceHostError("TRANSPORT_STARTUP_CLEANUP_FAILED") from None
            self._check_gateway_owner_lifecycles()
            self._endpoint = self._server.endpoint

            if not self._all_components_ready():
                raise ServiceHostError("COMPONENT_NOT_READY")
            # Do not advertise local intake without the trusted lookup needed
            # to bind a query action to the original journal record.
            self._atomic_intake_services = tuple(atomic_intake_services)
            self._check_gateway_owner_lifecycles()
            self._refresh_registry_readiness()
            self._check_gateway_owner_lifecycles()
            self._state = "started"
            if migration_guard is not None:
                guard_to_exit = migration_guard
                migration_guard = None
                try:
                    guard_to_exit.__exit__(None, None, None)
                except MigrationGateError as exc:
                    raise ServiceHostError(exc.code) from None
            self._maintain_atomic_monitors(force=True)
            return self
        except BaseException as exc:
            failure_code = self._start_error_code(exc)
            cleanup_failures = self._shutdown_resources()
            if migration_guard is not None:
                guard_to_exit = migration_guard
                migration_guard = None
                try:
                    guard_to_exit.__exit__(type(exc), exc, exc.__traceback__)
                except BaseException:
                    cleanup_failures = (*cleanup_failures, "MIGRATION_GATE_RELEASE_FAILED")
            self._start_failure = failure_code
            self._last_cleanup_failures = cleanup_failures
            self._cleanup_history.extend(cleanup_failures)
            self._state = "close_failed" if cleanup_failures else "failed"
            if cleanup_failures:
                raise ServiceHostError("START_CLEANUP_FAILED") from None
            if not isinstance(exc, Exception):
                raise
            if isinstance(exc, ServiceHostError):
                raise
            if isinstance(exc, ServiceConfigError):
                raise ServiceHostError("SERVICE_CONFIGURATION_INVALID") from None
            if isinstance(exc, ServiceComponentError):
                raise ServiceHostError(exc.code) from None
            raise ServiceHostError("START_FAILED") from None

    def serve_once(self, timeout_ms: int = 0) -> bool:
        """Serve at most one authenticated request on the owner thread."""

        self._check_owner_thread()
        self._require_no_execution_submission()
        if self._state != "started" or self._server is None:
            raise ServiceHostError("HOST_NOT_STARTED")
        if type(timeout_ms) is not int or timeout_ms < 0:
            raise ServiceHostError("INVALID_TIMEOUT")
        try:
            # Renewal and polling share this owner thread. If renewal fails,
            # keep polling so discovery/unregister and other control traffic
            # remain available while business operations are withdrawn.
            if self._upstream is not None:
                try:
                    self.maintain()
                except ServiceHostError as exc:
                    if exc.code != "UPSTREAM_MAINTENANCE_FAILED":
                        raise
            self._refresh_registry_readiness()
            try:
                delay = self.next_maintenance_delay()
            except Exception:
                self._host_delay_faulted = True
                self._refresh_registry_readiness()
                raise
            bounded_timeout = timeout_ms
            if delay is not None:
                if type(delay) not in (int, float) or not isfinite(delay) or delay < 0:
                    # The aggregate includes local component delays, so an
                    # invalid value is a Host failure, not upstream evidence.
                    self._host_delay_faulted = True
                    self._refresh_registry_readiness()
                    bounded_timeout = 0
                else:
                    self._host_delay_faulted = False
                    delay_ms = delay * 1000
                    if isfinite(delay_ms):
                        bounded_timeout = min(timeout_ms, max(0, floor(delay_ms)))
            else:
                self._host_delay_faulted = False
            maintained = self._maintain_components()
            self._maintain_atomic_monitors()
            if maintained or self._trusted_atomic_monitors:
                self._refresh_registry_readiness()
            if self._trusted_atomic_monitors:
                bounded_timeout = min(bounded_timeout, 1000)
            if self._registry is not None and self._registry._parent_retry_unready:
                bounded_timeout = min(bounded_timeout, 10)
            handled = self._server.serve_once(bounded_timeout)
            # The validated single market-hub Host can defer the second sweep
            # after a request, or after an idle nonblocking poll. Its next
            # owner-loop iteration performs the same preflight, and
            # Registry.handle independently checks expiry for each request.
            # A blocking idle poll and mixed Hosts retain the post-poll sweep:
            # mixed component maintenance can include non-market owner work.
            single_market_hub = self._is_single_market_hub_host()
            if not (single_market_hub and (handled or timeout_ms == 0)):
                self._maintain_components()
            self._refresh_registry_readiness()
            return handled
        except ServiceHostError:
            raise
        except Exception:
            raise ServiceHostError("SERVE_FAILED") from None

    def serve_burst(
        self,
        timeout_ms: int = 0,
        *,
        max_requests: int = 16,
        max_burst_seconds: float = 0.05,
    ) -> int:
        """Serve up to ``max_requests`` ready authenticated requests on the owner thread.

        Runs the full pre-poll and post-poll readiness/maintenance cycle around a
        bounded nonblocking drain so busy multi-client hosts (e.g. Gateway with
        dozens of active sessions) do not repeat full maintenance sweeps between
        every queued RPC while keeping every per-request Registry authorization,
        epoch, and lease check intact.
        """

        self._check_owner_thread()
        self._require_no_execution_submission()
        if self._state != "started" or self._server is None:
            raise ServiceHostError("HOST_NOT_STARTED")
        if type(timeout_ms) is not int or timeout_ms < 0:
            raise ServiceHostError("INVALID_TIMEOUT")
        if type(max_requests) is not int or not 1 <= max_requests <= 256:
            raise ServiceHostError("INVALID_TIMEOUT")
        if (
            type(max_burst_seconds) not in (int, float)
            or not isfinite(max_burst_seconds)
            or not 0 < float(max_burst_seconds) <= 1.0
        ):
            raise ServiceHostError("INVALID_TIMEOUT")
        try:
            if self._upstream is not None:
                try:
                    self.maintain()
                except ServiceHostError as exc:
                    if exc.code != "UPSTREAM_MAINTENANCE_FAILED":
                        raise
            self._refresh_registry_readiness()
            try:
                delay = self.next_maintenance_delay()
            except Exception:
                self._host_delay_faulted = True
                self._refresh_registry_readiness()
                raise
            bounded_timeout = timeout_ms
            if delay is not None:
                if type(delay) not in (int, float) or not isfinite(delay) or delay < 0:
                    self._host_delay_faulted = True
                    self._refresh_registry_readiness()
                    bounded_timeout = 0
                else:
                    self._host_delay_faulted = False
                    delay_ms = delay * 1000
                    if isfinite(delay_ms):
                        bounded_timeout = min(timeout_ms, max(0, floor(delay_ms)))
            else:
                self._host_delay_faulted = False
            maintained = self._maintain_components()
            self._maintain_atomic_monitors()
            if maintained or self._trusted_atomic_monitors:
                self._refresh_registry_readiness()
            if self._trusted_atomic_monitors:
                bounded_timeout = min(bounded_timeout, 1000)
            if self._registry is not None and self._registry._parent_retry_unready:
                bounded_timeout = min(bounded_timeout, 10)
            served = 0
            if self._server.serve_once(bounded_timeout):
                served = 1
                if max_requests > 1:
                    burst_budget = float(max_burst_seconds)
                    if delay is not None and type(delay) in (int, float) and isfinite(delay) and delay >= 0:
                        burst_budget = min(burst_budget, float(delay))
                    burst_deadline = time.monotonic() + burst_budget
                    while (
                        served < max_requests
                        and self._state == "started"
                        and self._server is not None
                        and time.monotonic() < burst_deadline
                    ):
                        if not self._server.serve_once(0):
                            break
                        served += 1
            single_market_hub = self._is_single_market_hub_host()
            if not (single_market_hub and (served > 0 or timeout_ms == 0)):
                self._maintain_components()
            self._refresh_registry_readiness()
            return served
        except ServiceHostError:
            raise
        except Exception:
            raise ServiceHostError("SERVE_FAILED") from None

    def _is_single_market_hub_host(self) -> bool:
        """Return true only for the validated, market-only built-in composition."""

        if (
            self._state != "started"
            or self._registry is None
            or self._server is None
            or self._upstream is not None
            or not self._market_endpoint
            or self._market_publisher is None
            or len(self._prepared) != 1
            or len(self._components) != 1
        ):
            return False
        prepared = self._prepared[0]
        component = self._components[0]
        trusted_market_module = sys.modules.get("bt_api_gateway.market_hub")
        if (
            type(prepared) is not PreparedComponent
            or type(prepared.spec) is not ComponentSpec
            or type(component) is not ServiceComponent
            or prepared.module is not trusted_market_module
        ):
            return False
        spec = prepared.spec
        if (
            spec.component != "gateway"
            or spec.options.get("role") != "market_hub"
            or spec.bind_ports != ("service.market_source",)
            or set(prepared.operations_by_port) != {"service.market_source"}
            or prepared.write_operations_by_port.get("service.market_source") != frozenset()
            or component.component != "gateway"
            or component.instance_id != spec.instance_id
            or component.internal_ports
            or component.maintain_registrations is not None
            or component.maintain is None
            or len(component.capabilities) != 1
        ):
            return False
        capability = component.capabilities[0]
        if type(capability) is not ServiceCapability:
            return False
        return (
            capability.instance_id == spec.instance_id
            and capability.port == "service.market_source"
            and not capability.write_operations
            and capability.operations <= frozenset(
                {
                    "market.bootstrap_many",
                    "market.bootstrap_begin",
                    "market.bootstrap_status",
                    "market.bootstrap_cancel",
                    "market.subscribe",
                    "market.unsubscribe",
                    "market.snapshot",
                    "market.head",
                    "market.read_after",
                    "market.read_after_bounded",
                    "market.history",
                }
            )
        )

    def close(self) -> None:
        """Mark the registry unready and close owned resources in reverse order."""

        if self._state == "closed":
            return
        if self._owner_thread is not None:
            self._check_owner_thread()
        self._require_no_execution_submission()
        cleanup_failures = self._shutdown_resources()
        self._last_cleanup_failures = cleanup_failures
        self._cleanup_history.extend(cleanup_failures)
        self._state = "close_failed" if cleanup_failures else "closed"
        if cleanup_failures:
            # Finite step labels only; the operator otherwise saw just
            # CLOSE_FAILED (I63-E107).
            _LOG.warning("ServiceHost close incomplete: %s", ", ".join(cleanup_failures[:16]))
            raise ServiceHostError("CLOSE_FAILED")

    def _enforce_migration_gate(self) -> None:
        """Refuse a sealed cutover state before any start side effect."""

        try:
            enforce_migration_start(
                self._registry_path,
                deployment_epoch=self._migration_deployment_epoch,
            )
        except MigrationGateError as exc:
            raise ServiceHostError(exc.code) from None

    def _record_migration_write_attempt(self, *, fact_kind: str) -> bool:
        """Seal a cutover before a Host-owned internal writer is entered."""

        return record_migration_write_attempt(
            self._registry_path,
            deployment_epoch=self._migration_deployment_epoch,
            fact_kind=fact_kind,
        )

    def _validate_authority_references(self) -> None:
        authority = self._authority
        if authority.identity.actor_ref != self._config.service_id:
            raise ServiceHostError("SERVICE_IDENTITY_MISMATCH")
        if authority.scope_ref != self._config.scope_ref:
            raise ServiceHostError("SCOPE_REFERENCE_MISMATCH")
        if authority.registration_policy_ref != self._config.registration_policy_ref:
            raise ServiceHostError("POLICY_REFERENCE_MISMATCH")
        if authority.service_requirements_ref != self._config.service_requirements_ref:
            raise ServiceHostError("REQUIREMENTS_REFERENCE_MISMATCH")
        profile = self._listener_profile()
        if profile.get("security_profile_ref") != authority.security_ref:
            raise ServiceHostError("SECURITY_REFERENCE_MISMATCH")
        if "rpc_bind" not in profile.get("endpoints", {}):
            raise ServiceHostError("LISTENER_ENDPOINT_MISSING")

    def _validate_outbound_bindings(self) -> None:
        has_clients = bool(self._config.clients)
        if not has_clients:
            if self._trusted_clients is not None:
                raise ServiceHostError("UNUSED_TRUSTED_CLIENTS")
            return
        if self._service_directory is None:
            raise ServiceHostError("OUTBOUND_DIRECTORY_REQUIRED")
        # Directory validation still runs first, so a malformed deployment is
        # rejected consistently before reporting a missing code bundle.
        missing_bundle = self._trusted_clients is None
        directory = self._service_directory
        configured_self = directory.get(self._config.service_id)
        if type(configured_self) is not ServiceConfig:
            raise ServiceHostError("OUTBOUND_DEPLOYMENT_INVALID")
        try:
            if configured_self.to_public_dict() != self._config.to_public_dict():
                raise ServiceHostError("OUTBOUND_DEPLOYMENT_INVALID")
        except ServiceHostError:
            raise
        except Exception:
            raise ServiceHostError("OUTBOUND_DEPLOYMENT_INVALID") from None
        try:
            validate_deployment(directory)
        except Exception:
            raise ServiceHostError("OUTBOUND_DEPLOYMENT_INVALID") from None
        if missing_bundle:
            raise ServiceHostError("TRUSTED_CLIENTS_REQUIRED")

    def _listener_profile(self) -> Mapping[str, Any]:
        try:
            public = self._config.to_public_dict()
            profiles = public["communication"]["profiles"]
            profile = profiles[self._config.transport_ref]
        except Exception:
            raise ServiceHostError("LISTENER_PROFILE_INVALID") from None
        if not isinstance(profile, Mapping):
            raise ServiceHostError("LISTENER_PROFILE_INVALID")
        return profile

    def _configure_paper_compositions(
        self,
        owners: list[tuple[PreparedComponent, ServiceComponent, ComponentResources]],
    ) -> None:
        """Bind each configured Paper owner to its exact local Risk/Monitor objects."""

        if not owners:
            return
        try:
            from bt_api_execution.contracts import ExecutionScope
            from bt_api_execution.paper_execution import (
                PAPER_ENVIRONMENT,
                PAPER_PROVIDER,
                PaperMatchingPort,
                PaperOwnerDomainV1,
            )
        except ImportError:
            raise ServiceHostError("PAPER_EXECUTION_COMPONENT_UNAVAILABLE") from None

        prepared_by_instance = {item.spec.instance_id: item for item in self._prepared}
        for item, component, resources in owners:
            selected_mode = item.options.get("paper_composition")
            mode = "execution_only" if selected_mode is None else selected_mode
            if mode not in {
                "execution_only",
                "execution_risk",
                "execution_risk_monitor",
            }:
                raise ServiceHostError("PAPER_COMPOSITION_INVALID")
            values = resources.values
            domain = values.get("paper_owner_domain")
            if domain is None:
                if selected_mode is not None:
                    raise ServiceHostError("PAPER_OWNER_DOMAIN_REQUIRED")
                continue
            if (
                type(domain) is not PaperOwnerDomainV1
                or component.component != "execution"
                or item.spec.options.get("role") != "account_owner"
                or "service.order_entry" not in item.spec.bind_ports
            ):
                raise ServiceHostError("PAPER_OWNER_COMPONENT_INVALID")
            targets = values.get("targets")
            dispatch = values.get("dispatch")
            admission = values.get("admission")
            if (
                not isinstance(targets, Mapping)
                or len(targets) != 1
                or type(dispatch) is not PaperMatchingPort
                or dispatch.owner_domain is not domain
                or not isinstance(admission, Mapping)
            ):
                raise ServiceHostError("PAPER_OWNER_TARGET_INVALID")
            wire_scope, execution_scope = next(iter(targets.items()))
            if (
                type(wire_scope) is not WireScope
                or type(execution_scope) is not ExecutionScope
                or wire_scope.run_id is not None
                or wire_scope.manual_book_ref is not None
                or wire_scope.portfolio_ref is not None
                or wire_scope.group_id is not None
                or wire_scope.leg_id is not None
                or wire_scope.account_refs
                or wire_scope.execution_domain_ref != domain.execution_domain_ref
                or wire_scope.account_ref != domain.simulation_account_ref
                or wire_scope.strategy_ref != domain.strategy_ref
                or execution_scope.provider != PAPER_PROVIDER
                or execution_scope.environment != PAPER_ENVIRONMENT
                or execution_scope.account_ref != domain.simulation_account_ref
                or execution_scope.strategy_id != domain.strategy_ref
            ):
                raise ServiceHostError("PAPER_OWNER_TARGET_INVALID")
            self._paper_composition_modes[component.instance_id] = mode
            if mode == "execution_only":
                continue

            try:
                from bt_api_execution.paper_risk import PaperRiskAdmissionAdapter
                from bt_api_risk import (
                    AccountScope,
                    DurableRiskAdmissionAdapter,
                    DurableRiskGate,
                )
                from bt_api_py.service_risk_order_entry import RiskOrderTarget
            except ImportError:
                raise ServiceHostError("PAPER_RISK_COMPOSITION_UNAVAILABLE") from None

            paper_risk = admission.get("order_gate")
            if type(paper_risk) is not PaperRiskAdmissionAdapter:
                raise ServiceHostError("PAPER_RISK_ADMISSION_REQUIRED")
            risk_gate = paper_risk.risk_gate
            if type(risk_gate) is not DurableRiskGate:
                raise ServiceHostError("PAPER_RISK_BINDING_UNAVAILABLE")

            matching_risk_owners = [
                candidate
                for candidate in self._components
                if candidate.component == "risk"
                and candidate.risk_owner_gate is risk_gate
                and any(
                    capability.port == "service.order_entry"
                    and "orders.submit" in capability.write_operations
                    for capability in candidate.capabilities
                )
            ]
            if len(matching_risk_owners) != 1:
                raise ServiceHostError("PAPER_RISK_COMPONENT_UNAVAILABLE")
            risk_owner = matching_risk_owners[0]
            risk_prepared = prepared_by_instance.get(risk_owner.instance_id)
            if (
                risk_prepared is None
                or risk_prepared.spec.component != "risk"
                or risk_prepared.options.get("role") != "account_owner"
            ):
                raise ServiceHostError("PAPER_RISK_COMPONENT_UNAVAILABLE")
            risk_targets = risk_owner.risk_owner_targets
            risk_admissions = risk_owner.risk_owner_admission
            if (
                set(risk_targets) != {wire_scope}
                or set(risk_admissions) != {wire_scope}
            ):
                raise ServiceHostError("PAPER_RISK_SCOPE_MISMATCH")
            risk_target = risk_targets[wire_scope]
            risk_admission = risk_admissions[wire_scope]
            if (
                type(risk_target) is not RiskOrderTarget
                or type(risk_target.account_scope) is not AccountScope
                or risk_target.account_scope
                != AccountScope(PAPER_PROVIDER, domain.simulation_account_ref, PAPER_ENVIRONMENT)
                or risk_target.strategy_id != domain.strategy_ref
                or risk_target.provider_account_id != domain.simulation_account_ref
                or type(risk_admission) is not DurableRiskAdmissionAdapter
                or risk_admission.is_bound_to(risk_gate) is not True
                or paper_risk.is_bound_to_composition(
                    risk_gate,
                    domain,
                    dispatch,
                    store=values.get("store"),
                    execution_scope=execution_scope,
                    instrument_registry=risk_target.registry,
                )
                is not True
            ):
                raise ServiceHostError("PAPER_RISK_COMPOSITION_MISMATCH")
            if mode == "execution_risk_monitor":
                self._configure_paper_monitor_relay(
                    instance_id=component.instance_id,
                    store=values.get("store"),
                    domain=domain,
                    execution_scope=execution_scope,
                )

    def _configure_paper_monitor_relay(
        self,
        *,
        instance_id: str,
        store: object,
        domain: object,
        execution_scope: object,
    ) -> None:
        """Create one owner-thread relay to the sole exact Monitor append binding."""

        upstream = self._upstream
        if upstream is None or upstream.healthy is not True:
            raise ServiceHostError("PAPER_MONITOR_INGRESS_UNAVAILABLE")
        try:
            from bt_api_execution.contracts import ExecutionScope
            from bt_api_execution.journal_reader import ExecutionJournalReader
            from bt_api_execution.store import SqliteExecutionStore
            from .service_execution_monitor import (
                ServiceExecutionMonitorRelay,
                ServiceExecutionMonitorRelayError,
            )
        except ImportError:
            raise ServiceHostError("PAPER_MONITOR_RELAY_UNAVAILABLE") from None
        if (
            type(store) is not SqliteExecutionStore
            or type(execution_scope) is not ExecutionScope
            or type(getattr(domain, "simulation_account_ref", None)) is not str
        ):
            raise ServiceHostError("PAPER_MONITOR_SOURCE_INVALID")

        candidates = tuple(
            binding
            for binding in upstream.bindings
            if type(binding) is RegisteredBinding
            and binding.active is True
            and binding.mount == "upstream.telemetry"
            and type(binding.operations) is frozenset
            and "events.append" in binding.operations
        )
        if len(candidates) != 1:
            raise ServiceHostError("PAPER_MONITOR_INGRESS_UNAVAILABLE")
        binding = candidates[0]
        expected_scope = WireScope(
            execution_domain_ref=domain.execution_domain_ref,
            account_ref=domain.simulation_account_ref,
            strategy_ref=domain.strategy_ref,
        )
        if (
            binding.operations != frozenset({"events.append"})
            or binding.scope != expected_scope
            or type(binding.scope_ref) is not str
            or not binding.scope_ref
        ):
            raise ServiceHostError("PAPER_MONITOR_INGRESS_SCOPE_MISMATCH")
        store_path = getattr(store, "path", None)
        if type(store_path) is not str or not store_path:
            raise ServiceHostError("PAPER_MONITOR_SOURCE_INVALID")
        if store_path == ":memory:":
            raise ServiceHostError("PAPER_MONITOR_SOURCE_NOT_PERSISTENT")
        try:
            source_path = Path(store_path).expanduser().resolve(strict=True)
        except Exception:
            raise ServiceHostError("PAPER_MONITOR_SOURCE_UNAVAILABLE") from None
        digest = sha256(
            "\0".join(
                (
                    str(source_path),
                    domain.fingerprint,
                    execution_scope.key,
                    binding.scope_ref,
                )
            ).encode("utf-8")
        ).hexdigest()[:32]
        checkpoint_path = source_path.with_name(f".paper-monitor-{digest}.sqlite3")
        reader = None
        try:
            reader = ExecutionJournalReader(source_path, scope=execution_scope)
            relay = ServiceExecutionMonitorRelay(
                upstream,
                reader,
                scope_ref=binding.scope_ref,
                checkpoint_path=checkpoint_path,
            )
        except ServiceExecutionMonitorRelayError:
            if reader is not None:
                with suppress(Exception):
                    reader.close()
            raise ServiceHostError("PAPER_MONITOR_RELAY_START_FAILED") from None
        except Exception:
            if reader is not None:
                with suppress(Exception):
                    reader.close()
            raise ServiceHostError("PAPER_MONITOR_RELAY_START_FAILED") from None
        self._paper_monitor_relays.append((instance_id, reader, relay))

    def _poll_paper_monitor_relays(self) -> None:
        self._check_owner_thread()
        if not self._paper_monitor_relays:
            self._paper_monitor_faults.clear()
            return
        try:
            from .service_execution_monitor import ServiceExecutionMonitorRelayError
        except ImportError:
            for instance_id, _reader, _relay in self._paper_monitor_relays:
                self._paper_monitor_faults[instance_id] = "PAPER_MONITOR_RELAY_UNAVAILABLE"
            return
        for instance_id, _reader, relay in self._paper_monitor_relays:
            try:
                relay.poll(limit=32)  # type: ignore[attr-defined]
            except Exception as exc:
                code = getattr(exc, "code", None)
                if (
                    type(exc) is ServiceExecutionMonitorRelayError
                    and type(code) is str
                    and _SAFE_REF.fullmatch(code) is not None
                ):
                    self._paper_monitor_faults[instance_id] = f"PAPER_MONITOR_{code}"
                else:
                    self._paper_monitor_faults[instance_id] = "PAPER_MONITOR_RELAY_FAILED"
            else:
                self._paper_monitor_faults.pop(instance_id, None)

    def _validate_group_runtime_bundle_sharing(self) -> None:
        """Require Risk/Execution group roles for one account to share a bundle."""

        by_account: dict[tuple[str, str], object] = {}
        for item, resources in self._group_runtime_resources:
            bundle = resources.values.get("group_runtime_bundle")
            role = item.spec.options.get("role")
            scope_map_name = (
                "reservation_targets"
                if item.spec.component == "risk" and role == "group_reservations"
                else "proof_targets"
            )
            try:
                scopes = getattr(bundle, scope_map_name)
                if type(scopes) is not MappingProxyType or not scopes:
                    raise TypeError("group runtime scope map is invalid")
                for scope in scopes:
                    if type(scope) is not WireScope:
                        raise TypeError("group runtime scope is invalid")
                    if (
                        type(scope.execution_domain_ref) is not str
                        or type(scope.account_ref) is not str
                        or scope.account_refs
                    ):
                        raise TypeError("group runtime account scope is invalid")
                    account_key = (scope.execution_domain_ref, scope.account_ref)
                    existing = by_account.get(account_key)
                    if existing is not None and existing is not bundle:
                        raise ServiceHostError("GROUP_RUNTIME_BUNDLE_MISMATCH")
                    by_account[account_key] = bundle
            except ServiceHostError:
                raise
            except Exception:
                raise ServiceHostError("GROUP_RUNTIME_BUNDLE_INVALID") from None

    def _validate_shared_collateral_composition(self) -> None:
        """Bind a declared shared pool to one complete, real Risk owner."""

        declarations = tuple(
            (component, declaration)
            for component in self._components
            for declaration in component.shared_collateral_domains
        )
        has_risk_owner_targets = any(
            component.risk_owner_targets for component in self._components
        )
        if not declarations and not has_risk_owner_targets:
            return
        try:
            from bt_api_base.account_protocol import AccountResourceKey
            from bt_api_risk import (
                AccountScope,
                DurableRiskAdmissionAdapter,
                DurableRiskGate,
                InstrumentRiskAdmissionMapper,
            )
            from bt_api_py.service_risk_order_entry import RiskOrderTarget
            from decimal import Decimal
        except Exception:
            raise ServiceHostError("SHARED_COLLATERAL_COMPOSITION_UNAVAILABLE") from None

        def typed_scopes(domain: TrustedSharedCollateralDomain) -> tuple[AccountScope, ...]:
            if type(domain.member_scopes) is not tuple:
                return ()
            values = tuple(
                scope for scope in domain.member_scopes if type(scope) is AccountScope
            )
            return tuple({scope.key: scope for scope in values}.values())

        def target_pools(target: RiskOrderTarget) -> frozenset[str]:
            resources = target.resource_by_instrument
            if not isinstance(resources, Mapping):
                return frozenset()
            return frozenset(
                resource.margin_pool
                for resource in resources.values()
                if type(resource) is AccountResourceKey
            )

        if not declarations:
            scopes_by_pool: dict[str, set[AccountScope]] = {}
            target_scopes = {
                target.account_scope
                for component in self._components
                for target in component.risk_owner_targets.values()
                if type(target) is RiskOrderTarget
                and type(target.account_scope) is AccountScope
            }
            require_complete_resource_maps = len(target_scopes) > 1
            for component in self._components:
                if not component.risk_owner_targets:
                    continue
                if type(component.risk_owner_gate) is not DurableRiskGate:
                    raise ServiceHostError("SHARED_COLLATERAL_COMPOSITION_INCOMPLETE")
                for wire_scope, target in component.risk_owner_targets.items():
                    if (
                        type(target) is not RiskOrderTarget
                        or type(target.account_scope) is not AccountScope
                    ):
                        raise ServiceHostError("SHARED_COLLATERAL_COMPOSITION_INCOMPLETE")
                    adapter = component.risk_owner_admission.get(wire_scope)
                    mapper = getattr(adapter, "_map_intent", None)
                    if (
                        type(adapter) is not DurableRiskAdmissionAdapter
                        or adapter._gate is not component.risk_owner_gate
                        or type(mapper) is not InstrumentRiskAdmissionMapper
                        or mapper.scope != target.account_scope
                        or mapper.registry is not target.registry
                        or dict(mapper.resource_by_instrument)
                        != dict(target.resource_by_instrument)
                    ):
                        raise ServiceHostError("SHARED_COLLATERAL_COMPOSITION_INCOMPLETE")
                    resources = mapper.resource_by_instrument
                    if not isinstance(resources, Mapping) or any(
                        type(resource) is not AccountResourceKey
                        for resource in resources.values()
                    ):
                        raise ServiceHostError("SHARED_COLLATERAL_COMPOSITION_INCOMPLETE")
                    if (require_complete_resource_maps or resources) and (
                        set(resources) != set(target.registry.instruments)
                    ):
                        raise ServiceHostError("SHARED_COLLATERAL_COMPOSITION_INCOMPLETE")
                    for pool in target_pools(target):
                        scopes_by_pool.setdefault(pool, set()).add(target.account_scope)
            if any(len(scopes) > 1 for scopes in scopes_by_pool.values()):
                # The trusted mapper has already established that multiple
                # AccountKeys use the same pool. Without one complete domain
                # declaration, this Host cannot grant independent budgets.
                raise ServiceHostError("SHARED_COLLATERAL_COMPOSITION_INCOMPLETE")
            return

        def owners_for(
            scopes: frozenset[AccountScope], pools: frozenset[str] = frozenset()
        ) -> tuple[ServiceComponent, ...]:
            return tuple(
                component
                for component in self._components
                if type(component.risk_owner_gate) is DurableRiskGate
                and any(
                    type(target) is RiskOrderTarget
                    and (
                        target.account_scope in scopes
                        or bool(target_pools(target).intersection(pools))
                    )
                    for target in component.risk_owner_targets.values()
                )
            )

        def quarantine(
            domain: TrustedSharedCollateralDomain,
            scopes: tuple[AccountScope, ...],
            owners: tuple[ServiceComponent, ...],
        ) -> None:
            if (
                type(domain.domain_id) is not str
                or _SAFE_REF.fullmatch(domain.domain_id) is None
                or type(domain.revision) is not str
                or _SAFE_REF.fullmatch(domain.revision) is None
                or not scopes
            ):
                return
            gates: list[DurableRiskGate] = []
            if type(domain.owner_gate) is DurableRiskGate:
                gates.append(domain.owner_gate)
            gates.extend(
                component.risk_owner_gate
                for component in owners
                if type(component.risk_owner_gate) is DurableRiskGate
            )
            for component in owners:
                for adapter in component.risk_owner_admission.values():
                    candidate_gate = getattr(adapter, "_gate", None)
                    if type(candidate_gate) is DurableRiskGate:
                        gates.append(candidate_gate)
            unique_gates: list[DurableRiskGate] = []
            for gate in gates:
                if not any(existing is gate for existing in unique_gates):
                    unique_gates.append(gate)
            for gate in unique_gates:
                try:
                    gate.declare_shared_collateral_domain(
                        domain.domain_id,
                        domain.revision,
                        scopes,
                        (),
                        max_notional=None,
                        ledger_identity_ref=None,
                        manifest_complete=False,
                    )
                except Exception:
                    # Startup still fails closed.  A failed quarantine write
                    # must not be mistaken for an installed ready domain.
                    continue

        domain_id_counts: dict[str, int] = {}
        for _component, domain in declarations:
            if type(domain.domain_id) is str:
                domain_id_counts[domain.domain_id] = domain_id_counts.get(domain.domain_id, 0) + 1
        duplicate_domain_ids = {
            domain_id for domain_id, count in domain_id_counts.items() if count > 1
        }
        validated_domains: list[
            tuple[
                TrustedSharedCollateralDomain,
                tuple[AccountScope, ...],
                tuple[ServiceComponent, ...],
                ServiceComponent,
                set[tuple[AccountScope, AccountResourceKey]],
                tuple[AccountScope, ...],
            ]
        ] = []
        incomplete_domains: list[
            tuple[
                TrustedSharedCollateralDomain,
                tuple[AccountScope, ...],
                tuple[ServiceComponent, ...],
                tuple[AccountScope, ...],
            ]
        ] = []
        for declaring_component, domain in declarations:
            scopes = typed_scopes(domain)
            scope_set = frozenset(scopes)
            scope_owners = owners_for(scope_set)
            shared_pools = {
                resource.margin_pool
                for component in scope_owners
                for target in component.risk_owner_targets.values()
                if type(target) is RiskOrderTarget and target.account_scope in scope_set
                for resource in target.resource_by_instrument.values()
                if type(resource) is AccountResourceKey
            }
            if type(domain.member_targets) is tuple:
                shared_pools.update(
                    resource.margin_pool
                    for item in domain.member_targets
                    if type(item) is tuple
                    and len(item) == 2
                    and type(item[1]) is RiskOrderTarget
                    for resource in item[1].resource_by_instrument.values()
                    if type(resource) is AccountResourceKey
                )
            owners = owners_for(scope_set, frozenset(shared_pools))
            quarantine_scopes = set(scopes)
            for component in owners:
                for target in component.risk_owner_targets.values():
                    if (
                        type(target) is RiskOrderTarget
                        and target_pools(target).intersection(shared_pools)
                    ):
                        quarantine_scopes.add(target.account_scope)
            normalized_quarantine_scopes = tuple(
                sorted(quarantine_scopes, key=lambda item: item.key)
            )

            complete = (
                type(domain.domain_id) is str
                and domain.domain_id not in duplicate_domain_ids
                and _SAFE_REF.fullmatch(domain.domain_id) is not None
                and type(domain.revision) is str
                and _SAFE_REF.fullmatch(domain.revision) is not None
                and len(scopes) >= 2
                and type(domain.member_scopes) is tuple
                and len(scopes) == len(domain.member_scopes)
                and type(domain.max_notional) is Decimal
                and domain.max_notional.is_finite()
                and domain.max_notional >= 0
                and type(domain.owner_instance_id) is str
                and _SAFE_REF.fullmatch(domain.owner_instance_id) is not None
                and type(domain.owner_gate) is DurableRiskGate
                and len(owners) == 1
            )
            owner = owners[0] if len(owners) == 1 else None
            complete = bool(
                complete
                and owner is not None
                and owner is declaring_component
                and owner.instance_id == domain.owner_instance_id
                and owner.risk_owner_gate is domain.owner_gate
            )

            actual_targets: dict[WireScope, RiskOrderTarget] = {}
            if owner is not None:
                for wire_scope, target in owner.risk_owner_targets.items():
                    if type(target) is not RiskOrderTarget:
                        if complete:
                            complete = False
                        continue
                    if target.account_scope in scope_set:
                        actual_targets[wire_scope] = target
            pooled_target_scopes = {
                target.account_scope
                for component in owners
                for target in component.risk_owner_targets.values()
                if type(target) is RiskOrderTarget
                and target_pools(target).intersection(shared_pools)
            }
            if pooled_target_scopes != scope_set:
                complete = False
            declared_targets: dict[WireScope, RiskOrderTarget] = {}
            if type(domain.member_targets) is tuple:
                for item in domain.member_targets:
                    if (
                        type(item) is not tuple
                        or len(item) != 2
                        or type(item[0]) is not WireScope
                        or type(item[1]) is not RiskOrderTarget
                        or item[0] in declared_targets
                    ):
                        complete = False
                        continue
                    declared_targets[item[0]] = item[1]
            else:
                complete = False
            if set(declared_targets) != set(actual_targets) or any(
                declared_targets[wire_scope] is not target
                for wire_scope, target in actual_targets.items()
                if wire_scope in declared_targets
            ):
                complete = False
            if {target.account_scope for target in actual_targets.values()} != scope_set:
                complete = False

            resources_by_scope: dict[AccountScope, Mapping[str, AccountResourceKey]] = {}
            members: set[tuple[AccountScope, AccountResourceKey]] = set()
            if owner is not None:
                for wire_scope, target in actual_targets.items():
                    adapter = owner.risk_owner_admission.get(wire_scope)
                    mapper = getattr(adapter, "_map_intent", None)
                    if (
                        type(adapter) is not DurableRiskAdmissionAdapter
                        or adapter._gate is not domain.owner_gate
                        or type(mapper) is not InstrumentRiskAdmissionMapper
                        or mapper.scope != target.account_scope
                        or mapper.registry is not target.registry
                        or dict(mapper.resource_by_instrument)
                        != dict(target.resource_by_instrument)
                    ):
                        complete = False
                        continue
                    resource_map = target.resource_by_instrument
                    if set(resource_map) != set(target.registry.instruments) or any(
                        type(resource) is not AccountResourceKey
                        for resource in resource_map.values()
                    ):
                        complete = False
                        continue
                    previous = resources_by_scope.get(target.account_scope)
                    if previous is not None and dict(previous) != dict(resource_map):
                        complete = False
                    resources_by_scope[target.account_scope] = resource_map
                    members.update(
                        (target.account_scope, resource) for resource in resource_map.values()
                    )
            if set(resources_by_scope) != scope_set or not members:
                complete = False

            if not complete or owner is None:
                incomplete_domains.append(
                    (domain, scopes, owners, normalized_quarantine_scopes)
                )
                continue
            validated_domains.append(
                (domain, scopes, owners, owner, members, normalized_quarantine_scopes)
            )

        if incomplete_domains:
            for domain, scopes, owners, quarantine_scopes in incomplete_domains:
                quarantine(domain, quarantine_scopes, owners)
            raise ServiceHostError("SHARED_COLLATERAL_COMPOSITION_INCOMPLETE")

        member_domains: dict[tuple[str, str, str], str] = {}
        overlapping_domain_ids: set[str] = set()
        for domain, _scopes, _owners, _owner, members, _quarantine_scopes in validated_domains:
            for scope, resource in members:
                key = (scope.key, resource.product_type, resource.margin_pool)
                previous_domain_id = member_domains.get(key)
                if previous_domain_id is not None and previous_domain_id != domain.domain_id:
                    overlapping_domain_ids.update((previous_domain_id, domain.domain_id))
                else:
                    member_domains[key] = domain.domain_id
        if overlapping_domain_ids:
            still_validated = []
            for item in validated_domains:
                domain, scopes, owners, _owner, _members, quarantine_scopes = item
                if domain.domain_id in overlapping_domain_ids:
                    incomplete_domains.append((domain, scopes, owners, quarantine_scopes))
                else:
                    still_validated.append(item)
            validated_domains = still_validated
            for domain, scopes, owners, quarantine_scopes in incomplete_domains:
                quarantine(domain, quarantine_scopes, owners)
            raise ServiceHostError("SHARED_COLLATERAL_COMPOSITION_INCOMPLETE")

        for domain, scopes, _owners, owner, members, _quarantine_scopes in validated_domains:
            try:
                ledger_identity_ref = owner.risk_owner_gate.ledger_identity_ref
                ready = owner.risk_owner_gate.declare_shared_collateral_domain(
                    domain.domain_id,
                    domain.revision,
                    scopes,
                    tuple(
                        sorted(
                            members,
                            key=lambda item: (
                                item[0].key,
                                item[1].product_type,
                                item[1].margin_pool,
                            ),
                        )
                    ),
                    max_notional=domain.max_notional,
                    ledger_identity_ref=ledger_identity_ref,
                    manifest_complete=True,
                )
            except Exception:
                raise ServiceHostError("SHARED_COLLATERAL_DOMAIN_INSTALL_FAILED") from None
            if ready is not True:
                raise ServiceHostError("SHARED_COLLATERAL_DOMAIN_NOT_READY")

    def _paper_owner_write_policies(
        self,
        item: PreparedComponent,
        resources: ComponentResources,
    ) -> tuple[TrustedPaperOwnerWritePolicy, ...]:
        """Bind the one application submit grant to a constructed paper owner."""

        values = resources.values
        if "paper_owner_domain" not in values:
            return ()
        if (
            item.spec.component != "execution"
            or item.spec.options.get("role") != "account_owner"
            or "service.order_entry" not in item.spec.bind_ports
            or values.get("paper_owner_domain") is None
        ):
            raise ServiceHostError("PAPER_OWNER_COMPONENT_INVALID")
        targets = values.get("targets")
        if not isinstance(targets, Mapping) or len(targets) != 1:
            raise ServiceHostError("PAPER_OWNER_TARGET_INVALID")
        wire_scope, execution_scope = next(iter(targets.items()))
        if (
            type(wire_scope) is not WireScope
            or wire_scope.run_id is not None
            or wire_scope.manual_book_ref is not None
            or wire_scope.portfolio_ref is not None
            or wire_scope.group_id is not None
            or wire_scope.leg_id is not None
        ):
            raise ServiceHostError("PAPER_OWNER_TARGET_INVALID")

        candidates: list[tuple[str, WireIdentity, Any]] = []
        for principal_ref, principal_grants in self._authority.grants.items():
            for principal_grant in principal_grants:
                identity = principal_grant.identity
                if (
                    identity.role is not WireRole.APPLICATION_CLIENT
                    or identity.principal_ref != principal_ref
                ):
                    continue
                candidates.extend(
                    (principal_ref, identity, grant)
                    for grant in principal_grant.resources
                    if grant.instance_id == item.spec.instance_id
                    and grant.port == "service.order_entry"
                    and grant.scope_ref
                    and grant.scope == wire_scope
                    and grant.operations == frozenset({"orders.submit"})
                )
        if len(candidates) != 1:
            raise ServiceHostError("PAPER_OWNER_SUBMIT_GRANT_INVALID")
        principal_ref, identity, grant = candidates[0]
        try:
            policy = TrustedPaperOwnerWritePolicy(
                instance_id=item.spec.instance_id,
                principal_ref=principal_ref,
                identity=identity,
                scope_ref=grant.scope_ref,
                scope=wire_scope,
                paper_owner_domain=values["paper_owner_domain"],
                execution_scope=execution_scope,
            )
        except Exception:
            raise ServiceHostError("PAPER_OWNER_POLICY_INVALID") from None
        return (policy,)

    def _paper_legacy_import_policies(
        self,
        item: PreparedComponent,
        resources: ComponentResources,
    ) -> tuple[TrustedPaperLegacyImportPolicy, ...]:
        """Bind a Host-owned import adapter to one exact application grant."""

        values = resources.values
        if "legacy_import_domain" not in values:
            return ()
        port = "service.paper_legacy_import"
        if (
            item.spec.component != "execution"
            or item.spec.options.get("role") != "legacy_import_owner"
            or item.spec.bind_ports != (port,)
            or set(values) != {"store", "targets", "legacy_import_domain"}
        ):
            raise ServiceHostError("LEGACY_IMPORT_COMPONENT_INVALID")
        targets = values.get("targets")
        if not isinstance(targets, Mapping) or len(targets) != 1:
            raise ServiceHostError("LEGACY_IMPORT_TARGET_INVALID")
        wire_scope, execution_scope = next(iter(targets.items()))
        if type(wire_scope) is not WireScope:
            raise ServiceHostError("LEGACY_IMPORT_TARGET_INVALID")

        operations = frozenset(
            {
                "paper.import.status",
                "paper.import.inspect_source",
                "paper.import.prepare",
                "paper.import.activate",
                "paper.import.abort",
            }
        )
        candidates: list[tuple[str, WireIdentity, Any]] = []
        for principal_ref, principal_grants in self._authority.grants.items():
            for principal_grant in principal_grants:
                identity = principal_grant.identity
                if (
                    identity.role is not WireRole.APPLICATION_CLIENT
                    or identity.principal_ref != principal_ref
                ):
                    continue
                candidates.extend(
                    (principal_ref, identity, grant)
                    for grant in principal_grant.resources
                    if grant.instance_id == item.spec.instance_id
                    and grant.port == port
                    and grant.scope_ref
                    and grant.scope == wire_scope
                    and grant.operations == operations
                )
        if len(candidates) != 1:
            raise ServiceHostError("LEGACY_IMPORT_GRANT_INVALID")
        principal_ref, identity, grant = candidates[0]
        try:
            policy = TrustedPaperLegacyImportPolicy(
                instance_id=item.spec.instance_id,
                principal_ref=principal_ref,
                identity=identity,
                scope_ref=grant.scope_ref,
                scope=wire_scope,
                paper_owner_domain=values["legacy_import_domain"],
                execution_scope=execution_scope,
            )
        except Exception:
            raise ServiceHostError("LEGACY_IMPORT_POLICY_INVALID") from None
        return (policy,)

    def _track_resources(self, item: PreparedComponent, resources: ComponentResources) -> None:
        """Retain resources before binding a validated market publisher."""

        self._pending_resources.append((item.spec.component, item.spec.instance_id, resources))
        if (
            item.spec.component == "risk" and item.spec.options.get("role") == "group_reservations"
        ) or (
            item.spec.component == "execution"
            and item.spec.options.get("role")
            == "group_account_owner"
        ):
            self._group_runtime_resources.append((item, resources))
        if "service.market_source" not in item.operations_by_port:
            return
        if item.spec.options.get("role") == "trading_owner":
            expected_keys = {"binding", "hub"}
            if item.spec.component == "gateway":
                # Resource tracking happens before build_component validates the
                # concrete adapters.  Match its exact optional-key inventory so
                # independently enabled capabilities can coexist with market.
                optional_keys = _ROLE_BUILTINS[("gateway", "trading_owner")].optional_resource_keys
                if "service.account_read" not in item.operations_by_port:
                    optional_keys = optional_keys - {
                        "account_comparison", "account_strategy_reads", "account_observer", "account_facts", "account_cancel_attempts"
                    }
                expected_keys.update(optional_keys.intersection(resources.values))
            if "service.account_read" in item.operations_by_port:
                expected_keys.add("read_service")
                if "instrument_service" in resources.values:
                    from bt_api_py.gateway_instrument_service import GatewayInstrumentService

                    service = resources.values["instrument_service"]
                    if (type(service) is not GatewayInstrumentService
                            or not service.matches(resources.values.get("binding"),
                                                   resources.values.get("read_service"))):
                        raise ServiceHostError("MARKET_RESOURCE_KEYS_INVALID")
                    expected_keys.add("instrument_service")
            if "service.instrument_product_read" in item.operations_by_port:
                from bt_api_py.gateway_instrument_product_service import GatewayInstrumentProductService

                service = resources.values.get("instrument_product_service")
                if (type(service) is not GatewayInstrumentProductService
                        or not service.matches(resources.values.get("binding"))):
                    raise ServiceHostError("MARKET_RESOURCE_KEYS_INVALID")
                expected_keys.add("instrument_product_service")
            from bt_api_gateway.market_hub import GatewayMarketHub

            expected_hub_type = GatewayMarketHub
        else:
            expected_keys = {"hub"}
            expected_hub_type = getattr(item.module, "GatewayMarketHub", None)
        if set(resources.values) != expected_keys:
            raise ServiceHostError("MARKET_RESOURCE_KEYS_INVALID")
        hub = resources.values["hub"]
        if not isinstance(expected_hub_type, type) or type(hub) is not expected_hub_type:
            raise ServiceHostError("MARKET_HUB_CONFIGURATION_INVALID")
        publisher_principals = self._authority.principals
        if item.spec.options.get("role") == "trading_owner":
            from bt_api_py.gateway_direct_service import GatewayDirectServiceBinding

            binding = resources.values["binding"]
            if type(binding) is not GatewayDirectServiceBinding:
                raise ServiceHostError("MARKET_HUB_CONFIGURATION_INVALID")
            publisher_principals = self._trading_market_principals(
                item.spec.instance_id, binding.account_scope
            )
        self._validate_market_hub_audience(hub, frozenset(publisher_principals.values()))

        module = self._market_transport_module
        if module is None or self._market_startup_cleanup_error_type is None:
            raise ServiceHostError("MARKET_TRANSPORT_UNAVAILABLE")
        profile = self._listener_profile()
        endpoints = profile.get("endpoints")
        options = profile.get("options")
        endpoint = endpoints.get("market_bind") if isinstance(endpoints, Mapping) else None
        if type(endpoint) is not str:
            raise ServiceHostError("MARKET_ENDPOINT_MISSING")
        if not isinstance(options, Mapping):
            raise ServiceHostError("MARKET_PROFILE_INVALID")
        profile_market_wire_version = options.get("market_wire_version", 1)
        if (
            type(profile_market_wire_version) is not int
            or profile_market_wire_version not in (1, 2)
            or profile_market_wire_version != self._planned_market_wire_version
        ):
            raise ServiceHostError("MARKET_WIRE_VERSION_MISMATCH")
        publisher_options = {
            "high_water_mark": options.get("high_water_mark", 1000),
            "max_message_bytes": options.get("max_message_bytes", 1_048_576),
        }
        if self._planned_market_wire_version == 2:
            publisher_options["wire_version"] = 2
        partitioned = hub.requires_partitioned_audience
        stream_grants = None
        if partitioned:
            # Only the trusted hub's validated ACL determines these grants;
            # client registrations and topic requests cannot grant access.
            stream_grants = {
                principal: frozenset(key.stream_id for key in allowed)
                for principal, allowed in hub.principal_allowed_keys.items()
            }
            publisher_options["stream_grants"] = stream_grants
        try:
            from bt_api_transport_zmq.service_v2 import CurveServerCredentials

            credentials = CurveServerCredentials(
                public_key=self._authority.curve_public_key,
                secret_key=self._authority.curve_secret_key,
            )
            publisher_options["credentials"] = credentials
            publisher = module.ZmqMarketPublisher(
                endpoint,
                self._authority.identity,
                self._authority.scope,
                publisher_principals,
                **publisher_options,
            )
        except BaseException as exc:
            cleanup_error = self._market_startup_cleanup_error_type
            if isinstance(exc, cleanup_error):
                self._market_publisher = exc.transport
                raise ServiceHostError("MARKET_PUBLISHER_STARTUP_CLEANUP_FAILED") from None
            if not isinstance(exc, Exception):
                raise
            raise ServiceHostError("MARKET_PUBLISHER_START_FAILED") from None

        # Retain before calling into the trusted hub: if the callback setter
        # fails, startup cleanup still owns the live publisher.
        self._market_publisher = publisher
        self._market_endpoint = publisher.endpoint
        try:
            if partitioned:
                if publisher.enforced_stream_grants != stream_grants:
                    raise ServiceHostError("MARKET_PUBLISHER_GRANTS_INVALID")
                regrant = getattr(publisher, "regrant", None)
                if callable(regrant):
                    # Runtime revocation (iteration 60, G2): the hub rebuilds
                    # the enforcing XPUB through this callback.
                    hub.set_partitioned_publisher(
                        publisher.publish,
                        principal_stream_grants=stream_grants,
                        regrant=regrant,
                    )
                else:
                    hub.set_partitioned_publisher(
                        publisher.publish, principal_stream_grants=stream_grants
                    )
            else:
                hub.set_publisher(publisher.publish)
        except Exception:
            raise ServiceHostError("MARKET_HUB_BINDING_FAILED") from None

    def _trading_market_principals(
        self, instance_id: str, account_scope: str
    ) -> dict[bytes, str]:
        """Select CURVE keys with a matching static market subscription grant."""

        authority = self._authority
        host_accounts = (
            frozenset((authority.scope.account_ref,))
            if authority.scope.account_ref is not None
            else frozenset(authority.scope.account_refs)
        )
        if host_accounts and host_accounts != frozenset({account_scope}):
            return {}

        def permits(principal: str) -> bool:
            for entry in authority.grants[principal]:
                if entry.identity.principal_ref != principal:
                    continue
                for grant in entry.resources:
                    grant_accounts = (
                        frozenset((grant.scope.account_ref,))
                        if grant.scope.account_ref is not None
                        else frozenset(grant.scope.account_refs)
                    )
                    if (
                        grant.instance_id == instance_id
                        and grant.port == "service.market_source"
                        and grant.scope_ref == authority.scope_ref
                        and grant.scope.execution_domain_ref == authority.scope.execution_domain_ref
                        and "market.subscribe" in grant.operations
                        and grant_accounts == frozenset({account_scope})
                    ):
                        return True
            return False

        return {key: principal for key, principal in authority.principals.items() if permits(principal)}

    def _validate_market_hub_audience(
        self, hub: object, expected_principals: frozenset[str]
    ) -> None:
        """Check the trusted hub's complete authority before opening sockets."""

        try:
            identity = hub.identity
            service_scope = hub.service_scope
            key_universe = hub.key_universe
            principal_allowed_keys = hub.principal_allowed_keys
            authorized_principals = hub.authorized_principals
            partitioned = hub.requires_partitioned_audience
        except Exception:
            raise ServiceHostError("MARKET_HUB_CONFIGURATION_INVALID") from None
        if (
            type(identity) is not WireIdentity
            or type(service_scope) is not WireScope
            or identity != self._authority.identity
            or service_scope != self._authority.scope
        ):
            raise ServiceHostError("MARKET_HUB_AUTHORITY_MISMATCH")
        if type(key_universe) is not frozenset or not key_universe:
            raise ServiceHostError("MARKET_HUB_AUDIENCE_INVALID")
        if not isinstance(principal_allowed_keys, Mapping):
            raise ServiceHostError("MARKET_HUB_AUDIENCE_INVALID")
        if type(partitioned) is not bool:
            raise ServiceHostError("MARKET_HUB_AUDIENCE_INVALID")
        if not expected_principals or frozenset(principal_allowed_keys) != expected_principals:
            raise ServiceHostError("MARKET_HUB_AUDIENCE_INVALID")
        if (
            type(authorized_principals) is not frozenset
            or authorized_principals != expected_principals
        ):
            raise ServiceHostError("MARKET_HUB_AUDIENCE_INVALID")
        is_partial = False
        for principal_ref in expected_principals:
            allowed = principal_allowed_keys.get(principal_ref)
            if type(allowed) is not frozenset or not allowed or not allowed <= key_universe:
                raise ServiceHostError("MARKET_HUB_AUDIENCE_INVALID")
            is_partial = is_partial or allowed != key_universe
        if partitioned != is_partial:
            raise ServiceHostError("MARKET_HUB_AUDIENCE_INVALID")
        if partitioned and self._planned_market_wire_version != 2:
            raise ServiceHostError("MARKET_HUB_AUDIENCE_INVALID")
        validate_audience = getattr(hub, "validate_audience", None)
        if not callable(validate_audience):
            raise ServiceHostError("MARKET_HUB_CONFIGURATION_INVALID")
        try:
            if partitioned:
                validate_audience(
                    expected_principals, self._authority.identity, self._authority.scope,
                    partitioned=True,
                )
            else:
                validate_audience(
                    expected_principals, self._authority.identity, self._authority.scope
                )
        except Exception:
            raise ServiceHostError("MARKET_HUB_AUDIENCE_INVALID") from None
        if not callable(getattr(hub, "set_publisher", None)):
            raise ServiceHostError("MARKET_HUB_CONFIGURATION_INVALID")
        if partitioned and not callable(getattr(hub, "set_partitioned_publisher", None)):
            raise ServiceHostError("MARKET_HUB_CONFIGURATION_INVALID")
        try:
            readiness = hub.ready  # type: ignore[attr-defined]
        except Exception:
            raise ServiceHostError("MARKET_HUB_CONFIGURATION_INVALID") from None
        if type(readiness) is not bool:
            raise ServiceHostError("MARKET_HUB_CONFIGURATION_INVALID")

    def _all_components_ready(self) -> bool:
        return self._components_readiness_state() == 'valid'

    def _record_readiness(self, state: str, reason: str | None = None) -> str:
        # Only fixed internal codes are recorded; never retain callback data.
        with self._readiness_diagnostic_lock:
            last_failure = self._readiness_diagnostic[2]
            if state == 'failed':
                last_failure = reason
            self._readiness_diagnostic = (state, reason, last_failure)
        return state

    def _readiness_fault_reason(self) -> str | None:
        if self._component_maintenance_faults:
            return 'component_maintenance'
        if self._component_delay_faults:
            return 'component_delay'
        if self._gateway_lease_faults:
            return 'gateway_lease'
        if getattr(self, '_host_delay_faulted', False):
            return 'host_delay'
        if self._paper_monitor_faults:
            return 'paper_monitor'
        return None

    def _atomic_intake_fence_state(self) -> str:
        """Observe every writer fence; only explicit contention is retryable."""
        retry = False
        for service in self._atomic_intake_services:
            status = getattr(service, 'local_intake_status', None)
            if not callable(status):
                return 'failed'
            state = status()
            if type(state) is not str or state not in ('valid', 'retry', 'failed'):
                return 'failed'
            if state == 'failed':
                return 'failed'
            retry = retry or state == 'retry'
        return 'retry' if retry else 'valid'

    def _components_readiness_state(self) -> str:
        # A local writer fence is independent of the component's lease.
        # Hard failures must win even when another component asks to retry.
        try:
            fence_state = self._atomic_intake_fence_state()
            if fence_state == 'failed':
                return self._record_readiness('failed', 'intake_fence')
        except Exception:
            return self._record_readiness('failed', 'intake_fence_exception')
        if (self._component_maintenance_faults or self._component_delay_faults or self._gateway_lease_faults
                or getattr(self, '_host_delay_faulted', False)):
            return self._record_readiness('failed', self._readiness_fault_reason() or 'concurrent_fault')
        if self._paper_monitor_faults:
            return self._record_readiness('failed', 'paper_monitor')
        if self._upstream is not None:
            try:
                if self._upstream_faulted or self._upstream.healthy is not True:
                    return self._record_readiness('failed', 'upstream_unhealthy')
            except Exception:
                return self._record_readiness('failed', 'upstream_exception')
        if self._execution_parent_active and not self._execution_parent_gateway_ready(
            tuple(self._execution_parent_active.values())
        ):
            return self._record_readiness('failed', 'execution_parent_gateway')
        retry = fence_state == 'retry'
        for component in self._components:
            try:
                status = component.readiness()
                if status is _ATOMIC_READINESS_RETRY and any(
                        component.component == 'execution' and component.instance_id == service.instance_id
                        for service in self._atomic_intake_services):
                    retry = True
                elif status is not True:
                    return self._record_readiness(
                        'failed', 'component_false' if status is False else 'component_invalid'
                    )
            except Exception:
                return self._record_readiness('failed', 'component_exception')
        if (self._component_maintenance_faults or self._component_delay_faults
                or getattr(self, '_host_delay_faulted', False)
                or self._gateway_lease_faults or self._paper_monitor_faults):
            return self._record_readiness('failed', self._readiness_fault_reason() or 'concurrent_fault')
        return self._record_readiness('retry', 'intake_fence_retry' if fence_state == 'retry'
                                      else 'component_retry') if retry else self._record_readiness('valid')

    def _restricted_operation_ready(
        self,
        target: ServiceComponent,
        operation_readiness: Callable[[], bool],
        *,
        operation: str | None = None,
    ) -> bool:
        """Evaluate an explicitly restricted write readiness callback.

        The resource callback is evaluated for every call, including while the
        Registry is globally ready. Owner cancellation may also run while all
        Host components are healthy; during degradation, either restricted
        route is permitted only when its exact component is the sole component
        reporting unready and all Host-owned health checks remain clear.
        """

        if operation is None:
            # Preserve the original private helper call shape used by the
            # recovery-close readiness tests; Host wiring always supplies the
            # exact operation key.
            operation = _RECOVERY_CLOSE_OPERATION
        if operation not in {_RECOVERY_CLOSE_OPERATION, _OWNER_CANCEL_OPERATION}:
            return False
        if not callable(operation_readiness) or not any(
            component is target for component in self._components
        ):
            return False
        registry = self._registry
        if self._state != "started" or registry is None:
            return False
        if (
            self._component_maintenance_faults
            or self._component_delay_faults
            or getattr(self, '_host_delay_faulted', False)
            or self._paper_monitor_faults
        ):
            return False
        if self._upstream is not None:
            try:
                if self._upstream_faulted or self._upstream.healthy is not True:
                    return False
            except Exception:
                return False
        if registry.ready is True:
            if operation != _OWNER_CANCEL_OPERATION or not self._all_components_ready():
                return False
            try:
                return operation_readiness() is True
            except Exception:
                return False
        if registry.ready is not False:
            return False
        try:
            for component in self._components:
                readiness = component.readiness()
                if component is target:
                    if readiness is not False:
                        return False
                elif readiness is not True:
                    return False
            return operation_readiness() is True
        except Exception:
            return False

    @property
    def upstream(self) -> ServiceClientRuntime | None:
        """Read-only access to the selected upstream runtime for trusted wiring."""

        return self._upstream

    @property
    def healthy(self) -> bool:
        """Whether the local registry can currently serve business requests."""

        return self._state == "started" and self._all_components_ready()

    def migration_consumer_inventory(self) -> tuple[object, ...]:
        """Return the parsed-config and trusted-authority consumer snapshot.

        The snapshot is available only from a started Host so configured
        outbound bindings use the same trusted scope/peer resolvers and
        requested-operation policy that the production runtime uses. It is a
        preflight input for MIG03, not evidence that the consumers have stopped.
        """

        self._check_owner_thread()
        if self._state != "started":
            raise ServiceHostError("HOST_NOT_STARTED")
        bundle = self._trusted_clients
        if self._config.clients and bundle is None:
            raise ServiceHostError("TRUSTED_CLIENTS_REQUIRED")
        try:
            from .service_migration_boundary import derive_service_consumer_inventory

            return derive_service_consumer_inventory(
                self._config,
                self._authority.grants,
                authority=self._authority,
                requested_operations=(
                    None if bundle is None else bundle.requested_operations
                ),
                scope_resolver=None if bundle is None else bundle.scope_resolver,
                peer_resolver=None if bundle is None else bundle.peer_resolver,
            )
        except Exception as exc:
            if isinstance(exc, ServiceHostError):
                raise
            raise ServiceHostError("MIGRATION_CONSUMER_INVENTORY_UNAVAILABLE") from None

    def enable_migration_writes(
        self,
        *,
        migration_id: str,
        readiness_sha256: str,
    ) -> None:
        """Explicitly enable a ready destination after operator readback.

        The caller must first verify destination-side reads/projections and
        policy against the source inventory. The Registry then checks the
        durable cutover identity and its own readiness before releasing the
        local writer latch.
        """

        self._check_owner_thread()
        registry = self._registry
        if self._state != "started" or registry is None:
            raise ServiceHostError("HOST_NOT_STARTED")
        if not self.healthy or not registry.ready:
            raise ServiceHostError("COMPONENT_NOT_READY")
        try:
            registry.enable_migration_writes(
                migration_id=migration_id,
                deployment_epoch=self._migration_deployment_epoch,
                readiness_sha256=readiness_sha256,
            )
        except MigrationGateError as exc:
            raise ServiceHostError(exc.code) from None

    @staticmethod
    def _execution_parent_key(grant: object) -> tuple[object, ...]:
        from .execution_parent_grant import ExecutionParentGrant
        if type(grant) is not ExecutionParentGrant:
            raise ServiceHostError("EXECUTION_PARENT_GRANT_INVALID")
        return (grant.instance_id, grant.scope_ref, grant.identity, grant.account_ref,
                grant.strategy_ref, grant.execution_domain_ref)

    def _execution_parent_gateway_binding(self, grant: object, *, scope: WireScope | None = None) -> RegisteredBinding | None:
        """Select an exact live registration with the SDK's authenticated peer proof."""
        upstream = self._upstream
        summary = self._upstream_registration_summary
        try:
            if upstream is None or upstream.healthy is not True or summary is None:
                return None
            for binding in summary.bindings:
                if (binding.mount != "upstream.direct_write" or not binding.required or not binding.active
                        or binding.scope.account_ref != grant.account_ref
                        or binding.scope.strategy_ref != grant.strategy_ref
                        or binding.scope.execution_domain_ref != grant.execution_domain_ref
                        or (scope is not None and binding.scope.run_id != scope.run_id)
                        or not {"direct.parent.register", "direct.parent.query"} <= binding.operations):
                    continue
                proof = upstream.authenticated_peer_for_binding(binding)
                if (type(proof) is tuple and len(proof) == 2 and type(proof[0]) is WireIdentity
                        and proof[0].role is WireRole.SERVICE and proof[0].delegate_ref is None
                        and proof[0].actor_ref == binding.service_id and type(proof[1]) is str
                        and len(proof[1]) == 64 and all(c in '0123456789abcdef' for c in proof[1])):
                    return binding
            return None
        except Exception:
            return None

    def _execution_parent_gateway_ready(self, grants: tuple[object, ...], *, scope: WireScope | None = None) -> bool:
        return all(self._execution_parent_gateway_binding(grant, scope=scope) is not None for grant in grants)

    def _check_execution_parent_operator_grant(self, grant: object, *, require_gateway: bool) -> None:
        if self._state != "started" or self._registry is None:
            raise ServiceHostError("HOST_NOT_STARTED")
        self._check_owner_thread()
        self._require_no_execution_submission()
        if self._execution_parent_key(grant) not in self._execution_parent_keys:
            raise ServiceHostError("EXECUTION_PARENT_GRANT_KEY_INVALID")
        if (require_gateway and self._execution_parent_key(grant) not in self._atomic_parent_keys
                and (self._upstream_faulted or not self._execution_parent_gateway_ready((grant,)))):
            raise ServiceHostError("EXECUTION_GATEWAY_UNAVAILABLE")

    def replace_execution_parent_grant(self, expected: object, replacement: object) -> bool:
        """Atomically replace a preconfigured grant on this Host's owner thread."""
        self._check_execution_parent_operator_grant(expected, require_gateway=False)
        self._check_execution_parent_operator_grant(replacement, require_gateway=True)
        if self._execution_parent_key(expected) != self._execution_parent_key(replacement):
            raise ServiceHostError("EXECUTION_PARENT_GRANT_KEY_INVALID")
        replaced = self._registry.replace_execution_parent_grant(expected, replacement)
        if replaced and self._execution_parent_key(replacement) not in self._atomic_parent_keys:
            self._execution_parent_active[self._execution_parent_key(replacement)] = replacement
        return replaced

    def revoke_execution_parent_grant(self, grant: object) -> bool:
        """Revoke a preconfigured grant without relying on Gateway availability."""
        self._check_execution_parent_operator_grant(grant, require_gateway=False)
        revoked = self._registry.revoke_execution_parent(grant)
        if revoked and self._execution_parent_key(grant) not in self._atomic_parent_keys:
            del self._execution_parent_active[self._execution_parent_key(grant)]
        return revoked

    def install_execution_parent_grant(self, grant: object) -> None:
        """Reinstall a revoked grant for an existing key from trusted local code."""
        self._check_execution_parent_operator_grant(grant, require_gateway=True)
        try:
            self._registry.configure_execution_parents(grants=(grant,))
        except ValueError:
            raise ServiceHostError("EXECUTION_PARENT_GRANT_ALREADY_INSTALLED") from None
        if self._execution_parent_key(grant) not in self._atomic_parent_keys:
            self._execution_parent_active[self._execution_parent_key(grant)] = grant

    def revoke_atomic_intake_action(self, grant: object) -> bool:
        """Revoke an exact action grant installed by this Host's resource provider."""
        if self._state != "started" or self._registry is None:
            raise ServiceHostError("HOST_NOT_STARTED")
        self._check_owner_thread()
        self._require_no_execution_submission()
        if not any(item is grant for item in self._atomic_action_grants):
            raise ServiceHostError("ATOMIC_INTAKE_ACTION_INVALID")
        return self._registry.revoke_execution_action(grant)

    def maintain(self) -> RegistrationSummary:
        """Synchronously maintain due upstream leases on the owner thread."""

        self._check_owner_thread()
        self._require_no_execution_submission()
        if self._state != "started":
            raise ServiceHostError("HOST_NOT_STARTED")
        self._maintain_atomic_monitors()
        upstream = self._upstream
        if upstream is None:
            return RegistrationSummary((), 0, ())
        try:
            summary = upstream.maintain()
            self._upstream_registration_summary = summary
            self._upstream_faulted = upstream.healthy is not True
        except Exception:
            self._upstream_faulted = True
            with suppress(ServiceHostError):
                self._refresh_registry_readiness()
            raise ServiceHostError("UPSTREAM_MAINTENANCE_FAILED") from None
        self._refresh_registry_readiness()
        return summary

    def next_maintenance_delay(self) -> float | None:
        """Return the earliest due time across upstream and owned resources."""

        self._check_owner_thread()
        self._require_no_execution_submission()
        if self._state != "started":
            raise ServiceHostError("HOST_NOT_STARTED")

        delays: list[int | float] = []
        if self._trusted_atomic_monitors:
            delays.append(max(0.0, self._atomic_monitor_next_attempt - time.monotonic()))
        if self._gateway_lease_drains or self._gateway_cancel_only_runs:
            delays.append(0.25)
        readiness_changed = False
        upstream = self._upstream
        if upstream is not None:
            try:
                delay = upstream.next_maintenance_delay()
                if delay is not None:
                    if type(delay) not in (int, float) or not isfinite(delay) or delay < 0:
                        raise TypeError("invalid upstream maintenance delay")
                    delays.append(delay)
            except Exception:
                if not self._upstream_faulted:
                    readiness_changed = True
                self._upstream_faulted = True
                delays.append(0.0)

        for component in self._components:
            callback = component.next_maintenance_delay
            if callback is None:
                continue
            was_faulted = component.instance_id in self._component_delay_faults
            try:
                delay = callback()
                if delay is not None and (
                    type(delay) not in (int, float) or not isfinite(delay) or delay < 0
                ):
                    raise TypeError("invalid component maintenance delay")
            except Exception:
                self._component_delay_faults.add(component.instance_id)
                if not was_faulted:
                    readiness_changed = True
                delays.append(0.0)
            else:
                self._component_delay_faults.discard(component.instance_id)
                if was_faulted:
                    readiness_changed = True
                if delay is not None:
                    delays.append(delay)

        if readiness_changed:
            with suppress(ServiceHostError):
                self._refresh_registry_readiness()
        return min(delays) if delays else None

    def recover_upstreams(self) -> RegistrationSummary:
        """Explicitly replace an upstream registration session, without replay."""

        self._check_owner_thread()
        self._require_no_execution_submission()
        if self._state != "started":
            raise ServiceHostError("HOST_NOT_STARTED")
        upstream = self._upstream
        if upstream is None:
            raise ServiceHostError("NO_UPSTREAM_CLIENTS")
        try:
            summary = upstream.recover()
            self._upstream_registration_summary = summary
            self._upstream_faulted = upstream.healthy is not True
            if self._execution_parent_active and not self._execution_parent_gateway_ready(
                tuple(self._execution_parent_active.values())
            ):
                raise ServiceHostError("EXECUTION_GATEWAY_UNAVAILABLE")
        except Exception:
            self._upstream_faulted = True
            with suppress(ServiceHostError):
                self._refresh_registry_readiness()
            raise ServiceHostError("UPSTREAM_RECOVERY_FAILED") from None
        relay_rebind_failed = False
        for instance_id, _reader, relay in self._paper_monitor_relays:
            try:
                relay.rebind()  # type: ignore[attr-defined]
            except Exception:
                self._paper_monitor_faults[instance_id] = "PAPER_MONITOR_REBIND_FAILED"
                relay_rebind_failed = True
            else:
                self._paper_monitor_faults.pop(instance_id, None)
        if relay_rebind_failed:
            with suppress(ServiceHostError):
                self._refresh_registry_readiness()
            raise ServiceHostError("PAPER_MONITOR_REBIND_FAILED")
        self._refresh_registry_readiness()
        return summary

    def _upstream_summary(self) -> Mapping[str, Any] | None:
        upstream = self._upstream
        if upstream is None:
            return None
        try:
            healthy = upstream.healthy is True and not self._upstream_faulted
            state = upstream.state
            registration = self._upstream_registration_summary
            degraded_count = 0 if registration is None else registration.degraded_count
            degradation_codes = () if registration is None else registration.degradation_codes
            cleanup_failures = self._runtime_counter(upstream, "cleanup_failures")
            unregister_uncertainties = self._runtime_counter(upstream, "unregister_uncertainties")
            unknown_registration_count = self._runtime_counter(
                upstream, "unknown_registration_count"
            )
        except Exception:
            healthy = False
            state = "unavailable"
            degraded_count = 0
            degradation_codes = ()
            cleanup_failures = 0
            unregister_uncertainties = 0
            unknown_registration_count = 0
        return MappingProxyType(
            {
                "state": state,
                "healthy": healthy,
                "degraded_count": max(0, degraded_count),
                "degradation_codes": tuple(degradation_codes),
                "cleanup_failures": cleanup_failures,
                "unregister_uncertainties": unregister_uncertainties,
                "unknown_registration_count": unknown_registration_count,
            }
        )

    @staticmethod
    def _runtime_counter(runtime: ServiceClientRuntime, name: str) -> int:
        try:
            value = getattr(runtime, name)
        except Exception:
            return 0
        return value if type(value) is int and value >= 0 else 0

    @classmethod
    def _upstream_cleanup_observation(cls, runtime: ServiceClientRuntime) -> Mapping[str, Any]:
        try:
            state = runtime.state
            if state not in {"new", "registered", "failed", "close_failed", "closed"}:
                state = "unavailable"
        except Exception:
            state = "unavailable"
        return MappingProxyType(
            {
                "local_state": state,
                "cleanup_failures": cls._runtime_counter(runtime, "cleanup_failures"),
                "unregister_uncertainties": cls._runtime_counter(
                    runtime, "unregister_uncertainties"
                ),
                "unknown_registration_count": cls._runtime_counter(
                    runtime, "unknown_registration_count"
                ),
            }
        )

    def _refresh_registry_readiness(self) -> None:
        registry = self._registry
        if registry is None:
            raise ServiceHostError("REGISTRY_UNAVAILABLE")
        try:
            # Serialize the observation with the lease worker's unready
            # transition so a concurrent failure cannot be overwritten by a
            # stale ready result.
            if self._atomic_intake_services:
                with registry._lock:
                    hard_generation = registry._hard_unready_generation
                observed = self._components_readiness_state()
                with registry._lock:
                    try:
                        if hard_generation != registry._hard_unready_generation:
                            observed = 'failed'
                            self._record_readiness('failed', 'hard_unready_generation')
                        if observed != 'failed':
                            fence_state = self._atomic_intake_fence_state()
                            if fence_state == 'failed':
                                observed = 'failed'
                                self._record_readiness('failed', 'intake_fence')
                            elif fence_state == 'retry':
                                observed = 'retry'
                                self._record_readiness('retry', 'intake_fence_retry')
                        if (self._component_maintenance_faults or self._component_delay_faults
                                or getattr(self, '_host_delay_faulted', False)
                                or self._gateway_lease_faults or self._paper_monitor_faults):
                            observed = 'failed'
                            self._record_readiness('failed', self._readiness_fault_reason() or 'concurrent_fault')
                        if observed != 'failed':
                            for service in self._atomic_intake_services:
                                lease_state = service._lease_readiness_status()
                                if lease_state not in ('valid', 'retry'):
                                    observed = 'failed'
                                    self._record_readiness('failed', 'atomic_lease_failed')
                                    break
                                if lease_state == 'retry':
                                    observed = 'retry'
                                    self._record_readiness('retry', 'atomic_lease_retry')
                    except Exception:
                        observed = 'failed'
                        self._record_readiness('failed', 'atomic_recheck_exception')
                    if observed == 'retry':
                        registry._set_parent_retry_unready()
                    elif observed == 'failed':
                        # Must revoke even if an earlier retry already set False.
                        registry.set_ready(False)
                    elif registry.ready is not True:
                        registry.set_ready(True)
            else:
                ready = self._all_components_ready()
                if registry.ready is not ready:
                    registry.set_ready(ready)
        except Exception:
            self._record_readiness('failed', 'registry_update_exception')
            raise ServiceHostError("READINESS_UPDATE_FAILED") from None

    def _maintain_components(self, *, registrations_only: bool = False) -> bool:
        """Prune per-registration component state using current registry leases."""

        registry = self._registry
        if registry is None:
            return False
        had_work = bool(self._gateway_lease_drains or self._gateway_cancel_only_runs)
        for lease_drain in self._gateway_lease_drains:
            try:
                lease_drain.maintain(registry)
            except Exception:
                self._gateway_lease_faults.add(lease_drain.instance_id)
            else:
                self._gateway_lease_faults.discard(lease_drain.instance_id)
        for cancel_only in self._gateway_cancel_only_runs:
            cancel_only.maintain()
        migration_writes_enabled = registry.migration_writes_enabled
        active_market_registrations = 0
        prepared_by_instance = {item.spec.instance_id: item for item in self._prepared}
        for component in self._components:
            prepared = prepared_by_instance.get(component.instance_id)
            spec = None if prepared is None else prepared.spec
            role = None if spec is None else spec.options.get("role")
            analytics_task_writer = (
                spec is not None
                and spec.component == "risk"
                and role == "portfolio_analytics"
            )
            risk_group_compensation_writer = (
                spec is not None
                and spec.component == "risk"
                and role == "account_owner"
                and any(
                    capability.port == "service.order_entry"
                    and "orders.submit_group_compensation_close"
                    in capability.write_operations
                    for capability in component.capabilities
                )
            )
            risk_group_roll_exit_writer = (
                spec is not None
                and spec.component == "risk"
                and role == "account_owner"
                and any(
                    capability.port == "service.order_entry"
                    and "orders.submit_group_roll_exit" in capability.write_operations
                    for capability in component.capabilities
                )
            )
            group_registration_writer = spec is not None and (
                (
                    spec.component == "execution"
                    and role
                    in {
                        "group_coordinator",
                        "group_account_owner",
                    }
                )
                or (spec.component == "risk" and role == "group_reservations")
                or risk_group_compensation_writer
                or risk_group_roll_exit_writer
            )
            maintain_resources = component.maintain_resources
            maintain = None if registrations_only else component.maintain
            maintain_registrations = component.maintain_registrations
            maintain_adapter = maintain is not None and not (
                analytics_task_writer and not migration_writes_enabled
            )
            if (
                maintain_resources is None
                and not maintain_adapter
                and maintain_registrations is None
            ):
                continue
            had_work = True
            active_ids: frozenset[str] | None = None
            active_market_ids: frozenset[str] | None = None
            try:
                if maintain_resources is not None or maintain_adapter:
                    if (
                        spec is not None
                        and spec.component == "gateway"
                        and role == "account_session_worker"
                        and not component.capabilities
                    ):
                        # An internal-only provider owner has no registry
                        # registration namespace. Its service-owned lifecycle
                        # still runs with the truthful empty client projection.
                        active_ids = frozenset()
                    else:
                        active_ids = registry.active_registration_ids(
                            instance_id=component.instance_id
                        )
                    if type(active_ids) is not frozenset or any(
                        type(registration_id) is not str for registration_id in active_ids
                    ):
                        raise TypeError("invalid registry registration projection")
                if maintain_resources is not None:
                    maintain_resources(active_ids)
                if maintain_adapter:
                    if analytics_task_writer and migration_writes_enabled:
                        self._record_migration_write_attempt(fact_kind="dispatch")
                    if (
                        spec is not None
                        and spec.component == "monitor"
                        and role == "control"
                    ):
                        maintain(active_ids, owner_registry=registry)
                    else:
                        maintain(active_ids)
                if maintain_registrations is not None:
                    if component.capabilities and hasattr(
                        registry, "active_registration_contexts_for_ports"
                    ):
                        contexts = registry.active_registration_contexts_for_ports(
                            instance_id=component.instance_id,
                            ports=tuple(capability.port for capability in component.capabilities),
                        )
                    else:
                        contexts = tuple(
                            context
                            for capability in component.capabilities
                            for context in registry.active_registration_contexts(
                                instance_id=component.instance_id,
                                port=capability.port,
                            )
                        )
                    if type(contexts) is not tuple or any(
                        type(context) is not ActiveRegistrationContext for context in contexts
                    ):
                        raise TypeError("invalid registry registration context projection")
                    if (spec is not None and spec.component == "gateway"
                            and role == "trading_owner"
                            and any(cap.port == "service.market_source" for cap in component.capabilities)):
                        active_market_ids = frozenset(
                            item.context.registration_id for item in contexts
                            if item.port == "service.market_source"
                        )
                    bound_group_run = False
                    for context in contexts if migration_writes_enabled else ():
                        operation = None
                        if (
                            context.port == "service.order_entry"
                            and "orders.submit_group_leg" in context.write_operations
                        ):
                            operation = "orders.submit_group_leg"
                        elif (
                            context.port == "service.order_entry"
                            and spec is not None
                            and spec.component == "risk"
                            and role == "account_owner"
                            and "orders.submit_group_compensation_close"
                            in context.write_operations
                        ):
                            operation = "orders.submit_group_compensation_close"
                        elif (
                            context.port == "service.order_entry"
                            and spec is not None
                            and spec.component == "risk"
                            and role == "account_owner"
                            and "orders.submit_group_roll_exit"
                            in context.write_operations
                        ):
                            operation = "orders.submit_group_roll_exit"
                        elif (
                            context.port == "service.group_reservations"
                            and "reservations.reserve" in context.write_operations
                        ):
                            operation = "reservations.reserve"
                        if operation is None:
                            continue
                        scope = context.context.scope
                        if (
                            scope.execution_domain_ref is None
                            or scope.strategy_ref is None
                            or scope.account_ref is None
                            or scope.account_refs
                        ):
                            raise TypeError("invalid trusted group registration scope")
                        self._record_migration_write_attempt(fact_kind="ownership")
                        registry.bind_trusted_group_run(
                            scope.execution_domain_ref,
                            scope.strategy_ref,
                            scope.account_ref,
                            registration_id=context.context.registration_id,
                        )
                        bound_group_run = True
                    # Seeding is a one-time local registry mapping. Re-project
                    # only when a group run binding was written above.
                    if bound_group_run:
                        contexts = tuple(
                            context
                            for capability in component.capabilities
                            for context in registry.active_registration_contexts(
                                instance_id=component.instance_id,
                                port=capability.port,
                            )
                        )
                        if type(contexts) is not tuple or any(
                            type(context) is not ActiveRegistrationContext for context in contexts
                        ):
                            raise TypeError("invalid registry registration context projection")
                    if group_registration_writer and not migration_writes_enabled:
                        # The coordinator has an explicit read-only refresh
                        # mode. Keep registrations visible for query routes,
                        # but do not recover a persisted group until the
                        # destination has passed readback and write enablement.
                        if (
                            spec is not None
                            and spec.component == "execution"
                            and role == "group_coordinator"
                        ):
                            maintain_registrations(contexts, recover=False)
                        else:
                            # Other grouped owners currently combine view
                            # refresh and recovery in this callback. Until
                            # they expose a read-only refresh API, leave their
                            # view empty and keep recovery stopped.
                            refresh_registrations = getattr(
                                component, "refresh_registrations", None
                            )
                            if callable(refresh_registrations):
                                refresh_registrations(contexts)
                    else:
                        if group_registration_writer:
                            self._record_migration_write_attempt(fact_kind="ownership")
                        maintain_registrations(contexts)
            except Exception:
                self._component_maintenance_faults.add(component.instance_id)
            else:
                self._component_maintenance_faults.discard(component.instance_id)
            if (
                any(
                    capability.port == "service.market_source"
                    for capability in component.capabilities
                )
                and component.instance_id not in self._component_maintenance_faults
                and (active_market_ids is not None or active_ids is not None)
            ):
                active_market_registrations = len(
                    active_market_ids if active_market_ids is not None else active_ids
                )
        self._market_active_registration_count = active_market_registrations
        if not registrations_only:
            if self._paper_monitor_relays:
                had_work = True
            self._poll_paper_monitor_relays()
        return had_work

    def _dispatch_request(self, authenticated_principal: str, request: Any) -> Mapping[str, Any]:
        """Recheck component readiness at the exact listener dispatch point."""

        registry = self._registry
        if registry is None:
            return {"ok": False, "code": "UNAVAILABLE", "result": {}}
        try:
            self._refresh_registry_readiness()
            if self._atomic_intake_services and registry._parent_retry_unready:
                deadline = time.monotonic() + 0.25
                while registry._parent_retry_unready and time.monotonic() < deadline:
                    time.sleep(0.01)
                    self._refresh_registry_readiness()
        except ServiceHostError:
            # If readiness cannot be persisted, fail this request closed. In
            # particular, do not fall through to a registry still marked ready.
            return {"ok": False, "code": "UNAVAILABLE", "result": {}}
        return registry.handle(authenticated_principal, request)

    @staticmethod
    def _start_error_code(exc: BaseException) -> str:
        if isinstance(exc, ServiceHostError):
            return exc.code
        if isinstance(exc, ServiceConfigError):
            return "SERVICE_CONFIGURATION_INVALID"
        if isinstance(exc, ServiceComponentError):
            return exc.code
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            return "START_INTERRUPTED"
        return "START_FAILED"

    def _check_owner_thread(self) -> None:
        if self._owner_thread is None or threading.get_ident() != self._owner_thread:
            raise ServiceHostError("OWNER_THREAD_REQUIRED")

    def _bind_gateway_owner_guard(
        self, registry: ServiceRegistry, prepared: PreparedComponent,
        component: ServiceComponent, resources: ComponentResources,
        expected_capabilities: tuple[ServiceCapability, ...],
    ) -> None:
        """Fix one trusted local composition on its native owner thread."""
        from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
        from bt_api_py._gateway_owner_guard_binding import _GatewayOwnerGuardBinding
        from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayOwnerServiceCallError
        from bt_api_py.gateway_direct_service import GatewayDirectServiceBinding

        binding = resources.values.get("binding")
        holder = resources.values.get("owner_guard_binding")
        if (type(registry) is not ServiceRegistry
                or type(binding) is not GatewayDirectServiceBinding
                or type(holder) is not _GatewayOwnerGuardBinding):
            raise ServiceHostError("GATEWAY_OWNER_GUARD_BINDING_INVALID")
        owner, journal = binding._owner, binding._journal
        instance = component.instance_id
        if (type(owner) is not GatewayDirectOwner or type(journal) is not GatewayDirectCommandJournal
                or type(prepared) is not PreparedComponent
                or not any(item is prepared for item in self._prepared)
                or prepared.spec.component != "gateway"
                or prepared.spec.options.get("role") != "trading_owner"
                or prepared.spec.instance_id != instance):
            raise ServiceHostError("GATEWAY_OWNER_GUARD_BINDING_INVALID")

        def bind_on_owner() -> None:
            holder.assert_bound(owner, journal)
            # Acquire the owner condition before the Host publication lock.
            # Monitor and other service submissions can briefly hold this
            # condition without invalidating the owner or its resources.
            with owner._condition, self._gateway_owner_guard_lock:
                holder.assert_instance(instance)
                actual = {cap.port: cap for cap in component.capabilities}
                registered = {port: cap for (candidate, port), cap in registry._capabilities.items()
                              if candidate == instance}
                if (not self._gateway_owner_guard_active or self._registry is not registry
                        or not any(item is component for item in self._components)
                        or component.component != "gateway" or component.instance_id != instance
                        or not actual or set(actual) != set(prepared.spec.bind_ports)
                        or set(actual) != set(registered)
                        or type(expected_capabilities) is not tuple
                        or len(expected_capabilities) != len(registered)
                        or any(type(cap) is not ServiceCapability
                               or registered.get(cap.port) != cap
                               for cap in expected_capabilities)
                        or any(type(cap) is not ServiceCapability
                               or type(registered[port]) is not ServiceCapability
                               or cap.instance_id != instance
                               or registered[port].instance_id != instance
                               or cap.port != registered[port].port
                               or cap.operations != registered[port].operations
                               or cap.write_operations != registered[port].write_operations
                               for port, cap in actual.items())
                        or binding._owner is not owner or binding._journal is not journal
                        or instance in self._gateway_owner_guard_bindings
                        or any(owner is row[2] or journal is row[3] or holder is row[4]
                               for row in self._gateway_owner_guard_bindings.values())):
                    raise RuntimeError("GATEWAY_OWNER_GUARD_BINDING_INVALID")
                if (owner._closing or owner._done.is_set()
                        or owner.binding() is None or not owner.is_bound_to(journal)):
                    raise RuntimeError("GATEWAY_OWNER_GUARD_BINDING_INVALID")
                self._gateway_owner_guard_bindings[instance] = (
                    registry, instance, owner, journal, holder, owner._lifecycle_snapshot()
                )

        handle = None
        try:
            # Do not hold the Registry lock (or the publication lock) while
            # waiting. A running callback is not cancelled by result timeout.
            handle = owner.submit_service_call(bind_on_owner)
            handle.result(timeout_seconds=owner.service_call_queue_timeout_seconds)
        except GatewayOwnerServiceCallError as error:
            codes = {
                "SERVICE_CALL_TIMEOUT_QUEUED": "GATEWAY_OWNER_GUARD_TIMEOUT_QUEUED",
                "SERVICE_CALL_TIMEOUT_RUNNING": "GATEWAY_OWNER_GUARD_TIMEOUT_RUNNING",
                "SERVICE_CALL_EXPIRED": "GATEWAY_OWNER_GUARD_EXPIRED",
                "SERVICE_CALL_NOT_READY": "GATEWAY_OWNER_GUARD_NOT_READY",
                "SERVICE_CALL_QUEUE_FULL": "GATEWAY_OWNER_GUARD_QUEUE_FULL",
            }
            code = error.code if type(error) is GatewayOwnerServiceCallError else None
            if code == "SERVICE_CALL_TIMEOUT_QUEUED" and handle is not None:
                try:
                    handle.cancel()
                except Exception:
                    pass  # no cancellation confirmation is inferred
            raise ServiceHostError(codes.get(code, "GATEWAY_OWNER_GUARD_BINDING_INVALID")) from None
        except Exception:
            raise ServiceHostError("GATEWAY_OWNER_GUARD_BINDING_INVALID") from None

    def _gateway_risk_first_owner_current(self, registry, service) -> bool:
        """Check the exact Host publication on-owner, without waiting under Registry."""
        owner = service.owner
        if not owner._condition.acquire(blocking=False):
            return False
        try:
            if not self._gateway_owner_guard_lock.acquire(blocking=False):
                return False
            try:
                row = self._gateway_owner_guard_bindings.get(service.instance_id)
                return bool(
                    self._gateway_owner_guard_active and self._registry is registry
                    and type(row) is tuple and len(row) == 6
                    and row[0] is registry and row[1] == service.instance_id
                    and row[2] is owner and row[3] is service.journal
                    and row[4] is service._risk_first_owner_binding
                    and any(item is service for item in self._gateway_delegations)
                    and owner._lifecycle_is_current(row[5])
                )
            finally:
                self._gateway_owner_guard_lock.release()
        finally:
            owner._condition.release()

    def _check_gateway_owner_lifecycles(self) -> None:
        """Recheck startup observations without holding locks across I/O."""
        with self._gateway_owner_guard_lock:
            rows = tuple(self._gateway_owner_guard_bindings.values())
            active = self._gateway_owner_guard_active
        if not active:
            raise ServiceHostError("GATEWAY_OWNER_GUARD_BINDING_INVALID")
        try:
            if any(not row[2]._lifecycle_is_current(row[5]) for row in rows):
                raise ServiceHostError("GATEWAY_OWNER_GUARD_BINDING_INVALID")
        except Exception:
            raise ServiceHostError("GATEWAY_OWNER_GUARD_BINDING_INVALID") from None

    def _require_no_execution_submission(self) -> None:
        if self._execution_submission_active:
            raise ServiceHostError("EXECUTION_SUBMISSION_ACTIVE")

    def _record_atomic_monitor_fault(self, selection, code: str) -> None:
        self._atomic_monitor_faults.add(code)
        self._atomic_monitor_histories[self._atomic_monitor_indices[id(selection)]].add(code)

    def _maintain_atomic_monitors(self, *, force: bool = False) -> None:
        """Retry optional observers without adding them to execution readiness."""
        if not self._trusted_atomic_monitors:
            return
        now = time.monotonic()
        if not force and now < self._atomic_monitor_next_attempt:
            return
        self._atomic_monitor_next_attempt = now + 1.0
        from .service_atomic_monitor_assembly import AtomicMonitorAssembly
        for selection in tuple(self._atomic_monitor_pending):
            try:
                services = [service for service in self._atomic_intake_services
                            if service.instance_id == selection.instance_id]
                if len(services) != 1:
                    raise ValueError("unknown source")
                assembly = AtomicMonitorAssembly(selection, services[0], self._authority.identity,
                                                 self._config.service_id)
                # Retain before starting: interrupted start cannot lose a hold.
                self._atomic_monitor_owners.append(assembly)
                self._atomic_monitor_pending.remove(selection)
                assembly.start()
            except Exception:
                self._record_atomic_monitor_fault(selection, "MONITOR_START_FAILED")
        for assembly in self._atomic_monitor_owners:
            try:
                status = assembly.owner.status()
                if status.outcome == 'FAILED' or status.phase == 'CLOSING':
                    self._record_atomic_monitor_fault(assembly.selection, "MONITOR_UNAVAILABLE")
                    resource = assembly.resource
                    if resource is not None and resource.fault_code in {
                        'GAP', 'STREAM_CHANGED', 'GENERATION_CHANGED', 'SOURCE_CHANGED',
                        'SOURCE_CORRUPT', 'CHECKPOINT_CORRUPT', 'CHECKPOINT_CONFLICT',
                    }:
                        self._record_atomic_monitor_fault(assembly.selection, 'MONITOR_' + resource.fault_code)
                assembly.restart_if_safe()
            except Exception:
                self._record_atomic_monitor_fault(assembly.selection, "MONITOR_START_FAILED")

    def _shutdown_resources(self) -> tuple[str, ...]:
        """Close in reverse ownership order and retain every failed handle."""

        # A timed-out owner callback may still be running. Revoke publication
        # before any cleanup and never wait for the owner while holding this lock.
        with self._gateway_owner_guard_lock:
            self._gateway_owner_guard_active = False
            self._gateway_owner_guard_bindings.clear()
        failures: list[str] = []
        registry = self._registry
        if registry is not None:
            try:
                if registry.ready:
                    registry.set_ready(False)
            except BaseException:
                failures.append("registry_unready")

        server = self._server
        if server is not None:
            try:
                server.close()
            except BaseException:
                failures.append("listener_close")
            else:
                self._server = None
        if self._server is not None:
            # Do not tear down resources while the listener may still dispatch.
            return tuple(failures)

        # A monitor reader borrows the atomic journal. Never close components,
        # registry or their dependencies until every worker has truly stopped
        # and its cleanup is confirmed. Retry close on the same Host later.
        for index in range(len(getattr(self, '_atomic_monitor_owners', ())) - 1, -1, -1):
            assembly = self._atomic_monitor_owners[index]
            try:
                confirmed = assembly.close()
            except BaseException:
                confirmed = False
            if confirmed:
                del self._atomic_monitor_owners[index]
            else:
                failures.append("atomic_monitor_cleanup_unknown")
        if getattr(self, '_atomic_monitor_owners', ()):
            return tuple(failures)

        paper_monitor_failed = False
        if self._paper_monitor_relays:
            retained_relays: list[tuple[str, object, object]] = []
            for instance_id, reader, relay in reversed(self._paper_monitor_relays):
                try:
                    reader.close()  # type: ignore[attr-defined]
                except BaseException:
                    failures.append(f"paper_monitor_reader_close:{instance_id}")
                    retained_relays.append((instance_id, reader, relay))
                    paper_monitor_failed = True
                    continue
                try:
                    relay.close()  # type: ignore[attr-defined]
                except BaseException:
                    failures.append(f"paper_monitor_relay_close:{instance_id}")
                    retained_relays.append((instance_id, reader, relay))
                    paper_monitor_failed = True
            self._paper_monitor_relays = list(reversed(retained_relays))
            if not self._paper_monitor_relays:
                self._paper_monitor_faults.clear()

        market_publisher = self._market_publisher
        market_publisher_failed = False
        if market_publisher is not None:
            try:
                market_publisher.close()
            except BaseException:
                failures.append("market_publisher_close")
                market_publisher_failed = True
            else:
                self._market_publisher = None
                self._market_endpoint = None

        # A resource returned by the most recent provider call may not yet
        # have become a ServiceComponent because adapter construction failed.
        if not market_publisher_failed and not paper_monitor_failed:
            for index in range(len(self._pending_resources) - 1, -1, -1):
                component_name, instance_id, resource = self._pending_resources[index]
                try:
                    resource.close()
                except BaseException:
                    failures.append(f"resource_close:{component_name}:{instance_id}")
                else:
                    del self._pending_resources[index]

            for index in range(len(self._components) - 1, -1, -1):
                component = self._components[index]
                if (component.component == "execution"
                        and any(item.spec.instance_id == component.instance_id
                                and item.spec.options.get("role") == "atomic_intake"
                                for item in self._prepared)
                        and any(name == component.component and instance == component.instance_id
                                for name, instance, _resource in self._pending_resources)):
                    # The same retryable close is tracked in both places.
                    # Do not release a failed journal hold on a second attempt
                    # within this shutdown pass.
                    continue
                try:
                    component.close()
                except BaseException:
                    failures.append(
                        f"component_close:{component.component}:{component.instance_id}"
                    )
                else:
                    del self._components[index]

        component_resources_remain = bool(
            market_publisher_failed
            or paper_monitor_failed
            or self._paper_monitor_relays
            or self._pending_resources
            or self._components
        )
        if not component_resources_remain:
            self._group_runtime_resources.clear()
            self._gateway_lease_drains = []
            self._gateway_cancel_only_runs = []
            self._gateway_delegations = []
            self._gateway_parent_services = []
            self._gateway_lease_faults = set()
        upstream = self._upstream
        if upstream is not None and not component_resources_remain:
            try:
                upstream.close()
            except BaseException:
                failures.append("upstream_close")
            else:
                self._last_upstream_cleanup = self._upstream_cleanup_observation(upstream)
                if upstream.state == "closed":
                    self._upstream = None
                    self._upstream_registration_summary = None
                    self._upstream_faulted = False
                else:
                    failures.append("upstream_close")
            if self._upstream is upstream:
                self._last_upstream_cleanup = self._upstream_cleanup_observation(upstream)

        if registry is not None:
            try:
                registry.close()
            except BaseException:
                failures.append("registry_close")
            else:
                self._registry = None
        return tuple(failures)


def _component_summary(spec: Any, component: ServiceComponent | None) -> Mapping[str, Any]:
    if component is None:
        ports = tuple(spec.bind_ports)
        capabilities: tuple[Mapping[str, Any], ...] = ()
        internal_ports: tuple[str, ...] = ()
    else:
        ports = tuple(spec.bind_ports)
        capabilities = tuple(
            MappingProxyType(
                {
                    "port": capability.port,
                    "operations": tuple(sorted(capability.operations)),
                }
            )
            for capability in component.capabilities
        )
        internal_ports = tuple(sorted(component.internal_ports))
    return MappingProxyType(
        {
            "component": spec.component,
            "instance_id": spec.instance_id,
            "declared_ports": ports,
            "capabilities": capabilities,
            "internal_ports": internal_ports,
        }
    )


__all__ = [
    "ServiceHost",
    "ServiceHostError",
    "TrustedServiceAuthority",
    "TrustedServiceClients",
]
