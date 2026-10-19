"""Signed AI-strategy paper start through real Backtrader and SDK hosts.

The test starts only local processes and synthetic CSV/quote sources. The
public strategy-start route verifies and promotes a signed research snapshot;
the normal workspace runner then executes its materialized strategy through
Backtrader's managed ServiceExecution session and real Execution/Risk/Monitor
ServiceHosts.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import importlib
import importlib.metadata
import importlib.util
import json
import multiprocessing
import sys
import time
import uuid
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import zmq
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


_PROFILE_REF = "afi13-ai-research-paper-profile-v1"
_OWNER_PROFILE_REF = "afi13-ai-research-paper-owner-v1"
_QUERY_SCOPE_REF = "paper-risk-strategy-order-query-v1"
_CLIENT_SECURITY_REF = "paper-risk-strategy-client-curve-v1"
_TRANSPORT_REF = "paper-risk-strategy-client-connect-v1"
_INTENT_ID = "afi13-ai-research-managed-buy-v1"
_SIGNAL_ID = "afi13-ai-research-signal-v1"
_STRATEGY_PRINCIPAL_REF = "paper-host-strategy-principal"
_PAPER_OWNER_POLICY_PRINCIPAL_REF = "paper-host-owner-policy-principal"
_PAPER_OWNER_POLICY_ACTOR_REF = "paper-host-owner-policy-application"
_INTENT_CONTRACT_REF = "bt-cerebro-fill-metadata-r1"
_INITIAL_CASH = 100.0
_INITIAL_QUANTITY = "0.25"
_DUPLICATE_MARKER = "AFI13_STRATEGY_DUPLICATE_SUBMITTED"


def _curve_keys() -> dict[str, bytes]:
    server_public, server_secret = zmq.curve_keypair()
    application_public, application_secret = zmq.curve_keypair()
    return {
        "server_public": server_public,
        "server_secret": server_secret,
        "application_public": application_public,
        "application_secret": application_secret,
    }


def _strategy_source() -> str:
    """An AI-generated strategy with one real submit and one idempotent replay."""

    return '''
AFI_SERVICE_INTENT_CONTRACT_REF = "bt-cerebro-fill-metadata-r1"

import backtrader as bt


class GeneratedPaperStrategy(bt.Strategy):
    def __init__(self):
        self._submitted = False
        self._duplicate_submitted = False

    def next(self):
        if not self._submitted:
            self._submitted = True
            self.buy(
                data=self.data,
                size=0.25,
                price=None,
                exectype=bt.Order.Market,
                time_in_force="FOK",
                managed_intent_id="afi13-ai-research-managed-buy-v1",
                managed_signal_id="afi13-ai-research-signal-v1",
                managed_instrument="BTC-USDT",
                managed_position_effect="OPEN",
                managed_metadata_version="paper-instrument-v1",
                reduce_only=False,
            )

    def notify_order(self, order):
        if order.status == order.Completed and not self._duplicate_submitted:
            self._duplicate_submitted = True
            self.buy(
                data=self.data,
                size=0.25,
                price=None,
                exectype=bt.Order.Market,
                time_in_force="FOK",
                managed_intent_id="afi13-ai-research-managed-buy-v1",
                managed_signal_id="afi13-ai-research-signal-v1",
                managed_instrument="BTC-USDT",
                managed_position_effect="OPEN",
                managed_metadata_version="paper-instrument-v1",
                reduce_only=False,
            )
            print("AFI13_STRATEGY_DUPLICATE_SUBMITTED", flush=True)
'''.lstrip()


def _strategy_owner_host_process(
    control,
    raw_config: dict[str, Any],
    registry_path: str,
    paths: dict[str, str],
    owner_keys: dict[str, bytes],
    monitor_keys: dict[str, bytes],
    quote_cursor_counter,
) -> None:
    """Serve the R13 durable graph with exact STRATEGY_CLIENT ACL grants."""

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
        query_grant = ResourceGrant(
            paper_host._SERVICE_INSTANCE,
            "service.order_query",
            _QUERY_SCOPE_REF,
            paper_host._WIRE_SCOPE,
            frozenset({"orders.query"}),
        )
        # ServiceHost requires one exact application submit grant to install
        # the trusted paper-owner write policy.  Keep it on its own unused
        # identity; the materialized strategy connects only as STRATEGY_CLIENT.
        paper_owner_policy_public, _paper_owner_policy_secret = zmq.curve_keypair()
        paper_owner_identity = WireIdentity(
            WireRole.APPLICATION_CLIENT,
            _PAPER_OWNER_POLICY_PRINCIPAL_REF,
            _PAPER_OWNER_POLICY_ACTOR_REF,
        )
        paper_owner_submit_grant = ResourceGrant(
            paper_host._SERVICE_INSTANCE,
            "service.order_entry",
            paper_host._ORDER_SCOPE_REF,
            paper_host._WIRE_SCOPE,
            frozenset({"orders.submit"}),
        )
        monitor_endpoint = raw_config["communication"]["profiles"][
            paper_host._MONITOR_CONNECT_REF
        ]["endpoints"]["rpc_connect"]
        strategy_identity = WireIdentity(
            WireRole.STRATEGY_CLIENT,
            _STRATEGY_PRINCIPAL_REF,
            paper_host._STRATEGY_REF,
        )
        strategy_order_grant = ResourceGrant(
            paper_host._SERVICE_INSTANCE,
            "service.order_entry",
            paper_host._ORDER_SCOPE_REF,
            paper_host._WIRE_SCOPE,
            frozenset({"orders.submit", "orders.cancel"}),
        )
        host = ServiceHost(
            config,
            TrustedServiceAuthority(
                identity=owner_identity,
                scope=WireScope(paper_host._DOMAIN_REF),
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
                    owner_keys["application_public"]: _STRATEGY_PRINCIPAL_REF,
                    paper_owner_policy_public: _PAPER_OWNER_POLICY_PRINCIPAL_REF,
                },
                grants={
                    _STRATEGY_PRINCIPAL_REF: (
                        PrincipalGrant(
                            strategy_identity,
                            (strategy_order_grant, query_grant),
                        ),
                    ),
                    _PAPER_OWNER_POLICY_PRINCIPAL_REF: (
                        PrincipalGrant(
                            paper_owner_identity,
                            (paper_owner_submit_grant,),
                        ),
                    ),
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
                paper_host._plain_host_components(host),
                dict(host.summary["paper"]["compositions"]),
                host.summary["paper"]["monitor_relays"],
                dict(host.summary["paper"]["monitor_faults"]),
            )
        )
        while True:
            if control.poll(0.01):
                command = control.recv()
                if command == ("snapshot",):
                    scope = graph["execution_scope"]
                    record = graph["store"].get(_INTENT_ID, scope=scope)
                    receipt = graph["store"].get_paper_match_receipt(
                        _INTENT_ID, scope=scope
                    )
                    book = graph["store"].read_paper_book(scope)
                    risk_snapshot = graph["gate"].snapshot(graph["account_scope"])
                    control.send(
                        (
                            "snapshot",
                            {
                                "state": None if record is None else record.state.value,
                                "dispatch_attempts": (
                                    None if record is None else record.dispatch_attempts
                                ),
                                "permit_reference": (
                                    None if record is None else record.permit_reference
                                ),
                                "receipt_state": (
                                    None if receipt is None else receipt.state.value
                                ),
                                "event_id": None if receipt is None else receipt.event_id,
                                "receipt_count": book.receipt_count,
                                "execution_cursor": book.execution_cursor,
                                "generation": book.execution_journal_generation,
                                "cash": str(book.cash),
                                "positions": tuple(
                                    (row.instrument, str(row.quantity))
                                    for row in book.positions
                                ),
                                "quote_calls": tuple(graph["quote_source"].calls),
                                "quote_cursor": graph["quote_source"].cursor,
                                "risk_increase_count": risk_snapshot["increase_count"],
                                "risk_increase_notional": str(
                                    risk_snapshot["increase_notional"]
                                ),
                                "risk_policy_fingerprint": risk_snapshot[
                                    "policy_fingerprint"
                                ],
                                "healthy": host.healthy,
                                "monitor_faults": dict(
                                    host.summary["paper"]["monitor_faults"]
                                ),
                                "fees": str(book.cumulative_fees),
                                "fill_quantity": (
                                    None if receipt is None else str(receipt.quantity)
                                ),
                                "fill_price": (
                                    None if receipt is None or receipt.fill_price is None
                                    else str(receipt.fill_price)
                                ),
                                "fee_amount": (
                                    None if receipt is None else str(receipt.fee_amount)
                                ),
                                "fee_currency": (
                                    None if receipt is None else receipt.fee_currency
                                ),
                                "quote_source_ref": (
                                    None
                                    if receipt is None or receipt.quote is None
                                    else receipt.quote.source_ref
                                ),
                                "quote_source_generation": (
                                    None
                                    if receipt is None or receipt.quote is None
                                    else receipt.quote.generation
                                ),
                                "quote_source_cursor": (
                                    None
                                    if receipt is None or receipt.quote is None
                                    else receipt.quote.cursor
                                ),
                                "risk_command": (
                                    None
                                    if (command := graph["journal"].get_command(
                                        graph["account_scope"],
                                        _STRATEGY_PRINCIPAL_REF,
                                        _INTENT_ID,
                                    )) is None
                                    else dict(command.canonical_payload)
                                ),
                            },
                        )
                    )
                    continue
                if command == ("close",):
                    host.close()
                    control.send(
                        ("closed", host.state, tuple(host.summary["cleanup_failures"]))
                    )
                    break
                raise AssertionError("unknown strategy paper owner command")
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


def _strategy_profile_plugin(
    plugin_root: Path,
    *,
    endpoint: str,
    owner_keys: dict[str, bytes],
    owner_subject: str,
    runner_sha256: str,
    profile_module_name: str,
    owner_module_name: str,
    legacy_gateway_key: str,
) -> None:
    plugin_root.mkdir(parents=True, exist_ok=True)
    distribution = plugin_root / "afi13_strategy_paper_test-1.0.dist-info"
    distribution.mkdir()
    (distribution / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: afi13-strategy-paper-test\nVersion: 1.0\n",
        encoding="utf-8",
    )
    (distribution / "entry_points.txt").write_text(
        "[ai_for_investor.service_strategy_profiles]\n"
        f"{_PROFILE_REF} = {profile_module_name}:make_profile\n\n"
        "[bt_api_py.gateway_operator_profiles]\n"
        f"{_OWNER_PROFILE_REF} = {owner_module_name}:profile\n",
        encoding="utf-8",
    )
    fixture = {
        "endpoint": endpoint,
        "keys": {
            name: value.decode("ascii") for name, value in owner_keys.items()
        },
    }
    (plugin_root / "fixture.json").write_text(
        json.dumps(fixture, sort_keys=True), encoding="utf-8"
    )
    profile_source = '''
import json
from pathlib import Path

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_py.gateway_operator_application import GatewayOperatorProfile
from bt_api_py.service_runtime import ClientCurveKeys, ServiceClientRuntime, ZmqTransportFactory
from app.services.live_trading.service_execution_launch import TrustedServiceStrategyProfile

_FIXTURE = json.loads(Path(__file__).with_name("fixture.json").read_text(encoding="utf-8"))
_DOMAIN = "paper-host-domain-v1"
_SERVICE_ID = "paper-host-service"
_SERVICE_PRINCIPAL = "paper-host-service-principal"
_SERVICE_INSTANCE = "paper-host-owner"
_STRATEGY_REF = "paper-host-strategy"
_PRINCIPAL_REF = "paper-host-strategy-principal"
_ACCOUNT_REF = "paper-host-simulation-account"
_ORDER_SCOPE = "paper-host-orders-scope-v1"
_QUERY_SCOPE = "paper-risk-strategy-order-query-v1"
_SECURITY_REF = "paper-risk-strategy-client-curve-v1"
_TRANSPORT_REF = "paper-risk-strategy-client-connect-v1"


def _client_config(_context):
    bindings = [
        {
            "mount": "broker.orders",
            "service_id": _SERVICE_ID,
            "instance_id": _SERVICE_INSTANCE,
            "scope_ref": _ORDER_SCOPE,
            "transport_ref": _TRANSPORT_REF,
            "account_ref": _ACCOUNT_REF,
            "required": True,
        },
        {
            "mount": "broker.order_query",
            "service_id": _SERVICE_ID,
            "instance_id": _SERVICE_INSTANCE,
            "scope_ref": _QUERY_SCOPE,
            "transport_ref": _TRANSPORT_REF,
            "account_ref": _ACCOUNT_REF,
            "required": True,
        },
    ]
    return {
        "client_config_schema_version": 1,
        "process_role": "strategy_client",
        "registration": {"strategy_ref": _STRATEGY_REF},
        "clients": {
            "execution": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": bindings,
                    "options": {"request_timeout_ms": 2000},
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                _TRANSPORT_REF: {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_connect": _FIXTURE["endpoint"]},
                    "security_profile_ref": _SECURITY_REF,
                    "options": {
                        "request_timeout_ms": 2000,
                        "high_water_mark": 32,
                        "max_message_bytes": 65536,
                    },
                }
            },
        },
    }


def _runtime(config, context):
    keys = {name: value.encode("ascii") for name, value in _FIXTURE["keys"].items()}
    return ServiceClientRuntime(
        config,
        identity=WireIdentity(WireRole.STRATEGY_CLIENT, _PRINCIPAL_REF, _STRATEGY_REF),
        run_id=context.run_id,
        scope_resolver=lambda _scope_ref: WireScope(
            _DOMAIN, account_ref=_ACCOUNT_REF, strategy_ref=_STRATEGY_REF
        ),
        peer_resolver=lambda _service_id: WireIdentity(
            WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID
        ),
        transport_factory=ZmqTransportFactory(
            {
                _SECURITY_REF: ClientCurveKeys(
                    keys["server_public"], keys["application_public"], keys["application_secret"]
                )
            }
        ),
        requested_operations={
            "broker.orders": frozenset({"orders.submit", "orders.cancel"}),
            "broker.order_query": frozenset({"orders.query"}),
        },
    )


def make_profile():
    return TrustedServiceStrategyProfile(
        profile_ref="afi13-ai-research-paper-profile-v1",
        strategy_id="__server_attested_ai_research_paper__",
        script_sha256="@@RUNNER_SHA256@@",
        strategy_ref=_STRATEGY_REF,
        account_ref=_ACCOUNT_REF,
        principal_ref=_PRINCIPAL_REF,
        owner_profile_ref="afi13-ai-research-paper-owner-v1",
        intent_contract_ref="bt-cerebro-fill-metadata-r1",
        service_id=_SERVICE_ID,
        service_instance_id=_SERVICE_INSTANCE,
        transport_ref=_TRANSPORT_REF,
        orders_scope_ref=_ORDER_SCOPE,
        order_query_scope_ref=_QUERY_SCOPE,
        authorized_subjects=frozenset({"@@OWNER_SUBJECT@@"}),
        legacy_gateway_keys=("@@LEGACY_GATEWAY_KEY@@",),
        orders_operations=frozenset({"orders.submit", "orders.cancel"}),
        order_query_operations=frozenset({"orders.query"}),
        client_config_factory=_client_config,
        runtime_factory=_runtime,
        server_attested_ai_research_strategy_ids=True,
    )
'''.replace("@@RUNNER_SHA256@@", runner_sha256).replace(
        "@@OWNER_SUBJECT@@", owner_subject
    ).replace("@@LEGACY_GATEWAY_KEY@@", legacy_gateway_key)
    (plugin_root / f"{profile_module_name}.py").write_text(
        profile_source, encoding="utf-8"
    )
    (plugin_root / f"{owner_module_name}.py").write_text(
        "from bt_api_py.gateway_operator_application import GatewayOperatorProfile\n"
        f"profile = GatewayOperatorProfile({_OWNER_PROFILE_REF!r}, "
        "'account_session_worker', lambda: None)\n",
        encoding="utf-8",
    )


def _wait_for_log_marker(path: Path, marker: str, timeout_seconds: float = 20.0) -> str:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        if marker in text:
            return text
        if "AFI_SERVICE_EXECUTION_ERROR:" in text:
            raise AssertionError(text[-2000:])
        time.sleep(0.05)
    raise TimeoutError(f"strategy child missed expected local marker: {marker}")


async def _wait_for_owned_process_exit(manager, instance_id: str, timeout_seconds: float = 20.0) -> int:
    deadline = time.monotonic() + timeout_seconds
    observed_process = None
    while time.monotonic() < deadline:
        observed_process = manager._processes.get(instance_id) or observed_process
        if observed_process is not None and observed_process.returncode is not None:
            await observed_process.wait()
            return int(observed_process.returncode)
        await asyncio.sleep(0.05)
    raise TimeoutError("server-owned strategy child did not exit within the local bound")


def _finite_error_code(error: BaseException) -> str:
    code = getattr(error, "code", type(error).__name__)
    if type(code) is not str or not code.isidentifier() or len(code) > 64:
        return "UNCLASSIFIED"
    return code


class _OwnedChildCleanupError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def test_ai_research_strategy_start_runs_signed_backtrader_paper_through_risk_and_monitor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise signed AI-run promotion, the real BT adapter and durable E/R/M."""

    import app.main as main_module
    import app.config as app_config
    import app.services.quote_service as quote_service
    import app.utils.security as security
    import httpx
    import jwt
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from app.api.strategy import base as strategy_api
    from app.db import database, sql_repository
    from app.models.user import User
    from app.schemas.ai_strategy_research import (
        AIStrategyPaperTradingStartRequest,
        AIStrategyResearchRunRecord,
    )
    from app.schemas.workspace import WorkspaceCreate
    from app.services.live_trading import manager as manager_module
    from app.services.live_trading.service_execution_launch import (
        SERVICE_EXECUTION_AI_RESEARCH_PROFILE_ENV,
        SERVICE_EXECUTION_PAPER_RUNTIME_DIGEST_ENV,
        SERVICE_EXECUTION_PAPER_STRATEGY_SOURCE_DIGEST_ENV,
        SERVICE_EXECUTION_PROFILE_ENV,
        SERVICE_EXECUTION_PROFILE_MAP_ENV,
    )
    from app.services.live_trading.service_gateway_operator_guard import (
        register_legacy_gateway_exclusions,
        unregister_legacy_gateway_exclusions,
    )
    from app.services.paper_runtime_scheduler import (
        get_paper_runtime_snapshot_scheduler,
    )
    from app.services import workspace_unit_runtime
    from app.services.strategy import core as strategy_core
    from app.services.strategy import templates as strategy_templates
    from app.services import strategy_service as strategy_service_module
    from app.services.workspace import (
        lifecycle as workspace_lifecycle,
        optimization as workspace_optimization,
        reconciliation as workspace_reconciliation,
        reports as workspace_reports,
        run_ops as workspace_run_ops,
        units as workspace_units,
    )
    from app.services.ai_research_provenance import (
        ai_research_paper_materialized_runtime_digest,
    )
    from app.services.workspace_unit_runtime import _UNIT_RUN_PY
    from bt_api_execution import (
        PAPER_ENVIRONMENT,
        PAPER_PROVIDER,
        ExecutionScope,
        SqliteExecutionStore,
    )
    from bt_api_execution.journal_reader import ExecutionJournalReader
    from bt_api_monitor.durable import DurableOutbox
    from bt_api_py.service_execution_monitor import _outbox_scope
    from pydantic import SecretStr
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    async def run_http_flow() -> None:
        owner_subject = str(uuid.uuid4())
        run_id = str(uuid.uuid4())
        db_path = tmp_path / "afi13-strategy-app.sqlite3"
        execution_path = tmp_path / "afi13-strategy-execution.sqlite3"
        paths = {
            "execution": str(execution_path),
            "risk": str(tmp_path / "afi13-strategy-risk.sqlite3"),
            "risk-journal": str(tmp_path / "afi13-strategy-risk-journal.sqlite3"),
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
        for module in (
            workspace_lifecycle,
            workspace_optimization,
            workspace_reconciliation,
            workspace_reports,
            workspace_run_ops,
            workspace_units,
            strategy_core,
            strategy_service_module,
        ):
            if hasattr(module, "async_session_maker"):
                monkeypatch.setattr(module, "async_session_maker", account_sessions)

        async with account_engine.begin() as connection:
            await connection.run_sync(database.Base.metadata.create_all)

        migration_path = (
            Path(main_module.__file__).resolve().parents[1]
            / "alembic"
            / "versions"
            / "20261016_legacy_paper_cutover.py"
        )
        migration_spec = importlib.util.spec_from_file_location(
            "afi13_strategy_paper_cutover_migration", migration_path
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
                    username=f"paper-strategy-{owner_subject[:8]}",
                    email=f"paper-strategy-{owner_subject[:8]}@example.test",
                    hashed_password="synthetic-test-only-unused",
                )
            )
            await session.commit()

        jwt_secret = "afi13-strategy-http-test-jwt-secret"
        provenance_secret = "afi13-strategy-paper-provenance-test-secret-" + uuid.uuid4().hex
        monkeypatch.setattr(
            security,
            "settings",
            SimpleNamespace(JWT_SECRET_KEY=jwt_secret, JWT_ALGORITHM="HS256"),
        )
        monkeypatch.setattr(main_module.settings, "SECRET_KEY", provenance_secret)
        # Provenance signing reads the app.config singleton, so bind it before
        # the run is signed through the real AIStrategyResearchService.
        monkeypatch.setattr(app_config, "_settings", main_module.settings)
        monkeypatch.setattr(
            main_module.settings,
            "SERVICE_READ_DEPLOYMENT_JSON",
            None,
        )
        monkeypatch.delenv(SERVICE_EXECUTION_PROFILE_ENV, raising=False)
        monkeypatch.delenv(SERVICE_EXECUTION_PROFILE_MAP_ENV, raising=False)
        monkeypatch.delenv(SERVICE_EXECUTION_PAPER_RUNTIME_DIGEST_ENV, raising=False)
        monkeypatch.delenv(
            SERVICE_EXECUTION_PAPER_STRATEGY_SOURCE_DIGEST_ENV,
            raising=False,
        )
        assert set(AIStrategyPaperTradingStartRequest.model_fields) == {
            "research_workspace_id",
            "trading_workspace_id",
            "paper_workspace_name",
            "gateway_config",
        }
        token = jwt.encode(
            {
                "sub": owner_subject,
                "username": owner_subject,
                "token_type": "access",
                "exp": int(time.time()) + 600,
            },
            jwt_secret,
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

        # The only feed data is a local synthetic CSV consumed by the real
        # workspace-unit runner and Cerebro.
        data_root = tmp_path / "synthetic-market-data"
        data_dir = data_root / "future"
        data_dir.mkdir(parents=True)
        csv_path = data_dir / "BTC-USDT.csv"
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                ("datetime", "open", "high", "low", "close", "volume", "openinterest")
            )
            writer.writerows(
                (
                    ("2026-10-01", "10.00", "10.05", "9.95", "10.00", "10", "0"),
                    ("2026-10-02", "10.00", "10.10", "9.98", "10.04", "10", "0"),
                    ("2026-10-03", "10.04", "10.12", "10.00", "10.08", "10", "0"),
                    ("2026-10-04", "10.08", "10.14", "10.02", "10.10", "10", "0"),
                )
            )

        app = main_module.app
        app.dependency_overrides.clear()
        strategy_api.get_ai_strategy_research_service.cache_clear()
        workspace_root = tmp_path / "workspace_units"
        strategy_root = tmp_path / "strategies"
        monkeypatch.setattr(workspace_unit_runtime, "_WORKSPACE_UNITS_ROOT", workspace_root)
        monkeypatch.setattr(strategy_core, "STRATEGIES_DIR", strategy_root)
        monkeypatch.setattr(strategy_templates, "STRATEGIES_DIR", strategy_root)
        monkeypatch.setattr(strategy_service_module, "STRATEGIES_DIR", strategy_root)

        monkeypatch.setattr(manager_module, "_manager", None)
        monkeypatch.setattr(
            manager_module,
            "_INSTANCES_FILE",
            tmp_path / "live_trading_instances.json",
        )
        monkeypatch.setattr(manager_module, "_MANUAL_GATEWAYS_FILE", tmp_path / "manual_gateways.json")
        monkeypatch.setenv("LIVE_TRADING_RESTORE_MANUAL_GATEWAYS", "false")
        sdk_root = Path(__file__).resolve().parents[2]
        package_roots = tuple(
            sorted(
                (
                    sdk_root / "bt_api" / name / "src"
                    for name in (
                        "bt_api_base",
                        "bt_api_execution",
                        "bt_api_risk",
                        "bt_api_monitor",
                        "bt_api_transport_zmq",
                        "bt_api_gateway",
                    )
                ),
                key=lambda path: path.name,
            )
        )
        assert all(path.is_dir() for path in package_roots)
        monkeypatch.setattr(
            manager_module.LiveTradingManager,
            "_service_strategy_sdk_import_roots",
            staticmethod(lambda: (sdk_root, package_roots)),
        )

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

        raw_owner_config = paper_host._owner_service_config(endpoint, monitor_endpoint)
        raw_owner_config["components"]["execution"]["settings"]["bind_ports"].append(
            "service.order_query"
        )
        raw_monitor_config = paper_host._monitor_service_config(monitor_endpoint)
        monitor_record = None
        owner_record = None
        forced: list[str] = []
        cleanup_failures: list[str] = []
        profile_module_name = f"afi13_strategy_profile_{uuid.uuid4().hex}"
        owner_module_name = f"afi13_strategy_operator_{uuid.uuid4().hex}"
        plugin_root = tmp_path / "trusted-service-profiles"
        legacy_gateway_key = f"manual:PAPER:{paper_host._ACCOUNT_REF}"
        owner_token = object()
        owner_exclusions_registered = False
        manager = None
        instance_id: str | None = None
        strategy_runtime_run_id: str | None = None
        created_strategy_children: list[tuple[str, Any]] = []
        previous_overrides = dict(app.dependency_overrides)
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://afi13-strategy-paper.integration",
            ) as client:
                async with main_module.lifespan(app):
                    owner_workspace = None
                    # Persist the normal, achieved research-run record through
                    # the real service so the HTTP path has to verify its HMAC.
                    strategy_service = strategy_api.get_ai_strategy_research_service()
                    owner_workspace = await strategy_service.workspace_service.create_workspace(
                        owner_subject,
                        WorkspaceCreate(
                            name="AFI13 signed synthetic research",
                            description="local strategy-start integration source",
                            workspace_type="research",
                        ),
                    )
                    strategy_source = _strategy_source()
                    research_unit_id = str(uuid.uuid4())
                    strategy_snapshot_id = "signed-ai-generated-strategy"
                    data_config = {
                        "symbol": paper_host._RISK_INSTRUMENT,
                        "asset_type": "future",
                        "data_type": "csv",
                        "directory_path": str(data_dir),
                        "timeframe": "1d",
                        "timeframe_n": 1,
                        "start_date": "2026-10-01",
                        "end_date": "2026-10-04",
                        "use_end_date": True,
                        "range_type": "date",
                    }
                    now = datetime.now(timezone.utc).isoformat()
                    run_record = AIStrategyResearchRunRecord(
                        run_id=run_id,
                        prompt="Generate a local BTC paper strategy with managed metadata",
                        symbol=paper_host._RISK_INSTRUMENT,
                        symbol_name=paper_host._RISK_INSTRUMENT,
                        timeframe="1d",
                        timeframe_n=1,
                        status="completed",
                        achieved=True,
                        target_sharpe=0.0,
                        quality_gates={"require_robustness_validation": False},
                        iteration_count=1,
                        best_iteration=1,
                        best_strategy_name="Synthetic BTC managed strategy",
                        research_workspace_id=owner_workspace.id,
                        started_at=now,
                        completed_at=now,
                        iterations=[
                            {
                                "iteration": 1,
                                "passed": True,
                                "quality_score": 1.0,
                                "run_status": "completed",
                                "metrics": {"sharpe_ratio": 1.0, "total_trades": 1},
                                "strategy_snapshot": {
                                    "id": strategy_snapshot_id,
                                    "name": "Synthetic BTC managed strategy",
                                    "description": "Signed local integration strategy",
                                    "code": strategy_source,
                                    "params": {},
                                    "category": "future",
                                },
                                "unit_id": research_unit_id,
                                "unit_snapshot": {
                                    "id": research_unit_id,
                                    "workspace_id": owner_workspace.id,
                                    "group_name": "Synthetic BTC managed strategy",
                                    "strategy_name": "Synthetic BTC managed strategy",
                                    "symbol": paper_host._RISK_INSTRUMENT,
                                    "symbol_name": paper_host._RISK_INSTRUMENT,
                                    "timeframe": "1d",
                                    "timeframe_n": 1,
                                    "category": "future",
                                    "data_config": data_config,
                                    "unit_settings": {
                                        "initial_cash": _INITIAL_CASH,
                                        "commission": 0.001,
                                    },
                                    "params": {},
                                    "optimization_config": {},
                                    "gateway_config": {},
                                    "trading_mode": "paper",
                                    "lock_trading": False,
                                    "lock_running": False,
                                },
                            }
                        ],
                    )
                    owner_workspace = await strategy_service._persist_research_run_record(
                        owner_subject, owner_workspace, run_record
                    )
                    signed_run = owner_workspace.settings["ai_research"]["last_run"]
                    assert signed_run["run_id"] == run_id
                    assert signed_run["server_provenance_signature"]

                    # Materialization derives the feed root from workspace
                    # settings and appends the server-selected asset type.
                    # Create this local-only workspace through the real
                    # workspace service so the runner cannot fall back to a
                    # repository/global market-data directory.
                    trading_workspace = await strategy_service.workspace_service.create_workspace(
                        owner_subject,
                        WorkspaceCreate(
                            name="AFI13 synthetic paper feed",
                            description="local synthetic CSV root for strategy runtime",
                            workspace_type="trading",
                            settings={
                                "data_source": {
                                    "type": "csv",
                                    "csv": {"directory_path": str(data_root)},
                                }
                            },
                        ),
                    )

                    profile_module_sha = hashlib.sha256(_UNIT_RUN_PY.encode("utf-8")).hexdigest()
                    _strategy_profile_plugin(
                        plugin_root,
                        endpoint=endpoint,
                        owner_keys=owner_keys,
                        owner_subject=owner_subject,
                        runner_sha256=profile_module_sha,
                        profile_module_name=profile_module_name,
                        owner_module_name=owner_module_name,
                        legacy_gateway_key=legacy_gateway_key,
                    )
                    importlib.invalidate_caches()
                    if str(plugin_root) not in sys.path:
                        sys.path.insert(0, str(plugin_root))
                    monkeypatch.setenv(
                        SERVICE_EXECUTION_AI_RESEARCH_PROFILE_ENV,
                        _PROFILE_REF,
                    )
                    operator_deployment = {
                        "schema_version": 1,
                        "operators": [
                            {
                                "profile_ref": _OWNER_PROFILE_REF,
                                "account_ref": paper_host._ACCOUNT_REF,
                                "authorized_subjects": [owner_subject],
                                "operations": ["status", "start", "stop"],
                                "auto_start": False,
                                "legacy_gateway_keys": [legacy_gateway_key],
                            }
                        ],
                    }
                    assert len(operator_deployment["operators"]) == 1
                    assert operator_deployment["operators"][0]["auto_start"] is False
                    assert operator_deployment["operators"][0]["account_ref"] == (
                        paper_host._ACCOUNT_REF
                    )
                    raw_operator_deployment = json.dumps(
                        operator_deployment,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    monkeypatch.setattr(
                        main_module.settings,
                        "SERVICE_GATEWAY_OPERATOR_DEPLOYMENT_JSON",
                        SecretStr(raw_operator_deployment),
                    )
                    monkeypatch.setattr(
                        app_config,
                        "_settings",
                        main_module.settings,
                    )
                    register_legacy_gateway_exclusions(owner_token, (legacy_gateway_key,))
                    owner_exclusions_registered = True

                    monitor_record = _start_process(
                        context,
                        paper_host._monitor_host_process,
                        (
                            raw_monitor_config,
                            str(tmp_path / "afi13-strategy-monitor-registry.sqlite3"),
                            str(tmp_path / "afi13-strategy-monitor-outbox.sqlite3"),
                            outbox_scope,
                            monitor_keys,
                            owner_keys,
                        ),
                        "afi13-strategy-monitor-host",
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
                        _strategy_owner_host_process,
                        (
                            raw_owner_config,
                            str(tmp_path / "afi13-strategy-owner-registry.sqlite3"),
                            paths,
                            owner_keys,
                            monitor_keys,
                            quote_cursor_counter,
                        ),
                        "afi13-strategy-execution-risk-owner-host",
                    )
                    owner_ready = _receive(owner_record["pipe"])
                    assert owner_ready[0] == "ready", owner_ready
                    assert owner_ready[1:3] == (endpoint, "started")
                    assert owner_ready[3] == (
                        (
                            "execution",
                            paper_host._SERVICE_INSTANCE,
                            ("service.order_entry", "service.order_query"),
                            (
                                (
                                    "service.order_entry",
                                    ("orders.cancel", "orders.submit"),
                                ),
                                ("service.order_query", ("orders.query",)),
                            ),
                        ),
                        (
                            "risk",
                            paper_host._RISK_INSTANCE,
                            ("service.order_entry",),
                            (("service.order_entry", ("orders.submit",)),),
                        ),
                    )
                    assert owner_ready[4] == {
                        paper_host._SERVICE_INSTANCE: "execution_risk_monitor"
                    }
                    assert owner_ready[5] == 1 and owner_ready[6] == {}

                    start_payload = {
                        "research_workspace_id": owner_workspace.id,
                        "trading_workspace_id": trading_workspace.id,
                        "paper_workspace_name": "AFI13 signed synthetic paper",
                        "gateway_config": {},
                    }
                    assert set(start_payload) == set(
                        AIStrategyPaperTradingStartRequest.model_fields
                    )
                    manager = manager_module.get_live_trading_manager()
                    process_registry_before = {
                        key: id(process)
                        for key, process in manager._processes.items()
                    }
                    real_execution_start = manager_module.live_execution_service.start_instance

                    async def capture_test_owned_strategy_child(*args, **kwargs):
                        candidate_id = kwargs.get(
                            "instance_id",
                            args[0] if args else None,
                        )
                        processes = kwargs.get(
                            "processes",
                            args[8] if len(args) > 8 else None,
                        )
                        try:
                            return await real_execution_start(*args, **kwargs)
                        finally:
                            candidate = (
                                processes.get(candidate_id)
                                if isinstance(candidate_id, str)
                                and isinstance(processes, dict)
                                else None
                            )
                            if (
                                candidate is not None
                                and process_registry_before.get(candidate_id) != id(candidate)
                                and getattr(candidate, "_bt_service_execution", False) is True
                                and not any(
                                    child_id == candidate_id and child is candidate
                                    for child_id, child in created_strategy_children
                                )
                            ):
                                created_strategy_children.append(
                                    (candidate_id, candidate)
                                )

                    monkeypatch.setattr(
                        manager_module.live_execution_service,
                        "start_instance",
                        capture_test_owned_strategy_child,
                    )
                    response = await client.post(
                        f"/api/v1/strategy/ai-research/runs/{run_id}/paper-trading",
                        headers=headers,
                        json=start_payload,
                    )
                    assert response.status_code == 200, response.text
                    body = response.json()
                    assert body["started"] is True, body
                    assert body["workspace"]["workspace_type"] == "trading"
                    assert body["workspace"]["id"] == trading_workspace.id
                    assert body["run_record"]["run_id"] == run_id
                    assert body["run_record"]["paper_trading_started"] is True
                    assert body["run_record"]["best_strategy_id"]
                    assert body["unit"]["trading_mode"] == "paper"
                    assert body["unit"]["strategy_id"] == body["run_record"]["best_strategy_id"]
                    assert body["unit"]["trading_instance_id"]
                    instance_id = body["unit"]["trading_instance_id"]
                    matching_children = [
                        process
                        for child_id, process in created_strategy_children
                        if child_id == instance_id
                    ]
                    assert len(matching_children) == 1
                    strategy_child = matching_children[0]
                    assert manager._processes.get(instance_id) is strategy_child
                    assert getattr(strategy_child, "_bt_service_execution", False) is True
                    instance_row = manager_module._load_instances().get(instance_id)
                    assert isinstance(instance_row, dict)
                    assert instance_row.get("id") == instance_id
                    assert instance_row.get("user_id") == owner_subject
                    assert instance_row.get("_server_service_execution_managed") is True
                    assert instance_row.get("_server_service_execution_profile_ref") == _PROFILE_REF
                    assert type(instance_row.get("_server_service_execution_run_id")) is str
                    assert instance_row["_server_service_execution_run_id"]
                    strategy_runtime_run_id = instance_row[
                        "_server_service_execution_run_id"
                    ]
                    persisted_pid = instance_row.get("pid")
                    assert persisted_pid is None or (
                        type(persisted_pid) is int and persisted_pid == strategy_child.pid
                    )

                    runtime_dir = workspace_unit_runtime.unit_dir(
                        body["workspace"]["id"], body["unit"]["id"]
                    )
                    runtime_config = json.loads(
                        json.dumps(
                            __import__("yaml").safe_load(
                                (runtime_dir / "config.yaml").read_text(encoding="utf-8")
                            )
                        )
                    )
                    selected_module = runtime_config["workspace_unit"]["strategy_module"]
                    assert selected_module == "strategy_generated.py"
                    materialized_source = (runtime_dir / selected_module).read_text(
                        encoding="utf-8"
                    )
                    assert materialized_source == strategy_source
                    template_dir = strategy_root / str(body["unit"]["strategy_id"])
                    assert (template_dir / "strategy_generated.py").read_text(
                        encoding="utf-8"
                    ) == strategy_source
                    runtime_digest = ai_research_paper_materialized_runtime_digest(runtime_dir)
                    assert runtime_digest
                    anchor = body["unit"]["unit_settings"][
                        "ai_research_paper_runtime_anchor"
                    ]
                    assert anchor["runtime_snapshot_digest"] == runtime_digest

                    assert manager._gateways == {}
                    stdout_path = runtime_dir / "logs" / "subprocess.stdout.log"
                    stdout_text = _wait_for_log_marker(stdout_path, _DUPLICATE_MARKER)
                    assert "AFI_SERVICE_EXECUTION_READY:" in stdout_text
                    return_code = await _wait_for_owned_process_exit(manager, instance_id)
                    assert return_code == 0

                    owner_record["pipe"].send(("snapshot",))
                    first_reply = _receive(owner_record["pipe"])
                    assert first_reply[0] == "snapshot", first_reply
                    first = first_reply[1]
                    assert first["state"] == "FILLED"
                    assert first["dispatch_attempts"] == 1
                    assert first["permit_reference"]
                    assert first["receipt_state"] == "FILLED"
                    assert first["event_id"]
                    assert first["receipt_count"] == 1
                    assert first["execution_cursor"] > 0
                    assert first["positions"] == ((paper_host._RISK_INSTRUMENT, "0.25"),)
                    assert first["fill_quantity"] == _INITIAL_QUANTITY
                    assert first["fill_price"] == "10.02"
                    assert first["fee_amount"] == "0.0025"
                    assert first["fee_currency"] == "USDT"
                    assert first["fees"] == first["fee_amount"]
                    assert first["quote_source_ref"] == "paper-host-synthetic-quotes-v1"
                    assert first["quote_source_generation"] == "paper-host-quote-generation-v1"
                    assert first["quote_source_cursor"] == 1
                    assert first["quote_calls"] == (paper_host._RISK_INSTRUMENT,)
                    assert first["quote_cursor"] == 1
                    assert first["risk_increase_count"] == 1
                    assert float(first["risk_increase_notional"]) > 0
                    assert first["risk_policy_fingerprint"]
                    risk_command = first["risk_command"]
                    assert risk_command is not None
                    assert risk_command["intent_id"] == _INTENT_ID
                    assert risk_command["signal_id"] == _SIGNAL_ID
                    assert risk_command["instrument"] == paper_host._RISK_INSTRUMENT
                    assert risk_command["side"] == "BUY"
                    assert risk_command["position_effect"] == "OPEN"
                    assert risk_command["metadata_version"] == "paper-instrument-v1"
                    assert risk_command["quantity"] == _INITIAL_QUANTITY
                    assert risk_command["reduce_only"] is False
                    assert risk_command["trusted_metadata_digest"]
                    assert first["healthy"] is True
                    assert first["monitor_faults"] == {}

                    monitor_record["pipe"].send(("rows",))
                    first_rows_reply = _receive(monitor_record["pipe"])
                    assert first_rows_reply[0] == "rows" and first_rows_reply[1] is False
                    first_rows = first_rows_reply[2]
                    assert first_rows
                    assert len({row[1] for row in first_rows}) == len(first_rows)
                    assert all(row[2] == "execution.lifecycle" for row in first_rows)
                    assert all(
                        row[3] == paper_host._SERVICE_PRINCIPAL
                        and row[4] == "sdk.execution_monitor_projection.v2"
                        and row[5] == paper_host._ACCOUNT_REF
                        and row[6] == paper_host._STRATEGY_REF
                        for row in first_rows
                    )
                    source_reader = ExecutionJournalReader(
                        paths["execution"], scope=execution_scope
                    )
                    try:
                        source_events = source_reader.read_page(
                            "execution", limit=100
                        ).events
                        assert source_events
                        events_by_id = {event.event_id: event for event in source_events}
                        assert {row[7] for row in first_rows} == set(events_by_id)
                        assert first["event_id"] in events_by_id
                        assert events_by_id[first["event_id"]].intent_id == _INTENT_ID
                        assert all(
                            events_by_id[row[7]].intent_id == _INTENT_ID
                            for row in first_rows
                        )
                    finally:
                        source_reader.close()

                    owner_closed = _close_normally(
                        owner_record, expected_tag="closed"
                    )
                    assert owner_closed[1:3] == ("closed", ())
                    owner_record = _start_process(
                        context,
                        _strategy_owner_host_process,
                        (
                            raw_owner_config,
                            str(tmp_path / "afi13-strategy-owner-restart-registry.sqlite3"),
                            paths,
                            owner_keys,
                            monitor_keys,
                            quote_cursor_counter,
                        ),
                        "afi13-strategy-execution-risk-owner-restarted",
                    )
                    restarted_ready = _receive(owner_record["pipe"])
                    assert restarted_ready[0] == "ready", restarted_ready
                    assert restarted_ready[1:3] == (endpoint, "started")
                    assert restarted_ready[3] == owner_ready[3]
                    assert restarted_ready[4] == {
                        paper_host._SERVICE_INSTANCE: "execution_risk_monitor"
                    }
                    assert restarted_ready[5] == 1 and restarted_ready[6] == {}
                    owner_record["pipe"].send(("snapshot",))
                    second_reply = _receive(owner_record["pipe"])
                    assert second_reply[0] == "snapshot", second_reply
                    second = second_reply[1]
                    for field in (
                        "state",
                        "dispatch_attempts",
                        "permit_reference",
                        "receipt_state",
                        "event_id",
                        "receipt_count",
                        "execution_cursor",
                        "generation",
                        "cash",
                        "positions",
                        "quote_cursor",
                        "risk_increase_count",
                        "risk_increase_notional",
                        "risk_policy_fingerprint",
                        "healthy",
                        "monitor_faults",
                        "fees",
                        "fill_quantity",
                        "fill_price",
                        "fee_amount",
                        "fee_currency",
                        "quote_source_ref",
                        "quote_source_generation",
                        "quote_source_cursor",
                        "risk_command",
                    ):
                        assert second[field] == first[field], field
                    assert second["quote_calls"] == ()
                    monitor_record["pipe"].send(("rows",))
                    second_rows_reply = _receive(monitor_record["pipe"])
                    assert second_rows_reply == first_rows_reply
                    persisted_outbox = DurableOutbox(
                        str(tmp_path / "afi13-strategy-monitor-outbox.sqlite3"),
                        read_only=True,
                    )
                    persisted, has_more = persisted_outbox.read_page_types(
                        outbox_scope,
                        ("execution.lifecycle", "execution.cancellation"),
                        0,
                        100,
                    )
                    assert has_more is False
                    assert tuple(
                        (row.sequence, row.event.event_id, row.event.event_type)
                        for row in persisted
                    ) == tuple((row[0], row[1], row[2]) for row in first_rows)

                    owner_closed = _close_normally(
                        owner_record, expected_tag="closed"
                    )
                    assert owner_closed[1:3] == ("closed", ())
                    monitor_closed = _close_normally(
                        monitor_record, expected_tag="closed"
                    )
                    assert monitor_closed[1:] == ("closed", ())
        finally:
            active_error = sys.exc_info()[1]
            cleanup_receipt_path = tmp_path / "afi13-strategy-paper-cleanup-receipt.json"
            cleanup_receipt: dict[str, Any] = {
                "schema_version": 1,
                "status": "no_strategy_child_created",
                "children": [],
                "cleanup_errors": [],
            }
            cleanup_errors: list[str] = []
            current_manager = manager or manager_module.get_existing_live_trading_manager()
            if created_strategy_children:
                cleanup_receipt["status"] = "strategy_children_recorded"
            for child_id, child_process in created_strategy_children:
                child_receipt: dict[str, Any] = {
                    "instance_id": child_id,
                    "pid": child_process.pid,
                    "profile_ref": _PROFILE_REF,
                    "run_id": (
                        strategy_runtime_run_id
                        if child_id == instance_id
                        else None
                    ),
                    "stop_requested": False,
                    "status": "cleanup_unverified",
                    "returncode": child_process.returncode,
                }
                stop_error_code: str | None = None
                wait_error_code: str | None = None
                try:
                    if current_manager is None:
                        raise _OwnedChildCleanupError("TEST_OWNED_MANAGER_UNAVAILABLE")
                    if instance_id is not None and child_id != instance_id:
                        raise _OwnedChildCleanupError("TEST_OWNED_INSTANCE_MISMATCH")
                    if getattr(child_process, "_bt_service_execution", False) is not True:
                        raise _OwnedChildCleanupError("TEST_OWNED_PROCESS_MARKER_MISSING")
                    current_process = current_manager._processes.get(child_id)
                    if current_process is not None and current_process is not child_process:
                        raise _OwnedChildCleanupError(
                            "TEST_OWNED_PROCESS_IDENTITY_CHANGED"
                        )
                    persisted = manager_module._load_instances().get(child_id)
                    if (
                        not isinstance(persisted, dict)
                        or persisted.get("id") != child_id
                        or persisted.get("user_id") != owner_subject
                    ):
                        raise _OwnedChildCleanupError("TEST_OWNED_INSTANCE_ROW_MISMATCH")
                    persisted_pid = persisted.get("pid")
                    if persisted_pid is not None and (
                        type(persisted_pid) is not int
                        or persisted_pid != child_process.pid
                    ):
                        raise _OwnedChildCleanupError("TEST_OWNED_INSTANCE_PID_MISMATCH")
                    persisted_run_id = persisted.get(
                        "_server_service_execution_run_id"
                    )
                    if persisted.get("_server_service_execution_managed") is True:
                        if (
                            persisted.get("_server_service_execution_profile_ref")
                            != _PROFILE_REF
                            or type(persisted_run_id) is not str
                            or not persisted_run_id
                        ):
                            raise _OwnedChildCleanupError(
                                "TEST_OWNED_INSTANCE_ROW_MISMATCH"
                            )
                        child_receipt["run_id"] = persisted_run_id
                    elif (
                        str(persisted.get("status") or "").strip().casefold()
                        != "stopped"
                        or persisted_pid is not None
                        or persisted.get("_server_service_execution_profile_ref")
                        is not None
                        or persisted_run_id is not None
                        or not isinstance(strategy_runtime_run_id, str)
                        or not strategy_runtime_run_id
                    ):
                        raise _OwnedChildCleanupError(
                            "TEST_OWNED_INSTANCE_ROW_MISMATCH"
                        )
                    else:
                        # Normal service exit clears profile/run metadata. Bind
                        # that terminal row to the captured managed run ID.
                        child_receipt["run_id"] = strategy_runtime_run_id
                    if (
                        strategy_runtime_run_id is not None
                        and child_id == instance_id
                        and strategy_runtime_run_id != child_receipt["run_id"]
                    ):
                        raise _OwnedChildCleanupError("TEST_OWNED_RUN_ID_CHANGED")
                    if child_process.returncode is not None:
                        child_receipt["lifecycle"] = "already_exited"
                    elif persisted_pid == child_process.pid:
                        if current_process is not child_process:
                            raise _OwnedChildCleanupError(
                                "TEST_OWNED_PROCESS_IDENTITY_CHANGED"
                            )
                        child_receipt["stop_requested"] = True
                        child_receipt["lifecycle"] = "test_requested_stop"
                        stop_result = await asyncio.wait_for(
                            current_manager.stop_instance(
                                child_id,
                                user_id=owner_subject,
                                allow_server_owned_ai_research_stop=True,
                                expected_service_execution_profile_ref=_PROFILE_REF,
                                expected_service_execution_run_id=child_receipt["run_id"],
                                expected_service_execution_process=child_process,
                            ),
                            timeout=35.0,
                        )
                        child_receipt["stop_result_type"] = type(stop_result).__name__
                    else:
                        # pid=None cannot authorize a stop. Wait only on this
                        # test-captured process for its natural synthetic exit.
                        child_receipt["lifecycle"] = "natural_exit_wait"
                except BaseException as cleanup_error:
                    stop_error_code = _finite_error_code(cleanup_error)
                    child_receipt["stop_error_code"] = stop_error_code
                try:
                    exit_code = await asyncio.wait_for(child_process.wait(), timeout=5.0)
                except BaseException as cleanup_error:
                    wait_error_code = _finite_error_code(cleanup_error)
                    child_receipt["wait_error_code"] = wait_error_code
                    child_receipt["status"] = "cleanup_failed"
                else:
                    child_receipt["returncode"] = int(exit_code)
                    child_receipt["status"] = (
                        "exited_after_cleanup_error"
                        if stop_error_code is not None
                        else (
                            "stopped_after_test_request"
                            if child_receipt["stop_requested"]
                            else "exited_naturally"
                        )
                    )
                    if exit_code != 0:
                        child_receipt["exit_note"] = "child_exited_nonzero"
                        cleanup_errors.append("STRATEGY_CHILD_EXIT_NONZERO")
                if stop_error_code is not None:
                    cleanup_errors.append(stop_error_code)
                if wait_error_code is not None:
                    cleanup_errors.append(wait_error_code)
                cleanup_receipt["children"].append(child_receipt)
            if cleanup_receipt["children"] and not cleanup_errors:
                cleanup_receipt["status"] = "strategy_children_exited"

            if owner_exclusions_registered:
                try:
                    unregister_legacy_gateway_exclusions(owner_token)
                except BaseException as cleanup_error:
                    cleanup_errors.append(_finite_error_code(cleanup_error))
            app.dependency_overrides.clear()
            app.dependency_overrides.update(previous_overrides)
            for record in (owner_record, monitor_record):
                if record is not None and not record["closed"]:
                    try:
                        _bounded_reap_spawned_process(record, forced, cleanup_failures)
                    except BaseException as cleanup_error:
                        cleanup_errors.append(_finite_error_code(cleanup_error))
            try:
                _assert_no_fallback_cleanup(forced, cleanup_failures)
            except BaseException as cleanup_error:
                cleanup_errors.append(_finite_error_code(cleanup_error))
            with suppress(Exception):
                await get_paper_runtime_snapshot_scheduler().shutdown()
            strategy_api.get_ai_strategy_research_service.cache_clear()
            for module_name in (profile_module_name, owner_module_name):
                sys.modules.pop(module_name, None)
            with suppress(ValueError):
                sys.path.remove(str(plugin_root))
            try:
                await account_engine.dispose()
            except BaseException as cleanup_error:
                cleanup_errors.append(_finite_error_code(cleanup_error))

            cleanup_receipt["cleanup_errors"] = cleanup_errors
            try:
                cleanup_receipt_path.write_text(
                    json.dumps(cleanup_receipt, sort_keys=True, separators=(",", ":")),
                    encoding="utf-8",
                )
            except BaseException as receipt_error:
                cleanup_errors.append(_finite_error_code(receipt_error))
                cleanup_receipt["cleanup_errors"] = cleanup_errors
            if cleanup_errors:
                note = (
                    "test-owned cleanup had unverified work; "
                    f"finite_codes={','.join(cleanup_errors)}; "
                    f"receipt={cleanup_receipt_path}"
                )
                if active_error is not None:
                    active_error.add_note(note)
                else:
                    raise AssertionError(note)

    asyncio.run(run_http_flow())
