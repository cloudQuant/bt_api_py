"""Fixed MIG03 migration-boundary inventory and conservation checks.

The iteration-10 plan permits retaining the existing stores in place, but
requires a complete account/resource-pool inventory and a demonstrated writer
handoff before a new epoch is armed.  This module is deliberately limited to
that contract: it does not copy databases, dispatch work, or treat an operator
reference as proof of an external provider fence.

Call :func:`arm_migration_boundary` only after every listed participant has a
readable snapshot (or a configuration-backed not-applicable record), the old
writer has exited normally, and every source consumer has a stop receipt.  The
separate post-write check verifies that the old deployment epoch is rejected
by the live service after the first new-epoch write attempt.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import stat
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Mapping

from .service_migration_gate import (
    BUSINESS_WRITE_FACT_KINDS,
    MigrationCutoverMarker,
    arm_migration_cutover,
)

BOUNDARY_CATEGORIES = frozenset(
    {
        "session_resource_pool",
        "execution_orders_fills_costs_manual_book_unknown",
        "risk_reservations_budgets_freezes_policies",
        "group_legs_deadlines_reservations",
        "gateway_outboxes_checkpoints",
        "all_consumers",
        "afi_operations_approvals",
        "afi_paper_ledger",
    }
)

PARTICIPANT_CATEGORY = {
    "registry": "session_resource_pool",
    "execution": "execution_orders_fills_costs_manual_book_unknown",
    "risk": "risk_reservations_budgets_freezes_policies",
    "groups": "group_legs_deadlines_reservations",
    "gateway": "gateway_outboxes_checkpoints",
    "consumers": "all_consumers",
    "afi_operations": "afi_operations_approvals",
    "afi_paper": "afi_paper_ledger",
}

DISPOSITION_IN_PLACE_RETAINED = "IN_PLACE_RETAINED"
DISPOSITION_NOT_APPLICABLE = "NOT_APPLICABLE"

CODE_BOUNDARY_INVALID = "MIG03_BOUNDARY_INVALID"
CODE_BOUNDARY_INCOMPLETE = "MIG03_BOUNDARY_INCOMPLETE"
CODE_BOUNDARY_UNKNOWN_PARTICIPANT = "MIG03_BOUNDARY_UNKNOWN_PARTICIPANT"
CODE_BOUNDARY_UNAVAILABLE = "MIG03_BOUNDARY_UNAVAILABLE"
CODE_BOUNDARY_INCOMPATIBLE = "MIG03_BOUNDARY_INCOMPATIBLE"
CODE_BOUNDARY_NOT_QUIESCENT = "MIG03_BOUNDARY_NOT_QUIESCENT"
CODE_BOUNDARY_NOT_CONSERVED = "MIG03_BOUNDARY_NOT_CONSERVED"
CODE_BOUNDARY_OLD_EPOCH_ACCEPTED = "MIG03_BOUNDARY_OLD_EPOCH_ACCEPTED"

_MAX_INVENTORY_ROWS = 100_000
_MAX_INVENTORY_BYTES = 32 * 1024 * 1024
_SQLITE_REQUIRED_TABLES = {
    "registry": frozenset(
        {"registry_meta", "sessions", "registration_keys", "strategy_write_claims"}
    ),
    "execution": frozenset({"execution_meta", "execution_records", "execution_outbox"}),
    "risk": frozenset({"risk_reservations", "risk_freezes", "risk_dispatch_claims"}),
    "groups": frozenset(
        {"execution_group_meta", "order_group_snapshots", "order_group_events"}
    ),
    "gateway": frozenset(
        {"gateway_events", "gateway_consumer_streams", "gateway_consumer_progress"}
    ),
}


class MigrationBoundaryError(RuntimeError):
    """Finite MIG03 boundary error without account or identity values."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ParticipantEvidence:
    """One fixed MIG03 participant's retained state or proven absence."""

    participant_id: str
    disposition: str
    source_ref: str
    configuration_sha256: str
    state_sha256: str | None = None
    identity_sha256: str | None = None
    schema_sha256: str | None = None
    row_count: int | None = None
    reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class ConsumerStopEvidence:
    """Normal close receipt for one old client or consumer process."""

    consumer_id: str
    process_id: int
    close_acknowledged: bool
    exit_code: int | None = None
    checkpoint_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class BoundaryPlan:
    """Validated immutable pre-arm inventory for one in-place cutover."""

    migration_id: str
    source_epoch: str
    destination_epoch: str
    old_writer_pid: int
    old_writer_exit_code: int
    consumers: tuple[ConsumerStopEvidence, ...]
    participants: tuple[ParticipantEvidence, ...]
    inventory_sha256: str
    fence_reference: str


@dataclass(frozen=True, slots=True)
class StateConservation:
    """Business identities and quantities that must survive an in-place handoff."""

    identity_links: tuple[tuple[str, str, str, str], ...]
    amounts: tuple[tuple[str, str], ...]
    positions: tuple[tuple[str, str], ...]
    unresolved_ids: tuple[str, ...]
    reservation_ids: tuple[str, ...]
    stream_cursors: tuple[tuple[str, str, int, int], ...]


@dataclass(frozen=True, slots=True)
class SqliteParticipantSnapshot:
    """Bounded read-only fingerprint of one supported SQLite participant."""

    evidence: ParticipantEvidence
    schema_sha256: str
    table_names: tuple[str, ...]


def capture_sqlite_participant(
    participant_id: str,
    path: str | Path,
    *,
    configuration_sha256: str,
    expected_schema_sha256: str | None = None,
    max_rows: int = _MAX_INVENTORY_ROWS,
    max_bytes: int = _MAX_INVENTORY_BYTES,
) -> SqliteParticipantSnapshot:
    """Capture a supported SDK SQLite store without changing it.

    The participant ID selects a fixed required-table contract.  Every user
    table and row is included in the state digest so a same-path handoff can
    prove byte-semantic retention without exporting identities into evidence.
    Reads are bounded and SQLite is opened read-only.
    """

    if participant_id not in _SQLITE_REQUIRED_TABLES:
        raise MigrationBoundaryError(CODE_BOUNDARY_UNKNOWN_PARTICIPANT)
    _require_sha256(configuration_sha256)
    if expected_schema_sha256 is not None:
        _require_sha256(expected_schema_sha256)
    if type(max_rows) is not int or max_rows <= 0:
        raise MigrationBoundaryError(CODE_BOUNDARY_INVALID)
    if type(max_bytes) is not int or max_bytes <= 0:
        raise MigrationBoundaryError(CODE_BOUNDARY_INVALID)

    db_path = Path(path)
    try:
        info = db_path.stat()
    except FileNotFoundError:
        raise MigrationBoundaryError(CODE_BOUNDARY_UNAVAILABLE) from None
    except OSError:
        raise MigrationBoundaryError(CODE_BOUNDARY_UNAVAILABLE) from None
    if not stat.S_ISREG(info.st_mode) or info.st_size <= 0:
        raise MigrationBoundaryError(CODE_BOUNDARY_UNAVAILABLE)
    if info.st_size > max_bytes:
        raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)

    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1.0
        )
        connection.execute("PRAGMA query_only = ON")
        quick_check = tuple(row[0] for row in connection.execute("PRAGMA quick_check"))
        if not quick_check or any(value != "ok" for value in quick_check):
            raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
        tables = tuple(
            sorted(
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%'"
                )
            )
        )
        if not _SQLITE_REQUIRED_TABLES[participant_id].issubset(tables):
            raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)

        schema_rows: list[tuple[Any, ...]] = []
        table_rows: list[tuple[str, tuple[bytes, ...]]] = []
        registry_identity_rows: list[tuple[str, bytes]] = []
        schema_objects = tuple(
            tuple(row)
            for row in connection.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
            )
        )
        schema_rows.append(("sqlite_master", schema_objects))
        total_rows = 0
        total_bytes = 0
        for table in tables:
            quoted_table = _quote_identifier(table)
            columns = tuple(connection.execute(f"PRAGMA table_info({quoted_table})"))
            if not columns:
                raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
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
            column_names = tuple(row[1] for row in columns)
            cursor = connection.execute(f"SELECT * FROM {quoted_table}")
            while True:
                batch = cursor.fetchmany(min(256, max_rows - total_rows + 1))
                if not batch:
                    break
                for row in batch:
                    total_rows += 1
                    if total_rows > max_rows:
                        raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
                    values = tuple(row)
                    payload = _canonical_json_bytes([_sqlite_value(value) for value in values])
                    total_bytes += len(payload)
                    if total_bytes > max_bytes:
                        raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
                    row_payloads.append(payload)
                    if participant_id == "registry":
                        if table == "registry_meta" and values[0] != "service_epoch":
                            registry_identity_rows.append((table, payload))
                        elif table == "strategy_write_claims":
                            stable_columns = (
                                "execution_domain_ref",
                                "strategy_ref",
                                "account_ref",
                                "run_id",
                            )
                            if any(name not in column_names for name in stable_columns):
                                raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
                            indexes = tuple(column_names.index(name) for name in stable_columns)
                            registry_identity_rows.append(
                                (
                                    table,
                                    _canonical_json_bytes(
                                        [_sqlite_value(values[index]) for index in indexes]
                                    ),
                                )
                            )
            row_payloads.sort()
            table_rows.append((table, tuple(row_payloads)))
    except MigrationBoundaryError:
        raise
    except (sqlite3.Error, OSError, ValueError, TypeError):
        raise MigrationBoundaryError(CODE_BOUNDARY_UNAVAILABLE) from None
    finally:
        if connection is not None:
            connection.close()

    schema_payload = _canonical_json_bytes(schema_rows)
    if len(schema_payload) + total_bytes > max_bytes:
        raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
    schema_digest = hashlib.sha256(schema_payload).hexdigest()
    if expected_schema_sha256 is not None and schema_digest != expected_schema_sha256:
        raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
    state_digest = _sqlite_state_sha256(schema_payload, table_rows)
    if participant_id == "registry":
        identity_rows = tuple(sorted(registry_identity_rows))
    else:
        identity_rows = tuple(
            (table, payload)
            for table, payloads in table_rows
            for payload in payloads
        )
    identity_digest = hashlib.sha256(
        _canonical_json_bytes(
            [(table, payload.hex()) for table, payload in identity_rows]
        )
    ).hexdigest()
    evidence = ParticipantEvidence(
        participant_id=participant_id,
        disposition=DISPOSITION_IN_PLACE_RETAINED,
        source_ref=str(db_path.resolve()),
        configuration_sha256=configuration_sha256,
        state_sha256=state_digest,
        identity_sha256=identity_digest,
        schema_sha256=schema_digest,
        row_count=total_rows,
    )
    return SqliteParticipantSnapshot(evidence, schema_digest, tables)


def not_applicable_participant(
    participant_id: str,
    *,
    evidence_ref: str,
    configuration_sha256: str,
) -> ParticipantEvidence:
    """Record a supported participant as unconfigured using pinned config evidence."""

    if participant_id not in PARTICIPANT_CATEGORY:
        raise MigrationBoundaryError(CODE_BOUNDARY_UNKNOWN_PARTICIPANT)
    _require_text(evidence_ref)
    _require_sha256(configuration_sha256)
    return ParticipantEvidence(
        participant_id=participant_id,
        disposition=DISPOSITION_NOT_APPLICABLE,
        source_ref=evidence_ref,
        configuration_sha256=configuration_sha256,
        reason_code="NOT_CONFIGURED",
    )


def retained_external_participant(
    participant_id: str,
    *,
    source_ref: str,
    configuration_sha256: str,
    state_sha256: str,
    identity_sha256: str,
    schema_sha256: str,
    row_count: int,
) -> ParticipantEvidence:
    """Adapt an independently implemented participant reader to this contract."""

    if participant_id not in {"consumers", "afi_operations", "afi_paper"}:
        raise MigrationBoundaryError(CODE_BOUNDARY_UNKNOWN_PARTICIPANT)
    _require_text(source_ref)
    _require_sha256(configuration_sha256)
    _require_sha256(state_sha256)
    _require_sha256(identity_sha256)
    _require_sha256(schema_sha256)
    if type(row_count) is not int or row_count < 0:
        raise MigrationBoundaryError(CODE_BOUNDARY_INVALID)
    return ParticipantEvidence(
        participant_id=participant_id,
        disposition=DISPOSITION_IN_PLACE_RETAINED,
        source_ref=source_ref,
        configuration_sha256=configuration_sha256,
        state_sha256=state_sha256,
        identity_sha256=identity_sha256,
        schema_sha256=schema_sha256,
        row_count=row_count,
    )


def prepare_migration_boundary(
    *,
    migration_id: str,
    source_epoch: str,
    destination_epoch: str,
    old_writer_pid: int,
    old_writer_exit_code: int,
    consumers: Iterable[ConsumerStopEvidence],
    participants: Iterable[ParticipantEvidence],
    fence_reference: str,
) -> BoundaryPlan:
    """Validate complete pre-arm inventory, old-writer exit and consumer stop facts."""

    for value in (migration_id, source_epoch, destination_epoch, fence_reference):
        _require_text(value)
    if source_epoch == destination_epoch:
        raise MigrationBoundaryError(CODE_BOUNDARY_INVALID)
    if type(old_writer_pid) is not int or old_writer_pid <= 0:
        raise MigrationBoundaryError(CODE_BOUNDARY_NOT_QUIESCENT)
    if type(old_writer_exit_code) is not int or old_writer_exit_code != 0:
        raise MigrationBoundaryError(CODE_BOUNDARY_NOT_QUIESCENT)

    stopped_consumers = tuple(consumers)
    if not stopped_consumers:
        raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPLETE)
    consumer_ids: set[str] = set()
    for receipt in stopped_consumers:
        if type(receipt) is not ConsumerStopEvidence:
            raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
        _require_text(receipt.consumer_id)
        if (
            receipt.consumer_id in consumer_ids
            or type(receipt.process_id) is not int
            or receipt.process_id <= 0
            or receipt.close_acknowledged is not True
        ):
            raise MigrationBoundaryError(CODE_BOUNDARY_NOT_QUIESCENT)
        if receipt.exit_code is not None and (
            type(receipt.exit_code) is not int or receipt.exit_code != 0
        ):
            raise MigrationBoundaryError(CODE_BOUNDARY_NOT_QUIESCENT)
        _require_sha256(receipt.checkpoint_sha256)
        consumer_ids.add(receipt.consumer_id)

    evidence_rows = tuple(participants)
    by_id: dict[str, ParticipantEvidence] = {}
    category_seen: set[str] = set()
    for evidence in evidence_rows:
        if type(evidence) is not ParticipantEvidence:
            raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
        category = PARTICIPANT_CATEGORY.get(evidence.participant_id)
        if category is None:
            raise MigrationBoundaryError(CODE_BOUNDARY_UNKNOWN_PARTICIPANT)
        if evidence.participant_id in by_id or category in category_seen:
            raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
        if evidence.disposition == DISPOSITION_IN_PLACE_RETAINED:
            _validate_retained_evidence(evidence)
        elif evidence.disposition == DISPOSITION_NOT_APPLICABLE:
            _validate_not_applicable_evidence(evidence)
            if evidence.participant_id not in {"groups"}:
                raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
        else:
            if evidence.disposition in {"UNKNOWN", "UNAVAILABLE"}:
                raise MigrationBoundaryError(CODE_BOUNDARY_UNAVAILABLE)
            if evidence.disposition == "INCOMPATIBLE":
                raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
            raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
        by_id[evidence.participant_id] = evidence
        category_seen.add(category)

    expected_ids = set(PARTICIPANT_CATEGORY)
    if set(by_id) != expected_ids or category_seen != BOUNDARY_CATEGORIES:
        raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPLETE)
    if by_id["consumers"].disposition != DISPOSITION_IN_PLACE_RETAINED:
        raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
    if by_id["consumers"].identity_sha256 != consumer_inventory_sha256(stopped_consumers):
        raise MigrationBoundaryError(CODE_BOUNDARY_NOT_QUIESCENT)

    ordered_participants = tuple(by_id[key] for key in sorted(by_id))
    ordered_consumers = tuple(sorted(stopped_consumers, key=lambda item: item.consumer_id))
    digest_payload = {
        "migration_id": migration_id,
        "source_epoch": source_epoch,
        "destination_epoch": destination_epoch,
        "old_writer_pid": old_writer_pid,
        "old_writer_exit_code": old_writer_exit_code,
        "consumers": [_consumer_document(item) for item in ordered_consumers],
        "participants": [_participant_document(item) for item in ordered_participants],
        # A reference labels the observed local boundary. It does not assert an
        # external/provider fence beyond the exact process/epoch facts above.
        "fence_reference": fence_reference,
    }
    return BoundaryPlan(
        migration_id=migration_id,
        source_epoch=source_epoch,
        destination_epoch=destination_epoch,
        old_writer_pid=old_writer_pid,
        old_writer_exit_code=old_writer_exit_code,
        consumers=ordered_consumers,
        participants=ordered_participants,
        inventory_sha256=hashlib.sha256(_canonical_json_bytes(digest_payload)).hexdigest(),
        fence_reference=fence_reference,
    )


def arm_migration_boundary(
    state_path: str | Path,
    plan: BoundaryPlan,
) -> MigrationCutoverMarker:
    """Arm the existing cutover marker only after full boundary preflight."""

    if type(plan) is not BoundaryPlan:
        raise MigrationBoundaryError(CODE_BOUNDARY_INVALID)
    verified = prepare_migration_boundary(
        migration_id=plan.migration_id,
        source_epoch=plan.source_epoch,
        destination_epoch=plan.destination_epoch,
        old_writer_pid=plan.old_writer_pid,
        old_writer_exit_code=plan.old_writer_exit_code,
        consumers=plan.consumers,
        participants=plan.participants,
        fence_reference=plan.fence_reference,
    )
    if verified.inventory_sha256 != plan.inventory_sha256:
        raise MigrationBoundaryError(CODE_BOUNDARY_INVALID)
    return arm_migration_cutover(
        state_path,
        source_epoch=plan.source_epoch,
        new_epoch=plan.destination_epoch,
        migration_id=plan.migration_id,
    )


def require_old_epoch_refused_after_new_write(
    plan: BoundaryPlan,
    *,
    serving_epoch: str,
    first_write_fact_kind: str,
    refusal_code: str,
    resource_provider_calls: int,
) -> None:
    """Require actual live-start refusal of the old stable epoch after sealing."""

    if type(plan) is not BoundaryPlan:
        raise MigrationBoundaryError(CODE_BOUNDARY_INVALID)
    if (
        serving_epoch != plan.destination_epoch
        or type(first_write_fact_kind) is not str
        or first_write_fact_kind not in BUSINESS_WRITE_FACT_KINDS
        or type(refusal_code) is not str
        or refusal_code != "REVERSE_MIGRATION_REFUSED"
        or type(resource_provider_calls) is not int
        or resource_provider_calls != 0
    ):
        raise MigrationBoundaryError(CODE_BOUNDARY_OLD_EPOCH_ACCEPTED)


def assert_state_conserved(before: StateConservation, after: StateConservation) -> None:
    """Require complete identity, money, position, unresolved and cursor equality."""

    if type(before) is not StateConservation or type(after) is not StateConservation:
        raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
    normalized_before = _normalize_conservation(before)
    normalized_after = _normalize_conservation(after)
    if normalized_before != normalized_after:
        raise MigrationBoundaryError(CODE_BOUNDARY_NOT_CONSERVED)


def assert_participant_states_conserved(
    before: Iterable[ParticipantEvidence],
    after: Iterable[ParticipantEvidence],
) -> None:
    """Compare retained stores, allowing only registry epoch/session rows to change."""

    before_rows = tuple(before)
    after_rows = tuple(after)
    if any(type(item) is not ParticipantEvidence for item in (*before_rows, *after_rows)):
        raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
    before_by_id = {item.participant_id: item for item in before_rows}
    after_by_id = {item.participant_id: item for item in after_rows}
    if len(before_by_id) != len(before_rows) or len(after_by_id) != len(after_rows):
        raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
    if set(before_by_id) != set(PARTICIPANT_CATEGORY) or set(after_by_id) != set(
        PARTICIPANT_CATEGORY
    ):
        raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPLETE)
    for participant_id in PARTICIPANT_CATEGORY:
        old = before_by_id[participant_id]
        new = after_by_id[participant_id]
        if old.disposition != new.disposition:
            raise MigrationBoundaryError(CODE_BOUNDARY_NOT_CONSERVED)
        if old.disposition == DISPOSITION_NOT_APPLICABLE:
            if _participant_document(old) != _participant_document(new):
                raise MigrationBoundaryError(CODE_BOUNDARY_NOT_CONSERVED)
            continue
        if (
            old.source_ref != new.source_ref
            or old.configuration_sha256 != new.configuration_sha256
            or old.schema_sha256 != new.schema_sha256
        ):
            raise MigrationBoundaryError(CODE_BOUNDARY_NOT_CONSERVED)
        if participant_id == "registry":
            # The state digest may change with the service epoch and live
            # sessions. capture_sqlite_participant fingerprints stable service
            # metadata and logical strategy-claim identities separately.
            if old.identity_sha256 != new.identity_sha256:
                raise MigrationBoundaryError(CODE_BOUNDARY_NOT_CONSERVED)
            continue
        if (
            old.state_sha256 != new.state_sha256
            or old.identity_sha256 != new.identity_sha256
            or old.schema_sha256 != new.schema_sha256
        ):
            raise MigrationBoundaryError(CODE_BOUNDARY_NOT_CONSERVED)


def conservation_from_mapping(value: Mapping[str, Any]) -> StateConservation:
    """Parse the fixed, JSON-compatible state-conservation projection."""

    if type(value) is not dict or set(value) != {
        "identity_links",
        "amounts",
        "positions",
        "unresolved_ids",
        "reservation_ids",
        "stream_cursors",
    }:
        raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
    try:
        result = StateConservation(
            identity_links=tuple(_string_tuple(row, 4) for row in value["identity_links"]),
            amounts=tuple(_amount_pair(row) for row in value["amounts"]),
            positions=tuple(_amount_pair(row) for row in value["positions"]),
            unresolved_ids=tuple(_string_value(item) for item in value["unresolved_ids"]),
            reservation_ids=tuple(_string_value(item) for item in value["reservation_ids"]),
            stream_cursors=tuple(_cursor_tuple(row) for row in value["stream_cursors"]),
        )
    except (TypeError, ValueError, KeyError, InvalidOperation):
        raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE) from None
    return _normalize_conservation(result)


def _normalize_conservation(state: StateConservation) -> StateConservation:
    try:
        return StateConservation(
            identity_links=tuple(sorted(_string_tuple(row, 4) for row in state.identity_links)),
            amounts=tuple(sorted(_amount_pair(row) for row in state.amounts)),
            positions=tuple(sorted(_amount_pair(row) for row in state.positions)),
            unresolved_ids=tuple(sorted(_string_value(item) for item in state.unresolved_ids)),
            reservation_ids=tuple(sorted(_string_value(item) for item in state.reservation_ids)),
            stream_cursors=tuple(sorted(_cursor_tuple(row) for row in state.stream_cursors)),
        )
    except (TypeError, ValueError, InvalidOperation):
        raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE) from None


def _validate_retained_evidence(evidence: ParticipantEvidence) -> None:
    _require_text(evidence.source_ref)
    _require_sha256(evidence.configuration_sha256)
    _require_sha256(evidence.state_sha256)
    _require_sha256(evidence.identity_sha256)
    _require_sha256(evidence.schema_sha256)
    if type(evidence.row_count) is not int or evidence.row_count < 0:
        raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
    if evidence.reason_code is not None:
        raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)


def _validate_not_applicable_evidence(evidence: ParticipantEvidence) -> None:
    _require_text(evidence.source_ref)
    _require_sha256(evidence.configuration_sha256)
    if (
        evidence.reason_code != "NOT_CONFIGURED"
        or evidence.state_sha256 is not None
        or evidence.identity_sha256 is not None
        or evidence.schema_sha256 is not None
        or evidence.row_count is not None
    ):
        raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)


def _participant_document(evidence: ParticipantEvidence) -> dict[str, Any]:
    return {
        "participant_id": evidence.participant_id,
        "disposition": evidence.disposition,
        "source_ref": evidence.source_ref,
        "configuration_sha256": evidence.configuration_sha256,
        "state_sha256": evidence.state_sha256,
        "identity_sha256": evidence.identity_sha256,
        "schema_sha256": evidence.schema_sha256,
        "row_count": evidence.row_count,
        "reason_code": evidence.reason_code,
    }


def _consumer_document(receipt: ConsumerStopEvidence) -> dict[str, Any]:
    return {
        "consumer_id": receipt.consumer_id,
        "process_id": receipt.process_id,
        "close_acknowledged": receipt.close_acknowledged,
        "exit_code": receipt.exit_code,
        "checkpoint_sha256": receipt.checkpoint_sha256,
    }


def consumer_inventory_sha256(consumers: Iterable[ConsumerStopEvidence]) -> str:
    """Fingerprint the exact consumer IDs and process owners in a stop set."""

    receipts = tuple(consumers)
    ids: set[str] = set()
    identities: list[tuple[str, int]] = []
    for receipt in receipts:
        if type(receipt) is not ConsumerStopEvidence:
            raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
        _require_text(receipt.consumer_id)
        if type(receipt.process_id) is not int or receipt.process_id <= 0:
            raise MigrationBoundaryError(CODE_BOUNDARY_NOT_QUIESCENT)
        if receipt.consumer_id in ids:
            raise MigrationBoundaryError(CODE_BOUNDARY_NOT_QUIESCENT)
        ids.add(receipt.consumer_id)
        identities.append((receipt.consumer_id, receipt.process_id))
    if not identities:
        raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPLETE)
    return hashlib.sha256(_canonical_json_bytes(sorted(identities))).hexdigest()


def _quote_identifier(value: str) -> str:
    if type(value) is not str or not value:
        raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
    return '"' + value.replace('"', '""') + '"'


def _sqlite_value(value: Any) -> Any:
    if value is None or type(value) in (str, int):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
        return {"float_hex": value.hex()}
    if type(value) is bytes:
        return {"bytes_hex": value.hex()}
    raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)


def _sqlite_state_sha256(
    schema_payload: bytes,
    table_rows: list[tuple[str, tuple[bytes, ...]]],
) -> str:
    """Hash a bounded canonical representation of schema and binary row payloads."""

    digest = hashlib.sha256()
    digest.update(schema_payload)
    digest.update(b"\0[")
    for table_index, (table_name, row_payloads) in enumerate(table_rows):
        if table_index:
            digest.update(b",")
        digest.update(b"[")
        digest.update(_canonical_json_bytes(table_name))
        digest.update(b",[")
        for row_index, payload in enumerate(row_payloads):
            if row_index:
                digest.update(b",")
            # Row payloads are canonical JSON bytes. Hex preserves every byte
            # exactly while keeping the digest input JSON-safe and bounded.
            digest.update(_canonical_json_bytes(payload.hex()))
        digest.update(b"]]")
    digest.update(b"]")
    return digest.hexdigest()


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeError):
        raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE) from None


def _amount_pair(row: Any) -> tuple[str, str]:
    values = _string_tuple(row, 2)
    key, raw = values
    try:
        number = Decimal(raw)
        if not number.is_finite():
            raise InvalidOperation
        canonical = _decimal_text(number)
    except (InvalidOperation, ValueError):
        raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE) from None
    return key, canonical


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise InvalidOperation
    sign, digits_tuple, exponent = value.as_tuple()
    digits = "".join(str(digit) for digit in digits_tuple)
    if not digits or not any(digit != "0" for digit in digits_tuple):
        return "0"
    digits = digits.rstrip("0")
    exponent += len(digits_tuple) - len(digits)
    if len(digits) > 256 or abs(exponent) > 256:
        raise InvalidOperation
    if exponent >= 0:
        body = digits + ("0" * exponent)
    else:
        point = len(digits) + exponent
        if point > 0:
            body = digits[:point] + "." + digits[point:]
        else:
            body = "0." + ("0" * -point) + digits
    return ("-" if sign else "") + body


def _cursor_tuple(row: Any) -> tuple[str, str, int, int]:
    values = tuple(row)
    if (
        len(values) != 4
        or type(values[0]) is not str
        or not values[0]
        or type(values[1]) is not str
        or not values[1]
        or type(values[2]) is not int
        or type(values[3]) is not int
        or values[2] < 0
        or values[3] < 0
        or values[3] > values[2]
    ):
        raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
    return values


def _string_tuple(row: Any, expected_length: int) -> tuple[str, ...]:
    values = tuple(row)
    if len(values) != expected_length:
        raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
    return tuple(_string_value(value) for value in values)


def _string_value(value: Any) -> str:
    if type(value) is not str or not value:
        raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
    return value


def _require_text(value: Any) -> None:
    if type(value) is not str or not value:
        raise MigrationBoundaryError(CODE_BOUNDARY_INVALID)


def _require_sha256(value: Any) -> None:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise MigrationBoundaryError(CODE_BOUNDARY_INVALID)
