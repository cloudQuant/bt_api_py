"""Explicit AI confirmation through a real local E/R/Monitor paper owner.

Only the parser leaf and the AI confirmation decision are deterministic test
inputs. FastAPI authentication, AI confirmation/pending-state handling,
ServiceRead workers, SDK ServiceHosts, Risk admission, Execution matching and
the Monitor relay all use their production implementations.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import multiprocessing
import time
import uuid
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import zmq

if TYPE_CHECKING:
    import pytest

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py.service_config import parse_service_config
from bt_api_py.service_host import (
    ServiceHost,
    TrustedServiceAuthority,
    TrustedServiceClients,
)
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import ClientCurveKeys, ZmqTransportFactory
from tests.unit import test_service_execution_paper_risk_monitor_host_integration as paper_host
from tests.unit._spawned_process_helpers import (
    _assert_no_fallback_cleanup,
    _bounded_reap_spawned_process,
    _close_normally,
    _receive,
    _start_process,
)

_AI_QUERY_SCOPE_REF = "paper-risk-ai-order-query-v1"
_AI_CONNECT_REF = "paper-risk-ai-application-connect-v1"
_AI_SECURITY_REF = "paper-risk-ai-application-curve-v1"
_AI_INITIAL_CASH = 100.0


def _curve_keys() -> dict[str, bytes]:
    server_public, server_secret = zmq.curve_keypair()
    application_public, application_secret = zmq.curve_keypair()
    return {
        "server_public": server_public,
        "server_secret": server_secret,
        "application_public": application_public,
        "application_secret": application_secret,
    }


def _ai_owner_host_process(
    control,
    raw_config: dict[str, Any],
    registry_path: str,
    paths: dict[str, str],
    owner_keys: dict[str, bytes],
    monitor_keys: dict[str, bytes],
    quote_cursor_counter,
) -> None:
    """Run the same trusted Paper E/R/M graph with submit and query grants."""

    host = None
    graph = None
    try:
        config = parse_service_config(raw_config)
        resources, graph = paper_host._risk_resources(
            paths,
            initialize_strategy_allocation=True,
            quote_cursor_counter=quote_cursor_counter,
        )
        monitor_identity = WireIdentity(
            WireRole.SERVICE, paper_host._MONITOR_PRINCIPAL, paper_host._MONITOR_ID
        )
        owner_identity = WireIdentity(
            WireRole.SERVICE, paper_host._SERVICE_PRINCIPAL, paper_host._SERVICE_ID
        )

        def resource_provider(component, _instance_id, _options, _scope):
            if component not in resources:
                raise AssertionError("unexpected selected Paper component")
            return resources[component]

        clients = TrustedServiceClients(
            scope_resolver=lambda scope_ref: (
                paper_host._WIRE_SCOPE
                if scope_ref == paper_host._MONITOR_SCOPE_REF
                else (_ for _ in ()).throw(ValueError("unexpected Monitor scope"))
            ),
            peer_resolver=lambda service_id: (
                monitor_identity
                if service_id == paper_host._MONITOR_ID
                else (_ for _ in ()).throw(ValueError("unexpected Monitor peer"))
            ),
            transport_factory=ZmqTransportFactory(
                {
                    paper_host._OWNER_CONNECT_REF: ClientCurveKeys(
                        monitor_keys["server_public"],
                        owner_keys["server_public"],
                        owner_keys["server_secret"],
                    )
                }
            ),
            requested_operations={
                "upstream.telemetry": frozenset({"events.append"})
            },
        )
        owner_scope = WireScope(paper_host._DOMAIN_REF)
        submit_grant = ResourceGrant(
            paper_host._SERVICE_INSTANCE,
            "service.order_entry",
            paper_host._ORDER_SCOPE_REF,
            paper_host._WIRE_SCOPE,
            frozenset({"orders.submit"}),
        )
        query_grant = ResourceGrant(
            paper_host._SERVICE_INSTANCE,
            "service.order_query",
            _AI_QUERY_SCOPE_REF,
            paper_host._WIRE_SCOPE,
            frozenset({"orders.query"}),
        )
        monitor_endpoint = raw_config["communication"]["profiles"][
            paper_host._MONITOR_CONNECT_REF
        ]["endpoints"]["rpc_connect"]
        host = ServiceHost(
            config,
            TrustedServiceAuthority(
                identity=owner_identity,
                scope=owner_scope,
                scope_ref=config.scope_ref,
                scope_version="paper-risk-owner-scope-v1",
                security_ref="paper-risk-owner-server-curve-v1",
                security_version="curve-key-v1",
                registration_policy_ref=config.registration_policy_ref,
                registration_policy_version="paper-risk-owner-policy-v1",
                service_requirements_ref=config.service_requirements_ref,
                service_requirements_version="paper-risk-owner-requirements-v1",
                curve_public_key=owner_keys["server_public"],
                curve_secret_key=owner_keys["server_secret"],
                principals={
                    owner_keys["application_public"]: paper_host._APPLICATION_PRINCIPAL
                },
                grants={
                    paper_host._APPLICATION_PRINCIPAL: (
                        PrincipalGrant(
                            WireIdentity(
                                WireRole.APPLICATION_CLIENT,
                                paper_host._APPLICATION_PRINCIPAL,
                                paper_host._APPLICATION_REF,
                            ),
                            (submit_grant, query_grant),
                        ),
                    )
                },
            ),
            registry_path,
            resource_provider,
            service_directory={
                paper_host._SERVICE_ID: config,
                paper_host._MONITOR_ID: parse_service_config(
                    paper_host._monitor_service_config(monitor_endpoint)
                ),
            },
            trusted_clients=clients,
        )
        host.start()
        control.send(
            (
                "ready",
                host.endpoint,
                host.state,
                dict(host.summary["paper"]["compositions"]),
                host.summary["paper"]["monitor_relays"],
                dict(host.summary["paper"]["monitor_faults"]),
            )
        )
        while True:
            if control.poll(0.01):
                command = control.recv()
                if command == ("close",):
                    host.close()
                    control.send(
                        ("closed", host.state, tuple(host.summary["cleanup_failures"]))
                    )
                    break
                raise AssertionError("unknown AI paper owner command")
            host.serve_once(timeout_ms=25)
    except BaseException as error:
        if host is not None and host.state == "started":
            with suppress(BaseException):
                host.close()
        with suppress(BaseException):
            code = getattr(error, "code", type(error).__name__)
            if type(code) is not str or not code.isidentifier() or len(code) > 64:
                code = "UNCLASSIFIED"
            control.send(("host_error", code))
        raise
    finally:
        if graph is not None:
            paper_host._close_graph(graph)
        control.close()


def _paper_service_profile(
    *,
    endpoint: str,
    subject: str,
    legacy_account_id: str,
    owner_keys: dict[str, bytes],
) -> dict[str, Any]:
    domain = paper_host._paper_domain()
    bindings = [
        {
            "mount": "application.orders",
            "service_id": paper_host._SERVICE_ID,
            "instance_id": paper_host._SERVICE_INSTANCE,
            "scope_ref": paper_host._ORDER_SCOPE_REF,
            "transport_ref": _AI_CONNECT_REF,
            "account_ref": paper_host._ACCOUNT_REF,
        },
        {
            "mount": "application.order_query",
            "service_id": paper_host._SERVICE_ID,
            "instance_id": paper_host._SERVICE_INSTANCE,
            "scope_ref": _AI_QUERY_SCOPE_REF,
            "transport_ref": _AI_CONNECT_REF,
            "account_ref": paper_host._ACCOUNT_REF,
        },
    ]
    same_scope_claim = {
        "execution_domain_ref": paper_host._DOMAIN_REF,
        "account_ref": paper_host._ACCOUNT_REF,
        "strategy_ref": paper_host._STRATEGY_REF,
    }
    return {
        "subject": subject,
        "identity": {
            "principal_ref": paper_host._APPLICATION_PRINCIPAL,
            "actor_ref": paper_host._APPLICATION_REF,
            "delegate_ref": None,
        },
        "client_config": {
            "client_config_schema_version": 1,
            "process_role": "application_client",
            "registration": {"application_ref": paper_host._APPLICATION_REF},
            "clients": {
                "execution": {
                    "config_schema_version": 1,
                    "settings": {
                        "bindings": bindings,
                        "options": {"request_timeout_ms": 2_000},
                    },
                }
            },
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    _AI_CONNECT_REF: {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_connect": endpoint},
                        "security_profile_ref": _AI_SECURITY_REF,
                        "options": {
                            "request_timeout_ms": 2_000,
                            "high_water_mark": 32,
                            "max_message_bytes": 65_536,
                        },
                    }
                },
            },
        },
        "scope_claims": {
            paper_host._ORDER_SCOPE_REF: dict(same_scope_claim),
            _AI_QUERY_SCOPE_REF: dict(same_scope_claim),
        },
        "peer_identities": {
            paper_host._SERVICE_ID: {
                "principal_ref": paper_host._SERVICE_PRINCIPAL,
                "actor_ref": paper_host._SERVICE_ID,
            }
        },
        "curve_keys": {
            _AI_SECURITY_REF: {
                "server_key": owner_keys["server_public"].decode("ascii"),
                "public_key": owner_keys["application_public"].decode("ascii"),
                "secret_key": owner_keys["application_secret"].decode("ascii"),
            }
        },
        "grants": {
            "account_refs": [],
            "order_scope_refs": [_AI_QUERY_SCOPE_REF],
            "monitor_scope_refs": [],
            "paper_submit_account_ids": [legacy_account_id],
        },
        "account_bindings": {},
        "order_bindings": {
            _AI_QUERY_SCOPE_REF: {"mount": "application.order_query"},
        },
        "monitor_bindings": {},
        "paper_submit_bindings": {
            legacy_account_id: {
                "control_mode": "execution_risk_monitor",
                "mount": "application.orders",
                "scope_ref": paper_host._ORDER_SCOPE_REF,
                "query_scope_ref": _AI_QUERY_SCOPE_REF,
                "account_ref": paper_host._ACCOUNT_REF,
                "execution_domain_ref": paper_host._DOMAIN_REF,
                "strategy_ref": paper_host._STRATEGY_REF,
                "paper_owner_domain": domain.to_payload(),
                "execution_scope": {
                    "provider": "paper",
                    "environment": "paper",
                    "account_ref": paper_host._ACCOUNT_REF,
                    "strategy_id": paper_host._STRATEGY_REF,
                    "trading_day": "paper-host-trading-day-v1",
                },
            }
        },
        "worker": {"max_queue_size": 8, "maintenance_interval_seconds": 30.0},
    }


def test_ai_explicit_confirmation_reaches_managed_paper_risk_and_monitor_host(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run AI execute→confirm over HTTP, then read the durable E and Monitor facts."""

    import app.main as main_module
    import app.services.ai_trading_service as ai_service_module
    import app.services.quote_service as quote_service
    import app.utils.security as security
    import httpx
    import jwt
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from app.api import ai_trading as ai_api
    from app.db import database, sql_repository
    from app.models.user import User
    from app.schemas.ai_trading import (
        RiskLevel,
        TradeAction,
        TradeStatus,
        TradingIntent,
    )
    from app.services.ai_trading_service import AITradingService
    from app.services.trading_risk_guard import TradingRiskConfig, TradingRiskGuard
    from bt_api_execution import (
        PAPER_ENVIRONMENT,
        PAPER_PROVIDER,
        ExecutionScope,
        SqliteExecutionStore,
    )
    from bt_api_execution.journal_reader import ExecutionJournalReader
    from pydantic import SecretStr
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from bt_api_py.service_execution_monitor import _outbox_scope

    async def run_http_flow() -> None:
        owner_subject = str(uuid.uuid4())
        db_path = tmp_path / "afi13-ai-paper-app.sqlite3"
        execution_path = tmp_path / "afi13-ai-paper-execution.sqlite3"
        paths = {
            "execution": str(execution_path),
            "risk": str(tmp_path / "afi13-ai-paper-risk.sqlite3"),
            "risk-journal": str(tmp_path / "afi13-ai-paper-risk-journal.sqlite3"),
        }
        account_engine = create_async_engine(
            f"sqlite+aiosqlite:///{db_path.as_posix()}",
            connect_args={"check_same_thread": False},
        )
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
            "afi13_ai_paper_cutover_migration", migration_path
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
            names = await connection.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'trigger'")
            )
            assert "trg_lpc_account_update" in {str(row[0]) for row in names.all()}

        async with account_sessions() as session:
            session.add(
                User(
                    id=owner_subject,
                    username=f"paper-ai-{owner_subject[:8]}",
                    email=f"paper-ai-{owner_subject[:8]}@example.test",
                    hashed_password="synthetic-test-only-unused",
                )
            )
            await session.commit()

        monkeypatch.setattr(
            security,
            "settings",
            SimpleNamespace(JWT_SECRET_KEY="afi13-ai-paper-test-secret", JWT_ALGORITHM="HS256"),
        )
        token = jwt.encode(
            {
                "sub": owner_subject,
                "username": owner_subject,
                "token_type": "access",
                "exp": int(time.time()) + 600,
            },
            "afi13-ai-paper-test-secret",
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
        monkeypatch.setattr(main_module.settings, "RAG_VECTOR_ENABLED", False)
        monkeypatch.setattr(
            quote_service,
            "get_quote_service",
            lambda: SimpleNamespace(shutdown=lambda: None),
        )

        app = main_module.app
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
        outbox_scope = _outbox_scope(source_reader.generation, execution_scope.key)
        source_reader.close()

        monitor_record = None
        owner_record = None
        forced_processes: list[str] = []
        cleanup_failures: list[str] = []
        trade_id: str | None = None
        previous_overrides = dict(app.dependency_overrides)
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://afi13-ai-paper.integration",
            ) as client:
                monkeypatch.setattr(main_module.settings, "SERVICE_READ_DEPLOYMENT_JSON", None)
                async with main_module.lifespan(app):
                    created = await client.post(
                        "/api/v1/paper-trading/accounts",
                        headers=headers,
                        json={
                            "name": "AI confirmed managed paper",
                            "initial_cash": _AI_INITIAL_CASH,
                            "commission_rate": 0.001,
                            "slippage_rate": 0,
                        },
                    )
                    assert created.status_code == 200, created.text
                    assert created.headers["X-Paper-Data-Source"] == "legacy"
                    legacy_account_id = created.json()["id"]

                owner_raw_config = paper_host._owner_service_config(
                    endpoint, monitor_endpoint
                )
                owner_ports = owner_raw_config["components"]["execution"]["settings"][
                    "bind_ports"
                ]
                owner_ports.append("service.order_query")
                raw_monitor_config = paper_host._monitor_service_config(monitor_endpoint)
                monitor_record = _start_process(
                    context,
                    paper_host._monitor_host_process,
                    (
                        raw_monitor_config,
                        str(tmp_path / "afi13-ai-paper-monitor-registry.sqlite3"),
                        str(tmp_path / "afi13-ai-paper-monitor-outbox.sqlite3"),
                        outbox_scope,
                        monitor_keys,
                        owner_keys,
                    ),
                    "afi13-ai-paper-monitor-host",
                )
                monitor_ready = _receive(monitor_record["pipe"])
                assert monitor_ready == (
                    "ready",
                    monitor_endpoint,
                    "started",
                    ("monitor",),
                )
                owner_record = _start_process(
                    context,
                    _ai_owner_host_process,
                    (
                        owner_raw_config,
                        str(tmp_path / "afi13-ai-paper-owner-registry.sqlite3"),
                        paths,
                        owner_keys,
                        monitor_keys,
                        quote_cursor_counter,
                    ),
                    "afi13-ai-paper-execution-risk-owner-host",
                )
                owner_ready = _receive(owner_record["pipe"])
                assert owner_ready[0] == "ready", owner_ready
                assert owner_ready[1:3] == (endpoint, "started")
                assert owner_ready[3] == {
                    paper_host._SERVICE_INSTANCE: "execution_risk_monitor"
                }
                assert owner_ready[4] == 1 and owner_ready[5] == {}

                profile = _paper_service_profile(
                    endpoint=endpoint,
                    subject=owner_subject,
                    legacy_account_id=legacy_account_id,
                    owner_keys=owner_keys,
                )
                monkeypatch.setattr(
                    main_module.settings,
                    "SERVICE_READ_DEPLOYMENT_JSON",
                    SecretStr(
                        json.dumps(
                            {"schema_version": 1, "subjects": [profile]},
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                    ),
                )

                ai_service = AITradingService()
                ai_service.risk_guard = TradingRiskGuard(
                    TradingRiskConfig(
                        require_confirmation_above=0.0,
                        max_single_trade_amount=1000.0,
                        max_position_ratio=0.3,
                        min_confidence_threshold=0.3,
                    )
                )

                async def synthetic_local_parser(*, user_input: str, market_context: Any):
                    assert user_input == "Buy 0.25 BTC for my paper account"
                    assert isinstance(market_context, str)
                    return TradingIntent(
                        action=TradeAction.BUY,
                        symbol="BTC-USDT",
                        quantity=0.25,
                        order_type="market",
                        confidence=0.99,
                        risk_level=RiskLevel.LOW,
                        reason="deterministic local integration intent",
                        raw_input=user_input,
                        additional_params={
                            "reference_price": 10.02,
                            "contract_multiplier": 1,
                        },
                    )

                async def no_confirmation_push(*_args: Any, **_kwargs: Any) -> None:
                    return None

                ai_service._notify_confirmation_needed = no_confirmation_push
                app.dependency_overrides[ai_api.get_ai_trading_service] = (
                    lambda: ai_service
                )
                monkeypatch.setattr(
                    ai_service_module, "parse_trading_intent", synthetic_local_parser
                )

                async with main_module.lifespan(app):
                    deployment = app.state.service_read_deployment
                    assert deployment is not None
                    assert len(deployment._workers) == 1
                    worker_thread = deployment._workers[0]._thread
                    assert worker_thread is not None and worker_thread.is_alive()
                    cutover = await client.post(
                        f"/api/v1/paper-trading/accounts/{legacy_account_id}/managed-cutover",
                        headers=headers,
                        json={"confirmed": True},
                    )
                    assert cutover.status_code == 200, cutover.text
                    assert cutover.headers["X-Paper-Data-Source"] == "managed_paper"
                    assert cutover.json()["state"] == "ACTIVE"
                    assert cutover.json()["source_counts"] == {
                        "orders": 0,
                        "positions": 0,
                        "trades": 0,
                    }

                    pending = await client.post(
                        "/api/v1/ai-trading/execute",
                        headers=headers,
                        json={
                            "message": "Buy 0.25 BTC for my paper account",
                            "account_id": legacy_account_id,
                            "dry_run": True,
                            "auto_confirm": False,
                        },
                    )
                    assert pending.status_code == 200, pending.text
                    pending_body = pending.json()
                    assert pending_body["status"] == TradeStatus.PENDING_CONFIRMATION.value
                    assert pending_body["requires_confirmation"] is True
                    assert pending_body["execution_result"] is None
                    trade_id = pending_body["trade_id"]

                    confirmed = await client.post(
                        "/api/v1/ai-trading/confirm",
                        headers=headers,
                        json={"trade_id": trade_id, "confirmed": True},
                    )
                    assert confirmed.status_code == 200, confirmed.text
                    result = confirmed.json()
                    assert result["trade_id"] == trade_id
                    assert result["status"] == TradeStatus.FILLED.value
                    execution = result["execution_result"]
                    assert execution["type"] == "managed_paper_trade"
                    assert execution["state"] == "FILLED"
                    assert execution["source"] == "managed_paper"
                    assert execution["submission_acknowledged"] is True
                    assert execution["filled_size"] == 0.25
                    assert execution["avg_fill_price"] == 10.02
                    assert execution["commission"] == 0.0025

                    from app.services.managed_paper_adapter import _stable_intent_id

                    expected_intent_id = _stable_intent_id(
                        "ai", owner_subject, legacy_account_id, trade_id
                    )
                    assert execution["intent_id"] == expected_intent_id
                    readback = await client.get(
                        f"/api/v1/paper-trading/managed-orders/{expected_intent_id}",
                        headers=headers,
                        params={"account_id": legacy_account_id},
                    )
                    assert readback.status_code == 200, readback.text
                    assert readback.headers["X-Paper-Data-Source"] == "managed_paper"
                    assert readback.json()["intent_id"] == expected_intent_id
                    assert readback.json()["state"] == "FILLED"

                monitor_record["pipe"].send(("rows",))
                rows_reply = _receive(monitor_record["pipe"])
                assert rows_reply[0] == "rows" and rows_reply[1] is False
                rows = rows_reply[2]
                assert rows
                assert all(row[2] == "execution.lifecycle" for row in rows)
                assert all(
                    row[3] == paper_host._SERVICE_PRINCIPAL
                    and row[4] == "sdk.execution_monitor_projection.v2"
                    and row[5] == paper_host._ACCOUNT_REF
                    and row[6] == paper_host._STRATEGY_REF
                    for row in rows
                )
                reader = ExecutionJournalReader(paths["execution"], scope=execution_scope)
                try:
                    events = reader.read_page("execution", limit=100).events
                    assert events
                    assert {row[7] for row in rows} == {event.event_id for event in events}
                finally:
                    reader.close()

                    assert trade_id not in ai_service_module._pending_trades

                owner_closed = _close_normally(owner_record, expected_tag="closed")
                assert owner_closed[1:3] == ("closed", ())
                monitor_closed = _close_normally(monitor_record, expected_tag="closed")
                assert monitor_closed[1:2] == ("closed",)
        finally:
            if trade_id is not None:
                ai_service_module._pending_trades.pop(trade_id, None)
            app.dependency_overrides.clear()
            app.dependency_overrides.update(previous_overrides)
            for record in (owner_record, monitor_record):
                if record is not None and not record["closed"]:
                    _bounded_reap_spawned_process(
                        record,
                        forced_processes,
                        cleanup_failures,
                    )
            _assert_no_fallback_cleanup(forced_processes, cleanup_failures)
            await account_engine.dispose()

    asyncio.run(run_http_flow())
