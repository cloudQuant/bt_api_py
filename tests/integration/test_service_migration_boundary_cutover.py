"""MIG03 cutover with stopped process owners and a retained nonempty group.

This focused successor complements the R188/R235 drills.  It uses synthetic
SQLite stores and the normal SDK/AFI entry points, closes every source owner
through a child-process handshake, and only enables the destination after the
new SDK and group registrations have read back the retained facts.  The local
provider is synthetic; this does not claim an external provider fence.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import multiprocessing
import os
import sqlite3
from contextlib import suppress
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole
from bt_api_execution import (
    GroupBudgetReservationState,
    GroupOrderSnapshot,
    GroupOrderState,
)

from bt_api_py.service_host import TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_migration_boundary import (
    CODE_BOUNDARY_NOT_QUIESCENT,
    ConfiguredConsumer,
    ConsumerStopEvidence,
    MigrationBoundaryError,
    arm_migration_boundary,
    assert_participant_states_conserved,
    assert_state_conserved,
    capture_group_participant,
    capture_sqlite_participant,
    configured_consumer_inventory_sha256,
    consumer_inventory_sha256,
    derive_service_consumer_inventory,
    prepare_migration_boundary,
    require_old_epoch_refused_after_new_write,
    require_consumer_inventory_compatible,
    retained_external_participant,
)
from bt_api_py.service_migration_gate import (
    REFUSAL_REVERSE_MIGRATION_REFUSED,
    STATUS_COMMITTED,
    STATUS_ENABLED,
    STATUS_SEALED,
    MigrationGateError,
    read_migration_marker,
    rollback_migration_cutover,
)
from bt_api_py.service_migration_import import apply_migration_import
from bt_api_py.service_migration_map import MigrationIdentityStore
from tests.integration import test_service_migration_boundary_enable as enable
from tests.integration import test_service_migration_boundary_inventory as r235
from tests.unit import test_service_execution_group as group
from tests.unit import test_service_migration_drill as drill
from tests.unit import test_service_migration_integration as migration


_GROUP_NOW_NS = 1_900_000_000_000_000_000
_MIGRATION_ID = "mig03-real-consumer-close-group-cutover"
# The normal R235 composition still exercises an unfenced AFI snapshot task.
# A dedicated successor can flip this flag while composing the same cutover
# with the production AFI12 ACTIVE receipt, in which case the destination
# correctly restores no legacy scheduler task.
_EXPECT_ACTIVE_AFI12_IMPORT = False


def _sha256_json(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8", errors="strict")
    return hashlib.sha256(raw).hexdigest()


def _group_service_config(endpoint: str) -> dict[str, Any]:
    raw = drill._service_config(endpoint)
    raw["service_id"] = "mig03-group-coordinator-service"
    raw["scope_ref"] = "mig03-group-coordinator-root"
    raw["registration_policy_ref"] = "mig03-group-registration-v1"
    raw["service_requirements_ref"] = "mig03-group-requirements-v1"
    raw["components"] = {
        "execution": {
            "config_schema_version": 2,
            "settings": {
                "instance_id": group._COORDINATOR_INSTANCE,
                "bind_ports": ["service.order_group_entry"],
                "options": {
                    "role": "group_coordinator",
                    "portfolio_ref": group._PORTFOLIO,
                },
            },
        }
    }
    return raw


def _group_authority(config: Any, keys: dict[str, bytes]) -> TrustedServiceAuthority:
    scope = group._strategy_scope(run_id=None)
    peer_identity = WireIdentity(
        WireRole.STRATEGY_CLIENT,
        group._STRATEGY_PRINCIPAL,
        actor_ref=group._STRATEGY,
    )
    resource = ResourceGrant(
        group._COORDINATOR_INSTANCE,
        "service.order_group_entry",
        group._COORDINATOR_SCOPE_REF,
        scope,
        group.GROUP_COORDINATOR_OPERATIONS,
    )
    return TrustedServiceAuthority(
        identity=WireIdentity(
            WireRole.SERVICE,
            group._COORDINATOR_PRINCIPAL,
            actor_ref="mig03-group-coordinator-service",
        ),
        scope=scope,
        scope_ref=config.scope_ref,
        scope_version="mig03-group-scope-v1",
        security_ref=drill._SERVER_SECURITY_REF,
        security_version="curve-key-v1",
        registration_policy_ref=config.registration_policy_ref,
        registration_policy_version="mig03-group-policy-v1",
        service_requirements_ref=config.service_requirements_ref,
        service_requirements_version="mig03-group-requirements-v1",
        curve_public_key=keys["server_public"],
        curve_secret_key=keys["server_secret"],
        principals={keys["strategy_public"]: group._STRATEGY_PRINCIPAL},
        grants={
            group._STRATEGY_PRINCIPAL: (
                PrincipalGrant(peer_identity, (resource,)),
            )
        },
    )


def _afi_scheduler_consumer(
    *, configuration_sha256: str, task_name: str
) -> ConfiguredConsumer:
    """Bind the AFI task row to its actual scheduler config and live task name."""

    scope_sha256 = _sha256_json(
        {
            "user_id": "mig03-user",
            "paper_account_id": r235._AFI_ACCOUNT_ID,
            "instance_id": r235._AFI_INSTANCE_ID,
            "task_name": task_name,
        }
    )
    operations_sha256 = _sha256_json(
        {"write_model": "PaperEquitySnapshot", "operation": "scheduled_snapshot"}
    )
    consumer_id = "afi-snapshot-" + _sha256_json(
        {"configuration": configuration_sha256, "task": task_name}
    )[:24]
    return ConfiguredConsumer(
        consumer_id=consumer_id,
        consumer_kind="afi_snapshot_scheduler",
        configuration_sha256=configuration_sha256,
        scope_sha256=scope_sha256,
        operations_sha256=operations_sha256,
    )


def _group_component_process(control, store_root: str, mode: str, service_epoch: str) -> None:
    """Run the production coordinator component and close its owned stores."""

    now_ns = [_GROUP_NOW_NS]
    stable_scope, target, fingerprints = group._target(now_ns[0])
    runtime = group._LoopbackRuntime(fingerprints)
    service = store = bindings = None
    close_facts: dict[str, Any] = {}
    try:
        root = Path(store_root)
        root.mkdir(parents=True, exist_ok=True)
        service, store, bindings = group._make_service(
            root, runtime, stable_scope, target, now_ns=now_ns
        )
        registration = group._registration(
            now_ns[0],
            registration_id=f"mig03-group-{mode}-registration",
            service_epoch=service_epoch,
        )
        service.maintain_registrations((registration,), recover=False)
        if mode == "seed":
            reply = service.handle(
                "groups.submit",
                context=registration.context,
                arguments=group._arguments(
                    now_ns[0],
                    next(iter(target.accounts.values())).instruments["BTC-USDT"].metadata,
                ),
            )
        elif mode == "readback":
            reply = service.handle(
                "groups.query",
                context=registration.context,
                arguments={"group_id": group._GROUP_ID},
            )
        else:
            raise AssertionError("unknown group migration phase")
        assert reply["ok"] is True, reply
        snapshot = GroupOrderSnapshot.from_payload(reply["result"]["snapshot"])
        binding = bindings.get(group._PORTFOLIO, group._GROUP_ID)
        assert binding is not None
        events = store.events(group._PORTFOLIO, group._GROUP_ID)
        facts = {
            "snapshot": snapshot.to_payload(),
            "origin_run_id": binding.origin_run_id,
            "request_fingerprint": binding.request_fingerprint,
            "binding_request_fingerprint": binding.request.unquoted_fingerprint,
            "events": [
                {
                    "sequence": item.sequence,
                    "event_type": item.event_type,
                    "created_at_ns": item.created_at_ns,
                    "payload_sha256": _sha256_json(dict(item.payload)),
                }
                for item in events
            ],
            "submitted_intents": list(runtime.submitted),
            "reservation_count": len(runtime.reservations),
            "service_epoch": registration.service_epoch,
        }
        control.send(("ready", facts))
        if control.recv() != "close":
            raise AssertionError("group coordinator did not receive normal close")
        service.close()
        service = None
        store.close()
        store = None
        bindings.close()
        bindings = None
        close_facts = {"service_closed": True, "stores_closed": True}
        control.send(("closed", close_facts))
    except BaseException as error:
        try:
            control.send(("error", type(error).__name__, getattr(error, "code", None)))
        except BaseException:
            pass
        raise
    finally:
        for resource in (service, store, bindings):
            close = getattr(resource, "close", None)
            if callable(close):
                close()
        control.close()


def _source_strategy_client_process(
    control,
    endpoint: str,
    keys: dict[str, bytes],
    projection_path: str,
    completed_arguments: dict[str, Any],
) -> None:
    """Own the old strategy registration, cursor, and its normal close."""

    runtime = consumer = None
    try:
        runtime = drill._new_runtime(
            endpoint, keys, session_id="mig03-close-source-strategy-session"
        )
        started = runtime.start()
        assert started.degraded_count == 0
        completed = drill._execution_client(runtime).submit(**completed_arguments)
        unknown = drill._execution_client(runtime).submit(
            **drill._submit_arguments(
                drill._NEW_INTENT_ID,
                "mig03-close-source-unknown-signal",
                quantity="2",
                price="50000",
            )
        )
        consumer = migration._target_projection(
            runtime, Path(projection_path), account_ref=drill._ACCOUNT
        )
        checkpoint = consumer.poll()
        progress = runtime.call("store.account", "account.progress", {})[
            "consumer_progress"
        ]
        facts = {
            "completed_state": completed.state,
            "completed_filled_quantity": str(completed.filled_quantity),
            "completed_commission": str(completed.cumulative_commission),
            "unknown_state": unknown.state,
            "unknown_attempts": unknown.dispatch_attempts,
            "unknown_permit_reference": unknown.permit_reference,
            "checkpoint": {
                "stream_epoch": checkpoint.stream_epoch,
                "cursor": checkpoint.cursor,
                "pending_ack": checkpoint.pending_ack,
                "projection": r235._consumer_projection_json_value(
                    checkpoint.projection
                ),
            },
            "progress": dict(progress),
        }
        control.send(("ready", facts))
        if control.recv() != "close":
            raise AssertionError("strategy client did not receive normal close")
        consumer.close()
        consumer = None
        runtime.close()
        runtime = None
        control.send(
            (
                "closed",
                {
                    "runtime_state": "closed",
                    "cleanup_failures": 0,
                    "consumer_closed": True,
                },
            )
        )
    except BaseException as error:
        try:
            control.send(("error", type(error).__name__, getattr(error, "code", None)))
        except BaseException:
            pass
        raise
    finally:
        if consumer is not None:
            consumer.close()
        if runtime is not None:
            runtime.close()
        control.close()


def _afi_snapshot_scheduler_process(control) -> None:
    """Start and normally stop the configured AFI snapshot task in its owner."""

    async def run() -> None:
        scheduler = r235.PaperRuntimeSnapshotScheduler()
        started = asyncio.Event()
        hold = asyncio.Event()

        async def blocked_snapshot(_user_id: str, _instance_id: str) -> None:
            started.set()
            await hold.wait()

        scheduler._run = blocked_snapshot  # type: ignore[method-assign]
        scheduler.ensure_running("mig03-user", r235._AFI_INSTANCE_ID)
        stopped_normally = False
        try:
            await asyncio.wait_for(started.wait(), timeout=5.0)
            task_name = f"paper-runtime-snapshot:{r235._AFI_INSTANCE_ID}"
            control.send(("ready", {"task_name": task_name}))
            while not control.poll(0):
                await asyncio.sleep(0.01)
            if control.recv() != "close":
                raise AssertionError("AFI snapshot scheduler did not receive stop")
            stopped = await r235.stop_paper_snapshot_writers(scheduler)
            stopped_normally = True
            facts = {
                "tasks_before": stopped.tasks_before,
                "stopped_task_names": tuple(stopped.stopped_task_names),
                "tasks_after": stopped.tasks_after,
            }
            control.send(("closed", facts))
        finally:
            # The normal stop helper has already cancelled and joined the task.
            if not stopped_normally:
                with suppress(BaseException):
                    await r235.stop_paper_snapshot_writers(scheduler)

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


def _spawn_owned(context, target, args: tuple[Any, ...], *, name: str) -> dict[str, Any]:
    parent, child = context.Pipe(duplex=True)
    process = context.Process(name=name, target=target, args=(child, *args))
    try:
        process.start()
    except BaseException:
        parent.close()
        child.close()
        raise
    child.close()
    return {"process": process, "pipe": parent, "pid": process.pid, "name": name}


def _receive_owned(record: dict[str, Any], *, timeout: float = 30.0) -> tuple[Any, ...]:
    pipe = record["pipe"]
    assert pipe.poll(timeout), f"{record['name']} did not report within its bound"
    message = pipe.recv()
    if message and message[0] == "error":
        raise AssertionError((record["name"], message[1], message[2]))
    return message


def _close_owned_normally(record: dict[str, Any]) -> tuple[dict[str, Any], int]:
    process = record["process"]
    assert process.is_alive(), f"{record['name']} exited before normal close"
    record["pipe"].send("close")
    reply = _receive_owned(record)
    assert reply[0] == "closed", reply
    process.join(20)
    assert not process.is_alive(), f"{record['name']} remained alive after close"
    assert process.exitcode == 0, (record["name"], process.exitcode)
    pid = record["pid"]
    record["pipe"].close()
    process.close()
    record["closed"] = True
    return reply[1], pid


def _cleanup_owned(record: dict[str, Any] | None) -> None:
    if record is None or record.get("closed"):
        return
    process = record["process"]
    pipe = record["pipe"]
    if process.is_alive():
        try:
            pipe.send("close")
            if pipe.poll(10):
                pipe.recv()
        except BaseException:
            pass
        process.join(10)
    if process.is_alive():
        # This is an owned test process only; it is never a retained host.
        process.terminate()
        process.join(5)
    pipe.close()
    if not process.is_alive():
        process.close()
    record["closed"] = True


def _group_snapshot_from_facts(facts: dict[str, Any]) -> GroupOrderSnapshot:
    return GroupOrderSnapshot.from_payload(facts["snapshot"])


def _group_projection(snapshot: GroupOrderSnapshot) -> dict[str, Any]:
    return enable._group_business_projection(snapshot)


def _build_group_inventory(
    endpoint: str, keys: dict[str, bytes]
) -> tuple[Any, TrustedServiceAuthority, tuple[ConfiguredConsumer, ...], str]:
    raw = _group_service_config(endpoint)
    config = drill.parse_service_config(raw)
    authority = _group_authority(config, keys)
    inventory = derive_service_consumer_inventory(config, authority.grants, authority=authority)
    now_ns = [_GROUP_NOW_NS]
    scope, target, _ = group._target(now_ns[0])
    configuration_sha256 = enable._group_configuration_sha256(scope, target)
    return config, authority, inventory, configuration_sha256


def _all_inventory(*parts: tuple[ConfiguredConsumer, ...]) -> tuple[ConfiguredConsumer, ...]:
    result = tuple(sorted((item for part in parts for item in part), key=lambda item: item.consumer_id))
    assert result and len({item.consumer_id for item in result}) == len(result)
    return result


def test_mig03_spawned_consumers_group_and_cursor_cross_cutover(tmp_path: Path) -> None:
    """Close real source owners, then read/re-register every retained projection."""

    source_origins = r235._assert_all_fixed_origins()
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
    service_config = drill.parse_service_config(raw_config)
    service_authority = drill._service_authority(
        service_config,
        keys,
        execution_instance=drill._EXEC_INSTANCE,
        gateway_instance=drill._GW_INSTANCE,
    )
    source_sdk_inventory = derive_service_consumer_inventory(
        service_config, service_authority.grants, authority=service_authority
    )
    _source_group_config, _source_group_authority, source_group_inventory, group_config_sha256 = (
        _build_group_inventory(endpoint, keys)
    )

    registry_path = tmp_path / "registry.sqlite3"
    execution_path = tmp_path / "execution.sqlite3"
    risk_path = tmp_path / "risk.sqlite3"
    provider_path = tmp_path / "provider.sqlite3"
    gateway_path = tmp_path / "gateway.sqlite3"
    map_path = tmp_path / "identity-map.sqlite3"
    projection_path = tmp_path / "account-projection.sqlite3"
    afi_path = tmp_path / "afi-paper.sqlite3"
    group_root = tmp_path / "group-component"

    migration._seed_gateway_events(gateway_path)
    r235._seed_afi_database(afi_path)
    completed_arguments = drill._submit_arguments(
        drill._INTENT_ID,
        "mig03-process-cutover-historical-fill",
        quantity="2",
        price="5000",
    )
    migration._seed_preexisting_completed_fill(execution_path, completed_arguments)

    group_source = _spawn_owned(
        context,
        _group_component_process,
        (str(group_root), "seed", migration._OLD_DEPLOYMENT_EPOCH),
        name="mig03-source-group-component",
    )
    try:
        group_seed_message = _receive_owned(group_source)
        assert group_seed_message[0] == "ready"
        group_source_facts = group_seed_message[1]
        source_group_snapshot = _group_snapshot_from_facts(group_source_facts)
        assert source_group_snapshot.state is GroupOrderState.RECOVERY_REQUIRED
        assert source_group_snapshot.request.deadline_ns > _GROUP_NOW_NS
        assert len(source_group_snapshot.legs) == 2
        assert len(source_group_snapshot.reservations) == 2
        assert source_group_snapshot.budget_reservation is not None
        assert source_group_snapshot.budget_reservation.state is GroupBudgetReservationState.HELD
        assert group_source_facts["origin_run_id"] == group._RUN
        assert group_source_facts["request_fingerprint"] == group_source_facts[
            "binding_request_fingerprint"
        ]
        assert len(group_source_facts["events"]) >= 2
        group_source_close_facts, group_source_pid = _close_owned_normally(group_source)
        assert group_source_close_facts == {"service_closed": True, "stores_closed": True}

        group_before = capture_group_participant(
            group_root / "groups.sqlite3",
            group_root / "group-runs.sqlite3",
            configuration_sha256=group_config_sha256,
        )
        assert group_before.evidence.row_count > 0
        assert set(group_before.table_names) >= {
            "group_store:order_group_snapshots",
            "group_store:order_group_events",
            "coordinator_run_bindings:coordinator_group_run_bindings",
            "coordinator_sidecar:coordinator_group_cancel_child_claims",
            "coordinator_sidecar:coordinator_group_cancel_plan_legs",
            "coordinator_sidecar:coordinator_group_cancel_plans",
            "coordinator_sidecar:coordinator_group_roll_exit_actions",
        }
        unsupported_sidecar = tmp_path / "group-runs-with-unmigratable-cancel.sqlite3"
        source_connection = sqlite3.connect(group_root / "group-runs.sqlite3")
        target_connection = sqlite3.connect(unsupported_sidecar)
        try:
            source_connection.backup(target_connection)
        finally:
            target_connection.close()
            source_connection.close()
        with sqlite3.connect(unsupported_sidecar) as unsupported_connection:
            unsupported_connection.execute(
                "INSERT INTO coordinator_group_cancel_child_claims VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "mig03-pending-cancel",
                    group._PORTFOLIO,
                    group._GROUP_ID,
                    group._RUN,
                    "leg-a",
                    "account-a",
                    "intent-a",
                    "provider-order-a",
                    "cancel-request-a",
                    "UNKNOWN",
                    _GROUP_NOW_NS,
                ),
            )
        unsupported_connection.close()
        with pytest.raises(MigrationBoundaryError) as unsupported_cancel_state:
            capture_group_participant(
                group_root / "groups.sqlite3",
                unsupported_sidecar,
                configuration_sha256=group_config_sha256,
            )
        assert unsupported_cancel_state.value.code == "MIG03_BOUNDARY_INCOMPATIBLE"
        assert read_migration_marker(registry_path) is None
    except BaseException:
        _cleanup_owned(group_source)
        raise

    afi_config_sha256 = r235._afi_config_sha256(afi_path)
    scheduler = _spawn_owned(
        context,
        _afi_snapshot_scheduler_process,
        (),
        name="mig03-source-afi-snapshot-scheduler",
    )
    try:
        scheduler_start = _receive_owned(scheduler)
        assert scheduler_start[0] == "ready"
        scheduler_task_name = scheduler_start[1]["task_name"]
    except BaseException:
        _cleanup_owned(scheduler)
        raise
    assert scheduler_task_name == f"paper-runtime-snapshot:{r235._AFI_INSTANCE_ID}"
    source_afi_consumer = _afi_scheduler_consumer(
        configuration_sha256=afi_config_sha256,
        task_name=scheduler_task_name,
    )

    hosts: list[dict[str, Any]] = []
    owned: list[dict[str, Any]] = [group_source, scheduler]
    client = None
    refusal = None
    runtime = None
    destination_group = None
    destination_scheduler = None
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
            label="mig03-process-cutover-old-host",
        )
        hosts.append(old_host)
        ready_old = drill._receive_host_message(old_host)
        assert ready_old[0] == "ready" and ready_old[1] == endpoint
        assert ready_old[3] == source_origins
        assert ready_old[4] == migration._OLD_DEPLOYMENT_EPOCH
        old_host_pid = old_host["pid"]

        client = _spawn_owned(
            context,
            _source_strategy_client_process,
            (
                endpoint,
                keys,
                str(projection_path),
                completed_arguments,
            ),
            name="mig03-source-strategy-client",
        )
        client_message = _receive_owned(client)
        assert client_message[0] == "ready"
        client_facts = client_message[1]
        assert client_facts["completed_state"] == "FILLED"
        assert client_facts["completed_filled_quantity"] == "2"
        assert client_facts["completed_commission"] == "0.03"
        assert client_facts["unknown_state"] == "UNKNOWN"
        assert client_facts["unknown_attempts"] == 1
        checkpoint_facts = client_facts["checkpoint"]
        assert checkpoint_facts["stream_epoch"]
        assert checkpoint_facts["cursor"] == 1
        assert client_facts["progress"]["stream_epoch"] == checkpoint_facts["stream_epoch"]

        old_host_facts = drill._host_command(old_host, "facts")[1]
        assert old_host_facts["service_epoch"] == ready_old[2]
        assert old_host_facts["provider_dispatch_counts"] == {"intent_1": 0, "intent_2": 1}
        freeze = f"dispatch-inflight:{drill._NEW_INTENT_ID}"
        assert old_host_facts["risk_rows_intent_2"]["freezes"] == ((freeze, freeze, 1),)
        assert freeze in old_host_facts["risk_snapshot"]["active_freeze_reasons"]

        # Every source process gets a normal close handshake before the
        # read-only participant snapshot and before arm.
        client_close_facts, client_pid = _close_owned_normally(client)
        assert client_close_facts == {
            "runtime_state": "closed",
            "cleanup_failures": 0,
            "consumer_closed": True,
        }
        client["close_facts"] = client_close_facts
        scheduler_close_facts, scheduler_pid = _close_owned_normally(scheduler)
        assert scheduler_close_facts["tasks_before"] == 1
        assert scheduler_close_facts["tasks_after"] == 0
        assert scheduler_close_facts["stopped_task_names"] == (scheduler_task_name,)
        scheduler["close_facts"] = scheduler_close_facts
        closed = drill._close_host_normally(old_host)
        assert closed[1] == "closed" and old_host["normal_exit_code"] == 0

        # The source cursor is intentionally expired by a durable snapshot and
        # same-epoch tail.  A changed stream epoch remains a refusal; this
        # node checks the same-epoch re-registration/read path only.
        journal = migration.GatewayEventJournal(
            gateway_path,
            clock=lambda: drill._JOURNAL_CLOCK_SECONDS,
        )
        compacted = journal.capture_snapshot(
            drill._JOURNAL_ACCOUNT_SCOPE,
            drill._JOURNAL_STRATEGY_SCOPE,
            "mig03-process-cutover-snapshot-through-two",
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
        assert compacted.cursor == 2
        appended = journal.append(
            drill._JOURNAL_ACCOUNT_SCOPE,
            drill._JOURNAL_STRATEGY_SCOPE,
            "mig03-process-cutover-event-3",
            "position_update",
            {"sequence": 3, "quantity": "5"},
            observed_at=drill._JOURNAL_CLOCK_SECONDS,
        )
        assert appended.sequence == 3

        afi_before = r235._capture_afi(afi_path, afi_config_sha256)
        sdk_config_sha256 = _sha256_json(raw_config)
        registry_before = capture_sqlite_participant(
            "registry", registry_path, configuration_sha256=sdk_config_sha256
        )
        execution_before = capture_sqlite_participant(
            "execution", execution_path, configuration_sha256=sdk_config_sha256
        )
        risk_before = capture_sqlite_participant(
            "risk", risk_path, configuration_sha256=sdk_config_sha256
        )
        gateway_before = capture_sqlite_participant(
            "gateway", gateway_path, configuration_sha256=sdk_config_sha256
        )
        execution_facts_before = r235._execution_handoff_facts(execution_path)
        r235._assert_execution_source_lease(execution_facts_before)

        checkpoint = SimpleNamespace(
            stream_epoch=checkpoint_facts["stream_epoch"],
            cursor=checkpoint_facts["cursor"],
            pending_ack=checkpoint_facts["pending_ack"],
            projection=checkpoint_facts["projection"],
        )
        consumer_config_sha256 = _sha256_json(
            {
                "database_path": str(projection_path.resolve()),
                "consumer_id": "old-account-projection",
                "gateway_scope_ref": drill._GATEWAY_SCOPE_REF,
                "gateway_principal": drill._STRATEGY_PRINCIPAL,
            }
        )
        consumer_checkpoint_sha256 = r235._consumer_checkpoint_sha256(
            checkpoint, projection_path
        )
        sdk_destination_config = drill.parse_service_config(
            raw_config
        )
        sdk_destination_authority = drill._service_authority(
            sdk_destination_config,
            keys,
            execution_instance=drill._EXEC_INSTANCE,
            gateway_instance=drill._GW_INSTANCE,
        )
        destination_sdk_inventory = derive_service_consumer_inventory(
            sdk_destination_config,
            sdk_destination_authority.grants,
            authority=sdk_destination_authority,
        )
        destination_group_config = drill.parse_service_config(
            _group_service_config(endpoint)
        )
        destination_group_authority = _group_authority(destination_group_config, keys)
        destination_group_inventory = derive_service_consumer_inventory(
            destination_group_config,
            destination_group_authority.grants,
            authority=destination_group_authority,
        )
        destination_afi_consumer = _afi_scheduler_consumer(
            configuration_sha256=afi_config_sha256,
            task_name=scheduler_task_name,
        )
        source_inventory = _all_inventory(
            source_sdk_inventory,
            source_group_inventory,
            (source_afi_consumer,),
        )
        destination_inventory = _all_inventory(
            destination_sdk_inventory,
            destination_group_inventory,
            (destination_afi_consumer,),
        )
        require_consumer_inventory_compatible(source_inventory, destination_inventory)

        # The consumer-to-owner mapping is derived from the source inventory;
        # each PID below came from a spawned Process and each close ack was
        # received before its successful join/exit-code check.
        receipts: list[ConsumerStopEvidence] = []
        sdk_ids = {item.consumer_id for item in source_sdk_inventory}
        group_ids = {item.consumer_id for item in source_group_inventory}
        afi_ids = {source_afi_consumer.consumer_id}
        client_checkpoint_hash = _sha256_json(
            {
                "process_id": client_pid,
                "close": client_close_facts,
                "consumer_checkpoint_sha256": consumer_checkpoint_sha256,
            }
        )
        host_checkpoint_hash = _sha256_json(
            {
                "process_id": old_host_pid,
                "service_epoch": ready_old[2],
                "normal_close": closed[1],
                "exit_code": old_host["normal_exit_code"],
            }
        )
        group_checkpoint_hash = _sha256_json(
            {
                "process_id": group_source_pid,
                "close": group_source_close_facts,
                "origin_run_id": group_source_facts["origin_run_id"],
                "request_fingerprint": group_source_facts["request_fingerprint"],
            }
        )
        afi_checkpoint_hash = _sha256_json(
            {
                "process_id": scheduler_pid,
                "close": scheduler_close_facts,
            }
        )
        for item in source_inventory:
            if item.consumer_id in sdk_ids:
                if item.consumer_kind == "trusted_inbound_grant":
                    pid, checkpoint_hash = client_pid, client_checkpoint_hash
                elif item.consumer_kind in {
                    "configured_component",
                    "trusted_service_authority",
                }:
                    pid, checkpoint_hash = old_host_pid, host_checkpoint_hash
                else:
                    raise AssertionError("unowned SDK consumer kind")
            elif item.consumer_id in group_ids:
                pid, checkpoint_hash = group_source_pid, group_checkpoint_hash
            elif item.consumer_id in afi_ids:
                pid, checkpoint_hash = scheduler_pid, afi_checkpoint_hash
            else:
                raise AssertionError("source consumer has no observed process owner")
            receipts.append(
                ConsumerStopEvidence(
                    consumer_id=item.consumer_id,
                    process_id=pid,
                    close_acknowledged=True,
                    exit_code=0,
                    checkpoint_sha256=checkpoint_hash,
                )
            )
        stopped_consumers = tuple(sorted(receipts, key=lambda item: item.consumer_id))
        source_pids = {old_host_pid, client_pid, group_source_pid, scheduler_pid}
        assert len(source_pids) == 4
        assert os.getpid() not in {item.process_id for item in stopped_consumers}
        assert {item.process_id for item in stopped_consumers} == source_pids
        assert {item.consumer_id for item in stopped_consumers} == {
            item.consumer_id for item in source_inventory
        }

        consumer_configuration_sha256 = configured_consumer_inventory_sha256(
            source_inventory
        )
        stopped_consumer_digest = consumer_inventory_sha256(stopped_consumers)
        consumer_state_sha256 = _sha256_json(
            {
                "account_checkpoint_sha256": consumer_checkpoint_sha256,
                "account_stream_epoch": checkpoint.stream_epoch,
                "account_cursor": checkpoint.cursor,
                "afi_scheduler_stop": scheduler_close_facts,
                "group_source_fingerprint": group_source_facts["request_fingerprint"],
            }
        )
        consumers_before = retained_external_participant(
            "consumers",
            source_ref=str(projection_path.resolve()),
            configuration_sha256=consumer_configuration_sha256,
            state_sha256=consumer_state_sha256,
            identity_sha256=stopped_consumer_digest,
            schema_sha256=_sha256_json("mig03-configured-consumer-cutover-v1"),
            row_count=len(stopped_consumers),
        )
        afi_participants_before = tuple(
            retained_external_participant(**item)
            for item in afi_before.boundary_participants()
        )
        group_participant_before = group_before.evidence
        participants_before = (
            registry_before.evidence,
            execution_before.evidence,
            risk_before.evidence,
            group_participant_before,
            gateway_before.evidence,
            consumers_before,
            *afi_participants_before,
        )

        state_path_marker = read_migration_marker(registry_path)
        assert state_path_marker is None
        with pytest.raises(MigrationBoundaryError) as missing_close:
            prepare_migration_boundary(
                migration_id=_MIGRATION_ID,
                source_epoch=migration._OLD_DEPLOYMENT_EPOCH,
                destination_epoch=migration._NEW_DEPLOYMENT_EPOCH,
                old_writer_pid=old_host_pid,
                old_writer_exit_code=0,
                consumers=stopped_consumers[:-1],
                participants=participants_before,
                fence_reference="spawned-local-owner-close-required",
                source_configured_consumers=source_inventory,
                destination_configured_consumers=destination_inventory,
            )
        assert missing_close.value.code == CODE_BOUNDARY_NOT_QUIESCENT
        assert read_migration_marker(registry_path) is None

        plan = prepare_migration_boundary(
            migration_id=_MIGRATION_ID,
            source_epoch=migration._OLD_DEPLOYMENT_EPOCH,
            destination_epoch=migration._NEW_DEPLOYMENT_EPOCH,
            old_writer_pid=old_host_pid,
            old_writer_exit_code=old_host["normal_exit_code"],
            consumers=stopped_consumers,
            participants=participants_before,
            fence_reference="spawned-local-host-client-group-and-scheduler-normal-close",
            source_configured_consumers=source_inventory,
            destination_configured_consumers=destination_inventory,
        )

        with MigrationIdentityStore(map_path) as map_store:
            manifest = migration._build_import_manifest(
                execution_path,
                endpoint=endpoint,
                provider_order_id=migration._SOURCE_PROVIDER_ORDER_ID,
            )
            applied = apply_migration_import(manifest, execution_path, map_store)
            assert len(applied.mappings) == 5
            target_registration = migration._readback_target_registration(
                map_store, manifest
            )
            assert target_registration.order_ref == "mig03-integrated-target-order-ref"
            assert target_registration.booked_instrument == "BTC-USDT"
            assert map_store.resolve(
                migration.SOURCE_BARE_ORDER_ID,
                migration._SOURCE_PROVIDER_ORDER_ID,
            ).mapping.target_value == target_registration.order_ref

        group_after_import = capture_group_participant(
            group_root / "groups.sqlite3",
            group_root / "group-runs.sqlite3",
            configuration_sha256=group_config_sha256,
            expected_schema_sha256=group_before.schema_sha256,
        )
        assert group_after_import == group_before
        armed = arm_migration_boundary(
            registry_path,
            plan,
            source_configured_consumers=source_inventory,
            destination_configured_consumers=destination_inventory,
        )
        assert armed.status == STATUS_COMMITTED and not armed.sealed

        target_raw = manifest["target"]["service_config"]["raw"]
        target_config = drill.parse_service_config(target_raw)
        target_authority = drill._service_authority(
            target_config,
            keys,
            execution_instance=drill._EXEC_INSTANCE,
            gateway_instance=drill._GW_INSTANCE,
        )
        target_sdk_inventory = derive_service_consumer_inventory(
            target_config, target_authority.grants, authority=target_authority
        )
        target_all_inventory = _all_inventory(
            target_sdk_inventory,
            destination_group_inventory,
            (destination_afi_consumer,),
        )
        require_consumer_inventory_compatible(source_inventory, target_all_inventory)

        new_host = drill._start_host_process(
            context,
            migration._migration_host_process,
            (
                target_raw,
                keys,
                migration._NEW_DEPLOYMENT_EPOCH,
                str(registry_path),
                str(execution_path),
                str(risk_path),
                str(provider_path),
                str(gateway_path),
            ),
            label="mig03-process-cutover-new-host",
        )
        hosts.append(new_host)
        ready_new = drill._receive_host_message(new_host)
        assert ready_new[0] == "ready" and ready_new[4] == migration._NEW_DEPLOYMENT_EPOCH
        assert ready_new[3] == source_origins

        runtime = migration._new_mapped_runtime(
            endpoint,
            keys,
            target_registration,
            session_id="mig03-process-cutover-destination-client",
        )
        assert runtime.start().degraded_count == 0
        assert runtime.bindings
        assert all(binding.service_epoch == ready_new[2] for binding in runtime.bindings)
        filled_readback = drill._execution_client(runtime).query(drill._INTENT_ID)
        unknown_readback = drill._execution_client(runtime).query(drill._NEW_INTENT_ID)
        assert filled_readback is not None and filled_readback.state == "FILLED"
        assert filled_readback.provider_order_id == migration._SOURCE_PROVIDER_ORDER_ID
        assert type(filled_readback.filled_quantity) is Decimal
        assert filled_readback.filled_quantity == Decimal("2")
        assert type(filled_readback.cumulative_commission) is Decimal
        assert filled_readback.cumulative_commission == Decimal("0.03")
        assert unknown_readback is not None and unknown_readback.state == "UNKNOWN"
        assert unknown_readback.dispatch_attempts == 1
        assert drill._risk_rows(str(risk_path), drill._NEW_INTENT_ID) == old_host_facts[
            "risk_rows_intent_2"
        ]
        cursor_progress = runtime.call("store.account", "account.progress", {})[
            "consumer_progress"
        ]
        assert cursor_progress == {
            "stream_epoch": checkpoint.stream_epoch,
            "offered_cursor": checkpoint.cursor,
            "acknowledged_cursor": checkpoint.cursor,
        }
        with pytest.raises(drill.ServiceExecutionError) as disabled_write:
            drill._execution_client(runtime).submit(
                **drill._submit_arguments(
                    "mig03-process-cutover-disabled-before-enable",
                    "mig03-process-cutover-disabled-signal",
                    quantity="1",
                    price="50000",
                )
            )
        assert disabled_write.value.code == "MIGRATION_WRITE_DISABLED"
        assert not drill._execution_rows(
            execution_path, "mig03-process-cutover-disabled-before-enable"
        )
        assert drill._provider_count(str(provider_path), drill._NEW_INTENT_ID) == 1
        assert drill._provider_count(
            str(provider_path), "mig03-process-cutover-disabled-before-enable"
        ) == 0
        destination_unknown_risk = drill._risk_rows(
            str(risk_path), drill._NEW_INTENT_ID
        )
        assert destination_unknown_risk == old_host_facts["risk_rows_intent_2"]
        assert destination_unknown_risk["freezes"] == ((freeze, freeze, 1),)
        assert freeze in old_host_facts["risk_snapshot"]["active_freeze_reasons"]

        destination_group = _spawn_owned(
            context,
            _group_component_process,
            (str(group_root), "readback", migration._NEW_DEPLOYMENT_EPOCH),
            name="mig03-destination-group-component",
        )
        destination_group_message = _receive_owned(destination_group)
        assert destination_group_message[0] == "ready"
        destination_group_facts = destination_group_message[1]
        destination_snapshot = _group_snapshot_from_facts(destination_group_facts)
        assert destination_group_facts["service_epoch"] == migration._NEW_DEPLOYMENT_EPOCH
        assert _group_projection(destination_snapshot) == _group_projection(
            source_group_snapshot
        )
        assert destination_group_facts["origin_run_id"] == group_source_facts[
            "origin_run_id"
        ]
        assert destination_group_facts["request_fingerprint"] == group_source_facts[
            "request_fingerprint"
        ]
        assert destination_group_facts["events"] == group_source_facts["events"]
        destination_group_close, _destination_group_pid = _close_owned_normally(
            destination_group
        )
        assert destination_group_close == {
            "service_closed": True,
            "stores_closed": True,
        }

        group_after_readback = capture_group_participant(
            group_root / "groups.sqlite3",
            group_root / "group-runs.sqlite3",
            configuration_sha256=group_config_sha256,
            expected_schema_sha256=group_before.schema_sha256,
        )
        assert group_after_readback == group_before
        afi_after_readback = r235._capture_afi(afi_path, afi_config_sha256)
        assert afi_after_readback == afi_before

        registry_after = capture_sqlite_participant(
            "registry",
            registry_path,
            configuration_sha256=sdk_config_sha256,
            expected_schema_sha256=registry_before.schema_sha256,
        )
        execution_after = capture_sqlite_participant(
            "execution",
            execution_path,
            configuration_sha256=sdk_config_sha256,
            expected_schema_sha256=execution_before.schema_sha256,
        )
        risk_after = capture_sqlite_participant(
            "risk",
            risk_path,
            configuration_sha256=sdk_config_sha256,
            expected_schema_sha256=risk_before.schema_sha256,
        )
        gateway_after = capture_sqlite_participant(
            "gateway",
            gateway_path,
            configuration_sha256=sdk_config_sha256,
            expected_schema_sha256=gateway_before.schema_sha256,
        )
        execution_facts_after = r235._execution_handoff_facts(execution_path)
        r235._assert_execution_participant_handoff(
            execution_before,
            execution_after,
            execution_facts_before,
            execution_facts_after,
        )
        afi_participants_after = tuple(
            retained_external_participant(**item)
            for item in afi_after_readback.boundary_participants()
        )
        consumer_after = retained_external_participant(
            "consumers",
            source_ref=str(projection_path.resolve()),
            configuration_sha256=consumer_configuration_sha256,
            state_sha256=consumer_state_sha256,
            identity_sha256=stopped_consumer_digest,
            schema_sha256=_sha256_json("mig03-configured-consumer-cutover-v1"),
            row_count=len(stopped_consumers),
        )
        participants_after = (
            registry_after.evidence,
            execution_after.evidence,
            risk_after.evidence,
            group_after_readback.evidence,
            gateway_after.evidence,
            consumer_after,
            *afi_participants_after,
        )
        participants_after_execution_source = tuple(
            execution_before.evidence
            if item.participant_id == "execution"
            else item
            for item in participants_after
        )
        assert_participant_states_conserved(
            participants_before, participants_after_execution_source
        )
        before_conservation = r235._sdk_state_conservation(
            execution_path,
            risk_path,
            gateway_path,
            checkpoint,
            target_registration,
        )
        after_conservation = r235._sdk_state_conservation(
            execution_path,
            risk_path,
            gateway_path,
            checkpoint,
            target_registration,
        )
        assert_state_conserved(before_conservation, after_conservation)
        r235.assert_afi_state_conserved(afi_before, afi_after_readback)
        assert afi_before.account_ref == target_registration.account_ref
        # The digest is an operator audit reference, not a proof token.  The
        # evidence above is verified before the production Host enable call.
        readiness_sha256 = _sha256_json(
            {
                "migration_id": _MIGRATION_ID,
                "configured_inventory_sha256": configured_consumer_inventory_sha256(
                    target_all_inventory
                ),
                "completed_order": {
                    "intent_id": filled_readback.intent_id,
                    "state": filled_readback.state,
                    "provider_order_id": filled_readback.provider_order_id,
                    "quantity": str(filled_readback.filled_quantity),
                    "commission": str(filled_readback.cumulative_commission),
                },
                "unknown_order": {
                    "intent_id": unknown_readback.intent_id,
                    "state": unknown_readback.state,
                    "dispatch_attempts": unknown_readback.dispatch_attempts,
                    "permit_reference": unknown_readback.permit_reference,
                },
                "cursor": dict(cursor_progress),
                "group": _group_projection(destination_snapshot),
                "group_origin_run_id": destination_group_facts["origin_run_id"],
                "group_request_fingerprint": destination_group_facts[
                    "request_fingerprint"
                ],
                "afi_state_sha256": _sha256_json(afi_before.boundary_participants()),
                "risk_state_sha256": _sha256_json(
                    drill._risk_rows(str(risk_path), drill._NEW_INTENT_ID)
                ),
            }
        )
        enabled_reply = drill._host_command(
            new_host, "enable", armed.migration_id, readiness_sha256
        )
        assert enabled_reply[0] == "enabled" and enabled_reply[1] is True
        enabled = read_migration_marker(registry_path)
        assert enabled is not None and enabled.status == STATUS_ENABLED
        with pytest.raises(MigrationGateError) as refused_rollback:
            rollback_migration_cutover(
                registry_path,
                deployment_epoch=migration._OLD_DEPLOYMENT_EPOCH,
            )
        assert refused_rollback.value.code == REFUSAL_REVERSE_MIGRATION_REFUSED
        assert read_migration_marker(registry_path) == enabled

        destination_scheduler = _spawn_owned(
            context,
            _afi_snapshot_scheduler_process,
            (),
            name="mig03-destination-afi-snapshot-scheduler",
        )
        destination_scheduler_start = _receive_owned(destination_scheduler)
        assert destination_scheduler_start[0] == "ready"
        assert destination_scheduler_start[1]["task_name"] == scheduler_task_name
        expected_destination_tasks = 0 if _EXPECT_ACTIVE_AFI12_IMPORT else 1
        if _EXPECT_ACTIVE_AFI12_IMPORT:
            assert destination_scheduler_start[1]["start_existing_count"] == 0
            assert (
                destination_scheduler_start[1]["snapshot_rows_before_start"]
                == destination_scheduler_start[1]["snapshot_rows_after_start"]
            )
        destination_scheduler_close, _destination_scheduler_pid = _close_owned_normally(
            destination_scheduler
        )
        assert destination_scheduler_close["tasks_before"] == expected_destination_tasks
        assert destination_scheduler_close["tasks_after"] == 0
        assert destination_scheduler_close["stopped_task_names"] == (
            (scheduler_task_name,) if expected_destination_tasks else ()
        )

        # Reopen the same durable account projection under the destination
        # registration.  Its cursor predates compaction, so the real consumer
        # must rebuild from the snapshot plus same-stream tail.  The tracked
        # read/ACK is a legitimate write-attempt barrier and seals the marker.
        migrated_consumer = migration._target_projection(
            runtime,
            projection_path,
            account_ref=target_registration.account_ref,
        )
        try:
            recovered_checkpoint = migrated_consumer.poll()
        finally:
            migrated_consumer.close()
        assert recovered_checkpoint.stream_epoch == checkpoint.stream_epoch
        assert recovered_checkpoint.cursor == 3
        assert recovered_checkpoint.pending_ack is False
        assert recovered_checkpoint.projection == {
            "last_sequence": 3,
            "event_ids": (
                "mig03-integrated-event-1",
                "mig03-integrated-event-2",
                "mig03-process-cutover-event-3",
            ),
        }
        cursor_sealed = read_migration_marker(registry_path)
        assert cursor_sealed is not None and cursor_sealed.status == STATUS_SEALED
        assert cursor_sealed.first_write_fact_kind == "cursor"

        rejected = drill._execution_client(runtime).submit(
            **drill._submit_arguments(
                "mig03-process-cutover-after-enable-rejected",
                "mig03-process-cutover-after-enable-signal",
                quantity="1",
                price="50000",
            )
        )
        assert rejected.state == "REJECTED" and rejected.dispatch_attempts == 0
        assert drill._execution_client(runtime).query(
            "mig03-process-cutover-after-enable-rejected"
        ) == rejected
        sealed = read_migration_marker(registry_path)
        assert sealed is not None and sealed.status == STATUS_SEALED
        assert sealed.first_write_fact_kind == "cursor"
        assert drill._provider_count(str(provider_path), drill._NEW_INTENT_ID) == 1
        assert drill._provider_count(
            str(provider_path), "mig03-process-cutover-after-enable-rejected"
        ) == 0
        assert drill._risk_rows(str(risk_path), drill._NEW_INTENT_ID) == old_host_facts[
            "risk_rows_intent_2"
        ]
        assert capture_group_participant(
            group_root / "groups.sqlite3",
            group_root / "group-runs.sqlite3",
            configuration_sha256=group_config_sha256,
            expected_schema_sha256=group_before.schema_sha256,
        ) == group_before
        assert r235._capture_afi(afi_path, afi_config_sha256) == afi_before

        refusal = drill._start_host_process(
            context,
            migration._migration_refusal_process,
            (
                raw_config,
                keys,
                str(registry_path),
                migration._OLD_DEPLOYMENT_EPOCH,
            ),
            label="mig03-process-cutover-old-epoch-refusal",
        )
        refusal_message = drill._receive_host_message(refusal)
        assert refusal_message[0] == "refused", refusal_message
        assert refusal_message[1] == REFUSAL_REVERSE_MIGRATION_REFUSED
        assert refusal_message[4] == (), refusal_message
        refusal["process"].join(15)
        assert not refusal["process"].is_alive()
        assert refusal["process"].exitcode == 0
        refusal["normal_exit_code"] = refusal["process"].exitcode
        refusal["pipe"].close()
        refusal["process"].close()
        refusal["closed"] = True
        require_old_epoch_refused_after_new_write(
            plan,
            serving_epoch=ready_new[4],
            first_write_fact_kind=sealed.first_write_fact_kind,
            refusal_code=refusal_message[1],
            resource_provider_calls=len(refusal_message[4]),
        )
        runtime.close()
        assert runtime.cleanup_failures == 0
        runtime = None
        closed_new = drill._close_host_normally(new_host)
        assert closed_new[1] == "closed" and new_host["normal_exit_code"] == 0
    finally:
        if runtime is not None:
            runtime.close()
        if destination_group is not None:
            _cleanup_owned(destination_group)
        if destination_scheduler is not None:
            _cleanup_owned(destination_scheduler)
        if client is not None:
            _cleanup_owned(client)
        for record in reversed(owned):
            _cleanup_owned(record)
        for host in reversed(hosts):
            drill._bounded_reap_owned_host(host, [], [])
        if refusal is not None:
            drill._bounded_reap_owned_host(refusal, [], [])

