"""Compose the AFI12 snapshot writer and SDK STOP_NEW receipt at one MIG03 cutover.

All stores and providers are temporary/synthetic.  This node reuses the accepted
MIG03 spawned-Host drill, but substitutes the production AFI scheduler and
production AFI12 import before the boundary snapshot.  It does not access a
venue account or claim an external provider fence.
"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from bt_api_base.service_protocol import (
    WireEnvelope,
    WireHeader,
    WireIdentity,
    WireMessageKind,
    WireRole,
    WireScope,
)
from bt_api_py.service_migration_boundary import ConfiguredConsumer
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    ServiceCapability,
    ServiceRegistry,
)
from tests.integration import test_service_migration_boundary_cutover as cutover
from tests.integration import test_service_migration_boundary_inventory as r235
from tests.unit import test_service_migration_drill as drill
from tests.unit import test_service_migration_integration as migration


_STOP_NEW_COMMAND_ID = "mig03-stop-new-preserved-composition-v1-" + "a" * 32
_AFI_WORKSPACE_ID = "mig03-afi12-snapshot-workspace"
_AFI_UNIT_ID = "mig03-afi12-snapshot-unit"


def _registry_request(
    operation: str,
    payload: dict[str, Any],
    *,
    identity: WireIdentity,
    scope: WireScope,
) -> WireEnvelope:
    return WireEnvelope(
        header=WireHeader(
            protocol_version=2,
            kind=WireMessageKind.REQUEST,
            message_id=uuid4().hex,
            correlation_id=None,
            identity=identity,
            scope=scope,
            operation=operation,
            schema_id=(
                "sdk.service.control" if operation.startswith("service.") else "sdk.service.call"
            ),
            schema_version=1,
            sent_at_utc="2026-10-17T00:00:00Z",
        ),
        payload=payload,
    )


def _fixed_afi_root() -> Path:
    value = os.environ.get("BTAPI_MIG03_FIXED_AFI_SOURCE_ROOT")
    assert value, "Root QA must set the fixed AFI export root"
    return (Path(value).resolve() / "src" / "backend").resolve()


def _load_legacy_import_helpers():
    backend = _fixed_afi_root()
    path = backend / "tests" / "test_legacy_paper_import_migration.py"
    spec = importlib.util.spec_from_file_location(
        "mig03_composition_afi12_import_helpers", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _assert_fixed_origins(legacy: Any) -> None:
    afi_root = _fixed_afi_root()
    sdk_value = os.environ.get("BTAPI_MIG03B_FIXED_SOURCE_ROOT")
    assert sdk_value, "Root QA must set the fixed SDK export root"
    sdk_root = Path(sdk_value).resolve()
    from app.db import database
    from app.models.legacy_paper_import import LegacyPaperImportReceipt
    from app.models.paper_runtime import PaperEquitySnapshot
    from app.services.legacy_paper_import_migration import migrate_nonempty_legacy_paper_account
    from app.services.mig03_boundary_inventory import (
        enumerate_paper_snapshot_writers,
        stop_paper_snapshot_writers,
    )
    from app.services.paper_runtime_scheduler import PaperRuntimeSnapshotScheduler
    from app.services.paper_runtime_service import PaperRuntimeService

    for symbol in (
        database,
        LegacyPaperImportReceipt,
        PaperEquitySnapshot,
        enumerate_paper_snapshot_writers,
        stop_paper_snapshot_writers,
        PaperRuntimeSnapshotScheduler,
        PaperRuntimeService,
        migrate_nonempty_legacy_paper_account,
        legacy._domain,
        legacy._binding,
    ):
        actual = Path(inspect.getsourcefile(symbol)).resolve()
        try:
            actual.relative_to(afi_root)
        except ValueError:
            raise AssertionError((symbol.__name__, actual, afi_root)) from None

    sdk_target = (sdk_root / "bt_api_py" / "service_migration_boundary.py").resolve()
    sdk_actual = Path(inspect.getsourcefile(cutover.arm_migration_boundary)).resolve()
    assert sdk_actual == sdk_target, (sdk_actual, sdk_target)


def _seed_afi_source_database(path: Path, legacy: Any) -> None:
    """Create the real AFI12 schema/ledger plus one persisted runtime FK history."""

    from app.db.database import Base
    from app.models.approved_target_http_operation import ApprovedTargetHttpOperation
    from app.models.alerts import Alert, AlertNotification, AlertRule
    from app.models.legacy_paper_import import LegacyPaperImportReceipt
    from app.models.paper_runtime import (
        LiveHandoffReview,
        PaperEquitySnapshot,
        PaperReviewReport,
        RiskRule,
    )
    from app.models.paper_trading import Account, Order, PaperTrade, Position
    from app.models.service_order_operation import ServiceOrderOperation
    from app.models.strategy_approval_authority import (
        StrategyApprovalAuthorityEpoch,
        StrategyOrderApprovalPolicyHead,
        StrategyOrderApprovalPolicyVersion,
    )
    from app.models.strategy_target_approval import (
        StrategyTargetApproval,
        StrategyTargetApprovalEvent,
    )
    from app.models.user import User
    from app.models.workspace import StrategyUnit, Workspace
    from app.db import database

    models = (
        User,
        Account,
        Order,
        PaperTrade,
        Position,
        Alert,
        AlertNotification,
        AlertRule,
        PaperEquitySnapshot,
        PaperReviewReport,
        LiveHandoffReview,
        RiskRule,
        LegacyPaperImportReceipt,
        StrategyApprovalAuthorityEpoch,
        StrategyOrderApprovalPolicyHead,
        StrategyOrderApprovalPolicyVersion,
        Workspace,
        StrategyUnit,
        ServiceOrderOperation,
        ApprovedTargetHttpOperation,
        StrategyTargetApproval,
        StrategyTargetApprovalEvent,
    )
    engine = create_engine(f"sqlite:///{path.resolve().as_posix()}", connect_args={"timeout": 5})
    try:
        Base.metadata.create_all(engine, tables=tuple(model.__table__ for model in models))
    finally:
        engine.dispose()

    async_engine = create_async_engine(
        f"sqlite+aiosqlite:///{path.resolve().as_posix()}",
        connect_args={"timeout": 5},
    )
    database._engine = async_engine
    database._async_session_maker = async_sessionmaker(
        async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async def seed_legacy() -> None:
        await legacy._install_source_migrations()
        await legacy._seed_account()

    try:
        asyncio.run(seed_legacy())
    finally:
        asyncio.run(async_engine.dispose())
        database._async_session_maker = None
        database._engine = None

    engine = create_engine(f"sqlite:///{path.resolve().as_posix()}", connect_args={"timeout": 5})
    try:
        with Session(engine) as session:
            owner = legacy._SOURCE_OWNER_ID
            session.add(
                Workspace(
                    id=_AFI_WORKSPACE_ID,
                    user_id=owner,
                    name="MIG03 AFI12 snapshot source",
                    workspace_type="trading",
                    settings={},
                    trading_config={"migration_fixture": "synthetic"},
                )
            )
            session.add(
                StrategyUnit(
                    id=_AFI_UNIT_ID,
                    workspace_id=_AFI_WORKSPACE_ID,
                    strategy_id="mig03-afi12-snapshot-strategy",
                    strategy_name="MIG03 AFI12 snapshot source",
                    symbol="SYNTH-USD",
                    trading_mode="paper",
                    trading_instance_id=r235._AFI_INSTANCE_ID,
                    run_status="running",
                    lock_running=False,
                    trading_snapshot={
                        "position_pnl": -0.1,
                        "long_market_value": 10.0,
                        "short_market_value": 0.0,
                        "cumulative_pnl": -0.1,
                    },
                    metrics_snapshot={"initial_cash": 89.9, "final_value": 99.9},
                    unit_settings={"initial_cash": 89.9},
                )
            )
            session.add(
                PaperEquitySnapshot(
                    id="mig03-afi12-persisted-account-history",
                    user_id=owner,
                    workspace_id=_AFI_WORKSPACE_ID,
                    unit_id=_AFI_UNIT_ID,
                    instance_id=r235._AFI_INSTANCE_ID,
                    paper_account_id=legacy._SOURCE_ACCOUNT_ID,
                    observed_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
                    source="migration_seed",
                    total_equity=99.9,
                    cash=89.9,
                    position_value=10.0,
                    unrealized_pnl=-0.1,
                    realized_pnl=0.0,
                    metadata_json={"fixture": "persisted_fk_history"},
                )
            )
            session.commit()
    finally:
        engine.dispose()


def _snapshot_rows(database_path: str | Path, instance_id: str) -> tuple[tuple[Any, ...], ...]:
    connection = sqlite3.connect(database_path, timeout=5)
    try:
        connection.execute("PRAGMA query_only = ON")
        return tuple(
            tuple(row)
            for row in connection.execute(
                "SELECT id,user_id,workspace_id,unit_id,instance_id,paper_account_id,source,"
                "total_equity,cash,position_value,unrealized_pnl,realized_pnl "
                "FROM paper_equity_snapshots WHERE instance_id=? ORDER BY observed_at,id",
                (instance_id,),
            ).fetchall()
        )
    finally:
        connection.close()


def _real_afi_scheduler_owner(control: Any, database_path: str, phase: str) -> None:
    """Run the unmodified configured AFI scheduler in a real spawned owner process."""

    from app.db import database
    from app.services.mig03_boundary_inventory import stop_paper_snapshot_writers
    from app.services.paper_runtime_scheduler import PaperRuntimeSnapshotScheduler

    async def run() -> None:
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{Path(database_path).resolve().as_posix()}",
            connect_args={"timeout": 5},
        )
        database._engine = engine
        database._async_session_maker = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        scheduler = PaperRuntimeSnapshotScheduler(interval_seconds=1)
        normally_stopped = False
        task_name = f"paper-runtime-snapshot:{r235._AFI_INSTANCE_ID}"
        try:
            started = await scheduler.start_existing()
            rows_before_start = _snapshot_rows(database_path, r235._AFI_INSTANCE_ID)
            if phase == "source":
                if started != 1:
                    raise AssertionError(("source scheduler count", started))
                deadline = time.monotonic() + 12.0
                while time.monotonic() < deadline:
                    mark_rows = tuple(row for row in _snapshot_rows(database_path, r235._AFI_INSTANCE_ID) if row[6] == "mark_to_market")
                    if len(mark_rows) >= 2:
                        break
                    await asyncio.sleep(0.05)
                if len(mark_rows) < 2:
                    raise AssertionError("source AFI scheduler did not persist repeated snapshots")
                control.send(
                    (
                        "ready",
                        {
                            "task_name": task_name,
                            "start_existing_count": started,
                            "snapshot_rows_before_stop": len(mark_rows),
                            "snapshot_rows_before_start": rows_before_start,
                        },
                    )
                )
            elif phase == "destination":
                if started != 0 or scheduler._tasks:
                    raise AssertionError(("ACTIVE receipt failed to fence restart", started))
                rows_after_start = _snapshot_rows(database_path, r235._AFI_INSTANCE_ID)
                control.send(
                    (
                        "ready",
                        {
                            "task_name": task_name,
                            "start_existing_count": started,
                            "snapshot_rows_before_start": rows_before_start,
                            "snapshot_rows_after_start": rows_after_start,
                        },
                    )
                )
                await asyncio.sleep(1.25)
                if _snapshot_rows(database_path, r235._AFI_INSTANCE_ID) != rows_after_start:
                    raise AssertionError("destination scheduler wrote despite ACTIVE receipt")
            else:
                raise AssertionError("unknown AFI scheduler phase")

            deadline = time.monotonic() + 25.0
            while time.monotonic() < deadline and not control.poll(0):
                await asyncio.sleep(0.01)
            if not control.poll(0) or control.recv() != "close":
                raise AssertionError("parent did not request AFI scheduler close")
            stopped = await stop_paper_snapshot_writers(scheduler)
            normally_stopped = True
            after_stop = _snapshot_rows(database_path, r235._AFI_INSTANCE_ID)
            await asyncio.sleep(1.25)
            after_quiet = _snapshot_rows(database_path, r235._AFI_INSTANCE_ID)
            if after_quiet != after_stop:
                raise AssertionError("AFI scheduler wrote after normal stop")
            control.send(
                (
                    "closed",
                    {
                        "tasks_before": stopped.tasks_before,
                        "stopped_task_names": tuple(stopped.stopped_task_names),
                        "tasks_after": stopped.tasks_after,
                        "snapshot_rows_after_stop": after_stop,
                        "snapshot_rows_after_quiet_interval": after_quiet,
                    },
                )
            )
        finally:
            if not normally_stopped:
                await stop_paper_snapshot_writers(scheduler)
            await engine.dispose()
            database._async_session_maker = None
            database._engine = None

    try:
        asyncio.run(run())
    except BaseException as error:
        try:
            control.send(("error", type(error).__name__, getattr(error, "code", None)))
        except BaseException:
            pass
        raise
    finally:
        control.close()


def _run_active_afi12_import_and_probe(
    database_path: Path,
    target_root: Path,
    legacy: Any,
) -> dict[str, Any]:
    from app.db import database
    from app.services.legacy_paper_import_migration import get_legacy_paper_import_receipt
    from app.services.paper_runtime_service import (
        PaperRuntimeService,
        PaperRuntimeSnapshotWriteDenied,
    )

    async def run() -> dict[str, Any]:
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{database_path.resolve().as_posix()}",
            connect_args={"timeout": 5},
        )
        database._engine = engine
        database._async_session_maker = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        target_store, target_worker, scope, domain = legacy._target(target_root)
        try:
            service = legacy.PaperTradingService()
            imported = await legacy.migrate_nonempty_legacy_paper_account(
                service,
                legacy._SOURCE_OWNER_ID,
                legacy._binding(domain),
                target_worker=target_worker,
                scope=scope,
                domain=domain,
                symbol_bindings=(
                    legacy.LegacyPaperSymbolBindingV1(
                        legacy._LEGACY_SYMBOL,
                        legacy._INSTRUMENT,
                    ),
                ),
            )
            receipt = await get_legacy_paper_import_receipt(
                legacy._SOURCE_ACCOUNT_ID,
                legacy._SOURCE_OWNER_ID,
            )
            assert imported.receipt.state == "ACTIVE"
            assert imported.receipt.source_inventory_sha256
            assert receipt is not None and receipt.state == "ACTIVE"
            assert receipt.source_inventory_sha256 == imported.receipt.source_inventory_sha256
            target_readback = target_store.get_paper_legacy_import(scope)
            assert target_readback is not None and target_readback.state == "ACTIVE"
            book = target_store.read_paper_book(scope)
            assert book.cash == Decimal("89.9")
            assert book.cumulative_fees == Decimal("0.1")
            assert [
                (item.instrument, item.quantity, item.average_cost) for item in book.positions
            ] == [(legacy._INSTRUMENT, Decimal("1"), Decimal("10"))]

            rows_before_write = _snapshot_rows(database_path, r235._AFI_INSTANCE_ID)
            observed_at = datetime.now(timezone.utc).replace(microsecond=0)
            try:
                await PaperRuntimeService().record_snapshot(
                    legacy._SOURCE_OWNER_ID,
                    r235._AFI_INSTANCE_ID,
                    {
                        "observed_at": observed_at,
                        "source": "mig03-composition-post-active-write-attempt",
                        "total_equity": 100.0,
                    },
                )
            except PaperRuntimeSnapshotWriteDenied as error:
                assert error.code == "MANAGED_PAPER_LEGACY_ACCOUNT_FENCED"
            else:
                raise AssertionError("old AFI direct snapshot writer passed ACTIVE receipt")
            rows_after_write = _snapshot_rows(database_path, r235._AFI_INSTANCE_ID)
            assert rows_after_write == rows_before_write
            return {
                "source_inventory_sha256": imported.receipt.source_inventory_sha256,
                "receipt_id": receipt.receipt_id,
                "receipt_state": receipt.state,
                "target_origin": target_readback.imported.origin,
                "target_source_sha256": target_readback.imported.source_sha256,
                "cash": str(book.cash),
                "fees": str(book.cumulative_fees),
                "position": (book.positions[0].instrument, str(book.positions[0].quantity), str(book.positions[0].average_cost)),
                "snapshot_rows_before_direct_write": rows_before_write,
                "snapshot_rows_after_direct_write": rows_after_write,
            }
        finally:
            target_store.close()
            await engine.dispose()
            database._async_session_maker = None
            database._engine = None

    return asyncio.run(run())


def _seed_stop_new_receipt(registry_path: Path) -> dict[str, Any]:
    """Use the production Registry API to persist an adjacent active run fence."""

    identity = WireIdentity(
        WireRole.STRATEGY_CLIENT,
        "mig03-composition-stop-new-principal",
        "strategy:adjacent-stopped-run",
    )
    scope_template = WireScope(
        "live:venue-a",
        account_ref="account-a",
        strategy_ref="strategy-alpha",
    )
    scope = WireScope(
        "live:venue-a",
        account_ref="account-a",
        strategy_ref="strategy-alpha",
        run_id="mig03-adjacent-stop-new-run",
    )
    operations = frozenset({"orders.submit", "orders.cancel"})
    capability = ServiceCapability(
        "execution-alpha",
        "service.order_entry",
        operations,
        operations,
    )
    grant = ResourceGrant(
        "execution-alpha",
        "service.order_entry",
        "strategy-alpha-orders",
        scope_template,
        operations,
    )
    registry = ServiceRegistry(
        registry_path,
        service_id=drill._SERVICE_ID,
        capabilities=(capability,),
        grants={identity.principal_ref: (PrincipalGrant(identity, (grant,)),)},
        policy_version="mig03-stop-new-composition-policy-v1",
        requirements_version="mig03-stop-new-composition-requirements-v1",
        migration_deployment_epoch=migration._OLD_DEPLOYMENT_EPOCH,
    )
    try:
        registered = dict(
            registry.handle(
                identity.principal_ref,
                _registry_request(
                    "service.register",
                    {
                        "registration_key": "mig03-stop-new-registration-v1",
                        "client_session_id": "mig03-stop-new-session-v1",
                        "instance_id": "execution-alpha",
                        "port": "service.order_entry",
                        "scope_ref": "strategy-alpha-orders",
                        "operations": ("orders.submit", "orders.cancel"),
                    },
                    identity=identity,
                    scope=scope,
                ),
            )
        )
        assert registered["ok"] is True
        receipt = dict(registry.stop_strategy_new(scope, _STOP_NEW_COMMAND_ID))
        assert receipt["status"] == "applied"
        assert receipt["effect_ref"].startswith("strategy-stop-new-v1:")
        assert receipt["effect_result"] == {"action": "stop_new", "result": "fenced"}
        # Keep the durable stop claim and idempotency receipt, while closing
        # the temporary owner session before the destination epoch is opened.
        unregistered = dict(
            registry.handle(
                identity.principal_ref,
                _registry_request(
                    "service.unregister",
                    {
                        "registration_id": registered["result"]["registration_id"],
                        "service_epoch": registered["result"]["service_epoch"],
                    },
                    identity=identity,
                    scope=scope,
                ),
            )
        )
        assert unregistered["ok"] is True
        return receipt
    finally:
        registry.close()


def _read_stop_new_rows(path: Path) -> tuple[tuple[tuple[Any, ...], ...], tuple[tuple[Any, ...], ...]]:
    connection = sqlite3.connect(path, timeout=5)
    try:
        connection.execute("PRAGMA query_only = ON")
        claims = tuple(
            connection.execute(
                "SELECT execution_domain_ref,strategy_ref,account_ref,run_id,stop_new_at,"
                "stop_new_command_id FROM strategy_write_claims "
                "WHERE stop_new_command_id=? ORDER BY execution_domain_ref,strategy_ref,account_ref",
                (_STOP_NEW_COMMAND_ID,),
            ).fetchall()
        )
        receipts = tuple(
            connection.execute(
                "SELECT command_id,scope_digest,status,effect_ref,effect_result_json,created_at "
                "FROM strategy_stop_new_receipts WHERE command_id=?",
                (_STOP_NEW_COMMAND_ID,),
            ).fetchall()
        )
        return claims, receipts
    finally:
        connection.close()


def test_mig03_cutover_composes_real_afi12_scheduler_and_stop_new_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run the accepted cutover with real AFI writes, ACTIVE import and durable STOP_NEW."""

    legacy = _load_legacy_import_helpers()
    _assert_fixed_origins(legacy)
    monkeypatch.setattr(cutover, "_EXPECT_ACTIVE_AFI12_IMPORT", True)
    monkeypatch.setattr(legacy, "_ACCOUNT_REF", r235._AFI_ACCOUNT_REF)
    monkeypatch.setattr(r235, "_AFI_ACCOUNT_ID", legacy._SOURCE_ACCOUNT_ID)

    state: dict[str, Any] = {
        "scheduler_spawn_count": 0,
        "registry_snapshots": [],
        "afi_snapshots": [],
        "owned_hosts": [],
        "host_reap_events": [],
        "host_reaper_errors": [],
    }
    cleanup_receipt_path = tmp_path / "mig03-owned-host-cleanup-receipt.json"

    original_start_host = drill._start_host_process

    def track_start_host(context: Any, target: Any, args: tuple[Any, ...], *, label: str):
        record = original_start_host(context, target, args, label=label)
        state["owned_hosts"].append(record)
        return record

    monkeypatch.setattr(drill, "_start_host_process", track_start_host)

    original_receive_host_message = drill._receive_host_message

    def track_host_message(record: dict[str, Any], timeout: float = 15.0):
        message = original_receive_host_message(record, timeout)
        record["last_response_kind"] = message[0] if message else None
        if record.get("label") == "mig03-process-cutover-old-epoch-refusal":
            record["normal_refusal_ack"] = bool(message and message[0] == "refused")
        return message

    monkeypatch.setattr(drill, "_receive_host_message", track_host_message)

    original_bounded_reap = drill._bounded_reap_owned_host

    def track_bounded_reap(record: dict[str, Any], forced: list[str], failures: list[str]):
        failure_count = len(failures)
        raised_type = None
        retry_raised_type = None
        attempts = 1
        try:
            return original_bounded_reap(record, forced, failures)
        except BaseException as error:
            raised_type = type(error).__name__
            state["host_reaper_errors"].append(
                (record.get("label", "unknown"), raised_type)
            )
            # A test-owned cleanup failure must not prevent bounded attempts
            # for the other Hosts. Retry only this same bounded helper once;
            # record both exceptions and never treat the retry as a clean pass.
            attempts = 2
            try:
                return original_bounded_reap(record, forced, failures)
            except BaseException as retry_error:
                retry_raised_type = type(retry_error).__name__
                state["host_reaper_errors"].append(
                    (record.get("label", "unknown"), retry_raised_type)
                )
        finally:
            process = record.get("process")
            final_alive = None
            exit_code = record.get("normal_exit_code", record.get("terminal_exit_code"))
            process_state_error = None
            if process is not None and not record.get("closed"):
                try:
                    final_alive = process.is_alive()
                    if exit_code is None:
                        exit_code = process.exitcode
                except BaseException as error:
                    process_state_error = type(error).__name__
            label = record.get("label", "unknown")
            state["host_reap_events"].append(
                {
                    "label": label,
                    "bounded_reap_invoked": True,
                    "normal_close_ack": record.get("normal_close_ack") is True,
                    "normal_refusal_ack": record.get("normal_refusal_ack") is True,
                    "observed_exit_code": exit_code,
                    "fallback_cleanup_attempted": label in forced,
                    "cleanup_failures": tuple(failures[failure_count:]),
                    "process_handle_closed": record.get("closed") is True,
                    "final_alive": final_alive,
                    "process_state_error": process_state_error,
                    "reaper_exception_type": raised_type,
                    "reaper_retry_exception_type": retry_raised_type,
                    "bounded_reap_attempts": attempts,
                }
            )

    monkeypatch.setattr(drill, "_bounded_reap_owned_host", track_bounded_reap)

    def seed(path: Path) -> None:
        state["afi_path"] = Path(path)
        _seed_afi_source_database(Path(path), legacy)

    monkeypatch.setattr(r235, "_seed_afi_database", seed)

    original_spawn = cutover._spawn_owned

    def spawn(context: Any, target: Any, args: tuple[Any, ...], *, name: str):
        if target is cutover._afi_snapshot_scheduler_process:
            state["scheduler_spawn_count"] += 1
            phase = "source" if state["scheduler_spawn_count"] == 1 else "destination"
            assert state["scheduler_spawn_count"] <= 2
            return original_spawn(
                context,
                _real_afi_scheduler_owner,
                (str(state["afi_path"]), phase),
                name=name,
            )
        return original_spawn(context, target, args, name=name)

    monkeypatch.setattr(cutover, "_spawn_owned", spawn)

    from app.services.mig03_boundary_inventory import enumerate_paper_snapshot_writers

    def configured_afi_consumer(*, configuration_sha256: str, task_name: str) -> ConfiguredConsumer:
        path = Path(state["afi_path"])
        engine = create_engine(f"sqlite:///{path.resolve().as_posix()}", connect_args={"timeout": 5})
        try:
            with Session(engine) as session:
                rows = enumerate_paper_snapshot_writers(
                    session,
                    account_refs_by_paper_account={
                        legacy._SOURCE_ACCOUNT_ID: legacy._ACCOUNT_REF
                    },
                    configuration_sha256=configuration_sha256,
                )
        finally:
            engine.dispose()
        assert len(rows) == 1
        writer = rows[0]
        assert writer.consumer_kind == "afi_snapshot_scheduler"
        assert writer.paper_account_id == legacy._SOURCE_ACCOUNT_ID
        assert writer.account_ref == legacy._ACCOUNT_REF
        assert task_name == f"paper-runtime-snapshot:{writer.instance_id}"
        state["configured_afi_writer"] = writer
        return ConfiguredConsumer(
            consumer_id=writer.consumer_id,
            consumer_kind=writer.consumer_kind,
            configuration_sha256=writer.configuration_sha256,
            scope_sha256=writer.scope_sha256,
            operations_sha256=writer.operations_sha256,
        )

    monkeypatch.setattr(cutover, "_afi_scheduler_consumer", configured_afi_consumer)

    original_capture_afi = r235._capture_afi

    def capture_afi(path: Path, configuration_sha256: str):
        if "afi12_import" not in state:
            state["afi12_import"] = _run_active_afi12_import_and_probe(
                Path(path),
                tmp_path / "afi12-target",
                legacy,
            )
        snapshot = original_capture_afi(path, configuration_sha256)
        state["afi_snapshots"].append(snapshot)
        return snapshot

    monkeypatch.setattr(r235, "_capture_afi", capture_afi)

    original_readback_target = migration._readback_target_registration

    def capture_target_registration(*args: Any, **kwargs: Any):
        registration = original_readback_target(*args, **kwargs)
        state["target_registration"] = registration
        return registration

    monkeypatch.setattr(migration, "_readback_target_registration", capture_target_registration)

    original_capture_sqlite = cutover.capture_sqlite_participant

    def capture_sqlite(participant_id: str, path: str | Path, **kwargs: Any):
        participant_path = Path(path)
        if participant_id == "registry":
            if "stop_new_receipt" not in state:
                state["stop_new_receipt"] = _seed_stop_new_receipt(participant_path)
                assert state["stop_new_receipt"]["status"] == "applied"
                state["stop_new_rows_before"] = _read_stop_new_rows(participant_path)
                assert len(state["stop_new_rows_before"][0]) == 1
                assert len(state["stop_new_rows_before"][1]) == 1
            snapshot = original_capture_sqlite(participant_id, path, **kwargs)
            state["registry_snapshots"].append(snapshot)
            return snapshot
        return original_capture_sqlite(participant_id, path, **kwargs)

    monkeypatch.setattr(cutover, "capture_sqlite_participant", capture_sqlite)

    def cleanup_receipt_document() -> dict[str, Any]:
        events_by_label = {
            event["label"]: event for event in state["host_reap_events"]
        }
        host_rows = []
        for record in state["owned_hosts"]:
            label = record.get("label", "unknown")
            event = events_by_label.get(label)
            if event is None:
                fallback_state = "unobserved"
            elif event["fallback_cleanup_attempted"]:
                fallback_state = "attempted"
            elif (
                event["reaper_exception_type"] is not None
                or event["reaper_retry_exception_type"] is not None
                or event["process_state_error"] is not None
                or event["cleanup_failures"]
            ):
                fallback_state = "unobserved"
            else:
                fallback_state = "not_attempted"
            host_rows.append(
                {
                    "label": label,
                    "pid": record.get("pid"),
                    "normal_close_ack": record.get("normal_close_ack") is True,
                    "normal_refusal_ack": record.get("normal_refusal_ack") is True,
                    "normal_exit_code": record.get("normal_exit_code"),
                    "terminal_exit_code": record.get("terminal_exit_code"),
                    "bounded_reap_invoked": (
                        event["bounded_reap_invoked"] if event is not None else False
                    ),
                    "fallback_cleanup": fallback_state,
                    "cleanup_failures": (
                        event["cleanup_failures"] if event is not None else ()
                    ),
                    "process_handle_closed": record.get("closed") is True,
                    "final_alive": event["final_alive"] if event is not None else None,
                    "process_state_error": (
                        event["process_state_error"] if event is not None else None
                    ),
                    "reaper_exception_type": (
                        event["reaper_exception_type"] if event is not None else None
                    ),
                    "reaper_retry_exception_type": (
                        event["reaper_retry_exception_type"] if event is not None else None
                    ),
                    "bounded_reap_attempts": (
                        event["bounded_reap_attempts"] if event is not None else 0
                    ),
                }
            )
        return {
            "schema": "mig03-owned-host-cleanup-receipt-v1",
            "test_outcome": state.get("test_outcome", "incomplete"),
            "test_exception_type": state.get("test_exception_type"),
            "test_exception_code": state.get("test_exception_code"),
            "host_reaper_errors": tuple(state["host_reaper_errors"]),
            "hosts": tuple(host_rows),
        }

    try:
        # This is the existing full Host/cursor/group cutover drill in its
        # distinct active-import mode. The accepted default R235 selector is
        # not rerun.
        cutover.test_mig03_spawned_consumers_group_and_cursor_cross_cutover(tmp_path)
        assert not state["host_reaper_errors"], tuple(state["host_reaper_errors"])

        assert state["scheduler_spawn_count"] == 2
        assert state["afi12_import"]["receipt_state"] == "ACTIVE"
        assert state["afi12_import"]["target_origin"] == "imported_legacy"
        assert state["afi12_import"]["snapshot_rows_before_direct_write"] == state[
            "afi12_import"
        ]["snapshot_rows_after_direct_write"]
        assert len(state["registry_snapshots"]) >= 2
        first_registry, last_registry = (
            state["registry_snapshots"][0],
            state["registry_snapshots"][-1],
        )
        assert (
            first_registry.evidence.identity_sha256
            == last_registry.evidence.identity_sha256
        )
        assert state["stop_new_rows_before"] == _read_stop_new_rows(
            tmp_path / "registry.sqlite3"
        )
        assert state["configured_afi_writer"].consumer_id

        # The AFI legacy-import database has its own typed facts. Compare that
        # complete projection independently from SDK execution/Risk/Gateway
        # reservations; the two stores have no AFI reservation crosswalk here.
        afi_snapshots = state["afi_snapshots"]
        assert len(afi_snapshots) == 3
        afi_before, *afi_readbacks = afi_snapshots
        assert type(afi_before).__name__ == "AfiBoundarySnapshot"
        for afi_after in afi_readbacks:
            assert type(afi_after) is type(afi_before)
            r235.assert_afi_state_conserved(afi_before, afi_after)
        target_registration = state["target_registration"]
        assert afi_before.account_ref == legacy._ACCOUNT_REF
        assert afi_before.account_ref == target_registration.account_ref
        assert afi_before.strategy_refs == (target_registration.strategy_ref,)
        assert afi_before.reservation_ids == ()
        assert afi_before.unresolved_ids == ()

        expected_host_labels = {
            "mig03-process-cutover-old-host",
            "mig03-process-cutover-new-host",
            "mig03-process-cutover-old-epoch-refusal",
        }
        host_receipt = cleanup_receipt_document()
        assert {item["label"] for item in host_receipt["hosts"]} == expected_host_labels
        for item in host_receipt["hosts"]:
            assert item["bounded_reap_invoked"] is True
            assert item["fallback_cleanup"] == "not_attempted"
            assert item["cleanup_failures"] == ()
            assert item["process_handle_closed"] is True
            assert item["normal_exit_code"] == 0
        for item in host_receipt["hosts"]:
            if item["label"] == "mig03-process-cutover-old-epoch-refusal":
                assert item["normal_refusal_ack"] is True
            else:
                assert item["normal_close_ack"] is True
        state["test_outcome"] = "passed"
    except BaseException as error:
        state["test_outcome"] = "failed"
        state["test_exception_type"] = type(error).__name__
        state["test_exception_code"] = getattr(error, "code", None)
        raise
    finally:
        # This receipt is written even if any cutover assertion or cleanup
        # fails. R2 predates this instrumentation and remains unobserved.
        cleanup_receipt_path.write_text(
            json.dumps(
                cleanup_receipt_document(),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )

