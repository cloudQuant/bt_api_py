"""MIG03 production inventory, group retention, and destination write enable.

These focused nodes use only synthetic temporary stores and the normal SDK
service APIs. They do not contact a provider account. The process-level old
Host exit and old-epoch refusal remain covered by the accepted R188/R235
drills; the final node adds the destination read-ready/write-disabled latch
and its explicit enable transition.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import sqlite3
import threading
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import zmq
from bt_api_base.service_protocol import WireScope
from bt_api_execution import (
    GroupBudgetReservationState,
    GroupLegPhase,
    GroupOrderSnapshot,
    GroupOrderState,
)

from bt_api_py.service_components import ComponentResources
from bt_api_py.service_host import ServiceHost, ServiceHostError
from bt_api_py.service_migration_boundary import (
    CODE_BOUNDARY_INCOMPATIBLE,
    CODE_BOUNDARY_NOT_QUIESCENT,
    ConsumerStopEvidence,
    MigrationBoundaryError,
    capture_group_participant,
    configured_consumer_inventory_sha256,
    derive_service_consumer_inventory,
    prepare_migration_boundary,
    require_consumer_inventory_compatible,
)
from bt_api_py.service_migration_gate import (
    CODE_MIGRATION_WRITE_ENABLEMENT_INVALID,
    REFUSAL_REVERSE_MIGRATION_REFUSED,
    STATUS_COMMITTED,
    STATUS_ENABLED,
    STATUS_SEALED,
    MigrationGateError,
    arm_migration_cutover,
    marker_path,
    read_migration_marker,
    record_migration_first_write,
    rollback_migration_cutover,
)
from bt_api_py.service_runtime import ServiceRuntimeError
from tests.unit import test_service_execution_group as group
from tests.unit import test_service_migration_drill as drill
from tests.unit import test_service_migration_integration as migration


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8", errors="strict")
    return hashlib.sha256(encoded).hexdigest()


def _source_bound_inventory(config, authority):
    return derive_service_consumer_inventory(
        config,
        authority.grants,
        authority=authority,
    )


def test_production_consumer_inventory_binds_authority_scope_and_exact_stop_set(
    tmp_path: Path,
) -> None:
    server_public, server_secret = zmq.curve_keypair()
    strategy_public, _strategy_secret = zmq.curve_keypair()
    keys = {
        "server_public": server_public,
        "server_secret": server_secret,
        "strategy_public": strategy_public,
    }
    source_config = drill.parse_service_config(
        drill._competitor_service_config("tcp://127.0.0.1:45101")
    )
    destination_config = drill.parse_service_config(
        drill._competitor_service_config("tcp://127.0.0.1:45102")
    )
    source_authority = drill._service_authority(
        source_config,
        keys,
        execution_instance=drill._COMPETITOR_INSTANCE,
        gateway_instance=None,
    )
    destination_authority = drill._service_authority(
        destination_config,
        keys,
        execution_instance=drill._COMPETITOR_INSTANCE,
        gateway_instance=None,
    )
    source_inventory = _source_bound_inventory(source_config, source_authority)
    destination_inventory = _source_bound_inventory(destination_config, destination_authority)

    require_consumer_inventory_compatible(source_inventory, destination_inventory)
    kinds = {item.consumer_kind for item in source_inventory}
    assert {"configured_component", "trusted_inbound_grant", "trusted_service_authority"} <= kinds
    assert all(item.scope_sha256 and item.operations_sha256 for item in source_inventory)
    assert len({item.consumer_id for item in source_inventory}) == len(source_inventory)

    changed_authority = replace(
        destination_authority,
        service_requirements_version="mig03-requirements-changed",
    )
    changed_inventory = _source_bound_inventory(destination_config, changed_authority)
    with pytest.raises(MigrationBoundaryError) as config_drift:
        require_consumer_inventory_compatible(source_inventory, changed_inventory)
    assert config_drift.value.code == CODE_BOUNDARY_INCOMPATIBLE

    checkpoint = _sha256_json({"normal_close": True, "exit_code": 0})
    exact_receipts = tuple(
        ConsumerStopEvidence(
            consumer_id=item.consumer_id,
            process_id=os.getpid(),
            close_acknowledged=True,
            exit_code=0,
            checkpoint_sha256=checkpoint,
        )
        for item in source_inventory
    )
    state_path = tmp_path / "must-not-arm.sqlite3"
    with pytest.raises(MigrationBoundaryError) as missing:
        prepare_migration_boundary(
            migration_id="mig03-consumer-inventory-negative",
            source_epoch="mig03-old-deployment",
            destination_epoch="mig03-new-deployment",
            old_writer_pid=os.getpid(),
            old_writer_exit_code=0,
            consumers=exact_receipts[:-1],
            participants=(),
            fence_reference="synthetic-local-stop-receipts",
            source_configured_consumers=source_inventory,
            destination_configured_consumers=destination_inventory,
        )
    assert missing.value.code == CODE_BOUNDARY_NOT_QUIESCENT
    assert read_migration_marker(state_path) is None
    assert configured_consumer_inventory_sha256(source_inventory) == (
        configured_consumer_inventory_sha256(destination_inventory)
    )


def _group_configuration_sha256(stable_scope: WireScope, target: Any) -> str:
    accounts = []
    for account_ref, route in sorted(target.accounts.items()):
        accounts.append(
            {
                "account_ref": account_ref,
                "execution_scope": {
                    "provider": route.execution_scope.provider,
                    "environment": route.execution_scope.environment,
                    "account_ref": route.execution_scope.account_ref,
                    "strategy_id": route.execution_scope.strategy_id,
                    "trading_day": route.execution_scope.trading_day,
                },
                "risk_scope_fingerprint": route.risk_scope_fingerprint,
                "reservation_scope_ref": route.reservation_scope_ref,
                "order_scope_ref": route.order_scope_ref,
                "query_scope_ref": route.query_scope_ref,
                "instruments": {
                    instrument: {
                        "metadata_digest": rule.metadata.digest,
                        "base_units_per_order_unit": str(rule.base_units_per_order_unit),
                        "allowed_order_types": sorted(item.value for item in rule.allowed_order_types),
                    }
                    for instrument, rule in sorted(route.instruments.items())
                },
            }
        )
    return _sha256_json(
        {
            "coordinator_scope": {
                "execution_domain_ref": stable_scope.execution_domain_ref,
                "strategy_ref": stable_scope.strategy_ref,
                "portfolio_ref": stable_scope.portfolio_ref,
            },
            "scope_ref": target.scope_ref,
            "max_group_lifetime_ns": target.max_group_lifetime_ns,
            "portfolio_budget_policy_fingerprint": (
                None
                if target.portfolio_budget_policy is None
                else target.portfolio_budget_policy.fingerprint
            ),
            "accounts": accounts,
        }
    )


def _group_business_projection(snapshot: GroupOrderSnapshot) -> dict[str, Any]:
    return {
        "request": snapshot.request.to_payload(),
        "state": snapshot.state.value,
        "revision": snapshot.revision,
        "reservations": [item.to_payload() for item in snapshot.reservations],
        "legs": [item.to_payload() for item in snapshot.legs],
        "budget_reservation": (
            None if snapshot.budget_reservation is None else snapshot.budget_reservation.to_payload()
        ),
        "created_at_ns": snapshot.created_at_ns,
        "updated_at_ns": snapshot.updated_at_ns,
    }


def _risk_policy_projection(path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(path, timeout=5.0)
    try:
        connection.execute("PRAGMA query_only = ON")
        row = connection.execute(
            "SELECT policy_id,policy_hash,max_increase_notional,max_increase_count,"
            "permit_ttl_seconds,require_strategy_allocation,notional_unit "
            "FROM risk_policy_bindings WHERE scope_key=?",
            (drill._ACCOUNT_SCOPE.key,),
        ).fetchone()
        assert row is not None
        return {
            "policy_id": row[0],
            "policy_fingerprint": row[1],
            "max_increase_notional": row[2],
            "max_increase_count": row[3],
            "permit_ttl_seconds": row[4],
            "require_strategy_allocation": row[5],
            "notional_unit": row[6],
        }
    finally:
        connection.close()


def test_nonempty_group_and_origin_binding_survive_same_path_restart_and_query(
    tmp_path: Path,
) -> None:
    now_ns = [1_900_000_000_000_000_000]
    stable_scope, target, fingerprints = group._target(now_ns[0])
    metadata = next(iter(target.accounts.values())).instruments["BTC-USDT"].metadata
    request = group._arguments(now_ns[0], metadata)
    runtime = group._LoopbackRuntime(fingerprints)
    service, store, bindings = group._make_service(
        tmp_path, runtime, stable_scope, target, now_ns=now_ns
    )
    registration = group._registration(now_ns[0])
    service.maintain_registrations((registration,), recover=False)
    restarted: tuple[Any, Any, Any] | None = None
    stores_open = True
    try:
        accepted = service.handle(
            "groups.submit",
            context=registration.context,
            arguments=request,
        )
        assert accepted["ok"] is True
        snapshot = GroupOrderSnapshot.from_payload(accepted["result"]["snapshot"])
        assert snapshot.state is GroupOrderState.RECOVERY_REQUIRED
        assert snapshot.request.deadline_ns == request["deadline_ns"]
        assert tuple(leg.leg_id for leg in snapshot.request.legs) == ("leg-a", "leg-b")
        assert tuple(leg.leg_id for leg in snapshot.legs) == ("leg-a", "leg-b")
        assert tuple(
            (
                leg.leg_id,
                leg.account_ref,
                leg.maximum_intent.instrument,
                leg.maximum_intent.side.value,
                leg.maximum_intent.quantity,
                leg.maximum_intent.price,
            )
            for leg in snapshot.request.legs
        ) == (
            ("leg-a", "account-a", "BTC-USDT", "BUY", Decimal("1"), Decimal("100")),
            ("leg-b", "account-b", "BTC-USDT", "SELL", Decimal("1"), Decimal("100")),
        )
        assert snapshot.legs[0].phase is GroupLegPhase.UNKNOWN
        assert snapshot.legs[0].child_status is not None
        assert snapshot.legs[0].child_status.state.value == "UNKNOWN"
        assert snapshot.legs[0].submitted_intent is not None
        assert snapshot.legs[1].phase is GroupLegPhase.READY
        assert snapshot.legs[1].submitted_intent is None
        assert len(snapshot.reservations) == 2
        assert {item.account_ref for item in snapshot.reservations} == {"account-a", "account-b"}
        assert all(item.deadline_ns == request["deadline_ns"] for item in snapshot.reservations)
        assert sum(len(item.legs) for item in snapshot.reservations) == 2
        assert all(
            leg.expires_at_ns == request["deadline_ns"]
            and leg.state == "ACTIVE"
            and not leg.claimed
            and not leg.resolved
            for reservation in snapshot.reservations
            for leg in reservation.legs
        )
        assert snapshot.budget_reservation is not None
        assert snapshot.budget_reservation.state is GroupBudgetReservationState.HELD
        assert snapshot.budget_reservation.deadline_ns == request["deadline_ns"]
        assert snapshot.budget_reservation.gross_notional > Decimal("0")
        assert snapshot.budget_reservation.valuation_unit == (
            target.portfolio_budget_policy.valuation_unit
        )
        assert snapshot.budget_reservation.policy_fingerprint == (
            target.portfolio_budget_policy.fingerprint
        )
        assert snapshot.request.budget_quote is not None
        assert snapshot.budget_reservation.quote_fingerprint == (
            snapshot.request.budget_quote.fingerprint
        )
        assert snapshot.budget_reservation.gross_notional == (
            snapshot.request.budget_quote.gross_notional
        )
        expected_reservations = {
            (
                leg.account_ref,
                leg.leg_id,
                leg.maximum_intent.intent_id,
                leg.maximum_intent.fingerprint,
            )
            for leg in snapshot.request.legs
        }
        actual_reservations = {
            (
                reservation.account_ref,
                leg.leg_id,
                leg.intent_id,
                leg.reserved_intent_hash,
            )
            for reservation in snapshot.reservations
            for leg in reservation.legs
        }
        assert actual_reservations == expected_reservations
        assert len(runtime.reservations) == 2
        assert len(runtime.submitted) == 1
        assert snapshot.legs[0].submitted_intent is not None
        assert runtime.submitted == [snapshot.legs[0].submitted_intent.intent_id]
        original_binding = bindings.get(group._PORTFOLIO, group._GROUP_ID)
        assert original_binding is not None
        assert original_binding.origin_run_id == group._RUN
        assert (
            original_binding.request_fingerprint
            == snapshot.request.unquoted_fingerprint
        )
        original_events = store.events(group._PORTFOLIO, group._GROUP_ID)
        assert original_events
        assert original_binding.request.legs == snapshot.request.legs
        assert original_binding.strategy_scope.run_id == group._RUN
        assert original_binding.strategy_scope.account_refs == snapshot.request.account_refs

        service.close()
        store.close()
        bindings.close()
        stores_open = False
        before = capture_group_participant(
            tmp_path / "groups.sqlite3",
            tmp_path / "group-runs.sqlite3",
            configuration_sha256=_group_configuration_sha256(stable_scope, target),
        )
        assert before.evidence.row_count > 0
        assert set(before.table_names) >= {
            "group_store:order_group_snapshots",
            "group_store:order_group_events",
            "coordinator_run_bindings:coordinator_group_run_bindings",
        }

        restarted_runtime = group._LoopbackRuntime(fingerprints)
        restarted_runtime.orders.update(runtime.orders)
        restarted = group._make_service(
            tmp_path,
            restarted_runtime,
            stable_scope,
            target,
            now_ns=now_ns,
        )
        restarted_service, reopened_store, reopened_bindings = restarted
        renewed = group._registration(
            now_ns[0],
            registration_id="mig03-registration-after-restart",
            service_epoch="mig03-destination-registry-epoch",
        )
        restarted_service.maintain_registrations((renewed,), recover=False)
        queried = restarted_service.handle(
            "groups.query",
            context=renewed.context,
            arguments={"group_id": group._GROUP_ID},
        )
        assert queried["ok"] is True
        recovered = GroupOrderSnapshot.from_payload(queried["result"]["snapshot"])
        assert recovered == snapshot
        assert _group_business_projection(recovered) == _group_business_projection(snapshot)
        assert reopened_store.events(group._PORTFOLIO, group._GROUP_ID) == original_events
        recovered_binding = reopened_bindings.get(group._PORTFOLIO, group._GROUP_ID)
        assert recovered_binding == original_binding
        assert recovered_binding is not None
        assert recovered_binding.origin_run_id == group._RUN
        assert (
            recovered_binding.request_fingerprint
            == recovered.request.unquoted_fingerprint
        )
        assert restarted_runtime.calls == []
        assert restarted_runtime.submitted == []

        restarted_service.close()
        reopened_store.close()
        reopened_bindings.close()
        restarted = None
        after = capture_group_participant(
            tmp_path / "groups.sqlite3",
            tmp_path / "group-runs.sqlite3",
            configuration_sha256=_group_configuration_sha256(stable_scope, target),
            expected_schema_sha256=before.schema_sha256,
        )
        assert after == before
    finally:
        if stores_open:
            service.close()
            store.close()
            bindings.close()
        if restarted is not None:
            restarted_service, reopened_store, reopened_bindings = restarted
            restarted_service.close()
            reopened_store.close()
            reopened_bindings.close()


def _host_resources(
    *,
    execution_path: Path,
    risk_path: Path,
    provider_path: Path,
    gateway_path: Path,
):
    state: dict[str, Any] = {"resources": []}

    def resource_provider(component, _instance_id, _options, _scope):
        if component == "execution":
            if "provider" not in state:
                provider = drill._DurableTimeoutProvider(str(provider_path))
                store = drill.SqliteExecutionStore(execution_path)
                gate = drill.DurableRiskGate(
                    risk_path,
                    drill._RISK_POLICY,
                    clock=lambda: drill._RISK_CLOCK_SECONDS,
                )
                mapper = drill.InstrumentRiskAdmissionMapper(
                    drill._ACCOUNT_SCOPE,
                    drill.InstrumentRiskRegistry((drill._metadata(),)),
                    clock_ns=lambda: drill._METADATA_TIME_NS + 1,
                )
                state.update(
                    provider=provider,
                    store=store,
                    gate=gate,
                    admission=drill.DurableRiskAdmissionAdapter(gate, mapper),
                )
                state["resources"].extend((provider, store, gate))
            return ComponentResources.borrowed(
                {
                    "store": state["store"],
                    "targets": {drill._WIRE_SCOPE: drill._EXECUTION_SCOPE},
                    "dispatch": state["provider"],
                    "admission": {"order_gate": state["admission"], "cancel_gate": None},
                },
                ready=lambda: True,
            )
        if component == "gateway":
            journal = drill.GatewayEventJournal(
                str(gateway_path),
                clock=lambda: drill._JOURNAL_CLOCK_SECONDS,
            )
            state["journal"] = journal
            account = drill.GatewayServiceAccountScope(
                account_ref=drill._ACCOUNT,
                execution_domain_ref=drill._DOMAIN,
                journal_account_scope=drill._JOURNAL_ACCOUNT_SCOPE,
                strategy_scope_map={drill._STRATEGY: drill._JOURNAL_STRATEGY_SCOPE},
            )
            return ComponentResources.borrowed(
                {
                    "journal": journal,
                    "accounts": {drill._ACCOUNT: account},
                    "principals": {drill._STRATEGY_PRINCIPAL: drill._gateway_principal()},
                },
                ready=lambda: True,
            )
        raise AssertionError("unexpected MIG03 synthetic Host component")

    return resource_provider, state["resources"], lambda: state.get("journal")


def _start_local_serving_host(
    config,
    authority,
    registry_path: Path,
    resource_provider,
    resources: list[Any],
    *,
    deployment_epoch: str,
) -> dict[str, Any]:
    """Serve a real Host on its owner thread while the test drives ZMQ clients."""

    commands: queue.Queue[Any] = queue.Queue()
    startup: queue.Queue[Any] = queue.Queue(maxsize=1)

    def close_owned_resources() -> None:
        failures: list[str] = []
        for resource in resources:
            close = getattr(resource, "close", None)
            if callable(close):
                try:
                    close()
                except BaseException as error:
                    failures.append(type(error).__name__)
        if failures:
            raise AssertionError(("synthetic Host resource close failed", tuple(failures)))

    def run() -> None:
        host = ServiceHost(
            config,
            authority,
            registry_path,
            resource_provider,
            migration_deployment_epoch=deployment_epoch,
        )
        started = False
        resources_closed = False
        try:
            host.start()
            started = True
            startup.put(("started", host.endpoint))
            while True:
                try:
                    name, arguments, response = commands.get(timeout=0.005)
                except queue.Empty:
                    host.serve_once(timeout_ms=5)
                    continue
                try:
                    if name == "status":
                        registry = host._registry
                        value = {
                            "healthy": host.healthy,
                            "registry_ready": registry is not None and registry.ready,
                            "migration_writes_enabled": (
                                True if registry is None else registry.migration_writes_enabled
                            ),
                            "service_epoch": None if registry is None else registry.service_epoch,
                        }
                    elif name == "inventory":
                        value = host.migration_consumer_inventory()
                    elif name == "enable":
                        host.enable_migration_writes(**arguments)
                        value = None
                    elif name == "close":
                        host.close()
                        close_owned_resources()
                        resources_closed = True
                        response.put(("ok", host.state))
                        return
                    else:
                        raise AssertionError("unknown synthetic Host command")
                    response.put(("ok", value))
                except BaseException as error:
                    response.put(("error", error))
        except BaseException as error:
            if not started:
                startup.put(("error", error))
        finally:
            if started and host.state not in {"closed", "failed", "close_failed"}:
                host.close()
            if not resources_closed:
                close_owned_resources()

    thread = threading.Thread(
        target=run,
        name=f"mig03-local-host-{deployment_epoch}",
        daemon=False,
    )
    thread.start()
    kind, value = startup.get(timeout=15)
    if kind != "started":
        thread.join(timeout=5)
        raise value
    return {"thread": thread, "commands": commands, "endpoint": value}


def _local_host_command(host: dict[str, Any], name: str, **arguments: Any) -> Any:
    response: queue.Queue[Any] = queue.Queue(maxsize=1)
    host["commands"].put((name, arguments, response))
    kind, value = response.get(timeout=15)
    if kind != "ok":
        raise value
    return value


def _close_local_serving_host(host: dict[str, Any] | None) -> None:
    if host is None:
        return
    thread = host["thread"]
    if thread.is_alive():
        try:
            state = _local_host_command(host, "close")
            assert state == "closed"
        finally:
            thread.join(timeout=10)
    assert not thread.is_alive(), "synthetic Host thread did not close normally"


def _client_runtime(endpoint: str, keys: dict[str, bytes], *, session_id: str):
    return drill._new_runtime(endpoint, keys, session_id=session_id)


def test_legacy_first_write_cannot_enable_restarted_destination_host(
    tmp_path: Path,
) -> None:
    old_epoch = migration._OLD_DEPLOYMENT_EPOCH
    new_epoch = migration._NEW_DEPLOYMENT_EPOCH
    registry_path = tmp_path / "migration-registry.sqlite3"
    execution_path = tmp_path / "migration-execution.sqlite3"
    risk_path = tmp_path / "migration-risk.sqlite3"
    provider_path = tmp_path / "migration-provider.sqlite3"
    gateway_path = tmp_path / "migration-gateway.sqlite3"
    drill._seed_journal(str(gateway_path))
    server_public, server_secret = zmq.curve_keypair()
    strategy_public, strategy_secret = zmq.curve_keypair()
    keys = {
        "server_public": server_public,
        "server_secret": server_secret,
        "strategy_public": strategy_public,
        "strategy_secret": strategy_secret,
    }

    def start_destination_host(endpoint: str):
        config = drill.parse_service_config(drill._service_config(endpoint))
        authority = drill._service_authority(
            config,
            keys,
            execution_instance=drill._EXEC_INSTANCE,
            gateway_instance=drill._GW_INSTANCE,
        )
        resource_provider, resources, _journal = _host_resources(
            execution_path=execution_path,
            risk_path=risk_path,
            provider_path=provider_path,
            gateway_path=gateway_path,
        )
        host = _start_local_serving_host(
            config,
            authority,
            registry_path,
            resource_provider,
            resources,
            deployment_epoch=new_epoch,
        )
        return host

    # Start and normally close this stable destination once before arming,
    # then restart it after the legacy API is attempted. This reproduces the
    # process-local latch reset where the old COMMITTED -> SEALED loophole
    # could otherwise make a restarted destination writable.
    first_host = None
    try:
        first_host = start_destination_host(drill._endpoint())
        first_status = _local_host_command(first_host, "status")
        assert first_status["migration_writes_enabled"] is True
        first_process_epoch = first_status["service_epoch"]
        assert type(first_process_epoch) is str and first_process_epoch
        assert first_process_epoch != new_epoch
    finally:
        _close_local_serving_host(first_host)

    committed = arm_migration_cutover(
        registry_path,
        source_epoch=old_epoch,
        new_epoch=new_epoch,
        migration_id="mig03-legacy-first-write-fence",
    )
    committed_bytes = marker_path(registry_path).read_bytes()
    with pytest.raises(MigrationGateError) as refused_legacy_transition:
        record_migration_first_write(registry_path, fact_kind="dispatch")
    assert refused_legacy_transition.value.code == CODE_MIGRATION_WRITE_ENABLEMENT_INVALID
    assert marker_path(registry_path).read_bytes() == committed_bytes
    assert read_migration_marker(registry_path) == committed

    restarted_host = None
    runtime = None
    try:
        endpoint = drill._endpoint()
        restarted_host = start_destination_host(endpoint)
        status = _local_host_command(restarted_host, "status")
        assert status["healthy"] and status["registry_ready"]
        assert type(status["service_epoch"]) is str and status["service_epoch"]
        assert status["service_epoch"] != first_process_epoch
        assert status["service_epoch"] != new_epoch
        assert status["migration_writes_enabled"] is False

        runtime = _client_runtime(
            endpoint, keys, session_id="mig03-legacy-restart-readonly"
        )
        assert runtime.start().degraded_count == 0
        with pytest.raises(drill.ServiceExecutionError) as blocked:
            drill._execution_client(runtime).submit(
                **drill._submit_arguments(
                    "mig03-legacy-restart-intent",
                    "mig03-legacy-restart-signal",
                    quantity="1",
                    price="50000",
                )
            )
        assert blocked.value.code == "MIGRATION_WRITE_DISABLED"
        assert drill._execution_client(runtime).query("mig03-legacy-restart-intent") is None
        assert drill._provider_count(str(provider_path), "mig03-legacy-restart-intent") == 0
        assert drill._risk_rows(str(risk_path), "mig03-legacy-restart-intent") == {
            "reservations": (),
            "dispatch_claims": (),
            "freezes": (),
        }
        assert read_migration_marker(registry_path) == committed
    finally:
        if runtime is not None:
            runtime.close()
        _close_local_serving_host(restarted_host)


def test_destination_host_waits_for_explicit_enable_then_seals_first_write(
    tmp_path: Path,
) -> None:
    old_epoch = migration._OLD_DEPLOYMENT_EPOCH
    new_epoch = migration._NEW_DEPLOYMENT_EPOCH
    endpoint_old = drill._endpoint()
    endpoint_new = drill._endpoint()
    server_public, server_secret = zmq.curve_keypair()
    strategy_public, strategy_secret = zmq.curve_keypair()
    keys = {
        "server_public": server_public,
        "server_secret": server_secret,
        "strategy_public": strategy_public,
        "strategy_secret": strategy_secret,
    }
    registry_path = tmp_path / "migration-registry.sqlite3"
    execution_path = tmp_path / "migration-execution.sqlite3"
    risk_path = tmp_path / "migration-risk.sqlite3"
    provider_path = tmp_path / "migration-provider.sqlite3"
    gateway_path = tmp_path / "migration-gateway.sqlite3"
    drill._seed_journal(str(gateway_path))
    raw_old = drill._service_config(endpoint_old)
    raw_new = drill._service_config(endpoint_new)
    config_old = drill.parse_service_config(raw_old)
    config_new = drill.parse_service_config(raw_new)
    authority_old = drill._service_authority(
        config_old,
        keys,
        execution_instance=drill._EXEC_INSTANCE,
        gateway_instance=drill._GW_INSTANCE,
    )
    authority_new = drill._service_authority(
        config_new,
        keys,
        execution_instance=drill._EXEC_INSTANCE,
        gateway_instance=drill._GW_INSTANCE,
    )
    source_provider_factory, source_resources, source_journal = _host_resources(
        execution_path=execution_path,
        risk_path=risk_path,
        provider_path=provider_path,
        gateway_path=gateway_path,
    )
    old_host = None
    old_runtime = None
    old_binding = None
    old_closed = False
    destination_host = None
    destination_runtime = None
    destination_resources = None
    stale_transport = None
    try:
        old_host = _start_local_serving_host(
            config_old,
            authority_old,
            registry_path,
            source_provider_factory,
            source_resources,
            deployment_epoch=old_epoch,
        )
        assert old_host["endpoint"] == endpoint_old
        source_status = _local_host_command(old_host, "status")
        assert source_status["healthy"] and source_status["registry_ready"]
        source_inventory = _local_host_command(old_host, "inventory")
        assert sum(
            item.consumer_kind == "configured_component" for item in source_inventory
        ) == len(config_old.components)
        assert {
            item.consumer_kind for item in source_inventory
        } >= {
            "configured_component",
            "trusted_inbound_grant",
            "trusted_service_authority",
        }
        old_runtime = _client_runtime(
            endpoint_old,
            keys,
            session_id="mig03-destination-source-session",
        )
        assert old_runtime.start().degraded_count == 0
        old_binding = drill._binding_of(old_runtime, "broker.orders")
        first = drill._execution_client(old_runtime).submit(
            **drill._submit_arguments(
                drill._INTENT_ID,
                "mig03-destination-source-signal",
                quantity="1",
                price="50000",
            )
        )
        assert first.state == "UNKNOWN" and first.dispatch_attempts == 1
        source_risk_state = drill._risk_rows(str(risk_path), drill._INTENT_ID)
        source_policy = _risk_policy_projection(risk_path)
        assert source_policy["policy_id"] == drill._RISK_POLICY.policy_id
        assert source_policy["policy_fingerprint"] == drill._RISK_POLICY.fingerprint
        first_read = old_runtime.call(
            "store.account",
            "account.read_after",
            {"cursor": 0, "limit": 10, "track_delivery": True},
        )
        assert first_read["consumer_progress"]["offered_cursor"] == 2
        stream_epoch = first_read["consumer_progress"]["stream_epoch"]
        ack = old_runtime.call(
            "store.account",
            "account.ack",
            {"stream_epoch": stream_epoch, "cursor": 2},
        )
        assert ack["consumer_progress"]["acknowledged_cursor"] == 2
        before_provider_count = drill._provider_count(str(provider_path), drill._INTENT_ID)
        assert before_provider_count == 1
        old_runtime.close()
        assert old_runtime.state == "closed"
        assert old_runtime.cleanup_failures == 0
        _close_local_serving_host(old_host)
        old_closed = True
        source_gateway = source_journal()
        assert source_gateway is not None

        armed = arm_migration_cutover(
            registry_path,
            source_epoch=old_epoch,
            new_epoch=new_epoch,
            migration_id="mig03-explicit-destination-enable",
        )
        assert armed.status == STATUS_COMMITTED
        assert armed.sealed is False

        destination_provider_factory, destination_resources, destination_journal = _host_resources(
            execution_path=execution_path,
            risk_path=risk_path,
            provider_path=provider_path,
            gateway_path=gateway_path,
        )
        destination_host = _start_local_serving_host(
            config_new,
            authority_new,
            registry_path,
            destination_provider_factory,
            destination_resources,
            deployment_epoch=new_epoch,
        )
        assert destination_host["endpoint"] == endpoint_new
        destination_status = _local_host_command(destination_host, "status")
        assert destination_status["healthy"] and destination_status["registry_ready"]
        assert destination_status["migration_writes_enabled"] is False
        assert destination_journal() is not None
        destination_inventory = _local_host_command(destination_host, "inventory")
        require_consumer_inventory_compatible(source_inventory, destination_inventory)

        destination_runtime = _client_runtime(
            endpoint_new,
            keys,
            session_id="mig03-destination-target-session",
        )
        assert destination_runtime.start().degraded_count == 0
        order_binding = drill._binding_of(destination_runtime, "broker.orders")
        assert order_binding.risk_policy_status == "NOT_APPLICABLE"
        assert order_binding.risk_policy_ref is None
        assert order_binding.risk_policy_version is None
        destination_policy = _risk_policy_projection(risk_path)
        assert destination_policy == source_policy
        recovered = drill._execution_client(destination_runtime).query(drill._INTENT_ID)
        assert recovered is not None
        assert recovered.state == "UNKNOWN"
        assert recovered.dispatch_attempts == 1
        assert recovered.permit_reference == first.permit_reference
        assert drill._risk_rows(str(risk_path), drill._INTENT_ID) == source_risk_state
        progress_value = destination_runtime.call(
            "store.account", "account.progress", {}
        )["consumer_progress"]
        assert set(progress_value) == {
            "stream_epoch",
            "offered_cursor",
            "acknowledged_cursor",
        }
        progress = {
            "stream_epoch": progress_value["stream_epoch"],
            "offered_cursor": progress_value["offered_cursor"],
            "acknowledged_cursor": progress_value["acknowledged_cursor"],
        }
        assert type(progress["stream_epoch"]) is str and progress["stream_epoch"]
        assert type(progress["offered_cursor"]) is int
        assert type(progress["acknowledged_cursor"]) is int
        assert progress == {
            "stream_epoch": stream_epoch,
            "offered_cursor": 2,
            "acknowledged_cursor": 2,
        }
        assert read_migration_marker(registry_path) == armed

        with pytest.raises(ServiceRuntimeError) as tracked_read_disabled:
            destination_runtime.call(
                "store.account",
                "account.read_after",
                {"cursor": 0, "limit": 10, "track_delivery": True},
            )
        assert tracked_read_disabled.value.code == "MIGRATION_WRITE_DISABLED"
        assert destination_runtime.call("store.account", "account.progress", {})[
            "consumer_progress"
        ] == progress
        assert read_migration_marker(registry_path) == armed

        stale_client_config = drill._client_config(endpoint_new)
        stale_profile = stale_client_config.communication["profiles"][
            drill._CLIENT_CONNECT_PROFILE
        ]
        stale_transport = drill.ZmqTransportFactory(
            {
                drill._CLIENT_SECURITY_REF: drill.ClientCurveKeys(
                    keys["server_public"],
                    keys["strategy_public"],
                    keys["strategy_secret"],
                )
            }
        )(stale_profile, drill._SERVICE_IDENTITY)
        stale_reply = drill._direct_request(
            stale_transport,
            identity=drill._STRATEGY_IDENTITY,
            scope=old_binding.scope,
            operation="orders.submit",
            binding=old_binding,
            arguments=drill._submit_arguments(
                "mig03-stale-old-epoch-attempt",
                "mig03-stale-old-epoch-signal",
                quantity="1",
                price="50000",
            ),
        )
        assert stale_reply.payload["ok"] is False
        assert stale_reply.payload["code"] == "STALE_EPOCH"
        assert drill._provider_count(str(provider_path), "mig03-stale-old-epoch-attempt") == 0
        assert drill._provider_count(str(provider_path), drill._INTENT_ID) == 1

        with pytest.raises(drill.ServiceExecutionError) as disabled:
            drill._execution_client(destination_runtime).submit(
                **drill._submit_arguments(
                    drill._NEW_INTENT_ID,
                    "mig03-destination-before-enable",
                    quantity="1",
                    price="50000",
                )
            )
        assert disabled.value.code == "MIGRATION_WRITE_DISABLED"
        assert drill._execution_client(destination_runtime).query(drill._NEW_INTENT_ID) is None
        assert drill._provider_count(str(provider_path), drill._NEW_INTENT_ID) == 0
        assert drill._risk_rows(str(risk_path), drill._NEW_INTENT_ID) == {
            "reservations": (),
            "dispatch_claims": (),
            "freezes": (),
        }

        def consumer_document(item) -> dict[str, str]:
            return {
                "consumer_id": item.consumer_id,
                "consumer_kind": item.consumer_kind,
                "configuration_sha256": item.configuration_sha256,
                "scope_sha256": item.scope_sha256,
                "operations_sha256": item.operations_sha256,
            }

        readiness_sha256 = _sha256_json(
            {
                "source_consumers": [consumer_document(item) for item in source_inventory],
                "destination_consumers": [
                    consumer_document(item) for item in destination_inventory
                ],
                "old_order": {
                    "intent_id": recovered.intent_id,
                    "state": recovered.state,
                    "dispatch_attempts": recovered.dispatch_attempts,
                    "permit_reference": recovered.permit_reference,
                },
                "consumer_progress": progress,
                "client_risk_policy_projection": {
                    "status": order_binding.risk_policy_status,
                    "reference": order_binding.risk_policy_ref,
                    "version": order_binding.risk_policy_version,
                },
                "durable_risk_policy": destination_policy,
                "destination_epoch": new_epoch,
                "host_healthy": destination_status["healthy"],
                "registry_ready": destination_status["registry_ready"],
            }
        )
        _local_host_command(
            destination_host,
            "enable",
            migration_id=armed.migration_id,
            readiness_sha256=readiness_sha256,
        )
        enabled = read_migration_marker(registry_path)
        assert enabled is not None and enabled.status == STATUS_ENABLED
        with pytest.raises(MigrationGateError) as enabled_rollback:
            rollback_migration_cutover(registry_path, deployment_epoch=old_epoch)
        assert enabled_rollback.value.code == REFUSAL_REVERSE_MIGRATION_REFUSED
        assert read_migration_marker(registry_path) == enabled
        assert _local_host_command(destination_host, "status")[
            "migration_writes_enabled"
        ] is True
        new_order = drill._execution_client(destination_runtime).submit(
            **drill._submit_arguments(
                drill._NEW_INTENT_ID,
                "mig03-destination-after-enable",
                quantity="1",
                price="50000",
            )
        )
        # The preserved UNKNOWN order still owns a dispatch-inflight Risk
        # freeze, so the enabled destination's real handler durably rejects a
        # second risk-increasing intent without another reservation/provider
        # call. Registry classifies the pre-handler orders.submit as
        # fact_kind="dispatch"; that marker is a write-attempt barrier, not
        # evidence of provider dispatch or successful business commit.
        assert new_order.state == "REJECTED"
        assert new_order.dispatch_attempts == 0
        assert new_order.permit_reference is None
        assert drill._execution_client(destination_runtime).query(
            drill._NEW_INTENT_ID
        ) == new_order
        assert drill._risk_rows(str(risk_path), drill._NEW_INTENT_ID) == {
            "reservations": (),
            "dispatch_claims": (),
            "freezes": (),
        }
        assert drill._risk_rows(str(risk_path), drill._INTENT_ID) == source_risk_state
        sealed = read_migration_marker(registry_path)
        assert sealed is not None and sealed.status == STATUS_SEALED
        assert sealed.migration_id == armed.migration_id
        assert sealed.first_write_fact_kind == "dispatch"
        with pytest.raises(MigrationGateError) as sealed_rollback:
            rollback_migration_cutover(registry_path, deployment_epoch=old_epoch)
        assert sealed_rollback.value.code == REFUSAL_REVERSE_MIGRATION_REFUSED
        assert read_migration_marker(registry_path) == sealed
        assert drill._provider_count(str(provider_path), drill._NEW_INTENT_ID) == 0
        assert drill._provider_count(str(provider_path), "mig03-stale-old-epoch-attempt") == 0

        refused_calls: list[str] = []

        def refuse_old_resources(component, *_args):
            refused_calls.append(component)
            raise AssertionError("sealed old deployment must be refused before resource access")

        refused_old_host = ServiceHost(
            config_old,
            authority_old,
            registry_path,
            refuse_old_resources,
            migration_deployment_epoch=old_epoch,
        )
        with pytest.raises(ServiceHostError) as refused:
            refused_old_host.start()
        assert refused.value.code == "REVERSE_MIGRATION_REFUSED"
        assert refused_calls == []
        assert drill._provider_count(str(provider_path), drill._NEW_INTENT_ID) == 0
    finally:
        if stale_transport is not None:
            stale_transport.close()
        if destination_runtime is not None:
            destination_runtime.close()
        _close_local_serving_host(destination_host)
        if old_runtime is not None and old_runtime.state != "closed":
            old_runtime.close()
        if not old_closed:
            _close_local_serving_host(old_host)
