"""MIG03 evidence for the real AFI snapshot writer lifecycle.

This test starts AFI's configured scheduler against a temporary SQLite database,
observes its actual persisted mark-to-market rows, and closes its owner process
through the production stop helper.  It proves scheduler shutdown prevents
later writes; migration fencing and old matcher refusal are covered separately.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from app.db.database import Base
from app.models.paper_runtime import PaperEquitySnapshot
from app.models.user import User
from app.models.workspace import StrategyUnit, Workspace
from app.services.mig03_boundary_inventory import stop_paper_snapshot_writers
from app.services.paper_runtime_scheduler import PaperRuntimeSnapshotScheduler
from app.services.paper_runtime_service import PaperRuntimeService
from tests.integration import test_service_migration_boundary_cutover as cutover
from tests.integration import test_service_migration_boundary_inventory as r235


_USER_ID = "mig03-scheduler-user"
_WORKSPACE_ID = "mig03-scheduler-workspace"
_UNIT_ID = "mig03-scheduler-unit"


def _assert_fixed_afi_origins() -> None:
    """Bind every AFI producer/model used here to the Root-selected snapshot."""
    root = os.environ.get("BTAPI_MIG03_FIXED_AFI_SOURCE_ROOT")
    assert root, "Root QA must bind the accepted AFI source snapshot"
    expected = (Path(root).resolve() / "src" / "backend").resolve()
    for symbol in (
        Workspace,
        StrategyUnit,
        PaperEquitySnapshot,
        PaperRuntimeService,
        PaperRuntimeSnapshotScheduler,
        stop_paper_snapshot_writers,
    ):
        actual = Path(inspect.getsourcefile(symbol)).resolve()
        try:
            actual.relative_to(expected)
        except ValueError:
            raise AssertionError((symbol.__name__, actual, expected)) from None


def _seed_running_paper_runtime(database_path: Path) -> None:
    """Reuse the accepted synthetic AFI ledger seed and add its real scheduler rows."""
    r235._seed_afi_database(database_path)
    engine = create_engine(f"sqlite:///{database_path}", connect_args={"timeout": 5})
    try:
        Base.metadata.create_all(
            engine,
            tables=(User.__table__, Workspace.__table__, StrategyUnit.__table__),
        )
        with Session(engine) as session:
            session.execute(
                delete(PaperEquitySnapshot).where(
                    PaperEquitySnapshot.instance_id == r235._AFI_INSTANCE_ID
                )
            )
            session.add(
                User(
                    id=_USER_ID,
                    username="mig03-scheduler-user",
                    email="mig03-scheduler-user@example.test",
                    hashed_password="synthetic-test-only-unused",
                )
            )
            session.add(
                Workspace(
                    id=_WORKSPACE_ID,
                    user_id=_USER_ID,
                    name="MIG03 scheduler source",
                    workspace_type="trading",
                    settings={},
                    trading_config={},
                )
            )
            session.add(
                StrategyUnit(
                    id=_UNIT_ID,
                    workspace_id=_WORKSPACE_ID,
                    strategy_id="mig03-scheduler-strategy",
                    strategy_name="MIG03 scheduler source",
                    symbol="SYNTH-USD",
                    trading_mode="paper",
                    trading_instance_id=r235._AFI_INSTANCE_ID,
                    run_status="running",
                    lock_running=False,
                    trading_snapshot={
                        "position_pnl": 1.25,
                        "long_market_value": 12.5,
                        "short_market_value": 0.0,
                        "cumulative_pnl": 1.25,
                    },
                    metrics_snapshot={"initial_cash": 100000.0, "final_value": 100001.25},
                    unit_settings={"initial_cash": 100000.0},
                )
            )
            session.commit()
    finally:
        engine.dispose()


def _snapshot_rows(database_path: str, instance_id: str) -> tuple[tuple[Any, ...], ...]:
    with sqlite3.connect(database_path, timeout=5) as connection:
        row = connection.execute(
            "SELECT id, user_id, workspace_id, unit_id, instance_id, source, total_equity, "
            "cash, position_value, unrealized_pnl, realized_pnl "
            "FROM paper_equity_snapshots WHERE instance_id = ? AND source = ? "
            "ORDER BY observed_at, id",
            (instance_id, "mark_to_market"),
        ).fetchall()
    return tuple(tuple(item) for item in row)


def _real_afi_scheduler_process(control: Any, database_path: str) -> None:
    """Run the unmodified AFI scheduler/service in its own process and DB loop."""
    import app.db.database as database

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
        try:
            started = await scheduler.start_existing()
            if started != 1:
                raise AssertionError(f"expected one configured scheduler, got {started}")

            deadline = time.monotonic() + 10.0
            rows = 0
            while time.monotonic() < deadline:
                rows = len(_snapshot_rows(database_path, r235._AFI_INSTANCE_ID))
                if rows >= 2:
                    break
                await asyncio.sleep(0.05)
            if rows < 2:
                raise AssertionError("real scheduler did not persist repeated snapshots")

            task_names = tuple(sorted(task.get_name() for task in scheduler._tasks.values()))
            control.send(
                (
                    "ready",
                    {
                        "start_existing_count": started,
                        "snapshot_rows_before_stop": rows,
                        "task_names": task_names,
                        "instance_id": r235._AFI_INSTANCE_ID,
                    },
                )
            )
            deadline = time.monotonic() + 20.0
            while time.monotonic() < deadline and not control.poll(0):
                await asyncio.sleep(0.01)
            if not control.poll(0) or control.recv() != "close":
                raise AssertionError("parent did not request bounded scheduler close")

            stopped = await stop_paper_snapshot_writers(scheduler)
            normally_stopped = True
            rows_after_stop = _snapshot_rows(database_path, r235._AFI_INSTANCE_ID)
            await asyncio.sleep(1.25)
            rows_after_quiet_interval = _snapshot_rows(database_path, r235._AFI_INSTANCE_ID)
            control.send(
                (
                    "closed",
                    {
                        "tasks_before": stopped.tasks_before,
                        "stopped_task_names": tuple(stopped.stopped_task_names),
                        "tasks_after": stopped.tasks_after,
                        "snapshot_rows_after_stop": rows_after_stop,
                        "snapshot_rows_after_quiet_interval": rows_after_quiet_interval,
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


def test_mig03_real_afi_snapshot_scheduler_persists_then_stops(tmp_path: Path) -> None:
    _assert_fixed_afi_origins()
    r235._assert_all_fixed_origins()
    database_path = tmp_path / "afi-paper-scheduler.sqlite3"
    _seed_running_paper_runtime(database_path)
    assert _snapshot_rows(str(database_path), r235._AFI_INSTANCE_ID) == ()

    context = cutover.multiprocessing.get_context("spawn")
    scheduler = cutover._spawn_owned(
        context,
        _real_afi_scheduler_process,
        (str(database_path),),
        name="mig03-real-afi-snapshot-scheduler",
    )
    try:
        ready = cutover._receive_owned(scheduler, timeout=20.0)
        assert ready[0] == "ready", ready
        started = ready[1]
        assert started["start_existing_count"] == 1
        assert started["snapshot_rows_before_stop"] >= 2
        assert started["task_names"] == (
            f"paper-runtime-snapshot:{r235._AFI_INSTANCE_ID}",
        )

        stopped, _pid = cutover._close_owned_normally(scheduler)
        assert stopped["tasks_before"] == 1
        assert stopped["stopped_task_names"] == started["task_names"]
        assert stopped["tasks_after"] == 0
        stopped_rows = stopped["snapshot_rows_after_stop"]
        assert len(stopped_rows) >= started["snapshot_rows_before_stop"]
        assert stopped_rows
        for row in stopped_rows:
            (
                _snapshot_id,
                user_id,
                workspace_id,
                unit_id,
                instance_id,
                source,
                total_equity,
                cash,
                position_value,
                unrealized_pnl,
                realized_pnl,
            ) = row
            assert (user_id, workspace_id, unit_id, instance_id, source) == (
                _USER_ID,
                _WORKSPACE_ID,
                _UNIT_ID,
                r235._AFI_INSTANCE_ID,
                "mark_to_market",
            )
            assert total_equity == pytest.approx(100001.25)
            assert cash == pytest.approx(99988.75)
            assert position_value == pytest.approx(12.5)
            assert unrealized_pnl == pytest.approx(1.25)
            assert realized_pnl == pytest.approx(0.0)
        assert (
            stopped["snapshot_rows_after_quiet_interval"]
            == stopped["snapshot_rows_after_stop"]
        )
        assert _snapshot_rows(str(database_path), r235._AFI_INSTANCE_ID) == stopped[
            "snapshot_rows_after_stop"
        ]
    finally:
        cutover._cleanup_owned(scheduler)
