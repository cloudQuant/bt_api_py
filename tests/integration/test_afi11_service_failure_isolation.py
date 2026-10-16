"""AFI11 app-process failure isolation over a real SDK owner process.

This is one deliberately narrow AFI11 slice.  The AFI HTTP application and
the Strategy client are separate processes/sessions, while the Execution
ServiceHost owns the real DurableRiskGate and the only synthetic provider
leaf.  Monitor and analysis-worker fault slices remain separate work.

The test requires Root to supply fixed exports through AFI11_BACKEND_ROOT,
AFI11_SDK_EXPORT_ROOT, AFI11_RISK_EXPORT_ROOT, and AFI11_MONITOR_EXPORT_ROOT.
It does not fall back to dirty checkouts or in-place SDK package sources.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import inspect
import json
import multiprocessing
import os
import sys
import time
import tomllib
import uuid
from contextlib import contextmanager, suppress
from decimal import Decimal
from importlib import metadata as importlib_metadata
from pathlib import Path
from types import ModuleType
from typing import Any, Iterator

import pytest
import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution import ExecutionScope, ProviderObservation, SqliteExecutionStore
from bt_api_execution.service_order_entry import ExecutionOrderEntryService
from bt_api_risk import (
    INSTRUMENT_METADATA_DIGEST_TAG,
    AccountScope,
    DurableRiskAdmissionAdapter,
    DurableRiskGate,
    ExposureAccountingMode,
    InstrumentRiskAdmissionMapper,
    InstrumentRiskMetadata,
    InstrumentRiskRegistry,
    RiskDeniedError,
    RiskPolicy,
)
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
)
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ZmqTransportFactory,
)


_AFI_BACKEND_COMMIT = "5be2e26c9cbb558187ed4ba7cce622989cb50c0e"
_AFI_HTTP_TEST_SHA256 = "957e6ee952a91357f01916ce61370ea74dc1ce3f8df25da17ee798b4f7d997c9"
_AFI_ORDER_TEST_SHA256 = "14f6c01229486f3da1ce002b60618daf56afb1456b47779b87dbbf43f5ea8530"
_EXPECTED_PYTHON_ENV = "AFI11_EXPECTED_PYTHON"
_SDK_SOURCE_ROOT_ENV = "AFI11_SDK_EXPORT_ROOT"
_RISK_SOURCE_ROOT_ENV = "AFI11_RISK_EXPORT_ROOT"
_MONITOR_SOURCE_ROOT_ENV = "AFI11_MONITOR_EXPORT_ROOT"
_MONITOR_REVISION_ENV = "AFI11_MONITOR_REVISION"
_AFI_SOURCE_ROOT_ENV = "AFI11_BACKEND_ROOT"
_MONITOR_SOURCE_COMMIT = "24516efc42f0696389592ecd36436c66c28ba270"
_MONITOR_EXPORT_MANIFEST_SHA256 = "351c63cf43513f49016cf35ba00a76dc147f2169b1c5781d356d197bed56bc78"
_MONITOR_DEPENDENCY_METADATA_SHA256 = "37be6783a76fc83d09ac90e538fc8fd00ae1476fd53299288a1b71556a3ed7ea"
_MONITOR_PROJECT_SHA256 = "585085934b64afe474f5c8665caf0eb92513ee5f8a174d2a2e289ec5509a35a9"
_MONITOR_DISTRIBUTIONS = {
    "psutil": "7.2.2",
    "aiohttp": "3.14.5",
    "pydantic": "2.14.1",
    "typing-extensions": "4.16.0",
    "pyzmq": "27.2.0",
}
_MONITOR_RUNTIME_REQUIREMENTS = (
    "bt_api_base>=0.15.5,<0.16",
    "psutil>=5.9.0",
    "aiohttp>=3.9.0",
    "pydantic>=2.0.0",
    "typing-extensions>=4.8.0",
)
_APP_DB_USERS = (
    ("afi11-app-user-a", "afi11-app-a@example.invalid"),
)
_WINDOWS_PROCESS_ENVIRONMENT_KEYS = (
    # Match the accepted AFI HTTP helper's PATH preservation and keep the
    # platform variables used by Windows DLL lookup and process startup.
    "PATH",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "SYSTEMDRIVE",
    "OS",
    "PROCESSOR_ARCHITECTURE",
    "PROCESSOR_IDENTIFIER",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_LEVEL",
    "PROCESSOR_REVISION",
)


def _required_directory(variable: str) -> Path:
    raw = os.environ.get(variable)
    if type(raw) is not str or not raw.strip():
        pytest.fail(f"Root must set {variable} to the accepted source export")
    path = Path(raw).resolve()
    if not path.is_dir():
        pytest.fail(f"{variable} does not name a source directory")
    return path


def _assert_expected_python() -> None:
    expected = os.environ.get(_EXPECTED_PYTHON_ENV)
    assert type(expected) is str and expected.strip(), "Root must set the AFI venv interpreter"
    assert Path(sys.executable).resolve() == Path(expected).resolve(), (
        "multiprocessing process is using an unexpected Python interpreter",
    )
    assert sys.version_info[:2] == (3, 12), "AFI11 source export is run under Python 3.12"


def _assert_file_origin(symbol: Any, expected: Path) -> None:
    actual_raw = inspect.getsourcefile(symbol)
    assert type(actual_raw) is str
    assert Path(actual_raw).resolve() == expected.resolve(), (
        getattr(symbol, "__name__", type(symbol).__name__),
        "source_origin_mismatch",
    )


def _assert_module_origin(name: str, expected: Path) -> ModuleType:
    module = importlib.import_module(name)
    actual = getattr(module, "__file__", None)
    assert type(actual) is str and Path(actual).resolve() == expected.resolve(), (
        name,
        "source_origin_mismatch",
    )
    return module


def _assert_monitor_source_export(sdk_root: Path) -> None:
    """Require the exact Monitor source/dependency closure before AFI helpers load.

    AFI's reusable support module keeps Monitor optional and uses pytest.importorskip.
    This AFI11 node requires the real component, so it resolves and validates the
    fixed package first; a missing source or runtime dependency is a failure, never
    an accepted skip.
    """

    export_root = _required_directory(_MONITOR_SOURCE_ROOT_ENV)
    assert os.environ.get(_MONITOR_REVISION_ENV) == _MONITOR_SOURCE_COMMIT, (
        "Monitor source export revision mismatch",
    )

    manifest_path = export_root / "export-manifest.json"
    dependency_path = export_root / "monitor-dependency-metadata.json"
    assert manifest_path.is_file() and dependency_path.is_file(), (
        "fixed Monitor source/dependency manifest missing",
    )
    manifest_bytes = manifest_path.read_bytes()
    dependency_bytes = dependency_path.read_bytes()
    assert hashlib.sha256(manifest_bytes).hexdigest() == _MONITOR_EXPORT_MANIFEST_SHA256
    assert hashlib.sha256(dependency_bytes).hexdigest() == _MONITOR_DEPENDENCY_METADATA_SHA256
    manifest = json.loads(manifest_bytes)
    dependency_metadata = json.loads(dependency_bytes)
    assert set(manifest) == {
        "dependency_metadata", "files", "schema_version", "source_commit", "source_repository"
    }
    assert manifest["schema_version"] == 1
    assert manifest["source_commit"] == _MONITOR_SOURCE_COMMIT
    assert dependency_metadata["schema_version"] == 1
    assert dependency_metadata["source_commit"] == _MONITOR_SOURCE_COMMIT
    assert manifest["dependency_metadata"] == {
        "path": "monitor-dependency-metadata.json",
        "sha256": _MONITOR_DEPENDENCY_METADATA_SHA256,
    }

    for relative, expected_sha256 in manifest["files"].items():
        path = export_root / relative
        assert path.is_file(), (relative, "fixed Monitor source file missing")
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected_sha256, (
            relative,
            "fixed Monitor source pin mismatch",
        )

    package_root = export_root / "monitor"
    project_path = package_root / "pyproject.toml"
    project_bytes = project_path.read_bytes()
    assert hashlib.sha256(project_bytes).hexdigest() == _MONITOR_PROJECT_SHA256
    project = tomllib.loads(project_bytes.decode("utf-8"))["project"]
    assert project["name"] == "bt_api_monitor"
    assert project["version"] == "0.1.0"
    assert project["requires-python"] == ">=3.9"
    assert tuple(project["dependencies"]) == _MONITOR_RUNTIME_REQUIREMENTS
    assert dependency_metadata["declared_runtime_dependencies"] == list(
        _MONITOR_RUNTIME_REQUIREMENTS
    )

    resolved = dependency_metadata["resolved_runtime_dependencies"]
    base_project = tomllib.loads(
        (sdk_root / "base" / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]
    assert base_project["name"] == "bt_api_base"
    assert base_project["version"] == "0.15.8"
    assert resolved["bt_api_base"] == {
        "version": base_project["version"],
        "source": "accepted SDK export r3 sdk/base/pyproject.toml",
        "pyproject_sha256": "edc854dfe82050f170cdc08843bf2595db990f2fbfb26e601daae210e4b20582",
    }
    assert dependency_metadata["interpreter"]["path"] == str(Path(sys.executable).resolve())
    assert dependency_metadata["interpreter"]["version"] == ".".join(
        map(str, sys.version_info[:3])
    )
    for distribution, expected_version in _MONITOR_DISTRIBUTIONS.items():
        assert resolved[distribution] == expected_version
        assert importlib_metadata.version(distribution) == expected_version, (
            distribution,
            "fixed AFI venv dependency version mismatch",
        )

    source_root = package_root / "src"
    assert any(
        entry and Path(entry).resolve() == source_root.resolve() for entry in sys.path
    ), "Monitor source export is absent from PYTHONPATH"
    _assert_module_origin("bt_api_monitor", source_root / "bt_api_monitor" / "__init__.py")
    component = _assert_module_origin(
        "bt_api_monitor.service_component",
        source_root / "bt_api_monitor" / "service_component.py",
    )
    telemetry = _assert_module_origin(
        "bt_api_monitor.service_telemetry",
        source_root / "bt_api_monitor" / "service_telemetry.py",
    )
    assert component.MONITOR_READ_OPERATIONS == frozenset(
        {"monitor.query", "monitor.read_after"}
    )
    assert telemetry.TELEMETRY_OPERATIONS == frozenset(
        {"telemetry.append", "telemetry.snapshot"}
    )


def _assert_sdk_source_exports() -> tuple[Path, Path]:
    sdk_root = _required_directory(_SDK_SOURCE_ROOT_ENV)
    risk_root = _required_directory(_RISK_SOURCE_ROOT_ENV)
    expected = (
        (WireIdentity, sdk_root / "base" / "src" / "bt_api_base" / "service_protocol.py"),
        (WireScope, sdk_root / "base" / "src" / "bt_api_base" / "service_protocol.py"),
        (ExecutionScope, sdk_root / "execution" / "src" / "bt_api_execution" / "contracts.py"),
        (
            ProviderObservation,
            sdk_root / "execution" / "src" / "bt_api_execution" / "contracts.py",
        ),
        (SqliteExecutionStore, sdk_root / "execution" / "src" / "bt_api_execution" / "store.py"),
        (
            ExecutionOrderEntryService,
            sdk_root / "execution" / "src" / "bt_api_execution" / "service_order_entry.py",
        ),
        (DurableRiskGate, risk_root / "src" / "bt_api_risk" / "core" / "admission.py"),
        (
            DurableRiskAdmissionAdapter,
            risk_root / "src" / "bt_api_risk" / "order_admission.py",
        ),
        (AccountScope, risk_root / "src" / "bt_api_risk" / "core" / "admission.py"),
        (RiskPolicy, risk_root / "src" / "bt_api_risk" / "core" / "admission.py"),
        (RiskDeniedError, risk_root / "src" / "bt_api_risk" / "core" / "admission.py"),
        (
            ExposureAccountingMode,
            risk_root / "src" / "bt_api_risk" / "core" / "exposure.py",
        ),
        (
            InstrumentRiskMetadata,
            risk_root / "src" / "bt_api_risk" / "core" / "instrument.py",
        ),
        (
            InstrumentRiskRegistry,
            risk_root / "src" / "bt_api_risk" / "core" / "instrument.py",
        ),
        (
            InstrumentRiskAdmissionMapper,
            risk_root / "src" / "bt_api_risk" / "core" / "instrument.py",
        ),
        (ServiceHost, sdk_root / "root" / "bt_api_py" / "service_host.py"),
        (TrustedServiceAuthority, sdk_root / "root" / "bt_api_py" / "service_host.py"),
        (ComponentResources, sdk_root / "root" / "bt_api_py" / "service_components.py"),
        (PrincipalGrant, sdk_root / "root" / "bt_api_py" / "service_registry.py"),
        (ResourceGrant, sdk_root / "root" / "bt_api_py" / "service_registry.py"),
        (ServiceClientRuntime, sdk_root / "root" / "bt_api_py" / "service_runtime.py"),
        (ClientCurveKeys, sdk_root / "root" / "bt_api_py" / "service_runtime.py"),
        (ZmqTransportFactory, sdk_root / "root" / "bt_api_py" / "service_runtime.py"),
        (parse_client_config, sdk_root / "root" / "bt_api_py" / "service_config.py"),
        (parse_service_config, sdk_root / "root" / "bt_api_py" / "service_config.py"),
    )
    for symbol, source in expected:
        _assert_file_origin(symbol, source)
    _assert_monitor_source_export(sdk_root)
    return sdk_root, risk_root


@contextmanager
def _afi_backend_import_path(backend_root: Path) -> Iterator[None]:
    """Expose AFI imports temporarily without leaking into spawn bootstrap."""

    original_sys_path = list(sys.path)
    sys.path.insert(0, str(backend_root.resolve()))
    try:
        yield
    finally:
        sys.path[:] = original_sys_path


def _load_accepted_afi_support() -> ModuleType:
    backend_root = _required_directory(_AFI_SOURCE_ROOT_ENV)
    expected_revision = os.environ.get("AFI11_BACKEND_REVISION")
    assert expected_revision == _AFI_BACKEND_COMMIT, "AFI backend export revision mismatch"
    support_path = backend_root / "tests" / "test_service_read_authorized_http_integration.py"
    order_support_path = backend_root / "tests" / "test_service_read_order_local_process.py"
    assert support_path.is_file() and order_support_path.is_file(), "accepted AFI support files missing"
    assert hashlib.sha256(support_path.read_bytes()).hexdigest() == _AFI_HTTP_TEST_SHA256
    assert hashlib.sha256(order_support_path.read_bytes()).hexdigest() == _AFI_ORDER_TEST_SHA256

    # Preserve the SDK's ``tests`` package for this test's own process helpers,
    # while temporarily making the accepted AFI support files importable by
    # their original package names.  AFI's support module imports its sibling
    # source-fake fixtures from ``tests.unit`` during module initialization.
    tests_package = importlib.import_module("tests")
    unit_package = importlib.import_module("tests.unit")
    original_tests_path = list(tests_package.__path__)
    original_unit_path = list(unit_package.__path__)
    tests_package.__path__ = [str(backend_root / "tests"), *original_tests_path]
    unit_package.__path__ = [str(backend_root / "tests" / "unit"), *original_unit_path]
    try:
        support = importlib.import_module("tests.test_service_read_authorized_http_integration")
    finally:
        tests_package.__path__ = original_tests_path
        unit_package.__path__ = original_unit_path

    assert Path(support.__file__).resolve() == support_path.resolve()
    return support


def _assert_afi_application_origins(backend_root: Path) -> None:
    modules = (
        ("app.main", backend_root / "app" / "main.py"),
        ("app.api.service_read", backend_root / "app" / "api" / "service_read.py"),
        ("app.models.permission", backend_root / "app" / "models" / "permission.py"),
        ("app.models.user", backend_root / "app" / "models" / "user.py"),
        (
            "app.services.service_read.client",
            backend_root / "app" / "services" / "service_read" / "client.py",
        ),
        (
            "app.services.service_read.contracts",
            backend_root / "app" / "services" / "service_read" / "contracts.py",
        ),
        (
            "app.services.service_read.deployment",
            backend_root / "app" / "services" / "service_read" / "deployment.py",
        ),
        (
            "app.services.service_read.session",
            backend_root / "app" / "services" / "service_read" / "session.py",
        ),
        (
            "app.services.service_read.worker",
            backend_root / "app" / "services" / "service_read" / "worker.py",
        ),
    )
    for name, expected in modules:
        module = importlib.import_module(name)
        assert Path(module.__file__).resolve() == expected.resolve(), (name, "source_origin_mismatch")


def _synthetic_home_values(home_path: Path) -> dict[str, str]:
    """Give AFI startup a disposable home without consulting the user profile."""

    home = home_path.resolve()
    home.mkdir(parents=True, exist_ok=True)
    drive = home.drive
    home_tail = str(home)[len(drive) :] if drive else str(home)
    return {
        "HOME": str(home),
        "USERPROFILE": str(home),
        "HOMEDRIVE": drive,
        "HOMEPATH": home_tail,
    }


def _install_synthetic_home_environment(home_path: Path) -> dict[str, tuple[bool, str | None]]:
    """Set only home-related variables and retain only their prior values."""

    values = _synthetic_home_values(home_path)
    previous = {key: (key in os.environ, os.environ.get(key)) for key in values}
    os.environ.update(values)
    return previous


def _restore_home_environment(previous: dict[str, tuple[bool, str | None]]) -> None:
    for key, (was_present, value) in previous.items():
        if was_present:
            assert type(value) is str
            os.environ[key] = value
        else:
            os.environ.pop(key, None)


def _synthetic_afi_imports(support: ModuleType, *, synthetic_home: Path):
    from pydantic_settings import BaseSettings

    original_environment = os.environ
    original_init = BaseSettings.__init__

    def settings_init_without_dotenv(self: Any, *args: Any, **kwargs: Any) -> None:
        kwargs["_env_file"] = None
        original_init(self, *args, **kwargs)

    synthetic_environment = dict(support._SYNTHETIC_ENV)
    # Keep only non-secret Windows process settings needed by imported native
    # dependencies. The AFI HTTP integration does the same for PATH; this
    # broader finite allowlist also preserves DLL and spawn setup. Temp paths
    # stay inside this test's synthetic home rather than using a user profile.
    for key in _WINDOWS_PROCESS_ENVIRONMENT_KEYS:
        value = original_environment.get(key)
        if type(value) is str:
            synthetic_environment[key] = value
        elif key == "PATH":
            synthetic_environment[key] = ""
    synthetic_temp = synthetic_home / "temp"
    synthetic_temp.mkdir(parents=True, exist_ok=True)
    synthetic_environment["TEMP"] = str(synthetic_temp)
    synthetic_environment["TMP"] = str(synthetic_temp)
    synthetic_environment.update(_synthetic_home_values(synthetic_home))
    os.environ = synthetic_environment  # noqa: B003 - synthetic-only AFI import inputs
    BaseSettings.__init__ = settings_init_without_dotenv
    return original_environment, original_init


def _restore_synthetic_afi_imports(originals: tuple[Any, Any]) -> None:
    from pydantic_settings import BaseSettings

    original_environment, original_init = originals
    BaseSettings.__init__ = original_init
    os.environ = original_environment  # noqa: B003 - restore caller environment mapping


def _prepare_afi_database(tmp_path: Path, support: ModuleType) -> str:
    from sqlalchemy import insert
    from sqlalchemy.engine import URL
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    backend_root = _required_directory(_AFI_SOURCE_ROOT_ENV)
    originals = _synthetic_afi_imports(
        support,
        synthetic_home=tmp_path / "afi11-parent-synthetic-home",
    )
    try:
        import app.main as main_module
        import app.models  # noqa: F401 - register mapped tables before create_all
        from app.db import database
        from app.models.permission import Role, user_roles
        from app.models.user import User

        _assert_afi_application_origins(backend_root)
        assert database._engine is None, "AFI database initialized before the test-bound file database"
        db_path = tmp_path / "afi11-service-read.sqlite3"
        database_url = URL.create("sqlite+aiosqlite", database=str(db_path)).render_as_string(
            hide_password=False
        )
        engine = create_async_engine(
            database_url,
            connect_args={"check_same_thread": False},
        )
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        async def create_schema_and_users() -> None:
            try:
                async with engine.begin() as connection:
                    await connection.run_sync(database.Base.metadata.create_all)
                async with session_factory() as session:
                    session.add_all(
                        [
                            User(
                                id=subject,
                                username=f"{subject}-name",
                                email=email,
                                hashed_password="synthetic-only",
                                is_active=True,
                                principal_kind="HUMAN",
                            )
                            for subject, email in _APP_DB_USERS
                        ]
                    )
                    await session.flush()
                    await session.execute(
                        insert(user_roles),
                        [
                            {"user_id": subject, "role": Role.USER.value}
                            for subject, _ in _APP_DB_USERS
                        ],
                    )
                    await session.commit()
            finally:
                await engine.dispose()

        asyncio.run(create_schema_and_users())
        return database_url
    finally:
        _restore_synthetic_afi_imports(originals)


def _owner_process(
    control: Any,
    raw_config: dict[str, Any],
    registry_path: str,
    execution_store_path: str,
    risk_store_path: str,
    service_material: dict[str, Any],
) -> None:
    """Spawn target: one production ServiceHost with the durable owner Risk gate."""

    _assert_expected_python()
    _assert_sdk_source_exports()
    config = parse_service_config(raw_config)
    store = None
    gate = None
    host = None
    provider = None
    denial_reasons: list[str] = []
    provider_construction_count = 0
    try:
        account_scope = service_material["account_scope"]
        wire_scope = service_material["wire_scope"]
        execution_scope = service_material["execution_scope"]
        metadata = service_material["metadata"]
        service_identity = service_material["service_identity"]
        grant_rows = service_material["grant_rows"]
        principal_keys = service_material["principal_keys"]
        security_ref = service_material["security_ref"]
        service_domain = service_material["service_domain"]

        class SyntheticProvider:
            def __init__(self) -> None:
                nonlocal provider_construction_count
                provider_construction_count += 1
                self.session_id = (
                    f"afi11-synthetic-provider-session-{provider_construction_count}"
                )
                self.session_open_count = provider_construction_count
                self.submitted: list[str] = []

            def submit(self, intent: Any) -> ProviderObservation:
                self.submitted.append(intent.intent_id)
                return ProviderObservation.accepted(
                    intent.intent_id,
                    f"afi11-synthetic-provider-order-{len(self.submitted)}",
                )

            def cancel(self, _intent: Any) -> Any:
                raise AssertionError("AFI11 slice does not authorize cancellation")

        provider = SyntheticProvider()
        gate = DurableRiskGate(
            risk_store_path,
            RiskPolicy(
                "afi11-owner-risk-policy-v1",
                Decimal("5000"),
                max_increase_count=10,
                permit_ttl_seconds=60.0,
                require_strategy_allocation=True,
                notional_unit="USD",
                exposure_accounting_mode=ExposureAccountingMode.RESERVATIONS_ONLY,
            ),
        )
        gate.set_strategy_allocation(
            account_scope,
            execution_scope.strategy_id,
            "afi11-owner-allocation-v1",
            max_notional=Decimal("1000"),
            notional_unit="USD",
        )
        registry = InstrumentRiskRegistry((metadata,))
        mapper = InstrumentRiskAdmissionMapper(
            account_scope,
            registry,
            clock_ns=time.time_ns,
            allocation_reader=lambda selected_scope: gate.get_strategy_allocation(
                account_scope, selected_scope.strategy_id
            ),
            wire_account_ref=wire_scope.account_ref,
        )

        def observe_mapper(intent: Any) -> Any:
            try:
                return mapper(intent)
            except RiskDeniedError as error:
                if type(error.code) is str:
                    denial_reasons.append(error.code)
                raise

        admission = DurableRiskAdmissionAdapter(gate, observe_mapper)
        store = SqliteExecutionStore(execution_store_path)
        principals = {public_key: principal for public_key, principal in principal_keys.items()}
        grants: dict[str, tuple[PrincipalGrant, ...]] = {}
        for principal_ref, identity, resources in grant_rows:
            grants[principal_ref] = grants.get(principal_ref, ()) + (
                PrincipalGrant(identity, resources),
            )

        authority = TrustedServiceAuthority(
            identity=service_identity,
            scope=WireScope(service_domain),
            scope_ref=config.scope_ref,
            scope_version="afi11-owner-scope-v1",
            security_ref=security_ref,
            security_version="curve-key-v1",
            registration_policy_ref=config.registration_policy_ref,
            registration_policy_version="afi11-owner-registration-v1",
            service_requirements_ref=config.service_requirements_ref,
            service_requirements_version="afi11-owner-requirements-v1",
            curve_public_key=service_material["server_public"],
            curve_secret_key=service_material["server_secret"],
            principals=principals,
            grants=grants,
        )

        def resource_provider(
            component: str, instance_id: str, _options: Any, _scope: Any
        ) -> ComponentResources:
            if component != "execution" or instance_id != service_material["instance_id"]:
                raise AssertionError("unexpected AFI11 owner resource request")
            return ComponentResources.borrowed(
                {
                    "store": store,
                    "targets": {wire_scope: execution_scope},
                    "dispatch": provider,
                    "admission": {"order_gate": admission, "cancel_gate": None},
                },
                ready=lambda: True,
            )

        host = ServiceHost(config, authority, registry_path, resource_provider)
        host.start()
        control.send(("ready", os.getpid(), host.endpoint, host.state))
        while True:
            host.serve_once(timeout_ms=25)
            if not control.poll():
                continue
            command = control.recv()
            if command == ("counts",):
                binding = admission.policy_binding
                control.send(
                    (
                        "counts",
                        os.getpid(),
                        host.state,
                        provider.session_id,
                        provider.session_open_count,
                        tuple(provider.submitted),
                        tuple(denial_reasons),
                        binding.policy_id,
                        binding.policy_fingerprint,
                        provider_construction_count,
                    )
                )
                continue
            if command == ("close",):
                host.close()
                gate.close()
                store.close()
                gate = None
                store = None
                control.send(("closed", os.getpid(), host.state))
                return
            raise AssertionError("unknown AFI11 owner command")
    except BaseException as error:
        if host is not None and host.state not in {"closed", "failed", "close_failed"}:
            with suppress(BaseException):
                host.close()
        if gate is not None:
            with suppress(BaseException):
                gate.close()
        if store is not None:
            with suppress(BaseException):
                store.close()
        with suppress(BaseException):
            control.send(("error", getattr(error, "code", type(error).__name__)))
        raise
    finally:
        control.close()


def _afi_http_process(
    control: Any, backend_root: str, database_url: str, deployment_json: str
) -> None:
    """Load the fixed AFI test support in this child and run its real Uvicorn app."""

    _assert_expected_python()
    _assert_sdk_source_exports()
    backend = Path(backend_root).resolve()
    os.environ[_AFI_SOURCE_ROOT_ENV] = str(backend)
    os.environ["AFI11_BACKEND_REVISION"] = _AFI_BACKEND_COMMIT
    with _afi_backend_import_path(backend):
        support = _load_accepted_afi_support()
        _assert_sdk_source_exports()
        _assert_afi_application_origins(backend)
        support._manual_order_persistent_http_app_process(control, database_url, deployment_json)


def _receive_pipe(connection: Any, *, timeout: float = 25.0) -> Any:
    if not connection.poll(timeout):
        raise AssertionError("test-owned process missed its bounded response")
    message = connection.recv()
    if isinstance(message, tuple) and message and message[0] == "error":
        raise AssertionError(("test-owned process reported a finite startup failure", message[1:]))
    return message


def _start_owner(context: Any, args: tuple[Any, ...], name: str) -> dict[str, Any]:
    parent, child = context.Pipe(duplex=True)
    process = context.Process(target=_owner_process, args=(child, *args), name=name)
    try:
        process.start()
    except BaseException:
        parent.close()
        child.close()
        raise
    child.close()
    return {"process": process, "pid": process.pid, "control": parent, "name": name}


def _owner_command(owner: dict[str, Any], *command: Any) -> tuple[Any, ...]:
    owner["control"].send(command)
    message = _receive_pipe(owner["control"])
    assert message[0] != "error", message
    return message


def _start_afi_app(
    context: Any,
    *,
    backend_root: Path,
    database_url: str,
    deployment_json: str,
    label: str,
) -> dict[str, Any]:
    parent, child = context.Pipe(duplex=True)
    process = context.Process(
        target=_afi_http_process,
        args=(child, str(backend_root), database_url, deployment_json),
        name=label,
    )
    try:
        process.start()
    except BaseException:
        parent.close()
        child.close()
        raise
    child.close()
    app = {"process": process, "pid": process.pid, "control": parent, "label": label}
    try:
        ready = _receive_pipe(parent)
        assert ready[0] == "ready" and ready[1] == app["pid"]
        app["port"] = ready[2]
        app["ready"] = ready
        return app
    except BaseException as error:
        cleanup_errors: list[str] = []
        try:
            process.join(10.0)
            if process.is_alive():
                assert process.pid == app["pid"] and type(app["pid"]) is int
                process.terminate()
                process.join(10.0)
                cleanup_errors.append("afi_child_required_termination")
            if process.is_alive():
                cleanup_errors.append("afi_child_not_reaped")
        except BaseException as cleanup_error:
            cleanup_errors.append(type(cleanup_error).__name__)
        try:
            parent.close()
        except BaseException as cleanup_error:
            cleanup_errors.append("control_close_" + type(cleanup_error).__name__)
        if not process.is_alive():
            try:
                process.close()
            except BaseException as cleanup_error:
                cleanup_errors.append("process_close_" + type(cleanup_error).__name__)
        if cleanup_errors:
            error.add_note("test-owned app startup cleanup: " + ",".join(cleanup_errors))
        raise


def _close_afi_app(app: dict[str, Any]) -> None:
    process = app["process"]
    failures: list[str] = []
    receipt: Any = None
    try:
        if process.is_alive():
            app["control"].send(("close",))
            receipt = _receive_pipe(app["control"], timeout=30.0)
            assert receipt[0] == "closed" and receipt[1] == app["pid"]
            assert receipt[2] == (True, True, True, True, True, 1)
    except BaseException as error:
        failures.append(type(error).__name__)
    try:
        process.join(20.0)
        if process.is_alive():
            # This is an exact test-owned AFI child PID and is used only during
            # failure cleanup after bounded normal shutdown was attempted.
            assert process.pid == app["pid"] and type(app["pid"]) is int
            process.terminate()
            process.join(10.0)
            failures.append("afi_child_required_termination")
        if process.is_alive():
            failures.append("afi_child_not_reaped")
        exit_code = process.exitcode
    except BaseException as error:
        failures.append(type(error).__name__)
        exit_code = None
    try:
        app["control"].close()
    except BaseException as error:
        failures.append("control_close_" + type(error).__name__)
    if not process.is_alive():
        try:
            process.close()
        except BaseException as error:
            failures.append("process_close_" + type(error).__name__)
    if exit_code != 0:
        failures.append("afi_child_exit_not_zero")
    app["cleanup"] = {"receipt": receipt, "exit_code": exit_code, "failures": tuple(failures)}
    if failures:
        raise AssertionError("test-owned AFI application did not close normally: " + ",".join(failures))


def _crash_exact_afi_app_process(app: dict[str, Any]) -> int:
    process = app["process"]
    assert process.pid == app["pid"] and type(app["pid"]) is int
    assert process.is_alive(), "test-owned AFI process exited before the injected failure"
    process.terminate()
    process.join(10.0)
    assert not process.is_alive(), "the exact test-owned AFI process was not reaped"
    assert type(process.exitcode) is int and process.exitcode != 0
    app["failure_exit_code"] = process.exitcode
    app["control"].close()
    process.close()
    return app["pid"]


def _strategy_session_facts(runtime: ServiceClientRuntime) -> tuple[Any, ...]:
    return (
        runtime.client_session_id,
        runtime.run_id,
        runtime.state,
        tuple(sorted((key, id(transport)) for key, transport in runtime._transports.items())),
        tuple(
            (binding.mount, binding.scope_ref, binding.registration_id, binding.active)
            for binding in runtime.bindings
        ),
    )


def _close_owner(owner: dict[str, Any]) -> None:
    process = owner["process"]
    failures: list[str] = []
    if process.is_alive():
        try:
            receipt = _owner_command(owner, "close")
            assert receipt[0] == "closed" and receipt[1] == owner["pid"]
            assert receipt[2] == "closed"
        except BaseException as error:
            failures.append("owner_close_" + type(error).__name__)
    try:
        process.join(20.0)
        if process.is_alive():
            assert process.pid == owner["pid"] and type(owner["pid"]) is int
            process.terminate()
            process.join(10.0)
            failures.append("owner_required_termination")
    except BaseException as error:
        failures.append("owner_join_" + type(error).__name__)
    if process.is_alive():
        failures.append("owner_not_reaped")
    elif process.exitcode != 0:
        failures.append("owner_exit_not_zero")
    try:
        owner["control"].close()
    except BaseException as error:
        failures.append("owner_control_close_" + type(error).__name__)
    if not process.is_alive():
        try:
            process.close()
        except BaseException as error:
            failures.append("owner_process_close_" + type(error).__name__)
    if failures:
        raise AssertionError("test-owned owner cleanup: " + ",".join(failures))


def _submit_arguments(intent_id: str, metadata: InstrumentRiskMetadata, *, instrument: str) -> dict[str, Any]:
    return {
        "intent_id": intent_id,
        "signal_id": f"signal-{intent_id}",
        "instrument": instrument,
        "side": "BUY",
        "position_effect": "OPEN",
        "order_type": "LIMIT",
        "quantity": "1",
        "price": "100",
        "time_in_force": "GTC",
        "reduce_only": False,
        "metadata_version": metadata.metadata_version,
        "deadline_ns": None,
        "tags": {INSTRUMENT_METADATA_DIGEST_TAG: metadata.digest},
    }


def _http_json(httpx: Any, url: str, *, token: str) -> tuple[int, dict[str, Any]]:
    # These requests target only the test-owned loopback AFI process. Do not
    # route them through machine-level HTTP(S)_PROXY settings.
    with httpx.Client(timeout=8.0, trust_env=False) as client:
        response = client.get(url, headers={"Authorization": f"Bearer {token}"})
    payload = response.json()
    assert type(payload) is dict
    return response.status_code, payload


def test_afi11_app_process_failure_recovery_keeps_strategy_on_same_owner(tmp_path: Path) -> None:
    _assert_expected_python()
    sdk_root, risk_root = _assert_sdk_source_exports()
    backend_root = _required_directory(_AFI_SOURCE_ROOT_ENV)
    assert os.environ.get("AFI11_BACKEND_REVISION") == _AFI_BACKEND_COMMIT
    parent_sys_path = list(sys.path)
    with _afi_backend_import_path(backend_root):
        support = _load_accepted_afi_support()
        _assert_sdk_source_exports()
        database_url = _prepare_afi_database(tmp_path, support)
    assert sys.path == parent_sys_path, "AFI source path leaked into spawn initialization"

    # Keep both spawned service processes on the exact interpreter selected by
    # the fixed AFI venv Root spec, and assert that fact in each child target.
    multiprocessing.set_executable(sys.executable)
    context = multiprocessing.get_context("spawn")
    endpoint = support._order._endpoint()
    server_public, server_secret = zmq.curve_keypair()
    strategy_public, strategy_secret = zmq.curve_keypair()
    app_a_public, app_a_secret = zmq.curve_keypair()
    # The recovery process represents the same authenticated application
    # principal, so it reuses that principal's one CURVE credential. Its new
    # delegate identity is granted separately below.
    app_b_public, app_b_secret = app_a_public, app_a_secret
    security_ref = support._SERVER_SECURITY_REF
    account_ref = support._ACCOUNT_A
    strategy_ref = support._STRATEGY_A
    order_scope = support._SCOPE_A
    order_scope_ref = support._ENTRY_SCOPE_A
    query_scope_ref = support._QUERY_SCOPE_A
    service_id = support._SERVICE_ID
    service_principal = support._SERVICE_PRINCIPAL
    instance_id = support._INSTANCE_ID
    app_principal = support._APP_PRINCIPAL
    app_identity_a = support._APPLICATION_IDENTITY_A
    app_identity_b = WireIdentity(
        WireRole.APPLICATION_CLIENT,
        app_principal,
        support._SUBJECT_A,
        f"afi11-recovered-delegate-{uuid.uuid4().hex[:8]}",
    )
    service_identity = WireIdentity(WireRole.SERVICE, service_principal, service_id)
    strategy_identity = WireIdentity(
        WireRole.STRATEGY_CLIENT,
        support._STRATEGY_A_PRINCIPAL,
        strategy_ref,
    )
    app_security_a = f"afi11-app-a-{uuid.uuid4().hex[:8]}-curve-v1"
    app_security_b = f"afi11-app-b-{uuid.uuid4().hex[:8]}-curve-v1"

    raw_service_config = support._order._service_config(endpoint)
    execution_scope = ExecutionScope(
        "PAPER", "simulation", account_ref, strategy_ref, "afi11-run-20261016"
    )
    account_scope = AccountScope("PAPER", account_ref, "simulation")
    metadata_now_ns = time.time_ns()
    metadata = InstrumentRiskMetadata(
        instrument="BTC-USDT",
        metadata_version="afi11-instrument-metadata-v1",
        as_of_ns=metadata_now_ns - 1_000_000_000,
        expires_at_ns=metadata_now_ns + 3_600_000_000_000,
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

    strategy_config_raw = support._order._client_config(
        endpoint,
        "strategy_client",
        "afi11-strategy-curve-v1",
        strategy=strategy_ref,
    )
    strategy_bindings = strategy_config_raw["clients"]["execution"]["settings"]["bindings"]
    assert len(strategy_bindings) == 1
    strategy_bindings[0].update(scope_ref=order_scope_ref, account_ref=account_ref)
    strategy_bindings.append(
        {
            "mount": "broker.order_query",
            "service_id": service_id,
            "instance_id": instance_id,
            "scope_ref": query_scope_ref,
            "transport_ref": "afi-execution-connect-v1",
            "account_ref": account_ref,
        }
    )
    strategy_config = parse_client_config(strategy_config_raw)
    peer_identity = WireIdentity(WireRole.SERVICE, service_principal, service_id)
    strategy_runtime = ServiceClientRuntime(
        strategy_config,
        identity=strategy_identity,
        scope_resolver=lambda scope_ref: {
            order_scope_ref: order_scope,
            query_scope_ref: order_scope,
        }[scope_ref],
        peer_resolver=lambda _service_id: peer_identity,
        transport_factory=ZmqTransportFactory(
            {
                "afi11-strategy-curve-v1": ClientCurveKeys(
                    server_public, strategy_public, strategy_secret
                )
            }
        ),
        requested_operations={
            "broker.orders": frozenset({"orders.submit"}),
            "broker.order_query": frozenset({"orders.query"}),
        },
    )

    app_profiles = []
    for (
        subject,
        identity,
        app_public,
        app_secret,
        app_security,
    ) in (
        (_APP_DB_USERS[0][0], app_identity_a, app_a_public, app_a_secret, app_security_a),
        (_APP_DB_USERS[0][0], app_identity_b, app_b_public, app_b_secret, app_security_b),
    ):
        app_profiles.append(
            support._default_order_read_profile(
                endpoint=endpoint,
                subject=subject,
                identity=identity,
                account_ref=account_ref,
                scope_ref=query_scope_ref,
                scope=order_scope,
                security_ref=app_security,
                keys={
                    "server_public": server_public,
                    "application_public": app_public,
                    "application_secret": app_secret,
                },
            )
        )
    assert all(profile["account_bindings"] == {} for profile in app_profiles)
    assert app_profiles[0]["subject"] == app_profiles[1]["subject"]
    assert app_profiles[0]["identity"]["actor_ref"] == app_profiles[1]["identity"]["actor_ref"]
    assert app_profiles[0]["identity"]["delegate_ref"] != app_profiles[1]["identity"]["delegate_ref"]
    assert tuple(profile["identity"]["principal_ref"] for profile in app_profiles) == (
        app_principal,
        app_principal,
    )
    expected_application_curve_key = {
        "server_key": server_public.decode("ascii"),
        "public_key": app_a_public.decode("ascii"),
        "secret_key": app_a_secret.decode("ascii"),
    }
    assert app_profiles[0]["curve_keys"] == {app_security_a: expected_application_curve_key}
    assert app_profiles[1]["curve_keys"] == {app_security_b: expected_application_curve_key}
    assert all(profile["grants"]["account_refs"] == [] for profile in app_profiles)
    assert all(profile["grants"]["order_scope_refs"] == [query_scope_ref] for profile in app_profiles)
    assert all(
        binding["mount"] == "application.order_query"
        for profile in app_profiles
        for binding in profile["client_config"]["clients"]["execution"]["settings"]["bindings"]
    )
    assert all(
        profile["order_bindings"] == {query_scope_ref: {"mount": "application.order_query"}}
        for profile in app_profiles
    )
    deployment_json_a = json.dumps(
        {"schema_version": 1, "subjects": [app_profiles[0]]},
        sort_keys=True,
        separators=(",", ":"),
    )
    deployment_json_b = json.dumps(
        {"schema_version": 1, "subjects": [app_profiles[1]]},
        sort_keys=True,
        separators=(",", ":"),
    )

    strategy_resources = (
        ResourceGrant(
            instance_id,
            "service.order_entry",
            order_scope_ref,
            order_scope,
            frozenset({"orders.submit"}),
        ),
        ResourceGrant(
            instance_id,
            "service.order_query",
            query_scope_ref,
            order_scope,
            frozenset({"orders.query"}),
        ),
    )
    app_resources = (
        ResourceGrant(
            instance_id,
            "service.order_query",
            query_scope_ref,
            order_scope,
            frozenset({"orders.query"}),
        ),
    )
    grant_rows = (
        (support._STRATEGY_A_PRINCIPAL, strategy_identity, strategy_resources),
        (app_principal, app_identity_a, app_resources),
        (app_principal, app_identity_b, app_resources),
    )
    principal_keys = {
        strategy_public: support._STRATEGY_A_PRINCIPAL,
        app_a_public: app_principal,
    }
    app_identity_grants = tuple(
        (identity, resources)
        for principal_ref, identity, resources in grant_rows
        if principal_ref == app_principal
    )
    assert app_a_public == app_b_public and app_a_secret == app_b_secret
    assert len(principal_keys) == 2
    assert len(set(principal_keys.values())) == len(principal_keys)
    assert set(principal_keys.values()) == {principal_ref for principal_ref, _, _ in grant_rows}
    assert all(
        identity.principal_ref == principal_ref
        for principal_ref, identity, _ in grant_rows
    )
    assert tuple(identity for identity, _ in app_identity_grants) == (
        app_identity_a,
        app_identity_b,
    )
    assert all(resources == app_resources for _, resources in app_identity_grants)
    service_material = {
        "account_scope": account_scope,
        "wire_scope": order_scope,
        "execution_scope": execution_scope,
        "metadata": metadata,
        "service_identity": service_identity,
        "grant_rows": grant_rows,
        "principal_keys": principal_keys,
        "security_ref": security_ref,
        "service_domain": support._DOMAIN,
        "instance_id": instance_id,
        "server_public": server_public,
        "server_secret": server_secret,
    }

    owner = None
    strategy_started = False
    app_a = None
    app_b = None
    primary_error: BaseException | None = None
    cleanup_errors: list[str] = []
    # Spawn inherits the explicit temporary home for both test-owned children.
    process_home_environment = _install_synthetic_home_environment(
        tmp_path / "afi11-spawned-process-home"
    )
    try:
        owner = _start_owner(
            context,
            (
                raw_service_config,
                str(tmp_path / "afi11-owner-registry.sqlite3"),
                str(tmp_path / "afi11-owner-execution.sqlite3"),
                str(tmp_path / "afi11-owner-risk.sqlite3"),
                service_material,
            ),
            "afi11-real-risk-execution-owner",
        )
        owner_ready = _receive_pipe(owner["control"])
        assert owner_ready[0] == "ready" and owner_ready[1] == owner["pid"]
        assert owner_ready[2] == endpoint and owner_ready[3] == "started"

        strategy_started = True
        startup = strategy_runtime.start()
        assert startup.degraded_count == 0
        before = _strategy_session_facts(strategy_runtime)
        assert before[2] == "registered" and len(before[3]) == 1

        app_a = _start_afi_app(
            context,
            backend_root=backend_root,
            database_url=database_url,
            deployment_json=deployment_json_a,
            label="afi11-http-app-generation-a",
        )
        token_a = support._make_token(_APP_DB_USERS[0][0], support._JWT_SECRET)
        status, unavailable_a = _http_json(
            importlib.import_module("httpx"),
            f"http://127.0.0.1:{app_a['port']}/api/v1/service-read/accounts/{account_ref}",
            token=token_a,
        )
        assert status == 403
        assert unavailable_a["details"]["code"] == "SERVICE_READ_ACCESS_DENIED"

        app_a_pid = _crash_exact_afi_app_process(app_a)
        app_a_failure_exit_code = app_a["failure_exit_code"]
        assert app_a_pid != owner["pid"] and app_a_failure_exit_code != 0
        app_a = None
        after_app_failure = _strategy_session_facts(strategy_runtime)
        assert after_app_failure == before
        assert _strategy_session_facts(strategy_runtime) == before

        app_failure_command = _owner_command(owner, "counts")
        assert app_failure_command[0] == "counts"
        assert app_failure_command[1] == owner["pid"]
        assert app_failure_command[2] == "started"
        assert app_failure_command[3] == "afi11-synthetic-provider-session-1"
        assert app_failure_command[4] == 1
        assert app_failure_command[5] == ()
        assert app_failure_command[6] == ()
        policy_id, policy_fingerprint = app_failure_command[7:9]
        assert app_failure_command[9] == 1
        assert policy_id == "afi11-owner-risk-policy-v1"

        # Without the application process, the same still-registered Strategy
        # reaches the real owner mapper. Missing instrument facts are rejected
        # before provider dispatch and do not replace the owner's policy.
        denied_id = "afi11-app-failure-missing-risk-facts"
        denied = strategy_runtime.call(
            "broker.orders",
            "orders.submit",
            _submit_arguments(denied_id, metadata, instrument="UNREGISTERED-ASSET"),
            scope_ref=order_scope_ref,
        )
        assert denied["code"] == "OK"
        denied_record = denied["result"]["record"]
        assert denied_record["state"] == "REJECTED"
        assert denied_record["unknown_reason"] == "admission_denied"
        assert denied_record["dispatch_attempts"] == 0
        assert denied_record["provider_order_id"] is None
        queried = strategy_runtime.call(
            "broker.order_query",
            "orders.query",
            {"intent_id": denied_id},
            scope_ref=query_scope_ref,
        )
        assert queried["code"] == "OK"
        assert queried["result"]["record"]["state"] == "REJECTED"
        assert queried["result"]["record"]["unknown_reason"] == "admission_denied"
        assert _strategy_session_facts(strategy_runtime) == before

        # A valid fact set remains usable through the exact same owner while
        # AFI is unavailable. Submit one success only: the durable gate may
        # correctly retain an unresolved dispatch freeze after this exposure.
        first_id = "afi11-app-failure-first-approved"
        first = strategy_runtime.call(
            "broker.orders",
            "orders.submit",
            _submit_arguments(first_id, metadata, instrument="BTC-USDT"),
            scope_ref=order_scope_ref,
        )
        assert first["code"] == "OK"
        assert first["result"]["record"]["state"] == "ACKED"
        assert first["result"]["record"]["dispatch_attempts"] == 1
        assert _strategy_session_facts(strategy_runtime) == before

        during_app_failure = _owner_command(owner, "counts")
        assert during_app_failure[1] == owner["pid"]
        assert during_app_failure[2] == "started"
        assert during_app_failure[3] == "afi11-synthetic-provider-session-1"
        assert during_app_failure[4] == 1
        assert during_app_failure[9] == 1
        assert during_app_failure[5] == (first_id,)
        assert during_app_failure[6] == ("INSTRUMENT_UNREGISTERED",)
        assert during_app_failure[7:9] == (policy_id, policy_fingerprint)

        import httpx

        app_b = _start_afi_app(
            context,
            backend_root=backend_root,
            database_url=database_url,
            deployment_json=deployment_json_b,
            label="afi11-http-app-generation-b",
        )
        assert app_b["pid"] != app_a_pid
        token_b = support._make_token(_APP_DB_USERS[0][0], support._JWT_SECRET)
        status, first_view = _http_json(
            httpx,
            f"http://127.0.0.1:{app_b['port']}/api/v1/service-read/orders/{first_id}?scope_ref={query_scope_ref}",
            token=token_b,
        )
        assert status == 200 and first_view["state"] == "ACKED"
        status, denied_view = _http_json(
            httpx,
            f"http://127.0.0.1:{app_b['port']}/api/v1/service-read/orders/{denied_id}?scope_ref={query_scope_ref}",
            token=token_b,
        )
        assert status == 200
        assert denied_view["state"] == "REJECTED"
        assert denied_view["unknown_reason"] == "admission_denied"

        # This deployment has no account binding: an unmounted account view is
        # explicitly refused instead of being served by an application-side
        # provider connection.
        status, unavailable = _http_json(
            httpx,
            f"http://127.0.0.1:{app_b['port']}/api/v1/service-read/accounts/{account_ref}",
            token=token_b,
        )
        assert status == 403
        assert unavailable["details"]["code"] == "SERVICE_READ_ACCESS_DENIED"

        recovered_counts = _owner_command(owner, "counts")
        assert recovered_counts[1] == owner["pid"]
        assert recovered_counts[2] == "started"
        assert recovered_counts[3] == "afi11-synthetic-provider-session-1"
        assert recovered_counts[4] == 1
        assert recovered_counts[9] == 1
        assert recovered_counts[5] == (first_id,)
        assert recovered_counts[6] == ("INSTRUMENT_UNREGISTERED",)
        assert recovered_counts[7:9] == (policy_id, policy_fingerprint)
        assert _strategy_session_facts(strategy_runtime) == before
    except BaseException as error:
        primary_error = error
    finally:
        if app_b is not None:
            try:
                _close_afi_app(app_b)
                app_b = None
            except BaseException as error:
                cleanup_errors.append(type(error).__name__)
        if app_a is not None:
            try:
                _close_afi_app(app_a)
                app_a = None
            except BaseException as error:
                cleanup_errors.append(type(error).__name__)
        if strategy_started:
            try:
                strategy_runtime.close()
                assert strategy_runtime.cleanup_failures == 0
                assert strategy_runtime.unregister_uncertainties == 0
            except BaseException as error:
                cleanup_errors.append(type(error).__name__)
        if owner is not None:
            try:
                _close_owner(owner)
            except BaseException as error:
                cleanup_errors.append(type(error).__name__)
        _restore_home_environment(process_home_environment)

    if primary_error is not None:
        if cleanup_errors:
            primary_error.add_note("test-owned process cleanup reported: " + ",".join(cleanup_errors))
        raise primary_error
    assert cleanup_errors == [], tuple(cleanup_errors)
