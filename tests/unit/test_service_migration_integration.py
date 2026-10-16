"""One-process-chain MIG03 import, epoch handoff, and cursor rebuild drill.

This test uses temporary SQLite state and real spawned ServiceHost processes.
It composes the already accepted v23 importer, migration gate, and GW03
consumer recovery path; it does not touch an installed account or provider.
"""

from __future__ import annotations

import copy
import inspect
import json
import os
import sqlite3
import sys
from contextlib import suppress
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import multiprocessing
import pytest
import zmq
from bt_api_base.account_protocol import AccountKey
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution.contracts import (
    ExecutionState,
    ProviderObservation,
)
from bt_api_execution import ManagedExecutionFacade, OrderIntent
from bt_api_gateway import GatewayEventJournal
from bt_api_py.service_account_consumer import ServiceAccountConsumer
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_execution import ServiceExecutionError
from bt_api_py.service_migration_gate import (
    REFUSAL_REVERSE_MIGRATION_REFUSED,
    MigrationGateError,
    arm_migration_cutover,
    read_migration_marker,
    rollback_migration_cutover,
)
from bt_api_py.service_migration_import import (
    CODE_SOURCE_DRIFT,
    MigrationImportError,
    apply_migration_import,
    inspect_execution_snapshot,
)
from bt_api_py.service_migration_map import (
    SOURCE_ACCOUNT_CONFIG,
    SOURCE_BARE_ORDER_ID,
    SOURCE_BARE_SYMBOL,
    SOURCE_IN_PROCESS_RUN,
    SOURCE_LEGACY_STRATEGY_ID,
    MigrationIdentityStore,
)
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    ServiceCapability,
    ServiceRegistry,
)
from bt_api_py.service_runtime import ClientCurveKeys, ServiceClientRuntime, ZmqTransportFactory
from tests.unit import test_service_migration_drill as drill
from tests.unit import test_service_migration_import as import_fixtures
from tests.unit import test_service_migration_write_barrier as write_barrier_fixtures
from tests.unit.test_service_account_cursor_recovery_integration import (
    _event_reducer,
    _snapshot_reducer,
)


_OLD_DEPLOYMENT_EPOCH = "mig03-integrated-old-deployment-r1"
_NEW_DEPLOYMENT_EPOCH = "mig03-integrated-new-deployment-r1"
_TARGET_RUN_REF = "mig03-integrated-target-run-r1"
_SOURCE_ACCOUNT_CONFIG_ID = "mig03-integrated-source-account-config"
_SOURCE_PROVIDER_ORDER_ID = "mig03-integrated-provider-order-filled"
_PROJECTION_VERSION = "mig03-integrated-gw03-rebuild-v1"
_INITIAL_PROJECTION: dict[str, Any] = {"last_sequence": 0, "event_ids": []}

def _assert_integrated_source_origins() -> dict[str, str]:
    """Check this drill's SDK, exported Base, and exported Risk origins."""

    source_root = os.environ.get("BTAPI_MIG03B_FIXED_SOURCE_ROOT")
    repository = (
        Path(source_root).resolve()
        if source_root is not None
        else Path(drill.__file__).resolve().parents[2]
    )
    risk_source_root = os.environ.get("BTAPI_MIG03_FIXED_RISK_SOURCE_ROOT")
    assert risk_source_root, "Root QA must provide the fixed Risk export source root"
    risk_package_root = Path(risk_source_root).resolve() / "bt_api_risk"
    base_source_root = os.environ.get("BTAPI_MIG03_FIXED_BASE_SOURCE_ROOT")
    assert base_source_root, "Root QA must provide the fixed Base export source root"
    base_package_root = Path(base_source_root).resolve() / "bt_api_base"

    symbols = (
        (drill.ServiceHost, repository / "bt_api_py/service_host.py"),
        (drill.ServiceRegistry, repository / "bt_api_py/service_registry.py"),
        (drill.ServiceClientRuntime, repository / "bt_api_py/service_runtime.py"),
        (drill.ServiceExecutionClient, repository / "bt_api_py/service_execution.py"),
        (AccountKey, base_package_root / "account_protocol.py"),
        (MarketInstrumentKey, base_package_root / "market_protocol.py"),
        (WireIdentity, base_package_root / "service_protocol.py"),
        (WireRole, base_package_root / "service_protocol.py"),
        (WireScope, base_package_root / "service_protocol.py"),
        (
            drill.DurableRiskAdmissionAdapter,
            risk_package_root / "order_admission.py",
        ),
        (drill.DurableRiskGate, risk_package_root / "core/admission.py"),
        (
            drill.SqliteExecutionStore,
            repository / "bt_api/bt_api_execution/src/bt_api_execution/store.py",
        ),
        (
            drill.GatewayEventJournal,
            repository / "bt_api/bt_api_gateway/src/bt_api_gateway/event_journal.py",
        ),
    )
    actual_origins: dict[str, str] = {}
    for symbol, expected in symbols:
        actual = Path(inspect.getsourcefile(symbol)).resolve()
        assert actual == expected.resolve(), (symbol.__name__, actual, expected.resolve())
        actual_origins[symbol.__name__] = str(actual)
    return actual_origins


@dataclass(frozen=True, slots=True)
class _MappedTargetRegistration:
    """Resolved identity values used to construct the destination client."""

    account_key: AccountKey
    account_ref: str
    strategy_ref: str
    run_ref: str
    booked_instrument: str
    order_ref: str
    service_config: dict[str, Any]
    wire_scope: WireScope
    identity: WireIdentity


class _MigrationProvider:
    """Synthetic provider: one dispatched order whose reply is lost."""

    def __init__(self, journal_path: str) -> None:
        self._db = sqlite3.connect(journal_path, timeout=5.0, isolation_level=None)
        self.cancel_calls = 0
        self._db.execute("PRAGMA journal_mode = WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS provider_dispatches "
            "(sequence INTEGER PRIMARY KEY AUTOINCREMENT, intent_id TEXT NOT NULL)"
        )

    def submit(self, intent: Any) -> ProviderObservation:
        if intent.intent_id != drill._NEW_INTENT_ID:
            raise AssertionError("only the new UNKNOWN order may reach this provider")
        self._db.execute(
            "INSERT INTO provider_dispatches(intent_id) VALUES(?)", (intent.intent_id,)
        )
        raise TimeoutError("synthetic provider accepted request and lost its reply")

    def cancel(self, _intent: Any) -> None:
        self.cancel_calls += 1
        raise AssertionError("cancellation is outside this migration drill")

    def close(self) -> None:
        self._db.close()


def _migration_host_process(
    control,
    raw_config: dict[str, Any],
    keys: dict[str, bytes],
    deployment_epoch: str,
    registry_path: str,
    execution_path: str,
    risk_path: str,
    provider_journal_path: str,
    gateway_path: str,
) -> None:
    """Reuse R188's real Host process composition with the two provider leaves."""

    original_provider = drill._DurableTimeoutProvider
    original_origin_checker = drill._assert_fixed_source_origins
    drill._DurableTimeoutProvider = _MigrationProvider
    drill._assert_fixed_source_origins = _assert_integrated_source_origins
    try:
        drill._host_process(
            control,
            raw_config,
            keys,
            deployment_epoch,
            registry_path,
            execution_path,
            risk_path,
            provider_journal_path,
            gateway_path,
        )
    finally:
        drill._DurableTimeoutProvider = original_provider
        drill._assert_fixed_source_origins = original_origin_checker


def _migration_refusal_process(
    control,
    raw_config: dict[str, Any],
    keys: dict[str, bytes],
    registry_path: str,
    declared_epoch: str,
) -> None:
    """Use the pinned export-aware checker in R188's refusal child."""

    original_origin_checker = drill._assert_fixed_source_origins
    drill._assert_fixed_source_origins = _assert_integrated_source_origins
    try:
        drill._refusal_process(
            control,
            raw_config,
            keys,
            registry_path,
            declared_epoch,
        )
    finally:
        drill._assert_fixed_source_origins = original_origin_checker


def _seed_preexisting_completed_fill(
    execution_path: Path,
    submit_arguments: dict[str, Any],
) -> str:
    """Seed one synthetic, pre-existing completed source order before Hosts.

    The old Host later submits the identical immutable request and must read
    this terminal record idempotently. The fill is historical baseline state;
    this test's provider only dispatches the new UNKNOWN order. Keeping that
    historical dispatch outside the current Risk claim window leaves the
    host's sole new opening dispatch available without weakening Risk's latch.
    """

    assert submit_arguments["order_type"] == "LIMIT"
    intent = OrderIntent.limit(
        intent_id=submit_arguments["intent_id"],
        scope=drill._EXECUTION_SCOPE,
        signal_id=submit_arguments["signal_id"],
        instrument=submit_arguments["instrument"],
        side=submit_arguments["side"],
        position_effect=submit_arguments["position_effect"],
        quantity=submit_arguments["quantity"],
        price=submit_arguments["price"],
        time_in_force=submit_arguments["time_in_force"],
        reduce_only=submit_arguments.get("reduce_only", False),
        metadata_version=submit_arguments["metadata_version"],
        deadline_ns=submit_arguments.get("deadline_ns"),
        tags=submit_arguments["tags"],
    )
    store = drill.SqliteExecutionStore(execution_path)
    writer: ManagedExecutionFacade | None = None
    lease = None
    try:
        writer = ManagedExecutionFacade(
            store,
            drill._EXECUTION_SCOPE,
            writer_id="mig03-integrated-prior-fill-seed",
        )
        lease = writer.acquire_writer_lease()
        record = store.admit_intent(intent, writer_lease=lease)
        assert record.state is ExecutionState.PENDING_ADMISSION
        record = store.activate_intent(
            intent.intent_id,
            drill._EXECUTION_SCOPE,
            writer_lease=lease,
        )
        assert record.state is ExecutionState.PENDING_DISPATCH
        record, claimed = store.claim_for_dispatch(
            intent.intent_id,
            drill._EXECUTION_SCOPE,
            writer_lease=lease,
        )
        assert claimed and record.state is ExecutionState.DISPATCHING
        record, event = store.record_observation_with_event(
            ProviderObservation(
                intent_id=intent.intent_id,
                state=ExecutionState.FILLED,
                provider_order_id=_SOURCE_PROVIDER_ORDER_ID,
                filled_quantity=Decimal("2"),
                average_price=Decimal("5000"),
                cumulative_commission=Decimal("0.03"),
                cumulative_commission_currency="USD",
            ),
            scope=drill._EXECUTION_SCOPE,
            writer_lease=lease,
        )
        assert record.state is ExecutionState.FILLED
        assert record.payload_sha256 == intent.fingerprint
        assert event is not None and event.event_type == "provider_observation"
        return record.payload_sha256
    finally:
        try:
            if writer is not None and lease is not None:
                assert writer.close()
        finally:
            store.close()


def _seed_gateway_events(gateway_path: Path) -> None:
    journal = GatewayEventJournal(
        gateway_path,
        clock=lambda: drill._JOURNAL_CLOCK_SECONDS,
    )
    journal.append(
        drill._JOURNAL_ACCOUNT_SCOPE,
        drill._JOURNAL_STRATEGY_SCOPE,
        "mig03-integrated-event-1",
        "position_update",
        {"sequence": 1, "quantity": "2"},
        observed_at=drill._JOURNAL_CLOCK_SECONDS,
    )
    journal.append(
        drill._JOURNAL_ACCOUNT_SCOPE,
        drill._JOURNAL_STRATEGY_SCOPE,
        "mig03-integrated-event-2",
        "position_update",
        {"sequence": 2, "quantity": "3"},
        observed_at=drill._JOURNAL_CLOCK_SECONDS,
    )
    journal.append(
        drill._JOURNAL_ACCOUNT_SCOPE,
        drill._JOURNAL_LEGACY_SCOPE,
        "mig03-integrated-unallocated-legacy-event",
        "position_update",
        {"sequence": 1, "quantity": "9"},
        observed_at=drill._JOURNAL_CLOCK_SECONDS,
    )


def _execution_projection(path: Path) -> dict[str, tuple[tuple[Any, ...], ...]]:
    connection = sqlite3.connect(path, timeout=5.0)
    try:
        connection.execute("PRAGMA query_only = ON")
        return {
            "records": tuple(
                connection.execute(
                    "SELECT scope_key, intent_id, state, provider_order_id, "
                    "filled_quantity, average_price, cumulative_commission, "
                    "unknown_reason, dispatch_attempts, permit_reference "
                    "FROM execution_records WHERE intent_id IN (?, ?) "
                    "ORDER BY intent_id",
                    (drill._INTENT_ID, drill._NEW_INTENT_ID),
                ).fetchall()
            ),
            "outbox": tuple(
                connection.execute(
                    "SELECT sequence, intent_id, event_type, state, payload_json "
                    "FROM execution_outbox WHERE intent_id IN (?, ?) ORDER BY sequence",
                    (drill._INTENT_ID, drill._NEW_INTENT_ID),
                ).fetchall()
            ),
        }
    finally:
        connection.close()


def _provider_projection(path: Path) -> tuple[tuple[Any, ...], ...]:
    connection = sqlite3.connect(path, timeout=5.0)
    try:
        connection.execute("PRAGMA query_only = ON")
        return tuple(
            connection.execute(
                "SELECT sequence, intent_id FROM provider_dispatches ORDER BY sequence"
            ).fetchall()
        )
    finally:
        connection.close()


def _gateway_snapshot_projection(path: Path) -> tuple[tuple[Any, ...], ...]:
    connection = sqlite3.connect(path, timeout=5.0)
    try:
        connection.execute("PRAGMA query_only = ON")
        return tuple(
            connection.execute(
                "SELECT account_scope, strategy_scope, snapshot_id, cursor, payload_json "
                "FROM gateway_event_snapshots ORDER BY account_scope, strategy_scope"
            ).fetchall()
        )
    finally:
        connection.close()


def _build_import_manifest(
    execution_path: Path,
    *,
    endpoint: str,
    provider_order_id: str,
) -> dict[str, Any]:
    source = inspect_execution_snapshot(execution_path, (drill._EXECUTION_SCOPE.key,))
    records = {item.intent_id: item for item in source.records}
    completed = records[drill._INTENT_ID]
    unknown = records[drill._NEW_INTENT_ID]
    account_key_source = AccountKey(
        provider=drill._EXECUTION_SCOPE.provider,
        venue="SHFE",
        environment=drill._EXECUTION_SCOPE.environment,
        account_id="mig03-integrated-source-account",
        subaccount_id=None,
    )
    account_sidecar = import_fixtures._account_sidecar(
        record_id="mig03-integrated-source-account-row",
        account_config_id=_SOURCE_ACCOUNT_CONFIG_ID,
        execution_account_ref=drill._ACCOUNT,
        account_key=account_key_source,
    )
    run_sidecar = import_fixtures._run_sidecar()
    run_sidecar.update(
        {
            "record_id": "mig03-integrated-source-run-row",
            "run_id": drill._RUN_ID,
            "execution_domain_ref": drill._DOMAIN,
            "strategy_id": drill._STRATEGY,
            "account_config_id": _SOURCE_ACCOUNT_CONFIG_ID,
        }
    )

    target_account_key = AccountKey(
        provider=drill._EXECUTION_SCOPE.provider,
        venue="SHFE",
        environment=drill._EXECUTION_SCOPE.environment,
        account_id="mig03-integrated-target-account",
        subaccount_id=None,
    )
    target_instrument_key = MarketInstrumentKey(
        provider=drill._EXECUTION_SCOPE.provider,
        venue="SHFE",
        environment=drill._EXECUTION_SCOPE.environment,
        product_type="synthetic",
        instrument_id="BTC-USDT",
        native_symbol="BTC-USDT",
    )
    target_catalog = import_fixtures._target_catalog(
        account_keys=(("target-account-row-1", target_account_key),),
        instruments=(("target-instrument-row-1", "BTC-USDT", target_instrument_key),),
        strategy_refs=(("target-strategy-row-1", drill._STRATEGY),),
        run_refs=((
            "target-run-row-1",
            _TARGET_RUN_REF,
            drill._STRATEGY,
            target_account_key.account_key_id,
        ),),
        orders=((
            "target-order-row-1",
            "mig03-integrated-target-order-ref",
            provider_order_id,
            target_account_key.account_key_id,
            drill._STRATEGY,
        ),),
    )
    # Keep the stable account/strategy references used by the existing
    # consumer identity; the typed AccountKey, run, instrument, and order
    # destinations still come from the explicit target catalog.
    target_catalog["accounts"][0]["account_ref"] = drill._ACCOUNT
    raw_target_service_config = drill._service_config(endpoint)
    target_config_fingerprint = parse_service_config(raw_target_service_config).fingerprint()
    target_catalog["service_config_fingerprint"] = target_config_fingerprint
    target_catalog.pop("sha256", None)
    target_catalog["sha256"] = import_fixtures._digest(target_catalog)

    crosswalk = [
        import_fixtures._crosswalk_sql(
            crosswalk_id="mig03-integrated-strategy",
            source_kind=SOURCE_LEGACY_STRATEGY_ID,
            source_value=drill._STRATEGY,
            record=completed,
            field="scope.strategy_id",
            target_catalog_ref="target-strategy-row-1",
        ),
        import_fixtures._crosswalk_sql(
            crosswalk_id="mig03-integrated-instrument",
            source_kind=SOURCE_BARE_SYMBOL,
            source_value="BTC-USDT",
            record=completed,
            field="instrument",
            target_catalog_ref="target-instrument-row-1",
        ),
        import_fixtures._crosswalk_sql(
            crosswalk_id="mig03-integrated-completed-order",
            source_kind=SOURCE_BARE_ORDER_ID,
            source_value=provider_order_id,
            record=completed,
            field="provider_order_id",
            target_catalog_ref="target-order-row-1",
        ),
        import_fixtures._crosswalk_sql(
            crosswalk_id="mig03-integrated-unknown-order",
            source_kind=SOURCE_BARE_ORDER_ID,
            source_value=None,
            record=unknown,
            field="provider_order_id",
            status="UNALLOCATED",
            reason_code="unknown_without_provider_id",
        ),
        import_fixtures._crosswalk_sidecar(
            crosswalk_id="mig03-integrated-run",
            source_kind=SOURCE_IN_PROCESS_RUN,
            source_value=drill._RUN_ID,
            record=run_sidecar,
            field="run_id",
            target_catalog_ref="target-run-row-1",
        ),
        import_fixtures._crosswalk_sidecar(
            crosswalk_id="mig03-integrated-account-config",
            source_kind=SOURCE_ACCOUNT_CONFIG,
            source_value=_SOURCE_ACCOUNT_CONFIG_ID,
            record=account_sidecar,
            field="account_config_id",
            target_catalog_ref="target-account-row-1",
        ),
    ]
    manifest = import_fixtures._manifest(
        execution_path,
        crosswalk=crosswalk,
        sidecar_records=(account_sidecar, run_sidecar),
        catalog=target_catalog,
        scope_keys=source.scope_keys,
    )
    manifest["migration_id"] = "mig03-integrated-offline-map-r1"
    manifest["fence_receipt_ref"] = "synthetic-old-host-normal-exit-observation"
    manifest["source"]["deployment_epoch"] = _OLD_DEPLOYMENT_EPOCH
    manifest["target"]["deployment_epoch"] = _NEW_DEPLOYMENT_EPOCH
    manifest["target"]["service_config"] = {
        "raw": raw_target_service_config,
        "fingerprint": target_config_fingerprint,
        "sha256": import_fixtures._digest(raw_target_service_config),
    }
    return manifest


def _readback_target_registration(
    store: MigrationIdentityStore,
    manifest: dict[str, Any],
) -> _MappedTargetRegistration:
    def target(source_kind: str, source_value: str) -> str:
        resolution = store.resolve(source_kind, source_value)
        assert resolution.resolved and resolution.mapping is not None
        return resolution.target_value

    account_key_id = target(SOURCE_ACCOUNT_CONFIG, _SOURCE_ACCOUNT_CONFIG_ID)
    account_rows = [
        row
        for row in manifest["target"]["catalog"]["accounts"]
        if row["account_key_id"] == account_key_id
    ]
    assert len(account_rows) == 1
    account_key = AccountKey.from_dict(account_rows[0]["account_key"])
    assert account_key.account_key_id == account_key_id
    target_service_config = copy.deepcopy(manifest["target"]["service_config"]["raw"])
    parsed_service_config = parse_service_config(target_service_config)
    assert parsed_service_config.fingerprint() == manifest["target"]["service_config"][
        "fingerprint"
    ]
    assert manifest["target"]["catalog"]["service_config_fingerprint"] == (
        parsed_service_config.fingerprint()
    )
    strategy_ref = target(SOURCE_LEGACY_STRATEGY_ID, drill._STRATEGY)
    run_ref = target(SOURCE_IN_PROCESS_RUN, drill._RUN_ID)
    booked_instrument = target(SOURCE_BARE_SYMBOL, "BTC-USDT")
    order_ref = target(SOURCE_BARE_ORDER_ID, _SOURCE_PROVIDER_ORDER_ID)
    wire_scope = WireScope(
        drill._DOMAIN,
        account_ref=account_rows[0]["account_ref"],
        strategy_ref=strategy_ref,
    )
    identity = WireIdentity(
        WireRole.STRATEGY_CLIENT,
        drill._STRATEGY_PRINCIPAL,
        strategy_ref,
    )
    return _MappedTargetRegistration(
        account_key=account_key,
        account_ref=account_rows[0]["account_ref"],
        strategy_ref=strategy_ref,
        run_ref=run_ref,
        booked_instrument=booked_instrument,
        order_ref=order_ref,
        service_config=target_service_config,
        wire_scope=wire_scope,
        identity=identity,
    )


def _new_mapped_runtime(
    endpoint: str,
    keys: dict[str, bytes],
    target: _MappedTargetRegistration,
    *,
    session_id: str,
) -> ServiceClientRuntime:
    # Resolve the service endpoint from the imported target config and build
    # the client registration from the durable map readback values.
    target_endpoint = target.service_config["communication"]["profiles"][
        drill._LISTENER_PROFILE
    ]["endpoints"]["rpc_bind"]
    raw_client_config = {
        "client_config_schema_version": 1,
        "process_role": "strategy_client",
        "registration": {"strategy_ref": target.strategy_ref},
        "clients": {
            "execution": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": [
                        {
                            "mount": "broker.orders",
                            "service_id": drill._SERVICE_ID,
                            "instance_id": drill._EXEC_INSTANCE,
                            "scope_ref": drill._ORDER_SCOPE_REF,
                            "transport_ref": drill._CLIENT_CONNECT_PROFILE,
                            "account_ref": target.account_ref,
                        },
                        {
                            "mount": "broker.order_query",
                            "service_id": drill._SERVICE_ID,
                            "instance_id": drill._EXEC_INSTANCE,
                            "scope_ref": drill._QUERY_SCOPE_REF,
                            "transport_ref": drill._CLIENT_CONNECT_PROFILE,
                            "account_ref": target.account_ref,
                        },
                    ],
                    "options": {"request_timeout_ms": 2000},
                },
            },
            "gateway": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": [
                        {
                            "mount": "store.account",
                            "service_id": drill._SERVICE_ID,
                            "instance_id": drill._GW_INSTANCE,
                            "scope_ref": drill._GATEWAY_SCOPE_REF,
                            "transport_ref": drill._CLIENT_CONNECT_PROFILE,
                            "account_ref": target.account_ref,
                        }
                    ],
                    "options": {"request_timeout_ms": 2000},
                },
            },
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                drill._CLIENT_CONNECT_PROFILE: {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_connect": target_endpoint},
                    "security_profile_ref": drill._CLIENT_SECURITY_REF,
                    "options": {
                        "request_timeout_ms": 2000,
                        "high_water_mark": 32,
                        "max_message_bytes": 65_536,
                    },
                }
            },
        },
    }
    config = parse_client_config(raw_client_config)
    return ServiceClientRuntime(
        config,
        identity=target.identity,
        scope_resolver=lambda _scope_ref: target.wire_scope,
        peer_resolver=lambda _service_id: drill._SERVICE_IDENTITY,
        transport_factory=ZmqTransportFactory(
            {
                drill._CLIENT_SECURITY_REF: ClientCurveKeys(
                    keys["server_public"],
                    keys["strategy_public"],
                    keys["strategy_secret"],
                )
            }
        ),
        requested_operations={
            "broker.orders": frozenset({"orders.submit", "orders.cancel"}),
            "broker.order_query": frozenset({"orders.query"}),
            "store.account": frozenset(
                {"account.read_after", "account.ack", "account.progress"}
            ),
        },
        client_session_id=session_id,
        run_id=target.run_ref,
    )


def _target_projection(
    runtime: ServiceClientRuntime,
    path: Path,
    *,
    account_ref: str,
) -> ServiceAccountConsumer:
    return ServiceAccountConsumer(
        runtime,
        "store.account",
        account_ref=account_ref,
        path=path,
        projection_version=_PROJECTION_VERSION,
        initial_state=_INITIAL_PROJECTION,
        expected_account_scope=drill._JOURNAL_ACCOUNT_SCOPE,
        expected_strategy_scope=drill._JOURNAL_STRATEGY_SCOPE,
        snapshot_reducer=_snapshot_reducer,
        event_reducer=_event_reducer,
        limit=1,
    )


def test_offline_import_handoff_preserves_state_and_rebuilds_expired_cursor(
    tmp_path: Path,
) -> None:
    _assert_integrated_source_origins()
    context = multiprocessing.get_context("spawn")
    endpoint = drill._endpoint()
    keys = {}
    server_public, server_secret = zmq.curve_keypair()
    strategy_public, strategy_secret = zmq.curve_keypair()
    keys.update(
        {
            "server_public": server_public,
            "server_secret": server_secret,
            "strategy_public": strategy_public,
            "strategy_secret": strategy_secret,
        }
    )
    raw_config = drill._service_config(endpoint)
    registry_path = tmp_path / "mig03-integrated-registry.sqlite3"
    execution_path = tmp_path / "mig03-integrated-execution.sqlite3"
    risk_path = tmp_path / "mig03-integrated-risk.sqlite3"
    provider_path = tmp_path / "mig03-integrated-provider.sqlite3"
    gateway_path = tmp_path / "mig03-integrated-gateway.sqlite3"
    map_path = tmp_path / "mig03-integrated-map.sqlite3"
    projection_path = tmp_path / "mig03-integrated-consumer.sqlite3"
    _seed_gateway_events(gateway_path)
    completed_arguments = drill._submit_arguments(
        drill._INTENT_ID,
        "mig03-integrated-completed-fill-signal",
        quantity="2",
        price="5000",
    )
    completed_payload_sha256 = _seed_preexisting_completed_fill(
        execution_path,
        completed_arguments,
    )

    hosts: list[dict[str, Any]] = []
    refusal_records: list[dict[str, Any]] = []
    runtimes: list[ServiceClientRuntime] = []
    consumers: list[ServiceAccountConsumer] = []
    forced: list[str] = []
    cleanup_failures: list[str] = []
    try:
        old_host = drill._start_host_process(
            context,
            _migration_host_process,
            (
                raw_config,
                keys,
                _OLD_DEPLOYMENT_EPOCH,
                str(registry_path),
                str(execution_path),
                str(risk_path),
                str(provider_path),
                str(gateway_path),
            ),
            label="mig03-integrated-old-host",
        )
        hosts.append(old_host)
        ready_old = drill._receive_host_message(old_host)
        assert ready_old[0] == "ready" and ready_old[1] == endpoint
        assert ready_old[4] == _OLD_DEPLOYMENT_EPOCH
        old_service_epoch = ready_old[2]

        old_runtime = drill._new_runtime(
            endpoint,
            keys,
            session_id="mig03-integrated-source-session",
        )
        runtimes.append(old_runtime)
        assert old_runtime.start().degraded_count == 0
        old_registration_ids = drill._registration_ids(old_runtime)
        old_query_binding = drill._binding_of(old_runtime, "broker.order_query")
        assert "orders.query" in old_query_binding.operations
        old_identity = old_runtime.identity
        assert {epoch for _, epoch in old_registration_ids.values()} == {
            old_service_epoch
        }

        completed = drill._execution_client(old_runtime).submit(**completed_arguments)
        assert completed.state == "FILLED"
        assert completed.filled_quantity == Decimal("2")
        assert completed.average_price == Decimal("5000")
        assert completed.cumulative_commission == Decimal("0.03")
        completed_store = drill.SqliteExecutionStore(execution_path)
        try:
            persisted_completed = completed_store.get(
                drill._INTENT_ID,
                scope=drill._EXECUTION_SCOPE,
            )
            assert persisted_completed is not None
            assert persisted_completed.payload_sha256 == completed_payload_sha256
        finally:
            completed_store.close()
        unknown = drill._execution_client(old_runtime).submit(
            **drill._submit_arguments(
                drill._NEW_INTENT_ID,
                "mig03-integrated-unknown-signal",
                quantity="2",
                price="50000",
            )
        )
        assert unknown.state == "UNKNOWN" and unknown.dispatch_attempts == 1
        assert unknown.permit_reference

        consumer = _target_projection(
            old_runtime,
            projection_path,
            account_ref=drill._ACCOUNT,
        )
        consumers.append(consumer)
        first_page = consumer.poll()
        assert first_page.stream_epoch and first_page.cursor == 1
        assert first_page.projection == {
            "last_sequence": 1,
            "event_ids": ("mig03-integrated-event-1",),
        }
        offered_second = old_runtime.call(
            "store.account",
            "account.read_after",
            {
                "cursor": 1,
                "stream_epoch": first_page.stream_epoch,
                "limit": 1,
                "track_delivery": True,
            },
        )
        assert [event["event_id"] for event in offered_second["events"]] == [
            "mig03-integrated-event-2"
        ]
        assert offered_second["consumer_progress"] == {
            "stream_epoch": first_page.stream_epoch,
            "offered_cursor": 2,
            "acknowledged_cursor": 1,
        }
        assert consumer.checkpoint().cursor == 1
        consumer.close()
        consumers.remove(consumer)

        old_facts = drill._host_command(old_host, "facts")[1]
        assert old_facts["provider_dispatch_counts"] == {"intent_1": 0, "intent_2": 1}
        unknown_risk = old_facts["risk_rows_intent_2"]
        assert len(unknown_risk["reservations"]) == 1
        assert unknown_risk["reservations"][0][2] == "active"
        dispatch_freeze = f"dispatch-inflight:{drill._NEW_INTENT_ID}"
        assert unknown_risk["freezes"] == (
            (dispatch_freeze, dispatch_freeze, 1),
        )
        assert dispatch_freeze in old_facts["risk_snapshot"]["active_freeze_reasons"]
        assert old_facts["risk_snapshot"]["increase_count"] == 1
        assert old_facts["risk_snapshot"]["increase_notional"] == Decimal("100000")
        execution_before = _execution_projection(execution_path)
        provider_before = _provider_projection(provider_path)
        risk_before = drill._sqlite_rows(str(risk_path))
        source_records = {
            row[1]: row for row in execution_before["records"]
        }
        assert set(source_records) == {drill._INTENT_ID, drill._NEW_INTENT_ID}
        completed_projection = source_records[drill._INTENT_ID]
        assert completed_projection[0] == drill._EXECUTION_SCOPE.key
        assert completed_projection[2:7] == (
            "FILLED",
            _SOURCE_PROVIDER_ORDER_ID,
            "2",
            "5000",
            "0.03",
        )
        unknown_projection = source_records[drill._NEW_INTENT_ID]
        assert unknown_projection[0] == drill._EXECUTION_SCOPE.key
        assert unknown_projection[2:4] == ("UNKNOWN", None)
        assert unknown_projection[8] == 1
        assert unknown_projection[9] == unknown.permit_reference
        assert provider_before == ((1, drill._NEW_INTENT_ID),)

        old_client_ids = old_registration_ids
        old_runtime.close()
        assert old_runtime.cleanup_failures == 0
        runtimes.remove(old_runtime)
        closed_old = drill._close_host_normally(old_host)
        assert closed_old[1] == "closed" and closed_old[2] == old_service_epoch

        # The migration manifest records this local process observation by
        # reference only; it is not proof of an external provider fence.
        manifest = _build_import_manifest(
            execution_path,
            endpoint=endpoint,
            provider_order_id=_SOURCE_PROVIDER_ORDER_ID,
        )
        source_projection_before_import = _execution_projection(execution_path)
        assert source_projection_before_import == execution_before
        source_risk_before_import = drill._sqlite_rows(str(risk_path))
        source_gateway_before_import = drill._journal_projection(str(gateway_path))

        invalid_manifest = copy.deepcopy(manifest)
        invalid_manifest["source"]["execution"]["snapshot_sha256"] = "0" * 64
        imported = None
        with MigrationIdentityStore(map_path) as map_store:
            with pytest.raises(MigrationImportError) as stale_cut:
                apply_migration_import(invalid_manifest, execution_path, map_store)
            assert stale_cut.value.code == CODE_SOURCE_DRIFT
            assert map_store.registration_count() == 0
            assert _execution_projection(execution_path) == source_projection_before_import
            assert drill._sqlite_rows(str(risk_path)) == source_risk_before_import
            assert drill._journal_projection(str(gateway_path)) == source_gateway_before_import

            imported = apply_migration_import(manifest, execution_path, map_store)
            assert len(imported.mappings) == 5
            assert len(imported.plan.unallocated) == 1
            assert imported.plan.unallocated[0].source_kind == SOURCE_BARE_ORDER_ID
            assert imported.plan.unallocated[0].source_value is None
            assert imported.plan.unallocated[0].reason_code == "unknown_without_provider_id"
            assert map_store.registration_count() == 5
        assert imported is not None

        # Reopen the map DB: destination bindings are built from durable
        # readback, not from the in-memory import plan or source identities.
        with MigrationIdentityStore(map_path) as map_readback:
            durable_order = map_readback.resolve(
                SOURCE_BARE_ORDER_ID,
                _SOURCE_PROVIDER_ORDER_ID,
            )
            assert durable_order.resolved and durable_order.mapping is not None
            assert durable_order.mapping.note is not None
            durable_provenance = json.loads(durable_order.mapping.note)
            assert durable_provenance["manifest_sha256"] == imported.plan.manifest_sha256
            assert durable_provenance["source_snapshot_sha256"] == (
                imported.plan.source_snapshot_sha256
            )
            assert durable_provenance["target_catalog_sha256"] == (
                imported.plan.target_catalog_sha256
            )
            assert durable_provenance["target_config_fingerprint"] == manifest["target"][
                "service_config"
            ]["fingerprint"]
            target_registration = _readback_target_registration(
                map_readback,
                manifest,
            )
            assert target_registration.account_key.account_id == "mig03-integrated-target-account"
            assert target_registration.account_ref == drill._ACCOUNT
            assert target_registration.strategy_ref == drill._STRATEGY
            assert target_registration.run_ref == _TARGET_RUN_REF
            assert target_registration.booked_instrument == "BTC-USDT"
            assert target_registration.order_ref == "mig03-integrated-target-order-ref"
            unknown_mapping = map_readback.resolve(
                SOURCE_BARE_ORDER_ID,
                "mig03-integrated-intentionally-absent-provider-id",
            )
            assert not unknown_mapping.resolved
            assert unknown_mapping.pending_reconciliation

        # GW03's accepted expired-cursor path: compact through cursor 2 while
        # this consumer's durable local cursor remains at 1, then append the
        # contiguous tail. The migration itself preserves the valid stream
        # epoch; the consumer rebuilds its projection from the source snapshot.
        journal = GatewayEventJournal(
            gateway_path,
            clock=lambda: drill._JOURNAL_CLOCK_SECONDS,
        )
        captured = journal.capture_snapshot(
            drill._JOURNAL_ACCOUNT_SCOPE,
            drill._JOURNAL_STRATEGY_SCOPE,
            "mig03-integrated-snapshot-through-two",
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
            "mig03-integrated-event-3",
            "position_update",
            {"sequence": 3, "quantity": "5"},
            observed_at=drill._JOURNAL_CLOCK_SECONDS,
        )
        assert tail.sequence == 3
        assert _gateway_snapshot_projection(gateway_path) == (
            (
                drill._JOURNAL_ACCOUNT_SCOPE,
                drill._JOURNAL_STRATEGY_SCOPE,
                "mig03-integrated-snapshot-through-two",
                2,
                json.dumps(
                    {
                        "event_ids": [
                            "mig03-integrated-event-1",
                            "mig03-integrated-event-2",
                        ],
                        "last_sequence": 2,
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            ),
        )

        cutover = arm_migration_cutover(
            registry_path,
            source_epoch=_OLD_DEPLOYMENT_EPOCH,
            new_epoch=_NEW_DEPLOYMENT_EPOCH,
            migration_id="mig03-integrated-cutover-r1",
        )
        assert cutover.status == "COMMITTED"
        assert read_migration_marker(registry_path) == cutover
        host_new = drill._start_host_process(
            context,
            _migration_host_process,
            (
                target_registration.service_config,
                keys,
                _NEW_DEPLOYMENT_EPOCH,
                str(registry_path),
                str(execution_path),
                str(risk_path),
                str(provider_path),
                str(gateway_path),
            ),
            label="mig03-integrated-new-host",
        )
        hosts.append(host_new)
        ready_new = drill._receive_host_message(host_new)
        assert ready_new[0] == "ready" and ready_new[1] == endpoint
        assert ready_new[4] == _NEW_DEPLOYMENT_EPOCH
        new_service_epoch = ready_new[2]
        assert new_service_epoch != _NEW_DEPLOYMENT_EPOCH
        assert read_migration_marker(registry_path).status == "COMMITTED"
        new_facts_before_registration = drill._host_command(host_new, "facts")[1]
        assert drill._business_projection(new_facts_before_registration) == drill._business_projection(
            old_facts
        )
        assert _execution_projection(execution_path) == execution_before
        assert _provider_projection(provider_path) == provider_before
        assert drill._sqlite_rows(str(risk_path)) == risk_before
        assert drill._journal_projection(str(gateway_path))["streams"] == source_gateway_before_import[
            "streams"
        ]

        # The old registration tuple is retained as a stale request, while
        # the old runtime itself was normally closed before the target start.
        stale_transport = ZmqTransportFactory(
            {
                drill._CLIENT_SECURITY_REF: ClientCurveKeys(
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
            stale_reply = drill._direct_request(
                stale_transport,
                identity=old_identity,
                scope=old_query_binding.scope,
                operation="orders.query",
                binding=old_query_binding,
                arguments={"intent_id": drill._INTENT_ID},
            )
            assert stale_reply.payload["ok"] is False
            assert stale_reply.payload["code"] == "STALE_EPOCH"
        finally:
            stale_transport.close()

        target_runtime = _new_mapped_runtime(
            endpoint,
            keys,
            target_registration,
            session_id="mig03-integrated-target-session",
        )
        runtimes.append(target_runtime)
        assert target_runtime.start().degraded_count == 0
        new_registration_ids = drill._registration_ids(target_runtime)
        assert {epoch for _, epoch in new_registration_ids.values()} == {
            new_service_epoch
        }
        assert all(
            new_registration_ids[mount][0] != old_client_ids[mount][0]
            for mount in old_client_ids
        )
        target_account_binding = drill._binding_of(target_runtime, "store.account")
        assert "account.read_after" in target_account_binding.operations
        assert target_account_binding.scope.account_ref == target_registration.account_ref
        assert target_account_binding.scope.strategy_ref == target_registration.strategy_ref
        assert target_account_binding.scope.run_id == target_registration.run_ref
        progress_before_rebuild = target_runtime.call(
            "store.account", "account.progress", {}
        )["consumer_progress"]
        assert progress_before_rebuild == {
            "stream_epoch": first_page.stream_epoch,
            "offered_cursor": 2,
            "acknowledged_cursor": 1,
        }

        # This is the first new-deployment operation with a durable cursor
        # effect. The registry must seal before the handler offers sequence 3.
        # A later consumer read from its expired cursor is recoverable only
        # because the journal stream epoch remains unchanged.
        tracked_offer = target_runtime.call(
            "store.account",
            "account.read_after",
            {
                "cursor": 2,
                "stream_epoch": first_page.stream_epoch,
                "limit": 1,
                "track_delivery": True,
            },
        )
        assert [event["event_id"] for event in tracked_offer["events"]] == [
            "mig03-integrated-event-3"
        ]
        assert tracked_offer["consumer_progress"] == {
            "stream_epoch": first_page.stream_epoch,
            "offered_cursor": 3,
            "acknowledged_cursor": 1,
        }

        sealed = read_migration_marker(registry_path)
        assert sealed is not None and sealed.sealed
        assert sealed.destination_epoch == _NEW_DEPLOYMENT_EPOCH
        assert sealed.first_write_fact_kind == "cursor"
        assert sealed.migration_id == cutover.migration_id

        # An epoch mismatch is not a cursor-expiry recovery case. The service
        # rejects it without changing the durable offered/acknowledged row.
        mismatch_transport = ZmqTransportFactory(
            {
                drill._CLIENT_SECURITY_REF: ClientCurveKeys(
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
                identity=target_runtime.identity,
                scope=target_account_binding.scope,
                operation="account.read_after",
                binding=target_account_binding,
                arguments={
                    "cursor": 2,
                    "stream_epoch": "mig03-integrated-wrong-stream-epoch",
                    "limit": 1,
                    "track_delivery": True,
                },
            )
            assert mismatch_reply.payload["ok"] is False
            assert mismatch_reply.payload["code"] == "HANDLER_FAILED"
        finally:
            mismatch_transport.close()
        assert target_runtime.call(
            "store.account", "account.progress", {}
        )["consumer_progress"] == tracked_offer["consumer_progress"]

        # The local projection still has cursor 1. Since only same-epoch
        # cursor expiry is recoverable, this read rebuilds from the compacted
        # snapshot through 2 and contiguous tail 3, then acknowledges 3.
        migrated_consumer = _target_projection(
            target_runtime,
            projection_path,
            account_ref=target_registration.account_ref,
        )
        consumers.append(migrated_consumer)

        # The first tracked read is already a write-attempt barrier: an old
        # deployment cannot start and the cutover cannot be rolled back.
        marker_before_reverse_attempt = read_migration_marker(registry_path)
        assert marker_before_reverse_attempt == sealed
        assert marker_before_reverse_attempt is not None
        assert marker_before_reverse_attempt.accepted_reverse_epoch is None
        refused_old = drill._start_host_process(
            context,
            _migration_refusal_process,
            (raw_config, keys, str(registry_path), _OLD_DEPLOYMENT_EPOCH),
            label="mig03-integrated-old-epoch-refusal",
        )
        refusal_records.append(refused_old)
        refusal = drill._receive_host_message(refused_old)
        assert refusal[0] == "refused"
        assert refusal[1] == REFUSAL_REVERSE_MIGRATION_REFUSED
        assert refusal[4] == ()
        assert read_migration_marker(registry_path) == marker_before_reverse_attempt
        refusal_process = refused_old["process"]
        refusal_process.join(15)
        assert not refusal_process.is_alive() and refusal_process.exitcode == 0
        refused_old["normal_exit_code"] = refusal_process.exitcode
        refused_old["closed"] = True
        refused_old["pipe"].close()
        refusal_process.close()
        with pytest.raises(MigrationGateError) as rollback_refused:
            rollback_migration_cutover(
                registry_path,
                deployment_epoch=_NEW_DEPLOYMENT_EPOCH,
            )
        assert rollback_refused.value.code == REFUSAL_REVERSE_MIGRATION_REFUSED
        assert read_migration_marker(registry_path) == marker_before_reverse_attempt

        recovered = migrated_consumer.poll()
        assert recovered.stream_epoch == first_page.stream_epoch
        assert recovered.cursor == 3 and recovered.pending_ack is False
        assert recovered.projection == {
            "last_sequence": 3,
            "event_ids": (
                "mig03-integrated-event-1",
                "mig03-integrated-event-2",
                "mig03-integrated-event-3",
            ),
        }
        consumers.remove(migrated_consumer)
        migrated_consumer.close()

        # The cursor ACK has sealed the new deployment. Replaying the completed
        # execution identity through the map-derived destination instrument is
        # now safe and must remain idempotent; UNKNOWN stays query-only.
        replay_arguments = drill._submit_arguments(
            drill._INTENT_ID,
            "mig03-integrated-completed-fill-signal",
            quantity="2",
            price="5000",
        )
        replay_arguments["instrument"] = target_registration.booked_instrument
        replay = drill._execution_client(target_runtime).submit(**replay_arguments)
        assert replay.state == "FILLED"
        assert replay.dispatch_attempts == completed.dispatch_attempts == 1
        assert replay.provider_order_id == _SOURCE_PROVIDER_ORDER_ID
        assert target_registration.order_ref != replay.provider_order_id
        unknown_after_handoff = drill._execution_client(target_runtime).query(
            drill._NEW_INTENT_ID
        )
        assert unknown_after_handoff is not None
        assert unknown_after_handoff.state == "UNKNOWN"
        assert unknown_after_handoff.permit_reference == unknown.permit_reference
        assert unknown_after_handoff.dispatch_attempts == 1
        assert _provider_projection(provider_path) == provider_before

        final_facts = drill._host_command(host_new, "facts")[1]
        assert drill._business_projection(final_facts) == drill._business_projection(old_facts)
        assert _execution_projection(execution_path) == execution_before
        assert _provider_projection(provider_path) == provider_before
        assert drill._sqlite_rows(str(risk_path)) == risk_before
        final_journal = drill._journal_projection(str(gateway_path))
        assert final_journal["streams"] == source_gateway_before_import["streams"]
        assert final_journal["progress"] == (
            (
                drill._STRATEGY_PRINCIPAL,
                drill._JOURNAL_ACCOUNT_SCOPE,
                drill._JOURNAL_STRATEGY_SCOPE,
                first_page.stream_epoch,
                3,
                3,
            ),
        )
        assert _provider_projection(provider_path) == (
            (1, drill._NEW_INTENT_ID),
        )

        target_runtime.close()
        assert target_runtime.cleanup_failures == 0
        runtimes.remove(target_runtime)
        drill._close_host_normally(host_new)
    finally:
        active_exception = sys.exc_info()[1]
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
        if active_exception is None:
            drill._assert_no_fallback_cleanup(forced, cleanup_failures)
            assert all(runtime.cleanup_failures == 0 for runtime in runtimes)


def test_tracked_account_read_seals_only_for_exact_true_delivery_flag(
    tmp_path: Path,
) -> None:
    """The registry fences only the cursor-persisting, valid tracked-read form."""

    def run_case(
        case: str, track_delivery: Any
    ) -> tuple[dict[str, Any], dict[str, int], Path]:
        state_path = tmp_path / f"tracked-read-{case}.sqlite3"
        marker = arm_migration_cutover(
            state_path,
            source_epoch=_OLD_DEPLOYMENT_EPOCH,
            new_epoch=_NEW_DEPLOYMENT_EPOCH,
            migration_id=f"tracked-read-{case}",
        )
        assert marker.status == "COMMITTED"
        operations = frozenset({"account.read_after"})
        capability = ServiceCapability(
            "account-instance",
            "service.account_read",
            operations,
        )
        grant = ResourceGrant(
            "account-instance",
            "service.account_read",
            "account-grant-a",
            WireScope("live:venue-a", account_ref="account-a", strategy_ref="strategy-a"),
            operations,
        )
        registry = ServiceRegistry(
            state_path,
            service_id="account-service-a",
            capabilities=(capability,),
            grants={
                write_barrier_fixtures._PRINCIPAL: (
                    PrincipalGrant(write_barrier_fixtures._IDENTITY, (grant,)),
                )
            },
            policy_version="policy-v1",
            requirements_version="requirements-v1",
            migration_deployment_epoch=_NEW_DEPLOYMENT_EPOCH,
        )
        progress = {"offered_cursor": 0}
        try:
            registered = registry.handle(
                write_barrier_fixtures._PRINCIPAL,
                write_barrier_fixtures._request(
                    "service.register",
                    {
                        "registration_key": f"tracked-read-registration-{case}",
                        "client_session_id": f"tracked-read-client-{case}",
                        "instance_id": "account-instance",
                        "port": "service.account_read",
                        "scope_ref": "account-grant-a",
                        "operations": ["account.read_after"],
                    },
                    control=True,
                ),
            )
            assert registered["ok"] is True
            registration = registered["result"]

            def handler(_context: object, arguments: object) -> dict[str, Any]:
                assert isinstance(arguments, dict) or hasattr(arguments, "get")
                supplied = arguments.get("track_delivery", False)  # type: ignore[union-attr]
                if type(supplied) is not bool:
                    raise ValueError("INVALID_TRACK_DELIVERY")
                if supplied:
                    current = read_migration_marker(state_path)
                    assert current is not None and current.sealed
                    assert current.first_write_fact_kind == "cursor"
                    progress["offered_cursor"] = 3
                return {"offered_cursor": progress["offered_cursor"]}

            registry.register_handler("account-instance", "account.read_after", handler)
            reply = registry.handle(
                write_barrier_fixtures._PRINCIPAL,
                write_barrier_fixtures._request(
                    "account.read_after",
                    {
                        "registration_id": registration["registration_id"],
                        "service_epoch": registration["service_epoch"],
                        "instance_id": "account-instance",
                        "arguments": {
                            "cursor": 2,
                            "limit": 1,
                            "track_delivery": track_delivery,
                        },
                    },
                ),
            )
            return dict(reply), progress, state_path
        finally:
            registry.close()

    plain_reply, plain_progress, plain_path = run_case("plain", False)
    assert plain_reply["ok"] is True
    assert plain_progress == {"offered_cursor": 0}
    plain_marker = read_migration_marker(plain_path)
    assert plain_marker is not None and plain_marker.status == "COMMITTED"

    tracked_reply, tracked_progress, tracked_path = run_case("tracked", True)
    assert tracked_reply["ok"] is True
    assert tracked_progress == {"offered_cursor": 3}
    tracked_marker = read_migration_marker(tracked_path)
    assert tracked_marker is not None and tracked_marker.sealed
    assert tracked_marker.first_write_fact_kind == "cursor"

    invalid_reply, invalid_progress, invalid_path = run_case("invalid", 1)
    assert invalid_reply == {"ok": False, "code": "HANDLER_FAILED", "result": {}}
    assert invalid_progress == {"offered_cursor": 0}
    invalid_marker = read_migration_marker(invalid_path)
    assert invalid_marker is not None and invalid_marker.status == "COMMITTED"


def test_risk_snapshot_seals_before_persistent_reservation_expiry_query(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "risk-snapshot-query.sqlite3"
    armed = arm_migration_cutover(
        state_path,
        source_epoch=_OLD_DEPLOYMENT_EPOCH,
        new_epoch=_NEW_DEPLOYMENT_EPOCH,
        migration_id="risk-snapshot-query",
    )
    operations = frozenset({"risk.snapshot"})
    capability = ServiceCapability(
        "risk-instance",
        "service.risk_query",
        operations,
    )
    grant = ResourceGrant(
        "risk-instance",
        "service.risk_query",
        "risk-grant-a",
        WireScope("live:venue-a", account_ref="account-a", strategy_ref="strategy-a"),
        operations,
    )
    registry = ServiceRegistry(
        state_path,
        service_id="risk-service-a",
        capabilities=(capability,),
        grants={
            write_barrier_fixtures._PRINCIPAL: (
                PrincipalGrant(write_barrier_fixtures._IDENTITY, (grant,)),
            )
        },
        policy_version="policy-v1",
        requirements_version="requirements-v1",
        migration_deployment_epoch=_NEW_DEPLOYMENT_EPOCH,
    )
    observed: list[tuple[str | None, str | None]] = []
    try:
        registered = registry.handle(
            write_barrier_fixtures._PRINCIPAL,
            write_barrier_fixtures._request(
                "service.register",
                {
                    "registration_key": "risk-snapshot-registration",
                    "client_session_id": "risk-snapshot-client",
                    "instance_id": "risk-instance",
                    "port": "service.risk_query",
                    "scope_ref": "risk-grant-a",
                    "operations": ["risk.snapshot"],
                },
                control=True,
            ),
        )
        assert registered["ok"] is True
        registration = registered["result"]

        def handler(_context: object, _arguments: object) -> dict[str, bool]:
            current = read_migration_marker(state_path)
            observed.append(
                (None if current is None else current.status,
                 None if current is None else current.first_write_fact_kind)
            )
            return {"read": True}

        registry.register_handler("risk-instance", "risk.snapshot", handler)
        reply = registry.handle(
            write_barrier_fixtures._PRINCIPAL,
            write_barrier_fixtures._request(
                "risk.snapshot",
                {
                    "registration_id": registration["registration_id"],
                    "service_epoch": registration["service_epoch"],
                    "instance_id": "risk-instance",
                    "arguments": {},
                },
            ),
        )
        assert reply["ok"] is True
        assert observed == [("SEALED", "reservation")]
        sealed = read_migration_marker(state_path)
        assert sealed is not None and sealed.sealed
        assert sealed.migration_id == armed.migration_id
    finally:
        registry.close()
