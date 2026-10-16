"""AFI11 Monitor process restart preserves the authorized Strategy owner.

This leaf keeps one signed FREEZE pending in the real Monitor ledger, terminates
only the Monitor process created here, and verifies that the already-running
Strategy runtime can still dispatch a risk-valid reduce-only CLOSE through its
original owner and policy. A separate intent missing trusted instrument facts
is rejected without dispatch. After Monitor restarts on the same wire identity
and durable ledger/Risk stores, the recovered freeze is visible in the real Risk
gate and a later OPEN is rejected without another provider dispatch.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib
import inspect
import json
import multiprocessing
import os
import socket
import sys
import time
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import zmq


_BACKEND_ENV = "AFI11_BACKEND_ROOT"
_SDK_ENV = "AFI11_SDK_EXPORT_ROOT"
_RISK_ENV = "AFI11_RISK_EXPORT_ROOT"
_R236_RISK_ENV = "AFI11_R236_RISK_EXPORT_ROOT"
_MONITOR_ENV = "AFI11_MONITOR_PACKAGE_ROOT"
_EXPECTED_PYTHON_ENV = "AFI11_EXPECTED_PYTHON"
_R263_BYTES = 53_949
_R263_SHA256 = "306959bea86723a148d855d4818005e93112df274919e27db1f27b06fd358669"
_AFI08_SOURCE_PINS = {
    "app/config.py": (61_019, "f3a2d39868a53c4c6720c94297ea91eeaaaab4cf6873a77e03efea992186c5fd"),
    "app/api/monitoring.py": (
        22_365,
        "84c25dec1d669d5025305fe9e08c48412ed0ca6b790af720b7ea327e3f9ae543",
    ),
    "app/services/monitoring_control.py": (
        35_973,
        "c47927a19b32ce50db56920eb55be69845e9553101e2458093bed1381d5d5ea3",
    ),
}
# Exact raw bytes in the fixed AFI test export for the application entry point,
# standardized HTTP error envelope, and real client/auth fixtures used here.
_AFI_RUNTIME_SOURCE_PINS = {
    "app/main.py": (
        8_784,
        "1b68af1c2e3326daf45e214aad438a45d97f57b01ea4c128fd1c62372ce455a9",
    ),
    "app/middleware/exception_handling.py": (
        13_000,
        "d6d544fc1c9ac6704c3d8b26175547212978d82be8af5112b2e26364d3881530",
    ),
    "tests/conftest.py": (
        9_506,
        "446e544f67dc9c50898ee687815006f8b568d8aeae63a3619483f77d3682a60c",
    ),
}
# Exact SDK source files from the accepted e6cd356c commit; the live SDK has
# separate Monitor WIP that this fixed process test deliberately excludes.
_SDK_E6CD_COMMITTED_PINS = {
    "service_components.py": (
        107_129,
        "ae9ce9505fbef25d038ca6b319e30f31506dfda21ad14563456d17d802520dbf",
    ),
    "service_monitor_control.py": (
        19_771,
        "7f428465e14a1aaa0fba0afe0e9ed6705979d85ae9a8af0ce9f3fa44aa36a816",
    ),
    "service_registry.py": (
        102_618,
        "abecddd3dc1a826cd7e7d9d8c4560c45f897580840d2ffa3c51346b5802c91b4",
    ),
    "service_runtime.py": (
        78_462,
        "ca8f09f248f773ba92f2df281bc920124ec7bf1f30a92aeaf956d28a3babf309",
    ),
}
_MONITOR_7C407_SOURCE_PINS = {
    "control.py": (
        49_351,
        "15571dcfc5eb873e85989eee5f74ac9802c4a875b80839fb7eec3154a2013e4c",
    ),
    "ingress.py": (
        22_374,
        "38b9065b0fd44e0eb6bd0cb454eb82472e7bffb47e380e45da9d00e814a20e26",
    ),
    "service_component.py": (
        12_286,
        "f21a5256c60e759f24e305a1f78a360df332544bab36551fbd95d7a81a696247",
    ),
    "service_control.py": (
        32_670,
        "f2ef7ab640feb81db7f63c7586699e8d7ac39759fd9f69cfb8aee9fd477f3b81",
    ),
}

_SERVICE_ID = "afi11-monitor-control-service"
_SERVICE_PRINCIPAL = "service:afi11-monitor-control"
_SERVICE_INSTANCE = "afi11-monitor-control-owner"
_SERVICE_ROOT_SCOPE_REF = "afi11-monitor-service-root-v1"
_SERVICE_SCOPE_REF = "afi11-monitor-control-route-v1"
_CLIENT_PRINCIPAL = "application:afi11-monitor-console"
_APPLICATION_REF = "application:afi11-monitor-console"
_MODE = "simulation"
_RECEIPT = hashlib.sha256(b"AFI11 synthetic Monitor authority receipt").hexdigest()


class _StaticKeyResolver:
    def __init__(self, key: Any) -> None:
        self._key = key

    def resolve(self, key_id: str) -> Any:
        return self._key if key_id == self._key.key_id else None


class _StaticPolicyResolver:
    def __init__(self, policy: Any) -> None:
        self._policy = policy

    def resolve(self, issuer: str) -> Any:
        return self._policy if issuer == self._policy.issuer else None


def _required_directory(name: str) -> Path:
    raw = os.environ.get(name)
    assert type(raw) is str and raw.strip(), f"Root must set {name} to a fixed source export"
    path = Path(raw).resolve()
    assert path.is_dir(), f"{name} does not name a source directory"
    return path


def _assert_file_pin(path: Path, expected_bytes: int, expected_sha256: str) -> None:
    raw = path.read_bytes()
    assert len(raw) == expected_bytes, (str(path), "source byte count mismatch")
    assert hashlib.sha256(raw).hexdigest() == expected_sha256, (
        str(path),
        "source SHA-256 mismatch",
    )


def _assert_source_closure() -> tuple[Any, dict[str, Path], Path]:
    expected_python = os.environ.get(_EXPECTED_PYTHON_ENV)
    assert type(expected_python) is str and expected_python.strip()
    assert Path(sys.executable).resolve() == Path(expected_python).resolve()
    assert sys.version_info[:2] == (3, 12)

    backend_root = _required_directory(_BACKEND_ENV)
    sdk_root = _required_directory(_SDK_ENV)
    monitor_root = _required_directory(_MONITOR_ENV)
    assert Path(__file__).resolve().parent == (backend_root / "tests").resolve()

    r263_path = backend_root / "tests" / "test_afi11_analytics_worker_failure.py"
    r263_bytes = r263_path.read_bytes()
    assert len(r263_bytes) == _R263_BYTES
    assert hashlib.sha256(r263_bytes).hexdigest() == _R263_SHA256
    r263 = importlib.import_module("tests.test_afi11_analytics_worker_failure")
    assert Path(r263.__file__).resolve() == r263_path.resolve()
    roots = r263._assert_r236_sources()

    _required_directory(_RISK_ENV)
    _required_directory(_R236_RISK_ENV)

    from app import main as main_module
    from app.api import monitoring as monitoring_routes
    from app.config import get_settings
    from app.services import monitoring_control as control_deployment
    from bt_api_monitor import control, ingress, service_component, service_control
    from bt_api_py import service_monitor_control
    from bt_api_py import service_components, service_config, service_host, service_registry, service_runtime

    app_sources = (
        (main_module, backend_root / "app" / "main.py"),
        (monitoring_routes, backend_root / "app" / "api" / "monitoring.py"),
        (get_settings, backend_root / "app" / "config.py"),
        (control_deployment, backend_root / "app" / "services" / "monitoring_control.py"),
    )
    for symbol_or_module, expected in app_sources:
        actual = (
            getattr(symbol_or_module, "__file__", None)
            if inspect.ismodule(symbol_or_module)
            else inspect.getsourcefile(symbol_or_module)
        )
        assert type(actual) is str and Path(actual).resolve() == expected.resolve(), (
            getattr(symbol_or_module, "__name__", type(symbol_or_module).__name__),
            "AFI source origin mismatch",
        )
    for relative, (size, sha256) in _AFI08_SOURCE_PINS.items():
        _assert_file_pin(backend_root / relative, size, sha256)
    for relative, (size, sha256) in _AFI_RUNTIME_SOURCE_PINS.items():
        _assert_file_pin(backend_root / relative, size, sha256)

    monitor_package = monitor_root.resolve()
    monitor_src = monitor_package / "src" / "bt_api_monitor"
    monitor_sources = (
        (control, monitor_src / "control.py"),
        (ingress, monitor_src / "ingress.py"),
        (service_component, monitor_src / "service_component.py"),
        (service_control, monitor_src / "service_control.py"),
        (service_monitor_control, sdk_root / "root" / "bt_api_py" / "service_monitor_control.py"),
    )
    for module, expected in monitor_sources:
        assert Path(module.__file__).resolve() == expected.resolve(), (
            module.__name__,
            "Monitor/SDK source origin mismatch",
        )
    for relative, (size, sha256) in _MONITOR_7C407_SOURCE_PINS.items():
        _assert_file_pin(monitor_src / relative, size, sha256)
    sdk_package = sdk_root / "root" / "bt_api_py"
    sdk_modules = (
        (service_components, sdk_package / "service_components.py"),
        (service_config, sdk_package / "service_config.py"),
        (service_host, sdk_package / "service_host.py"),
        (service_registry, sdk_package / "service_registry.py"),
        (service_runtime, sdk_package / "service_runtime.py"),
    )
    for module, expected in sdk_modules:
        assert Path(module.__file__).resolve() == expected.resolve(), (
            module.__name__,
            "SDK source origin mismatch",
        )
    for relative, (size, sha256) in _SDK_E6CD_COMMITTED_PINS.items():
        _assert_file_pin(sdk_package / relative, size, sha256)
    assert service_monitor_control.MonitorControlRuntime.PUBLIC_OPERATIONS == frozenset(
        {"monitor.control", "monitor.control_status"}
    )
    assert service_control.MONITOR_CONTROL_PUBLIC_OPERATIONS == frozenset(
        {"monitor.control", "monitor.control_status"}
    )
    return r263, roots, backend_root


def _endpoint() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"tcp://127.0.0.1:{port}"


def _communication(endpoint: str, *, server: bool) -> dict[str, Any]:
    profile = "listener-v1" if server else "client-curve-v1"
    security = "server-curve-v1" if server else "client-curve-v1"
    options: dict[str, int] = {"high_water_mark": 16, "max_message_bytes": 65_536}
    if not server:
        options["request_timeout_ms"] = 5_000
    return {
        "config_schema_version": 1,
        "profiles": {
            profile: {
                "backend": "zmq",
                "config_schema_version": 1,
                "endpoints": {"rpc_bind" if server else "rpc_connect": endpoint},
                "security_profile_ref": security,
                "options": options,
            }
        },
    }


def _service_config(endpoint: str) -> dict[str, Any]:
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": _SERVICE_ID,
        "scope_ref": _SERVICE_ROOT_SCOPE_REF,
        "transport_ref": "listener-v1",
        "registration_policy_ref": "afi11-monitor-registration-v1",
        "service_requirements_ref": "afi11-monitor-requirements-v1",
        "components": {
            "monitor": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": _SERVICE_INSTANCE,
                    "bind_ports": ["service.monitor_control"],
                    "options": {"role": "control"},
                },
            }
        },
        "communication": _communication(endpoint, server=True),
    }


def _client_config(endpoint: str) -> dict[str, Any]:
    return {
        "client_config_schema_version": 1,
        "process_role": "application_client",
        "registration": {"application_ref": _APPLICATION_REF},
        "clients": {
            "monitor": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": [
                        {
                            "mount": "application.monitor",
                            "service_id": _SERVICE_ID,
                            "instance_id": _SERVICE_INSTANCE,
                            "scope_ref": _SERVICE_SCOPE_REF,
                            "transport_ref": "client-curve-v1",
                        }
                    ],
                    "options": {"request_timeout_ms": 5_000},
                },
            }
        },
        "communication": _communication(endpoint, server=False),
    }


def _monitor_process(control_pipe: Any, values: dict[str, Any]) -> None:
    """Spawn target for one test-owned, real Monitor ServiceHost process."""

    from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
    from bt_api_monitor.control import (
        ControlAction,
        ControlCommandBinding,
        ControlIngress,
        ControlStatus,
        DurableControlLedger,
    )
    from bt_api_monitor.ingress import (
        ControlIssuerPolicy,
        ControlScopeGrant,
        HmacControlCommandVerifier,
        HmacControlKey,
    )
    from bt_api_monitor.service_control import MonitorControlService, MonitorControlTarget
    from bt_api_py.service_components import ComponentResources
    from bt_api_py.service_config import parse_service_config
    from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
    from bt_api_py.service_monitor_control import MonitorRiskControlOwner
    from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
    from bt_api_risk import AccountScope, DurableRiskGate

    gate = None
    host = None
    ledger = None
    apply_calls = 0
    try:
        account_id = values["account_id"]
        endpoint = values["endpoint"]
        mode = values["mode"]
        command_scope = values["command_scope"]
        now = values["authority_now"]
        wire_scope = WireScope(values["execution_domain"], account_ref=account_id)
        risk_scope = AccountScope(values["risk_scope"], account_id, mode)
        service_identity = WireIdentity(
            WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID, "service-delegation:afi11-monitor"
        )
        client_identity = WireIdentity(
            WireRole.APPLICATION_CLIENT,
            _CLIENT_PRINCIPAL,
            _APPLICATION_REF,
            values["client_delegate"],
        )
        owner_identity = WireIdentity(
            WireRole.SERVICE,
            values["owner_principal"],
            values["owner_actor"],
            values["owner_delegate"],
        )
        issuer = values["issuer"]
        key_id = values["key_id"]
        secret = values["key_material"]
        receipt = values["receipt_digest"]
        policy = ControlIssuerPolicy(
            issuer=issuer,
            policy_id=values["issuer_policy_id"],
            grants=(
                ControlScopeGrant(
                    account_id=account_id,
                    mode=mode,
                    scope=command_scope,
                    actions=(ControlAction.FREEZE,),
                    receipt_digests=(receipt,),
                    manual_resume_allowed=False,
                ),
            ),
            not_before_utc=now - 60.0,
            expires_at_utc=now + 3_600.0,
        )
        issuer_key = HmacControlKey(
            key_id=key_id,
            issuer=issuer,
            secret=secret,
            not_before_utc=now - 60.0,
            expires_at_utc=now + 3_600.0,
        )
        ledger = DurableControlLedger(values["ledger_path"])
        verifier = HmacControlCommandVerifier(
            _StaticKeyResolver(issuer_key),
            _StaticPolicyResolver(policy),
            max_command_ttl_seconds=30,
        )
        ingress = ControlIngress(ledger, verifier)
        caller_binding = ControlCommandBinding(
            issuer=issuer,
            account_id=account_id,
            mode=mode,
            scope=command_scope,
            actions=frozenset({ControlAction.FREEZE}),
        )
        target = MonitorControlTarget(
            ingress=ingress,
            caller_bindings={client_identity: caller_binding},
            owner_identity=owner_identity,
            executor_id=values["executor_id"],
        )
        service = MonitorControlService({wire_scope: target})
        gate = DurableRiskGate(values["risk_store_path"], values["risk_policy"])
        activate = gate.activate_freeze_once

        def observe_apply(scope: Any, cause_id: str, reason: str) -> Any:
            nonlocal apply_calls
            apply_calls += 1
            return activate(scope, cause_id, reason)

        gate.activate_freeze_once = observe_apply
        owner = MonitorRiskControlOwner(
            service,
            gate,
            risk_scope,
            wire_scope=wire_scope,
            owner_identity=owner_identity,
            executor_id=values["executor_id"],
            account_id=account_id,
            mode=mode,
            command_scope=command_scope,
        )
        if values["hold_owner"]:
            owner.run_next = lambda: None

        resource = ResourceGrant(
            _SERVICE_INSTANCE,
            "service.monitor_control",
            _SERVICE_SCOPE_REF,
            wire_scope,
            frozenset({"monitor.control", "monitor.control_status"}),
        )

        def resource_provider(component: str, instance_id: str, _options: Any, _scope: Any) -> Any:
            if component != "monitor" or instance_id != _SERVICE_INSTANCE:
                raise AssertionError("unexpected Monitor control component")
            return ComponentResources.owned(
                {"service": service, "owners": {wire_scope: owner}},
                ready=lambda: True,
                close=gate.close,
            )

        config = parse_service_config(_service_config(endpoint))
        host = ServiceHost(
            config,
            TrustedServiceAuthority(
                identity=service_identity,
                scope=wire_scope,
                scope_ref=config.scope_ref,
                scope_version="afi11-monitor-scope-v1",
                security_ref="server-curve-v1",
                security_version="afi11-monitor-curve-v1",
                registration_policy_ref=config.registration_policy_ref,
                registration_policy_version="afi11-monitor-registration-v1",
                service_requirements_ref=config.service_requirements_ref,
                service_requirements_version="afi11-monitor-requirements-v1",
                curve_public_key=values["server_public"],
                curve_secret_key=values["server_secret"],
                principals={values["client_public"]: _CLIENT_PRINCIPAL},
                grants={_CLIENT_PRINCIPAL: (PrincipalGrant(client_identity, (resource,)),)},
            ),
            values["registry_path"],
            resource_provider,
        )
        host.start()
        control_pipe.send(
            (
                "ready",
                os.getpid(),
                host.state,
                host.endpoint,
                _SERVICE_ID,
                _SERVICE_INSTANCE,
                _SERVICE_SCOPE_REF,
                owner_identity.principal_ref,
                owner_identity.actor_ref,
                owner_identity.delegate_ref,
                values["executor_id"],
            )
        )
        while True:
            host.serve_once(timeout_ms=20)
            if not control_pipe.poll():
                continue
            command = control_pipe.recv()
            if command[0] == "snapshot":
                row = ledger.get(command[1])
                control_pipe.send(
                    (
                        "snapshot",
                        os.getpid(),
                        host.state,
                        None if row is None else row.command.command_id,
                        None if row is None else row.status.value,
                        None if row is None else row.outcome,
                        None if row is None else row.executor_id,
                        tuple(gate.active_freeze_reasons(risk_scope)),
                        apply_calls,
                    )
                )
                continue
            if command == ("close",):
                host.close()
                control_pipe.send(("closed", os.getpid(), host.state))
                return
            raise AssertionError("unknown Monitor process control command")
    except BaseException as error:
        try:
            control_pipe.send(("error", getattr(error, "code", type(error).__name__)))
        except BaseException:
            pass
        if host is not None and host.state not in {"closed", "failed", "close_failed"}:
            try:
                host.close()
            except BaseException:
                pass
        elif gate is not None:
            try:
                gate.close()
            except BaseException:
                pass
        raise
    finally:
        control_pipe.close()


def _start_monitor(context: Any, values: dict[str, Any]) -> dict[str, Any]:
    parent, child = context.Pipe(duplex=True)
    process = context.Process(
        target=_monitor_process,
        args=(child, values),
        name="afi11-test-owned-monitor-service",
    )
    process.start()
    child.close()
    owner = {"control": parent, "process": process, "pid": process.pid}
    if not parent.poll(20.0):
        _terminate_monitor(owner)
        raise AssertionError("Monitor ServiceHost process did not report readiness")
    ready = parent.recv()
    if not isinstance(ready, tuple) or not ready or ready[0] != "ready":
        _terminate_monitor(owner)
        raise AssertionError(("Monitor ServiceHost process startup failed", ready))
    owner["ready"] = ready
    return owner


def _monitor_command(owner: dict[str, Any], *command: Any) -> tuple[Any, ...]:
    owner["control"].send(command)
    if not owner["control"].poll(10.0):
        raise AssertionError("Monitor process did not answer its bounded control request")
    result = owner["control"].recv()
    assert isinstance(result, tuple) and result and result[0] != "error", result
    return result


def _terminate_monitor(owner: dict[str, Any]) -> None:
    process = owner["process"]
    expected_pid = owner["pid"]
    assert type(expected_pid) is int and process.pid == expected_pid
    if process.is_alive():
        process.terminate()
    process.join(10.0)
    assert not process.is_alive(), "the exact test-owned Monitor PID was not reaped"
    assert type(process.exitcode) is int and process.exitcode != 0
    try:
        owner["control"].close()
    finally:
        process.close()


def _close_monitor(owner: dict[str, Any]) -> None:
    process = owner["process"]
    assert type(owner["pid"]) is int and process.pid == owner["pid"]
    closed = _monitor_command(owner, "close")
    assert closed == ("closed", owner["pid"], "closed"), closed
    process.join(15.0)
    assert not process.is_alive() and process.exitcode == 0
    owner["control"].close()
    process.close()


async def _create_alert(auth_user: Any) -> tuple[str, str, str]:
    user_data, _headers = auth_user
    from sqlalchemy import select

    from app.db.database import async_session_maker
    from app.models.alerts import Alert, AlertSeverity, AlertStatus, AlertType
    from app.models.paper_trading import Account
    from app.models.user import User

    alert_id = f"afi11-monitor-alert-{uuid.uuid4().hex}"
    afi_account_id = f"afi11-monitor-account-{uuid.uuid4().hex}"
    async with async_session_maker() as session:
        user = await session.scalar(select(User).where(User.username == user_data["username"]))
        assert user is not None
        subject = str(user.id)
        session.add(Account(id=afi_account_id, user_id=user.id, name="AFI11 synthetic Monitor control"))
        session.add(
            Alert(
                id=alert_id,
                user_id=user.id,
                account_id=afi_account_id,
                alert_type=AlertType.RISK,
                severity=AlertSeverity.CRITICAL,
                status=AlertStatus.ACTIVE,
                title="Synthetic Monitor process recovery",
                message="A signed FREEZE is recovered by its original owner after restart.",
                trigger_type="manual",
            )
        )
        await session.commit()
    return subject, afi_account_id, alert_id


async def _wait_for_acknowledged(client: Any, command_id: str, headers: dict[str, str]) -> Any:
    deadline = time.monotonic() + 8.0
    latest = None
    while time.monotonic() < deadline:
        latest = await client.get(
            f"/api/v1/monitoring/control/commands/{command_id}", headers=headers
        )
        if latest.status_code == 503:
            await asyncio.sleep(0.05)
            continue
        assert latest.status_code in {200, 202}, latest.text
        if latest.json().get("status") == "acknowledged":
            return latest
        await asyncio.sleep(0.05)
    raise AssertionError(
        "persisted Monitor command did not recover: "
        + (latest.text if latest is not None else "no status response")
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_monitor_process_failure_recovers_same_owner_policy_and_strategy_session(
    client: Any, auth_user: Any, tmp_path: Path
) -> None:
    r263, _source_roots, _backend_root = _assert_source_closure()

    from app import main as main_module
    from app.api import monitoring as monitoring_routes
    from app.db.database import async_session_maker
    from app.services.monitoring_control import OwnerControlDeployment
    from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
    from bt_api_execution import ExecutionScope
    from bt_api_py.service_config import parse_client_config
    from bt_api_py.service_runtime import ClientCurveKeys, ServiceClientRuntime, ZmqTransportFactory
    from bt_api_py.service_registry import ResourceGrant
    from bt_api_risk import (
        AccountScope,
        ExposureAccountingMode,
        INSTRUMENT_METADATA_DIGEST_TAG,
        InstrumentRiskMetadata,
        RiskPolicy,
    )
    from pydantic import SecretStr

    user_data, headers = auth_user
    subject, afi_account_id, alert_id = await _create_alert(auth_user)
    assert main_module.app is not None

    # Construct the exact Strategy process topology accepted by R263.
    multiprocessing.set_executable(sys.executable)
    context = multiprocessing.get_context("spawn")
    strategy_endpoint = _endpoint()
    strategy_server_public, strategy_server_secret = zmq.curve_keypair()
    strategy_client_public, strategy_client_secret = zmq.curve_keypair()
    account_scope = AccountScope("PAPER", r263._OWNER_ACCOUNT, _MODE)
    strategy_wire_scope = WireScope(
        r263._OWNER_DOMAIN,
        account_ref=r263._OWNER_ACCOUNT,
        strategy_ref=r263._OWNER_STRATEGY,
    )
    execution_scope = ExecutionScope(
        "PAPER",
        _MODE,
        r263._OWNER_ACCOUNT,
        r263._OWNER_STRATEGY,
        "afi11-monitor-recovery-run-v1",
    )
    now_ns = time.time_ns()
    metadata = InstrumentRiskMetadata(
        instrument="BTC-USDT",
        metadata_version="afi11-monitor-recovery-instrument-v1",
        as_of_ns=now_ns - 1_000_000_000,
        expires_at_ns=now_ns + 3_600_000_000_000,
        tick_size=Decimal("0.01"),
        quantity_step=Decimal("0.001"),
        contract_multiplier=Decimal("1"),
        max_gross_notional=Decimal("500000"),
        taker_fee_bps=Decimal("0"),
        fixed_fee=Decimal("0"),
        max_slippage_bps=Decimal("0"),
        quantity_unit="BTC",
        valuation_unit="USD",
    )
    service_identity = WireIdentity(
        WireRole.SERVICE, r263._OWNER_SERVICE_PRINCIPAL, r263._OWNER_SERVICE_ID
    )
    strategy_identity = WireIdentity(
        WireRole.STRATEGY_CLIENT,
        r263._OWNER_STRATEGY_PRINCIPAL,
        r263._OWNER_STRATEGY,
    )
    resources = (
        ResourceGrant(
            r263._OWNER_INSTANCE_ID,
            "service.order_entry",
            r263._OWNER_ENTRY_SCOPE_REF,
            strategy_wire_scope,
            frozenset({"orders.submit"}),
        ),
        ResourceGrant(
            r263._OWNER_INSTANCE_ID,
            "service.order_query",
            r263._OWNER_QUERY_SCOPE_REF,
            strategy_wire_scope,
            frozenset({"orders.query"}),
        ),
    )
    strategy_policy = RiskPolicy(
        r263._OWNER_POLICY_ID,
        Decimal("5000"),
        max_increase_count=10,
        permit_ttl_seconds=60.0,
        require_strategy_allocation=True,
        notional_unit="USD",
        exposure_accounting_mode=ExposureAccountingMode.RESERVATIONS_ONLY,
    )
    strategy_material = {
        "account_scope": account_scope,
        "wire_scope": strategy_wire_scope,
        "execution_scope": execution_scope,
        "metadata": metadata,
        "risk_policy": strategy_policy,
        "service_identity": service_identity,
        "grant_rows": ((r263._OWNER_STRATEGY_PRINCIPAL, strategy_identity, resources),),
        "principal_keys": {strategy_client_public: r263._OWNER_STRATEGY_PRINCIPAL},
        "server_public": strategy_server_public,
        "server_secret": strategy_server_secret,
    }
    strategy_parent, strategy_child = context.Pipe(duplex=True)
    strategy_process = context.Process(
        target=r263._owner_process,
        args=(
            strategy_child,
            r263._owner_service_config(strategy_endpoint),
            str(tmp_path / "afi11-monitor-strategy-registry.sqlite3"),
            str(tmp_path / "afi11-monitor-execution-store.sqlite3"),
            str(tmp_path / "afi11-monitor-shared-risk-store.sqlite3"),
            strategy_material,
        ),
        name="afi11-monitor-preserved-strategy-owner",
    )
    strategy_owner = {"control": strategy_parent, "process": strategy_process, "pid": None}

    client_config = parse_client_config(r263._owner_client_config(strategy_endpoint))
    strategy_runtime = ServiceClientRuntime(
        client_config,
        identity=strategy_identity,
        scope_resolver=lambda scope_ref: {
            r263._OWNER_ENTRY_SCOPE_REF: strategy_wire_scope,
            r263._OWNER_QUERY_SCOPE_REF: strategy_wire_scope,
        }[scope_ref],
        peer_resolver=lambda _service_id: service_identity,
        transport_factory=ZmqTransportFactory(
            {
                r263._OWNER_SECURITY_CLIENT: ClientCurveKeys(
                    strategy_server_public, strategy_client_public, strategy_client_secret
                )
            }
        ),
        requested_operations={
            "broker.orders": frozenset({"orders.submit"}),
            "broker.order_query": frozenset({"orders.query"}),
        },
    )

    # Monitor reuses the Strategy owner's policy database and exact account scope.
    monitor_endpoint = _endpoint()
    monitor_server_public, monitor_server_secret = zmq.curve_keypair()
    monitor_client_public, monitor_client_secret = zmq.curve_keypair()
    owner_identity = WireIdentity(
        WireRole.SERVICE,
        "service:afi11-risk-owner",
        f"risk-owner:{r263._OWNER_ACCOUNT}",
        f"monitor-owner-delegation:{r263._OWNER_ACCOUNT}",
    )
    control_identity = WireIdentity(
        WireRole.APPLICATION_CLIENT,
        _CLIENT_PRINCIPAL,
        _APPLICATION_REF,
        f"operator-delegation:{subject}",
    )
    issuer = f"operator:afi11:{subject}"
    key_id = f"afi11-control-key-{uuid.uuid4().hex}"
    key_material = hashlib.sha256(f"afi11 synthetic key:{subject}".encode()).digest()
    authority_now = time.time()
    command_scope = f"account:{r263._OWNER_ACCOUNT}:freeze"
    monitor_values = {
        "account_id": r263._OWNER_ACCOUNT,
        "authority_now": authority_now,
        "client_delegate": control_identity.delegate_ref,
        "client_public": monitor_client_public,
        "command_scope": command_scope,
        "endpoint": monitor_endpoint,
        "execution_domain": r263._OWNER_DOMAIN,
        "executor_id": f"risk-owner:{r263._OWNER_ACCOUNT}",
        "hold_owner": True,
        "issuer": issuer,
        "issuer_policy_id": "afi11-freeze-policy-v1",
        "key_id": key_id,
        "key_material": key_material,
        "ledger_path": str(tmp_path / "afi11-monitor-control-ledger.sqlite3"),
        "mode": _MODE,
        "owner_actor": owner_identity.actor_ref,
        "owner_delegate": owner_identity.delegate_ref,
        "owner_principal": owner_identity.principal_ref,
        "receipt_digest": _RECEIPT,
        "registry_path": str(tmp_path / "afi11-monitor-registry-first.sqlite3"),
        "risk_policy": strategy_policy,
        "risk_scope": "PAPER",
        "risk_store_path": str(tmp_path / "afi11-monitor-shared-risk-store.sqlite3"),
        "server_public": monitor_server_public,
        "server_secret": monitor_server_secret,
    }
    monitor_client_config = _client_config(monitor_endpoint)
    deployment_json = {
        "schema_version": 1,
        "deployment_id": f"afi11-local-{uuid.uuid4().hex}",
        "deployment_version": "afi11-test-v1",
        "not_before_utc": authority_now - 60.0,
        "expires_at_utc": authority_now + 3_600.0,
        "issuer_state_path": str((tmp_path / "afi11-monitor-issuer-state.sqlite3").resolve()),
        "bindings": [
            {
                "subject": subject,
                "afi_account_id": afi_account_id,
                "portfolio_ref": None,
                "account_id": r263._OWNER_ACCOUNT,
                "mode": _MODE,
                "command_scope": command_scope,
                "issuer": issuer,
                "key_ref": key_id,
                "key_material_b64": base64.b64encode(key_material).decode("ascii"),
                "issuer_policy_version": "afi11-freeze-policy-v1",
                "authority_receipt_digest": _RECEIPT,
                "not_before_utc": authority_now - 60.0,
                "expires_at_utc": authority_now + 3_600.0,
                "issuer_sequence_start": 0,
                "allowed_actions": ["FREEZE"],
                "service_id": _SERVICE_ID,
                "instance_id": _SERVICE_INSTANCE,
                "scope_ref": _SERVICE_SCOPE_REF,
                "wire_account_ref": r263._OWNER_ACCOUNT,
                "wire_portfolio_ref": None,
                "wire_scope": {
                    "execution_domain_ref": r263._OWNER_DOMAIN,
                    "account_ref": r263._OWNER_ACCOUNT,
                },
                "client_identity": {
                    "principal_ref": control_identity.principal_ref,
                    "actor_ref": control_identity.actor_ref,
                    "delegate_ref": control_identity.delegate_ref,
                },
                "peer_identity": {
                    "principal_ref": _SERVICE_PRINCIPAL,
                    "actor_ref": _SERVICE_ID,
                    "delegate_ref": "service-delegation:afi11-monitor",
                },
                "client_config": monitor_client_config,
                "curve_keys": {
                    "client-curve-v1": {
                        "server_key_z85": monitor_server_public.decode("ascii"),
                        "public_key_z85": monitor_client_public.decode("ascii"),
                        "secret_key_z85": monitor_client_secret.decode("ascii"),
                    }
                },
            }
        ],
    }
    deployment = OwnerControlDeployment.from_secret(
        SecretStr(json.dumps(deployment_json, sort_keys=True, separators=(",", ":")))
    )
    assert deployment is not None
    monitor_context = multiprocessing.get_context("spawn")
    monitor_owner: dict[str, Any] | None = None
    strategy_started = False
    runtime_started = False
    try:
        strategy_process.start()
        strategy_started = True
        strategy_child.close()
        strategy_owner["pid"] = strategy_process.pid
        ready = r263._owner_receive(strategy_parent)
        assert ready[0] == "ready" and ready[1] == strategy_process.pid, ready
        baseline = r263._owner_command(strategy_owner, "counts")
        assert baseline[2] == "started" and baseline[4] == baseline[9] == 1
        assert baseline[7] == r263._OWNER_POLICY_ID

        registered = strategy_runtime.start()
        runtime_started = True
        assert registered.degraded_count == 0 and len(registered.bindings) == 2
        strategy_session = r263._strategy_facts(strategy_runtime)
        assert r263._owner_command(strategy_owner, "counts")[5] == ()

        monitor_owner = _start_monitor(monitor_context, monitor_values)
        first_ready = monitor_owner["ready"]
        assert first_ready[1] != strategy_process.pid
        assert first_ready[2:7] == (
            "started",
            monitor_endpoint,
            _SERVICE_ID,
            _SERVICE_INSTANCE,
            _SERVICE_SCOPE_REF,
        )
        assert first_ready[7:] == (
            owner_identity.principal_ref,
            owner_identity.actor_ref,
            owner_identity.delegate_ref,
            monitor_values["executor_id"],
        )

        main_module.app.dependency_overrides[
            monitoring_routes.get_owner_control_deployment
        ] = lambda: deployment
        pending = await client.post(
            f"/api/v1/monitoring/control/alerts/{alert_id}",
            headers={**headers, "Idempotency-Key": "afi11-monitor-recovery-freeze-v1"},
            json={"action": "FREEZE", "portfolio_ref": None},
        )
        assert pending.status_code == 202, pending.text
        pending_projection = pending.json()
        command_id = pending_projection["command_id"]
        assert pending_projection["status"] == "pending"
        assert pending_projection["owner_final"] is False
        assert pending_projection["effect_result"] is None
        pending_snapshot = _monitor_command(monitor_owner, "snapshot", command_id)
        assert pending_snapshot[0:7] == (
            "snapshot",
            first_ready[1],
            "started",
            command_id,
            "pending",
            None,
            None,
        )
        assert pending_snapshot[7:] == ((), 0)

        # Stop only the Monitor child created by this test. Strategy's exact
        # owner PID and already-registered runtime must remain available.
        _terminate_monitor(monitor_owner)
        monitor_owner = None
        assert strategy_process.is_alive() and strategy_process.pid == strategy_owner["pid"]
        assert r263._strategy_facts(strategy_runtime) == strategy_session
        assert r263._owner_command(strategy_owner, "counts")[5] == ()

        unavailable = await client.get(
            f"/api/v1/monitoring/control/commands/{command_id}", headers=headers
        )
        assert unavailable.status_code == 503, unavailable.text
        assert unavailable.json()["details"]["code"] == "OWNER_SERVICE_UNAVAILABLE"

        outage_order_id = "afi11-monitor-during-failure-risk-valid-close"
        outage_order_arguments = r263._owner_order_arguments(
            outage_order_id, metadata, metadata.instrument, INSTRUMENT_METADATA_DIGEST_TAG
        )
        outage_order_arguments["side"] = "SELL"
        outage_order_arguments["position_effect"] = "CLOSE"
        outage_order_arguments["reduce_only"] = True
        outage_order = strategy_runtime.call(
            "broker.orders",
            "orders.submit",
            outage_order_arguments,
            scope_ref=r263._OWNER_ENTRY_SCOPE_REF,
        )
        assert outage_order["code"] == "OK"
        outage_record = outage_order["result"]["record"]
        assert outage_record["state"] == "ACKED"
        assert outage_record["dispatch_attempts"] == 1
        assert outage_record["provider_order_id"] == "afi11-synthetic-owner-order-1"

        missing_fact_order_id = "afi11-monitor-during-failure-missing-risk-fact"
        missing_fact_arguments = r263._owner_order_arguments(
            missing_fact_order_id, metadata, metadata.instrument, INSTRUMENT_METADATA_DIGEST_TAG
        )
        missing_fact_arguments["tags"] = {}
        missing_fact_order = strategy_runtime.call(
            "broker.orders",
            "orders.submit",
            missing_fact_arguments,
            scope_ref=r263._OWNER_ENTRY_SCOPE_REF,
        )
        assert missing_fact_order["code"] == "OK"
        missing_fact_record = missing_fact_order["result"]["record"]
        assert missing_fact_record["state"] == "REJECTED"
        assert missing_fact_record["unknown_reason"] == "admission_denied"
        assert missing_fact_record["dispatch_attempts"] == 0
        assert missing_fact_record["provider_order_id"] is None
        during_counts = r263._owner_command(strategy_owner, "counts")
        assert during_counts[1] == strategy_process.pid
        assert during_counts[3] == "afi11-synthetic-owner-session-1"
        assert during_counts[4] == during_counts[9] == 1
        assert during_counts[5] == (outage_order_id,)
        assert during_counts[6] == ("INSTRUMENT_METADATA_DIGEST_REQUIRED",)
        assert during_counts[7:9] == baseline[7:9]
        assert r263._strategy_facts(strategy_runtime) == strategy_session

        # Restart the same service on its fixed endpoint and durable command/Risk stores.
        restarted_values = dict(monitor_values)
        restarted_values["hold_owner"] = False
        restarted_values["registry_path"] = str(
            tmp_path / "afi11-monitor-registry-first.sqlite3"
        )
        monitor_owner = _start_monitor(monitor_context, restarted_values)
        second_ready = monitor_owner["ready"]
        assert second_ready[1] != first_ready[1]
        assert second_ready[2:] == first_ready[2:]
        recovered = await _wait_for_acknowledged(client, command_id, headers)
        assert recovered.status_code == 200, recovered.text
        projection = recovered.json()
        assert projection["command_id"] == command_id
        assert projection["status"] == "acknowledged"
        assert projection["outcome"] == "applied"
        assert projection["owner_final"] is True
        assert projection["reconciliation_required"] is False
        assert projection["effect_result"]["activation_status"] == "CREATED"
        recovered_snapshot = _monitor_command(monitor_owner, "snapshot", command_id)
        assert recovered_snapshot[0:7] == (
            "snapshot",
            second_ready[1],
            "started",
            command_id,
            "acknowledged",
            "applied",
            monitor_values["executor_id"],
        )
        assert set(recovered_snapshot[7]) == {
            "AFI monitoring alert owner freeze",
            f"dispatch-inflight:{outage_order_id}",
        }
        assert len(recovered_snapshot[7]) == 2
        assert recovered_snapshot[8] == 1

        repeat = await client.post(
            f"/api/v1/monitoring/control/alerts/{alert_id}",
            headers={**headers, "Idempotency-Key": "afi11-monitor-recovery-freeze-v1"},
            json={"action": "FREEZE", "portfolio_ref": None},
        )
        assert repeat.status_code == 200, repeat.text
        assert repeat.json() == projection
        issuance = deployment._issuance_row(command_id)
        assert issuance is not None
        assert issuance["owner_final"] == 1
        assert issuance["owner_status"] == "acknowledged"

        # The same Strategy runtime receives a real post-restart rejection. The
        # Monitor receipt and Risk snapshot above independently prove the freeze;
        # the execution reply does not expose a unique cause code.
        frozen_order_id = "afi11-monitor-after-recovery-frozen"
        frozen_order = strategy_runtime.call(
            "broker.orders",
            "orders.submit",
            r263._owner_order_arguments(
                frozen_order_id, metadata, metadata.instrument, INSTRUMENT_METADATA_DIGEST_TAG
            ),
            scope_ref=r263._OWNER_ENTRY_SCOPE_REF,
        )
        assert frozen_order["code"] == "OK"
        frozen_record = frozen_order["result"]["record"]
        assert frozen_record["state"] == "REJECTED"
        assert frozen_record["unknown_reason"] == "admission_denied"
        assert frozen_record["dispatch_attempts"] == 0
        assert frozen_record["provider_order_id"] is None
        after_freeze_counts = r263._owner_command(strategy_owner, "counts")
        assert after_freeze_counts[1] == strategy_process.pid
        assert after_freeze_counts[2] == "started"
        assert after_freeze_counts[3] == "afi11-synthetic-owner-session-1"
        assert after_freeze_counts[4] == after_freeze_counts[9] == 1
        assert after_freeze_counts[5] == (outage_order_id,)
        assert after_freeze_counts[6] == ("INSTRUMENT_METADATA_DIGEST_REQUIRED",)
        assert after_freeze_counts[7:9] == baseline[7:9]
        assert r263._strategy_facts(strategy_runtime) == strategy_session

        _close_monitor(monitor_owner)
        monitor_owner = None

    finally:
        main_module.app.dependency_overrides.pop(
            monitoring_routes.get_owner_control_deployment, None
        )
        if monitor_owner is not None:
            process = monitor_owner["process"]
            if process.is_alive():
                # Teardown may terminate only this exact test-owned child.
                _terminate_monitor(monitor_owner)
            else:
                monitor_owner["control"].close()
                if process.exitcode is not None:
                    process.close()
        if runtime_started:
            strategy_runtime.close()
            assert strategy_runtime.cleanup_failures == 0
            assert strategy_runtime.unregister_uncertainties == 0
        if strategy_started:
            r263._close_owner(strategy_owner)
