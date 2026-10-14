"""Fail-closed lifecycle owner for fixed SDK service components.

Construction is pure. ``start`` validates the trusted references, imports only
the selected fixed component packages, resolves trusted resources, creates a
not-ready SQLite registry, and finally binds the CURVE-authenticated ZMQv2
listener on the calling thread. The current round supports local read adapters
and explicitly rejects outbound client bindings before resource acquisition;
it does not silently ignore them.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from .service_components import (
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
    PrincipalGrant,
    ServiceCapability,
    ServiceRegistry,
    ServiceRegistryStartupCleanupError,
    validate_capabilities_and_grants,
)

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

        object.__setattr__(self, "principals", MappingProxyType(principals))
        object.__setattr__(self, "grants", MappingProxyType(grants))


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

        self._config = config
        self._authority = authority
        self._registry_path = Path(registry_path)
        self._resource_provider = resource_provider
        self._service_directory = (
            None if service_directory is None else MappingProxyType(dict(service_directory))
        )
        try:
            self._specs = component_specs(config.components)
        except ServiceComponentError as exc:
            raise ServiceHostError(exc.code) from None
        self._state = "new"
        self._owner_thread: int | None = None
        self._prepared: tuple[PreparedComponent, ...] = ()
        self._components: list[ServiceComponent] = []
        self._pending_resources: list[tuple[str, str, ComponentResources]] = []
        self._registry: ServiceRegistry | None = None
        self._server: Any = None
        self._endpoint: str | None = None
        self._start_failure: str | None = None
        self._last_cleanup_failures: tuple[str, ...] = ()
        self._cleanup_history: list[str] = []

    @property
    def endpoint(self) -> str | None:
        """The transport's bound endpoint after successful startup."""

        return self._endpoint

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
            self._prepared = order_components(validate_components(self._specs))
            planned_capabilities = tuple(
                ServiceCapability(item.spec.instance_id, port, operations)
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
            except ImportError:
                raise ServiceHostError("TRANSPORT_UNAVAILABLE") from None
            except Exception:
                raise ServiceHostError("TRANSPORT_CONFIGURATION_INVALID") from None

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

                component = build_component(
                    item,
                    scope=self._authority.scope,
                    resource_provider=self._resource_provider,
                    on_resources=lambda resource, component=item.spec.component, instance=item.spec.instance_id: (
                        self._pending_resources.append((component, instance, resource))
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

            capabilities = tuple(
                capability
                for component in self._components
                for capability in component.capabilities
            )
            if capabilities != planned_capabilities:
                raise ServiceHostError("CAPABILITY_PLAN_MISMATCH")

            try:
                self._registry = ServiceRegistry(
                    self._registry_path,
                    service_id=self._config.service_id,
                    capabilities=capabilities,
                    grants=self._authority.grants,
                    policy_version=self._authority.registration_policy_version,
                    requirements_version=self._authority.service_requirements_version,
                    ready=False,
                )
            except ServiceRegistryStartupCleanupError as exc:
                self._registry = exc.registry
                raise ServiceHostError("REGISTRY_STARTUP_CLEANUP_FAILED") from None
            for component in self._components:
                for operation, handler in component.handlers.items():
                    self._registry.register_handler(component.instance_id, operation, handler)

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
        try:
            # Readiness is a live invariant, not only a startup check. A
            # failed/false check withdraws business registration and dispatch,
            # while registry discovery and unregister remain available.
            self._refresh_registry_readiness()
            return self._server.serve_once(timeout_ms)
        except ServiceHostError:
            raise
        except Exception:
            raise ServiceHostError("SERVE_FAILED") from None

    def close(self) -> None:
        """Mark the registry unready and close listener/components/registry."""

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
        if not self._config.clients:
            return
        if self._service_directory is None:
            raise ServiceHostError("OUTBOUND_DIRECTORY_REQUIRED")
        directory = dict(self._service_directory)
        directory[self._config.service_id] = self._config
        try:
            validate_deployment(directory)
        except Exception:
            raise ServiceHostError("OUTBOUND_DEPLOYMENT_INVALID") from None
        # Static graph validation does not create authenticated client
        # connections. Do not silently discard manifest outbound bindings.
        raise ServiceHostError("OUTBOUND_WIRING_UNSUPPORTED")

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

    def _all_components_ready(self) -> bool:
        for component in self._components:
            try:
                if component.readiness() is not True:
                    return False
            except Exception:
                return False
        return True

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

        # A resource returned by the most recent provider call may not yet
        # have become a ServiceComponent because adapter construction failed.
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
                failures.append(f"component_close:{component.component}:{component.instance_id}")
            else:
                del self._components[index]

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


__all__ = ["ServiceHost", "ServiceHostError", "TrustedServiceAuthority"]
