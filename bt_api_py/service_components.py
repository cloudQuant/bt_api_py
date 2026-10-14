"""Fixed, lazy factories for the SDK's selected read-service components.

This module contains no domain-package imports at module load time.  A
component package is imported only when its fixed name appears in a parsed
service manifest.  The manifest can select a built-in component; it cannot
name Python modules, factories, handlers, or backend objects.

The current runtime exposes only the implemented read adapters.  Other
manifest ports are either explicitly internal objects supplied by the trusted
resource provider or fail closed before the listener is opened.
"""

from __future__ import annotations

import importlib
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Protocol

from bt_api_base.service_protocol import WireScope

from .service_registry import ServiceCallContext, ServiceCapability


class ServiceComponentError(ValueError):
    """Finite component-construction error without resource or config values."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ResourceOwnership(str, Enum):  # noqa: UP042 -- Python 3.9 compatibility.
    """Whether a trusted provider resource is owned by the host."""

    OWNED = "owned"
    BORROWED = "borrowed"


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
    ) -> ComponentResources:
        """Describe operator-owned objects whose lifetime remains external."""

        return cls(
            values=values,
            ready=ready,
            internal_ports={} if internal_ports is None else internal_ports,
            ownership=ResourceOwnership.BORROWED,
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
    ) -> ComponentResources:
        """Describe host-owned objects and their explicit cleanup callback."""

        return cls(
            values=values,
            ready=ready,
            internal_ports={} if internal_ports is None else internal_ports,
            ownership=ResourceOwnership.OWNED,
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
        object.__setattr__(self, "handlers", MappingProxyType(handlers))
        object.__setattr__(self, "internal_ports", MappingProxyType(internal))


@dataclass(frozen=True, slots=True)
class _Builtin:
    module_name: str
    operations_attribute: tuple[str, ...]
    resource_keys: frozenset[str]
    public_ports: Mapping[str, frozenset[str]]
    internal_ports: frozenset[str]


_BUILTINS: Mapping[str, _Builtin] = MappingProxyType(
    {
        "gateway": _Builtin(
            module_name="bt_api_gateway.service_component",
            operations_attribute=("SUPPORTED_OPERATIONS",),
            resource_keys=frozenset({"journal", "accounts", "principals"}),
            public_ports=MappingProxyType(
                {
                    "service.account_read": frozenset(
                        {"account.read_after", "account.snapshot", "account.head"}
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
        if type(spec) is not ComponentSpec or spec.component not in _BUILTINS:
            raise ServiceComponentError("UNKNOWN_COMPONENT")
        if spec.instance_id in seen_instances:
            raise ServiceComponentError("DUPLICATE_COMPONENT_INSTANCE")
        seen_instances.add(spec.instance_id)
        builtin = _BUILTINS[spec.component]
        try:
            module = importlib.import_module(builtin.module_name)
        except Exception:
            raise ServiceComponentError("BUILTIN_COMPONENT_UNAVAILABLE") from None
        validator = getattr(module, "validate_service_options", None)
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
        for port in spec.bind_ports:
            allowed = builtin.public_ports.get(port)
            if allowed is not None:
                supported = frozenset(allowed.intersection(operations))
                if not supported:
                    raise ServiceComponentError("PUBLIC_PORT_HAS_NO_SUPPORTED_OPERATIONS")
                by_port[port] = supported
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
        if target is None or target.spec.component != "gateway":
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


def build_component(
    prepared: PreparedComponent,
    *,
    scope: WireScope,
    resource_provider: ServiceResourceProvider,
    on_resources: Callable[[ComponentResources], None] | None = None,
) -> ServiceComponent:
    """Resolve trusted resources and construct one fixed read adapter."""

    if type(prepared) is not PreparedComponent or type(scope) is not WireScope:
        raise ServiceComponentError("INVALID_COMPONENT_BUILD")
    if not callable(resource_provider):
        raise ServiceComponentError("RESOURCE_PROVIDER_REQUIRED")
    spec = prepared.spec
    builtin = _BUILTINS[spec.component]
    resources: ComponentResources | None = None
    tracked_by_caller = False
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
        if on_resources is not None:
            # Track before validation or adapter construction can fail.
            on_resources(resources)
            tracked_by_caller = True
        public_ports = frozenset(prepared.operations_by_port)
        expected_keys = builtin.resource_keys if public_ports else frozenset()
        if set(resources.values) != set(expected_keys):
            raise ServiceComponentError("RESOURCE_KEYS_MISMATCH")
        if any(port not in builtin.internal_ports for port in resources.internal_ports):
            raise ServiceComponentError("UNSUPPORTED_INTERNAL_PORT")
        if any(port not in spec.bind_ports for port in resources.internal_ports):
            raise ServiceComponentError("UNDECLARED_INTERNAL_PORT")

        adapter: object | None = None
        if public_ports:
            values = resources.values
            module = prepared.module
            if spec.component == "gateway":
                constructor = getattr(module, "GatewayReadService", None)
                adapter = constructor(
                    values["journal"],
                    accounts=values["accounts"],
                    principals=values["principals"],
                )
            elif spec.component == "monitor":
                constructor = getattr(module, "MonitorReadService", None)
                adapter = constructor(values["outbox"], values["targets"])
            elif spec.component == "risk":
                constructor = getattr(module, "RiskReadService", None)
                adapter = constructor(values["gate"], values["targets"])
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

        capabilities: list[ServiceCapability] = []
        all_operations: set[str] = set()
        for port, operations in prepared.operations_by_port.items():
            if operations.intersection(all_operations):
                raise ServiceComponentError("DUPLICATE_COMPONENT_OPERATION")
            all_operations.update(operations)
            capabilities.append(ServiceCapability(spec.instance_id, port, operations))

        handlers: dict[
            str,
            Callable[[ServiceCallContext, Mapping[str, Any]], Mapping[str, Any]],
        ] = {}
        for operation in all_operations:

            def handler(
                context: ServiceCallContext,
                arguments: Mapping[str, Any],
                *,
                _adapter: object = adapter,
                _operation: str = operation,
            ) -> Mapping[str, Any]:
                assert _adapter is not None
                return _adapter.handle(  # type: ignore[attr-defined]
                    _operation,
                    principal_ref=context.principal_ref,
                    scope=context.scope,
                    arguments=arguments,
                )

            handlers[operation] = handler

        return ServiceComponent(
            component=spec.component,
            instance_id=spec.instance_id,
            capabilities=tuple(capabilities),
            handlers=handlers,
            internal_ports=internal_ports,
            readiness=resources.ready,
            close=resources.close,
        )
    except BaseException:
        if not tracked_by_caller:
            try:
                resources.close()
            except Exception:
                raise ServiceComponentError("RESOURCE_CLEANUP_FAILED") from None
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
    "ComponentResources",
    "ComponentSpec",
    "PreparedComponent",
    "ResourceOwnership",
    "ServiceComponent",
    "ServiceComponentError",
    "ServiceResourceProvider",
    "build_component",
    "component_specs",
    "order_components",
    "validate_components",
]
