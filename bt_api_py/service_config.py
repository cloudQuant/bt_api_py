"""Pure parsing for the Iteration 10 SDK client and service configuration contract.

The parser accepts an already-decoded plain-data tree. It performs no file,
network, plugin, domain-package, or transport I/O. Authorization, secret lookup,
and service-directory resolution remain runtime responsibilities.
"""

from __future__ import annotations

import ipaddress
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from hashlib import sha256
from types import MappingProxyType
from typing import Any, Final
from urllib.parse import urlsplit

_REF_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_VERSIONED_REF_RE: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:-]*-v[1-9][0-9]*$"
)
_MAX_TREE_DEPTH: Final[int] = 48
_MAX_TREE_NODES: Final[int] = 20_000

_STRATEGY_MOUNTS: Final[frozenset[str]] = frozenset(
    {
        "store.market",
        "store.account",
        "broker.orders",
        "broker.order_query",
        "broker.order_groups",
        "risk.read_model",
        "runtime.telemetry",
    }
)
_APPLICATION_MOUNTS: Final[frozenset[str]] = frozenset(
    {
        "application.market",
        "application.account",
        "application.orders",
        "application.order_query",
        "application.order_groups",
        "application.risk_read",
        "application.risk_analysis",
        "application.risk_policy",
        "application.monitor",
        "application.owner_control",
    }
)
_UPSTREAM_MOUNTS: Final[frozenset[str]] = frozenset(
    {
        "upstream.market",
        "upstream.account",
        "upstream.orders",
        "upstream.order_query",
        "upstream.risk_read",
        "upstream.risk_policy",
        "upstream.telemetry",
        "upstream.owner_control",
        "upstream.account_reservations",
    }
)
_MOUNTS_BY_ROLE: Final[dict[str, frozenset[str]]] = {
    "strategy_client": _STRATEGY_MOUNTS,
    "application_client": _APPLICATION_MOUNTS,
    "service": _UPSTREAM_MOUNTS,
}

_ACCOUNT_MOUNTS: Final[frozenset[str]] = frozenset(
    {
        "store.account",
        "broker.orders",
        "broker.order_query",
        "risk.read_model",
        "application.account",
        "application.orders",
        "application.order_query",
        "application.risk_read",
        "upstream.account",
        "upstream.orders",
        "upstream.order_query",
        "upstream.risk_read",
        "upstream.account_reservations",
    }
)
_PORTFOLIO_MOUNTS: Final[frozenset[str]] = frozenset(
    {
        "broker.order_groups",
        "application.order_groups",
        "application.risk_analysis",
    }
)
_OPTIONAL_MOUNTS: Final[frozenset[str]] = frozenset(
    {
        "store.market",
        "store.account",
        "broker.order_query",
        "risk.read_model",
        "runtime.telemetry",
        "application.market",
        "application.account",
        "application.order_query",
        "application.risk_read",
        "upstream.market",
        "upstream.account",
        "upstream.order_query",
        "upstream.risk_read",
        "upstream.telemetry",
    }
)
_WRITER_MOUNTS: Final[frozenset[str]] = frozenset(
    {"broker.orders", "application.orders", "upstream.orders"}
)
_OWNER_CONTROL_MOUNTS: Final[frozenset[str]] = frozenset(
    {"application.owner_control", "upstream.owner_control"}
)
_CLIENT_ALIAS_MOUNTS: Final[dict[str, frozenset[str]]] = {
    "gateway": frozenset(
        {
            "store.market",
            "store.account",
            "application.market",
            "application.account",
            "upstream.market",
            "upstream.account",
            "application.owner_control",
            "upstream.owner_control",
        }
    ),
    "execution": frozenset(
        {
            "broker.orders",
            "broker.order_query",
            "broker.order_groups",
            "application.orders",
            "application.order_query",
            "application.order_groups",
            "upstream.orders",
            "upstream.order_query",
            "application.owner_control",
            "upstream.owner_control",
        }
    ),
    "risk": frozenset(
        {
            "risk.read_model",
            "application.risk_read",
            "application.risk_analysis",
            "application.risk_policy",
            "upstream.risk_read",
            "upstream.risk_policy",
            "upstream.account_reservations",
            "application.owner_control",
            "upstream.owner_control",
        }
    ),
    "monitor": frozenset({"runtime.telemetry", "application.monitor", "upstream.telemetry"}),
}

_COMPONENT_PORTS: Final[dict[str, dict[str | None, frozenset[str]]]] = {
    "gateway": {
        "market_hub": frozenset({"service.market_source"}),
        "account_session_worker": frozenset(
            {"service.account_read", "service.provider_session", "service.owner_control"}
        ),
    },
    "execution": {
        "account_owner": frozenset(
            {
                "service.order_entry",
                "service.order_query",
                "service.owner_control",
            }
        ),
        "group_account_owner": frozenset(
            {
                "service.order_entry",
                "service.order_query",
            }
        ),
        "group_coordinator": frozenset({"service.order_group_entry"}),
    },
    "risk": {
        "account_owner": frozenset({"service.order_entry", "service.order_query"}),
        "group_reservations": frozenset({"service.group_reservations"}),
        "account_admission": frozenset(
            {
                "service.order_admission",
                "service.risk_query",
                "service.group_reservations",
                "service.owner_control",
            }
        ),
        "group_policy": frozenset({"service.group_admission"}),
        "portfolio_analytics": frozenset({"service.risk_analysis"}),
        "policy_catalog": frozenset({"service.risk_policy"}),
    },
    # Legacy Monitor reads remain roleless. Explicit ingress roles bind only
    # their fixed ordinary-telemetry or durable-event capabilities.
    "monitor": {
        None: frozenset(
            {
                "service.telemetry_ingress",
                "service.monitor_query",
            }
        ),
        "durable_ingress": frozenset({"service.telemetry_ingress"}),
        "telemetry_ingress": frozenset({"service.telemetry_ingress", "service.monitor_query"}),
        "control": frozenset({"service.monitor_control"}),
    },
}
_SERVICE_PORTS: Final[frozenset[str]] = frozenset(
    port for roles in _COMPONENT_PORTS.values() for ports in roles.values() for port in ports
).union({"service.provider_session"})

_CLIENT_SETTINGS_FIELDS: Final[frozenset[str]] = frozenset({"bindings", "options"})
_CLIENT_OPTIONS: Final[frozenset[str]] = frozenset(
    {"request_timeout_ms", "heartbeat_interval_seconds"}
)
_MONITOR_CLIENT_OPTIONS: Final[frozenset[str]] = _CLIENT_OPTIONS.union(
    {"telemetry_queue_max_events", "telemetry_queue_max_bytes"}
)
_PROFILE_OPTIONS: Final[frozenset[str]] = frozenset(
    {"request_timeout_ms", "high_water_mark", "max_message_bytes", "market_wire_version"}
)
_ENDPOINTS_BY_SIDE: Final[dict[str, frozenset[str]]] = {
    "client": frozenset({"rpc_connect", "market_connect"}),
    "service": frozenset({"rpc_bind", "market_bind"}),
}


class ServiceConfigError(ValueError):
    """A finite, safe configuration error that never includes input values."""

    def __init__(self, code: str, path: str) -> None:
        self.code = code
        self.path = path
        super().__init__(f"{code} at {path}")


_PARSED_CONFIG_MARKER: Final[object] = object()


class _PublicConfig:
    def _require_parsed(self) -> None:
        if getattr(self, "_parsed_marker", None) is not _PARSED_CONFIG_MARKER:
            raise TypeError("Configuration objects must come from a parse function")

    def to_public_dict(self) -> dict[str, Any]:
        """Return a detached tree containing only JSON-compatible primitives."""
        self._require_parsed()
        return _thaw(self._public)

    def fingerprint(self) -> str:
        """Return SHA-256 over the canonical normalized public representation."""
        canonical = json.dumps(
            self.to_public_dict(),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return sha256(canonical).hexdigest()


@dataclass(frozen=True, slots=True, init=False)
class ClientConfig(_PublicConfig):
    """Immutable parsed client projection; it does not convey runtime authorization."""

    process_role: str | None
    schema_version: int | None
    registration: Mapping[str, str]
    clients: Mapping[str, Any]
    communication: Mapping[str, Any]
    _public: Mapping[str, Any] = field(repr=False, compare=False)
    _parsed_marker: object = field(init=False, repr=False, compare=False)

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        raise TypeError("Use parse_client_config() to construct ClientConfig")


@dataclass(frozen=True, slots=True, init=False)
class ServiceConfig(_PublicConfig):
    """Immutable parsed service projection; it does not convey runtime authorization."""

    service_id: str
    scope_ref: str
    transport_ref: str
    registration_policy_ref: str
    service_requirements_ref: str
    components: Mapping[str, Any]
    clients: Mapping[str, Any]
    communication: Mapping[str, Any]
    _public: Mapping[str, Any] = field(repr=False, compare=False)
    _parsed_marker: object = field(init=False, repr=False, compare=False)

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        raise TypeError("Use parse_service_config() to construct ServiceConfig")


def _new_client_config(
    *,
    process_role: str | None,
    schema_version: int | None,
    registration: Mapping[str, Any],
    clients: Mapping[str, Any],
    communication: Mapping[str, Any],
    public: Mapping[str, Any],
) -> ClientConfig:
    instance = object.__new__(ClientConfig)
    object.__setattr__(instance, "process_role", process_role)
    object.__setattr__(instance, "schema_version", schema_version)
    object.__setattr__(instance, "registration", _freeze(registration))
    object.__setattr__(instance, "clients", _freeze(clients))
    object.__setattr__(instance, "communication", _freeze(communication))
    object.__setattr__(instance, "_public", _freeze(public))
    object.__setattr__(instance, "_parsed_marker", _PARSED_CONFIG_MARKER)
    return instance


def _new_service_config(
    *,
    service_id: str,
    scope_ref: str,
    transport_ref: str,
    registration_policy_ref: str,
    service_requirements_ref: str,
    components: Mapping[str, Any],
    clients: Mapping[str, Any],
    communication: Mapping[str, Any],
    public: Mapping[str, Any],
) -> ServiceConfig:
    instance = object.__new__(ServiceConfig)
    object.__setattr__(instance, "service_id", service_id)
    object.__setattr__(instance, "scope_ref", scope_ref)
    object.__setattr__(instance, "transport_ref", transport_ref)
    object.__setattr__(instance, "registration_policy_ref", registration_policy_ref)
    object.__setattr__(instance, "service_requirements_ref", service_requirements_ref)
    object.__setattr__(instance, "components", _freeze(components))
    object.__setattr__(instance, "clients", _freeze(clients))
    object.__setattr__(instance, "communication", _freeze(communication))
    object.__setattr__(instance, "_public", _freeze(public))
    object.__setattr__(instance, "_parsed_marker", _PARSED_CONFIG_MARKER)
    return instance


def parse_client_config(raw: object) -> ClientConfig:
    """Validate a strategy/application SDK client configuration without I/O."""
    _validate_plain_tree(raw)
    if type(raw) is not dict:
        _fail("INVALID_NODE", "$")
    if raw == {} or raw == {"clients": {}}:
        return _new_client_config(
            process_role=None,
            schema_version=None,
            registration={},
            clients={},
            communication={},
            public=raw,
        )

    top_fields = {
        "config_schema_version",
        "client_config_schema_version",
        "process_role",
        "registration",
        "clients",
        "communication",
    }
    _check_keys(raw, top_fields, "$")
    role = _required_ref(raw, "process_role", "$")
    if role not in {"strategy_client", "application_client"}:
        _fail("UNKNOWN_ROLE", "$.process_role")

    if "config_schema_version" in raw:
        if "client_config_schema_version" in raw:
            _fail("CONFLICTING_VERSION_FIELDS", "$")
        version_field = "config_schema_version"
        expected_version = 6
        if role != "strategy_client":
            _fail("INVALID_ROLE_VERSION", "$.process_role")
    else:
        version_field = "client_config_schema_version"
        expected_version = 1
    version = _required_version(raw, version_field, expected_version, "$")

    clients_raw = raw.get("clients", {})
    _require_dict(clients_raw, "$.clients")
    bindings_by_client: dict[str, dict[str, Any]] = {}
    for index, (client_name, client_raw) in enumerate(clients_raw.items()):
        if client_name not in _CLIENT_ALIAS_MOUNTS:
            _fail("UNKNOWN_CLIENT_ALIAS", "$.clients")
        parsed = _parse_client_block(client_raw, role, client_name, f"$.clients[{index}]")
        bindings_by_client[client_name] = parsed

    registration_raw = raw.get("registration")
    registration: dict[str, str] = {}
    identity_field = "strategy_ref" if role == "strategy_client" else "application_ref"
    if registration_raw is None:
        if bindings_by_client:
            _fail("MISSING_FIELD", "$.registration")
    else:
        _require_dict(registration_raw, "$.registration")
        _check_keys(registration_raw, {identity_field}, "$.registration")
        registration[identity_field] = _required_ref(
            registration_raw, identity_field, "$.registration"
        )

    communication_raw = raw.get("communication")
    if bindings_by_client and communication_raw is None:
        _fail("MISSING_FIELD", "$.communication")
    if communication_raw is None:
        communication: dict[str, Any] = {}
    else:
        communication = _parse_communication(communication_raw, {"client"})

    _validate_bindings_references(bindings_by_client, communication, role)

    public_data: dict[str, Any] = {version_field: version, "process_role": role}
    if registration:
        public_data["registration"] = registration
    if bindings_by_client:
        public_data["clients"] = bindings_by_client
    elif "clients" in raw:
        public_data["clients"] = {}
    if communication:
        public_data["communication"] = communication
    return _new_client_config(
        process_role=role,
        schema_version=version,
        registration=registration,
        clients=bindings_by_client,
        communication=communication,
        public=public_data,
    )


def parse_service_config(raw: object) -> ServiceConfig:
    """Validate a service root and its component/outbound wiring without I/O."""
    _validate_plain_tree(raw)
    _require_dict(raw, "$")
    allowed = {
        "service_config_schema_version",
        "process_role",
        "service_id",
        "scope_ref",
        "transport_ref",
        "registration_policy_ref",
        "service_requirements_ref",
        "components",
        "clients",
        "communication",
    }
    _check_keys(raw, allowed, "$")
    _required_version(raw, "service_config_schema_version", 1, "$")
    if _required_ref(raw, "process_role", "$") != "service":
        _fail("UNKNOWN_ROLE", "$.process_role")

    service_id = _required_ref(raw, "service_id", "$")
    scope_ref = _required_ref(raw, "scope_ref", "$")
    transport_ref = _required_ref(raw, "transport_ref", "$")
    registration_policy_ref = _required_ref(raw, "registration_policy_ref", "$")
    service_requirements_ref = _required_ref(raw, "service_requirements_ref", "$")

    components_raw = raw.get("components")
    _require_dict(components_raw, "$.components")
    if not components_raw:
        _fail("EMPTY_COMPONENTS", "$.components")
    components: dict[str, dict[str, Any]] = {}
    instance_roles: dict[str, tuple[str, str | None]] = {}
    bound_ports: dict[str, str] = {}
    for component_name, component_raw in components_raw.items():
        if component_name not in _COMPONENT_PORTS:
            _fail("UNKNOWN_COMPONENT", "$.components")
        parsed, role = _parse_component(component_name, component_raw)
        instance_id = parsed["settings"]["instance_id"]
        if instance_id in instance_roles:
            _fail("DUPLICATE_INSTANCE", f"$.components.{component_name}.settings.instance_id")
        instance_roles[instance_id] = (component_name, role)
        for port in parsed["settings"]["bind_ports"]:
            if port in bound_ports:
                _fail("PORT_CONFLICT", f"$.components.{component_name}.settings.bind_ports")
            bound_ports[port] = component_name
        components[component_name] = parsed

    _validate_component_references(components, instance_roles)

    clients_raw = raw.get("clients", {})
    _require_dict(clients_raw, "$.clients")
    clients: dict[str, dict[str, Any]] = {}
    for index, (client_name, client_raw) in enumerate(clients_raw.items()):
        if client_name not in _CLIENT_ALIAS_MOUNTS:
            _fail("UNKNOWN_CLIENT_ALIAS", "$.clients")
        clients[client_name] = _parse_client_block(
            client_raw, "service", client_name, f"$.clients[{index}]"
        )

    communication_raw = raw.get("communication")
    communication = _parse_communication(
        communication_raw,
        {"service", "client"},
    )
    _validate_bindings_references(clients, communication, "service")
    binding_index = 0
    for client in clients.values():
        for binding in client["settings"]["bindings"]:
            if binding["service_id"] == service_id:
                _fail("SELF_REFERENCE", f"$.clients.bindings[{binding_index}].service_id")
            binding_index += 1
    profiles = communication["profiles"]
    listener = profiles.get(transport_ref)
    if listener is None or "rpc_bind" not in listener["endpoints"]:
        _fail("UNKNOWN_REFERENCE", "$.transport_ref")
    if (
        any(
            "service.market_source" in component["settings"]["bind_ports"]
            for component in components.values()
        )
        and "market_bind" not in listener["endpoints"]
    ):
        _fail("MISSING_ENDPOINT", "$.communication.profiles")

    public_data: dict[str, Any] = {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": service_id,
        "scope_ref": scope_ref,
        "transport_ref": transport_ref,
        "registration_policy_ref": registration_policy_ref,
        "service_requirements_ref": service_requirements_ref,
        "components": components,
        "communication": communication,
    }
    if clients or "clients" in raw:
        public_data["clients"] = clients
    return _new_service_config(
        service_id=service_id,
        scope_ref=scope_ref,
        transport_ref=transport_ref,
        registration_policy_ref=registration_policy_ref,
        service_requirements_ref=service_requirements_ref,
        components=components,
        clients=clients,
        communication=communication,
        public=public_data,
    )


def _parse_client_block(raw: object, role: str, alias: str, path: str) -> dict[str, Any]:
    _require_dict(raw, path)
    _check_keys(raw, {"config_schema_version", "settings"}, path)
    _required_version(raw, "config_schema_version", 1, path)
    settings = raw.get("settings")
    _require_dict(settings, f"{path}.settings")
    _check_keys(settings, _CLIENT_SETTINGS_FIELDS, f"{path}.settings")

    bindings_raw = settings.get("bindings", [])
    _require_list(bindings_raw, f"{path}.settings.bindings")
    if not bindings_raw:
        _fail("EMPTY_BINDINGS", f"{path}.settings.bindings")
    bindings = [
        _parse_binding(binding, role, f"{path}.settings.bindings[{index}]")
        for index, binding in enumerate(bindings_raw)
    ]
    if any(binding["mount"] not in _CLIENT_ALIAS_MOUNTS[alias] for binding in bindings):
        _fail("CLIENT_ALIAS_MOUNT_MISMATCH", f"{path}.settings.bindings")
    _check_binding_conflicts(bindings, f"{path}.settings.bindings")
    options_raw = settings.get("options", {})
    allowed_options = _MONITOR_CLIENT_OPTIONS if alias == "monitor" else _CLIENT_OPTIONS
    options = _parse_options(options_raw, allowed_options, f"{path}.settings.options")
    normalized_options = dict(options)
    normalized_options.setdefault("request_timeout_ms", 2000)
    normalized_settings: dict[str, Any] = {"bindings": bindings}
    if normalized_options:
        normalized_settings["options"] = normalized_options
    return {"config_schema_version": 1, "settings": normalized_settings}


def _parse_binding(raw: object, role: str, path: str) -> dict[str, Any]:
    _require_dict(raw, path)
    fields = {
        "mount",
        "service_id",
        "instance_id",
        "scope_ref",
        "transport_ref",
        "account_ref",
        "portfolio_ref",
        "required",
    }
    _check_keys(raw, fields, path)
    mount = _required_ref(raw, "mount", path)
    allowed_mounts = _MOUNTS_BY_ROLE[role]
    if mount not in allowed_mounts:
        _fail("UNKNOWN_MOUNT", f"{path}.mount")
    result = {
        "mount": mount,
        "service_id": _required_ref(raw, "service_id", path),
        "instance_id": _required_ref(raw, "instance_id", path),
        "scope_ref": _required_ref(raw, "scope_ref", path),
        "transport_ref": _required_ref(raw, "transport_ref", path),
        "required": _optional_bool(raw, "required", True, path),
    }
    if result["required"] is False and mount not in _OPTIONAL_MOUNTS:
        _fail("REQUIRED_CANNOT_BE_FALSE", f"{path}.required")

    account_required = mount in _ACCOUNT_MOUNTS
    if account_required:
        result["account_ref"] = _required_ref(raw, "account_ref", path)
    elif "account_ref" in raw:
        if mount not in {
            "application.risk_policy",
            "upstream.risk_policy",
            "upstream.owner_control",
        }:
            _fail("FIELD_NOT_ALLOWED", path)
        result["account_ref"] = _required_ref(raw, "account_ref", path)

    portfolio_required = mount in _PORTFOLIO_MOUNTS
    if portfolio_required:
        result["portfolio_ref"] = _required_ref(raw, "portfolio_ref", path)
    elif "portfolio_ref" in raw:
        if mount not in {
            "application.risk_policy",
            "upstream.risk_policy",
            "upstream.owner_control",
        }:
            _fail("FIELD_NOT_ALLOWED", path)
        result["portfolio_ref"] = _required_ref(raw, "portfolio_ref", path)

    if mount in {"application.risk_policy", "upstream.risk_policy"} and (
        ("account_ref" in result) == ("portfolio_ref" in result)
    ):
        _fail("SCOPE_REFERENCE_REQUIRED", path)
    if mount in _OWNER_CONTROL_MOUNTS and "account_ref" in result and "portfolio_ref" in result:
        _fail("SCOPE_REFERENCE_CONFLICT", path)
    return result


def _parse_options(raw: object, allowed: frozenset[str], path: str) -> dict[str, Any]:
    _require_dict(raw, path)
    _check_keys(raw, allowed, path)
    result: dict[str, Any] = {}
    for key, value in raw.items():
        value_path = f"{path}.{key}"
        if key in {
            "request_timeout_ms",
            "high_water_mark",
            "max_message_bytes",
            "market_wire_version",
            "max_concurrent_tasks",
            "max_input_bytes",
            "telemetry_queue_max_events",
            "telemetry_queue_max_bytes",
        }:
            lower, upper = {
                "request_timeout_ms": (1, 120_000),
                "high_water_mark": (1, 100_000),
                "max_message_bytes": (1, 1_048_576),
                "market_wire_version": (1, 2),
                "max_concurrent_tasks": (1, 64),
                "max_input_bytes": (1, 67_108_864),
                "telemetry_queue_max_events": (1, 4096),
                "telemetry_queue_max_bytes": (1, 4 * 1024 * 1024),
            }[key]
            number = _int(value, value_path)
            if number < lower or number > upper:
                _fail("INVALID_VALUE", value_path)
            result[key] = number
        elif key == "heartbeat_interval_seconds":
            number = _int(value, value_path)
            if number < 1 or number > 3600:
                _fail("INVALID_VALUE", value_path)
            result[key] = number
        else:  # The caller's closed field set makes this defensive only.
            _fail("UNKNOWN_OPTION", path)
    return result


def _parse_communication(raw: object, sides: set[str]) -> dict[str, Any]:
    path = "$.communication"
    _require_dict(raw, path)
    _check_keys(raw, {"config_schema_version", "profiles"}, path)
    _required_version(raw, "config_schema_version", 1, path)
    profiles_raw = raw.get("profiles")
    _require_dict(profiles_raw, f"{path}.profiles")
    profiles: dict[str, dict[str, Any]] = {}
    bound_addresses: set[tuple[str, int]] = set()
    for index, (profile_ref, profile_raw) in enumerate(profiles_raw.items()):
        if not _is_ref(profile_ref):
            _fail("INVALID_REFERENCE", f"{path}.profiles")
        profile_path = f"{path}.profiles[{index}]"
        _require_dict(profile_raw, profile_path)
        _check_keys(
            profile_raw,
            {"backend", "config_schema_version", "endpoints", "security_profile_ref", "options"},
            profile_path,
        )
        if profile_raw.get("backend") != "zmq":
            _fail("UNKNOWN_BACKEND", f"{profile_path}.backend")
        _required_version(profile_raw, "config_schema_version", 1, profile_path)
        security_ref = _required_ref(profile_raw, "security_profile_ref", profile_path)
        endpoints_raw = profile_raw.get("endpoints")
        _require_dict(endpoints_raw, f"{profile_path}.endpoints")
        allowed_endpoints = frozenset().union(*_ENDPOINTS_BY_SIDE.values())
        _check_keys(endpoints_raw, allowed_endpoints, f"{profile_path}.endpoints")
        if not endpoints_raw:
            _fail("MISSING_ENDPOINT", f"{profile_path}.endpoints")
        endpoint_sides: set[str] = set()
        endpoints: dict[str, str] = {}
        for endpoint_name, endpoint_raw in endpoints_raw.items():
            endpoint_side = "client" if endpoint_name.endswith("_connect") else "service"
            if endpoint_side not in sides:
                _fail("INVALID_ENDPOINT_SIDE", f"{profile_path}.endpoints")
            endpoint_sides.add(endpoint_side)
            endpoint = _string(endpoint_raw, f"{profile_path}.endpoints")
            _validate_endpoint(endpoint, endpoint_name, f"{profile_path}.endpoints")
            endpoints[endpoint_name] = endpoint
            if endpoint_side == "service":
                bind_key = _bind_endpoint_key(endpoint)
                if bind_key in bound_addresses:
                    _fail("DUPLICATE_BIND_ENDPOINT", f"{profile_path}.endpoints")
                bound_addresses.add(bind_key)
        if len(endpoint_sides) > 1:
            _fail("MIXED_ENDPOINT_SIDES", f"{profile_path}.endpoints")
        options = _parse_options(
            profile_raw.get("options", {}), _PROFILE_OPTIONS, f"{profile_path}.options"
        )
        if "market_wire_version" in options and not (
            "market_connect" in endpoints or "market_bind" in endpoints
        ):
            _fail("MARKET_WIRE_VERSION_REQUIRES_MARKET_ENDPOINT", f"{profile_path}.options")
        normalized_options = {
            "request_timeout_ms": 2000,
            "high_water_mark": 1000,
            "max_message_bytes": 1_048_576,
        }
        normalized_options.update(options)
        profiles[profile_ref] = {
            "backend": "zmq",
            "config_schema_version": 1,
            "endpoints": endpoints,
            "security_profile_ref": security_ref,
            "options": normalized_options,
        }
    return {"config_schema_version": 1, "profiles": profiles}


def _bind_endpoint_key(value: str) -> tuple[str, int]:
    """Canonicalize literal IP bind hosts without DNS or network access."""
    parsed = urlsplit(value)
    host = parsed.hostname
    port = parsed.port
    if host is None or port is None:  # Already rejected by _validate_endpoint.
        raise AssertionError("validated bind endpoint must have a host and port")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        host_key = f"dns:{host.casefold()}"
    else:
        host_key = f"ip{address.version}:{address.compressed.casefold()}"
    return host_key, port


def _validate_bindings_references(
    clients: Mapping[str, Mapping[str, Any]], communication: Mapping[str, Any], role: str
) -> None:
    profiles = communication.get("profiles", {})
    bindings: list[dict[str, Any]] = []
    for client in clients.values():
        bindings.extend(client["settings"]["bindings"])
    _check_binding_conflicts(bindings, "$.clients")
    for index, binding in enumerate(bindings):
        profile = profiles.get(binding["transport_ref"])
        if profile is None:
            _fail("UNKNOWN_REFERENCE", f"$.clients.bindings[{index}].transport_ref")
        endpoints = profile["endpoints"]
        required_endpoints = (
            ("rpc_connect", "market_connect")
            if binding["mount"].endswith(".market")
            else ("rpc_connect",)
        )
        if any(name not in endpoints for name in required_endpoints):
            _fail("MISSING_ENDPOINT", f"$.clients.bindings[{index}].transport_ref")
    if role == "service" and clients and not profiles:
        _fail("MISSING_FIELD", "$.communication.profiles")


def _check_binding_conflicts(bindings: list[dict[str, Any]], path: str) -> None:
    account_slots: dict[tuple[str, str], tuple[str, str, str]] = {}
    portfolio_slots: dict[tuple[str, str], tuple[str, str, str]] = {}
    scope_accounts: dict[tuple[str, str], str] = {}
    exact: set[tuple[str, str, str, str, str, str, str]] = set()
    account_writers: dict[str, tuple[str, str]] = {}
    for index, binding in enumerate(bindings):
        mount = binding["mount"]
        account_ref = binding.get("account_ref", "")
        portfolio_ref = binding.get("portfolio_ref", "")
        scope_ref = binding["scope_ref"]
        identity = (
            mount,
            account_ref,
            portfolio_ref,
            scope_ref,
            binding["service_id"],
            binding["instance_id"],
            binding["transport_ref"],
        )
        if identity in exact:
            _fail("DUPLICATE_BINDING", f"{path}[{index}]")
        exact.add(identity)
        if mount in _ACCOUNT_MOUNTS:
            slot = (mount, account_ref)
            previous = account_slots.get(slot)
            current = (scope_ref, binding["service_id"], binding["instance_id"])
            if previous is not None and previous != current:
                _fail("SCOPE_CONFLICT", f"{path}[{index}]")
            if previous is not None:
                _fail("DUPLICATE_BINDING", f"{path}[{index}]")
            account_slots[slot] = current
            scope_key = (binding["service_id"], scope_ref)
            other_account = scope_accounts.get(scope_key)
            if other_account is not None and other_account != account_ref:
                _fail("SCOPE_CONFLICT", f"{path}[{index}]")
            scope_accounts[scope_key] = account_ref
            if mount in _WRITER_MOUNTS:
                prior_writer = account_writers.get(account_ref)
                current_writer = (mount, binding["service_id"])
                if prior_writer is not None:
                    _fail("WRITER_CONFLICT", f"{path}[{index}]")
                account_writers[account_ref] = current_writer
        if mount in _PORTFOLIO_MOUNTS:
            slot = (mount, portfolio_ref)
            if slot in portfolio_slots:
                _fail("SCOPE_CONFLICT", f"{path}[{index}]")
            portfolio_slots[slot] = (scope_ref, binding["service_id"], binding["instance_id"])


def _parse_component(name: str, raw: object) -> tuple[dict[str, Any], str | None]:
    path = f"$.components.{name}"
    _require_dict(raw, path)
    _check_keys(raw, {"config_schema_version", "settings"}, path)
    _required_version(raw, "config_schema_version", 2, path)
    settings_raw = raw.get("settings")
    _require_dict(settings_raw, f"{path}.settings")
    _check_keys(settings_raw, {"instance_id", "bind_ports", "options"}, f"{path}.settings")
    instance_id = _required_ref(settings_raw, "instance_id", f"{path}.settings")
    ports_raw = settings_raw.get("bind_ports")
    _require_list(ports_raw, f"{path}.settings.bind_ports")
    ports: list[str] = []
    for index, port_raw in enumerate(ports_raw):
        port = _string(port_raw, f"{path}.settings.bind_ports[{index}]")
        if port not in _SERVICE_PORTS:
            _fail("UNKNOWN_PORT", f"{path}.settings.bind_ports")
        if port in ports:
            _fail("DUPLICATE_PORT", f"{path}.settings.bind_ports")
        ports.append(port)
    options_raw = settings_raw.get("options", {})
    _require_dict(options_raw, f"{path}.settings.options")
    role: str | None
    if name == "monitor":
        if "role" not in options_raw:
            _check_keys(options_raw, set(), f"{path}.settings.options")
            role = None
        else:
            role = _required_ref(options_raw, "role", f"{path}.settings.options")
            if role not in _COMPONENT_PORTS[name]:
                _fail("UNKNOWN_COMPONENT_ROLE", f"{path}.settings.options.role")
        allowed_ports = _COMPONENT_PORTS[name][role]
    else:
        role = _required_ref(options_raw, "role", f"{path}.settings.options")
        role_ports = _COMPONENT_PORTS[name]
        if role not in role_ports:
            _fail("UNKNOWN_COMPONENT_ROLE", f"{path}.settings.options.role")
        allowed_ports = role_ports[role]
    if any(port not in allowed_ports for port in ports):
        _fail("PORT_ROLE_MISMATCH", f"{path}.settings.bind_ports")
    if not ports:
        _fail("MISSING_PORT", f"{path}.settings.bind_ports")
    normalized_options = _validate_component_options(name, role, options_raw, path)
    settings = {"instance_id": instance_id, "bind_ports": ports}
    if normalized_options:
        settings["options"] = normalized_options
    return {"config_schema_version": 2, "settings": settings}, role


def _validate_component_options(
    component: str, role: str | None, raw: dict[str, Any], path: str
) -> dict[str, Any]:
    options_path = f"{path}.settings.options"
    if component == "monitor":
        if role is None:
            return {}
        if role == "control":
            _check_keys(raw, {"role"}, options_path)
            return {"role": role}
        if role == "telemetry_ingress":
            defaults = {
                "max_streams": 256,
                "max_bytes": 1024 * 1024,
                "heartbeat_timeout_seconds": 30,
                "retention_seconds": 300,
            }
            _check_keys(raw, {"role", *defaults}, options_path)
            result = {"role": role, **defaults}
            for key in defaults:
                if key in raw:
                    value = _int(raw[key], f"{options_path}.{key}")
                    if value <= 0:
                        _fail("INVALID_VALUE", f"{options_path}.{key}")
                    result[key] = value
            return result
        fields = {"role", "max_events", "max_bytes", "max_event_bytes"}
        _check_keys(raw, fields, options_path)
        result = {
            "role": role,
            "max_events": 10_000,
            "max_bytes": 64 * 1024 * 1024,
            "max_event_bytes": 64 * 1024,
        }
        for key in ("max_events", "max_bytes", "max_event_bytes"):
            if key not in raw:
                continue
            value = _int(raw[key], f"{options_path}.{key}")
            if value <= 0:
                _fail("INVALID_VALUE", f"{options_path}.{key}")
            result[key] = value
        return result
    if component == "gateway":
        fields = {"role", "account_session_ref"} if role == "account_session_worker" else {"role"}
        _check_keys(raw, fields, options_path)
        result = {"role": role}
        if role == "account_session_worker":
            result["account_session_ref"] = _required_ref(raw, "account_session_ref", options_path)
        return result
    if component == "execution":
        if role == "account_owner":
            fields = {"role", "provider_session_ref"}
            _check_keys(raw, fields, options_path)
            result = {"role": role}
            if "provider_session_ref" in raw:
                result["provider_session_ref"] = _required_ref(
                    raw, "provider_session_ref", options_path
                )
            return result
        if role == "group_account_owner":
            _check_keys(raw, {"role"}, options_path)
            return {"role": role}
        fields = {"role", "portfolio_ref"}
        _check_keys(raw, fields, options_path)
        return {
            "role": role,
            "portfolio_ref": _required_ref(raw, "portfolio_ref", options_path),
        }
    if component == "risk":
        if role in {"account_admission", "account_owner"}:
            _check_keys(raw, {"role", "account_policy_ref"}, options_path)
            return {
                "role": role,
                "account_policy_ref": _versioned_ref(raw, "account_policy_ref", options_path),
            }
        if role == "group_reservations":
            _check_keys(raw, {"role"}, options_path)
            return {"role": role}
        if role == "group_policy":
            _check_keys(raw, {"role", "group_policy_ref"}, options_path)
            return {
                "role": role,
                "group_policy_ref": _versioned_ref(raw, "group_policy_ref", options_path),
            }
        if role == "portfolio_analytics":
            fields = {
                "role",
                "model_profile_ref",
                "allowed_source_modes",
                "max_concurrent_tasks",
                "max_input_bytes",
            }
            _check_keys(raw, fields, options_path)
            result = {
                "role": role,
                "model_profile_ref": _versioned_ref(raw, "model_profile_ref", options_path),
                "allowed_source_modes": _source_modes(raw, options_path),
            }
            quota: dict[str, int] = {}
            for key in ("max_concurrent_tasks", "max_input_bytes"):
                if key in raw:
                    quota[key] = _bounded_int_option(raw[key], key, options_path)
            result.update(quota)
            return result
        _check_keys(raw, {"role"}, options_path)
        return {"role": role}
    raise AssertionError("component registry and validator are out of sync")


def _bounded_int_option(raw: object, key: str, path: str) -> int:
    lower, upper = {
        "max_concurrent_tasks": (1, 64),
        "max_input_bytes": (1, 67_108_864),
    }[key]
    value = _int(raw, f"{path}.{key}")
    if value < lower or value > upper:
        _fail("INVALID_VALUE", f"{path}.{key}")
    return value


def _validate_component_references(
    components: Mapping[str, Mapping[str, Any]],
    instance_roles: Mapping[str, tuple[str, str | None]],
) -> None:
    execution = components.get("execution")
    if execution is None:
        return
    options = execution["settings"].get("options", {})
    provider_ref = options.get("provider_session_ref")
    if provider_ref is None:
        return
    gateway_instances = {
        instance_id: role
        for instance_id, (component, role) in instance_roles.items()
        if component == "gateway"
    }
    if not gateway_instances or provider_ref not in gateway_instances:
        _fail("UNKNOWN_REFERENCE", "$.components.execution.settings.options.provider_session_ref")
    if gateway_instances[provider_ref] != "account_session_worker":
        _fail(
            "REFERENCE_TYPE_MISMATCH",
            "$.components.execution.settings.options.provider_session_ref",
        )


def _source_modes(raw: dict[str, Any], path: str) -> list[str]:
    value = raw.get("allowed_source_modes")
    _require_list(value, f"{path}.allowed_source_modes")
    allowed = {"research", "imported", "paper"}
    if not value:
        _fail("INVALID_VALUE", f"{path}.allowed_source_modes")
    modes: list[str] = []
    for index, item in enumerate(value):
        mode = _string(item, f"{path}.allowed_source_modes[{index}]")
        if mode not in allowed:
            _fail("INVALID_VALUE", f"{path}.allowed_source_modes[{index}]")
        if mode in modes:
            _fail("DUPLICATE_VALUE", f"{path}.allowed_source_modes[{index}]")
        modes.append(mode)
    return modes


def _versioned_ref(raw: dict[str, Any], key: str, path: str) -> str:
    value = _required_ref(raw, key, path)
    if _VERSIONED_REF_RE.fullmatch(value) is None:
        _fail("VERSIONED_REFERENCE_REQUIRED", f"{path}.{key}")
    return value


def _validate_endpoint(value: str, name: str, path: str) -> None:
    if len(value) > 512 or any(char.isspace() or ord(char) < 0x20 for char in value):
        _fail("INVALID_ENDPOINT", path)
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        _fail("INVALID_ENDPOINT", path)
    if (
        parsed.scheme != "tcp"
        or not parsed.hostname
        or port is None
        or not 1 <= port <= 65_535
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        _fail("INVALID_ENDPOINT", path)
    is_bind = name.endswith("_bind")
    if not is_bind and parsed.hostname in {"*", "0.0.0.0", "::"}:
        _fail("INVALID_ENDPOINT", path)


def _validate_plain_tree(value: object) -> None:
    visited: set[int] = set()
    node_count = 0

    def visit(node: object, depth: int) -> None:
        nonlocal node_count
        node_count += 1
        if node_count > _MAX_TREE_NODES or depth > _MAX_TREE_DEPTH:
            _fail("INVALID_NODE", "$")
        if node is None or type(node) in {str, bool, int}:
            return
        if type(node) is float:
            if not math.isfinite(node):
                _fail("INVALID_NODE", "$")
            return
        if type(node) not in {dict, list}:
            _fail("INVALID_NODE", "$")
        identity = id(node)
        if identity in visited:
            _fail("INVALID_NODE", "$")
        visited.add(identity)
        if type(node) is dict:
            for key, item in node.items():
                if type(key) is not str:
                    _fail("INVALID_NODE", "$")
                visit(item, depth + 1)
        else:
            for item in node:
                visit(item, depth + 1)

    visit(value, 0)


def _check_keys(raw: dict[str, Any], allowed: set[str] | frozenset[str], path: str) -> None:
    if any(type(key) is not str for key in raw):
        _fail("INVALID_NODE", path)
    if any(key not in allowed for key in raw):
        _fail("UNKNOWN_FIELD", path)


def _require_dict(value: object, path: str) -> None:
    if type(value) is not dict:
        _fail("INVALID_TYPE", path)


def _require_list(value: object, path: str) -> None:
    if type(value) is not list:
        _fail("INVALID_TYPE", path)


def _required_version(raw: dict[str, Any], key: str, expected: int, path: str) -> int:
    if key not in raw:
        _fail("MISSING_FIELD", f"{path}.{key}")
    value = raw[key]
    if type(value) is not int:
        _fail("INVALID_VERSION", f"{path}.{key}")
    if value != expected:
        _fail("UNSUPPORTED_VERSION", f"{path}.{key}")
    return value


def _required_ref(raw: dict[str, Any], key: str, path: str) -> str:
    if key not in raw:
        _fail("MISSING_FIELD", f"{path}.{key}")
    return _string(raw[key], f"{path}.{key}", reference=True)


def _is_ref(value: object) -> bool:
    return type(value) is str and _REF_RE.fullmatch(value) is not None


def _string(value: object, path: str, *, reference: bool = False) -> str:
    if type(value) is not str:
        _fail("INVALID_TYPE", path)
    if not value or value != value.strip() or "\x00" in value:
        _fail("INVALID_VALUE", path)
    if reference and not _is_ref(value):
        _fail("INVALID_REFERENCE", path)
    return value


def _optional_bool(raw: dict[str, Any], key: str, default: bool, path: str) -> bool:
    if key not in raw:
        return default
    value = raw[key]
    if type(value) is not bool:
        _fail("INVALID_TYPE", f"{path}.{key}")
    return value


def _int(value: object, path: str) -> int:
    if type(value) is not int:
        _fail("INVALID_TYPE", path)
    return value


def _freeze(value: Any) -> Any:
    if type(value) is dict:
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if type(value) is list:
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if type(value) is tuple:
        return [_thaw(item) for item in value]
    return value


def _fail(code: str, path: str) -> None:
    raise ServiceConfigError(code, path)


__all__ = [
    "ClientConfig",
    "ServiceConfig",
    "ServiceConfigError",
    "parse_client_config",
    "parse_service_config",
]
