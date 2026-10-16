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
import threading
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
    ComponentResources,
    PreparedComponent,
    ServiceComponent,
    ServiceComponentError,
    ServiceResourceProvider,
    build_component,
    component_specs,
    order_components,
    validate_components,
)
from .service_config import ServiceConfig, ServiceConfigError
from .service_deployment import validate_deployment
from .service_registry import (
    ActiveRegistrationContext,
    PrincipalGrant,
    ServiceCapability,
    ServiceRegistry,
    ServiceRegistryStartupCleanupError,
    TrustedGroupReservationPolicy,
    TrustedGroupResolutionPolicy,
    TrustedGroupWritePolicy,
    validate_capabilities_and_grants,
)
from .service_runtime import RegistrationSummary, ServiceClientRuntime

_SAFE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


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

        self._config = config
        self._authority = authority
        self._registry_path = Path(registry_path)
        self._resource_provider = resource_provider
        self._service_directory = (
            None if service_directory is None else MappingProxyType(dict(service_directory))
        )
        self._trusted_clients = trusted_clients
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
        try:
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
            for item in self._prepared:
                dependency_ref = (
                    item.options.get("provider_session_ref")
                    if item.spec.component == "execution"
                    else None
                )
                if dependency_ref is not None:
                    dependency = built_by_instance.get(dependency_ref)
                    if (
                        dependency is None
                        or "service.provider_session" not in dependency.internal_ports
                    ):
                        raise ServiceHostError("PROVIDER_SESSION_UNAVAILABLE")

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
                )
                self._components.append(component)
                if (
                    not self._pending_resources
                    or self._pending_resources[-1][0] != item.spec.component
                    or self._pending_resources[-1][1] != item.spec.instance_id
                ):
                    raise ServiceHostError("RESOURCE_TRACKING_FAILED")
                self._pending_resources.pop()
                built_by_instance[component.instance_id] = component

            self._validate_group_runtime_bundle_sharing()

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
                    or (
                        planned.port == "service.order_query"
                        and actual.operations
                        == planned.operations - {"execution.account_books.query"}
                        and actual.write_operations == planned.write_operations
                    )
                    or (
                        planned.port == "service.order_entry"
                        and actual.operations
                        == planned.operations - {"orders.submit_approved_target"}
                        and actual.write_operations
                        == planned.write_operations - {"orders.submit_approved_target"}
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
            return self
        except BaseException as exc:
            failure_code = self._start_error_code(exc)
            cleanup_failures = self._shutdown_resources()
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
            self._maintain_components()
            self._refresh_registry_readiness()
            return handled
        except ServiceHostError:
            raise
        except Exception:
            raise ServiceHostError("SERVE_FAILED") from None

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
        active_market_registrations = 0
        for component in self._components:
            maintain = None if registrations_only else component.maintain
            maintain_registrations = component.maintain_registrations
            if maintain is None and maintain_registrations is None:
                continue
            active_ids: frozenset[str] | None = None
            try:
                if maintain is not None:
                    active_ids = registry.active_registration_ids(instance_id=component.instance_id)
                    if type(active_ids) is not frozenset or any(
                        type(registration_id) is not str for registration_id in active_ids
                    ):
                        raise TypeError("invalid registry registration projection")
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
                    for context in contexts:
                        operation = None
                        if (
                            context.port == "service.order_entry"
                            and "orders.submit_group_leg" in context.write_operations
                        ):
                            operation = "orders.submit_group_leg"
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
        if not market_publisher_failed:
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
            market_publisher_failed or self._pending_resources or self._components
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
