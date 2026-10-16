"""Focused local tests for the MIG03 map-only offline identity importer.

All databases and typed catalog values in this file are synthetic temporary
fixtures. The importer performs actual read-only queries against a complete
Execution v23 schema created by ``SqliteExecutionStore``; no Host, provider,
account, or private deployment store is involved.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from bt_api_base.account_protocol import AccountKey
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_execution.contracts import (
    ExecutionScope,
    OrderIntent,
    ProviderObservation,
    Side,
    canonical_json,
)
from bt_api_execution.facade import ManagedExecutionFacade
from bt_api_execution.store import SqliteExecutionStore

from bt_api_py.service_config import parse_service_config
import bt_api_py.service_migration_import as migration_import
from bt_api_py.service_migration_import import (
    CODE_AMBIGUOUS_BARE_IDENTITY,
    CODE_PROVENANCE_MISMATCH,
    CODE_SOURCE_UNREADABLE,
    CODE_SOURCE_DRIFT,
    CODE_TARGET_CATALOG_MISMATCH,
    CODE_TARGET_CONFIG_MISMATCH,
    CODE_UNKNOWN_ORDER_UNRESOLVED,
    EXECUTION_PROFILE,
    IMPORT_SCHEMA,
    SIDE_CAR_SCHEMA,
    TARGET_CATALOG_SCHEMA,
    MigrationImportError,
    apply_migration_import,
    inspect_execution_snapshot,
)
from bt_api_py.service_migration_map import (
    CODE_SOURCE_ALREADY_MAPPED,
    SOURCE_ACCOUNT_CONFIG,
    SOURCE_BARE_ORDER_ID,
    SOURCE_BARE_SYMBOL,
    SOURCE_IN_PROCESS_RUN,
    SOURCE_LEGACY_STRATEGY_ID,
    MigrationIdentityStore,
    MigrationMapError,
)


_SOURCE_EPOCH = "old-deployment-20261016"
_TARGET_EPOCH = "new-deployment-20261016"
_SOURCE_ACCOUNT_REF = "legacy-account-ref-1"
_ACCOUNT_CONFIG_ID = "legacy-account-config-1"
_LEGACY_STRATEGY_ID = "legacy-strategy-1"
_PROVIDER_ORDER_ID = "provider-order-42"
_RUN_ID = "process-run-7"
_INTENT_ID = "intent-42"
_SYMBOL = "rb2310"


def _digest(value: Any) -> str:
    data = json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _service_config() -> dict[str, Any]:
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": "mig03-target-service",
        "scope_ref": "mig03-target-scope",
        "transport_ref": "mig03-target-listener",
        "registration_policy_ref": "mig03-target-registration",
        "service_requirements_ref": "mig03-target-requirements",
        "components": {
            "gateway": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": "mig03-gateway-instance",
                    "bind_ports": ["service.account_read"],
                    "options": {
                        "role": "account_session_worker",
                        "account_session_ref": "mig03-target-session",
                    },
                },
            },
            "execution": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": "mig03-execution-instance",
                    "bind_ports": ["service.order_entry", "service.order_query"],
                    "options": {"role": "account_owner"},
                },
            },
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "mig03-target-listener": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": "tcp://127.0.0.1:49173"},
                    "security_profile_ref": "mig03-target-security",
                    "options": {"high_water_mark": 32, "max_message_bytes": 65536},
                }
            },
        },
    }


def _old_account_key(
    *, account_id: str = "legacy-account-1", provider: str = "simnow", environment: str = "paper"
) -> AccountKey:
    return AccountKey(
        provider=provider,
        venue="SHFE",
        environment=environment,
        account_id=account_id,
        subaccount_id=None,
    )


def _target_account_key() -> AccountKey:
    return AccountKey(
        provider="simnow",
        venue="SHFE",
        environment="paper",
        account_id="target-account-1",
        subaccount_id=None,
    )


def _seed_execution_db(
    path: Path,
    *,
    scopes: tuple[ExecutionScope, ...] | None = None,
    provider_order_id: str | None = _PROVIDER_ORDER_ID,
    state: str | None = "ACKED",
    symbols: tuple[str, ...] | None = None,
) -> tuple[dict[str, OrderIntent], dict[str, ExecutionScope]]:
    """Create a full v23 schema and seed only finite synthetic intent rows."""

    store = SqliteExecutionStore(path)
    if scopes is None:
        scopes = (
            ExecutionScope(
                provider="simnow",
                environment="paper",
                account_ref=_SOURCE_ACCOUNT_REF,
                strategy_id=_LEGACY_STRATEGY_ID,
                trading_day="2026-10-16",
            ),
        )
    if symbols is None:
        symbols = tuple(_SYMBOL for _ in scopes)
    assert len(scopes) == len(symbols)

    generation = store.journal_source_identity()["generation"]
    connection = store._connection
    intents: dict[str, OrderIntent] = {}
    scope_by_intent: dict[str, ExecutionScope] = {}
    writer_facades: dict[str, tuple[ManagedExecutionFacade, Any]] = {}
    try:
        for index, (scope, symbol) in enumerate(zip(scopes, symbols, strict=True)):
            intent_id = _INTENT_ID if index == 0 else f"intent-{index + 42}"
            intent = OrderIntent.limit(
                intent_id=intent_id,
                scope=scope,
                signal_id=f"signal-{index + 1}",
                instrument=symbol,
                side=Side.BUY,
                quantity=Decimal("1"),
                price=Decimal("3000"),
            )
            payload_json = canonical_json(intent.to_payload())
            row_state = state or "UNKNOWN"
            row_provider_order_id = provider_order_id if row_state != "UNKNOWN" else None
            connection.execute(
                """
                INSERT INTO execution_records(
                    scope_key, intent_id, payload_sha256, payload_json, state,
                    provider_order_id, filled_quantity, unknown_reason,
                    review_required, created_at_ns, updated_at_ns
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scope.key,
                    intent_id,
                    intent.fingerprint,
                    payload_json,
                    "PENDING_DISPATCH",
                    None,
                    "0",
                    None,
                    0,
                    1_000 + index * 10,
                    1_000 + index * 10,
                ),
            )
            first_event_id = uuid.uuid4().hex
            connection.execute(
                """
                INSERT INTO execution_outbox(
                    event_id, intent_id, scope_key, event_type, state, payload_json,
                    created_at_ns, journal_incarnation_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    first_event_id,
                    intent_id,
                    scope.key,
                    "intent_recorded",
                    "PENDING_ADMISSION",
                    canonical_json({"payload_sha256": intent.fingerprint}),
                    1_001 + index * 10,
                    generation,
                ),
            )
            writer_facade_entry = writer_facades.get(scope.account_key)
            if writer_facade_entry is None:
                owner_digest = hashlib.sha256(scope.account_key.encode("utf-8")).hexdigest()[:16]
                facade = ManagedExecutionFacade(
                    store,
                    scope,
                    writer_id=f"mig03-import-fixture-{owner_digest}",
                )
                writer_lease = facade.acquire_writer_lease()
                writer_facades[scope.account_key] = (facade, writer_lease)
            else:
                writer_lease = writer_facade_entry[1]

            claimed, did_claim = store.claim_for_dispatch(
                intent_id,
                scope,
                writer_lease=writer_lease,
            )
            assert did_claim and claimed.state.value == "DISPATCHING"
            if row_state == "UNKNOWN":
                store.mark_unknown(
                    intent_id,
                    scope,
                    "interrupted_order_dispatch",
                    writer_lease=writer_lease,
                )
            else:
                assert row_state == "ACKED" and row_provider_order_id is not None
                # Exercise the production v23 event producer with a synthetic
                # normalized observation instead of fabricating its outbox row.
                store.record_observation(
                    ProviderObservation.accepted(intent_id, row_provider_order_id),
                    scope=scope,
                    writer_lease=writer_lease,
                )
            intents[intent_id] = intent
            scope_by_intent[intent_id] = scope
    except BaseException:
        for facade, _writer_lease in writer_facades.values():
            try:
                facade.close()
            except Exception:
                pass
        store.close()
        raise
    else:
        try:
            for facade, _writer_lease in writer_facades.values():
                assert facade.close()
        finally:
            store.close()
    return intents, scope_by_intent


def _rewrite_provider_observation_event(
    path: Path,
    *,
    payload_update: dict[str, Any] | None = None,
    payload_remove: tuple[str, ...] = (),
    state: str | None = None,
    scope_key: str | None = None,
) -> None:
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT sequence, payload_json FROM execution_outbox "
            "WHERE event_type = 'provider_observation'"
        ).fetchone()
        assert row is not None
        payload = json.loads(row[1])
        if payload_update is not None:
            payload.update(payload_update)
        for field in payload_remove:
            payload.pop(field, None)
        assignments = ["payload_json = ?"]
        values: list[Any] = [canonical_json(payload)]
        if state is not None:
            assignments.append("state = ?")
            values.append(state)
        if scope_key is not None:
            assignments.append("scope_key = ?")
            values.append(scope_key)
        values.append(row[0])
        connection.execute(
            "UPDATE execution_outbox SET " + ", ".join(assignments) + " WHERE sequence = ?",
            tuple(values),
        )


def _account_sidecar(
    *,
    record_id: str = "source-account-row-1",
    account_config_id: str = _ACCOUNT_CONFIG_ID,
    execution_account_ref: str = _SOURCE_ACCOUNT_REF,
    account_key: AccountKey | None = None,
) -> dict[str, Any]:
    key = account_key or _old_account_key()
    return {
        "record_id": record_id,
        "kind": "account_config",
        "account_config_id": account_config_id,
        "execution_account_ref": execution_account_ref,
        "account_key": key.to_dict(),
    }


def _run_sidecar() -> dict[str, Any]:
    return {
        "record_id": "source-run-row-1",
        "kind": "in_process_run",
        "run_id": _RUN_ID,
        "execution_domain_ref": "legacy-execution-domain-1",
        "strategy_id": _LEGACY_STRATEGY_ID,
        "account_config_id": _ACCOUNT_CONFIG_ID,
    }


def _crosswalk_sql(
    *,
    crosswalk_id: str,
    source_kind: str,
    source_value: str | None,
    record: Any,
    field: str,
    target_catalog_ref: str | None = None,
    status: str = "RESOLVED",
    reason_code: str | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "crosswalk_id": crosswalk_id,
        "status": status,
        "source_kind": source_kind,
        "source_value": source_value,
        "provenance": {
            "source_type": "execution_record",
            "scope_key": record.scope_key,
            "intent_id": record.intent_id,
            "record_sha256": record.record_sha256,
            "field": field,
        },
    }
    if status == "RESOLVED":
        assert target_catalog_ref is not None
        row["target_catalog_ref"] = target_catalog_ref
    else:
        assert reason_code is not None
        row["reason_code"] = reason_code
    return row


def _crosswalk_sidecar(
    *,
    crosswalk_id: str,
    source_kind: str,
    source_value: str,
    record: dict[str, Any],
    field: str,
    target_catalog_ref: str | None = None,
    status: str = "RESOLVED",
    reason_code: str | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "crosswalk_id": crosswalk_id,
        "status": status,
        "source_kind": source_kind,
        "source_value": source_value,
        "provenance": {
            "source_type": "identity_sidecar",
            "record_id": record["record_id"],
            "record_sha256": _digest(record),
            "field": field,
        },
    }
    if status == "RESOLVED":
        assert target_catalog_ref is not None
        row["target_catalog_ref"] = target_catalog_ref
    else:
        assert reason_code is not None
        row["reason_code"] = reason_code
    return row


def _target_catalog(
    *,
    account_keys: tuple[tuple[str, AccountKey], ...] = (("target-account-row-1", _target_account_key()),),
    instruments: tuple[tuple[str, str, MarketInstrumentKey], ...] = (
        (
            "target-instrument-row-1",
            "booked:SHFE:rb2310",
            MarketInstrumentKey(
                provider="simnow",
                venue="SHFE",
                environment="paper",
                product_type="futures",
                instrument_id="rb2310",
                native_symbol="rb2310",
            ),
        ),
    ),
    strategy_refs: tuple[tuple[str, str], ...] = (("target-strategy-row-1", "strategy-ref-1"),),
    run_refs: tuple[tuple[str, str, str, str], ...] = (
        ("target-run-row-1", "run-ref-1", "strategy-ref-1", _target_account_key().account_key_id),
    ),
    orders: tuple[tuple[str, str, str, str, str], ...] = (
        (
            "target-order-row-1",
            "order-ref-1",
            _PROVIDER_ORDER_ID,
            _target_account_key().account_key_id,
            "strategy-ref-1",
        ),
    ),
) -> dict[str, Any]:
    config_fingerprint = parse_service_config(_service_config()).fingerprint()
    raw: dict[str, Any] = {
        "schema": TARGET_CATALOG_SCHEMA,
        "service_config_fingerprint": config_fingerprint,
        "accounts": [
            {
                "catalog_ref": catalog_ref,
                "account_ref": f"target-account-ref-{index + 1}",
                "account_key": key.to_dict(),
                "account_key_id": key.account_key_id,
            }
            for index, (catalog_ref, key) in enumerate(account_keys)
        ],
        "instruments": [
            {
                "catalog_ref": catalog_ref,
                "booked_instrument": booked_instrument,
                "instrument_key": key.to_dict(),
            }
            for catalog_ref, booked_instrument, key in instruments
        ],
        "strategy_refs": [
            {"catalog_ref": catalog_ref, "strategy_ref": strategy_ref}
            for catalog_ref, strategy_ref in strategy_refs
        ],
        "run_refs": [
            {
                "catalog_ref": catalog_ref,
                "run_ref": run_ref,
                "strategy_ref": strategy_ref,
                "account_key_id": account_key_id,
            }
            for catalog_ref, run_ref, strategy_ref, account_key_id in run_refs
        ],
        "orders": [
            {
                "catalog_ref": catalog_ref,
                "order_ref": order_ref,
                "legacy_provider_order_id": legacy_provider_order_id,
                "account_key_id": account_key_id,
                "strategy_ref": strategy_ref,
            }
            for catalog_ref, order_ref, legacy_provider_order_id, account_key_id, strategy_ref in orders
        ],
    }
    raw["sha256"] = _digest(raw)
    return raw


def _manifest(
    execution_db: Path,
    *,
    crosswalk: list[dict[str, Any]],
    sidecar_records: tuple[dict[str, Any], ...] | None = None,
    catalog: dict[str, Any] | None = None,
    scope_keys: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    records = sidecar_records or (_account_sidecar(), _run_sidecar())
    sidecar: dict[str, Any] = {
        "schema": SIDE_CAR_SCHEMA,
        "records": list(records),
    }
    sidecar["sha256"] = _digest(sidecar)
    raw_config = _service_config()
    parsed_config = parse_service_config(raw_config)
    config = {
        "raw": raw_config,
        "fingerprint": parsed_config.fingerprint(),
        "sha256": _digest(raw_config),
    }
    target_catalog = catalog or _target_catalog()
    selected_scopes = scope_keys
    if selected_scopes is None:
        selected_scopes = tuple(
            sorted(
                {
                    item["provenance"]["scope_key"]
                    for item in crosswalk
                    if item["provenance"]["source_type"] == "execution_record"
                }
            )
        )
    snapshot = inspect_execution_snapshot(execution_db, selected_scopes)
    return {
        "schema": IMPORT_SCHEMA,
        "migration_id": "mig03-import-round-1",
        "fence_receipt_ref": "external-cutover-fence-receipt-r188",
        "source": {
            "profile": EXECUTION_PROFILE,
            "deployment_epoch": _SOURCE_EPOCH,
            "execution": {
                "scope_keys": list(snapshot.scope_keys),
                "cut": snapshot.cut_dict(),
                "snapshot_sha256": snapshot.snapshot_sha256,
            },
            "identity_sidecar": sidecar,
        },
        "target": {
            "deployment_epoch": _TARGET_EPOCH,
            "service_config": config,
            "catalog": target_catalog,
        },
        "crosswalk": crosswalk,
    }


def _positive_manifest(execution_db: Path) -> dict[str, Any]:
    # Use the sole seeded scope, then inspect it by its typed canonical key.
    with sqlite3.connect(execution_db) as connection:
        row = connection.execute("SELECT scope_key, intent_id FROM execution_records").fetchone()
    assert row is not None
    source = inspect_execution_snapshot(execution_db, (row[0],))
    record = next(item for item in source.records if item.intent_id == _INTENT_ID)
    sidecar_account = _account_sidecar()
    sidecar_run = _run_sidecar()
    crosswalk = [
        _crosswalk_sql(
            crosswalk_id="map-strategy-1",
            source_kind=SOURCE_LEGACY_STRATEGY_ID,
            source_value=_LEGACY_STRATEGY_ID,
            record=record,
            field="scope.strategy_id",
            target_catalog_ref="target-strategy-row-1",
        ),
        _crosswalk_sql(
            crosswalk_id="map-symbol-1",
            source_kind=SOURCE_BARE_SYMBOL,
            source_value=_SYMBOL,
            record=record,
            field="instrument",
            target_catalog_ref="target-instrument-row-1",
        ),
        _crosswalk_sql(
            crosswalk_id="map-order-1",
            source_kind=SOURCE_BARE_ORDER_ID,
            source_value=_PROVIDER_ORDER_ID,
            record=record,
            field="provider_order_id",
            target_catalog_ref="target-order-row-1",
        ),
        _crosswalk_sidecar(
            crosswalk_id="map-run-1",
            source_kind=SOURCE_IN_PROCESS_RUN,
            source_value=_RUN_ID,
            record=sidecar_run,
            field="run_id",
            target_catalog_ref="target-run-row-1",
        ),
        _crosswalk_sidecar(
            crosswalk_id="map-account-1",
            source_kind=SOURCE_ACCOUNT_CONFIG,
            source_value=_ACCOUNT_CONFIG_ID,
            record=sidecar_account,
            field="account_config_id",
            target_catalog_ref="target-account-row-1",
        ),
    ]
    return _manifest(
        execution_db,
        crosswalk=crosswalk,
        sidecar_records=(sidecar_account, sidecar_run),
        scope_keys=source.scope_keys,
    )


def test_map_only_import_reads_v23_and_atomically_registers_all_five_kinds(tmp_path: Path) -> None:
    execution_db = tmp_path / "execution.sqlite3"
    _seed_execution_db(execution_db)
    with sqlite3.connect(execution_db) as connection:
        row = connection.execute(
            "SELECT state, payload_json FROM execution_outbox "
            "WHERE event_type = 'provider_observation'"
        ).fetchone()
    assert row is not None and row[0] == "ACKED"
    observation_payload = json.loads(row[1])
    assert set(observation_payload) == {
        "provider_order_id",
        "filled_quantity",
        "average_price",
        "cumulative_commission",
        "reason_code",
        "source",
    }
    assert observation_payload == {
        "provider_order_id": _PROVIDER_ORDER_ID,
        "filled_quantity": "0",
        "average_price": None,
        "cumulative_commission": None,
        "reason_code": None,
        "source": "provider",
    }
    manifest = _positive_manifest(execution_db)
    map_path = tmp_path / "migration-map.sqlite3"
    source_bytes_before = execution_db.read_bytes()

    with MigrationIdentityStore(map_path) as store:
        first = apply_migration_import(manifest, execution_db, store)
        assert len(first.mappings) == 5
        assert store.registration_count() == 5
        assert {
            item.source_kind: (item.target_kind, item.target_value)
            for item in first.mappings
        } == {
            SOURCE_LEGACY_STRATEGY_ID: ("strategy_ref", "strategy-ref-1"),
            SOURCE_BARE_SYMBOL: ("booked_instrument", "booked:SHFE:rb2310"),
            SOURCE_BARE_ORDER_ID: ("order_ref", "order-ref-1"),
            SOURCE_IN_PROCESS_RUN: ("run_ref", "run-ref-1"),
            SOURCE_ACCOUNT_CONFIG: ("account_key", _target_account_key().account_key_id),
        }
        assert {item.recorded_epoch for item in first.mappings} == {_TARGET_EPOCH}
        for item in first.mappings:
            note = json.loads(item.note or "{}")
            assert note["schema"] == "bt-api.mig03.map-provenance/v1"
            assert note["manifest_sha256"] == first.plan.manifest_sha256
            assert note["source_snapshot_sha256"] == first.plan.source_snapshot_sha256
            assert note["fence_receipt_ref"] == "external-cutover-fence-receipt-r188"

        second = apply_migration_import(manifest, execution_db, store)
        assert second.mappings == first.mappings
        assert store.registration_count() == 5
    assert execution_db.read_bytes() == source_bytes_before


def test_execution_record_count_is_rejected_before_registering_a_batch(tmp_path: Path) -> None:
    execution_db = tmp_path / "record-count-execution.sqlite3"
    _seed_execution_db(execution_db)
    manifest = _positive_manifest(execution_db)
    with MigrationIdentityStore(tmp_path / "record-count-map.sqlite3") as store:
        with patch.object(migration_import, "_MAX_EXECUTION_RECORDS", 0):
            with pytest.raises(MigrationImportError) as error:
                apply_migration_import(manifest, execution_db, store)
        assert error.value.code == CODE_SOURCE_UNREADABLE
        assert store.registration_count() == 0


def test_selected_event_count_is_rejected_before_registering_a_batch(tmp_path: Path) -> None:
    execution_db = tmp_path / "event-count-execution.sqlite3"
    _seed_execution_db(execution_db)
    manifest = _positive_manifest(execution_db)
    with MigrationIdentityStore(tmp_path / "event-count-map.sqlite3") as store:
        with patch.object(migration_import, "_MAX_SELECTED_EVENTS", 0):
            with pytest.raises(MigrationImportError) as error:
                apply_migration_import(manifest, execution_db, store)
        assert error.value.code == CODE_SOURCE_UNREADABLE
        assert store.registration_count() == 0


def test_execution_record_payload_byte_limit_fails_before_parsing_or_batch_write(
    tmp_path: Path,
) -> None:
    execution_db = tmp_path / "record-payload-limit-execution.sqlite3"
    _seed_execution_db(execution_db)
    manifest = _positive_manifest(execution_db)
    with MigrationIdentityStore(tmp_path / "record-payload-limit-map.sqlite3") as store:
        with patch.object(migration_import, "_MAX_EXECUTION_RECORD_FIELD_BYTES", 128):
            with pytest.raises(MigrationImportError) as error:
                apply_migration_import(manifest, execution_db, store)
        assert error.value.code == CODE_SOURCE_UNREADABLE
        assert store.registration_count() == 0


def test_execution_event_payload_byte_limit_fails_before_parsing_or_batch_write(
    tmp_path: Path,
) -> None:
    execution_db = tmp_path / "event-payload-limit-execution.sqlite3"
    _seed_execution_db(execution_db)
    manifest = _positive_manifest(execution_db)
    with MigrationIdentityStore(tmp_path / "event-payload-limit-map.sqlite3") as store:
        with patch.object(migration_import, "_MAX_EXECUTION_EVENT_FIELD_BYTES", 128):
            with pytest.raises(MigrationImportError) as error:
                apply_migration_import(manifest, execution_db, store)
        assert error.value.code == CODE_SOURCE_UNREADABLE
        assert store.registration_count() == 0


@pytest.mark.parametrize(
    "payload_update,payload_remove,state,scope_key",
    [
        ({}, ("filled_quantity",), None, None),
        ({"provider_order_id": "different-order"}, (), None, None),
        ({"filled_quantity": "1e1000000000"}, (), None, None),
        ({}, (), "FILLED", None),
        ({}, (), None, "scope:" + "0" * 64),
    ],
    ids=(
        "missing-observation-field",
        "provider-id-mismatch",
        "unbounded-decimal-exponent",
        "state-mismatch",
        "scope-mismatch",
    ),
)
def test_order_crosswalk_requires_current_source_producer_evidence(
    tmp_path: Path,
    payload_update: dict[str, Any],
    payload_remove: tuple[str, ...],
    state: str | None,
    scope_key: str | None,
) -> None:
    execution_db = tmp_path / "invalid-order-observation-execution.sqlite3"
    _seed_execution_db(execution_db)
    _rewrite_provider_observation_event(
        execution_db,
        payload_update=payload_update,
        payload_remove=payload_remove,
        state=state,
        scope_key=scope_key,
    )
    manifest = _positive_manifest(execution_db)
    with MigrationIdentityStore(tmp_path / "invalid-order-observation-map.sqlite3") as store:
        with pytest.raises(MigrationImportError) as error:
            apply_migration_import(manifest, execution_db, store)
        assert error.value.code == CODE_UNKNOWN_ORDER_UNRESOLVED
        assert store.registration_count() == 0


def test_unknown_without_provider_id_stays_unallocated_and_cannot_be_resolved(
    tmp_path: Path,
) -> None:
    execution_db = tmp_path / "unknown-execution.sqlite3"
    _seed_execution_db(execution_db, provider_order_id=None, state="UNKNOWN")
    with sqlite3.connect(execution_db) as connection:
        scope_key, intent_id = connection.execute(
            "SELECT scope_key, intent_id FROM execution_records"
        ).fetchone()
    snapshot = inspect_execution_snapshot(execution_db, (scope_key,))
    record = next(item for item in snapshot.records if item.intent_id == intent_id)
    account = _account_sidecar()
    unallocated = _crosswalk_sql(
        crosswalk_id="unallocated-unknown-order",
        source_kind=SOURCE_BARE_ORDER_ID,
        source_value=None,
        record=record,
        field="provider_order_id",
        status="UNALLOCATED",
        reason_code="unknown_without_provider_id",
    )
    manifest = _manifest(
        execution_db,
        crosswalk=[unallocated],
        sidecar_records=(account,),
        catalog=_target_catalog(
            account_keys=(), instruments=(), strategy_refs=(), run_refs=(), orders=()
        ),
        scope_keys=snapshot.scope_keys,
    )
    with MigrationIdentityStore(tmp_path / "unknown-map.sqlite3") as store:
        result = apply_migration_import(manifest, execution_db, store)
        assert result.mappings == ()
        assert len(result.plan.unallocated) == 1
        assert result.plan.unallocated[0].source_value is None
        assert store.registration_count() == 0

        attempted = json.loads(json.dumps(manifest))
        attempted["crosswalk"][0]["status"] = "RESOLVED"
        attempted["crosswalk"][0].pop("reason_code")
        attempted["crosswalk"][0]["source_value"] = "invented-from-intent"
        attempted["crosswalk"][0]["target_catalog_ref"] = "target-order-row-1"
        attempted["target"]["catalog"] = _target_catalog()
        with pytest.raises(MigrationImportError) as error:
            apply_migration_import(attempted, execution_db, store)
        assert error.value.code == CODE_UNKNOWN_ORDER_UNRESOLVED
        assert store.registration_count() == 0


def test_unknown_with_reported_id_can_only_be_retained_unallocated(tmp_path: Path) -> None:
    execution_db = tmp_path / "unknown-with-id-execution.sqlite3"
    _seed_execution_db(execution_db, provider_order_id=None, state="UNKNOWN")
    with sqlite3.connect(execution_db) as connection:
        connection.execute(
            "UPDATE execution_records SET provider_order_id = ?",
            (_PROVIDER_ORDER_ID,),
        )
        scope_key, intent_id = connection.execute(
            "SELECT scope_key, intent_id FROM execution_records"
        ).fetchone()
    snapshot = inspect_execution_snapshot(execution_db, (scope_key,))
    record = next(item for item in snapshot.records if item.intent_id == intent_id)
    unallocated = _crosswalk_sql(
        crosswalk_id="unallocated-unknown-order-with-id",
        source_kind=SOURCE_BARE_ORDER_ID,
        source_value=_PROVIDER_ORDER_ID,
        record=record,
        field="provider_order_id",
        status="UNALLOCATED",
        reason_code="operator_review_required",
    )
    manifest = _manifest(
        execution_db,
        crosswalk=[unallocated],
        sidecar_records=(_account_sidecar(),),
        catalog=_target_catalog(
            account_keys=(), instruments=(), strategy_refs=(), run_refs=(), orders=()
        ),
        scope_keys=snapshot.scope_keys,
    )
    with MigrationIdentityStore(tmp_path / "unknown-with-id-map.sqlite3") as store:
        result = apply_migration_import(manifest, execution_db, store)
        assert result.mappings == ()
        assert len(result.plan.unallocated) == 1
        assert result.plan.unallocated[0].source_value == _PROVIDER_ORDER_ID
        assert result.plan.unallocated[0].reason_code == "operator_review_required"
        assert store.registration_count() == 0

        attempted = json.loads(json.dumps(manifest))
        attempted["crosswalk"][0]["status"] = "RESOLVED"
        attempted["crosswalk"][0].pop("reason_code")
        attempted["crosswalk"][0]["target_catalog_ref"] = "target-order-row-1"
        attempted["target"]["catalog"] = _target_catalog()
        with pytest.raises(MigrationImportError) as error:
            apply_migration_import(attempted, execution_db, store)
        assert error.value.code == CODE_UNKNOWN_ORDER_UNRESOLVED
        assert store.registration_count() == 0


def test_bare_symbol_context_ambiguity_is_rejected_before_the_batch(tmp_path: Path) -> None:
    execution_db = tmp_path / "ambiguous-execution.sqlite3"
    scopes = (
        ExecutionScope("simnow", "paper", "legacy-account-ref-a", "strategy-a", "2026-10-16"),
        ExecutionScope("simnow", "paper", "legacy-account-ref-b", "strategy-b", "2026-10-16"),
    )
    intents, _ = _seed_execution_db(
        execution_db,
        scopes=scopes,
        provider_order_id=None,
        state="UNKNOWN",
        symbols=(_SYMBOL, _SYMBOL),
    )
    snapshot = inspect_execution_snapshot(execution_db, tuple(sorted(scope.key for scope in scopes)))
    by_intent = {item.intent_id: item for item in snapshot.records}
    crosswalk = [
        _crosswalk_sql(
            crosswalk_id=f"symbol-{index}",
            source_kind=SOURCE_BARE_SYMBOL,
            source_value=_SYMBOL,
            record=by_intent[intent_id],
            field="instrument",
            target_catalog_ref=f"instrument-row-{index}",
        )
        for index, intent_id in enumerate(intents, start=1)
    ]
    sidecar = (
        _account_sidecar(
            record_id="account-a",
            account_config_id="config-a",
            execution_account_ref="legacy-account-ref-a",
            account_key=_old_account_key(account_id="old-a"),
        ),
        _account_sidecar(
            record_id="account-b",
            account_config_id="config-b",
            execution_account_ref="legacy-account-ref-b",
            account_key=_old_account_key(account_id="old-b"),
        ),
    )
    catalog = _target_catalog(
        account_keys=(),
        instruments=(
            (
                "instrument-row-1",
                "booked:SHFE:rb2310",
                MarketInstrumentKey("simnow", "SHFE", "paper", "futures", "rb2310", "rb2310"),
            ),
            (
                "instrument-row-2",
                "booked:DCE:rb2310",
                MarketInstrumentKey("simnow", "DCE", "paper", "futures", "rb2310", "rb2310"),
            ),
        ),
        strategy_refs=(),
        run_refs=(),
        orders=(),
    )
    manifest = _manifest(
        execution_db,
        crosswalk=crosswalk,
        sidecar_records=sidecar,
        catalog=catalog,
        scope_keys=snapshot.scope_keys,
    )
    with MigrationIdentityStore(tmp_path / "ambiguous-map.sqlite3") as store:
        with pytest.raises(MigrationImportError) as error:
            apply_migration_import(manifest, execution_db, store)
        assert error.value.code == CODE_AMBIGUOUS_BARE_IDENTITY
        assert store.registration_count() == 0

        mixed_crosswalk = json.loads(json.dumps(crosswalk))
        mixed_crosswalk[1]["status"] = "UNALLOCATED"
        mixed_crosswalk[1].pop("target_catalog_ref")
        mixed_crosswalk[1]["reason_code"] = "target_not_identified"
        mixed_manifest = _manifest(
            execution_db,
            crosswalk=mixed_crosswalk,
            sidecar_records=sidecar,
            catalog=catalog,
            scope_keys=snapshot.scope_keys,
        )
        with pytest.raises(MigrationImportError) as error:
            apply_migration_import(mixed_manifest, execution_db, store)
        assert error.value.code == CODE_AMBIGUOUS_BARE_IDENTITY
        assert store.registration_count() == 0


def test_provenance_cut_and_config_drift_fail_before_any_map_write(tmp_path: Path) -> None:
    execution_db = tmp_path / "drift-execution.sqlite3"
    _seed_execution_db(execution_db)
    original = _positive_manifest(execution_db)
    variants: tuple[tuple[dict[str, Any], str], ...] = (
        (
            _with_change(original, lambda value: value["source"]["execution"].__setitem__("snapshot_sha256", "0" * 64)),
            CODE_SOURCE_DRIFT,
        ),
        (
            _with_change(original, _advance_source_cut),
            CODE_SOURCE_DRIFT,
        ),
        (
            _with_change(
                original,
                lambda value: value["crosswalk"][0]["provenance"].__setitem__("record_sha256", "0" * 64),
            ),
            CODE_PROVENANCE_MISMATCH,
        ),
        (
            _with_change(
                original,
                lambda value: value["source"]["identity_sidecar"]["records"][0].__setitem__(
                    "account_config_id", "drifted-config"
                ),
            ),
            CODE_PROVENANCE_MISMATCH,
        ),
        (
            _with_change(
                original,
                lambda value: value["target"]["catalog"]["accounts"][0].__setitem__(
                    "account_ref", "drifted-target-account"
                ),
            ),
            CODE_TARGET_CATALOG_MISMATCH,
        ),
        (
            _with_change(
                original,
                lambda value: value["target"]["service_config"]["raw"].__setitem__(
                    "service_id", "drifted-target-service"
                ),
            ),
            CODE_TARGET_CONFIG_MISMATCH,
        ),
        (_with_change(original, _change_config_but_keep_old_fingerprint), CODE_TARGET_CONFIG_MISMATCH),
    )
    map_path = tmp_path / "drift-map.sqlite3"
    with MigrationIdentityStore(map_path) as store:
        for manifest, expected_code in variants:
            with pytest.raises(MigrationImportError) as error:
                apply_migration_import(manifest, execution_db, store)
            assert error.value.code == expected_code
            assert store.registration_count() == 0


def test_store_batch_conflict_is_atomic_for_the_imported_five_kind_batch(tmp_path: Path) -> None:
    execution_db = tmp_path / "conflict-execution.sqlite3"
    _seed_execution_db(execution_db)
    manifest = _positive_manifest(execution_db)
    with MigrationIdentityStore(tmp_path / "conflict-map.sqlite3") as store:
        store.register(SOURCE_BARE_SYMBOL, _SYMBOL, "preexisting-other-instrument")
        before = store.registration_count()
        with pytest.raises(MigrationMapError) as error:
            apply_migration_import(manifest, execution_db, store)
        assert error.value.code == CODE_SOURCE_ALREADY_MAPPED
        assert store.registration_count() == before
        assert store.resolve(SOURCE_LEGACY_STRATEGY_ID, _LEGACY_STRATEGY_ID).mapping is None


def test_typed_target_key_field_omission_fails_before_registration(tmp_path: Path) -> None:
    execution_db = tmp_path / "bad-key-execution.sqlite3"
    _seed_execution_db(execution_db)
    manifest = _positive_manifest(execution_db)
    manifest["target"]["catalog"]["accounts"][0]["account_key"].pop("venue")
    catalog = manifest["target"]["catalog"]
    catalog["sha256"] = _digest({key: value for key, value in catalog.items() if key != "sha256"})

    with MigrationIdentityStore(tmp_path / "bad-key-map.sqlite3") as store:
        with pytest.raises(MigrationImportError):
            apply_migration_import(manifest, execution_db, store)
        assert store.registration_count() == 0


def test_live_execution_record_drift_fails_before_registration(tmp_path: Path) -> None:
    execution_db = tmp_path / "live-drift-execution.sqlite3"
    _seed_execution_db(execution_db)
    manifest = _positive_manifest(execution_db)
    provenance = manifest["crosswalk"][0]["provenance"]
    with sqlite3.connect(execution_db) as connection:
        connection.execute(
            "UPDATE execution_records SET updated_at_ns = updated_at_ns + 1 "
            "WHERE scope_key = ? AND intent_id = ?",
            (provenance["scope_key"], provenance["intent_id"]),
        )

    with MigrationIdentityStore(tmp_path / "live-drift-map.sqlite3") as store:
        with pytest.raises(MigrationImportError) as error:
            apply_migration_import(manifest, execution_db, store)
        assert error.value.code == CODE_SOURCE_DRIFT
        assert store.registration_count() == 0


def _with_change(
    manifest: dict[str, Any], change: Any
) -> dict[str, Any]:
    copied = json.loads(json.dumps(manifest))
    change(copied)
    return copied


def _advance_source_cut(manifest: dict[str, Any]) -> None:
    cut = manifest["source"]["execution"]["cut"]
    if cut["row_count"] == 0:
        cut.update(row_count=1, first_sequence=1, last_sequence=1, high_water_sequence=1)
        return
    cut["row_count"] += 1
    cut["last_sequence"] += 1
    cut["high_water_sequence"] += 1


def _change_config_but_keep_old_fingerprint(manifest: dict[str, Any]) -> None:
    config = manifest["target"]["service_config"]
    config["raw"]["service_id"] = "rehash-drifted-target-service"
    config["sha256"] = _digest(config["raw"])
