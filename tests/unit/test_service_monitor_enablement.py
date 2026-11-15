"""Iteration 68.06: Monitor enablement, selection, capability and legacy boundary.

Evidence level: UNIT. No service, listener, provider, Gateway, or account is
started. Isolated-process probes prove that importing the trading chain does
not import, start, or connect the optional Monitor package.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from bt_api_py.service_config import ServiceConfigError, parse_service_config
from bt_api_py.service_monitor_lifecycle import (
    LEGACY_MONITOR_CONTROL_INVENTORY,
    LEGACY_MONITOR_CONTROL_STATE_MIGRATION_KEPT,
    LEGACY_MONITOR_CONTROL_STATE_RETAINED,
    LEGACY_MONITOR_CONTROL_STATE_RETIRED,
    MONITOR_CAPABILITY_UNVERIFIED,
    MONITOR_CONTROL_RETIRED,
    MONITOR_DEPLOYMENT_DIRECT,
    MONITOR_DEPLOYMENT_GATEWAY,
    MONITOR_DEPLOYMENT_MONITOR_SERVICE,
    MONITOR_DEPLOYMENT_OFFLINE,
    MONITOR_ENABLEMENT_LEGACY_UNDECIDED,
    MONITOR_GATEWAY_COMPOSITION_NOT_SELECTED,
    MONITOR_INCOMPATIBLE,
    MONITOR_NOT_INSTALLED,
    MONITOR_NOT_SUPPORTED_IN_DIRECT,
    MONITOR_REQUIRED_NOT_APPLICABLE_TO_MONITOR_SERVICE,
    MONITOR_REQUIRED_REQUIRES_MONITOR,
    MONITOR_REQUIRES_GATEWAY,
    MONITOR_SELECTION_DIRECT,
    MONITOR_SELECTION_INSTALLED_NOT_SELECTED,
    MONITOR_SELECTION_NOT_INSTALLED,
    MONITOR_SELECTION_OFFLINE_LEGACY,
    MONITOR_SELECTION_SELECTED,
    MONITOR_SERVICE_DISABLED,
    MonitorEnablementError,
    evaluate_required_monitor_capability,
    monitor_enablement_from_service_config,
    monitor_selection_state,
    parse_monitor_enablement,
    require_no_monitor_control_with_gateway,
)

_SDK_ROOT = Path(__file__).resolve().parents[2]
_MONITOR_SOURCE = _SDK_ROOT / "bt_api" / "bt_api_monitor" / "src"

_GATEWAY_COMPONENT = {
    "config_schema_version": 2,
    "settings": {
        "instance_id": "gateway-owner",
        "bind_ports": ["service.direct_write"],
        "options": {"role": "trading_owner"},
    },
}
_MONITOR_READ_COMPONENT = {
    "config_schema_version": 2,
    "settings": {
        "instance_id": "monitor-reader",
        "bind_ports": ["service.telemetry_ingress", "service.monitor_query"],
    },
}
_MONITOR_CONTROL_COMPONENT = {
    "config_schema_version": 2,
    "settings": {
        "instance_id": "monitor-controller",
        "bind_ports": ["service.monitor_control"],
        "options": {"role": "control"},
    },
}


def _service_config(components: dict[str, dict], *, port: int = 7612) -> dict:
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": "iter68-enablement-service",
        "scope_ref": "iter68-enablement-root",
        "transport_ref": "listener",
        "registration_policy_ref": "iter68-policy-v1",
        "service_requirements_ref": "iter68-requirements-v1",
        "components": components,
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "listener": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": f"tcp://127.0.0.1:{port}"},
                    "security_profile_ref": "iter68-server-key",
                }
            },
        },
    }


def _error(code: str, raw: dict, **kwargs) -> None:
    with pytest.raises(MonitorEnablementError) as captured:
        parse_monitor_enablement(raw, **kwargs)
    assert captured.value.code == code


def test_disabled_monitor_never_requires_the_package_or_a_gateway() -> None:
    enabled = parse_monitor_enablement(
        {"gateway": {"enabled": True, "monitor_required": False}, "monitor": {"enabled": False}}
    )

    assert enabled.monitor_enabled is False
    assert enabled.gateway_enabled is True
    assert enabled.observe_only is True
    assert (
        monitor_selection_state(enabled, package_available=False) == MONITOR_SELECTION_NOT_INSTALLED
    )
    assert (
        monitor_selection_state(enabled, package_available=True)
        == MONITOR_SELECTION_INSTALLED_NOT_SELECTED
    )
    assert enabled.to_public_dict()["monitor"] == {"enabled": False, "observe_only": True}


def test_enabling_monitor_without_a_gateway_is_a_finite_rejection() -> None:
    _error(MONITOR_REQUIRES_GATEWAY, {"monitor": {"enabled": True}})
    _error(
        MONITOR_REQUIRES_GATEWAY,
        {"gateway": {"enabled": False}, "monitor": {"enabled": True}},
    )
    _error(
        MONITOR_GATEWAY_COMPOSITION_NOT_SELECTED,
        {"gateway": {"enabled": True}, "monitor": {"enabled": True}},
        gateway_composition_selected=False,
    )

    enabled = parse_monitor_enablement(
        {"gateway": {"enabled": True}, "monitor": {"enabled": True}},
        gateway_composition_selected=True,
    )
    assert enabled.monitor_enabled is True
    assert enabled.gateway_composition_selected is True
    assert monitor_selection_state(enabled, package_available=True) == MONITOR_SELECTION_SELECTED


def test_monitor_required_requires_both_an_enabled_monitor_and_gateway() -> None:
    _error(
        MONITOR_REQUIRED_REQUIRES_MONITOR,
        {"gateway": {"enabled": True, "monitor_required": True}},
    )
    _error(
        MONITOR_REQUIRES_GATEWAY,
        {
            "gateway": {"enabled": False, "monitor_required": True},
            "monitor": {"enabled": True},
        },
    )
    enabled = parse_monitor_enablement(
        {
            "gateway": {"enabled": True, "monitor_required": True},
            "monitor": {"enabled": True},
        },
        gateway_composition_selected=True,
    )
    assert enabled.monitor_required is True


def test_direct_deployment_stays_log_only_without_importing_monitor() -> None:
    direct = parse_monitor_enablement({}, deployment=MONITOR_DEPLOYMENT_DIRECT)
    assert direct.monitor_enabled is False
    assert monitor_selection_state(direct, package_available=True) == MONITOR_SELECTION_DIRECT

    for raw in (
        {"monitor": {"enabled": True}},
        {"gateway": {"monitor_required": True}},
    ):
        _error(
            MONITOR_NOT_SUPPORTED_IN_DIRECT,
            raw,
            deployment=MONITOR_DEPLOYMENT_DIRECT,
        )


def test_offline_legacy_enablement_is_explicit_not_silently_removed() -> None:
    offline = parse_monitor_enablement({}, deployment=MONITOR_DEPLOYMENT_OFFLINE)
    assert offline.monitor_enabled is False
    assert (
        monitor_selection_state(offline, package_available=True) == MONITOR_SELECTION_OFFLINE_LEGACY
    )

    _error(
        MONITOR_ENABLEMENT_LEGACY_UNDECIDED,
        {"monitor": {"enabled": True}},
        deployment=MONITOR_DEPLOYMENT_OFFLINE,
    )


def test_the_standalone_monitor_service_does_not_require_a_gateway() -> None:
    service = parse_monitor_enablement(
        {"monitor": {"enabled": True}},
        deployment=MONITOR_DEPLOYMENT_MONITOR_SERVICE,
    )
    assert service.monitor_enabled is True
    assert service.gateway_enabled is False
    assert monitor_selection_state(service, package_available=True) == MONITOR_SELECTION_SELECTED

    _error(
        MONITOR_SERVICE_DISABLED,
        {"monitor": {"enabled": False}},
        deployment=MONITOR_DEPLOYMENT_MONITOR_SERVICE,
    )
    _error(
        MONITOR_REQUIRED_NOT_APPLICABLE_TO_MONITOR_SERVICE,
        {"gateway": {"monitor_required": True}, "monitor": {"enabled": True}},
        deployment=MONITOR_DEPLOYMENT_MONITOR_SERVICE,
    )


def test_unknown_fields_and_unsafe_values_are_finite_errors() -> None:
    _error("UNKNOWN_DEPLOYMENT", {}, deployment="cluster")
    _error("INVALID_TYPE", [], deployment=MONITOR_DEPLOYMENT_GATEWAY)
    _error("UNKNOWN_FIELD", {"mode": "observe"})
    _error("UNKNOWN_FIELD", {"gateway": {"timeout": 1}})
    _error("UNKNOWN_FIELD", {"monitor": {"control": True}})
    _error("INVALID_TYPE", {"monitor": {"enabled": 1}})
    _error("INVALID_VALUE", {"monitor": {"event_limit": 0}})
    _error("INVALID_VALUE", {"monitor": {"event_bytes_limit": -1}})
    _error("INVALID_VALUE", {"monitor": {"telemetry_queue_limit": 4097}})
    _error("UNSAFE_BIND_HOST", {"monitor": {"bind_host": "0.0.0.0"}})
    _error("INVALID_VALUE", {"monitor": {"bind_host": " 127.0.0.1"}})
    _error(
        MONITOR_GATEWAY_COMPOSITION_NOT_SELECTED,
        {"gateway": {"enabled": True}, "monitor": {"enabled": True}},
        gateway_composition_selected=False,
    )
    with pytest.raises(MonitorEnablementError, match="INVALID_TYPE"):
        parse_monitor_enablement({}, gateway_composition_selected="yes")


def test_the_full_semantic_example_parses_and_rejects_no_silent_downgrade() -> None:
    example = {
        "gateway": {"enabled": True, "monitor_required": False},
        "monitor": {
            "enabled": True,
            "event_limit": 10000,
            "event_bytes_limit": 67108864,
            "telemetry_queue_limit": 256,
            "telemetry_bytes_limit": 1048576,
            "bind_host": "127.0.0.1",
        },
    }
    enabled = parse_monitor_enablement(example, gateway_composition_selected=True)
    assert enabled.monitor_enabled is True

    contradictory = dict(example)
    contradictory["gateway"] = {"enabled": False, "monitor_required": False}
    _error(MONITOR_REQUIRES_GATEWAY, contradictory)


def test_selected_but_missing_package_is_a_finite_rejection() -> None:
    enabled = parse_monitor_enablement({"gateway": {"enabled": True}, "monitor": {"enabled": True}})
    with pytest.raises(MonitorEnablementError) as captured:
        monitor_selection_state(enabled, package_available=False)
    assert captured.value.code == MONITOR_NOT_INSTALLED


def test_service_manifest_requires_the_gateway_composition() -> None:
    monitor_only = parse_service_config(_service_config({"monitor": _MONITOR_READ_COMPONENT}))
    with pytest.raises(MonitorEnablementError) as captured:
        monitor_enablement_from_service_config(monitor_only)
    assert captured.value.code == MONITOR_GATEWAY_COMPOSITION_NOT_SELECTED

    standalone = monitor_enablement_from_service_config(
        monitor_only, deployment=MONITOR_DEPLOYMENT_MONITOR_SERVICE
    )
    assert standalone.monitor_enabled is True

    selected = parse_service_config(
        _service_config(
            {
                "gateway": _GATEWAY_COMPONENT,
                "monitor": _MONITOR_READ_COMPONENT,
            }
        )
    )
    enabled = monitor_enablement_from_service_config(selected)
    assert enabled.monitor_enabled is True
    assert enabled.gateway_composition_selected is True

    disabled = parse_service_config(_service_config({"gateway": _GATEWAY_COMPONENT}))
    assert monitor_enablement_from_service_config(disabled).monitor_enabled is False


def test_a_gateway_manifest_refuses_the_legacy_monitor_control_role() -> None:
    with pytest.raises(ServiceConfigError) as captured:
        parse_service_config(
            _service_config(
                {
                    "gateway": _GATEWAY_COMPONENT,
                    "monitor": _MONITOR_CONTROL_COMPONENT,
                }
            )
        )
    assert captured.value.code == MONITOR_CONTROL_RETIRED

    # A standalone legacy manifest still parses for an explicit diagnosis; the
    # enablement helper refuses it without importing or constructing Monitor.
    legacy = parse_service_config(_service_config({"monitor": _MONITOR_CONTROL_COMPONENT}))
    with pytest.raises(MonitorEnablementError) as captured:
        monitor_enablement_from_service_config(
            legacy, deployment=MONITOR_DEPLOYMENT_MONITOR_SERVICE
        )
    assert captured.value.code == MONITOR_CONTROL_RETIRED


def test_cross_service_gateway_deployment_refuses_monitor_control() -> None:
    gateway = parse_service_config(_service_config({"gateway": _GATEWAY_COMPONENT}))
    legacy = parse_service_config(
        _service_config({"monitor": _MONITOR_CONTROL_COMPONENT}, port=7613)
    )
    with pytest.raises(MonitorEnablementError) as captured:
        require_no_monitor_control_with_gateway((gateway, legacy))
    assert captured.value.code == MONITOR_CONTROL_RETIRED

    # Without a Gateway deployment the old manifest remains explicitly present.
    require_no_monitor_control_with_gateway((legacy,))
    assert any(
        record.state == LEGACY_MONITOR_CONTROL_STATE_RETIRED
        for record in LEGACY_MONITOR_CONTROL_INVENTORY
    )


def test_legacy_inventory_is_complete_and_never_claims_a_migration() -> None:
    from bt_api_py.service_monitor_control import (
        MONITOR_CONTROL_RETIRED as CONTROL_MODULE_CODE,
    )

    assert CONTROL_MODULE_CODE == MONITOR_CONTROL_RETIRED
    assert LEGACY_MONITOR_CONTROL_INVENTORY
    allowed_states = {
        LEGACY_MONITOR_CONTROL_STATE_RETIRED,
        LEGACY_MONITOR_CONTROL_STATE_RETAINED,
        LEGACY_MONITOR_CONTROL_STATE_MIGRATION_KEPT,
    }
    locations = {record.location for record in LEGACY_MONITOR_CONTROL_INVENTORY}
    assert any(
        "MonitorRiskControlOwner" in record.registration
        for record in LEGACY_MONITOR_CONTROL_INVENTORY
    )
    assert any(
        "DurableControlLedger" in record.registration for record in LEGACY_MONITOR_CONTROL_INVENTORY
    )
    for record in LEGACY_MONITOR_CONTROL_INVENTORY:
        assert record.repository in {"bt_api_py", "bt_api_monitor"}
        assert record.state in allowed_states
        assert record.location and record.registration
        assert record.boundary and record.persistent_state
    statements = " ".join(
        record.persistent_state.lower() for record in LEGACY_MONITOR_CONTROL_INVENTORY
    )
    assert "retain" in statements
    assert "never drop" in statements
    assert len(locations) == len(LEGACY_MONITOR_CONTROL_INVENTORY)


def test_required_monitor_capability_helper_is_finite_and_untrusting() -> None:
    compatible = {
        "protocol_version": 1,
        "operations": frozenset({"monitor.heartbeat", "monitor.query"}),
        "principal_binding": True,
        "heartbeat_binding": True,
    }
    assert evaluate_required_monitor_capability(compatible) is None
    assert (
        evaluate_required_monitor_capability(
            {**compatible, "operations": frozenset({"monitor.query"})}
        )
        == MONITOR_INCOMPATIBLE
    )
    assert (
        evaluate_required_monitor_capability({**compatible, "protocol_version": 2})
        == MONITOR_INCOMPATIBLE
    )
    assert (
        evaluate_required_monitor_capability({**compatible, "heartbeat_binding": False})
        == MONITOR_INCOMPATIBLE
    )
    for untrusted in (
        None,
        {"protocol_version": 1},
        {**compatible, "source": "self-reported"},
        {**compatible, "principal_binding": 1},
    ):
        assert evaluate_required_monitor_capability(untrusted) == MONITOR_CAPABILITY_UNVERIFIED


def test_monitor_capability_advertisement_matches_the_shared_contract() -> None:
    lifecycle = pytest.importorskip("bt_api_monitor.lifecycle")
    advertisement = dict(lifecycle.MONITOR_SERVICE_CAPABILITY)

    assert set(advertisement) == {
        "protocol_version",
        "operations",
        "principal_binding",
        "heartbeat_binding",
    }
    assert evaluate_required_monitor_capability(advertisement) is None
    assert lifecycle.MONITOR_PROTOCOL_VERSION == 1
    assert "monitor.heartbeat" in advertisement["operations"]
    assert not lifecycle.MONITOR_SERVICE_CONTROL_OPERATIONS
    assert lifecycle.MONITOR_SERVICE_OBSERVE_ONLY is True


def _run_isolated(program: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    return subprocess.run(  # fixed import probe; no provider or shell.
        [sys.executable, "-c", program, str(_SDK_ROOT), str(_MONITOR_SOURCE)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_not_installed_trading_chain_never_imports_monitor(tmp_path: Path) -> None:
    program = r"""
import threading
import sys

sys.dont_write_bytecode = True
sys.path[:0] = [sys.argv[1], sys.argv[2]]

class BlockMonitor:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "bt_api_monitor" or fullname.startswith("bt_api_monitor."):
            raise ImportError("bt_api_monitor is not installed")
        return None

sys.meta_path.insert(0, BlockMonitor())
before_threads = tuple(threading.enumerate())

import bt_api_py.service_components
import bt_api_py.service_config
import bt_api_py.service_deployment
import bt_api_py.gateway_required_monitor
import bt_api_py.service_monitor_lifecycle as lifecycle

assert not any(name.startswith("bt_api_monitor") for name in sys.modules)
disabled = lifecycle.parse_monitor_enablement(
    {"gateway": {"enabled": True}, "monitor": {"enabled": False}}
)
assert lifecycle.monitor_selection_state(
    disabled, package_available=False
) == lifecycle.MONITOR_SELECTION_NOT_INSTALLED
enabled = lifecycle.parse_monitor_enablement(
    {"gateway": {"enabled": True}, "monitor": {"enabled": True}}
)
try:
    lifecycle.monitor_selection_state(enabled, package_available=False)
except lifecycle.MonitorEnablementError as error:
    assert error.code == "MONITOR_NOT_INSTALLED"
else:
    raise AssertionError("a selected but missing Monitor was silently downgraded")
assert tuple(threading.enumerate()) == before_threads
"""
    result = _run_isolated(program, tmp_path)
    assert result.returncode == 0, result.stderr


def test_installed_but_not_selected_never_imports_or_connects(tmp_path: Path) -> None:
    program = r"""
import socket
import threading
import sys

sys.dont_write_bytecode = True
sys.path[:0] = [sys.argv[1], sys.argv[2]]

def forbidden_connect(*args, **kwargs):
    raise AssertionError("the trading chain attempted a Monitor connection")

socket.socket.connect = forbidden_connect
socket.create_connection = forbidden_connect
before_threads = tuple(threading.enumerate())

import bt_api_py.service_components
import bt_api_py.service_config
import bt_api_py.service_deployment
import bt_api_py.gateway_required_monitor
import bt_api_py.service_monitor_lifecycle as lifecycle

assert lifecycle.monitor_package_available() is True
assert not any(name.startswith("bt_api_monitor") for name in sys.modules)
disabled = lifecycle.parse_monitor_enablement(
    {"gateway": {"enabled": True}, "monitor": {"enabled": False}}
)
assert lifecycle.monitor_selection_state(
    disabled, package_available=True
) == lifecycle.MONITOR_SELECTION_INSTALLED_NOT_SELECTED
assert tuple(threading.enumerate()) == before_threads
"""
    result = _run_isolated(program, tmp_path)
    assert result.returncode == 0, result.stderr


def test_direct_deployment_never_imports_or_connects_monitor(tmp_path: Path) -> None:
    program = r"""
import socket
import threading
import sys

sys.dont_write_bytecode = True
sys.path[:0] = [sys.argv[1], sys.argv[2]]

def forbidden_connect(*args, **kwargs):
    raise AssertionError("direct deployment attempted a Monitor connection")

socket.socket.connect = forbidden_connect
socket.create_connection = forbidden_connect
before_threads = tuple(threading.enumerate())

import bt_api_py.service_components
import bt_api_py.service_config
import bt_api_py.service_deployment
import bt_api_py.gateway_required_monitor
import bt_api_py.service_monitor_lifecycle as lifecycle

assert not any(name.startswith("bt_api_monitor") for name in sys.modules)
direct = lifecycle.parse_monitor_enablement({}, deployment="direct")
assert lifecycle.monitor_selection_state(
    direct, package_available=True
) == lifecycle.MONITOR_SELECTION_DIRECT
try:
    lifecycle.parse_monitor_enablement(
        {"monitor": {"enabled": True}}, deployment="direct"
    )
except lifecycle.MonitorEnablementError as error:
    assert error.code == "MONITOR_NOT_SUPPORTED_IN_DIRECT"
else:
    raise AssertionError("direct deployment silently enabled Monitor")
assert not any(name.startswith("bt_api_monitor") for name in sys.modules)
assert tuple(threading.enumerate()) == before_threads
"""
    result = _run_isolated(program, tmp_path)
    assert result.returncode == 0, result.stderr


def test_gateway_deployment_rejects_legacy_monitor_control_in_isolation(
    tmp_path: Path,
) -> None:
    program = r"""
import socket
import threading
import sys

sys.dont_write_bytecode = True
sys.path[:0] = [sys.argv[1], sys.argv[2]]

def forbidden_connect(*args, **kwargs):
    raise AssertionError("legacy control parsing attempted a Monitor connection")

socket.socket.connect = forbidden_connect
socket.create_connection = forbidden_connect
before_threads = tuple(threading.enumerate())

from bt_api_py.service_config import ServiceConfigError, parse_service_config

raw = {
    "service_config_schema_version": 1,
    "process_role": "service",
    "service_id": "gateway-monitor-control",
    "scope_ref": "gateway-monitor-control-root",
    "transport_ref": "listener",
    "registration_policy_ref": "gateway-monitor-control-v1",
    "service_requirements_ref": "gateway-monitor-control-requirements-v1",
    "components": {
        "gateway": {
            "config_schema_version": 2,
            "settings": {
                "instance_id": "gateway-owner",
                "bind_ports": ["service.direct_write"],
                "options": {"role": "trading_owner"},
            },
        },
        "monitor": {
            "config_schema_version": 2,
            "settings": {
                "instance_id": "monitor-controller",
                "bind_ports": ["service.monitor_control"],
                "options": {"role": "control"},
            },
        },
    },
    "communication": {
        "config_schema_version": 1,
        "profiles": {
            "listener": {
                "backend": "zmq",
                "config_schema_version": 1,
                "endpoints": {"rpc_bind": "tcp://127.0.0.1:7612"},
                "security_profile_ref": "gateway-monitor-control-key",
            }
        },
    },
}
try:
    parse_service_config(raw)
except ServiceConfigError as error:
    assert error.code == "MONITOR_CONTROL_RETIRED"
else:
    raise AssertionError("a Gateway deployment accepted legacy Monitor control")
assert not any(name.startswith("bt_api_monitor") for name in sys.modules)
assert tuple(threading.enumerate()) == before_threads
"""
    result = _run_isolated(program, tmp_path)
    assert result.returncode == 0, result.stderr
