"""Pure validation of a selected SDK service deployment graph.

This module composes parser-created configuration snapshots into a deterministic
deployment description. It performs no service activation, authorization,
network access, DNS lookup, or domain-package loading. In particular, a valid
plan does not establish remote readiness or safe failover/fencing.
"""

from __future__ import annotations

import heapq
import ipaddress
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final
from urllib.parse import urlsplit

from bt_api_py.service_config import ClientConfig, ServiceConfig, ServiceConfigError

# Each entry is the minimum remote service port required by that mount. The
# monitor control API is intentionally optional and is listed separately below.
MOUNT_PORTS: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {
        "store.market": ("service.market_source",),
        "store.account": ("service.account_read",),
        "broker.orders": ("service.order_entry",),
        "broker.order_query": ("service.order_query",),
        "broker.order_groups": ("service.order_group_entry",),
        "broker.group_monitor": ("service.monitor_query",),
        "risk.read_model": ("service.risk_query",),
        "runtime.telemetry": ("service.telemetry_ingress",),
        "application.market": ("service.market_source",),
        "application.account": ("service.account_read",),
        "application.orders": ("service.order_entry",),
        "application.paper_legacy_import": ("service.paper_legacy_import",),
        "application.order_query": ("service.order_query",),
        "application.order_groups": ("service.order_group_entry",),
        "application.risk_read": ("service.risk_query",),
        "application.risk_analysis": ("service.risk_analysis",),
        "application.risk_policy": ("service.risk_policy",),
        "application.monitor": ("service.monitor_query",),
        "application.owner_control": ("service.owner_control",),
        "upstream.market": ("service.market_source",),
        "upstream.account": ("service.account_read",),
        "upstream.orders": ("service.order_entry",),
        "upstream.order_query": ("service.order_query",),
        "upstream.risk_read": ("service.risk_query",),
        "upstream.risk_policy": ("service.risk_policy",),
        "upstream.telemetry": ("service.telemetry_ingress",),
        "upstream.owner_control": ("service.owner_control",),
        "upstream.account_reservations": ("service.group_reservations",),
        "upstream.group_compensation_orders": ("service.order_entry",),
        "upstream.group_compensation_order_query": ("service.order_query",),
        "upstream.group_roll_exit_orders": ("service.order_entry",),
        "upstream.group_roll_exit_order_query": ("service.order_query",),
    }
)

# Optional ports augment (and never replace) MOUNT_PORTS requirements.
OPTIONAL_MOUNT_PORTS: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {"application.monitor": ("service.monitor_control",)}
)

_MARKET_MOUNTS: Final[frozenset[str]] = frozenset(
    {"store.market", "application.market", "upstream.market"}
)
_WRITER_MOUNTS: Final[frozenset[str]] = frozenset(
    {
        "broker.orders",
        "application.orders",
        "upstream.orders",
        "upstream.group_compensation_orders",
    }
)
_WILDCARD_HOSTS: Final[frozenset[str]] = frozenset({"*", "0.0.0.0", "::"})


@dataclass(frozen=True, slots=True)
class ComponentActivation:
    """A component selected by a target service configuration.

    This is a static selection record only. The class name does not mean that
    the component has been imported, constructed, or started.
    """

    service_id: str
    component: str
    instance_id: str
    bind_ports: tuple[str, ...]
    options: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ClientBindingRequest:
    """A parsed request to connect one client mount to one service instance."""

    requester_service_id: str | None
    client_index: int | None
    client_alias: str
    binding_index: int
    mount: str
    service_id: str
    instance_id: str
    scope_ref: str
    transport_ref: str
    account_ref: str | None
    portfolio_ref: str | None
    required: bool
    connect_endpoint: str
    target_bind_endpoint: str


@dataclass(frozen=True, slots=True)
class DeploymentPlan:
    """Immutable output from offline graph validation.

    Services are ordered after their dependencies. ``dependencies`` maps each
    service to the services it calls. The remaining flags make the evidence
    boundary explicit: planning does not start services, verify remote
    readiness, or provide automatic failover or external fencing.
    """

    service_order: tuple[str, ...]
    dependencies: Mapping[str, tuple[str, ...]]
    component_activations: tuple[ComponentActivation, ...]
    client_binding_requests: tuple[ClientBindingRequest, ...]
    services_activated: bool = False
    remote_readiness_verified: bool = False
    automatic_failover_supported: bool = False
    external_fencing_supported: bool = False


def validate_deployment(
    services: Mapping[str, ServiceConfig],
    clients: Sequence[ClientConfig] = (),
) -> DeploymentPlan:
    """Validate service dependencies and client bindings without side effects.

    ``services`` is a static directory keyed by the exact ``service_id`` in
    each parser-created service configuration. Client and service-outbound
    bindings must resolve to a selected service, instance, declared mount port,
    and compatible static endpoint pair. Scope references remain opaque grant
    selectors for runtime authorization; this function does not equate them to
    a target service's root scope or claim that a remote service is ready.
    """

    service_entries = _snapshot_service_directory(services)
    client_entries = _snapshot_client_sequence(clients)

    service_public: dict[str, dict[str, Any]] = {}
    for index, (directory_key, config) in enumerate(service_entries):
        path = f"$.services[{index}]"
        public = _public_snapshot(config, ServiceConfig, path)
        service_id = public.get("service_id")
        if type(service_id) is not str:
            _fail("INVALID_PUBLIC_CONFIG", f"{path}.service_id")
        if directory_key != service_id:
            _fail("SERVICE_KEY_MISMATCH", f"{path}.service_id")
        if service_id in service_public:
            _fail("DUPLICATE_SERVICE_ID", f"{path}.service_id")
        service_public[service_id] = public

    dependencies: dict[str, set[str]] = {service_id: set() for service_id in service_public}
    activations: list[ComponentActivation] = []
    binding_requests: list[ClientBindingRequest] = []
    account_writers: dict[
        str,
        tuple[
            tuple[str | None, int | None, str, str, str, str, str | None, str | None, str],
            set[str],
        ],
    ] = {}
    service_instances: dict[str, dict[str, frozenset[str]]] = {}

    # Inspect only normalized parser output. Sorting service IDs and aliases
    # makes plans independent of the input mapping's insertion order.
    ordered_service_ids = tuple(sorted(service_public))
    for service_index, service_id in enumerate(ordered_service_ids):
        public = service_public[service_id]
        component_rows = _component_rows(public, f"$.services[{service_index}]")
        instance_ports: dict[str, frozenset[str]] = {}
        for component_name, component_settings in component_rows:
            instance_id = component_settings["instance_id"]
            bind_ports = tuple(sorted(component_settings["bind_ports"]))
            instance_ports[instance_id] = frozenset(bind_ports)
            activations.append(
                ComponentActivation(
                    service_id=service_id,
                    component=component_name,
                    instance_id=instance_id,
                    bind_ports=bind_ports,
                    options=_freeze_public(component_settings.get("options", {})),
                )
            )
        service_instances[service_id] = instance_ports

    for service_index, service_id in enumerate(ordered_service_ids):
        public = service_public[service_id]
        for alias_index, alias, binding_index, binding in _bindings(
            public, f"$.services[{service_index}].clients"
        ):
            request = _validate_binding(
                binding=binding,
                requester_service_id=service_id,
                client_index=None,
                client_alias=alias,
                binding_index=binding_index,
                service_public=service_public,
                service_instances=service_instances,
                public=public,
                path=(
                    f"$.services[{service_index}].clients[{alias_index}].bindings[{binding_index}]"
                ),
            )
            dependencies[service_id].add(request.service_id)
            _record_writer(account_writers, request, len(binding_requests))
            binding_requests.append(request)

    for client_index, public in enumerate(client_entries):
        path = f"$.clients[{client_index}]"
        for alias_index, alias, binding_index, binding in _bindings(public, f"{path}.clients"):
            request = _validate_binding(
                binding=binding,
                requester_service_id=None,
                client_index=client_index,
                client_alias=alias,
                binding_index=binding_index,
                service_public=service_public,
                service_instances=service_instances,
                public=public,
                path=f"{path}.clients[{alias_index}].bindings[{binding_index}]",
            )
            _record_writer(account_writers, request, len(binding_requests))
            binding_requests.append(request)

    service_order = _topological_order(dependencies)
    frozen_dependencies = MappingProxyType(
        {service_id: tuple(sorted(dependencies[service_id])) for service_id in ordered_service_ids}
    )
    return DeploymentPlan(
        service_order=service_order,
        dependencies=frozen_dependencies,
        component_activations=tuple(activations),
        client_binding_requests=tuple(binding_requests),
    )


def _snapshot_service_directory(
    services: Mapping[str, ServiceConfig],
) -> list[tuple[str, ServiceConfig]]:
    if not isinstance(services, Mapping):
        _fail("INVALID_SERVICE_DIRECTORY", "$.services")
    try:
        entries = list(services.items())
        if any(type(key) is not str for key, _value in entries):
            _fail("INVALID_SERVICE_DIRECTORY", "$.services")
    except ServiceConfigError:
        raise
    except Exception:
        _fail("INVALID_SERVICE_DIRECTORY", "$.services")
    return sorted(entries, key=lambda item: item[0])


def _snapshot_client_sequence(clients: Sequence[ClientConfig]) -> tuple[dict[str, Any], ...]:
    if isinstance(clients, (str, bytes, bytearray)) or not isinstance(clients, Sequence):
        _fail("INVALID_CLIENT_LIST", "$.clients")
    try:
        entries = tuple(clients)
    except Exception:
        _fail("INVALID_CLIENT_LIST", "$.clients")
    public_entries: list[dict[str, Any]] = []
    for index, config in enumerate(entries):
        public_entries.append(_public_snapshot(config, ClientConfig, f"$.clients[{index}]"))
    return tuple(public_entries)


def _public_snapshot(config: object, expected_type: type[Any], path: str) -> dict[str, Any]:
    if type(config) is not expected_type:
        _fail("INVALID_CONFIG", path)
    try:
        public = config.to_public_dict()
    except Exception:
        _fail("INVALID_CONFIG", path)
    if type(public) is not dict:
        _fail("INVALID_PUBLIC_CONFIG", path)
    return public


def _component_rows(public: Mapping[str, Any], path: str) -> list[tuple[str, Mapping[str, Any]]]:
    components = public.get("components")
    if type(components) is not dict or not components:
        _fail("INVALID_PUBLIC_CONFIG", f"{path}.components")
    rows: list[tuple[str, Mapping[str, Any]]] = []
    seen_instances: set[str] = set()
    for index, component_name in enumerate(sorted(components)):
        component = components[component_name]
        settings = component.get("settings") if type(component) is dict else None
        if type(settings) is not dict:
            _fail("INVALID_PUBLIC_CONFIG", f"{path}.components[{index}]")
        instance_id = settings.get("instance_id")
        ports = settings.get("bind_ports")
        if (
            type(instance_id) is not str
            or type(ports) is not list
            or any(type(port) is not str for port in ports)
        ):
            _fail("INVALID_PUBLIC_CONFIG", f"{path}.components[{index}]")
        if instance_id in seen_instances:
            _fail("DUPLICATE_INSTANCE", f"{path}.components[{index}]")
        seen_instances.add(instance_id)
        options = settings.get("options", {})
        if type(options) is not dict:
            _fail("INVALID_PUBLIC_CONFIG", f"{path}.components[{index}]")
        rows.append((component_name, settings))
    return rows


def _bindings(
    public: Mapping[str, Any], path: str
) -> list[tuple[int, str, int, Mapping[str, Any]]]:
    clients = public.get("clients", {})
    if type(clients) is not dict:
        _fail("INVALID_PUBLIC_CONFIG", path)
    rows: list[tuple[int, str, int, Mapping[str, Any]]] = []
    for alias_index, alias in enumerate(sorted(clients)):
        client = clients[alias]
        settings = client.get("settings") if type(client) is dict else None
        bindings = settings.get("bindings") if type(settings) is dict else None
        if type(bindings) is not list:
            _fail("INVALID_PUBLIC_CONFIG", f"{path}[{alias_index}]")
        for binding_index, binding in enumerate(bindings):
            if type(binding) is not dict:
                _fail("INVALID_PUBLIC_CONFIG", f"{path}[{alias_index}]")
            rows.append((alias_index, alias, binding_index, binding))
    return rows


def _validate_binding(
    *,
    binding: Mapping[str, Any],
    requester_service_id: str | None,
    client_index: int | None,
    client_alias: str,
    binding_index: int,
    service_public: Mapping[str, Mapping[str, Any]],
    service_instances: Mapping[str, Mapping[str, frozenset[str]]],
    public: Mapping[str, Any],
    path: str,
) -> ClientBindingRequest:
    mount = binding.get("mount")
    target_service_id = binding.get("service_id")
    instance_id = binding.get("instance_id")
    if type(mount) is not str or mount not in MOUNT_PORTS:
        _fail("UNKNOWN_MOUNT", f"{path}.mount")
    if type(target_service_id) is not str or target_service_id not in service_public:
        _fail("MISSING_TARGET_SERVICE", f"{path}.service_id")
    if type(instance_id) is not str:
        _fail("INVALID_PUBLIC_CONFIG", f"{path}.instance_id")

    target = service_public[target_service_id]
    target_instances = service_instances[target_service_id]
    target_ports = target_instances.get(instance_id)
    if target_ports is None:
        _fail("UNKNOWN_INSTANCE", f"{path}.instance_id")
    missing_ports = tuple(port for port in MOUNT_PORTS[mount] if port not in target_ports)
    if missing_ports:
        _fail("MOUNT_PORT_MISMATCH", f"{path}.mount")

    source_profiles = _profiles(public, path)
    transport_ref = binding.get("transport_ref")
    if type(transport_ref) is not str or transport_ref not in source_profiles:
        _fail("UNKNOWN_TRANSPORT", f"{path}.transport_ref")
    source_profile = source_profiles[transport_ref]
    source_endpoints = source_profile.get("endpoints") if type(source_profile) is dict else None
    if type(source_endpoints) is not dict:
        _fail("INVALID_PUBLIC_CONFIG", f"{path}.transport_ref")

    target_profiles = _profiles(target, "$.services")
    target_transport_ref = target.get("transport_ref")
    target_profile = target_profiles.get(target_transport_ref)
    target_endpoints = target_profile.get("endpoints") if type(target_profile) is dict else None
    if type(target_endpoints) is not dict:
        _fail("INVALID_PUBLIC_CONFIG", "$.services")

    # Every configured client endpoint in this selected profile targets the
    # same service identity. Market bindings need both RPC for discovery,
    # registration, and requests, and market transport for the data stream.
    required_connect_endpoints = (
        frozenset({"rpc_connect", "market_connect"})
        if mount in _MARKET_MOUNTS
        else frozenset({"rpc_connect"})
    )
    for connect_name, bind_name in (
        ("rpc_connect", "rpc_bind"),
        ("market_connect", "market_bind"),
    ):
        configured_connect = source_endpoints.get(connect_name)
        if configured_connect is None:
            if connect_name in required_connect_endpoints:
                _fail("MISSING_ENDPOINT", f"{path}.transport_ref")
            continue
        configured_bind = target_endpoints.get(bind_name)
        if type(configured_connect) is not str or type(configured_bind) is not str:
            _fail("MISSING_ENDPOINT", f"{path}.transport_ref")
        _validate_endpoint_pair(configured_connect, configured_bind, path)

    client_endpoint_name = "market_connect" if mount in _MARKET_MOUNTS else "rpc_connect"
    target_endpoint_name = "market_bind" if mount in _MARKET_MOUNTS else "rpc_bind"
    connect_endpoint = source_endpoints.get(client_endpoint_name)
    target_bind_endpoint = target_endpoints.get(target_endpoint_name)
    if type(connect_endpoint) is not str:
        _fail("MISSING_ENDPOINT", f"{path}.transport_ref")
    if type(target_bind_endpoint) is not str:
        _fail("MISSING_ENDPOINT", f"{path}.service_id")

    account_ref = binding.get("account_ref")
    portfolio_ref = binding.get("portfolio_ref")
    return ClientBindingRequest(
        requester_service_id=requester_service_id,
        client_index=client_index,
        client_alias=client_alias,
        binding_index=binding_index,
        mount=mount,
        service_id=target_service_id,
        instance_id=instance_id,
        scope_ref=binding["scope_ref"],
        transport_ref=transport_ref,
        account_ref=account_ref,
        portfolio_ref=portfolio_ref,
        required=binding["required"],
        connect_endpoint=connect_endpoint,
        target_bind_endpoint=target_bind_endpoint,
    )


def _profiles(public: Mapping[str, Any], path: str) -> Mapping[str, Any]:
    communication = public.get("communication")
    profiles = communication.get("profiles") if type(communication) is dict else None
    if type(profiles) is not dict:
        _fail("INVALID_PUBLIC_CONFIG", f"{path}.communication")
    return profiles


def _validate_endpoint_pair(connect: str, bind: str, path: str) -> None:
    connect_host, connect_port = _endpoint_host_port(connect, path)
    bind_host, bind_port = _endpoint_host_port(bind, path)
    if connect_port != bind_port:
        _fail("ENDPOINT_MISMATCH", f"{path}.transport_ref")
    if _is_wildcard_host(bind_host):
        return
    connect_ip = _literal_ip(connect_host)
    bind_ip = _literal_ip(bind_host)
    # Hostname aliases cannot be resolved during offline validation. If both
    # sides are literal IPs, compare their canonical address forms; otherwise
    # retain the explicit service_id/instance_id handshake check for runtime.
    if connect_ip is not None and bind_ip is not None and connect_ip != bind_ip:
        _fail("ENDPOINT_MISMATCH", f"{path}.transport_ref")


def _endpoint_host_port(value: str, path: str) -> tuple[str, int]:
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        host = None
        port = None
    if host is None or port is None:
        _fail("INVALID_PUBLIC_CONFIG", f"{path}.transport_ref")
    return host, port


def _literal_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def _is_wildcard_host(host: str) -> bool:
    if host.casefold() in _WILDCARD_HOSTS:
        return True
    address = _literal_ip(host)
    return address is not None and address.is_unspecified


def _record_writer(
    writers: dict[
        str,
        tuple[
            tuple[str | None, int | None, str, str, str, str, str | None, str | None, str],
            set[str],
        ],
    ],
    request: ClientBindingRequest,
    request_index: int,
) -> None:
    if request.mount not in _WRITER_MOUNTS or request.account_ref is None:
        return
    identity = (
        request.requester_service_id,
        request.client_index,
        request.client_alias,
        request.service_id,
        request.instance_id,
        request.mount,
        request.account_ref,
        request.portfolio_ref,
        request.transport_ref,
    )
    existing = writers.get(request.account_ref)
    if existing is None:
        writers[request.account_ref] = (identity, {request.scope_ref})
        return
    existing_identity, scope_refs = existing
    if existing_identity != identity or request.scope_ref in scope_refs:
        _fail("WRITER_CONFLICT", f"$.bindings[{request_index}]")
    scope_refs.add(request.scope_ref)


def _topological_order(dependencies: Mapping[str, set[str]]) -> tuple[str, ...]:
    # A -> B means A calls B, so B must be ready before A is activated.
    remaining = {service_id: set(targets) for service_id, targets in dependencies.items()}
    dependents: dict[str, set[str]] = defaultdict(set)
    for service_id, targets in remaining.items():
        for target in targets:
            dependents[target].add(service_id)
    ready = [service_id for service_id, targets in remaining.items() if not targets]
    heapq.heapify(ready)
    ordered: list[str] = []
    while ready:
        service_id = heapq.heappop(ready)
        ordered.append(service_id)
        for dependent in sorted(dependents.get(service_id, ())):
            remaining[dependent].discard(service_id)
            if not remaining[dependent]:
                heapq.heappush(ready, dependent)
    if len(ordered) != len(remaining):
        _fail("DEPENDENCY_CYCLE", "$.services")
    return tuple(ordered)


def _freeze_public(value: Any) -> Any:
    if type(value) is dict:
        return MappingProxyType({key: _freeze_public(item) for key, item in value.items()})
    if type(value) is list:
        return tuple(_freeze_public(item) for item in value)
    return value


def _fail(code: str, path: str) -> None:
    raise ServiceConfigError(code, path)


__all__ = [
    "ClientBindingRequest",
    "ComponentActivation",
    "DeploymentPlan",
    "MOUNT_PORTS",
    "OPTIONAL_MOUNT_PORTS",
    "validate_deployment",
]
