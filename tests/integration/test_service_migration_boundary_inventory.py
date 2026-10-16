"""MIG03 fixed-boundary handoff over synthetic SQLite state and real Hosts.

This extends R220's same-path process handoff with the formal all-category
inventory.  It uses only temporary local state, accepted service APIs, and a
synthetic AFI database; no provider account or installed deployment is opened.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import math
import multiprocessing
import os
import sqlite3
import stat
import sys
import asyncio
import time
from contextlib import suppress
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest
import zmq
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.database import Base
from app.models.approved_target_http_operation import ApprovedTargetHttpOperation
from app.models.paper_runtime import PaperEquitySnapshot
from app.models.paper_trading import Account, Order, PaperTrade, Position
from app.models.service_order_operation import ServiceOrderOperation
from app.models.strategy_target_approval import (
    StrategyTargetApproval,
    StrategyTargetApprovalEvent,
)
from app.services.mig03_boundary_inventory import (
    capture_afi_boundary,
    stop_paper_snapshot_writers,
)
from app.services.paper_runtime_scheduler import PaperRuntimeSnapshotScheduler
from bt_api_py.service_migration_boundary import (
    CODE_BOUNDARY_INCOMPLETE,
    ConsumerStopEvidence,
    MigrationBoundaryError,
    StateConservation,
    _canonical_json_bytes,
    _sqlite_state_sha256,
    _sqlite_value,
    arm_migration_boundary,
    assert_participant_states_conserved,
    assert_state_conserved,
    capture_sqlite_participant,
    consumer_inventory_sha256,
    not_applicable_participant,
    prepare_migration_boundary,
    require_old_epoch_refused_after_new_write,
    retained_external_participant,
)
from bt_api_py.service_migration_gate import (
    REFUSAL_REVERSE_MIGRATION_REFUSED,
    read_migration_marker,
)
from bt_api_py.service_migration_import import apply_migration_import
from bt_api_py.service_migration_map import MigrationIdentityStore
from tests.unit import test_service_migration_drill as drill
from tests.unit import test_service_migration_integration as migration

_AFI_SCOPE = "mig03-afi-scope"
_AFI_ACCOUNT_REF = drill._ACCOUNT
_AFI_STRATEGY_REF = drill._STRATEGY
_AFI_ACCOUNT_ID = "mig03-afi-paper-account"
_AFI_INSTANCE_ID = "mig03-afi-paper-instance"
_AFI_HASH_1 = "1" * 64
_AFI_HASH_2 = "2" * 64
_AFI_HASH_3 = "3" * 64
_MAX_CONSUMER_DB_BYTES = 32 * 1024 * 1024


def _sha256_json(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8", errors="strict")
    return hashlib.sha256(raw).hexdigest()


def _consumer_projection_json_value(value: Any) -> Any:
    """Normalize the projection's immutable JSON-compatible value shapes."""

    if value is None or type(value) in (str, int, bool):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise AssertionError("consumer projection contains a non-finite number")
        return {"float_hex": value.hex()}
    if type(value) in (dict, MappingProxyType):
        if any(type(key) is not str for key in value):
            raise AssertionError("consumer projection keys must be strings")
        return {key: _consumer_projection_json_value(item) for key, item in value.items()}
    if type(value) in (list, tuple):
        return [_consumer_projection_json_value(item) for item in value]
    raise AssertionError("consumer projection contains an unsupported value")


def _consumer_projection_sha256(projection: Any) -> str:
    normalized = _consumer_projection_json_value(projection)
    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8", errors="strict")
    return hashlib.sha256(encoded).hexdigest()


def _afi_config_sha256(path: Path) -> str:
    return _sha256_json(
        {
            "database_url": f"sqlite:///{path.resolve().as_posix()}",
            "scope_ref": _AFI_SCOPE,
            "account_ref": _AFI_ACCOUNT_REF,
            "strategy_refs": [_AFI_STRATEGY_REF],
            "account_paper_accounts": {_AFI_ACCOUNT_REF: _AFI_ACCOUNT_ID},
            "paper_account_instances": {_AFI_ACCOUNT_ID: _AFI_INSTANCE_ID},
        }
    )


async def _stop_active_snapshot_scheduler():
    scheduler = PaperRuntimeSnapshotScheduler()
    started = asyncio.Event()
    never_release = asyncio.Event()

    async def active_snapshot(_user_id: str, _instance_id: str) -> None:
        started.set()
        await never_release.wait()

    scheduler._run = active_snapshot  # type: ignore[method-assign]
    scheduler.ensure_running("mig03-user", _AFI_INSTANCE_ID)
    await asyncio.wait_for(started.wait(), timeout=1.0)
    return await stop_paper_snapshot_writers(scheduler)


def _assert_group_component_absent(raw_config: dict[str, Any]) -> None:
    components = raw_config.get("components")
    assert type(components) is dict
    forbidden_components = {"group", "order_group", "group_coordinator", "group_reservations"}
    assert forbidden_components.isdisjoint(components)
    forbidden_roles = {
        "group_coordinator",
        "group_account_owner",
        "group_reservations",
        "group_policy",
    }
    for component in components.values():
        settings = component.get("settings", {})
        options = settings.get("options", {})
        assert options.get("role") not in forbidden_roles
        assert not any(
            type(port) is str and ("group" in port or "order_groups" in port)
            for port in settings.get("bind_ports", ())
        )


def _registry_identity_projection(path: Path) -> dict[str, tuple[tuple[Any, ...], ...]]:
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA query_only = ON")
        return {
            "stable_meta": tuple(
                connection.execute(
                    "SELECT key,value FROM registry_meta WHERE key <> 'service_epoch' ORDER BY key"
                )
            ),
            "registration_keys": tuple(
                connection.execute(
                    "SELECT principal,registration_key,request_digest,registration_id "
                    "FROM registration_keys ORDER BY principal,registration_key"
                )
            ),
            "strategy_claims": tuple(
                connection.execute(
                    "SELECT execution_domain_ref,strategy_ref,account_ref,run_id "
                    "FROM strategy_write_claims ORDER BY execution_domain_ref,strategy_ref,account_ref"
                )
            ),
        }
    finally:
        connection.close()


def _assert_registry_handoff_preserved(
    before: dict[str, tuple[tuple[Any, ...], ...]],
    after: dict[str, tuple[tuple[Any, ...], ...]],
    path: Path,
    service_epoch: str,
) -> None:
    assert before["stable_meta"] == after["stable_meta"]
    assert set(before["registration_keys"]).issubset(set(after["registration_keys"]))
    assert before["strategy_claims"] == after["strategy_claims"]
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA query_only = ON")
        sessions = tuple(connection.execute("SELECT service_epoch FROM sessions"))
    finally:
        connection.close()
    assert sessions and all(row[0] == service_epoch for row in sessions)


def _assert_all_fixed_origins() -> dict[str, str]:
    origins = migration._assert_integrated_source_origins()
    sdk_root = os.environ.get("BTAPI_MIG03B_FIXED_SOURCE_ROOT")
    assert sdk_root, "Root QA must bind the committed SDK source export"
    expected_sdk = Path(sdk_root).resolve() / "bt_api_py/service_migration_boundary.py"
    actual_sdk = Path(inspect.getsourcefile(arm_migration_boundary)).resolve()
    assert actual_sdk == expected_sdk.resolve(), (actual_sdk, expected_sdk)

    afi_root = os.environ.get("BTAPI_MIG03_FIXED_AFI_SOURCE_ROOT")
    assert afi_root, "Root QA must bind the fixed accepted AFI source export"
    expected_afi = Path(afi_root).resolve() / "src/backend"
    symbols = (
        capture_afi_boundary,
        Account,
        Order,
        Position,
        PaperTrade,
        PaperEquitySnapshot,
        ServiceOrderOperation,
        ApprovedTargetHttpOperation,
        StrategyTargetApproval,
        StrategyTargetApprovalEvent,
        PaperRuntimeSnapshotScheduler,
    )
    for symbol in symbols:
        actual = Path(inspect.getsourcefile(symbol)).resolve()
        try:
            actual.relative_to(expected_afi.resolve())
        except ValueError:
            raise AssertionError((symbol.__name__, actual, expected_afi)) from None
    # The Host ready message carries the drill's fixed 13-origin map. The
    # boundary and AFI symbols above are independently checked in this parent.
    return origins


def _seed_afi_database(path: Path) -> None:
    engine = create_engine(f"sqlite:///{path}")
    try:
        models = (
            ServiceOrderOperation,
            StrategyTargetApproval,
            StrategyTargetApprovalEvent,
            ApprovedTargetHttpOperation,
            Account,
            Position,
            Order,
            PaperTrade,
            PaperEquitySnapshot,
        )
        Base.metadata.create_all(engine, tables=[model.__table__ for model in models])
        with Session(engine) as session:
            session.add(
                ServiceOrderOperation(
                    owner_subject="mig03-owner",
                    principal_ref="mig03-principal",
                    actor_ref="mig03-actor",
                    delegate_ref="",
                    operation_kind="manual_order",
                    operation_key_sha256=_AFI_HASH_1,
                    request_sha256=_AFI_HASH_2,
                    intent_id=drill._INTENT_ID,
                    scope_ref=_AFI_SCOPE,
                    account_ref=_AFI_ACCOUNT_REF,
                    manual_book_ref="mig03-manual-book",
                    last_service_code="OK",
                    last_record_state="FILLED",
                )
            )
            approval = StrategyTargetApproval(
                approval_ref="mig03-afi-approval",
                approval_version=1,
                record_version=2,
                state="APPROVED",
                owner_subject="mig03-owner",
                principal_ref="mig03-principal",
                actor_ref="mig03-actor",
                delegate_ref="",
                scope_ref=_AFI_SCOPE,
                execution_domain_ref="mig03-domain",
                account_ref=_AFI_ACCOUNT_REF,
                strategy_ref=_AFI_STRATEGY_REF,
                strategy_version=1,
                membership_version=1,
                membership_sha256=_AFI_HASH_3,
                target_ref="mig03-target",
                target_version=1,
                target_sha256=_AFI_HASH_1,
                canonical_target_json='{"symbol":"SYNTH-USD","side":"BUY"}',
                policy_ref="mig03-policy",
                policy_version=1,
                policy_sha256=_AFI_HASH_2,
                approved_by_subject="mig03-approver",
                approved_by_principal_ref="mig03-approver-principal",
                approved_by_actor_ref="mig03-approver-actor",
                approved_by_delegate_ref="",
                approved_at_ns=100,
                expires_at_ns=10_000,
                operation_key_sha256=_AFI_HASH_3,
                operation_request_sha256=_AFI_HASH_1,
                intent_id=drill._NEW_INTENT_ID,
                operation_reserved_at_ns=101,
            )
            session.add(approval)
            session.flush()
            session.add_all(
                [
                    StrategyTargetApprovalEvent(
                        approval_ref=approval.approval_ref,
                        approval_version=1,
                        record_version=1,
                        event_type="APPROVED",
                        owner_subject="mig03-owner",
                        actor_subject="mig03-approver",
                        principal_ref="mig03-approver-principal",
                        actor_ref="mig03-approver-actor",
                        delegate_ref="",
                        target_sha256=_AFI_HASH_1,
                        occurred_at_ns=100,
                    ),
                    StrategyTargetApprovalEvent(
                        approval_ref=approval.approval_ref,
                        approval_version=1,
                        record_version=2,
                        event_type="OPERATION_RESERVED",
                        owner_subject="mig03-owner",
                        actor_subject="mig03-actor",
                        principal_ref="mig03-principal",
                        actor_ref="mig03-actor",
                        delegate_ref="",
                        target_sha256=_AFI_HASH_1,
                        occurred_at_ns=101,
                        operation_key_sha256=_AFI_HASH_3,
                        intent_id=drill._NEW_INTENT_ID,
                    ),
                ]
            )
            session.add(
                ApprovedTargetHttpOperation(
                    owner_subject="mig03-owner",
                    principal_ref="mig03-principal",
                    actor_ref="mig03-actor",
                    delegate_ref="",
                    operation_key_sha256=_AFI_HASH_3,
                    request_sha256=_AFI_HASH_1,
                    approval_ref=approval.approval_ref,
                    approval_version=1,
                    target_sha256=_AFI_HASH_1,
                        intent_id=drill._NEW_INTENT_ID,
                    selector_ref="mig03-selector",
                    scope_ref=_AFI_SCOPE,
                    account_ref=_AFI_ACCOUNT_REF,
                    strategy_ref=_AFI_STRATEGY_REF,
                )
            )
            session.add(
                Account(
                    id=_AFI_ACCOUNT_ID,
                    user_id="mig03-user",
                    name="Synthetic MIG03 paper account",
                    initial_cash=100_000.0,
                    current_cash=99_899.9,
                    total_equity=99_999.9,
                    profit_loss=-0.1,
                )
            )
            session.add(
                Position(
                    id="mig03-afi-position-1",
                    account_id=_AFI_ACCOUNT_ID,
                    symbol="SYNTH-USD",
                    size=2.0,
                    avg_price=50.0,
                    market_value=100.0,
                    margin_value=0.0,
                    multiplier=1.0,
                    margin_rate=1.0,
                    commission_rate=0.001,
                    commission_amount=0.1,
                    unrealized_pnl=0.0,
                    unrealized_pnl_pct=0.0,
                    entry_price=50.0,
                )
            )
            session.add_all(
                [
                    Order(
                        id="mig03-afi-paper-filled",
                        account_id=_AFI_ACCOUNT_ID,
                        symbol="SYNTH-USD",
                        order_type="limit",
                        side="buy",
                        size=2.0,
                        price=50.0,
                        filled_size=2.0,
                        avg_fill_price=50.0,
                        status="filled",
                        commission=0.1,
                        slippage=0.0,
                    ),
                    Order(
                        id="mig03-afi-paper-pending",
                        account_id=_AFI_ACCOUNT_ID,
                        symbol="SYNTH-USD",
                        order_type="limit",
                        side="sell",
                        size=1.0,
                        price=51.0,
                        status="pending",
                    ),
                ]
            )
            session.add(
                PaperTrade(
                    id="mig03-afi-paper-trade-1",
                    account_id=_AFI_ACCOUNT_ID,
                    order_id="mig03-afi-paper-filled",
                    symbol="SYNTH-USD",
                    side="buy",
                    size=2.0,
                    price=50.0,
                    commission=0.1,
                    slippage=0.0,
                    pnl=0.0,
                    pnl_pct=0.0,
                )
            )
            session.add(
                PaperEquitySnapshot(
                    id="mig03-afi-equity-1",
                    user_id="mig03-user",
                    workspace_id="mig03-workspace",
                    unit_id="mig03-unit",
                    instance_id=_AFI_INSTANCE_ID,
                    paper_account_id=_AFI_ACCOUNT_ID,
                    source="mark_to_market",
                    total_equity=99_999.9,
                    cash=99_899.9,
                    position_value=100.0,
                    unrealized_pnl=0.0,
                    realized_pnl=0.0,
                    metadata_json={"run_ref": drill._RUN_ID},
                )
            )
            session.commit()
    except BaseException:
        with suppress(BaseException):
            engine.dispose()
        raise
    else:
        engine.dispose()


def _capture_afi(path: Path, configuration_sha256: str):
    engine = create_engine(f"sqlite:///{path}")
    try:
        with Session(engine) as session:
            return capture_afi_boundary(
                session,
                scope_ref=_AFI_SCOPE,
                account_ref=_AFI_ACCOUNT_REF,
                strategy_refs=(_AFI_STRATEGY_REF,),
                account_paper_accounts={_AFI_ACCOUNT_REF: _AFI_ACCOUNT_ID},
                paper_account_instances={_AFI_ACCOUNT_ID: _AFI_INSTANCE_ID},
                source_ref=str(path.resolve()),
                configuration_sha256=configuration_sha256,
            )
    finally:
        engine.dispose()


def _consumer_checkpoint_sha256(checkpoint: Any, path: Path) -> str:
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size <= 0 or info.st_size > _MAX_CONSUMER_DB_BYTES:
        raise AssertionError("consumer checkpoint size is unsupported")
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as source:
        while True:
            chunk = source.read(min(64 * 1024, _MAX_CONSUMER_DB_BYTES - total + 1))
            if not chunk:
                break
            total += len(chunk)
            if total > _MAX_CONSUMER_DB_BYTES:
                raise AssertionError("consumer checkpoint grew beyond its bound")
            digest.update(chunk)
    if total != info.st_size:
        raise AssertionError("consumer checkpoint changed during hashing")
    raw_digest = digest.hexdigest()
    state = {
        "checkpoint": {
            "stream_epoch": checkpoint.stream_epoch,
            "cursor": checkpoint.cursor,
            "pending_ack": checkpoint.pending_ack,
        },
        "projection_sha256": _consumer_projection_sha256(checkpoint.projection),
        "sqlite_sha256": raw_digest,
    }
    return _sha256_json(state)


def test_consumer_projection_hash_normalizes_immutable_json_values() -> None:
    projection = MappingProxyType(
        {
            "event_ids": ("event-1", "event-2"),
            "details": MappingProxyType({"amount": 12.5, "accepted": True}),
            "last_sequence": 2,
        }
    )
    expected = {
        "event_ids": ["event-1", "event-2"],
        "details": {"amount": {"float_hex": (12.5).hex()}, "accepted": True},
        "last_sequence": 2,
    }
    assert _consumer_projection_json_value(projection) == expected
    assert _consumer_projection_sha256(projection) == hashlib.sha256(
        json.dumps(
            expected,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8", errors="strict")
    ).hexdigest()
    with pytest.raises(AssertionError, match="keys must be strings"):
        _consumer_projection_json_value(MappingProxyType({1: "invalid"}))
    with pytest.raises(AssertionError, match="non-finite"):
        _consumer_projection_json_value({"value": float("nan")})
    with pytest.raises(AssertionError, match="unsupported"):
        _consumer_projection_json_value({"value": object()})


def _consumer_facts(
    checkpoint: Any,
    path: Path,
    config_sha256: str,
    stopped_consumers: tuple[ConsumerStopEvidence, ...],
):
    state_digest = _consumer_checkpoint_sha256(checkpoint, path)
    identity_digest = consumer_inventory_sha256(stopped_consumers)
    return retained_external_participant(
        "consumers",
        source_ref=str(path.resolve()),
        configuration_sha256=config_sha256,
        state_sha256=state_digest,
        identity_sha256=identity_digest,
        schema_sha256=_sha256_json("bt_api_py.service_account_projection.v1"),
        row_count=1,
    ), state_digest, identity_digest


def _execution_handoff_facts(
    path: Path,
) -> tuple[str, str, tuple[tuple[Any, ...], ...], int, int]:
    """Fingerprint every execution row except the renewable writer lease.

    The lease row is captured separately because starting the destination Host
    must renew or reacquire it. Schema and every other table remain byte-semantic
    parts of the handoff comparison.
    """

    info = path.stat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_size <= 0
        or info.st_size > 32 * 1024 * 1024
    ):
        raise AssertionError("execution store size is unsupported")
    connection = sqlite3.connect(
        path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1.0
    )
    try:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")
        quick_check = tuple(row[0] for row in connection.execute("PRAGMA quick_check"))
        if not quick_check or any(value != "ok" for value in quick_check):
            raise AssertionError("execution store failed quick_check")
        tables = tuple(
            sorted(
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%'"
                )
            )
        )
        if "execution_writer_leases" not in tables:
            raise AssertionError("execution writer lease table is unavailable")
        lease_columns = tuple(
            row[1]
            for row in connection.execute(
                'PRAGMA table_info("execution_writer_leases")'
            )
        )
        if lease_columns != ("scope_key", "owner_id", "fencing_token", "expires_at_ns"):
            raise AssertionError("execution writer lease schema is unsupported")
        schema_rows: list[tuple[Any, ...]] = [
            (
                "sqlite_master",
                tuple(
                    tuple(row)
                    for row in connection.execute(
                        "SELECT type,name,tbl_name,sql FROM sqlite_master "
                        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
                    )
                ),
            )
        ]
        table_rows: list[tuple[str, tuple[bytes, ...]]] = []
        lease_rows: tuple[tuple[Any, ...], ...] | None = None
        total_rows = 0
        total_bytes = 0
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            columns = tuple(connection.execute(f"PRAGMA table_info({quoted})"))
            if not columns:
                raise AssertionError("execution table has no columns")
            schema_rows.append(
                (
                    table,
                    tuple(
                        (row[1], row[2], int(row[3]), row[4], int(row[5]))
                        for row in columns
                    ),
                )
            )
            row_payloads: list[bytes] = []
            cursor = connection.execute(f"SELECT * FROM {quoted}")
            while True:
                batch = cursor.fetchmany(256)
                if not batch:
                    break
                for row in batch:
                    total_rows += 1
                    if total_rows > 100_000:
                        raise AssertionError("execution store row bound exceeded")
                    values = tuple(row)
                    if table == "execution_writer_leases":
                        if lease_rows is not None:
                            lease_rows = (*lease_rows, values)
                        else:
                            lease_rows = (values,)
                        continue
                    payload = _canonical_json_bytes(
                        [_sqlite_value(value) for value in values]
                    )
                    total_bytes += len(payload)
                    if total_bytes > 32 * 1024 * 1024:
                        raise AssertionError("execution store byte bound exceeded")
                    row_payloads.append(payload)
            row_payloads.sort()
            table_rows.append((table, tuple(row_payloads)))
        if lease_rows is None:
            raise AssertionError("execution writer lease row is missing")
        schema_payload = _canonical_json_bytes(schema_rows)
        if len(schema_payload) + total_bytes > 32 * 1024 * 1024:
            raise AssertionError("execution store schema and rows exceed their bound")
        stable_state_sha256 = _sqlite_state_sha256(schema_payload, table_rows)
        return (
            stable_state_sha256,
            hashlib.sha256(schema_payload).hexdigest(),
            lease_rows,
            total_rows,
            time.time_ns(),
        )
    finally:
        connection.close()


def _assert_execution_lease_handoff(
    before: tuple[str, str, tuple[tuple[Any, ...], ...], int, int],
    after: tuple[str, str, tuple[tuple[Any, ...], ...], int, int],
) -> None:
    before_state, before_schema, before_rows, before_count, before_observed_ns = before
    after_state, after_schema, after_rows, after_count, after_observed_ns = after
    if before_schema != after_schema or before_state != after_state:
        raise AssertionError("execution schema or business rows changed during handoff")
    if before_count != after_count:
        raise AssertionError("execution participant row count changed during handoff")
    _assert_execution_source_lease(before)
    if len(after_rows) != 1:
        raise AssertionError("execution writer lease scope count changed")
    old_scope, old_owner, old_token, old_expiry = before_rows[0]
    new_scope, new_owner, new_token, new_expiry = after_rows[0]
    if (
        old_scope != drill._EXECUTION_SCOPE.account_key
        or new_scope != old_scope
        or type(old_owner) is not str
        or not old_owner
        or new_owner != old_owner
        or old_owner != "execution-order-entry"
        or type(old_token) is not int
        or old_token <= 0
        or type(new_token) is not int
        or new_token < old_token
        or (old_expiry <= before_observed_ns and new_token <= old_token)
        or type(old_expiry) is not int
        or old_expiry <= 0
        or type(new_expiry) is not int
        or new_expiry <= old_expiry
        or new_expiry <= after_observed_ns
    ):
        raise AssertionError("execution writer lease handoff is invalid")


def _assert_execution_source_lease(
    facts: tuple[str, str, tuple[tuple[Any, ...], ...], int, int],
) -> None:
    _state_sha256, _schema_sha256, lease_rows, _row_count, _observed_at_ns = facts
    if len(lease_rows) != 1:
        raise AssertionError("execution writer lease scope count changed")
    scope_key, owner_id, fencing_token, expires_at_ns = lease_rows[0]
    if (
        scope_key != drill._EXECUTION_SCOPE.account_key
        or owner_id != "execution-order-entry"
        or type(owner_id) is not str
        or type(fencing_token) is not int
        or fencing_token <= 0
        or type(expires_at_ns) is not int
        or expires_at_ns <= 0
    ):
        raise AssertionError("execution source writer lease is invalid")


def _assert_execution_participant_handoff(
    before: Any,
    after: Any,
    before_facts: tuple[str, str, tuple[tuple[Any, ...], ...], int, int],
    after_facts: tuple[str, str, tuple[tuple[Any, ...], ...], int, int],
) -> None:
    old = before.evidence
    new = after.evidence
    stable_fields = (
        "participant_id",
        "disposition",
        "source_ref",
        "configuration_sha256",
        "schema_sha256",
        "row_count",
    )
    if any(getattr(old, field) != getattr(new, field) for field in stable_fields):
        raise AssertionError("execution participant metadata changed during handoff")
    if (
        old.schema_sha256 != before_facts[1]
        or new.schema_sha256 != after_facts[1]
        or old.row_count != before_facts[3]
        or new.row_count != after_facts[3]
    ):
        raise AssertionError("execution participant schema or row inventory is inconsistent")
    _assert_execution_lease_handoff(before_facts, after_facts)


def _sdk_state_conservation(
    execution_path: Path,
    risk_path: Path,
    gateway_path: Path,
    consumer_checkpoint: Any,
    afi_snapshot: Any,
    target_registration: Any,
) -> StateConservation:
    execution = migration._execution_projection(execution_path)
    records = {row[1]: row for row in execution["records"]}
    filled = records[drill._INTENT_ID]
    unknown = records[drill._NEW_INTENT_ID]
    provider_id = filled[3]
    assert filled[2] == "FILLED" and provider_id == migration._SOURCE_PROVIDER_ORDER_ID
    assert unknown[2] == "UNKNOWN" and unknown[3] is None
    risk_unknown = drill._risk_rows(str(risk_path), drill._NEW_INTENT_ID)
    permit_ids = tuple(row[1] for row in risk_unknown["reservations"])
    assert len(permit_ids) == 1
    dispatch_freeze = f"dispatch-inflight:{drill._NEW_INTENT_ID}"
    assert risk_unknown["freezes"] == ((dispatch_freeze, dispatch_freeze, 1),)
    assert afi_snapshot.reservation_ids == tuple(
        sorted((drill._INTENT_ID, drill._NEW_INTENT_ID))
    )
    assert (
        "afi_operations.manual_key",
        _AFI_HASH_1,
        "execution.intent_id",
        drill._INTENT_ID,
    ) in afi_snapshot.identity_links
    assert (
        "afi_approval_ref",
        "mig03-afi-approval",
        "execution.intent_id",
        drill._NEW_INTENT_ID,
    ) in afi_snapshot.identity_links

    connection = sqlite3.connect(gateway_path)
    try:
        event_rows = tuple(
            connection.execute(
                "SELECT event_id,payload_json FROM gateway_events "
                "WHERE strategy_scope=? ORDER BY sequence",
                (drill._JOURNAL_STRATEGY_SCOPE,),
            )
        )
    finally:
        connection.close()
    gateway_positions = tuple(
        (
            f"gateway:{event_id}",
            str(json.loads(payload_json).get("quantity", "0")),
        )
        for event_id, payload_json in event_rows
    )
    cursor_rows = drill._journal_projection(str(gateway_path))["progress"]
    stream_cursors = tuple(
        (f"{row[1]}:{row[2]}", row[3], int(row[4]), int(row[5]))
        for row in cursor_rows
    )
    links = (
        (
            "legacy_provider_order_id",
            provider_id,
            "target_order_ref",
            target_registration.order_ref,
        ),
        (
            "execution_intent_id",
            drill._INTENT_ID,
            "provider_order_id",
            provider_id,
        ),
        (
            "execution_intent_id",
            drill._NEW_INTENT_ID,
            "provider_order_id",
            "UNALLOCATED_UNKNOWN",
        ),
        (
            "execution_scope_strategy",
            drill._STRATEGY,
            "target_strategy_ref",
            target_registration.strategy_ref,
        ),
        (
            "execution_run_ref",
            drill._RUN_ID,
            "target_run_ref",
            target_registration.run_ref,
        ),
        (
            "afi_account_ref",
            _AFI_ACCOUNT_REF,
            "target_account_ref",
            target_registration.account_ref,
        ),
        (
            "afi_strategy_ref",
            _AFI_STRATEGY_REF,
            "target_strategy_ref",
            target_registration.strategy_ref,
        ),
        (
            "paper_run_ref",
            drill._RUN_ID,
            "execution_run_ref",
            drill._RUN_ID,
        ),
        (
            "paper_run_ref",
            drill._RUN_ID,
            "target_run_ref",
            target_registration.run_ref,
        ),
        (
            "execution_scope_instrument",
            "BTC-USDT",
            "target_instrument",
            target_registration.booked_instrument,
        ),
        *afi_snapshot.identity_links,
    )
    amounts = (
        ("execution:filled_quantity", filled[4]),
        ("execution:average_price", filled[5]),
        ("execution:commission", filled[6]),
        ("risk:reserved_notional", str(risk_unknown["reservations"][0][4])),
        *afi_snapshot.amounts,
    )
    positions = (*gateway_positions, *afi_snapshot.positions)
    unresolved_ids = (
        drill._NEW_INTENT_ID,
        "mig03-afi-paper-pending",
        "mig03-integrated-unallocated-legacy-event",
    )
    reservations = (*permit_ids, *afi_snapshot.reservation_ids)
    cursors = (
        (
            "consumer:old-account-projection",
            consumer_checkpoint.stream_epoch,
            consumer_checkpoint.cursor,
            consumer_checkpoint.cursor,
        ),
        *stream_cursors,
    )
    return StateConservation(
        identity_links=links,
        amounts=amounts,
        positions=positions,
        unresolved_ids=unresolved_ids,
        reservation_ids=reservations,
        stream_cursors=cursors,
    )


def test_mig03_full_boundary_inventory_handoffs_same_paths_and_conserves_state(
    tmp_path: Path,
) -> None:
    source_origins = _assert_all_fixed_origins()
    context = multiprocessing.get_context("spawn")
    endpoint = drill._endpoint()
    server_public, server_secret = zmq.curve_keypair()
    strategy_public, strategy_secret = zmq.curve_keypair()
    keys = {
        "server_public": server_public,
        "server_secret": server_secret,
        "strategy_public": strategy_public,
        "strategy_secret": strategy_secret,
    }
    raw_config = drill._service_config(endpoint)
    config_sha256 = _sha256_json(raw_config)
    registry_path = tmp_path / "mig03-boundary-registry.sqlite3"
    execution_path = tmp_path / "mig03-boundary-execution.sqlite3"
    risk_path = tmp_path / "mig03-boundary-risk.sqlite3"
    provider_path = tmp_path / "mig03-boundary-provider.sqlite3"
    gateway_path = tmp_path / "mig03-boundary-gateway.sqlite3"
    map_path = tmp_path / "mig03-boundary-map.sqlite3"
    projection_path = tmp_path / "mig03-boundary-consumer.sqlite3"
    afi_path = tmp_path / "mig03-boundary-afi.sqlite3"
    migration._seed_gateway_events(gateway_path)
    _seed_afi_database(afi_path)
    completed_arguments = drill._submit_arguments(
        drill._INTENT_ID,
        "mig03-boundary-completed-fill",
        quantity="2",
        price="5000",
    )
    migration._seed_preexisting_completed_fill(execution_path, completed_arguments)

    hosts: list[dict[str, Any]] = []
    refusal_records: list[dict[str, Any]] = []
    runtimes: list[Any] = []
    consumers: list[Any] = []
    forced: list[str] = []
    cleanup_failures: list[str] = []
    try:
        old_host = drill._start_host_process(
            context,
            migration._migration_host_process,
            (
                raw_config,
                keys,
                migration._OLD_DEPLOYMENT_EPOCH,
                str(registry_path),
                str(execution_path),
                str(risk_path),
                str(provider_path),
                str(gateway_path),
            ),
            label="mig03-boundary-old-host",
        )
        hosts.append(old_host)
        ready_old = drill._receive_host_message(old_host)
        assert ready_old[0] == "ready" and ready_old[4] == migration._OLD_DEPLOYMENT_EPOCH
        assert ready_old[3] == source_origins
        old_pid = old_host["pid"]

        old_runtime = drill._new_runtime(
            endpoint,
            keys,
            session_id="mig03-boundary-old-session",
        )
        runtimes.append(old_runtime)
        assert old_runtime.start().degraded_count == 0
        completed = drill._execution_client(old_runtime).submit(**completed_arguments)
        assert completed.state == "FILLED"
        assert completed.filled_quantity == pytest.approx(2.0)
        unknown = drill._execution_client(old_runtime).submit(
            **drill._submit_arguments(
                drill._NEW_INTENT_ID,
                "mig03-boundary-unknown",
                quantity="2",
                price="50000",
            )
        )
        assert unknown.state == "UNKNOWN" and unknown.dispatch_attempts == 1
        consumer = migration._target_projection(
            old_runtime,
            projection_path,
            account_ref=drill._ACCOUNT,
        )
        consumers.append(consumer)
        consumer_checkpoint = consumer.poll()
        assert consumer_checkpoint.stream_epoch and consumer_checkpoint.cursor == 1
        consumer.close()
        consumers.remove(consumer)
        old_facts = drill._host_command(old_host, "facts")[1]
        assert old_facts["service_epoch"] == ready_old[2]
        assert old_facts["provider_dispatch_counts"] == {"intent_1": 0, "intent_2": 1}
        old_unknown_risk = old_facts["risk_rows_intent_2"]
        dispatch_freeze = f"dispatch-inflight:{drill._NEW_INTENT_ID}"
        assert old_unknown_risk["freezes"] == ((dispatch_freeze, dispatch_freeze, 1),)
        assert dispatch_freeze in old_facts["risk_snapshot"]["active_freeze_reasons"]
        assert old_facts["risk_snapshot"]["increase_count"] == 1
        assert old_facts["risk_snapshot"]["increase_notional"] == Decimal("100000")

        old_runtime.close()
        assert old_runtime.cleanup_failures == 0
        runtimes.remove(old_runtime)
        closed_old = drill._close_host_normally(old_host)
        assert closed_old[1] == "closed" and old_host["normal_exit_code"] == 0

        # Exercise the accepted GW03 expiry behavior against the exact state
        # the consumer left behind: the local cursor stays at one while the
        # source journal compacts through two and retains a contiguous tail.
        # Its stream epoch is unchanged, so a new process may rebuild; an epoch
        # mismatch remains a hard refusal below.
        journal = migration.GatewayEventJournal(
            gateway_path,
            clock=lambda: drill._JOURNAL_CLOCK_SECONDS,
        )
        captured = journal.capture_snapshot(
            drill._JOURNAL_ACCOUNT_SCOPE,
            drill._JOURNAL_STRATEGY_SCOPE,
            "mig03-boundary-snapshot-through-two",
            expected_cursor=2,
            payload={
                "last_sequence": 2,
                "event_ids": [
                    "mig03-integrated-event-1",
                    "mig03-integrated-event-2",
                ],
            },
            captured_at=drill._JOURNAL_CLOCK_SECONDS,
        )
        assert captured.cursor == 2
        tail = journal.append(
            drill._JOURNAL_ACCOUNT_SCOPE,
            drill._JOURNAL_STRATEGY_SCOPE,
            "mig03-boundary-event-3",
            "position_update",
            {"sequence": 3, "quantity": "5"},
            observed_at=drill._JOURNAL_CLOCK_SECONDS,
        )
        assert tail.sequence == 3

        # The fixed local participants are read only after the old service and
        # its client have closed. The old process exit plus later server-side
        # old-epoch refusal are local fencing evidence, not an external fence.
        registry_before = capture_sqlite_participant(
            "registry", registry_path, configuration_sha256=config_sha256
        )
        execution_before = capture_sqlite_participant(
            "execution", execution_path, configuration_sha256=config_sha256
        )
        execution_handoff_pre_import = _execution_handoff_facts(execution_path)
        _assert_execution_source_lease(execution_handoff_pre_import)
        risk_before = capture_sqlite_participant(
            "risk", risk_path, configuration_sha256=config_sha256
        )
        gateway_before = capture_sqlite_participant(
            "gateway", gateway_path, configuration_sha256=config_sha256
        )
        scheduler_stop = asyncio.run(_stop_active_snapshot_scheduler())
        assert scheduler_stop.tasks_before == 1 and scheduler_stop.tasks_after == 0
        assert scheduler_stop.stopped_task_names == (
            f"paper-runtime-snapshot:{_AFI_INSTANCE_ID}",
        )
        consumer_config_sha256 = _sha256_json(
            {
                "database_path": str(projection_path.resolve()),
                "consumer_id": "old-account-projection",
                "gateway_scope_ref": drill._GATEWAY_SCOPE_REF,
                "gateway_principal": drill._STRATEGY_PRINCIPAL,
            }
        )
        projection_checkpoint_sha256 = _consumer_checkpoint_sha256(
            consumer_checkpoint, projection_path
        )
        stopped_consumers = (
            ConsumerStopEvidence(
                "old-strategy-runtime",
                os.getpid(),
                close_acknowledged=(
                    old_runtime.state == "closed" and old_runtime.cleanup_failures == 0
                ),
                checkpoint_sha256=_sha256_json(
                    {
                        "cleanup_failures": old_runtime.cleanup_failures,
                        "state": old_runtime.state,
                    }
                ),
            ),
            ConsumerStopEvidence(
                "old-account-projection",
                os.getpid(),
                close_acknowledged=True,
                checkpoint_sha256=projection_checkpoint_sha256,
            ),
            ConsumerStopEvidence(
                "afi-paper-snapshot-scheduler",
                os.getpid(),
                close_acknowledged=scheduler_stop.tasks_after == 0,
                checkpoint_sha256=_sha256_json(
                    {
                        "tasks_before": scheduler_stop.tasks_before,
                        "stopped_task_names": scheduler_stop.stopped_task_names,
                        "tasks_after": scheduler_stop.tasks_after,
                    }
                ),
            ),
        )
        consumer_evidence, consumer_digest, consumer_identity = _consumer_facts(
            consumer_checkpoint,
            projection_path,
            consumer_config_sha256,
            stopped_consumers,
        )
        afi_config_sha256 = _afi_config_sha256(afi_path)
        afi_before = _capture_afi(afi_path, afi_config_sha256)
        registry_identity_before = _registry_identity_projection(registry_path)

        _assert_group_component_absent(raw_config)
        group_evidence = not_applicable_participant(
            "groups",
            evidence_ref=f"service-config-sha256:{config_sha256}:no-group-component",
            configuration_sha256=config_sha256,
        )
        afi_evidence = tuple(
            retained_external_participant(**item)
            for item in afi_before.boundary_participants()
        )
        participants_before = (
            registry_before.evidence,
            execution_before.evidence,
            risk_before.evidence,
            group_evidence,
            gateway_before.evidence,
            consumer_evidence,
            *afi_evidence,
        )

        missing_participants = tuple(
            item for item in participants_before if item.participant_id != "afi_paper"
        )
        with pytest.raises(MigrationBoundaryError) as incomplete:
            prepare_migration_boundary(
                migration_id="mig03-boundary-incomplete-r1",
                source_epoch=migration._OLD_DEPLOYMENT_EPOCH,
                destination_epoch=migration._NEW_DEPLOYMENT_EPOCH,
                old_writer_pid=old_pid,
                old_writer_exit_code=0,
                consumers=stopped_consumers,
                participants=missing_participants,
                fence_reference="synthetic-local-host-close-only",
            )
        assert incomplete.value.code == CODE_BOUNDARY_INCOMPLETE
        assert read_migration_marker(registry_path) is None
        assert not map_path.exists()

        plan = prepare_migration_boundary(
            migration_id="mig03-boundary-full-r1",
            source_epoch=migration._OLD_DEPLOYMENT_EPOCH,
            destination_epoch=migration._NEW_DEPLOYMENT_EPOCH,
            old_writer_pid=old_pid,
            old_writer_exit_code=old_host["normal_exit_code"],
            consumers=stopped_consumers,
            participants=participants_before,
            fence_reference="synthetic-local-host-exit-and-old-epoch-refusal-required",
        )

        # Inventory must be complete before the offline map writes begin. The
        # import is still map-only and does not mutate any retained source DB.
        with MigrationIdentityStore(map_path) as map_store:
            manifest = migration._build_import_manifest(
                execution_path,
                endpoint=endpoint,
                provider_order_id=migration._SOURCE_PROVIDER_ORDER_ID,
            )
            imported = apply_migration_import(manifest, execution_path, map_store)
            assert len(imported.mappings) == 5
            target_registration = migration._readback_target_registration(map_store, manifest)
            assert target_registration.order_ref == "mig03-integrated-target-order-ref"
            assert target_registration.booked_instrument == "BTC-USDT"
            assert map_store.resolve(
                migration.SOURCE_BARE_ORDER_ID,
                migration._SOURCE_PROVIDER_ORDER_ID,
            ).mapping.target_value == target_registration.order_ref

        # Capture the actual source projection immediately before arming. The
        # destination Host may renew this row in execution_writer_leases, so
        # keep that lease transition separate from the conserved business rows.
        execution_handoff_before = _execution_handoff_facts(execution_path)
        _assert_execution_source_lease(execution_handoff_before)
        if execution_handoff_pre_import[:4] != execution_handoff_before[:4]:
            raise AssertionError("offline map import changed the source execution store")
        before_conservation = _sdk_state_conservation(
            execution_path,
            risk_path,
            gateway_path,
            consumer_checkpoint,
            afi_before,
            target_registration,
        )

        armed = arm_migration_boundary(registry_path, plan)
        assert armed.status == "COMMITTED"

        new_host = drill._start_host_process(
            context,
            migration._migration_host_process,
            (
                manifest["target"]["service_config"]["raw"],
                keys,
                migration._NEW_DEPLOYMENT_EPOCH,
                str(registry_path),
                str(execution_path),
                str(risk_path),
                str(provider_path),
                str(gateway_path),
            ),
            label="mig03-boundary-new-host",
        )
        hosts.append(new_host)
        ready_new = drill._receive_host_message(new_host)
        assert ready_new[0] == "ready" and ready_new[4] == migration._NEW_DEPLOYMENT_EPOCH
        new_runtime = migration._new_mapped_runtime(
            endpoint,
            keys,
            target_registration,
            session_id="mig03-boundary-new-session",
        )
        runtimes.append(new_runtime)
        assert new_runtime.start().degraded_count == 0

        replay_arguments = drill._submit_arguments(
            drill._INTENT_ID,
            "mig03-boundary-completed-fill",
            quantity="2",
            price="5000",
        )
        replay_arguments["instrument"] = target_registration.booked_instrument
        replay = drill._execution_client(new_runtime).submit(**replay_arguments)
        assert replay.state == "FILLED" and replay.dispatch_attempts == 1
        assert replay.provider_order_id == migration._SOURCE_PROVIDER_ORDER_ID
        assert migration._provider_projection(provider_path) == ((1, drill._NEW_INTENT_ID),)

        new_facts = drill._host_command(new_host, "facts")[1]
        new_unknown_risk = new_facts["risk_rows_intent_2"]
        dispatch_freeze = f"dispatch-inflight:{drill._NEW_INTENT_ID}"
        assert new_unknown_risk["freezes"] == ((dispatch_freeze, dispatch_freeze, 1),)
        assert dispatch_freeze in new_facts["risk_snapshot"]["active_freeze_reasons"]
        assert new_facts["risk_snapshot"]["increase_count"] == 1
        assert new_facts["risk_snapshot"]["increase_notional"] == Decimal("100000")

        sealed = read_migration_marker(registry_path)
        assert sealed is not None and sealed.sealed
        assert sealed.first_write_fact_kind == "dispatch"
        refused_old = drill._start_host_process(
            context,
            migration._migration_refusal_process,
            (raw_config, keys, str(registry_path), migration._OLD_DEPLOYMENT_EPOCH),
            label="mig03-boundary-old-epoch-refusal",
        )
        refusal_records.append(refused_old)
        refusal = drill._receive_host_message(refused_old)
        assert refusal[0] == "refused"
        assert refusal[1] == REFUSAL_REVERSE_MIGRATION_REFUSED
        assert refusal[4] == ()
        process = refused_old["process"]
        process.join(15)
        assert not process.is_alive() and process.exitcode == 0
        refused_old["closed"] = True
        refused_old["normal_exit_code"] = process.exitcode
        refused_old["pipe"].close()
        process.close()
        require_old_epoch_refused_after_new_write(
            plan,
            serving_epoch=ready_new[4],
            first_write_fact_kind=sealed.first_write_fact_kind,
            refusal_code=refusal[1],
            resource_provider_calls=len(refusal[4]),
        )

        unknown_after = drill._execution_client(new_runtime).query(drill._NEW_INTENT_ID)
        assert unknown_after is not None and unknown_after.state == "UNKNOWN"
        assert unknown_after.permit_reference == unknown.permit_reference
        assert unknown_after.dispatch_attempts == unknown.dispatch_attempts == 1
        assert migration._provider_projection(provider_path) == ((1, drill._NEW_INTENT_ID),)

        registry_after = capture_sqlite_participant(
            "registry",
            registry_path,
            configuration_sha256=config_sha256,
            expected_schema_sha256=registry_before.schema_sha256,
        )
        execution_after = capture_sqlite_participant(
            "execution",
            execution_path,
            configuration_sha256=config_sha256,
            expected_schema_sha256=execution_before.schema_sha256,
        )
        execution_handoff_after = _execution_handoff_facts(execution_path)
        risk_after = capture_sqlite_participant(
            "risk",
            risk_path,
            configuration_sha256=config_sha256,
            expected_schema_sha256=risk_before.schema_sha256,
        )
        gateway_after = capture_sqlite_participant(
            "gateway",
            gateway_path,
            configuration_sha256=config_sha256,
            expected_schema_sha256=gateway_before.schema_sha256,
        )
        afi_after = _capture_afi(afi_path, afi_config_sha256)
        consumer_evidence_after, consumer_digest_after, consumer_identity_after = (
            _consumer_facts(
                consumer_checkpoint,
                projection_path,
                consumer_config_sha256,
                stopped_consumers,
            )
        )
        afi_evidence_after = tuple(
            retained_external_participant(**item)
            for item in afi_after.boundary_participants()
        )
        participants_after = (
            registry_after.evidence,
            execution_after.evidence,
            risk_after.evidence,
            group_evidence,
            gateway_after.evidence,
            consumer_evidence_after,
            *afi_evidence_after,
        )
        assert consumer_digest_after == consumer_digest
        assert consumer_identity_after == consumer_identity
        _assert_registry_handoff_preserved(
            registry_identity_before,
            _registry_identity_projection(registry_path),
            registry_path,
            ready_new[2],
        )
        _assert_execution_participant_handoff(
            execution_before,
            execution_after,
            execution_handoff_before,
            execution_handoff_after,
        )
        # The shared comparator is exact for the other participants. Execution
        # was separately checked above against its complete source projection,
        # schema, row count and the sole permitted writer-lease transition.
        participants_after_with_execution_source = tuple(
            execution_before.evidence
            if item.participant_id == "execution"
            else item
            for item in participants_after
        )
        assert_participant_states_conserved(
            participants_before, participants_after_with_execution_source
        )

        after_conservation = _sdk_state_conservation(
            execution_path,
            risk_path,
            gateway_path,
            consumer_checkpoint,
            afi_after,
            target_registration,
        )
        assert_state_conserved(before_conservation, after_conservation)
        assert afi_before.amounts == afi_after.amounts
        assert afi_before.positions == afi_after.positions

        # A service restart keeps the same journal stream epoch. Reject a
        # client that supplies any other epoch without changing progress, then
        # prove the preserved projection's expired cursor can be rebuilt from
        # the snapshot and same-epoch tail on the new runtime.
        target_account_binding = drill._binding_of(new_runtime, "store.account")
        assert target_account_binding.scope.account_ref == target_registration.account_ref
        assert target_account_binding.scope.strategy_ref == target_registration.strategy_ref
        assert target_account_binding.scope.run_id == target_registration.run_ref
        progress_before_cursor_recovery = new_runtime.call(
            "store.account", "account.progress", {}
        )["consumer_progress"]
        assert progress_before_cursor_recovery == {
            "stream_epoch": consumer_checkpoint.stream_epoch,
            "offered_cursor": 1,
            "acknowledged_cursor": 1,
        }
        mismatch_transport = migration.ZmqTransportFactory(
            {
                drill._CLIENT_SECURITY_REF: migration.ClientCurveKeys(
                    keys["server_public"],
                    keys["strategy_public"],
                    keys["strategy_secret"],
                )
            }
        )(
            drill._client_config(endpoint).communication["profiles"][
                drill._CLIENT_CONNECT_PROFILE
            ],
            drill._SERVICE_IDENTITY,
        )
        try:
            mismatch_reply = drill._direct_request(
                mismatch_transport,
                identity=new_runtime.identity,
                scope=target_account_binding.scope,
                operation="account.read_after",
                binding=target_account_binding,
                arguments={
                    "cursor": consumer_checkpoint.cursor,
                    "stream_epoch": "mig03-boundary-wrong-stream-epoch",
                    "limit": 1,
                    "track_delivery": True,
                },
            )
            assert mismatch_reply.payload["ok"] is False
            assert mismatch_reply.payload["code"] == "HANDLER_FAILED"
        finally:
            mismatch_transport.close()
        assert new_runtime.call(
            "store.account", "account.progress", {}
        )["consumer_progress"] == progress_before_cursor_recovery

        same_epoch_tail = new_runtime.call(
            "store.account",
            "account.read_after",
            {
                "cursor": consumer_checkpoint.cursor,
                "stream_epoch": consumer_checkpoint.stream_epoch,
                "limit": 1,
                "track_delivery": True,
            },
        )
        assert [event["event_id"] for event in same_epoch_tail["events"]] == [
            "mig03-boundary-event-3"
        ]
        assert same_epoch_tail["consumer_progress"] == {
            "stream_epoch": consumer_checkpoint.stream_epoch,
            "offered_cursor": 3,
            "acknowledged_cursor": 1,
        }
        migrated_consumer = migration._target_projection(
            new_runtime,
            projection_path,
            account_ref=target_registration.account_ref,
        )
        consumers.append(migrated_consumer)
        recovered = migrated_consumer.poll()
        assert recovered.stream_epoch == consumer_checkpoint.stream_epoch
        assert recovered.cursor == 3 and recovered.pending_ack is False
        assert recovered.projection == {
            "last_sequence": 3,
            "event_ids": (
                "mig03-integrated-event-1",
                "mig03-integrated-event-2",
                "mig03-boundary-event-3",
            ),
        }
        migrated_consumer.close()
        consumers.remove(migrated_consumer)
        cursor_recovery_marker = read_migration_marker(registry_path)
        assert cursor_recovery_marker == sealed
        assert cursor_recovery_marker is not None
        assert cursor_recovery_marker.first_write_fact_kind == "dispatch"

        new_runtime.close()
        assert new_runtime.cleanup_failures == 0
        runtimes.remove(new_runtime)
        drill._close_host_normally(new_host)
    finally:
        for consumer in reversed(consumers):
            with suppress(BaseException):
                consumer.close()
        for runtime in reversed(runtimes):
            with suppress(BaseException):
                runtime.close()
        for host_record in reversed(hosts):
            drill._bounded_reap_owned_host(host_record, forced, cleanup_failures)
        for refusal_record in reversed(refusal_records):
            drill._bounded_reap_owned_host(refusal_record, forced, cleanup_failures)
        if sys.exc_info()[1] is None:
            drill._assert_no_fallback_cleanup(forced, cleanup_failures)
            assert all(runtime.cleanup_failures == 0 for runtime in runtimes)
