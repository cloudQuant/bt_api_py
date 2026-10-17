"""Managed Paper HTTP operation-key replay across AFI worker processes.

The AFI application processes use independent ServiceRead deployments and
workers. They share a persistent AFI SQLite database and one real local
Execution/Risk/Monitor ServiceHost composition. The only synthetic leaf is
the deterministic paper quote source from the accepted R13 helper.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import multiprocessing
import os
import socket
import time
import uuid
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import zmq

if TYPE_CHECKING:
    import pytest

from bt_api_execution import (
    PAPER_ENVIRONMENT,
    PAPER_PROVIDER,
    ExecutionScope,
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


def _available_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


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


def test_managed_paper_http_same_key_is_single_fill_across_two_workers_and_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two normal HTTP workers and a restarted worker replay one Paper operation."""

    monkeypatch.setenv("SERVICE_GATEWAY_OPERATOR_DEPLOYMENT_JSON", "")
    monkeypatch.setenv("SERVICE_READ_DEPLOYMENT_JSON", "")
    monkeypatch.setenv("RAG_VECTOR_ENABLED", "false")

    import httpx
    import jwt
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    import app.main as main_module
    import app.services.quote_service as quote_service
    import app.utils.security as security
    from app.db import database, sql_repository
    from app.models.user import User
    from bt_api_py.service_execution_monitor import _outbox_scope

    async def run_case() -> None:
        database_path = tmp_path / "afi13-paper-multiworker-app.sqlite3"
        database_url = f"sqlite+aiosqlite:///{database_path.as_posix()}"
        account_engine = create_async_engine(
            database_url,
            connect_args={"check_same_thread": False},
        )
        try:
            await run_case_with_engine(account_engine)
        finally:
            await account_engine.dispose()

    async def run_case_with_engine(account_engine: Any) -> None:
        owner_subject = str(uuid.uuid4())
        database_path = tmp_path / "afi13-paper-multiworker-app.sqlite3"
        database_url = f"sqlite+aiosqlite:///{database_path.as_posix()}"
        execution_path = tmp_path / "afi13-paper-multiworker-execution.sqlite3"
        paths = {
            "execution": str(execution_path),
            "risk": str(tmp_path / "afi13-paper-multiworker-risk.sqlite3"),
            "risk-journal": str(tmp_path / "afi13-paper-multiworker-risk-journal.sqlite3"),
        }
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

        migration_path = (
            Path(main_module.__file__).resolve().parents[1]
            / "alembic"
            / "versions"
            / "20261016_legacy_paper_cutover.py"
        )
        migration_spec = importlib.util.spec_from_file_location(
            "afi13_multiworker_cutover_migration", migration_path
        )
        assert migration_spec is not None and migration_spec.loader is not None
        migration = importlib.util.module_from_spec(migration_spec)
        migration_spec.loader.exec_module(migration)

        def apply_cutover_migration(sync_connection: Any) -> None:
            sync_connection.exec_driver_sql("DROP TABLE legacy_paper_writer_leases")
            sync_connection.exec_driver_sql("DROP TABLE legacy_paper_cutover_receipts")
            context = MigrationContext.configure(sync_connection)
            with Operations.context(context):
                migration.upgrade()

        async with account_engine.begin() as connection:
            await connection.run_sync(apply_cutover_migration)
            trigger_rows = await connection.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'trigger'")
            )
            assert "trg_lpc_account_update" in {
                str(row[0]) for row in trigger_rows.all()
            }

        async with account_sessions() as session:
            session.add(
                User(
                    id=owner_subject,
                    username=f"paper-multi-{owner_subject[:8]}",
                    email=f"paper-multi-{owner_subject[:8]}@example.test",
                    hashed_password="synthetic-test-only-unused",
                )
            )
            await session.commit()

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
            base_url="http://afi13-managed-paper-setup.invalid",
        ) as setup_client:
            async with main_module.lifespan(main_module.app):
                created = await setup_client.post(
                    "/api/v1/paper-trading/accounts",
                    headers=headers,
                    json={
                        "name": "AFI13 managed Paper multiworker",
                        "initial_cash": _INITIAL_CASH,
                        "commission_rate": 0.001,
                        "slippage_rate": 0,
                    },
                )
                assert created.status_code == 200, created.text
                assert created.headers["X-Paper-Data-Source"] == "legacy"
                legacy_account_id = created.json()["id"]

        endpoint = paper_host._endpoint()
        monitor_endpoint = paper_host._endpoint()
        owner_keys = _curve_keys()
        monitor_keys = _curve_keys()
        context = multiprocessing.get_context("spawn")
        quote_cursor_counter = context.Value("Q", 0)
        execution_scope = ExecutionScope(
            PAPER_PROVIDER,
            PAPER_ENVIRONMENT,
            paper_host._ACCOUNT_REF,
            paper_host._STRATEGY_REF,
            "paper-host-trading-day-v1",
        )
        seed_store = SqliteExecutionStore(paths["execution"])
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
        app_records: list[dict[str, Any]] = []
        all_records: list[dict[str, Any]] = []
        forced: list[str] = []
        cleanup_failures: list[str] = []
        try:
            monitor_record = _start_process(
                context,
                paper_host._monitor_host_process,
                (
                    monitor_config,
                    str(tmp_path / "afi13-paper-multiworker-monitor-registry.sqlite3"),
                    str(tmp_path / "afi13-paper-multiworker-monitor-outbox.sqlite3"),
                    outbox_scope,
                    monitor_keys,
                    owner_keys,
                    True,
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
                _ai_owner_host_process,
                (
                    owner_config,
                    str(tmp_path / "afi13-paper-multiworker-owner-registry.sqlite3"),
                    paths,
                    owner_keys,
                    monitor_keys,
                    quote_cursor_counter,
                    True,
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

            profile = _paper_service_profile(
                endpoint=endpoint,
                subject=owner_subject,
                legacy_account_id=legacy_account_id,
                owner_keys=owner_keys,
            )
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

            cutover = await post(
                0,
                f"/api/v1/paper-trading/accounts/{legacy_account_id}/managed-cutover",
                {"confirmed": True},
            )
            assert cutover.status_code == 200, cutover.text
            assert cutover.headers["X-Paper-Data-Source"] == "managed_paper"
            assert cutover.json()["state"] == "ACTIVE"

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

            for record in app_records:
                closed = _close_normally(record, expected_tag="closed")
                assert closed[1] == record["ready"][1]
                assert closed[2] is True and closed[3] is True
            app_records = []

            owner_closed = _close_normally(owner_record, expected_tag="closed")
            assert owner_closed[1:3] == ("closed", ())
            monitor_closed = _close_normally(monitor_record, expected_tag="closed")
            assert monitor_closed[1:3] == ("closed", ())

            store = SqliteExecutionStore(paths["execution"])
            try:
                book = store.read_paper_book(execution_scope)
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
                    (position.instrument, position.quantity)
                    for position in book.positions
                ) == ((_SYMBOL, fill.quantity),)
                assert fill.quantity == Decimal("0.25")
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
                str(tmp_path / "afi13-paper-multiworker-monitor-outbox.sqlite3"),
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
        finally:
            for record in reversed(all_records):
                if not record["closed"]:
                    _bounded_reap_spawned_process(record, forced, cleanup_failures)
            _assert_no_fallback_cleanup(forced, cleanup_failures)

    asyncio.run(run_case())


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
