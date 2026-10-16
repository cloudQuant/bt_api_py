"""Bounded, offline MIG03 identity import for an Execution v23 journal.

This module imports only identity-map rows. It never opens a provider, Host,
registry, risk store, or target runtime store. Its source facts are the
read-only Execution v23 rows and outbox events named by the manifest plus an
explicit typed identity sidecar. The caller supplies a stable deployment
epoch and a fence-receipt reference; the reference is recorded as provenance
and is not independently authenticated here.

The manifest is deliberately fixed and small in scope. Its top-level fields
are ``schema``, ``migration_id``, ``fence_receipt_ref``, ``source``,
``target`` and ``crosswalk``. See :func:`prepare_migration_import` for the
field-level contract enforced by the implementation.

``source.execution`` binds sorted ``scope_keys``, an exact v23 journal
``cut``, and the logical ``snapshot_sha256`` returned by
:func:`inspect_execution_snapshot`. ``source.identity_sidecar`` is a typed,
canonical-hashed list of old account-config keys and process-run associations.
``target.service_config`` binds raw config bytes, the parsed service-config
fingerprint, and a canonical hash. ``target.catalog`` is bound to that
fingerprint and contains complete AccountKey/MarketInstrumentKey rows plus
explicit strategy, run, and order references. Each crosswalk row has one
field-level source provenance record and either a target catalog reference or
an UNALLOCATED reason. A sidecar or catalog digest binds supplied bytes; it
does not establish who authored them or prove an external fence.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from bt_api_py.service_migration_map import (
    IdentityMapping,
    IdentityRegistration,
    MigrationIdentityStore,
    SOURCE_ACCOUNT_CONFIG,
    SOURCE_BARE_ORDER_ID,
    SOURCE_BARE_SYMBOL,
    SOURCE_IN_PROCESS_RUN,
    SOURCE_LEGACY_STRATEGY_ID,
)

IMPORT_SCHEMA = "bt-api.mig03.offline-identity-import/v1"
SIDE_CAR_SCHEMA = "bt-api.mig03.typed-source-sidecar/v1"
TARGET_CATALOG_SCHEMA = "bt-api.mig03.typed-target-catalog/v1"
EXECUTION_PROFILE = "bt-api.execution-sqlite-v23"

CODE_INVALID_MANIFEST = "INVALID_MIGRATION_IMPORT_MANIFEST"
CODE_SOURCE_UNREADABLE = "MIGRATION_IMPORT_SOURCE_UNREADABLE"
CODE_SOURCE_DRIFT = "MIGRATION_IMPORT_SOURCE_DRIFT"
CODE_PROVENANCE_MISMATCH = "MIGRATION_IMPORT_PROVENANCE_MISMATCH"
CODE_TARGET_CONFIG_MISMATCH = "MIGRATION_IMPORT_TARGET_CONFIG_MISMATCH"
CODE_TARGET_CATALOG_MISMATCH = "MIGRATION_IMPORT_TARGET_CATALOG_MISMATCH"
CODE_AMBIGUOUS_BARE_IDENTITY = "MIGRATION_IMPORT_AMBIGUOUS_BARE_IDENTITY"
CODE_UNKNOWN_ORDER_UNRESOLVED = "MIGRATION_IMPORT_UNKNOWN_ORDER_UNRESOLVED"
CODE_INVALID_TARGET_KEY = "MIGRATION_IMPORT_INVALID_TARGET_KEY"

_HEX_256 = re.compile(r"^[0-9a-f]{64}$")
_JOURNAL_ID = re.compile(r"^[0-9a-f]{32}$")
_MAX_TEXT_BYTES = 256
_MAX_MANIFEST_BYTES = 4 * 1024 * 1024
_MAX_CROSSWALK_ROWS = 10_000
_MAX_SIDECAR_ROWS = 10_000
_MAX_CATALOG_ROWS = 10_000
_MAX_SCOPE_KEYS = 128
_MAX_EXECUTION_RECORDS = 20_000
_MAX_SELECTED_EVENTS = 100_000
_MAX_EXECUTION_RECORD_FIELD_BYTES = 1024 * 1024
_MAX_EXECUTION_EVENT_FIELD_BYTES = 1024 * 1024
_MAX_EXECUTION_DECIMAL_BYTES = 4096
_MAX_SELECTED_SOURCE_BYTES = 64 * 1024 * 1024
_SQLITE_BUSY_TIMEOUT_SECONDS = 5.0

_RECORD_COLUMNS = (
    "scope_key",
    "intent_id",
    "payload_sha256",
    "payload_json",
    "state",
    "provider_order_id",
    "filled_quantity",
    "average_price",
    "cumulative_commission",
    "permit_reference",
    "basic_admission_reference",
    "admission_context_sha256",
    "group_binding_json",
    "group_binding_sha256",
    "dispatch_attempts",
    "unknown_reason",
    "review_required",
    "created_at_ns",
    "updated_at_ns",
)
_OUTBOX_COLUMNS = (
    "sequence",
    "event_id",
    "intent_id",
    "scope_key",
    "event_type",
    "state",
    "payload_json",
    "created_at_ns",
    "journal_incarnation_id",
)
_META_COLUMNS = ("key", "value")
_RECORD_TEXT_FIELDS = tuple(
    field
    for field in _RECORD_COLUMNS
    if field not in {"dispatch_attempts", "review_required", "created_at_ns", "updated_at_ns"}
)
_OUTBOX_TEXT_FIELDS = tuple(
    field for field in _OUTBOX_COLUMNS if field not in {"sequence", "created_at_ns"}
)
_INTENT_FIELDS = frozenset(
    {
        "intent_id",
        "scope",
        "signal_id",
        "instrument",
        "side",
        "position_effect",
        "order_type",
        "quantity",
        "price",
        "time_in_force",
        "reduce_only",
        "metadata_version",
        "deadline_ns",
        "tags",
    }
)
_SCOPE_FIELDS = frozenset(
    {"provider", "environment", "account_ref", "strategy_id", "trading_day"}
)
_CUT_FIELDS = frozenset(
    {
        "journal_generation",
        "row_count",
        "first_sequence",
        "last_sequence",
        "high_water_sequence",
    }
)
_SOURCE_KINDS = frozenset(
    {
        SOURCE_LEGACY_STRATEGY_ID,
        SOURCE_BARE_SYMBOL,
        SOURCE_BARE_ORDER_ID,
        SOURCE_IN_PROCESS_RUN,
        SOURCE_ACCOUNT_CONFIG,
    }
)
_UNALLOCATED_REASONS = frozenset(
    {
        "ambiguous_context",
        "operator_review_required",
        "target_not_identified",
        "unknown_without_provider_id",
    }
)


class MigrationImportError(RuntimeError):
    """Finite, value-free error raised before an import batch is registered."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ExecutionRecordProvenance:
    """Stable public fields needed to author an explicit crosswalk row."""

    scope_key: str
    intent_id: str
    record_sha256: str
    provider: str
    environment: str
    account_ref: str
    strategy_id: str
    instrument: str
    state: str
    provider_order_id: str | None


@dataclass(frozen=True, slots=True)
class ExecutionSnapshotReceipt:
    """Digest and exact journal cut for the selected source execution scopes."""

    schema_version: int
    scope_keys: tuple[str, ...]
    journal_generation: str
    row_count: int
    first_sequence: int | None
    last_sequence: int | None
    high_water_sequence: int
    snapshot_sha256: str
    records: tuple[ExecutionRecordProvenance, ...]

    def cut_dict(self) -> dict[str, Any]:
        return {
            "journal_generation": self.journal_generation,
            "row_count": self.row_count,
            "first_sequence": self.first_sequence,
            "last_sequence": self.last_sequence,
            "high_water_sequence": self.high_water_sequence,
        }


@dataclass(frozen=True, slots=True)
class UnallocatedIdentity:
    """A source identity retained for reconciliation without a map write."""

    crosswalk_id: str
    source_kind: str
    source_value: str | None
    reason_code: str


@dataclass(frozen=True, slots=True)
class MigrationImportPlan:
    """Fully checked map-only batch, ready for one atomic store call."""

    migration_id: str
    source_deployment_epoch: str
    target_deployment_epoch: str
    manifest_sha256: str
    source_snapshot_sha256: str
    source_cut: tuple[str, int, int | None, int | None, int]
    target_catalog_sha256: str
    target_config_sha256: str
    registrations: tuple[IdentityRegistration, ...]
    unallocated: tuple[UnallocatedIdentity, ...]


@dataclass(frozen=True, slots=True)
class MigrationImportReceipt:
    """Result of the map-only batch call and the checks that preceded it."""

    plan: MigrationImportPlan
    mappings: tuple[IdentityMapping, ...]


@dataclass(frozen=True, slots=True)
class _ExecutionSnapshot:
    receipt: ExecutionSnapshotReceipt
    records: Mapping[tuple[str, str], dict[str, Any]]
    intents: Mapping[tuple[str, str], Any]
    events: Mapping[tuple[str, str], tuple[dict[str, Any], ...]]


def inspect_execution_snapshot(
    execution_db_path: str | os.PathLike[str], scope_keys: list[str] | tuple[str, ...]
) -> ExecutionSnapshotReceipt:
    """Read a v23 journal snapshot in SQLite read-only mode.

    ``snapshot_sha256`` covers every current execution record and outbox event
    in the explicitly selected scopes, the v23 schema identity, and the global
    contiguous outbox cut. It is a logical snapshot digest, not a signature or
    a proof that an external write fence was installed.
    """

    snapshot = _read_execution_snapshot(execution_db_path, scope_keys)
    return snapshot.receipt


def prepare_migration_import(
    manifest: Mapping[str, Any], execution_db_path: str | os.PathLike[str]
) -> MigrationImportPlan:
    """Validate the complete fixed-schema manifest and source before writes.

    Source-side crosswalk provenance may reference an Execution v23 record or
    an explicitly typed sidecar row. Resolved ``bare_order_id`` rows require
    a non-UNKNOWN record and a matching provider/reconciled observation event.
    The account and instrument targets must include complete canonical SDK
    keys. This function is read-only.
    """

    checked = _validate_manifest(manifest)
    source = checked["source"]
    execution = source["execution"]
    snapshot = _read_execution_snapshot(execution_db_path, execution["scope_keys"])
    _require_snapshot_match(execution, snapshot.receipt)
    records = snapshot.records
    intents = snapshot.intents
    events = snapshot.events

    sidecar_records = _validate_sidecar(source["identity_sidecar"])
    target = checked["target"]
    target_config_sha256, target_config_fingerprint = _validate_target_config(
        target["service_config"]
    )
    catalog = _validate_target_catalog(target["catalog"], target_config_fingerprint)
    manifest_sha256 = _digest(checked["raw_manifest"])

    crosswalk = checked["crosswalk"]
    _validate_crosswalk_scope_set(crosswalk, execution["scope_keys"])
    _validate_source_identity_contexts(crosswalk, records, intents, sidecar_records)

    resolved_accounts: dict[str, str] = {}
    verified_sources: dict[str, tuple[str, str, Any | None]] = {}
    for item in crosswalk:
        verified = _verify_crosswalk_provenance(
            item, records, intents, events, sidecar_records
        )
        verified_sources[item["crosswalk_id"]] = verified
        if item["status"] == "RESOLVED" and item["source_kind"] == SOURCE_ACCOUNT_CONFIG:
            source_record = sidecar_records[item["provenance"]["record_id"]]
            account_row = catalog["accounts_by_ref"].get(item["target_catalog_ref"])
            if account_row is None:
                raise MigrationImportError(CODE_TARGET_CATALOG_MISMATCH)
            account_ref = source_record["execution_account_ref"]
            target_account_key_id = account_row["account_key_id"]
            previous_target = resolved_accounts.get(account_ref)
            if previous_target is not None and previous_target != target_account_key_id:
                raise MigrationImportError(CODE_AMBIGUOUS_BARE_IDENTITY)
            resolved_accounts[account_ref] = target_account_key_id
    _validate_sidecar_execution_links(crosswalk, sidecar_records, intents, catalog)

    registrations: list[IdentityRegistration] = []
    unallocated: list[UnallocatedIdentity] = []
    for item in crosswalk:
        record_sha256, provenance_kind, scope = verified_sources[item["crosswalk_id"]]
        if item["status"] == "UNALLOCATED":
            unallocated.append(
                UnallocatedIdentity(
                    crosswalk_id=item["crosswalk_id"],
                    source_kind=item["source_kind"],
                    source_value=item["source_value"],
                    reason_code=item["reason_code"],
                )
            )
            continue

        target_value, target_row_sha256 = _resolve_target(item, catalog)
        if item["source_kind"] == SOURCE_BARE_ORDER_ID:
            assert scope is not None
            target_order = catalog["orders_by_ref"][item["target_catalog_ref"]]
            expected_account_key = resolved_accounts.get(scope.account_ref)
            expected_strategy = _unique_crosswalk_target(
                crosswalk, SOURCE_LEGACY_STRATEGY_ID, scope.strategy_id, catalog
            )
            if (
                expected_account_key is None
                or target_order["account_key_id"] != expected_account_key
                or expected_strategy is None
                or target_order["strategy_ref"] != expected_strategy
                or target_order["legacy_provider_order_id"] != item["source_value"]
            ):
                raise MigrationImportError(CODE_PROVENANCE_MISMATCH)
        elif item["source_kind"] == SOURCE_IN_PROCESS_RUN:
            run_record = sidecar_records[item["provenance"]["record_id"]]
            target_run = catalog["runs_by_ref"][item["target_catalog_ref"]]
            expected_strategy = _unique_crosswalk_target(
                crosswalk, SOURCE_LEGACY_STRATEGY_ID, run_record["strategy_id"], catalog
            )
            expected_account_key = _unique_account_target(
                crosswalk,
                sidecar_records,
                run_record["account_config_id"],
                catalog,
            )
            if (
                expected_strategy is None
                or expected_account_key is None
                or target_run["strategy_ref"] != expected_strategy
                or target_run["account_key_id"] != expected_account_key
            ):
                raise MigrationImportError(CODE_PROVENANCE_MISMATCH)

        note = _canonical_text(
            {
                "schema": "bt-api.mig03.map-provenance/v1",
                "migration_id": checked["migration_id"],
                "crosswalk_id": item["crosswalk_id"],
                "source_deployment_epoch": source["deployment_epoch"],
                "target_deployment_epoch": target["deployment_epoch"],
                "source_snapshot_sha256": snapshot.receipt.snapshot_sha256,
                "source_journal_cut": snapshot.receipt.cut_dict(),
                "source_row_sha256": record_sha256,
                "source_record_type": provenance_kind,
                "target_row_sha256": target_row_sha256,
                "identity_sidecar_sha256": source["identity_sidecar"]["sha256"],
                "target_catalog_sha256": target["catalog"]["sha256"],
                "target_config_sha256": target_config_sha256,
                "target_config_fingerprint": target_config_fingerprint,
                "manifest_sha256": manifest_sha256,
                "fence_receipt_ref": checked["fence_receipt_ref"],
            }
        )
        if len(note.encode("utf-8")) > 4096:
            raise MigrationImportError(CODE_INVALID_MANIFEST)
        registrations.append(
            IdentityRegistration(
                source_kind=item["source_kind"],
                source_value=item["source_value"],
                target_value=target_value,
                recorded_epoch=target["deployment_epoch"],
                note=note,
            )
        )

    return MigrationImportPlan(
        migration_id=checked["migration_id"],
        source_deployment_epoch=source["deployment_epoch"],
        target_deployment_epoch=target["deployment_epoch"],
        manifest_sha256=manifest_sha256,
        source_snapshot_sha256=snapshot.receipt.snapshot_sha256,
        source_cut=(
            snapshot.receipt.journal_generation,
            snapshot.receipt.row_count,
            snapshot.receipt.first_sequence,
            snapshot.receipt.last_sequence,
            snapshot.receipt.high_water_sequence,
        ),
        target_catalog_sha256=target["catalog"]["sha256"],
        target_config_sha256=target_config_sha256,
        registrations=tuple(registrations),
        unallocated=tuple(unallocated),
    )


def apply_migration_import(
    manifest: Mapping[str, Any],
    execution_db_path: str | os.PathLike[str],
    store: MigrationIdentityStore,
) -> MigrationImportReceipt:
    """Validate the current source cut, then issue exactly one atomic map batch.

    Existing source/target conflicts are left to ``register_batch`` so its
    transaction guarantees all-or-nothing behavior. This function changes only
    the migration identity map; it does not create runtime grants, rewrite old
    journals, schedule work, or authorize a target service.
    """

    if type(store) is not MigrationIdentityStore:
        raise MigrationImportError(CODE_INVALID_MANIFEST)
    plan = prepare_migration_import(manifest, execution_db_path)
    mappings = store.register_batch(plan.registrations)
    return MigrationImportReceipt(plan=plan, mappings=mappings)


def _read_execution_snapshot(
    execution_db_path: str | os.PathLike[str], scope_keys: list[str] | tuple[str, ...]
) -> _ExecutionSnapshot:
    normalized_scopes = _text_tuple(scope_keys, maximum=_MAX_SCOPE_KEYS, allow_empty=True)
    if len(set(normalized_scopes)) != len(normalized_scopes):
        raise MigrationImportError(CODE_INVALID_MANIFEST)
    ordered_scopes = tuple(sorted(normalized_scopes))

    connection: sqlite3.Connection | None = None
    try:
        source_path = Path(execution_db_path).expanduser().resolve(strict=True)
        if not stat.S_ISREG(source_path.stat().st_mode):
            raise MigrationImportError(CODE_SOURCE_UNREADABLE)
        connection = sqlite3.connect(
            source_path.as_uri() + "?mode=ro",
            uri=True,
            timeout=_SQLITE_BUSY_TIMEOUT_SECONDS,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute(f"PRAGMA busy_timeout = {int(_SQLITE_BUSY_TIMEOUT_SECONDS * 1000)}")
        connection.execute("BEGIN")
        _require_table_columns(connection, "execution_meta", _META_COLUMNS)
        _require_table_columns(connection, "execution_records", _RECORD_COLUMNS)
        _require_table_columns(connection, "execution_outbox", _OUTBOX_COLUMNS)

        metadata_rows = connection.execute(
            "SELECT key, value FROM execution_meta "
            "WHERE key IN ('schema_version', 'journal_incarnation_id')"
        ).fetchall()
        if any(type(row["key"]) is not str or type(row["value"]) is not str for row in metadata_rows):
            raise MigrationImportError(CODE_SOURCE_UNREADABLE)
        metadata = {row["key"]: row["value"] for row in metadata_rows}
        if metadata.get("schema_version") != "23":
            raise MigrationImportError(CODE_SOURCE_UNREADABLE)
        generation = metadata.get("journal_incarnation_id")
        if type(generation) is not str or not _JOURNAL_ID.fullmatch(generation):
            raise MigrationImportError(CODE_SOURCE_UNREADABLE)
        bounds = connection.execute(
            "SELECT COUNT(*) AS row_count, MIN(sequence) AS first_sequence, "
            "MAX(sequence) AS last_sequence FROM execution_outbox"
        ).fetchone()
        sequence = connection.execute(
            "SELECT seq FROM sqlite_sequence WHERE name = 'execution_outbox'"
        ).fetchone()
        if bounds is None:
            raise MigrationImportError(CODE_SOURCE_UNREADABLE)
        row_count = _sqlite_int(bounds["row_count"])
        first_sequence = (
            None if bounds["first_sequence"] is None else _sqlite_int(bounds["first_sequence"])
        )
        last_sequence = (
            None if bounds["last_sequence"] is None else _sqlite_int(bounds["last_sequence"])
        )
        high_water = 0 if sequence is None else _sqlite_int(sequence["seq"])
        cut = _checked_cut(generation, row_count, first_sequence, last_sequence, high_water)

        records: dict[tuple[str, str], dict[str, Any]] = {}
        intents: dict[tuple[str, str], Any] = {}
        selected_events: dict[tuple[str, str], list[dict[str, Any]]] = {}
        if ordered_scopes:
            placeholders = ",".join("?" for _ in ordered_scopes)
            record_count, record_bytes = _bounded_source_extent(
                connection, "execution_records", ordered_scopes, _RECORD_TEXT_FIELDS
            )
            if (
                record_count > _MAX_EXECUTION_RECORDS
                or record_bytes > _MAX_SELECTED_SOURCE_BYTES
            ):
                raise MigrationImportError(CODE_SOURCE_UNREADABLE)
            record_select, record_select_parameters = _bounded_select_projection(
                _RECORD_COLUMNS,
                _RECORD_TEXT_FIELDS,
                maximum_bytes=_MAX_EXECUTION_RECORD_FIELD_BYTES,
            )
            record_rows = connection.execute(
                "SELECT "
                + record_select
                + " FROM execution_records WHERE scope_key IN ("
                + placeholders
                + ") ORDER BY scope_key, intent_id LIMIT ?",
                (*record_select_parameters, *ordered_scopes, _MAX_EXECUTION_RECORDS + 1),
            ).fetchall()
            if len(record_rows) > _MAX_EXECUTION_RECORDS:
                raise MigrationImportError(CODE_SOURCE_UNREADABLE)
            from bt_api_execution.contracts import canonical_json, order_intent_from_payload
            from bt_api_execution.store import SqliteExecutionStore

            for row in record_rows:
                _validate_bounded_projection(
                    row,
                    _RECORD_TEXT_FIELDS,
                    maximum_bytes=_MAX_EXECUTION_RECORD_FIELD_BYTES,
                )
                raw = _row_dict(row, _RECORD_COLUMNS)
                try:
                    payload_json = raw["payload_json"]
                    if type(payload_json) is not str:
                        raise ValueError
                    payload = json.loads(payload_json)
                    if canonical_json(payload) != payload_json:
                        raise ValueError
                    _exact(payload, _INTENT_FIELDS)
                    _exact(payload["scope"], _SCOPE_FIELDS)
                    _validate_intent_decimal_wire(payload)
                    intent = order_intent_from_payload(payload)
                    _validate_execution_record_scalars(raw)
                    record = SqliteExecutionStore._record_from_row(row)
                    if (
                        intent.intent_id != record.intent_id
                        or intent.scope.key != record.scope_key
                        or intent.fingerprint != record.payload_sha256
                    ):
                        raise ValueError
                except Exception as error:
                    if isinstance(error, MigrationImportError):
                        raise
                    raise MigrationImportError(CODE_SOURCE_UNREADABLE) from None
                identity = (raw["scope_key"], raw["intent_id"])
                if identity in records:
                    raise MigrationImportError(CODE_SOURCE_UNREADABLE)
                records[identity] = raw
                intents[identity] = intent

            event_count, event_bytes = _bounded_source_extent(
                connection, "execution_outbox", ordered_scopes, _OUTBOX_TEXT_FIELDS
            )
            if (
                event_count > _MAX_SELECTED_EVENTS
                or event_bytes > _MAX_SELECTED_SOURCE_BYTES
            ):
                raise MigrationImportError(CODE_SOURCE_UNREADABLE)
            event_select, event_select_parameters = _bounded_select_projection(
                _OUTBOX_COLUMNS,
                _OUTBOX_TEXT_FIELDS,
                maximum_bytes=_MAX_EXECUTION_EVENT_FIELD_BYTES,
            )
            event_rows = connection.execute(
                "SELECT "
                + event_select
                + " FROM execution_outbox WHERE scope_key IN ("
                + placeholders
                + ") ORDER BY sequence LIMIT ?",
                (*event_select_parameters, *ordered_scopes, _MAX_SELECTED_EVENTS + 1),
            ).fetchall()
            if len(event_rows) > _MAX_SELECTED_EVENTS:
                raise MigrationImportError(CODE_SOURCE_UNREADABLE)
            seen_sequences: set[int] = set()
            seen_event_ids: set[str] = set()
            for row in event_rows:
                _validate_bounded_projection(
                    row,
                    _OUTBOX_TEXT_FIELDS,
                    maximum_bytes=_MAX_EXECUTION_EVENT_FIELD_BYTES,
                )
                raw = _row_dict(row, _OUTBOX_COLUMNS)
                _validate_outbox_scalars(raw)
                if raw["sequence"] in seen_sequences or raw["event_id"] in seen_event_ids:
                    raise MigrationImportError(CODE_SOURCE_UNREADABLE)
                seen_sequences.add(raw["sequence"])
                seen_event_ids.add(raw["event_id"])
                try:
                    payload = json.loads(raw["payload_json"])
                    if type(payload) is not dict:
                        raise ValueError
                    event = SqliteExecutionStore._execution_event_from_row(row)
                    if (
                        event.journal_incarnation_id != generation
                        or (event.scope_key, event.intent_id) not in records
                    ):
                        raise ValueError
                except Exception:
                    raise MigrationImportError(CODE_SOURCE_UNREADABLE) from None
                identity = (event.scope_key, event.intent_id)
                selected_events.setdefault(identity, []).append(raw)

        record_projection = [records[key] for key in sorted(records)]
        event_projection = [
            event
            for identity in sorted(selected_events)
            for event in selected_events[identity]
        ]
        snapshot_payload = {
            "profile": EXECUTION_PROFILE,
            "schema_version": 23,
            "scope_keys": list(ordered_scopes),
            "cut": cut,
            "records": record_projection,
            "events": event_projection,
        }
        public_records = tuple(
            ExecutionRecordProvenance(
                scope_key=key[0],
                intent_id=key[1],
                record_sha256=_digest(records[key]),
                provider=intents[key].scope.provider,
                environment=intents[key].scope.environment,
                account_ref=intents[key].scope.account_ref,
                strategy_id=intents[key].scope.strategy_id,
                instrument=intents[key].instrument,
                state=records[key]["state"],
                provider_order_id=records[key]["provider_order_id"],
            )
            for key in sorted(records)
        )
        receipt = ExecutionSnapshotReceipt(
            schema_version=23,
            scope_keys=ordered_scopes,
            journal_generation=generation,
            row_count=row_count,
            first_sequence=first_sequence,
            last_sequence=last_sequence,
            high_water_sequence=high_water,
            snapshot_sha256=_digest(snapshot_payload),
            records=public_records,
        )
        frozen_events = {key: tuple(value) for key, value in selected_events.items()}
        connection.execute("COMMIT")
        return _ExecutionSnapshot(receipt, records, intents, frozen_events)
    except MigrationImportError:
        if connection is not None:
            _rollback_read(connection)
        raise
    except (OSError, sqlite3.Error, ValueError, TypeError):
        if connection is not None:
            _rollback_read(connection)
        raise MigrationImportError(CODE_SOURCE_UNREADABLE) from None
    finally:
        if connection is not None:
            connection.close()


def _validate_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    try:
        raw = _plain_json_value(manifest)
        if type(raw) is not dict:
            raise ValueError
        if len(_canonical_bytes(raw)) > _MAX_MANIFEST_BYTES:
            raise ValueError
        _exact(raw, {"schema", "migration_id", "fence_receipt_ref", "source", "target", "crosswalk"})
        if raw["schema"] != IMPORT_SCHEMA:
            raise ValueError
        migration_id = _text(raw["migration_id"])
        fence_receipt_ref = _text(raw["fence_receipt_ref"])

        source = _exact(
            raw["source"],
            {"profile", "deployment_epoch", "execution", "identity_sidecar"},
        )
        if source["profile"] != EXECUTION_PROFILE:
            raise ValueError
        source["deployment_epoch"] = _text(source["deployment_epoch"])
        execution = _exact(source["execution"], {"scope_keys", "cut", "snapshot_sha256"})
        execution["scope_keys"] = list(
            _text_tuple(execution["scope_keys"], maximum=_MAX_SCOPE_KEYS, allow_empty=True)
        )
        if (
            len(set(execution["scope_keys"])) != len(execution["scope_keys"])
            or execution["scope_keys"] != sorted(execution["scope_keys"])
        ):
            raise ValueError
        execution["cut"] = _validate_cut_mapping(execution["cut"])
        execution["snapshot_sha256"] = _digest_text(execution["snapshot_sha256"])

        sidecar = _exact(source["identity_sidecar"], {"schema", "records", "sha256"})
        if sidecar["schema"] != SIDE_CAR_SCHEMA:
            raise ValueError
        sidecar["records"] = _array(sidecar["records"], maximum=_MAX_SIDECAR_ROWS)
        sidecar["sha256"] = _digest_text(sidecar["sha256"])
        if _digest({"schema": sidecar["schema"], "records": sidecar["records"]}) != sidecar["sha256"]:
            raise MigrationImportError(CODE_PROVENANCE_MISMATCH)

        target = _exact(raw["target"], {"deployment_epoch", "service_config", "catalog"})
        target["deployment_epoch"] = _text(target["deployment_epoch"])
        service_config = _exact(target["service_config"], {"raw", "fingerprint", "sha256"})
        if type(service_config["raw"]) is not dict:
            raise ValueError
        service_config["fingerprint"] = _digest_text(service_config["fingerprint"])
        service_config["sha256"] = _digest_text(service_config["sha256"])
        catalog = _exact(
            target["catalog"],
            {
                "schema",
                "service_config_fingerprint",
                "accounts",
                "instruments",
                "strategy_refs",
                "run_refs",
                "orders",
                "sha256",
            },
        )
        if catalog["schema"] != TARGET_CATALOG_SCHEMA:
            raise ValueError
        catalog["service_config_fingerprint"] = _digest_text(
            catalog["service_config_fingerprint"]
        )
        catalog["sha256"] = _digest_text(catalog["sha256"])
        for field in ("accounts", "instruments", "strategy_refs", "run_refs", "orders"):
            catalog[field] = _array(catalog[field], maximum=_MAX_CATALOG_ROWS)
        if _digest({key: value for key, value in catalog.items() if key != "sha256"}) != catalog["sha256"]:
            raise MigrationImportError(CODE_TARGET_CATALOG_MISMATCH)

        crosswalk = _array(raw["crosswalk"], maximum=_MAX_CROSSWALK_ROWS)
        if not crosswalk:
            raise ValueError
        crosswalk = _validate_crosswalk(crosswalk)
        canonical = {
            "schema": IMPORT_SCHEMA,
            "migration_id": migration_id,
            "fence_receipt_ref": fence_receipt_ref,
            "source": source,
            "target": target,
            "crosswalk": crosswalk,
        }
        if _canonical_bytes(canonical) != _canonical_bytes(raw):
            raise ValueError
        return {
            "raw_manifest": canonical,
            "migration_id": migration_id,
            "fence_receipt_ref": fence_receipt_ref,
            "source": source,
            "target": target,
            "crosswalk": crosswalk,
        }
    except MigrationImportError:
        raise
    except Exception:
        raise MigrationImportError(CODE_INVALID_MANIFEST) from None


def _validate_sidecar(sidecar: dict[str, Any]) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    account_ids: set[str] = set()
    for raw_record in sidecar["records"]:
        kind = raw_record.get("kind") if type(raw_record) is dict else None
        if kind == "account_config":
            record = _exact(
                raw_record,
                {
                    "record_id",
                    "kind",
                    "account_config_id",
                    "execution_account_ref",
                    "account_key",
                },
            )
            from bt_api_base.account_protocol import AccountKey

            account_key = AccountKey.from_dict(record["account_key"])
            record["account_key"] = account_key.to_dict()
            account_id = _text(record["account_config_id"])
            if account_id in account_ids:
                raise MigrationImportError(CODE_PROVENANCE_MISMATCH)
            account_ids.add(account_id)
            record["account_config_id"] = account_id
            record["execution_account_ref"] = _text(record["execution_account_ref"])
        elif kind == "in_process_run":
            record = _exact(
                raw_record,
                {
                    "record_id",
                    "kind",
                    "run_id",
                    "execution_domain_ref",
                    "strategy_id",
                    "account_config_id",
                },
            )
            for field in ("run_id", "execution_domain_ref", "strategy_id", "account_config_id"):
                record[field] = _text(record[field])
        else:
            raise MigrationImportError(CODE_INVALID_MANIFEST)
        record_id = _text(record["record_id"])
        if record_id in records:
            raise MigrationImportError(CODE_PROVENANCE_MISMATCH)
        records[record_id] = record
    return records


def _validate_target_config(config: dict[str, Any]) -> tuple[str, str]:
    raw = config["raw"]
    actual_sha256 = _digest(raw)
    if actual_sha256 != config["sha256"]:
        raise MigrationImportError(CODE_TARGET_CONFIG_MISMATCH)
    try:
        from bt_api_py.service_config import parse_service_config

        parsed = parse_service_config(raw)
        fingerprint = parsed.fingerprint()
    except Exception:
        raise MigrationImportError(CODE_TARGET_CONFIG_MISMATCH) from None
    if fingerprint != config["fingerprint"]:
        raise MigrationImportError(CODE_TARGET_CONFIG_MISMATCH)
    return actual_sha256, fingerprint


def _validate_target_catalog(
    catalog: dict[str, Any], config_fingerprint: str
) -> dict[str, dict[str, dict[str, Any]]]:
    if catalog["service_config_fingerprint"] != config_fingerprint:
        raise MigrationImportError(CODE_TARGET_CATALOG_MISMATCH)
    try:
        from bt_api_base.account_protocol import AccountKey
        from bt_api_base.market_protocol import MarketInstrumentKey

        result: dict[str, dict[str, dict[str, Any]]] = {
            "accounts_by_ref": {},
            "instruments_by_ref": {},
            "strategies_by_ref": {},
            "runs_by_ref": {},
            "orders_by_ref": {},
        }
        all_refs: set[str] = set()
        for raw in catalog["accounts"]:
            row = _exact(raw, {"catalog_ref", "account_ref", "account_key", "account_key_id"})
            key = AccountKey.from_dict(row["account_key"])
            if key.account_key_id != _text(row["account_key_id"]):
                raise MigrationImportError(CODE_INVALID_TARGET_KEY)
            row.update(account_key=key.to_dict(), account_key_id=key.account_key_id)
            row["account_ref"] = _text(row["account_ref"])
            _catalog_row(result["accounts_by_ref"], all_refs, row, "account_key_id")
        account_key_ids = {
            row["account_key_id"] for row in result["accounts_by_ref"].values()
        }
        for raw in catalog["instruments"]:
            row = _exact(raw, {"catalog_ref", "booked_instrument", "instrument_key"})
            key = MarketInstrumentKey.from_dict(row["instrument_key"])
            row.update(instrument_key=key.to_dict())
            _catalog_row(result["instruments_by_ref"], all_refs, row, "booked_instrument")
        for raw in catalog["strategy_refs"]:
            row = _exact(raw, {"catalog_ref", "strategy_ref"})
            row["strategy_ref"] = _text(row["strategy_ref"])
            _catalog_row(result["strategies_by_ref"], all_refs, row, "strategy_ref")
        strategy_values = {
            row["strategy_ref"] for row in result["strategies_by_ref"].values()
        }
        for raw in catalog["run_refs"]:
            row = _exact(
                raw,
                {"catalog_ref", "run_ref", "strategy_ref", "account_key_id"},
            )
            row["run_ref"] = _text(row["run_ref"])
            row["strategy_ref"] = _text(row["strategy_ref"])
            row["account_key_id"] = _text(row["account_key_id"])
            if row["strategy_ref"] not in strategy_values or row["account_key_id"] not in account_key_ids:
                raise MigrationImportError(CODE_INVALID_TARGET_KEY)
            _catalog_row(result["runs_by_ref"], all_refs, row, "run_ref")
        for raw in catalog["orders"]:
            row = _exact(
                raw,
                {
                    "catalog_ref",
                    "order_ref",
                    "legacy_provider_order_id",
                    "account_key_id",
                    "strategy_ref",
                },
            )
            row["order_ref"] = _text(row["order_ref"])
            row["legacy_provider_order_id"] = _text(row["legacy_provider_order_id"])
            row["account_key_id"] = _text(row["account_key_id"])
            row["strategy_ref"] = _text(row["strategy_ref"])
            if row["account_key_id"] not in account_key_ids or row["strategy_ref"] not in strategy_values:
                raise MigrationImportError(CODE_INVALID_TARGET_KEY)
            _catalog_row(result["orders_by_ref"], all_refs, row, "order_ref")
        for field, kind in (
            ("accounts_by_ref", SOURCE_ACCOUNT_CONFIG),
            ("instruments_by_ref", SOURCE_BARE_SYMBOL),
            ("strategies_by_ref", SOURCE_LEGACY_STRATEGY_ID),
            ("runs_by_ref", SOURCE_IN_PROCESS_RUN),
            ("orders_by_ref", SOURCE_BARE_ORDER_ID),
        ):
            values = [row[_target_field(kind)] for row in result[field].values()]
            if len(values) != len(set(values)):
                raise MigrationImportError(CODE_TARGET_CATALOG_MISMATCH)
        return result
    except MigrationImportError:
        raise
    except Exception:
        raise MigrationImportError(CODE_INVALID_TARGET_KEY) from None


def _catalog_row(
    destination: dict[str, dict[str, Any]],
    all_refs: set[str],
    row: dict[str, Any],
    value_field: str,
) -> None:
    catalog_ref = _text(row["catalog_ref"])
    if catalog_ref in all_refs:
        raise MigrationImportError(CODE_TARGET_CATALOG_MISMATCH)
    all_refs.add(catalog_ref)
    row[value_field] = _text(row[value_field])
    destination[catalog_ref] = row


def _target_field(source_kind: str) -> str:
    return {
        SOURCE_ACCOUNT_CONFIG: "account_key_id",
        SOURCE_BARE_SYMBOL: "booked_instrument",
        SOURCE_BARE_ORDER_ID: "order_ref",
        SOURCE_IN_PROCESS_RUN: "run_ref",
        SOURCE_LEGACY_STRATEGY_ID: "strategy_ref",
    }[source_kind]


def _validate_crosswalk_scope_set(
    crosswalk: list[dict[str, Any]], manifest_scope_keys: list[str]
) -> None:
    referenced_scopes: set[str] = set()
    for item in crosswalk:
        if type(item) is not dict:
            raise MigrationImportError(CODE_INVALID_MANIFEST)
        provenance = item.get("provenance")
        if type(provenance) is dict and provenance.get("source_type") == "execution_record":
            referenced_scopes.add(provenance.get("scope_key"))
    if not referenced_scopes.issubset(set(manifest_scope_keys)):
        raise MigrationImportError(CODE_PROVENANCE_MISMATCH)


def _validate_sidecar_execution_links(
    crosswalk: list[dict[str, Any]],
    sidecar: Mapping[str, dict[str, Any]],
    intents: Mapping[tuple[str, str], Any],
    catalog: Mapping[str, dict[str, dict[str, Any]]],
) -> None:
    accounts_by_execution_ref: dict[str, list[dict[str, Any]]] = {}
    for row in sidecar.values():
        if row["kind"] == "account_config":
            accounts_by_execution_ref.setdefault(row["execution_account_ref"], []).append(row)

    for item in crosswalk:
        provenance = item["provenance"]
        if provenance["source_type"] != "execution_record":
            continue
        intent = intents.get((provenance["scope_key"], provenance["intent_id"]))
        if intent is None:
            raise MigrationImportError(CODE_PROVENANCE_MISMATCH)
        account_rows = accounts_by_execution_ref.get(intent.scope.account_ref, ())
        if not account_rows:
            raise MigrationImportError(CODE_PROVENANCE_MISMATCH)
        source_account_keys = {
            _digest(account_row["account_key"]) for account_row in account_rows
        }
        if len(source_account_keys) != 1:
            raise MigrationImportError(CODE_AMBIGUOUS_BARE_IDENTITY)
        for account_row in account_rows:
            key = account_row["account_key"]
            if (
                key["provider"] != intent.scope.provider
                or key["environment"] != intent.scope.environment
            ):
                raise MigrationImportError(CODE_PROVENANCE_MISMATCH)

    for item in crosswalk:
        if item["status"] != "RESOLVED" or item["source_kind"] != SOURCE_IN_PROCESS_RUN:
            continue
        run_row = sidecar[item["provenance"]["record_id"]]
        strategy_target = _unique_crosswalk_target(
            crosswalk, SOURCE_LEGACY_STRATEGY_ID, run_row["strategy_id"], catalog
        )
        account_target = _unique_account_target(
            crosswalk, sidecar, run_row["account_config_id"], catalog
        )
        target_run = catalog["runs_by_ref"].get(item["target_catalog_ref"])
        if (
            strategy_target is None
            or account_target is None
            or target_run is None
            or target_run["strategy_ref"] != strategy_target
            or target_run["account_key_id"] != account_target
        ):
            raise MigrationImportError(CODE_PROVENANCE_MISMATCH)


def _unique_crosswalk_target(
    crosswalk: list[dict[str, Any]],
    source_kind: str,
    source_value: str,
    catalog: Mapping[str, dict[str, dict[str, Any]]],
) -> str | None:
    targets = {
        _resolve_target(item, catalog)[0]
        for item in crosswalk
        if item["status"] == "RESOLVED"
        and item["source_kind"] == source_kind
        and item["source_value"] == source_value
    }
    return next(iter(targets)) if len(targets) == 1 else None


def _unique_account_target(
    crosswalk: list[dict[str, Any]],
    sidecar: Mapping[str, dict[str, Any]],
    account_config_id: str,
    catalog: Mapping[str, dict[str, dict[str, Any]]],
) -> str | None:
    record_ids = {
        record_id
        for record_id, row in sidecar.items()
        if row["kind"] == "account_config" and row["account_config_id"] == account_config_id
    }
    targets = {
        _resolve_target(item, catalog)[0]
        for item in crosswalk
        if item["status"] == "RESOLVED"
        and item["source_kind"] == SOURCE_ACCOUNT_CONFIG
        and item["source_value"] == account_config_id
        and item["provenance"]["record_id"] in record_ids
    }
    return next(iter(targets)) if len(targets) == 1 else None


def _validate_crosswalk(value: list[Any]) -> list[dict[str, Any]]:
    checked: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for raw in value:
        if type(raw) is not dict:
            raise MigrationImportError(CODE_INVALID_MANIFEST)
        status = raw.get("status")
        if status == "RESOLVED":
            item = _exact(
                raw,
                {
                    "crosswalk_id",
                    "status",
                    "source_kind",
                    "source_value",
                    "provenance",
                    "target_catalog_ref",
                },
            )
            item["target_catalog_ref"] = _text(item["target_catalog_ref"])
            item["source_value"] = _text(item["source_value"])
        elif status == "UNALLOCATED":
            item = _exact(
                raw,
                {
                    "crosswalk_id",
                    "status",
                    "source_kind",
                    "source_value",
                    "provenance",
                    "reason_code",
                },
            )
            item["reason_code"] = _text(item["reason_code"])
            if item["reason_code"] not in _UNALLOCATED_REASONS:
                raise MigrationImportError(CODE_INVALID_MANIFEST)
            if item["source_value"] is None:
                if not (
                    item.get("source_kind") == SOURCE_BARE_ORDER_ID
                    and item["reason_code"] == "unknown_without_provider_id"
                ):
                    raise MigrationImportError(CODE_INVALID_MANIFEST)
            else:
                item["source_value"] = _text(item["source_value"])
        else:
            raise MigrationImportError(CODE_INVALID_MANIFEST)

        crosswalk_id = _text(item["crosswalk_id"])
        if crosswalk_id in seen_ids:
            raise MigrationImportError(CODE_INVALID_MANIFEST)
        seen_ids.add(crosswalk_id)
        if item["source_kind"] not in _SOURCE_KINDS:
            raise MigrationImportError(CODE_INVALID_MANIFEST)
        item["provenance"] = _validate_provenance(item["provenance"])
        if item["source_kind"] in {
            SOURCE_LEGACY_STRATEGY_ID,
            SOURCE_BARE_SYMBOL,
            SOURCE_BARE_ORDER_ID,
        }:
            if item["provenance"]["source_type"] != "execution_record":
                raise MigrationImportError(CODE_PROVENANCE_MISMATCH)
        elif item["provenance"]["source_type"] != "identity_sidecar":
            raise MigrationImportError(CODE_PROVENANCE_MISMATCH)
        checked.append(item)
    return checked


def _validate_provenance(value: Any) -> dict[str, Any]:
    if type(value) is not dict:
        raise MigrationImportError(CODE_INVALID_MANIFEST)
    source_type = value.get("source_type")
    if source_type == "execution_record":
        result = _exact(
            value,
            {"source_type", "scope_key", "intent_id", "record_sha256", "field"},
        )
        result["scope_key"] = _text(result["scope_key"])
        result["intent_id"] = _text(result["intent_id"])
        result["field"] = _text(result["field"])
    elif source_type == "identity_sidecar":
        result = _exact(
            value,
            {"source_type", "record_id", "record_sha256", "field"},
        )
        result["record_id"] = _text(result["record_id"])
        result["field"] = _text(result["field"])
    else:
        raise MigrationImportError(CODE_INVALID_MANIFEST)
    result["record_sha256"] = _digest_text(result["record_sha256"])
    return result


def _validate_source_identity_contexts(
    crosswalk: list[dict[str, Any]],
    records: Mapping[tuple[str, str], dict[str, Any]],
    intents: Mapping[tuple[str, str], Any],
    sidecar: Mapping[str, dict[str, Any]],
) -> None:
    contexts: dict[tuple[str, str], set[tuple[str, ...]]] = {}
    order_sources: dict[str, set[tuple[str, str]]] = {}
    statuses: dict[tuple[str, str], set[str]] = {}
    for item in crosswalk:
        source_value = item.get("source_value")
        if source_value is not None:
            identity = (item["source_kind"], source_value)
            statuses.setdefault(identity, set()).add(item["status"])
        if (
            source_value is None
            or item.get("source_kind")
            not in {SOURCE_LEGACY_STRATEGY_ID, SOURCE_BARE_SYMBOL, SOURCE_BARE_ORDER_ID}
        ):
            continue
        provenance = item.get("provenance", {})
        if provenance.get("source_type") != "execution_record":
            continue
        key = (provenance.get("scope_key"), provenance.get("intent_id"))
        intent = intents.get(key)
        row = records.get(key)
        if intent is None or row is None:
            continue
        context = (intent.scope.provider, intent.scope.environment, intent.scope.account_ref)
        identity = (item["source_kind"], item["source_value"])
        contexts.setdefault(identity, set()).add(context)
        if item["source_kind"] == SOURCE_BARE_ORDER_ID:
            order_sources.setdefault(source_value, set()).add(key)

    run_contexts: dict[str, set[tuple[str, ...]]] = {}
    for item in crosswalk:
        if (
            item.get("source_value") is None
            or item.get("source_kind") != SOURCE_IN_PROCESS_RUN
        ):
            continue
        provenance = item["provenance"]
        run_row = sidecar.get(provenance.get("record_id"))
        if run_row is None or run_row["kind"] != "in_process_run":
            continue
        context = (
            run_row["execution_domain_ref"],
            run_row["strategy_id"],
            run_row["account_config_id"],
        )
        run_contexts.setdefault(item["source_value"], set()).add(context)

    if any(len(values) > 1 for values in statuses.values()) or any(
        len(values) > 1 for values in contexts.values()
    ):
        raise MigrationImportError(CODE_AMBIGUOUS_BARE_IDENTITY)
    if any(len(values) > 1 for values in order_sources.values()) or any(
        len(values) > 1 for values in run_contexts.values()
    ):
        raise MigrationImportError(CODE_AMBIGUOUS_BARE_IDENTITY)


def _verify_crosswalk_provenance(
    item: dict[str, Any],
    records: Mapping[tuple[str, str], dict[str, Any]],
    intents: Mapping[tuple[str, str], Any],
    events: Mapping[tuple[str, str], tuple[dict[str, Any], ...]],
    sidecar: Mapping[str, dict[str, Any]],
) -> tuple[str, str, Any | None]:
    kind = item["source_kind"]
    provenance = item["provenance"]
    if provenance["source_type"] == "execution_record":
        key = (provenance["scope_key"], provenance["intent_id"])
        row = records.get(key)
        intent = intents.get(key)
        if row is None or intent is None or _digest(row) != provenance["record_sha256"]:
            raise MigrationImportError(CODE_PROVENANCE_MISMATCH)
        expected_field = {
            SOURCE_LEGACY_STRATEGY_ID: "scope.strategy_id",
            SOURCE_BARE_SYMBOL: "instrument",
            SOURCE_BARE_ORDER_ID: "provider_order_id",
        }.get(kind)
        if provenance["field"] != expected_field:
            raise MigrationImportError(CODE_PROVENANCE_MISMATCH)
        source_value = item["source_value"]
        if kind == SOURCE_LEGACY_STRATEGY_ID:
            actual_value = intent.scope.strategy_id
        elif kind == SOURCE_BARE_SYMBOL:
            actual_value = intent.instrument
        elif kind == SOURCE_BARE_ORDER_ID:
            actual_value = row["provider_order_id"]
            if source_value is None:
                if (
                    item["status"] != "UNALLOCATED"
                    or item.get("reason_code") != "unknown_without_provider_id"
                    or row["state"] != "UNKNOWN"
                    or actual_value is not None
                ):
                    raise MigrationImportError(CODE_UNKNOWN_ORDER_UNRESOLVED)
                return _digest(row), "execution_record", intent.scope
            if (
                actual_value is None
                or actual_value != source_value
            ):
                raise MigrationImportError(CODE_UNKNOWN_ORDER_UNRESOLVED)
            if item["status"] == "RESOLVED" and (
                row["state"] == "UNKNOWN"
                or not _confirmed_provider_id(key, actual_value, row, intent, events)
            ):
                raise MigrationImportError(CODE_UNKNOWN_ORDER_UNRESOLVED)
        else:
            raise MigrationImportError(CODE_INVALID_MANIFEST)
        if type(source_value) is not str or source_value != actual_value:
            raise MigrationImportError(CODE_PROVENANCE_MISMATCH)
        return _digest(row), "execution_record", intent.scope

    if provenance["source_type"] == "identity_sidecar":
        record_id = provenance["record_id"]
        row = sidecar.get(record_id)
        if row is None or _digest(row) != provenance["record_sha256"]:
            raise MigrationImportError(CODE_PROVENANCE_MISMATCH)
        if kind == SOURCE_ACCOUNT_CONFIG and row["kind"] == "account_config":
            expected_field, actual_value = "account_config_id", row["account_config_id"]
        elif kind == SOURCE_IN_PROCESS_RUN and row["kind"] == "in_process_run":
            expected_field, actual_value = "run_id", row["run_id"]
        else:
            raise MigrationImportError(CODE_PROVENANCE_MISMATCH)
        if provenance["field"] != expected_field or item["source_value"] != actual_value:
            raise MigrationImportError(CODE_PROVENANCE_MISMATCH)
        return _digest(row), "identity_sidecar", None
    raise MigrationImportError(CODE_PROVENANCE_MISMATCH)


def _confirmed_provider_id(
    key: tuple[str, str],
    provider_order_id: str,
    row: Mapping[str, Any],
    intent: Any,
    events: Mapping[tuple[str, str], tuple[dict[str, Any], ...]],
) -> bool:
    observations = [
        item
        for item in events.get(key, ())
        if item["event_type"] in {"provider_observation", "reconciled_observation"}
    ]
    if not observations:
        return False
    try:
        from decimal import Decimal

        from bt_api_execution.contracts import (
            ExecutionState,
            ProviderObservation,
            canonical_json,
        )
        from bt_api_execution.store import ExecutionRecord, SqliteExecutionStore

        latest = max(observations, key=lambda item: item["sequence"])
        payload_json = latest["payload_json"]
        payload = json.loads(payload_json)
        if canonical_json(payload) != payload_json:
            return False
        base_fields = {
            "provider_order_id",
            "filled_quantity",
            "average_price",
            "cumulative_commission",
            "reason_code",
            "source",
        }
        if type(payload) is not dict or frozenset(payload) not in {
            frozenset(base_fields),
            frozenset(base_fields | {"cumulative_commission_currency"}),
        }:
            return False
        if (
            "cumulative_commission_currency" in payload
            and payload["cumulative_commission_currency"] is None
        ):
            return False
        state = ExecutionState(latest["state"])
        expected_source = (
            "provider" if latest["event_type"] == "provider_observation" else "reconcile"
        )
        if (
            state is ExecutionState.UNKNOWN
            or latest["scope_key"] != key[0]
            or latest["intent_id"] != key[1]
            or row["scope_key"] != key[0]
            or row["intent_id"] != key[1]
            or row["provider_order_id"] != provider_order_id
            or row["state"] != state.value
            or type(payload["source"]) is not str
            or payload["source"] != expected_source
            or type(payload["provider_order_id"]) is not str
            or payload["provider_order_id"] != provider_order_id
            or intent.intent_id != key[1]
            or intent.scope.key != key[0]
        ):
            return False

        def decimal_field(name: str, *, optional: bool) -> Decimal | None:
            value = payload[name]
            if value is None and optional:
                return None
            if (
                type(value) is not str
                or len(value.encode("utf-8")) > _MAX_EXECUTION_DECIMAL_BYTES
                or "e" in value.lower()
            ):
                raise ValueError
            parsed = Decimal(value)
            if not parsed.is_finite() or format(parsed, "f") != value:
                raise ValueError
            return parsed

        observation = ProviderObservation(
            intent_id=key[1],
            state=state,
            provider_order_id=payload["provider_order_id"],
            filled_quantity=decimal_field("filled_quantity", optional=False),
            average_price=decimal_field("average_price", optional=True),
            reason_code=payload["reason_code"],
            cumulative_commission=decimal_field("cumulative_commission", optional=True),
            cumulative_commission_currency=payload.get("cumulative_commission_currency"),
        )
        if (
            observation.provider_order_id != row["provider_order_id"]
            or observation.filled_quantity != Decimal(row["filled_quantity"])
            or observation.average_price
            != (None if row["average_price"] is None else Decimal(row["average_price"]))
        ):
            return False
        record = ExecutionRecord(
            intent_id=row["intent_id"],
            scope_key=row["scope_key"],
            payload_sha256=row["payload_sha256"],
            state=ExecutionState(row["state"]),
            provider_order_id=row["provider_order_id"],
            filled_quantity=Decimal(row["filled_quantity"]),
            average_price=(
                None if row["average_price"] is None else Decimal(row["average_price"])
            ),
            permit_reference=row["permit_reference"],
            dispatch_attempts=row["dispatch_attempts"],
            unknown_reason=row["unknown_reason"],
            review_required=bool(row["review_required"]),
            created_at_ns=row["created_at_ns"],
            updated_at_ns=row["updated_at_ns"],
            cumulative_commission=(
                None
                if row["cumulative_commission"] is None
                else Decimal(row["cumulative_commission"])
            ),
            basic_admission_reference=row["basic_admission_reference"],
            admission_context_sha256=row["admission_context_sha256"],
        )
        SqliteExecutionStore._validate_fill_progress(record, observation, intent.quantity)
        return True
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False
    except Exception:
        return False


def _resolve_target(
    item: dict[str, Any], catalog: Mapping[str, dict[str, dict[str, Any]]]
) -> tuple[str, str]:
    field_and_group = {
        SOURCE_ACCOUNT_CONFIG: ("accounts_by_ref", "account_key_id"),
        SOURCE_BARE_SYMBOL: ("instruments_by_ref", "booked_instrument"),
        SOURCE_BARE_ORDER_ID: ("orders_by_ref", "order_ref"),
        SOURCE_IN_PROCESS_RUN: ("runs_by_ref", "run_ref"),
        SOURCE_LEGACY_STRATEGY_ID: ("strategies_by_ref", "strategy_ref"),
    }
    group, field = field_and_group[item["source_kind"]]
    row = catalog[group].get(item["target_catalog_ref"])
    if row is None:
        raise MigrationImportError(CODE_TARGET_CATALOG_MISMATCH)
    if item["source_kind"] == SOURCE_BARE_ORDER_ID and (
        row["legacy_provider_order_id"] != item["source_value"]
    ):
        raise MigrationImportError(CODE_PROVENANCE_MISMATCH)
    return row[field], _digest(row)


def _require_snapshot_match(execution: dict[str, Any], receipt: ExecutionSnapshotReceipt) -> None:
    expected_cut = execution["cut"]
    actual_cut = receipt.cut_dict()
    if (
        receipt.schema_version != 23
        or list(receipt.scope_keys) != execution["scope_keys"]
        or actual_cut != expected_cut
        or receipt.snapshot_sha256 != execution["snapshot_sha256"]
    ):
        raise MigrationImportError(CODE_SOURCE_DRIFT)


def _validate_cut_mapping(value: Any) -> dict[str, Any]:
    raw = _exact(value, _CUT_FIELDS)
    generation = raw["journal_generation"]
    if type(generation) is not str or not _JOURNAL_ID.fullmatch(generation):
        raise ValueError
    count = _integer(raw["row_count"], minimum=0)
    high_water = _integer(raw["high_water_sequence"], minimum=0)
    first = raw["first_sequence"]
    last = raw["last_sequence"]
    if count == 0:
        if first is not None or last is not None or high_water != 0:
            raise ValueError
    elif (
        type(first) is not int
        or type(last) is not int
        or first != 1
        or last != high_water
        or count != high_water
    ):
        raise ValueError
    return {
        "journal_generation": generation,
        "row_count": count,
        "first_sequence": first,
        "last_sequence": last,
        "high_water_sequence": high_water,
    }


def _checked_cut(
    generation: str,
    count: int,
    first: int | None,
    last: int | None,
    high_water: int,
) -> dict[str, Any]:
    try:
        return _validate_cut_mapping(
            {
                "journal_generation": generation,
                "row_count": count,
                "first_sequence": first,
                "last_sequence": last,
                "high_water_sequence": high_water,
            }
        )
    except Exception:
        raise MigrationImportError(CODE_SOURCE_UNREADABLE) from None


def _require_table_columns(
    connection: sqlite3.Connection, table: str, expected: tuple[str, ...]
) -> None:
    rows = connection.execute("PRAGMA table_info(" + table + ")").fetchall()
    actual = tuple(str(row["name"]) for row in rows)
    if actual != expected:
        raise MigrationImportError(CODE_SOURCE_UNREADABLE)


def _bounded_source_extent(
    connection: sqlite3.Connection,
    table: str,
    scope_keys: tuple[str, ...],
    text_fields: tuple[str, ...],
) -> tuple[int, int]:
    if table == "execution_records":
        expected_fields = _RECORD_COLUMNS
    elif table == "execution_outbox":
        expected_fields = _OUTBOX_COLUMNS
    else:
        raise MigrationImportError(CODE_SOURCE_UNREADABLE)
    if not scope_keys or any(field not in expected_fields for field in text_fields):
        raise MigrationImportError(CODE_SOURCE_UNREADABLE)
    placeholders = ",".join("?" for _ in scope_keys)
    text_size_expression = " + ".join(
        f"COALESCE(length(CAST({field} AS BLOB)), 0)" for field in text_fields
    )
    row = connection.execute(
        "SELECT COUNT(*) AS row_count, COALESCE(SUM("
        + text_size_expression
        + "), 0) AS text_bytes FROM "
        + table
        + " WHERE scope_key IN ("
        + placeholders
        + ")",
        scope_keys,
    ).fetchone()
    if row is None:
        raise MigrationImportError(CODE_SOURCE_UNREADABLE)
    return _sqlite_int(row["row_count"]), _sqlite_int(row["text_bytes"])


def _bounded_select_projection(
    columns: tuple[str, ...],
    text_fields: tuple[str, ...],
    *,
    maximum_bytes: int,
) -> tuple[str, tuple[int, ...]]:
    if any(field not in columns for field in text_fields):
        raise MigrationImportError(CODE_SOURCE_UNREADABLE)
    projection: list[str] = []
    parameters: list[int] = []
    for field in columns:
        if field not in text_fields:
            projection.append(field)
            continue
        projection.append(
            f"CASE WHEN length(CAST({field} AS BLOB)) <= ? THEN {field} "
            f"ELSE substr(CAST({field} AS BLOB), 1, 0) END AS {field}"
        )
        projection.append(
            f"length(CAST({field} AS BLOB)) AS _mig03_bytes_{field}"
        )
        parameters.append(maximum_bytes)
    return ", ".join(projection), tuple(parameters)


def _validate_bounded_projection(
    row: sqlite3.Row,
    text_fields: tuple[str, ...],
    *,
    maximum_bytes: int,
) -> None:
    for field in text_fields:
        byte_count = row[f"_mig03_bytes_{field}"]
        if byte_count is None:
            if row[field] is not None:
                raise MigrationImportError(CODE_SOURCE_UNREADABLE)
            continue
        if type(byte_count) is not int or byte_count < 0:
            raise MigrationImportError(CODE_SOURCE_UNREADABLE)
        if byte_count > maximum_bytes:
            raise MigrationImportError(CODE_SOURCE_UNREADABLE)


def _validate_intent_decimal_wire(payload: Mapping[str, Any]) -> None:
    from decimal import Decimal

    for field in ("quantity", "price"):
        value = payload[field]
        if field == "price" and value is None:
            continue
        if (
            type(value) is not str
            or len(value.encode("utf-8")) > _MAX_EXECUTION_DECIMAL_BYTES
            or "e" in value.lower()
        ):
            raise ValueError
        parsed = Decimal(value)
        if not parsed.is_finite() or format(parsed, "f") != value:
            raise ValueError


def _row_dict(row: sqlite3.Row, fields: tuple[str, ...]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field in fields:
        value = row[field]
        if value is not None and type(value) not in (str, int, float, bytes):
            raise MigrationImportError(CODE_SOURCE_UNREADABLE)
        if isinstance(value, float) or isinstance(value, bytes):
            raise MigrationImportError(CODE_SOURCE_UNREADABLE)
        result[field] = value
    return result


def _validate_execution_record_scalars(row: dict[str, Any]) -> None:
    required_text = (
        "scope_key",
        "intent_id",
        "payload_sha256",
        "payload_json",
        "state",
        "filled_quantity",
    )
    optional_text = (
        "provider_order_id",
        "average_price",
        "cumulative_commission",
        "permit_reference",
        "basic_admission_reference",
        "admission_context_sha256",
        "group_binding_json",
        "group_binding_sha256",
        "unknown_reason",
    )
    if any(type(row[name]) is not str for name in required_text):
        raise MigrationImportError(CODE_SOURCE_UNREADABLE)
    if any(row[name] is not None and type(row[name]) is not str for name in optional_text):
        raise MigrationImportError(CODE_SOURCE_UNREADABLE)
    if (
        type(row["dispatch_attempts"]) is not int
        or row["dispatch_attempts"] < 0
        or type(row["review_required"]) is not int
        or row["review_required"] not in (0, 1)
        or type(row["created_at_ns"]) is not int
        or row["created_at_ns"] <= 0
        or type(row["updated_at_ns"]) is not int
        or row["updated_at_ns"] <= 0
    ):
        raise MigrationImportError(CODE_SOURCE_UNREADABLE)


def _validate_outbox_scalars(row: dict[str, Any]) -> None:
    if (
        type(row["sequence"]) is not int
        or row["sequence"] <= 0
        or any(
            type(row[name]) is not str
            for name in ("event_id", "intent_id", "scope_key", "event_type", "state", "payload_json")
        )
        or type(row["created_at_ns"]) is not int
        or row["created_at_ns"] <= 0
        or type(row["journal_incarnation_id"]) is not str
    ):
        raise MigrationImportError(CODE_SOURCE_UNREADABLE)


def _sqlite_int(value: Any) -> int:
    if type(value) is not int or value < 0:
        raise MigrationImportError(CODE_SOURCE_UNREADABLE)
    return value


def _rollback_read(connection: sqlite3.Connection) -> None:
    try:
        connection.execute("ROLLBACK")
    except sqlite3.Error:
        pass


def _exact(value: Any, fields: set[str] | frozenset[str]) -> dict[str, Any]:
    if type(value) is not dict or any(type(key) is not str for key in value):
        raise ValueError
    if set(value) != set(fields):
        raise ValueError
    return value


def _array(value: Any, *, maximum: int) -> list[Any]:
    if type(value) is not list or len(value) > maximum:
        raise ValueError
    return value


def _text(value: Any) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise ValueError from None
    if len(encoded) > _MAX_TEXT_BYTES or not value.isprintable():
        raise ValueError
    return value


def _text_tuple(
    value: Any, *, maximum: int, allow_empty: bool = False
) -> tuple[str, ...]:
    if type(value) not in (list, tuple) or len(value) > maximum:
        raise MigrationImportError(CODE_INVALID_MANIFEST)
    if not value and not allow_empty:
        raise MigrationImportError(CODE_INVALID_MANIFEST)
    try:
        return tuple(_text(item) for item in value)
    except Exception:
        raise MigrationImportError(CODE_INVALID_MANIFEST) from None


def _integer(value: Any, *, minimum: int) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError
    return value


def _digest_text(value: Any) -> str:
    if type(value) is not str or not _HEX_256.fullmatch(value):
        raise ValueError
    return value


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _canonical_text(value: Any) -> str:
    return _canonical_bytes(value).decode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _plain_json_value(value: Any) -> Any:
    if type(value) in (str, bool, int) or value is None:
        return value
    if type(value) is float:
        if not (value == value and abs(value) != float("inf")):
            raise ValueError
        return value
    if type(value) is list:
        return [_plain_json_value(item) for item in value]
    if type(value) is dict:
        if any(type(key) is not str for key in value):
            raise ValueError
        return {key: _plain_json_value(item) for key, item in value.items()}
    raise ValueError
