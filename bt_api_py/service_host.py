"""Fail-closed lifecycle owner for fixed SDK service components.

Construction is pure. ``start`` validates the trusted references, imports only
the selected fixed component packages, starts any explicitly wired upstream
service clients, resolves trusted resources, creates a not-ready SQLite
registry, and finally binds the CURVE-authenticated ZMQv2 listener on the
calling thread. Outbound wiring comes only from a trusted code bundle; the
manifest cannot name Python modules or executable factories.
"""

from __future__ import annotations

import re
import sys
import threading
from hashlib import sha256
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from math import floor, isfinite
from pathlib import Path
from types import MappingProxyType
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


def _account_read_metadata_omission_allowed(
    component: str,
    role: object,
    resources: ComponentResources,
) -> bool:
    """Allow the one account-read operation the resolved provider cannot serve.

    ``build_component`` has already validated this resource as the exact
    GatewayAccountSessionWorker for the selected account-session component.
    Re-check its exact type and provider fact here so the Host plan exception
    is derived from the resolved trusted object, never from manifest options.
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
    return (
        type(metadata_available) is bool
        and metadata_available is False
        and provider_exposes_metadata is False
    )


def _matches_account_read_metadata_omission(
    actual: ServiceCapability,
    planned: ServiceCapability,
    allowed_instance_ids: set[str],
) -> bool:
    """Match only the exact read-only account metadata capability omission."""

    return (
        actual.instance_id == planned.instance_id
        and actual.port == planned.port == "service.account_read"
        and planned.instance_id in allowed_instance_ids
        and actual.operations
        == planned.operations - {"account.instrument_metadata"}
        and actual.write_operations == planned.write_operations
        and actual.market_wire_version == planned.market_wire_version
    )


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
    """

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

        self._config = config
        self._authority = authority
        self._registry_path = Path(registry_path)
        self._resource_provider = resource_provider
        self._service_directory = (
            None if service_directory is None else MappingProxyType(dict(service_directory))
        )
        self._trusted_clients = trusted_clients
        self._migration_deployment_epoch = migration_deployment_epoch
        try:
            self._specs = component_specs(config.components)
        except ServiceComponentError as exc:
            raise ServiceHostError(exc.code) from None
        self._state = "new"
        self._owner_thread: int | None = None
        self._prepared: tuple[PreparedComponent, ...] = ()
        self._components: list[ServiceComponent] = []
        self._pending_resources: list[tuple[str, str, ComponentResources]] = []
        self._group_runtime_resources: list[tuple[PreparedComponent, ComponentResources]] = []
        self._registry: ServiceRegistry | None = None
        self._reply_size_checker: Callable[[Any, Mapping[str, Any], int | None], bool] | None = None
        self._server: Any = None
        self._upstream: ServiceClientRuntime | None = None
        self._upstream_registration_summary: RegistrationSummary | None = None
        self._upstream_faulted = False
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
                        "maintenance_failures": tuple(sorted(self._component_maintenance_faults)),
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
                try:
                    self._upstream = ServiceClientRuntime(
                        self._config,
                        identity=self._authority.identity,
                        scope_resolver=bundle.scope_resolver,
                        peer_resolver=bundle.peer_resolver,
                        transport_factory=bundle.transport_factory,
                        requested_operations=bundle.requested_operations,
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
            account_read_metadata_omission_allowed: set[str] = set()
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
                if _account_read_metadata_omission_allowed(
                    item.spec.component,
                    item.spec.options.get("role"),
                    pending_resources,
                ):
                    account_read_metadata_omission_allowed.add(item.spec.instance_id)
                if (
                    item.spec.component == "execution"
                    and item.spec.options.get("role") == "account_owner"
                    and (
                        "paper_owner_domain" in pending_resources.values
                        or "paper_composition" in item.options
                    )
                ):
                    paper_owner_resources.append((item, component, pending_resources))
                if (
                    item.spec.component == "execution"
                    and item.spec.options.get("role") == "account_owner"
                    and "approved_target_authority" not in pending_resources.values
                ):
                    owner_reduce_omission_allowed.add(item.spec.instance_id)
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
                        planned.port == "service.market_source"
                        and actual.operations == planned.operations - {"market.history"}
                        and actual.write_operations == planned.write_operations
                    )
                    or _matches_account_read_metadata_omission(
                        actual,
                        planned,
                        account_read_metadata_omission_allowed,
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
                        == planned.operations - {"orders.submit_group_roll_exit"}
                        and actual.write_operations
                        == planned.write_operations - {"orders.submit_group_roll_exit"}
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
                self._registry = ServiceRegistry(
                    self._registry_path,
                    service_id=self._config.service_id,
                    capabilities=capabilities,
                    grants=self._authority.grants,
                    policy_version=self._authority.registration_policy_version,
                    requirements_version=self._authority.service_requirements_version,
                    access_policy_ref=self._authority.registration_policy_ref,
                    requirements_ref=self._authority.service_requirements_ref,
                    ready=False,
                    reply_size_checker=self._reply_size_checker,
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
                )
            except ServiceRegistryStartupCleanupError as exc:
                self._registry = exc.registry
                raise ServiceHostError("REGISTRY_STARTUP_CLEANUP_FAILED") from None
            for component in self._components:
                for operation, handler in component.handlers.items():
                    self._registry.register_handler(component.instance_id, operation, handler)

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
            self._endpoint = self._server.endpoint

            if not self._all_components_ready():
                raise ServiceHostError("COMPONENT_NOT_READY")
            self._registry.set_ready(True)
            self._state = "started"
            if migration_guard is not None:
                guard_to_exit = migration_guard
                migration_guard = None
                try:
                    guard_to_exit.__exit__(None, None, None)
                except MigrationGateError as exc:
                    raise ServiceHostError(exc.code) from None
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
            delay = self.next_maintenance_delay()
            bounded_timeout = timeout_ms
            if delay is not None:
                if type(delay) not in (int, float) or not isfinite(delay) or delay < 0:
                    self._upstream_faulted = True
                    self._refresh_registry_readiness()
                    bounded_timeout = 0
                else:
                    delay_ms = delay * 1000
                    if isfinite(delay_ms):
                        bounded_timeout = min(timeout_ms, max(0, floor(delay_ms)))
            self._maintain_components()
            self._refresh_registry_readiness()
            handled = self._server.serve_once(bounded_timeout)
            # Only the validated single market-hub Host can defer this second
            # sweep: its next owner-loop iteration performs the same preflight,
            # and Registry.handle independently checks expiry for this request.
            # Mixed Hosts retain the post-request sweep because their component
            # maintenance can include non-market owner work.
            if not handled or not self._is_single_market_hub_host():
                self._maintain_components()
            self._refresh_registry_readiness()
            return handled
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
                    "market.subscribe",
                    "market.unsubscribe",
                    "market.snapshot",
                    "market.head",
                    "market.read_after",
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
        cleanup_failures = self._shutdown_resources()
        self._last_cleanup_failures = cleanup_failures
        self._cleanup_history.extend(cleanup_failures)
        self._state = "close_failed" if cleanup_failures else "closed"
        if cleanup_failures:
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
            and item.spec.options.get("role") == "group_account_owner"
        ):
            self._group_runtime_resources.append((item, resources))
        if "service.market_source" not in item.operations_by_port:
            return
        if set(resources.values) != {"hub"}:
            raise ServiceHostError("MARKET_RESOURCE_KEYS_INVALID")
        hub = resources.values["hub"]
        expected_hub_type = getattr(item.module, "GatewayMarketHub", None)
        if not isinstance(expected_hub_type, type) or type(hub) is not expected_hub_type:
            raise ServiceHostError("MARKET_HUB_CONFIGURATION_INVALID")
        self._validate_market_hub_audience(hub)

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
                self._authority.principals,
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
            hub.set_publisher(publisher.publish)
        except Exception:
            raise ServiceHostError("MARKET_HUB_BINDING_FAILED") from None

    def _validate_market_hub_audience(self, hub: object) -> None:
        """Check the trusted hub's complete authority before opening sockets."""

        try:
            identity = hub.identity
            service_scope = hub.service_scope
            key_universe = hub.key_universe
            principal_allowed_keys = hub.principal_allowed_keys
            authorized_principals = hub.authorized_principals
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
        expected_principals = frozenset(self._authority.principals.values())
        if frozenset(principal_allowed_keys) != expected_principals:
            raise ServiceHostError("MARKET_HUB_AUDIENCE_INVALID")
        if (
            type(authorized_principals) is not frozenset
            or authorized_principals != expected_principals
        ):
            raise ServiceHostError("MARKET_HUB_AUDIENCE_INVALID")
        for principal_ref in expected_principals:
            allowed = principal_allowed_keys.get(principal_ref)
            if type(allowed) is not frozenset or allowed != key_universe:
                raise ServiceHostError("MARKET_HUB_AUDIENCE_INVALID")
        validate_audience = getattr(hub, "validate_audience", None)
        if not callable(validate_audience):
            raise ServiceHostError("MARKET_HUB_CONFIGURATION_INVALID")
        try:
            validate_audience(expected_principals, self._authority.identity, self._authority.scope)
        except Exception:
            raise ServiceHostError("MARKET_HUB_AUDIENCE_INVALID") from None
        if not callable(getattr(hub, "set_publisher", None)):
            raise ServiceHostError("MARKET_HUB_CONFIGURATION_INVALID")
        try:
            readiness = hub.ready  # type: ignore[attr-defined]
        except Exception:
            raise ServiceHostError("MARKET_HUB_CONFIGURATION_INVALID") from None
        if type(readiness) is not bool:
            raise ServiceHostError("MARKET_HUB_CONFIGURATION_INVALID")

    def _all_components_ready(self) -> bool:
        if self._component_maintenance_faults:
            return False
        if self._paper_monitor_faults:
            return False
        if self._upstream is not None:
            try:
                if self._upstream_faulted or self._upstream.healthy is not True:
                    return False
            except Exception:
                return False
        for component in self._components:
            try:
                if component.readiness() is not True:
                    return False
            except Exception:
                return False
        return True

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

    def maintain(self) -> RegistrationSummary:
        """Synchronously maintain due upstream leases on the owner thread."""

        self._check_owner_thread()
        if self._state != "started":
            raise ServiceHostError("HOST_NOT_STARTED")
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
        """Return seconds until the next upstream renewal is due."""

        self._check_owner_thread()
        if self._state != "started":
            raise ServiceHostError("HOST_NOT_STARTED")
        upstream = self._upstream
        if upstream is None:
            return None
        try:
            return upstream.next_maintenance_delay()
        except Exception:
            self._upstream_faulted = True
            with suppress(ServiceHostError):
                self._refresh_registry_readiness()
            return 0.0

    def recover_upstreams(self) -> RegistrationSummary:
        """Explicitly replace an upstream registration session, without replay."""

        self._check_owner_thread()
        if self._state != "started":
            raise ServiceHostError("HOST_NOT_STARTED")
        upstream = self._upstream
        if upstream is None:
            raise ServiceHostError("NO_UPSTREAM_CLIENTS")
        try:
            summary = upstream.recover()
            self._upstream_registration_summary = summary
            self._upstream_faulted = upstream.healthy is not True
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
        ready = self._all_components_ready()
        if registry.ready is ready:
            return
        try:
            registry.set_ready(ready)
        except Exception:
            raise ServiceHostError("READINESS_UPDATE_FAILED") from None

    def _maintain_components(self, *, registrations_only: bool = False) -> None:
        """Prune per-registration component state using current registry leases."""

        registry = self._registry
        if registry is None:
            return
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
                    and role in {"group_coordinator", "group_account_owner"}
                )
                or (spec.component == "risk" and role == "group_reservations")
                or risk_group_roll_exit_writer
            )
            maintain = None if registrations_only else component.maintain
            maintain_registrations = component.maintain_registrations
            if maintain is None and maintain_registrations is None:
                continue
            active_ids: frozenset[str] | None = None
            try:
                if maintain is not None and not (
                    analytics_task_writer and not migration_writes_enabled
                ):
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
                    # Seeding is a one-time local registry mapping. Adapters
                    # receive the resulting persistent run projection, never
                    # an unbound eligibility snapshot.
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
                and active_ids is not None
            ):
                active_market_registrations = len(active_ids)
        self._market_active_registration_count = active_market_registrations
        if not registrations_only:
            self._poll_paper_monitor_relays()

    def _dispatch_request(self, authenticated_principal: str, request: Any) -> Mapping[str, Any]:
        """Recheck component readiness at the exact listener dispatch point."""

        registry = self._registry
        if registry is None:
            return {"ok": False, "code": "UNAVAILABLE", "result": {}}
        try:
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

    def _shutdown_resources(self) -> tuple[str, ...]:
        """Close in reverse ownership order and retain every failed handle."""

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
