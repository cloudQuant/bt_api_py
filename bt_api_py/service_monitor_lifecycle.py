"""Iteration 68.06 Monitor enablement, lifecycle boundary and legacy inventory.

This module is pure configuration and policy. Importing it never imports,
starts, connects, or configures ``bt_api_monitor``; the optional Monitor
package is probed with ``importlib.util.find_spec`` only when a caller asks
for the package state explicitly.

Deployment rules (iteration 68 plan section 2 and section 7):

* an explicit ``monitor.enabled`` requires ``gateway.enabled`` and a selected
  Gateway composition; a contradictory tree is a finite startup rejection and
  is never silently reduced to "not selected";
* ``direct`` keeps the iteration 62 log-only behaviour, so enabling Monitor is
  an explicit finite rejection (D68-07);
* ``offline`` legacy libraries are not migrated or quietly restricted here;
  enabling Monitor is refused with a code that records the pending D68-01
  decision;
* the standalone Monitor service process does not require a Gateway, a page,
  AFI, or SDK runtime supervision for its own lifecycle.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final

from .service_config import ServiceConfig

MONITOR_ENABLEMENT_SCHEMA_VERSION: Final[int] = 1

MONITOR_DEPLOYMENT_GATEWAY: Final[str] = "gateway"
MONITOR_DEPLOYMENT_DIRECT: Final[str] = "direct"
MONITOR_DEPLOYMENT_OFFLINE: Final[str] = "offline"
MONITOR_DEPLOYMENT_MONITOR_SERVICE: Final[str] = "monitor_service"
MONITOR_DEPLOYMENTS: Final[frozenset[str]] = frozenset(
    {
        MONITOR_DEPLOYMENT_GATEWAY,
        MONITOR_DEPLOYMENT_DIRECT,
        MONITOR_DEPLOYMENT_OFFLINE,
        MONITOR_DEPLOYMENT_MONITOR_SERVICE,
    }
)

# Finite codes. They never include configuration values.
MONITOR_REQUIRES_GATEWAY: Final[str] = "MONITOR_REQUIRES_GATEWAY"
MONITOR_GATEWAY_COMPOSITION_NOT_SELECTED: Final[str] = "MONITOR_GATEWAY_COMPOSITION_NOT_SELECTED"
MONITOR_REQUIRED_REQUIRES_MONITOR: Final[str] = "MONITOR_REQUIRED_REQUIRES_MONITOR"
MONITOR_REQUIRED_NOT_APPLICABLE_TO_MONITOR_SERVICE: Final[str] = (
    "MONITOR_REQUIRED_NOT_APPLICABLE_TO_MONITOR_SERVICE"
)
MONITOR_NOT_SUPPORTED_IN_DIRECT: Final[str] = "MONITOR_NOT_SUPPORTED_IN_DIRECT"
MONITOR_ENABLEMENT_LEGACY_UNDECIDED: Final[str] = "MONITOR_ENABLEMENT_LEGACY_UNDECIDED"
MONITOR_SERVICE_DISABLED: Final[str] = "MONITOR_SERVICE_DISABLED"
MONITOR_NOT_INSTALLED: Final[str] = "MONITOR_NOT_INSTALLED"
# Mirrors bt_api_py.service_monitor_control.MONITOR_CONTROL_RETIRED without
# importing that module (which imports bt_api_monitor.service_control).
MONITOR_CONTROL_RETIRED: Final[str] = "MONITOR_CONTROL_RETIRED"
MONITOR_CAPABILITY_UNVERIFIED: Final[str] = "MONITOR_CAPABILITY_UNVERIFIED"
MONITOR_INCOMPATIBLE: Final[str] = "MONITOR_INCOMPATIBLE"

MONITOR_SELECTION_NOT_INSTALLED: Final[str] = "not_installed"
MONITOR_SELECTION_INSTALLED_NOT_SELECTED: Final[str] = "installed_not_selected"
MONITOR_SELECTION_SELECTED: Final[str] = "selected"
MONITOR_SELECTION_DIRECT: Final[str] = "direct"
MONITOR_SELECTION_OFFLINE_LEGACY: Final[str] = "offline_legacy"

_MONITOR_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "enabled",
        "event_limit",
        "event_bytes_limit",
        "telemetry_queue_limit",
        "telemetry_bytes_limit",
        "bind_host",
    }
)
_GATEWAY_FIELDS: Final[frozenset[str]] = frozenset({"enabled", "monitor_required"})
_MONITOR_INT_LIMITS: Final[Mapping[str, tuple[int, int]]] = {
    # These are structural sanity bounds. The authoritative telemetry bounds
    # stay with the 68.02/68.04 component validators.
    "event_limit": (1, 1_000_000),
    "event_bytes_limit": (1, 1 << 30),
    "telemetry_queue_limit": (1, 4096),
    "telemetry_bytes_limit": (1, 4 * 1024 * 1024),
}
_MAX_BIND_HOST_BYTES: Final[int] = 255
_WILDCARD_HOSTS: Final[frozenset[str]] = frozenset({"*", "0.0.0.0", "::"})

# The capability contract 63.01 must authenticate before it can be trusted.
# The operation name is the local copy of the frozen Gateway heartbeat wire;
# tests/unit/test_service_monitor_enablement.py cross-checks it when the
# Gateway package is installed, so it cannot drift silently.
MONITOR_REQUIRED_PROTOCOL_VERSION: Final[int] = 1
MONITOR_REQUIRED_OPERATIONS: Final[frozenset[str]] = frozenset({"monitor.heartbeat"})
_MONITOR_CAPABILITY_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "protocol_version",
        "operations",
        "principal_binding",
        "heartbeat_binding",
    }
)


class MonitorEnablementError(ValueError):
    """A finite enablement or legacy-boundary rejection with no config values."""

    def __init__(self, code: str, path: str = "$") -> None:
        self.code = code
        self.path = path
        super().__init__(f"{code} at {path}")


@dataclass(frozen=True, slots=True)
class MonitorEnablement:
    """Immutable result of the deployment enablement contract.

    ``monitor_required`` is the account-level Gateway setting. It is carried
    here so a contradictory tree is refused at parse time, not acted on by the
    Monitor, which only observes Gateway-published cause state.
    """

    schema_version: int
    deployment: str
    gateway_enabled: bool
    gateway_composition_selected: bool
    monitor_enabled: bool
    monitor_required: bool
    observe_only: bool = True

    def to_public_dict(self) -> dict[str, Any]:
        """Return a detached JSON-compatible projection."""

        return {
            "monitor_enablement_schema_version": self.schema_version,
            "deployment": self.deployment,
            "gateway": {
                "enabled": self.gateway_enabled,
                "composition_selected": self.gateway_composition_selected,
                "monitor_required": self.monitor_required,
            },
            "monitor": {
                "enabled": self.monitor_enabled,
                "observe_only": self.observe_only,
            },
        }


@dataclass(frozen=True, slots=True)
class LegacyMonitorControlRecord:
    """One inventoried legacy Monitor control registration or caller."""

    repository: str
    location: str
    registration: str
    state: str
    boundary: str
    persistent_state: str


LEGACY_MONITOR_CONTROL_STATE_RETIRED: Final[str] = "retired"
LEGACY_MONITOR_CONTROL_STATE_RETAINED: Final[str] = "retained-offline"
LEGACY_MONITOR_CONTROL_STATE_MIGRATION_KEPT: Final[str] = "migration-kept"

LEGACY_MONITOR_CONTROL_INVENTORY: Final[tuple[LegacyMonitorControlRecord, ...]] = (
    LegacyMonitorControlRecord(
        repository="bt_api_py",
        location="bt_api_py/service_config.py _COMPONENT_PORTS['monitor']['control']",
        registration="monitor control role -> service.monitor_control",
        state=LEGACY_MONITOR_CONTROL_STATE_RETIRED,
        boundary=(
            "Still parsed for an explicit finite diagnosis; component validation "
            "raises MONITOR_CONTROL_RETIRED and a Gateway deployment refuses it."
        ),
        persistent_state="No persistent record is read, migrated, or deleted by parsing.",
    ),
    LegacyMonitorControlRecord(
        repository="bt_api_py",
        location="bt_api_py/service_components.py _ROLE_BUILTINS[('monitor', 'control')]",
        registration="MonitorControlRuntime composition guard",
        state=LEGACY_MONITOR_CONTROL_STATE_RETIRED,
        boundary=(
            "D63-35 option b (iteration 64): PUBLIC_OPERATIONS raises "
            "MONITOR_CONTROL_RETIRED before any resource, route, or request."
        ),
        persistent_state="No resource is constructed, so nothing is written or removed.",
    ),
    LegacyMonitorControlRecord(
        repository="bt_api_py",
        location="bt_api_py/service_deployment.py OPTIONAL_MOUNT_PORTS['application.monitor']",
        registration="application.monitor -> optional service.monitor_control",
        state=LEGACY_MONITOR_CONTROL_STATE_RETAINED,
        boundary=(
            "The static port declaration remains so old manifests are diagnosed; "
            "the retired role can never advertise the port, so no route exists."
        ),
        persistent_state="Static declaration only; no persistent state.",
    ),
    LegacyMonitorControlRecord(
        repository="bt_api_py",
        location="bt_api_py/service_monitor_control.py",
        registration="MonitorRiskControlOwner / MonitorControlRuntime",
        state=LEGACY_MONITOR_CONTROL_STATE_RETIRED,
        boundary=(
            "The owner was removed by 64.06 under D63-35 option b; only the "
            "finite refusal guard remains. Monitor control effects now belong "
            "to Gateway operator control, not Monitor."
        ),
        persistent_state="No migration or deletion is performed here.",
    ),
    LegacyMonitorControlRecord(
        repository="bt_api_py",
        location="bt_api_py/service_registry.py strategy_stop_new_receipts",
        registration="legacy STOP_NEW receipt table/columns",
        state=LEGACY_MONITOR_CONTROL_STATE_MIGRATION_KEPT,
        boundary=(
            "64.06 renamed the table to strategy_freeze_receipts with an in-place "
            "migration; the old name is retained only as migration input."
        ),
        persistent_state=(
            "Migration preserves rows. Never drop or rewrite the old table "
            "without an operator decision."
        ),
    ),
    LegacyMonitorControlRecord(
        repository="bt_api_monitor",
        location="src/bt_api_monitor/control.py",
        registration="ControlIngress, DurableControlLedger, ControlCommandEnvelope",
        state=LEGACY_MONITOR_CONTROL_STATE_RETAINED,
        boundary=(
            "Offline primitives remain importable for reading historic local "
            "records; no service role, listener, or Gateway route is wired."
        ),
        persistent_state=(
            "Existing DurableControlLedger SQLite records are retained and are "
            "not migrated, deleted, or replayed by this iteration."
        ),
    ),
    LegacyMonitorControlRecord(
        repository="bt_api_monitor",
        location="src/bt_api_monitor/service_control.py",
        registration="MonitorControlService and control wire vocabulary",
        state=LEGACY_MONITOR_CONTROL_STATE_RETAINED,
        boundary=(
            "Kept so historic records and old manifests can be interpreted with "
            "the shared kill-switch vocabulary; no component selects it."
        ),
        persistent_state="Vocabulary only; no persistent state is created.",
    ),
    LegacyMonitorControlRecord(
        repository="bt_api_monitor",
        location="src/bt_api_monitor/__init__.py control exports",
        registration="explicit lazy exports of the legacy control primitives",
        state=LEGACY_MONITOR_CONTROL_STATE_RETAINED,
        boundary=(
            "Offline tooling can still import the names explicitly; the service "
            "entry never imports or starts them."
        ),
        persistent_state="No export reads or writes persistent state.",
    ),
)


def parse_monitor_enablement(
    raw: object,
    *,
    deployment: str = MONITOR_DEPLOYMENT_GATEWAY,
    gateway_composition_selected: bool | None = None,
) -> MonitorEnablement:
    """Validate the ``gateway``/``monitor`` enablement tree without I/O.

    ``deployment`` selects the deployment contract:
    ``gateway`` (trading deployment), ``direct``, ``offline`` legacy library,
    or ``monitor_service`` (the Monitor's own independent process).
    """

    if type(deployment) is not str or deployment not in MONITOR_DEPLOYMENTS:
        raise MonitorEnablementError("UNKNOWN_DEPLOYMENT", "$.deployment")
    if gateway_composition_selected is not None and (
        type(gateway_composition_selected) is not bool
    ):
        raise MonitorEnablementError("INVALID_TYPE", "$.gateway.composition_selected")
    if type(raw) is not dict:
        raise MonitorEnablementError("INVALID_TYPE", "$")
    _check_keys(raw, {"gateway", "monitor"}, "$")

    gateway_raw = raw.get("gateway", {})
    monitor_raw = raw.get("monitor", {})
    _require_dict(gateway_raw, "$.gateway")
    _require_dict(monitor_raw, "$.monitor")
    _check_keys(gateway_raw, _GATEWAY_FIELDS, "$.gateway")
    _check_keys(monitor_raw, _MONITOR_FIELDS, "$.monitor")

    gateway_enabled = _optional_bool(gateway_raw, "enabled", False, "$.gateway")
    monitor_required = _optional_bool(gateway_raw, "monitor_required", False, "$.gateway")
    monitor_enabled = _optional_bool(monitor_raw, "enabled", False, "$.monitor")
    _validate_monitor_values(monitor_raw)

    _enforce_deployment_rules(
        deployment=deployment,
        gateway_enabled=gateway_enabled,
        monitor_enabled=monitor_enabled,
        monitor_required=monitor_required,
        gateway_composition_selected=gateway_composition_selected,
    )
    return MonitorEnablement(
        schema_version=MONITOR_ENABLEMENT_SCHEMA_VERSION,
        deployment=deployment,
        gateway_enabled=gateway_enabled,
        gateway_composition_selected=(
            gateway_enabled
            if gateway_composition_selected is None
            else gateway_composition_selected
        ),
        monitor_enabled=monitor_enabled,
        monitor_required=monitor_required,
    )


def monitor_enablement_from_service_config(
    config: object,
    *,
    deployment: str = MONITOR_DEPLOYMENT_GATEWAY,
) -> MonitorEnablement:
    """Derive enablement from a parsed service manifest.

    A selected Monitor component is only allowed when the same manifest also
    selects the Gateway composition, unless this is the Monitor's own
    independent service (``deployment="monitor_service"``). The Monitor
    component is never imported or constructed here.
    """

    if type(deployment) is not str or deployment not in MONITOR_DEPLOYMENTS:
        raise MonitorEnablementError("UNKNOWN_DEPLOYMENT", "$.deployment")
    if type(config) is not ServiceConfig:
        raise MonitorEnablementError("INVALID_SERVICE_CONFIG", "$.services")

    components = config.components
    gateway_selected = "gateway" in components
    monitor = components.get("monitor")
    if monitor is None:
        return MonitorEnablement(
            schema_version=MONITOR_ENABLEMENT_SCHEMA_VERSION,
            deployment=deployment,
            gateway_enabled=gateway_selected,
            gateway_composition_selected=gateway_selected,
            monitor_enabled=False,
            monitor_required=False,
        )

    settings = monitor.get("settings")
    options = settings.get("options", {}) if isinstance(settings, Mapping) else {}
    if isinstance(options, Mapping) and options.get("role") == "control":
        raise MonitorEnablementError(
            MONITOR_CONTROL_RETIRED,
            "$.components.monitor.settings.options.role",
        )

    if deployment == MONITOR_DEPLOYMENT_DIRECT:
        raise MonitorEnablementError(MONITOR_NOT_SUPPORTED_IN_DIRECT, "$.components.monitor")
    if deployment == MONITOR_DEPLOYMENT_OFFLINE:
        raise MonitorEnablementError(MONITOR_ENABLEMENT_LEGACY_UNDECIDED, "$.components.monitor")
    if deployment == MONITOR_DEPLOYMENT_GATEWAY and not gateway_selected:
        raise MonitorEnablementError(
            MONITOR_GATEWAY_COMPOSITION_NOT_SELECTED, "$.components.monitor"
        )
    return MonitorEnablement(
        schema_version=MONITOR_ENABLEMENT_SCHEMA_VERSION,
        deployment=deployment,
        gateway_enabled=gateway_selected,
        gateway_composition_selected=gateway_selected,
        monitor_enabled=True,
        monitor_required=False,
    )


def monitor_package_available() -> bool:
    """Return whether the optional Monitor package can be imported, without importing it."""

    try:
        return importlib.util.find_spec("bt_api_monitor") is not None
    except (ImportError, ValueError):
        return False


def monitor_selection_state(
    enablement: MonitorEnablement,
    *,
    package_available: bool,
) -> str:
    """Classify a deployment without importing or starting Monitor.

    ``selected`` is returned only for an explicit enabled Monitor with the
    package available. A selected but missing package is a finite startup
    rejection, never a silent downgrade to "not selected".
    """

    if type(enablement) is not MonitorEnablement:
        raise MonitorEnablementError("INVALID_ENABLEMENT", "$")
    if type(package_available) is not bool:
        raise MonitorEnablementError("INVALID_TYPE", "$.package_available")
    if enablement.deployment == MONITOR_DEPLOYMENT_DIRECT:
        return MONITOR_SELECTION_DIRECT
    if enablement.deployment == MONITOR_DEPLOYMENT_OFFLINE:
        return MONITOR_SELECTION_OFFLINE_LEGACY
    if not enablement.monitor_enabled:
        return (
            MONITOR_SELECTION_INSTALLED_NOT_SELECTED
            if package_available
            else MONITOR_SELECTION_NOT_INSTALLED
        )
    if not package_available:
        raise MonitorEnablementError(MONITOR_NOT_INSTALLED, "$.monitor")
    return MONITOR_SELECTION_SELECTED


def require_no_monitor_control_with_gateway(
    configs: Iterable[ServiceConfig],
) -> None:
    """Reject a legacy Monitor control selection in a Gateway deployment.

    Accepts every service manifest in one deployment. A Gateway deployment is
    identified by any selected ``gateway`` component; a Monitor ``control``
    role anywhere in the same deployment is refused with the finite D63-35
    code. Nothing is deleted or migrated.
    """

    if isinstance(configs, (str, bytes, bytearray)):
        raise MonitorEnablementError("INVALID_SERVICE_LIST", "$.services")
    try:
        rows = tuple(configs)
    except TypeError:
        raise MonitorEnablementError("INVALID_SERVICE_LIST", "$.services") from None
    if any(type(config) is not ServiceConfig for config in rows):
        raise MonitorEnablementError("INVALID_SERVICE_CONFIG", "$.services")
    if not any("gateway" in config.components for config in rows):
        return
    for index, config in enumerate(rows):
        monitor = config.components.get("monitor")
        if monitor is None:
            continue
        settings = monitor.get("settings")
        options = settings.get("options", {}) if isinstance(settings, Mapping) else {}
        if isinstance(options, Mapping) and options.get("role") == "control":
            raise MonitorEnablementError(
                MONITOR_CONTROL_RETIRED,
                f"$.services[{index}].components.monitor.settings.options.role",
            )


def evaluate_required_monitor_capability(advertised: object) -> str | None:
    """Return a finite required-Monitor rejection code, or ``None`` if compatible.

    The argument must be an authenticated capability projection obtained by the
    Gateway (for example from discovery of a concrete registered instance plus
    a valid heartbeat exchange). An unauthenticated self-report is refused as
    ``MONITOR_CAPABILITY_UNVERIFIED``; this helper cannot authenticate anything
    by itself and does not decide Gateway startup.
    """

    if not isinstance(advertised, Mapping) or any(type(key) is not str for key in advertised):
        return MONITOR_CAPABILITY_UNVERIFIED
    if set(advertised) != _MONITOR_CAPABILITY_FIELDS:
        return MONITOR_CAPABILITY_UNVERIFIED
    version = advertised["protocol_version"]
    operations = advertised["operations"]
    principal_binding = advertised["principal_binding"]
    heartbeat_binding = advertised["heartbeat_binding"]
    if (
        type(version) is not int
        or type(principal_binding) is not bool
        or (type(heartbeat_binding) is not bool)
    ):
        return MONITOR_CAPABILITY_UNVERIFIED
    if type(operations) not in (frozenset, set, tuple, list) or any(
        type(operation) is not str for operation in operations
    ):
        return MONITOR_CAPABILITY_UNVERIFIED
    operation_set = set(operations)
    if (
        version != MONITOR_REQUIRED_PROTOCOL_VERSION
        or not MONITOR_REQUIRED_OPERATIONS.issubset(operation_set)
        or principal_binding is not True
        or heartbeat_binding is not True
    ):
        return MONITOR_INCOMPATIBLE
    return None


def _enforce_deployment_rules(
    *,
    deployment: str,
    gateway_enabled: bool,
    monitor_enabled: bool,
    monitor_required: bool,
    gateway_composition_selected: bool | None,
) -> None:
    if deployment == MONITOR_DEPLOYMENT_DIRECT:
        if monitor_enabled or monitor_required:
            raise MonitorEnablementError(MONITOR_NOT_SUPPORTED_IN_DIRECT, "$.monitor")
        return
    if deployment == MONITOR_DEPLOYMENT_OFFLINE:
        if monitor_enabled or monitor_required:
            raise MonitorEnablementError(MONITOR_ENABLEMENT_LEGACY_UNDECIDED, "$.monitor")
        return
    if deployment == MONITOR_DEPLOYMENT_MONITOR_SERVICE:
        if monitor_required:
            raise MonitorEnablementError(
                MONITOR_REQUIRED_NOT_APPLICABLE_TO_MONITOR_SERVICE, "$.gateway"
            )
        if not monitor_enabled:
            raise MonitorEnablementError(MONITOR_SERVICE_DISABLED, "$.monitor")
        return
    if monitor_required and not monitor_enabled:
        raise MonitorEnablementError(MONITOR_REQUIRED_REQUIRES_MONITOR, "$.gateway")
    if monitor_required and not gateway_enabled:
        raise MonitorEnablementError(MONITOR_REQUIRES_GATEWAY, "$.gateway")
    if monitor_enabled and not gateway_enabled:
        raise MonitorEnablementError(MONITOR_REQUIRES_GATEWAY, "$.monitor")
    if monitor_enabled and gateway_composition_selected is False:
        raise MonitorEnablementError(MONITOR_GATEWAY_COMPOSITION_NOT_SELECTED, "$.monitor")


def _validate_monitor_values(raw: Mapping[str, Any]) -> None:
    for key, (lower, upper) in _MONITOR_INT_LIMITS.items():
        if key not in raw:
            continue
        value = raw[key]
        if type(value) is not int:
            raise MonitorEnablementError("INVALID_TYPE", f"$.monitor.{key}")
        if value < lower or value > upper:
            raise MonitorEnablementError("INVALID_VALUE", f"$.monitor.{key}")
    if "bind_host" not in raw:
        return
    value = raw["bind_host"]
    path = "$.monitor.bind_host"
    if type(value) is not str or not value or value != value.strip():
        raise MonitorEnablementError("INVALID_VALUE", path)
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeError:
        raise MonitorEnablementError("INVALID_VALUE", path) from None
    if len(encoded) > _MAX_BIND_HOST_BYTES or any(
        character.isspace() or ord(character) < 0x20 for character in value
    ):
        raise MonitorEnablementError("INVALID_VALUE", path)
    if value.casefold() in _WILDCARD_HOSTS:
        raise MonitorEnablementError("UNSAFE_BIND_HOST", path)


def _check_keys(raw: Mapping[str, Any], allowed: frozenset[str], path: str) -> None:
    if any(type(key) is not str for key in raw):
        raise MonitorEnablementError("INVALID_TYPE", path)
    if any(key not in allowed for key in raw):
        raise MonitorEnablementError("UNKNOWN_FIELD", path)


def _require_dict(value: object, path: str) -> None:
    if type(value) is not dict:
        raise MonitorEnablementError("INVALID_TYPE", path)


def _optional_bool(raw: Mapping[str, Any], key: str, default: bool, path: str) -> bool:
    if key not in raw:
        return default
    value = raw[key]
    if type(value) is not bool:
        raise MonitorEnablementError("INVALID_TYPE", f"{path}.{key}")
    return value


__all__ = [
    "LEGACY_MONITOR_CONTROL_INVENTORY",
    "LEGACY_MONITOR_CONTROL_STATE_MIGRATION_KEPT",
    "LEGACY_MONITOR_CONTROL_STATE_RETAINED",
    "LEGACY_MONITOR_CONTROL_STATE_RETIRED",
    "LegacyMonitorControlRecord",
    "MONITOR_CAPABILITY_UNVERIFIED",
    "MONITOR_CONTROL_RETIRED",
    "MONITOR_DEPLOYMENT_DIRECT",
    "MONITOR_DEPLOYMENT_GATEWAY",
    "MONITOR_DEPLOYMENT_MONITOR_SERVICE",
    "MONITOR_DEPLOYMENT_OFFLINE",
    "MONITOR_DEPLOYMENTS",
    "MONITOR_ENABLEMENT_LEGACY_UNDECIDED",
    "MONITOR_ENABLEMENT_SCHEMA_VERSION",
    "MONITOR_GATEWAY_COMPOSITION_NOT_SELECTED",
    "MONITOR_INCOMPATIBLE",
    "MONITOR_NOT_INSTALLED",
    "MONITOR_NOT_SUPPORTED_IN_DIRECT",
    "MONITOR_REQUIRED_NOT_APPLICABLE_TO_MONITOR_SERVICE",
    "MONITOR_REQUIRED_OPERATIONS",
    "MONITOR_REQUIRED_PROTOCOL_VERSION",
    "MONITOR_REQUIRED_REQUIRES_MONITOR",
    "MONITOR_REQUIRES_GATEWAY",
    "MONITOR_SELECTION_DIRECT",
    "MONITOR_SELECTION_INSTALLED_NOT_SELECTED",
    "MONITOR_SELECTION_NOT_INSTALLED",
    "MONITOR_SELECTION_OFFLINE_LEGACY",
    "MONITOR_SELECTION_SELECTED",
    "MONITOR_SERVICE_DISABLED",
    "MonitorEnablement",
    "MonitorEnablementError",
    "evaluate_required_monitor_capability",
    "monitor_enablement_from_service_config",
    "monitor_package_available",
    "monitor_selection_state",
    "parse_monitor_enablement",
    "require_no_monitor_control_with_gateway",
]
