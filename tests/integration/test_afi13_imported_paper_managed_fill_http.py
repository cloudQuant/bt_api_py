"""Nonempty legacy import, explicit managed cutover, and one managed fill.

This composes the accepted AFI12 HTTP importer with the accepted R9 managed
Paper HTTP/Execution/Risk/Monitor path. Only the quote source is synthetic.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import multiprocessing
import os
import socket
import sys
import time
from contextlib import suppress
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import zmq

if TYPE_CHECKING:
    import pytest

from bt_api_execution import (
    PAPER_MODEL_REF,
    PAPER_MODEL_VERSION,
    ExecutionScope,
    PaperOwnerDomainV1,
    SqliteExecutionStore,
)
from bt_api_execution.journal_reader import ExecutionJournalReader
from bt_api_monitor.durable import DurableOutbox
from tests.integration.test_afi13_ai_paper_risk_monitor_http import (
    _ai_owner_host_process,
    _curve_keys,
    _paper_service_profile,
)
from tests.unit import test_service_execution_paper_risk_monitor_host_integration as paper_host
from tests.unit._spawned_process_helpers import (
    _assert_no_fallback_cleanup,
    _bounded_reap_spawned_process,
    _close_normally,
    _receive,
    _start_process,
)
from tests.unit._runtime_origin_receipts import (
    persist_parent_worker_receipt,
)

_INITIAL_CASH = 100.0
_JWT_SECRET = "afi13-managed-paper-multiworker-local-test-secret-v1-0123456789"
_OPERATION_KEY = "afi13-managed-paper-two-http-workers-same-operation-v1"
_SYMBOL = "BTC-USDT"
_QUANTITY = 0.25
_IMPORT_SERVICE_ID = "afi13-imported-paper-readonly-service"
_IMPORT_SERVICE_PRINCIPAL = "afi13-imported-paper-readonly-principal"
_IMPORT_SERVICE_INSTANCE = "afi13-imported-paper-readonly-instance"
_IMPORT_SERVICE_SCOPE_REF = "afi13-imported-paper-readonly-service-scope-v1"
_IMPORT_SCOPE_REF = paper_host._ORDER_SCOPE_REF
_IMPORT_MOUNT = "application.paper_legacy_import"
_IMPORT_PORT = "service.paper_legacy_import"
_IMPORT_CONNECT_REF = "afi13-imported-paper-readonly-connect-v1"
_IMPORT_SECURITY_REF = "afi13-imported-paper-readonly-curve-v1"
_IMPORT_OPERATIONS = frozenset(
    {
        "paper.import.status",
        "paper.import.inspect_source",
        "paper.import.prepare",
        "paper.import.activate",
        "paper.import.abort",
    }
)


def _imported_domain(source_domain: PaperOwnerDomainV1) -> PaperOwnerDomainV1:
    base = paper_host._paper_domain()
    return replace(
        base,
        execution_domain_ref=source_domain.execution_domain_ref,
        simulation_account_ref=source_domain.simulation_account_ref,
        experiment_ref=source_domain.experiment_ref,
        strategy_ref=source_domain.strategy_ref,
        initial_cash=source_domain.initial_cash,
        initial_fees=source_domain.initial_fees,
        opening_positions=source_domain.opening_positions,
    )


def _imported_owner_process(
    control,
    raw_config: dict[str, Any],
    registry_path: str,
    paths: dict[str, str],
    owner_keys: dict[str, bytes],
    monitor_keys: dict[str, bytes],
    quote_cursor_counter,
    domain: PaperOwnerDomainV1,
    application_principal: str,
) -> None:
    """Run the accepted owner graph using the imported full-book domain."""

    previous = paper_host._paper_domain
    previous_identity = (
        paper_host._ACCOUNT_REF,
        paper_host._DOMAIN_REF,
        paper_host._STRATEGY_REF,
        paper_host._WIRE_SCOPE,
        paper_host._APPLICATION_PRINCIPAL,
    )
    from bt_api_base.service_protocol import WireScope

    paper_host._paper_domain = lambda: domain
    paper_host._ACCOUNT_REF = domain.simulation_account_ref
    paper_host._DOMAIN_REF = domain.execution_domain_ref
    paper_host._STRATEGY_REF = domain.strategy_ref
    paper_host._APPLICATION_PRINCIPAL = application_principal
    paper_host._WIRE_SCOPE = WireScope(
        domain.execution_domain_ref,
        account_ref=domain.simulation_account_ref,
        strategy_ref=domain.strategy_ref,
    )
    try:
        _ai_owner_host_process(
            control,
            raw_config,
            registry_path,
            paths,
            owner_keys,
            monitor_keys,
            quote_cursor_counter,
            True,
        )
    finally:
        paper_host._paper_domain = previous
        (
            paper_host._ACCOUNT_REF,
            paper_host._DOMAIN_REF,
            paper_host._STRATEGY_REF,
            paper_host._WIRE_SCOPE,
            paper_host._APPLICATION_PRINCIPAL,
        ) = previous_identity


def _imported_monitor_process(
    control,
    raw_config: dict[str, Any],
    registry_path: str,
    outbox_path: str,
    outbox_scope: str,
    monitor_keys: dict[str, bytes],
    owner_keys: dict[str, bytes],
    domain: PaperOwnerDomainV1,
) -> None:
    """Run the accepted Monitor host against the imported Paper identity."""

    previous_identity = (
        paper_host._ACCOUNT_REF,
        paper_host._DOMAIN_REF,
        paper_host._STRATEGY_REF,
        paper_host._WIRE_SCOPE,
        paper_host._INGRESS_SCOPE,
    )
    from bt_api_base.service_protocol import WireScope

    wire_scope = WireScope(
        domain.execution_domain_ref,
        account_ref=domain.simulation_account_ref,
        strategy_ref=domain.strategy_ref,
    )
    paper_host._ACCOUNT_REF = domain.simulation_account_ref
    paper_host._DOMAIN_REF = domain.execution_domain_ref
    paper_host._STRATEGY_REF = domain.strategy_ref
    paper_host._WIRE_SCOPE = wire_scope
    paper_host._INGRESS_SCOPE = wire_scope
    try:
        paper_host._monitor_host_process(
            control,
            raw_config,
            registry_path,
            outbox_path,
            outbox_scope,
            monitor_keys,
            owner_keys,
            True,
        )
    finally:
        (
            paper_host._ACCOUNT_REF,
            paper_host._DOMAIN_REF,
            paper_host._STRATEGY_REF,
            paper_host._WIRE_SCOPE,
            paper_host._INGRESS_SCOPE,
        ) = previous_identity


def _import_readonly_host_process(
    control,
    endpoint: str,
    registry_path: str,
    execution_path: str,
    execution_scope: ExecutionScope,
    domain: PaperOwnerDomainV1,
    application_principal: str,
    owner_keys: dict[str, bytes],
) -> None:
    """Serve only the existing trusted import status/inspection contract."""

    host = None
    store = None
    try:
        from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
        from bt_api_py.service_components import ComponentResources
        from bt_api_py.service_config import parse_service_config
        from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
        from bt_api_py.service_registry import PrincipalGrant, ResourceGrant

        config = parse_service_config(
            {
                "service_config_schema_version": 1,
                "process_role": "service",
                "service_id": _IMPORT_SERVICE_ID,
                "scope_ref": _IMPORT_SERVICE_SCOPE_REF,
                "transport_ref": "afi13-import-readonly-listener-v1",
                "registration_policy_ref": "afi13-import-readonly-registration-v1",
                "service_requirements_ref": "afi13-import-readonly-requirements-v1",
                "components": {
                    "execution": {
                        "config_schema_version": 2,
                        "settings": {
                            "instance_id": _IMPORT_SERVICE_INSTANCE,
                            "bind_ports": [_IMPORT_PORT],
                            "options": {"role": "legacy_import_owner"},
                        },
                    }
                },
                "communication": {
                    "config_schema_version": 1,
                    "profiles": {
                        "afi13-import-readonly-listener-v1": {
                            "backend": "zmq",
                            "config_schema_version": 1,
                            "endpoints": {"rpc_bind": endpoint},
                            "security_profile_ref": _IMPORT_SECURITY_REF,
                            "options": {
                                "high_water_mark": 32,
                                "max_message_bytes": 65_536,
                            },
                        }
                    },
                },
            }
        )
        wire_scope = WireScope(
            domain.execution_domain_ref,
            account_ref=domain.simulation_account_ref,
            strategy_ref=domain.strategy_ref,
        )
        service_identity = WireIdentity(
            WireRole.SERVICE, _IMPORT_SERVICE_PRINCIPAL, _IMPORT_SERVICE_ID
        )
        app_identity = WireIdentity(
            WireRole.APPLICATION_CLIENT,
            application_principal,
            paper_host._APPLICATION_REF,
        )
        grant = ResourceGrant(
            _IMPORT_SERVICE_INSTANCE,
            _IMPORT_PORT,
            _IMPORT_SCOPE_REF,
            wire_scope,
            _IMPORT_OPERATIONS,
        )
        authority = TrustedServiceAuthority(
            identity=service_identity,
            scope=WireScope(domain.execution_domain_ref),
            scope_ref=config.scope_ref,
            scope_version="afi13-import-readonly-scope-v1",
            security_ref=_IMPORT_SECURITY_REF,
            security_version="curve-key-v1",
            registration_policy_ref=config.registration_policy_ref,
            registration_policy_version="afi13-import-readonly-policy-v1",
            service_requirements_ref=config.service_requirements_ref,
            service_requirements_version="afi13-import-readonly-contract-v1",
            curve_public_key=owner_keys["import_server_public"],
            curve_secret_key=owner_keys["import_server_secret"],
            principals={owner_keys["application_public"]: application_principal},
            grants={application_principal: (PrincipalGrant(app_identity, (grant,)),)},
        )
        store = SqliteExecutionStore(execution_path)

        def resource_provider(component, instance_id, _options, _scope):
            if component != "execution" or instance_id != _IMPORT_SERVICE_INSTANCE:
                raise AssertionError("unexpected import service resource")
            return ComponentResources.borrowed(
                {
                    "store": store,
                    "targets": {wire_scope: execution_scope},
                    "legacy_import_domain": domain,
                },
                ready=lambda: True,
            )

        host = ServiceHost(config, authority, registry_path, resource_provider)
        host.start()
        control.send(("ready", host.endpoint, host.state))
        while True:
            if control.poll(0.01):
                if control.recv() != ("close",):
                    raise AssertionError("unknown import host command")
                host.close()
                control.send(("closed", host.state, tuple(host.summary["cleanup_failures"])))
                break
            host.serve_once(timeout_ms=25)
    except BaseException as error:
        with suppress(BaseException):
            if host is not None and host.state == "started":
                host.close()
        code = getattr(error, "code", type(error).__name__)
        if type(code) is not str or not code.isidentifier() or len(code) > 64:
            code = "UNCLASSIFIED"
        with suppress(BrokenPipeError, EOFError, OSError):
            control.send(("host_error", code))
        raise
    finally:
        if store is not None:
            store.close()
        control.close()


def _available_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _load_afi_test_module(module_name: str, path: Path) -> Any:
    """Load an AFI test helper from the production backend without path shadowing."""

    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _cutover_projection(receipt: Any) -> tuple[Any, ...]:
    return tuple(
        getattr(receipt, column.key)
        for column in receipt.__table__.columns
    )


async def _wait_for_http_worker_close(control, server_task, close_deadline: float) -> None:
    """Wait for the parent close command without starving Uvicorn's loop."""

    loop = asyncio.get_running_loop()
    while True:
        if server_task.done():
            await server_task
            raise RuntimeError("HTTP_SERVER_STOPPED_BEFORE_CLOSE")
        if control.poll(0):
            command = control.recv()
            if command != ("close",):
                raise RuntimeError("UNKNOWN_HTTP_WORKER_COMMAND")
            return
        if loop.time() >= close_deadline:
            raise TimeoutError("HTTP_SERVER_CLOSE_TIMEOUT")
        await asyncio.sleep(0.01)


def _managed_paper_http_app_process(
    control,
    host: str,
    port: int,
    database_url: str,
    jwt_secret: str,
    deployment_json: str,
) -> None:
    """Serve the production FastAPI app with its normal service-read lifespan."""

    try:
        from tests.unit._runtime_origin_receipts import (
            capture_runtime_origins,
            persist_worker_error,
        )

        os.environ["DATABASE_URL"] = database_url
        os.environ["JWT_SECRET_KEY"] = jwt_secret
        os.environ["JWT_ALGORITHM"] = "HS256"
        os.environ["RAG_VECTOR_ENABLED"] = "false"
        os.environ["SERVICE_GATEWAY_OPERATOR_DEPLOYMENT_JSON"] = ""

        from pydantic import SecretStr

        from app.config import get_settings

        settings = get_settings()
        settings.SERVICE_READ_DEPLOYMENT_JSON = SecretStr(deployment_json)
        settings.RAG_VECTOR_ENABLED = False

        import app.main as main_module
        import app.services.quote_service as quote_service

        async def no_startup(*_args: Any, **_kwargs: Any) -> None:
            return None

        class NoopShutdownManager:
            async def initiate(self, _app: Any) -> None:
                return None

        main_module.run_startup = no_startup
        main_module.run_shutdown = no_startup
        main_module.GracefulShutdownManager = NoopShutdownManager
        main_module.settings.SERVICE_READ_DEPLOYMENT_JSON = SecretStr(deployment_json)
        main_module.settings.SERVICE_GATEWAY_OPERATOR_DEPLOYMENT_JSON = SecretStr("")
        main_module.settings.RAG_VECTOR_ENABLED = False
        quote_service.get_quote_service = lambda: SimpleNamespace(shutdown=lambda: None)

        import uvicorn

        async def serve() -> None:
            config = uvicorn.Config(
                main_module.app,
                host=host,
                port=port,
                lifespan="on",
                log_level="error",
                access_log=False,
            )
            server = uvicorn.Server(config)
            server_task = asyncio.create_task(server.serve())
            startup_deadline = asyncio.get_running_loop().time() + 30.0
            while not server.started:
                if server_task.done():
                    await server_task
                    raise RuntimeError("HTTP_SERVER_STOPPED_BEFORE_READY")
                if asyncio.get_running_loop().time() >= startup_deadline:
                    raise TimeoutError("HTTP_SERVER_START_TIMEOUT")
                await asyncio.sleep(0.02)

            deployment = getattr(main_module.app.state, "service_read_deployment", None)
            workers = tuple(getattr(deployment, "_workers", ()))
            worker_threads = tuple(
                bool(getattr(getattr(worker, "_thread", None), "is_alive", lambda: False)())
                for worker in workers
            )
            control.send(
                (
                    "ready",
                    os.getpid(),
                    host,
                    port,
                    len(workers),
                    worker_threads,
                    capture_runtime_origins("http-worker", phase="ready"),
                )
            )

            close_deadline = asyncio.get_running_loop().time() + 90.0
            await _wait_for_http_worker_close(control, server_task, close_deadline)

            server.should_exit = True
            await server_task
            control.send(
                (
                    "closed",
                    os.getpid(),
                    getattr(main_module.app.state, "service_read_deployment", None) is None,
                    server.should_exit,
                )
            )

        asyncio.run(serve())
    except BaseException as error:
        receipt = persist_worker_error("http-worker", error)
        try:
            control.send(("host_error", receipt["error"]["code"], receipt))
        except (BrokenPipeError, EOFError, OSError):
            pass
        raise
    finally:
        control.close()


def _manual_order_http_fact(response: Any) -> tuple[int, str | None]:
    try:
        body = response.json()
    except (TypeError, ValueError):
        return int(response.status_code), "INVALID_JSON"
    details = body.get("details") if isinstance(body, dict) else None
    code = details.get("code") if isinstance(details, dict) else None
    return int(response.status_code), code if type(code) is str else None


def test_nonempty_import_explicit_cutover_managed_fill_and_replay_across_workers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Import retained legacy rows, explicitly cut over, then fill once through E/R/M."""

    monkeypatch.setenv("SERVICE_GATEWAY_OPERATOR_DEPLOYMENT_JSON", "")
    monkeypatch.setenv("SERVICE_READ_DEPLOYMENT_JSON", "")
    monkeypatch.setenv("RAG_VECTOR_ENABLED", "false")

    import httpx
    import jwt
    import pytest
    from sqlalchemy import select, update
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    import app.main as main_module
    import app.services.quote_service as quote_service
    import app.utils.security as security
    from app.db import database, sql_repository
    from app.models.legacy_paper_cutover import LegacyPaperCutoverReceipt
    from app.models.legacy_paper_import import LegacyPaperImportReceipt
    from app.models.paper_trading import Account
    from app.services.legacy_paper_migration import LegacyPaperInventoryError
    from app.services.paper_trading_service import PaperTradingService
    from bt_api_py.service_execution_monitor import _outbox_scope

    dynamic_module_names = (
        "tests.test_legacy_paper_import_migration",
        "tests.test_legacy_paper_import_http_host_integration",
    )
    missing_module = object()
    original_modules = {
        name: sys.modules.get(name, missing_module) for name in dynamic_module_names
    }
    tests_package = sys.modules.get("tests")
    package_attribute_names = tuple(
        name.rsplit(".", 1)[1] for name in dynamic_module_names
    )
    original_package_attributes = (
        {
            name: tests_package.__dict__.get(name, missing_module)
            for name in package_attribute_names
        }
        if tests_package is not None
        else {}
    )

    async def run_case() -> None:
        database_path = tmp_path / "afi13-imported-paper-app.sqlite3"
        database_url = f"sqlite+aiosqlite:///{database_path.as_posix()}"
        account_engine = create_async_engine(
            database_url,
            connect_args={"check_same_thread": False},
        )
        try:
            await run_case_with_engine(account_engine, database_url)
        finally:
            await account_engine.dispose()

    async def run_case_with_engine(account_engine: Any, database_url: str) -> None:
        backend_root = Path(main_module.__file__).resolve().parents[1]
        tests_root = backend_root / "tests"
        migration_test = _load_afi_test_module(
            "tests.test_legacy_paper_import_migration",
            tests_root / "test_legacy_paper_import_migration.py",
        )
        sdk_tests = sys.modules.get("tests")
        if sdk_tests is not None:
            setattr(sdk_tests, "test_legacy_paper_import_migration", migration_test)
        import_http_test = _load_afi_test_module(
            "tests.test_legacy_paper_import_http_host_integration",
            tests_root / "test_legacy_paper_import_http_host_integration.py",
        )
        legacy = migration_test
        legacy_http = import_http_test
        monkeypatch.setattr(legacy_http, "_SCOPE_REF", paper_host._ORDER_SCOPE_REF)
        owner_subject = legacy._SOURCE_OWNER_ID
        legacy_account_id = legacy._SOURCE_ACCOUNT_ID
        source_scope, source_domain = legacy._domain()
        domain = _imported_domain(source_domain)
        execution_scope = replace(
            source_scope,
            account_ref=domain.simulation_account_ref,
            strategy_id=domain.strategy_ref,
            trading_day="paper-host-trading-day-v1",
        )
        monkeypatch.setattr(paper_host, "_ACCOUNT_REF", domain.simulation_account_ref)
        monkeypatch.setattr(paper_host, "_DOMAIN_REF", domain.execution_domain_ref)
        monkeypatch.setattr(paper_host, "_STRATEGY_REF", domain.strategy_ref)
        monkeypatch.setattr(paper_host, "_APPLICATION_PRINCIPAL", owner_subject)
        from bt_api_base.service_protocol import WireScope

        monkeypatch.setattr(
            paper_host,
            "_WIRE_SCOPE",
            WireScope(
                domain.execution_domain_ref,
                account_ref=domain.simulation_account_ref,
                strategy_ref=domain.strategy_ref,
            ),
        )
        monkeypatch.setattr(legacy, "_EXPERIMENT_REF", domain.experiment_ref)
        monkeypatch.setattr(legacy, "_QUOTE_SOURCE_REF", domain.quote_source_ref)
        monkeypatch.setattr(legacy, "_INSTRUMENT", _SYMBOL)
        monkeypatch.setattr(legacy, "_domain", lambda: (execution_scope, domain))
        original_binding = legacy._binding

        def imported_binding(bound_domain: Any) -> Any:
            binding = original_binding(bound_domain)
            binding.mount = "application.orders"
            binding.scope_ref = paper_host._ORDER_SCOPE_REF
            binding.query_scope_ref = "paper-risk-ai-order-query-v1"
            binding.account_ref = paper_host._ACCOUNT_REF
            binding.execution_domain_ref = paper_host._DOMAIN_REF
            binding.strategy_ref = paper_host._STRATEGY_REF
            return binding

        monkeypatch.setattr(legacy, "_binding", imported_binding)
        original_seed_account = legacy._seed_account

        async def seed_account_with_managed_fee(**kwargs: Any) -> None:
            await original_seed_account(**kwargs)
            async with database.async_session_maker() as session:
                await session.execute(
                    update(Account)
                    .where(Account.id == legacy_account_id)
                    .values(commission_rate=domain.fee_rate)
                )
                await session.commit()

        monkeypatch.setattr(legacy, "_seed_account", seed_account_with_managed_fee)
        account_sessions = async_sessionmaker(
            account_engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        monkeypatch.setattr(database, "_engine", account_engine)
        monkeypatch.setattr(database, "_async_session_maker", account_sessions)
        monkeypatch.setattr(database, "engine", account_engine)
        monkeypatch.setattr(database, "async_session_maker", account_sessions)
        monkeypatch.setattr(sql_repository, "async_session_maker", account_sessions)
        async with account_engine.begin() as connection:
            await connection.run_sync(database.Base.metadata.create_all)

        monkeypatch.setattr(
            security,
            "settings",
            SimpleNamespace(JWT_SECRET_KEY=_JWT_SECRET, JWT_ALGORITHM="HS256"),
        )
        token = jwt.encode(
            {
                "sub": owner_subject,
                "username": owner_subject,
                "token_type": "access",
                "exp": int(time.time()) + 900,
            },
            _JWT_SECRET,
            algorithm="HS256",
        )
        headers = {"Authorization": f"Bearer {token}"}

        async def no_startup(*_args: Any, **_kwargs: Any) -> None:
            return None

        class NoopShutdownManager:
            async def initiate(self, _app: Any) -> None:
                return None

        monkeypatch.setattr(main_module, "run_startup", no_startup)
        monkeypatch.setattr(main_module, "run_shutdown", no_startup)
        monkeypatch.setattr(main_module, "GracefulShutdownManager", NoopShutdownManager)
        monkeypatch.setattr(main_module.settings, "SERVICE_READ_DEPLOYMENT_JSON", None)
        monkeypatch.setattr(
            main_module.settings,
            "SERVICE_GATEWAY_OPERATOR_DEPLOYMENT_JSON",
            "",
        )
        monkeypatch.setattr(main_module.settings, "RAG_VECTOR_ENABLED", False)
        monkeypatch.setattr(
            quote_service,
            "get_quote_service",
            lambda: SimpleNamespace(shutdown=lambda: None),
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main_module.app),
            base_url="http://afi13-imported-paper-setup.invalid",
        ) as setup_client:
            async with main_module.lifespan(main_module.app):
                await legacy_http._run_http_host_import(
                    setup_client,
                    tmp_path,
                    residual_case=False,
                )

        source_before = await legacy_http.inspect_stable_legacy_paper_account(
            PaperTradingService(), legacy_account_id, owner_subject
        )
        assert source_before.complete and source_before.digest
        assert (source_before.order_count, source_before.position_count, source_before.trade_count) == (
            1,
            1,
            1,
        )
        async with account_sessions() as session:
            import_row = await session.scalar(
                select(LegacyPaperImportReceipt).where(
                    LegacyPaperImportReceipt.legacy_account_id == legacy_account_id
                )
            )
            assert import_row is not None and import_row.state == "ACTIVE"
            assert import_row.owner_subject == owner_subject
            assert import_row.source_inventory_sha256 == source_before.digest
            assert import_row.target_domain_sha256 == domain.fingerprint
            assert import_row.source_order_count == 1
            assert import_row.source_position_count == 1
            assert import_row.source_trade_count == 1
            imported_receipt_id = import_row.id
            imported_generation = import_row.generation
            imported_hash = import_row.target_import_sha256

        endpoint = paper_host._endpoint()
        monitor_endpoint = paper_host._endpoint()
        import_endpoint = paper_host._endpoint()
        owner_keys = _curve_keys()
        import_server_public, import_server_secret = zmq.curve_keypair()
        owner_keys["import_server_public"] = import_server_public
        owner_keys["import_server_secret"] = import_server_secret
        monitor_keys = _curve_keys()
        context = multiprocessing.get_context("spawn")
        quote_cursor_counter = context.Value("Q", 0)
        paths = {
            "execution": str(tmp_path / "afi12-http-host-execution.sqlite3"),
            "risk": str(tmp_path / "afi13-imported-paper-risk.sqlite3"),
            "risk-journal": str(tmp_path / "afi13-imported-paper-risk-journal.sqlite3"),
        }
        seed_store = SqliteExecutionStore(paths["execution"])
        try:
            initial_book = seed_store.read_paper_book(execution_scope)
            import_target = seed_store.get_paper_legacy_import(execution_scope)
            assert import_target is not None
            assert import_target.state == "ACTIVE"
            assert import_target.imported.origin == "imported_legacy"
            assert import_target.imported.source_account_id == legacy_account_id
            assert import_target.imported.source_owner_id == owner_subject
            assert import_target.imported.source_sha256 == source_before.digest
            assert import_target.imported.unallocated_equity_residual == Decimal("0.0")
            assert initial_book.cash == Decimal("89.9")
            assert initial_book.cumulative_fees == Decimal("0.1")
            assert initial_book.receipt_count == 0 and initial_book.execution_cursor == 0
            assert tuple(
                (item.instrument, item.quantity, item.average_cost)
                for item in initial_book.positions
            ) == ((_SYMBOL, Decimal("1"), Decimal("10")),)
        finally:
            seed_store.close()

        source_reader = ExecutionJournalReader(paths["execution"], scope=execution_scope)
        try:
            outbox_scope = _outbox_scope(source_reader.generation, execution_scope.key)
        finally:
            source_reader.close()

        owner_config = paper_host._owner_service_config(endpoint, monitor_endpoint)
        owner_config["components"]["execution"]["settings"]["bind_ports"].append(
            "service.order_query"
        )
        monitor_config = paper_host._monitor_service_config(monitor_endpoint)
        monitor_record: dict[str, Any] | None = None
        owner_record: dict[str, Any] | None = None
        import_record: dict[str, Any] | None = None
        app_records: list[dict[str, Any]] = []
        all_records: list[dict[str, Any]] = []
        forced: list[str] = []
        cleanup_failures: list[str] = []
        try:
            monitor_record = _start_process(
                context,
                _imported_monitor_process,
                (
                    monitor_config,
                    str(tmp_path / "afi13-imported-paper-monitor-registry.sqlite3"),
                    str(tmp_path / "afi13-imported-paper-monitor-outbox.sqlite3"),
                    outbox_scope,
                    monitor_keys,
                    owner_keys,
                    domain,
                ),
                "afi13-paper-multiworker-monitor-host",
            )
            monitor_record["runtime_role"] = "monitor-worker"
            monitor_record["process_pid"] = monitor_record["process"].pid
            all_records.append(monitor_record)
            monitor_ready = _receive(monitor_record["pipe"])
            if monitor_ready[0] == "ready" and len(monitor_ready) > 4:
                monitor_receipt = monitor_ready[4]
                monitor_record["process_instance_id"] = monitor_receipt.get(
                    "process_instance_id"
                )
            elif monitor_ready[0] == "host_error" and len(monitor_ready) > 2:
                monitor_record["process_instance_id"] = monitor_ready[2].get(
                    "process_instance_id"
                )
            assert monitor_ready[:4] == (
                "ready",
                monitor_endpoint,
                "started",
                ("monitor",),
            )
            persist_parent_worker_receipt(
                "monitor-worker",
                "ready",
                monitor_receipt,
                expected_pid=monitor_record["process_pid"],
            )
            assert monitor_receipt["missing_required_modules"] == []
            assert monitor_receipt["missing_required_sources"] == []
            owner_record = _start_process(
                context,
                _imported_owner_process,
                (
                    owner_config,
                    str(tmp_path / "afi13-imported-paper-owner-registry.sqlite3"),
                    paths,
                    owner_keys,
                    monitor_keys,
                    quote_cursor_counter,
                    domain,
                    owner_subject,
                ),
                "afi13-paper-multiworker-execution-risk-owner-host",
            )
            owner_record["runtime_role"] = "execution-owner"
            owner_record["process_pid"] = owner_record["process"].pid
            all_records.append(owner_record)
            owner_ready = _receive(owner_record["pipe"])
            if owner_ready[0] == "ready" and len(owner_ready) > 6:
                owner_receipt = owner_ready[6]
                owner_record["process_instance_id"] = owner_receipt.get(
                    "process_instance_id"
                )
            elif owner_ready[0] == "host_error" and len(owner_ready) > 2:
                owner_record["process_instance_id"] = owner_ready[2].get(
                    "process_instance_id"
                )
            assert owner_ready[0] == "ready", owner_ready
            assert owner_ready[1:3] == (endpoint, "started")
            assert owner_ready[3] == {
                paper_host._SERVICE_INSTANCE: "execution_risk_monitor"
            }
            assert owner_ready[4] == 1 and owner_ready[5] == {}
            persist_parent_worker_receipt(
                "execution-owner",
                "ready",
                owner_receipt,
                expected_pid=owner_record["process_pid"],
            )
            assert owner_receipt["missing_required_modules"] == []
            assert owner_receipt["missing_required_sources"] == []

            import_record = _start_process(
                context,
                _import_readonly_host_process,
                (
                    import_endpoint,
                    str(tmp_path / "afi13-imported-paper-import-registry.sqlite3"),
                    paths["execution"],
                    execution_scope,
                    domain,
                    owner_subject,
                    owner_keys,
                ),
                "afi13-imported-paper-status-host",
            )
            import_record["runtime_role"] = "legacy-import-host"
            all_records.append(import_record)
            import_ready = _receive(import_record["pipe"], timeout=35.0)
            assert import_ready == ("ready", import_endpoint, "started")

            previous_domain_factory = paper_host._paper_domain
            paper_host._paper_domain = lambda: domain
            try:
                profile = _paper_service_profile(
                    endpoint=endpoint,
                    subject=owner_subject,
                    legacy_account_id=legacy_account_id,
                    owner_keys=owner_keys,
                )
            finally:
                paper_host._paper_domain = previous_domain_factory
            imported_scope_claim = {
                "execution_domain_ref": domain.execution_domain_ref,
                "account_ref": domain.simulation_account_ref,
                "strategy_ref": domain.strategy_ref,
            }
            profile["client_config"]["clients"]["execution"]["settings"][
                "bindings"
            ].append(
                {
                    "mount": _IMPORT_MOUNT,
                    "service_id": _IMPORT_SERVICE_ID,
                    "instance_id": _IMPORT_SERVICE_INSTANCE,
                    "scope_ref": _IMPORT_SCOPE_REF,
                    "transport_ref": _IMPORT_CONNECT_REF,
                    "account_ref": domain.simulation_account_ref,
                }
            )
            profile["client_config"]["communication"]["profiles"][
                _IMPORT_CONNECT_REF
            ] = {
                "backend": "zmq",
                "config_schema_version": 1,
                "endpoints": {"rpc_connect": import_endpoint},
                "security_profile_ref": _IMPORT_SECURITY_REF,
                "options": {
                    "request_timeout_ms": 2_000,
                    "high_water_mark": 32,
                    "max_message_bytes": 65_536,
                },
            }
            profile["scope_claims"][_IMPORT_SCOPE_REF] = dict(imported_scope_claim)
            profile["peer_identities"][_IMPORT_SERVICE_ID] = {
                "principal_ref": _IMPORT_SERVICE_PRINCIPAL,
                "actor_ref": _IMPORT_SERVICE_ID,
            }
            profile["curve_keys"][_IMPORT_SECURITY_REF] = {
                "server_key": owner_keys["import_server_public"].decode("ascii"),
                "public_key": owner_keys["application_public"].decode("ascii"),
                "secret_key": owner_keys["application_secret"].decode("ascii"),
            }
            profile["grants"]["paper_legacy_import_account_ids"] = [legacy_account_id]
            profile["paper_legacy_import_bindings"] = {
                legacy_account_id: {
                    "mount": _IMPORT_MOUNT,
                    "scope_ref": _IMPORT_SCOPE_REF,
                    "symbol_bindings": [
                        {
                            "legacy_symbol": legacy._LEGACY_SYMBOL,
                            "target_instrument": _SYMBOL,
                        }
                    ],
                }
            }
            deployment_json = json.dumps(
                {"schema_version": 1, "subjects": [profile]},
                sort_keys=True,
                separators=(",", ":"),
            )
            database_env_url = database_url
            worker_origins: list[tuple[str, int]] = []

            def start_app_worker(label: str) -> dict[str, Any]:
                host = "127.0.0.1"
                port = _available_tcp_port()
                record = _start_process(
                    context,
                    _managed_paper_http_app_process,
                    (
                        host,
                        port,
                        database_env_url,
                        _JWT_SECRET,
                        deployment_json,
                    ),
                    label,
                )
                record["runtime_role"] = "http-worker"
                record["process_pid"] = record["process"].pid
                all_records.append(record)
                ready = _receive(record["pipe"], timeout=35.0)
                if ready[0] == "ready" and len(ready) > 6:
                    app_receipt = ready[6]
                    record["process_instance_id"] = app_receipt.get(
                        "process_instance_id"
                    )
                elif ready[0] == "host_error" and len(ready) > 2:
                    record["process_instance_id"] = ready[2].get(
                        "process_instance_id"
                    )
                assert ready[0] == "ready", ready
                assert ready[2:4] == (host, port)
                assert ready[4:6] == (1, (True,))
                persist_parent_worker_receipt(
                    "http-worker",
                    "ready",
                    app_receipt,
                    expected_pid=record["process_pid"],
                )
                assert app_receipt["missing_required_modules"] == []
                assert app_receipt["missing_required_sources"] == []
                record["ready"] = ready
                worker_origins.append((host, port))
                return record

            app_records.append(start_app_worker("afi13-paper-http-worker-a"))
            app_records.append(start_app_worker("afi13-paper-http-worker-b"))
            app_pids = tuple(record["ready"][1] for record in app_records)
            assert len(set(app_pids)) == 2
            assert all(pid != os.getpid() for pid in app_pids)

            async def post(
                worker_index: int,
                path: str,
                body: dict[str, Any],
                *,
                operation_key: str | None = None,
            ) -> Any:
                host, port = worker_origins[worker_index]
                request_headers = dict(headers)
                if operation_key is not None:
                    request_headers["Idempotency-Key"] = operation_key
                async with httpx.AsyncClient(timeout=30.0) as client:
                    return await client.post(
                        f"http://{host}:{port}{path}",
                        headers=request_headers,
                        json=body,
                    )

            async def get(worker_index: int, path: str) -> Any:
                host, port = worker_origins[worker_index]
                async with httpx.AsyncClient(timeout=30.0) as client:
                    return await client.get(
                        f"http://{host}:{port}{path}",
                        headers=headers,
                    )

            importer_check = await get(
                0,
                f"/api/v1/legacy-paper-imports/accounts/{legacy_account_id}",
            )
            assert importer_check.status_code == 200, importer_check.text
            imported_projection = importer_check.json()
            assert imported_projection["source_inventory"]["sha256"] == source_before.digest
            assert imported_projection["source_inventory"]["order_count"] == 1
            assert imported_projection["source_inventory"]["position_count"] == 1
            assert imported_projection["source_inventory"]["trade_count"] == 1
            assert imported_projection["source_receipt"]["receipt_id"] == imported_receipt_id
            assert imported_projection["source_receipt"]["generation"] == imported_generation
            assert imported_projection["source_receipt"]["state"] == "ACTIVE"
            target_projection = imported_projection["target"]
            assert target_projection["owner_domain_exists"] is True
            assert target_projection["active_account_import_state"] == "ACTIVE"
            assert target_projection["source_receipt"]["source_sha256"] == source_before.digest
            assert target_projection["source_receipt"]["import_sha256"] == imported_hash
            assert target_projection["source_receipt"]["domain_sha256"] == domain.fingerprint

            cutover = await post(
                0,
                f"/api/v1/paper-trading/accounts/{legacy_account_id}/managed-cutover",
                {"confirmed": True},
            )
            assert cutover.status_code == 200, cutover.text
            assert cutover.headers["X-Paper-Data-Source"] == "managed_paper"
            assert cutover.json()["state"] == "ACTIVE"
            assert cutover.json()["receipt_version"] == 2
            assert cutover.json()["source_inventory_sha256"] == source_before.digest
            assert cutover.json()["source_counts"] == {
                "orders": 1,
                "positions": 1,
                "trades": 1,
            }
            async with account_sessions() as session:
                active_cutover = await session.scalar(
                    select(LegacyPaperCutoverReceipt).where(
                        LegacyPaperCutoverReceipt.legacy_account_id == legacy_account_id
                    )
                )
                assert active_cutover is not None
                assert active_cutover.source_kind == "imported_legacy"
                assert active_cutover.source_import_receipt_id == imported_receipt_id
                assert active_cutover.source_import_generation == imported_generation
                assert active_cutover.target_import_sha256 == imported_hash
                assert active_cutover.source_inventory_sha256 == source_before.digest
                assert active_cutover.paper_domain_sha256 == domain.fingerprint
                assert active_cutover.scope_ref == paper_host._ORDER_SCOPE_REF
                assert active_cutover.account_ref == domain.simulation_account_ref
                assert active_cutover.execution_domain_ref == domain.execution_domain_ref
                assert active_cutover.strategy_ref == domain.strategy_ref
                cutover_projection_before_legacy_attempt = _cutover_projection(active_cutover)

            body = {
                "account_id": legacy_account_id,
                "symbol": _SYMBOL,
                "order_type": "market",
                "side": "buy",
                "size": _QUANTITY,
                "price": None,
            }

            async def concurrent_same_key_posts() -> tuple[Any, Any]:
                start_barrier = asyncio.Barrier(2)

                async def submit(worker_index: int) -> Any:
                    await start_barrier.wait()
                    return await post(
                        worker_index,
                        "/api/v1/paper-trading/orders",
                        body,
                        operation_key=_OPERATION_KEY,
                    )

                return await asyncio.gather(submit(0), submit(1))

            raced = await concurrent_same_key_posts()
            assert tuple(response.status_code for response in raced) == (200, 200), tuple(
                _manual_order_http_fact(response) for response in raced
            )
            first, second = (response.json() for response in raced)
            from app.services.managed_paper_adapter import _stable_intent_id

            expected_intent_id = _stable_intent_id(
                "manual", owner_subject, legacy_account_id, _OPERATION_KEY
            )
            assert first["intent_id"] == second["intent_id"] == expected_intent_id
            assert first["state"] == second["state"] == "FILLED"
            assert first["source"] == second["source"] == "managed_paper"
            assert first["filled_size"] == second["filled_size"] == _QUANTITY
            assert first["avg_fill_price"] == second["avg_fill_price"] == 10.02

            conflict = await post(
                1,
                "/api/v1/paper-trading/orders",
                {**body, "size": 0.5},
                operation_key=_OPERATION_KEY,
            )
            assert _manual_order_http_fact(conflict) == (
                409,
                "PAPER_IDEMPOTENCY_CONFLICT",
            )

            monitor_record["pipe"].send(("rows",))
            first_monitor = _receive(monitor_record["pipe"])
            assert first_monitor[0] == "rows" and first_monitor[1] is False
            assert first_monitor[2]
            assert all(row[2] == "execution.lifecycle" for row in first_monitor[2])
            assert all(
                row[3] == paper_host._SERVICE_PRINCIPAL
                and row[4] == "sdk.execution_monitor_projection.v2"
                and row[5] == paper_host._ACCOUNT_REF
                and row[6] == paper_host._STRATEGY_REF
                for row in first_monitor[2]
            )

            first_generation = list(app_records)
            for record in first_generation:
                closed = _close_normally(record, expected_tag="closed")
                assert closed[1] == record["ready"][1]
                assert closed[2] is True and closed[3] is True
            app_records = []

            restarted = start_app_worker("afi13-paper-http-worker-restarted")
            app_records.append(restarted)
            restart_body = await post(
                2,
                "/api/v1/paper-trading/orders",
                body,
                operation_key=_OPERATION_KEY,
            )
            assert restart_body.status_code == 200, restart_body.text
            replayed = restart_body.json()
            assert replayed["intent_id"] == expected_intent_id
            assert replayed["state"] == "FILLED"
            assert replayed["filled_size"] == _QUANTITY
            assert replayed["avg_fill_price"] == 10.02

            query_host, query_port = worker_origins[2]
            async with httpx.AsyncClient(timeout=20.0) as client:
                readback = await client.get(
                    f"http://{query_host}:{query_port}"
                    f"/api/v1/paper-trading/managed-orders/{expected_intent_id}",
                    headers=headers,
                    params={"account_id": legacy_account_id},
                )
            assert readback.status_code == 200, readback.text
            assert readback.headers["X-Paper-Data-Source"] == "managed_paper"
            assert readback.json()["intent_id"] == expected_intent_id
            assert readback.json()["state"] == "FILLED"

            monitor_record["pipe"].send(("rows",))
            replay_monitor = _receive(monitor_record["pipe"])
            assert replay_monitor == first_monitor
            assert int(quote_cursor_counter.value) == 1

            # Exercise the real decorated legacy mutator after import and
            # explicit cutover. If the guard is bypassed, this tripwire fires
            # before any legacy repository write or background task.
            legacy_service = PaperTradingService()
            legacy_create_calls: list[Any] = []

            class UnexpectedLegacyCreate(Exception):
                pass

            async def refuse_legacy_create(order: Any) -> None:
                legacy_create_calls.append(order)
                raise UnexpectedLegacyCreate

            monkeypatch.setattr(legacy_service.order_repo, "create", refuse_legacy_create)
            with pytest.raises(LegacyPaperInventoryError) as denied_legacy_write:
                await legacy_service.submit_order(
                    legacy_account_id,
                    legacy._LEGACY_SYMBOL,
                    "market",
                    "buy",
                    1.0,
                )
            assert denied_legacy_write.value.code == "MANAGED_PAPER_LEGACY_ACCOUNT_FENCED"
            assert legacy_create_calls == []
            source_after_legacy_attempt = await legacy_http.inspect_stable_legacy_paper_account(
                PaperTradingService(), legacy_account_id, owner_subject
            )
            assert source_after_legacy_attempt.complete
            assert source_after_legacy_attempt.digest == source_before.digest
            assert (
                source_after_legacy_attempt.order_count,
                source_after_legacy_attempt.position_count,
                source_after_legacy_attempt.trade_count,
            ) == (1, 1, 1)
            async with account_sessions() as session:
                cutover_after_legacy_attempt = await session.scalar(
                    select(LegacyPaperCutoverReceipt).where(
                        LegacyPaperCutoverReceipt.legacy_account_id == legacy_account_id
                    )
                )
                assert cutover_after_legacy_attempt is not None
                assert (
                    _cutover_projection(cutover_after_legacy_attempt)
                    == cutover_projection_before_legacy_attempt
                )

            for record in app_records:
                closed = _close_normally(record, expected_tag="closed")
                assert closed[1] == record["ready"][1]
                assert closed[2] is True and closed[3] is True
            app_records = []

            importer_closed = _close_normally(import_record, expected_tag="closed")
            assert importer_closed == ("closed", "closed", ())

            owner_closed = _close_normally(owner_record, expected_tag="closed")
            assert owner_closed[1:3] == ("closed", ())
            monitor_closed = _close_normally(monitor_record, expected_tag="closed")
            assert monitor_closed[1:3] == ("closed", ())

            store = SqliteExecutionStore(paths["execution"])
            try:
                book = store.read_paper_book(execution_scope)
                imported = store.get_paper_legacy_import(execution_scope)
                assert imported is not None and imported.state == "ACTIVE"
                assert imported.imported.origin == "imported_legacy"
                assert imported.imported.source_sha256 == source_before.digest
                assert imported.imported.source_account_id == legacy_account_id
                assert imported.imported.source_owner_id == owner_subject
                assert imported.imported.unallocated_equity_residual == Decimal("0.0")
                receipt = store.get_paper_match_receipt(
                    expected_intent_id,
                    scope=execution_scope,
                )
                assert receipt is not None
                fill = store.get_managed_paper_fill_v2_for_event(
                    receipt.event_id,
                    event_sequence=receipt.event_sequence,
                    journal_generation=receipt.journal_generation,
                    scope=execution_scope,
                )
                assert fill is not None
                assert fill.intent_id == expected_intent_id
                assert fill.event_id == receipt.event_id
                assert book.receipt_count == 1
                assert book.execution_journal_generation == receipt.journal_generation
                assert book.execution_cursor == receipt.event_sequence
                assert tuple(
                    (position.instrument, position.quantity, position.average_cost)
                    for position in book.positions
                ) == ((_SYMBOL, Decimal("1.25"), Decimal("10.004")),)
                assert fill.quantity == Decimal("0.25")
                assert fill.fee_amount == Decimal("0.0025")
                assert book.cash == Decimal("87.3925")
                assert book.cumulative_fees == Decimal("0.1025")
            finally:
                store.close()

            source_reader = ExecutionJournalReader(paths["execution"], scope=execution_scope)
            try:
                source_events = source_reader.read_page("execution", limit=100).events
                assert source_events
                source_event_ids = tuple(event.event_id for event in source_events)
                assert len(set(source_event_ids)) == len(source_event_ids)
                assert {event.intent_id for event in source_events} == {expected_intent_id}
                monitor_event_ids = tuple(row[7] for row in replay_monitor[2])
                assert len(set(monitor_event_ids)) == len(monitor_event_ids)
                assert set(monitor_event_ids) == set(source_event_ids)
                assert all(
                    event.journal_generation == book.execution_journal_generation
                    for event in source_events
                )
            finally:
                source_reader.close()

            outbox = DurableOutbox(
                str(tmp_path / "afi13-imported-paper-monitor-outbox.sqlite3"),
                read_only=True,
            )
            persisted, has_more = outbox.read_page_types(
                outbox_scope,
                ("execution.lifecycle", "execution.cancellation"),
                0,
                100,
            )
            assert has_more is False
            assert tuple(row.event.event_id for row in persisted) == tuple(
                row[1] for row in replay_monitor[2]
            )
            source_after_fill = await legacy_http.inspect_stable_legacy_paper_account(
                PaperTradingService(), legacy_account_id, owner_subject
            )
            assert source_after_fill.complete
            assert source_after_fill.digest == source_before.digest
            assert (
                source_after_fill.order_count,
                source_after_fill.position_count,
                source_after_fill.trade_count,
            ) == (1, 1, 1)
            async with account_sessions() as session:
                final_import_row = await session.get(
                    LegacyPaperImportReceipt, imported_receipt_id
                )
                final_cutover_row = await session.get(
                    LegacyPaperCutoverReceipt, legacy_account_id
                )
                assert final_import_row is not None
                assert final_import_row.state == "ACTIVE"
                assert final_import_row.source_inventory_sha256 == source_before.digest
                assert final_import_row.target_import_sha256 == imported_hash
                assert final_cutover_row is not None
                assert _cutover_projection(final_cutover_row) == (
                    cutover_projection_before_legacy_attempt
                )
        finally:
            for record in reversed(all_records):
                if not record["closed"]:
                    _bounded_reap_spawned_process(record, forced, cleanup_failures)
            _assert_no_fallback_cleanup(forced, cleanup_failures)

    try:
        asyncio.run(run_case())
    finally:
        for module_name, previous in original_modules.items():
            if previous is missing_module:
                sys.modules.pop(module_name, None)
            else:
                sys.modules[module_name] = previous
        if tests_package is not None:
            for attribute_name, previous in original_package_attributes.items():
                if previous is missing_module:
                    tests_package.__dict__.pop(attribute_name, None)
                else:
                    tests_package.__dict__[attribute_name] = previous


def test_http_worker_close_wait_yields_to_asyncio_tasks() -> None:
    async def verify() -> None:
        tick_state = {"count": 0}

        class TickControl:
            def __init__(self) -> None:
                self.poll_timeouts: list[float] = []

            def poll(self, timeout: float) -> bool:
                self.poll_timeouts.append(timeout)
                return tick_state["count"] >= 3

            def recv(self) -> tuple[str]:
                return ("close",)

        control = TickControl()

        async def run_ticks() -> None:
            for _ in range(3):
                await asyncio.sleep(0)
                tick_state["count"] += 1

        async def keep_server_pending() -> None:
            await asyncio.Event().wait()

        loop = asyncio.get_running_loop()
        server_task = asyncio.create_task(keep_server_pending())
        tick_task = asyncio.create_task(run_ticks())
        try:
            await _wait_for_http_worker_close(
                control,
                server_task,
                loop.time() + 1.0,
            )
            await tick_task
            assert tick_state["count"] >= 3
            assert control.poll_timeouts and set(control.poll_timeouts) == {0}
        finally:
            server_task.cancel()
            await asyncio.gather(server_task, return_exceptions=True)
            if not tick_task.done():
                tick_task.cancel()
                await asyncio.gather(tick_task, return_exceptions=True)

    asyncio.run(verify())
