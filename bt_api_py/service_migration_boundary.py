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

_GROUP_RUN_BINDING_TABLE = "coordinator_group_run_bindings"
_GROUP_COORDINATOR_SIDE_TABLE_SQL = {
    "coordinator_group_cancel_child_claims": (
        "CREATE TABLE coordinator_group_cancel_child_claims (cancel_id TEXT PRIMARY KEY, "
        "portfolio_ref TEXT NOT NULL, group_id TEXT NOT NULL, origin_run_id TEXT NOT NULL, "
        "leg_id TEXT NOT NULL, account_ref TEXT NOT NULL, target_intent_id TEXT NOT NULL, "
        "provider_order_id TEXT NOT NULL, owner_cancel_request_id TEXT NOT NULL, "
        "phase TEXT NOT NULL CHECK(phase IN ('PLANNED','DISPATCHING','UNKNOWN','OBSERVED')), "
        "updated_at_ns INTEGER NOT NULL)"
    ),
    "coordinator_group_cancel_plan_legs": (
        "CREATE TABLE coordinator_group_cancel_plan_legs (portfolio_ref TEXT NOT NULL, "
        "group_id TEXT NOT NULL, cancel_request_id TEXT NOT NULL, leg_id TEXT NOT NULL, "
        "account_ref TEXT NOT NULL, cancel_id TEXT NOT NULL, target_intent_id TEXT NOT NULL, "
        "provider_order_id TEXT NOT NULL, phase TEXT NOT NULL CHECK(phase IN "
        "('PLANNED','DISPATCHING','UNKNOWN','OBSERVED')), result_json TEXT, "
        "updated_at_ns INTEGER NOT NULL, PRIMARY KEY(portfolio_ref, group_id, "
        "cancel_request_id, leg_id), FOREIGN KEY(portfolio_ref, group_id, "
        "cancel_request_id) REFERENCES coordinator_group_cancel_plans(portfolio_ref, "
        "group_id, cancel_request_id))"
    ),
    "coordinator_group_cancel_plans": (
        "CREATE TABLE coordinator_group_cancel_plans (portfolio_ref TEXT NOT NULL, "
        "group_id TEXT NOT NULL, cancel_request_id TEXT NOT NULL, origin_run_id TEXT NOT NULL, "
        "manifest_json TEXT NOT NULL, manifest_sha256 TEXT NOT NULL, "
        "created_at_ns INTEGER NOT NULL, updated_at_ns INTEGER NOT NULL, "
        "PRIMARY KEY(portfolio_ref, group_id, cancel_request_id))"
    ),
    "coordinator_group_roll_exit_actions": (
        "CREATE TABLE coordinator_group_roll_exit_actions (action_id TEXT PRIMARY KEY, "
        "portfolio_ref TEXT NOT NULL, group_id TEXT NOT NULL, source_leg_id TEXT NOT NULL, "
        "source_digest TEXT NOT NULL, request_fingerprint TEXT NOT NULL, "
        "authorization_sha256 TEXT NOT NULL, authorization_json TEXT NOT NULL, "
        "phase TEXT NOT NULL, observation_json TEXT, updated_at_ns INTEGER NOT NULL)"
    ),
    "coordinator_group_roll_exit_source_idx": (
        "CREATE INDEX coordinator_group_roll_exit_source_idx "
        "ON coordinator_group_roll_exit_actions(portfolio_ref, group_id, source_leg_id, "
        "updated_at_ns)"
    ),
}
_SQLITE_REQUIRED_TABLES = {
    "registry": frozenset(
        {
            "registry_meta",
            "sessions",
            "registration_keys",
            "strategy_write_claims",
            "strategy_stop_new_receipts",
        }
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
class ConfiguredConsumer:
    """One consumer derived from parsed deployment configuration and authority.

    The public ID is a digest of its fixed configuration row.  This prevents a
    stop receipt from naming an arbitrary hand-written consumer set while
    keeping principals, account refs, and scopes out of the boundary record.
    """

    consumer_id: str
    consumer_kind: str
    configuration_sha256: str
    scope_sha256: str
    operations_sha256: str


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
    configured_consumers: tuple[ConfiguredConsumer, ...] = ()


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
        connection.execute("BEGIN")
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
                                "stop_new_at",
                                "stop_new_command_id",
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
                        elif table == "strategy_stop_new_receipts":
                            # The receipt ledger is part of the durable owner
                            # fence. Include its complete row in the identity
                            # projection so a migration cannot conserve the
                            # claim but silently lose the idempotency receipt.
                            registry_identity_rows.append((table, payload))
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


def capture_group_participant(
    group_store_path: str | Path,
    coordinator_binding_path: str | Path,
    *,
    configuration_sha256: str,
    expected_schema_sha256: str | None = None,
    max_rows: int = _MAX_INVENTORY_ROWS,
    max_bytes: int = _MAX_INVENTORY_BYTES,
) -> SqliteParticipantSnapshot:
    """Capture both durable parts of the production group coordinator state.

    Execution's group snapshots/events and the SDK's verified origin-run
    bindings live in separate SQLite files. A migration inventory that reads
    only ``SqliteOrderGroupStore`` would therefore preserve the legs while
    dropping the authenticated run/account attribution needed to recover them.
    This fixed helper combines those two supported schemas into the single
    formal ``groups`` participant. It also fingerprints the current fixed
    cancel/roll sidecar schema, but rejects those tables when they contain
    records because this slice has no typed transfer contract for those
    operations. Both files are opened read-only and the coordinator binding
    payloads are revalidated through their production typed decoder.
    """

    _require_sha256(configuration_sha256)
    if expected_schema_sha256 is not None:
        _require_sha256(expected_schema_sha256)
    if type(max_rows) is not int or max_rows <= 0:
        raise MigrationBoundaryError(CODE_BOUNDARY_INVALID)
    if type(max_bytes) is not int or max_bytes <= 0:
        raise MigrationBoundaryError(CODE_BOUNDARY_INVALID)

    group_store_size = _sqlite_storage_size(group_store_path, max_bytes=max_bytes)
    sidecar_size = _sqlite_storage_size(coordinator_binding_path, max_bytes=max_bytes)
    if group_store_size + sidecar_size > max_bytes:
        raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)

    group_snapshot = capture_sqlite_participant(
        "groups",
        group_store_path,
        configuration_sha256=configuration_sha256,
        max_rows=max_rows,
        max_bytes=max_bytes,
    )
    sidecar = _capture_group_run_bindings(
        coordinator_binding_path,
        max_rows=max_rows,
        max_bytes=max_bytes,
    )
    if (
        group_snapshot.evidence.row_count + sidecar["row_count"] > max_rows
        or group_store_size + sidecar_size > max_bytes
    ):
        raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
    combined_schema = _canonical_json_bytes(
        {
            "group_store": group_snapshot.schema_sha256,
            "coordinator_run_bindings": sidecar["schema_sha256"],
        }
    )
    schema_digest = hashlib.sha256(combined_schema).hexdigest()
    if expected_schema_sha256 is not None and schema_digest != expected_schema_sha256:
        raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
    state_digest = hashlib.sha256(
        _canonical_json_bytes(
            {
                "group_store": group_snapshot.evidence.state_sha256,
                "coordinator_run_bindings": sidecar["state_sha256"],
            }
        )
    ).hexdigest()
    identity_digest = hashlib.sha256(
        _canonical_json_bytes(
            {
                "group_store": group_snapshot.evidence.identity_sha256,
                "coordinator_run_bindings": sidecar["identity_sha256"],
            }
        )
    ).hexdigest()
    source_ref = _canonical_json_bytes(
        {
            "group_store": group_snapshot.evidence.source_ref,
            "coordinator_run_bindings": sidecar["source_ref"],
        }
    ).decode("utf-8", errors="strict")
    evidence = ParticipantEvidence(
        participant_id="groups",
        disposition=DISPOSITION_IN_PLACE_RETAINED,
        source_ref=source_ref,
        configuration_sha256=configuration_sha256,
        state_sha256=state_digest,
        identity_sha256=identity_digest,
        schema_sha256=schema_digest,
        row_count=group_snapshot.evidence.row_count + sidecar["row_count"],
    )
    return SqliteParticipantSnapshot(
        evidence,
        schema_digest,
        (
            *(f"group_store:{name}" for name in group_snapshot.table_names),
            "coordinator_run_bindings:coordinator_group_run_bindings",
            *(
                f"coordinator_sidecar:{name}"
                for name in sidecar["table_names"][1:]
            ),
        ),
    )


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


def derive_service_consumer_inventory(
    service_config: object,
    trusted_grants: Mapping[str, tuple[object, ...]],
    *,
    authority: object | None = None,
    requested_operations: Mapping[str, frozenset[str]] | None = None,
    scope_resolver: Any = None,
    peer_resolver: Any = None,
) -> tuple[ConfiguredConsumer, ...]:
    """Derive the SDK service's consumer set from parsed config and trusted ACL.

    This includes every configured component, each outbound service binding,
    and each trusted inbound resource grant.  It intentionally does not accept
    a caller-supplied list of consumer IDs.  AFI-owned schedulers and clients
    must be supplied by their own production inventory source; they are not
    inferred from an SDK service configuration. Configured outbound clients
    are resolved through the same trusted scope and peer resolvers used by the
    production runtime.
    """

    from .service_config import ServiceConfig
    from .service_host import TrustedServiceAuthority
    from .service_registry import PrincipalGrant, ResourceGrant
    from .service_runtime import (
        _APPROVED_ORDER_ENTRY_OPERATIONS,
        _DEFAULT_ACCOUNT_READ_OPERATIONS,
        _DEFAULT_TELEMETRY_INGRESS_OPERATIONS,
        _GROUP_MONITOR_OPERATIONS,
        _GROUP_ORDER_ENTRY_OPERATIONS,
        MOUNT_PORTS,
        OPTIONAL_MOUNT_PORTS,
        PORT_OPERATIONS,
    )
    from bt_api_base.service_protocol import WireIdentity, WireScope

    if type(service_config) is not ServiceConfig or not isinstance(trusted_grants, Mapping):
        raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
    if authority is not None and type(authority) is not TrustedServiceAuthority:
        raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
    try:
        raw = service_config.to_public_dict()
    except Exception:
        raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE) from None
    components = raw.get("components")
    clients = raw.get("clients", {})
    if type(components) is not dict or type(clients) is not dict or not components:
        raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
    if requested_operations is not None and (
        not isinstance(requested_operations, Mapping)
        or any(
            type(mount) is not str
            or type(operations) is not frozenset
            or not operations
            or any(type(operation) is not str or not operation for operation in operations)
            for mount, operations in requested_operations.items()
        )
    ):
        raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
    service_configuration_sha256 = hashlib.sha256(
        _canonical_json_bytes(_service_consumer_config_document(raw))
    ).hexdigest()
    component_rows: dict[str, tuple[str, frozenset[str], dict[str, Any]]] = {}
    rows: list[tuple[str, dict[str, Any]]] = []
    for component_name, component in components.items():
        if type(component_name) is not str or type(component) is not dict:
            raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
        settings = component.get("settings")
        if type(settings) is not dict:
            raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
        instance_id = settings.get("instance_id")
        ports = settings.get("bind_ports")
        options = settings.get("options", {})
        if (
            type(instance_id) is not str
            or not instance_id
            or type(ports) is not list
            or not ports
            or any(type(port) is not str or not port for port in ports)
            or len(set(ports)) != len(ports)
            or type(options) is not dict
        ):
            raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
        if instance_id in component_rows:
            raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
        component_rows[instance_id] = (component_name, frozenset(ports), component)
        component_document = {
            "kind": "configured_component",
            "service_id": service_config.service_id,
            "component": component_name,
            "instance_id": instance_id,
            "bind_ports": sorted(ports),
            "options": options,
            "component_configuration": component,
        }
        rows.append(("configured_component", component_document))

    for principal_ref, grants in trusted_grants.items():
        if type(principal_ref) is not str or not principal_ref or type(grants) is not tuple:
            raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
        for principal_grant in grants:
            if type(principal_grant) is not PrincipalGrant:
                raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
            identity = principal_grant.identity
            if identity.principal_ref != principal_ref:
                raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
            identity_document = {
                "role": identity.role.value,
                "principal_ref": identity.principal_ref,
                "actor_ref": identity.actor_ref,
                "delegate_ref": identity.delegate_ref,
            }
            for grant in principal_grant.resources:
                if type(grant) is not ResourceGrant:
                    raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
                component_row = component_rows.get(grant.instance_id)
                if component_row is None or grant.port not in component_row[1]:
                    raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
                scope_document = _wire_scope_document(grant.scope)
                rows.append(
                    (
                        "trusted_inbound_grant",
                        {
                            "kind": "trusted_inbound_grant",
                            "service_id": service_config.service_id,
                            "principal": identity_document,
                            "instance_id": grant.instance_id,
                            "port": grant.port,
                            "scope_ref": grant.scope_ref,
                            "scope": scope_document,
                            "operations": sorted(grant.operations),
                        },
                    )
                )

    if authority is not None:
        if authority.grants != trusted_grants:
            raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
        principal_rows = []
        for public_key, principal_ref in authority.principals.items():
            if type(public_key) is not bytes or type(principal_ref) is not str:
                raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
            principal_rows.append(
                {
                    "public_key_sha256": hashlib.sha256(public_key).hexdigest(),
                    "principal_ref": principal_ref,
                }
            )
        policy_rows: list[dict[str, Any]] = []
        for policy_type, policies in (
            ("group_write", authority.group_write_policies),
            ("group_reservation", authority.group_reservation_policies),
            ("group_resolution", authority.group_resolution_policies),
        ):
            for policy in policies:
                policy_document: dict[str, Any] = {"kind": policy_type}
                for field_name in (
                    "instance_id",
                    "principal_ref",
                    "scope_ref",
                    "run_id",
                    "port",
                    "operation",
                    "account_ref",
                ):
                    if hasattr(policy, field_name):
                        policy_document[field_name] = getattr(policy, field_name)
                policy_document["identity"] = _wire_identity_document(policy.identity)
                policy_document["scope"] = _wire_scope_document(policy.scope)
                policy_rows.append(policy_document)
        authority_document = {
            "kind": "trusted_service_authority",
            "service_id": service_config.service_id,
            "identity": _wire_identity_document(authority.identity),
            "scope": _wire_scope_document(authority.scope),
            "scope_ref": authority.scope_ref,
            "scope_version": authority.scope_version,
            "security_ref": authority.security_ref,
            "security_version": authority.security_version,
            "curve_public_key_sha256": hashlib.sha256(authority.curve_public_key).hexdigest(),
            "principals": sorted(
                principal_rows,
                key=lambda item: (item["principal_ref"], item["public_key_sha256"]),
            ),
            "registration_policy_ref": authority.registration_policy_ref,
            "registration_policy_version": authority.registration_policy_version,
            "service_requirements_ref": authority.service_requirements_ref,
            "service_requirements_version": authority.service_requirements_version,
            "group_policies": sorted(
                policy_rows,
                key=lambda item: hashlib.sha256(_canonical_json_bytes(item)).hexdigest(),
            ),
        }
        rows.append(("trusted_service_authority", authority_document))

    for client_name, client in clients.items():
        if type(client_name) is not str or type(client) is not dict:
            raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
        settings = client.get("settings")
        bindings = settings.get("bindings") if type(settings) is dict else None
        if type(bindings) is not list:
            raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
        for binding in bindings:
            if type(binding) is not dict:
                raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
            required = {"mount", "service_id", "instance_id", "scope_ref", "transport_ref"}
            if not required.issubset(binding):
                raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
            mount = binding["mount"]
            if (
                type(mount) is not str
                or mount not in MOUNT_PORTS
                or not callable(scope_resolver)
                or not callable(peer_resolver)
            ):
                raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPLETE)
            try:
                resolved_scope = scope_resolver(binding["scope_ref"])
                resolved_peer = peer_resolver(binding["service_id"])
            except Exception:
                raise MigrationBoundaryError(CODE_BOUNDARY_UNAVAILABLE) from None
            if type(resolved_scope) is not WireScope or type(resolved_peer) is not WireIdentity:
                raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
            operations = None if requested_operations is None else requested_operations.get(mount)
            if operations is None:
                # Match the production runtime's code-owned default intent.
                # Actual remote capability availability is checked by normal
                # registration/readiness before the destination is enabled.
                ports = MOUNT_PORTS[mount]
                default_operations = frozenset().union(
                    *(PORT_OPERATIONS[port] for port in ports)
                )
                if "service.account_read" in ports:
                    default_operations = (
                        default_operations - PORT_OPERATIONS["service.account_read"]
                    ) | _DEFAULT_ACCOUNT_READ_OPERATIONS
                if "service.telemetry_ingress" in ports:
                    default_operations = (
                        default_operations - PORT_OPERATIONS["service.telemetry_ingress"]
                    ) | _DEFAULT_TELEMETRY_INGRESS_OPERATIONS
                if mount == "broker.group_monitor":
                    default_operations = _GROUP_MONITOR_OPERATIONS
                if "service.order_query" in ports:
                    default_operations -= {
                        "execution.account_books.query",
                        "orders.query_group_leg",
                    }
                if "service.order_entry" in ports:
                    default_operations -= (
                        _GROUP_ORDER_ENTRY_OPERATIONS | _APPROVED_ORDER_ENTRY_OPERATIONS
                    )
                operations = default_operations
            allowed = frozenset().union(
                *(
                    PORT_OPERATIONS[port]
                    for port in MOUNT_PORTS[mount] + OPTIONAL_MOUNT_PORTS.get(mount, ())
                )
            )
            if type(operations) is not frozenset or not operations or not operations.issubset(
                allowed
            ):
                raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
            rows.append(
                (
                    "configured_outbound_binding",
                    {
                        "kind": "configured_outbound_binding",
                        "client_name": client_name,
                        "binding": binding,
                        "resolved_scope": _wire_scope_document(resolved_scope),
                        "resolved_peer": _wire_identity_document(resolved_peer),
                        "operations": sorted(operations),
                    },
                )
            )

    if not rows:
        raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPLETE)
    consumers: list[ConfiguredConsumer] = []
    seen: set[str] = set()
    for consumer_kind, document in rows:
        scope_document = document.get("scope", document.get("resolved_scope"))
        operations = document.get("operations", ())
        scope_sha256 = hashlib.sha256(_canonical_json_bytes(scope_document)).hexdigest()
        operations_sha256 = hashlib.sha256(
            _canonical_json_bytes(sorted(operations))
        ).hexdigest()
        configuration_sha256 = hashlib.sha256(_canonical_json_bytes(document)).hexdigest()
        consumer_id = "sdk-consumer:" + hashlib.sha256(
            _canonical_json_bytes(
                {
                    "kind": consumer_kind,
                    "configuration_sha256": configuration_sha256,
                }
            )
        ).hexdigest()
        if consumer_id in seen:
            raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
        seen.add(consumer_id)
        consumers.append(
            ConfiguredConsumer(
                consumer_id=consumer_id,
                consumer_kind=consumer_kind,
                configuration_sha256=configuration_sha256,
                scope_sha256=scope_sha256,
                operations_sha256=operations_sha256,
            )
        )
    # Bind the entire parsed deployment as well, so a scope/config freeze
    # cannot silently reuse individually identical consumer IDs after a
    # service-level policy or component change.
    return tuple(
        sorted(
            (
                ConfiguredConsumer(
                    consumer_id=item.consumer_id,
                    consumer_kind=item.consumer_kind,
                    configuration_sha256=hashlib.sha256(
                        _canonical_json_bytes(
                            {
                                "service_configuration_sha256": service_configuration_sha256,
                                "consumer_configuration_sha256": item.configuration_sha256,
                            }
                        )
                    ).hexdigest(),
                    scope_sha256=item.scope_sha256,
                    operations_sha256=item.operations_sha256,
                )
                for item in consumers
            ),
            key=lambda item: item.consumer_id,
        )
    )


def require_consumer_inventory_compatible(
    source: Iterable[ConfiguredConsumer], destination: Iterable[ConfiguredConsumer]
) -> None:
    """Reject a destination whose consumers, scopes, or grant operations drift."""

    source_rows = tuple(source)
    destination_rows = tuple(destination)
    if (
        not source_rows
        or any(type(item) is not ConfiguredConsumer for item in (*source_rows, *destination_rows))
        or _configured_consumer_documents(source_rows)
        != _configured_consumer_documents(destination_rows)
    ):
        raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)


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
    source_configured_consumers: Iterable[ConfiguredConsumer] | None = None,
    destination_configured_consumers: Iterable[ConfiguredConsumer] | None = None,
) -> BoundaryPlan:
    """Validate complete pre-arm inventory, old-writer exit and consumer stop facts."""

    for value in (migration_id, source_epoch, destination_epoch, fence_reference):
        _require_text(value)
    if source_epoch == destination_epoch:
        raise MigrationBoundaryError(CODE_BOUNDARY_INVALID)
    if source_configured_consumers is None or destination_configured_consumers is None:
        raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPLETE)
    configured_consumers = tuple(source_configured_consumers)
    destination_inventory = tuple(destination_configured_consumers)
    require_consumer_inventory_compatible(configured_consumers, destination_inventory)
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
    expected_consumer_ids = {item.consumer_id for item in configured_consumers}
    if consumer_ids != expected_consumer_ids:
        raise MigrationBoundaryError(CODE_BOUNDARY_NOT_QUIESCENT)

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
    if by_id["consumers"].configuration_sha256 != (
        configured_consumer_inventory_sha256(configured_consumers)
    ):
        raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)

    ordered_participants = tuple(by_id[key] for key in sorted(by_id))
    ordered_consumers = tuple(sorted(stopped_consumers, key=lambda item: item.consumer_id))
    digest_payload = {
        "migration_id": migration_id,
        "source_epoch": source_epoch,
        "destination_epoch": destination_epoch,
        "old_writer_pid": old_writer_pid,
        "old_writer_exit_code": old_writer_exit_code,
        "consumers": [_consumer_document(item) for item in ordered_consumers],
        "configured_consumers": _configured_consumer_documents(configured_consumers),
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
        configured_consumers=configured_consumers,
    )


def arm_migration_boundary(
    state_path: str | Path,
    plan: BoundaryPlan,
    *,
    source_configured_consumers: Iterable[ConfiguredConsumer] | None = None,
    destination_configured_consumers: Iterable[ConfiguredConsumer] | None = None,
) -> MigrationCutoverMarker:
    """Arm the existing cutover marker only after full boundary preflight."""

    if type(plan) is not BoundaryPlan:
        raise MigrationBoundaryError(CODE_BOUNDARY_INVALID)
    if source_configured_consumers is None or destination_configured_consumers is None:
        raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPLETE)
    verified = prepare_migration_boundary(
        migration_id=plan.migration_id,
        source_epoch=plan.source_epoch,
        destination_epoch=plan.destination_epoch,
        old_writer_pid=plan.old_writer_pid,
        old_writer_exit_code=plan.old_writer_exit_code,
        consumers=plan.consumers,
        participants=plan.participants,
        fence_reference=plan.fence_reference,
        source_configured_consumers=source_configured_consumers,
        destination_configured_consumers=destination_configured_consumers,
    )
    if (
        verified.inventory_sha256 != plan.inventory_sha256
        or verified.configured_consumers != plan.configured_consumers
    ):
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


def configured_consumer_inventory_sha256(
    consumers: Iterable[ConfiguredConsumer],
) -> str:
    """Fingerprint the exact production-derived consumer/scope inventory."""

    return hashlib.sha256(
        _canonical_json_bytes(_configured_consumer_documents(tuple(consumers)))
    ).hexdigest()


def _configured_consumer_documents(
    consumers: tuple[ConfiguredConsumer, ...],
) -> list[dict[str, str]]:
    if type(consumers) is not tuple or not consumers:
        raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPLETE)
    documents: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in consumers:
        if type(item) is not ConfiguredConsumer:
            raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
        _require_text(item.consumer_id)
        _require_text(item.consumer_kind)
        for digest in (
            item.configuration_sha256,
            item.scope_sha256,
            item.operations_sha256,
        ):
            _require_sha256(digest)
        if item.consumer_id in seen:
            raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
        seen.add(item.consumer_id)
        documents.append(
            {
                "consumer_id": item.consumer_id,
                "consumer_kind": item.consumer_kind,
                "configuration_sha256": item.configuration_sha256,
                "scope_sha256": item.scope_sha256,
                "operations_sha256": item.operations_sha256,
            }
        )
    return sorted(documents, key=lambda item: item["consumer_id"])


def _wire_scope_document(scope: Any) -> dict[str, Any]:
    from bt_api_base.service_protocol import WireScope

    if type(scope) is not WireScope:
        raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
    return {
        "execution_domain_ref": scope.execution_domain_ref,
        "account_ref": scope.account_ref,
        "account_refs": list(scope.account_refs),
        "strategy_ref": scope.strategy_ref,
        "run_id": scope.run_id,
        "manual_book_ref": scope.manual_book_ref,
        "portfolio_ref": scope.portfolio_ref,
        "group_id": scope.group_id,
        "leg_id": scope.leg_id,
    }


def _service_consumer_config_document(raw: dict[str, Any]) -> dict[str, Any]:
    """Keep logical deployment config while allowing listener address rotation."""

    document = json.loads(_canonical_json_bytes(raw))
    communication = document.get("communication")
    profiles = communication.get("profiles") if type(communication) is dict else None
    if type(profiles) is not dict:
        raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
    for profile in profiles.values():
        if type(profile) is not dict:
            raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
        endpoints = profile.get("endpoints")
        if type(endpoints) is not dict:
            raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
        # A different bound address is expected during a controlled handoff.
        # Endpoint roles, profile refs, security config and all logical routes
        # remain pinned and are checked by the Host/normal client readiness.
        for endpoint_name in tuple(endpoints):
            endpoints[endpoint_name] = {"endpoint_role": endpoint_name}
    return document


def _wire_identity_document(identity: Any) -> dict[str, str | None]:
    from bt_api_base.service_protocol import WireIdentity

    if type(identity) is not WireIdentity:
        raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
    return {
        "role": identity.role.value,
        "principal_ref": identity.principal_ref,
        "actor_ref": identity.actor_ref,
        "delegate_ref": identity.delegate_ref,
    }


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


def _capture_group_run_bindings(
    path: str | Path,
    *,
    max_rows: int,
    max_bytes: int,
) -> dict[str, Any]:
    """Read the coordinator's fixed sidecar schemas without writes.

    The accepted coordinator supports one run-attribution table. Newer
    coordinator builds also create fixed cancel/roll journals in this same
    SQLite file. Their schemas are inventoried, but this MIG03 slice has no
    typed transfer contract for their records, so any such row fails closed.
    """

    db_path = Path(path)
    try:
        info = db_path.stat()
    except (FileNotFoundError, OSError):
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
        connection.execute("BEGIN")
        quick_check = tuple(row[0] for row in connection.execute("PRAGMA quick_check"))
        if not quick_check or any(value != "ok" for value in quick_check):
            raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
        tables = tuple(
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        )
        optional_tables = tuple(
            sorted(
                set(_GROUP_COORDINATOR_SIDE_TABLE_SQL)
                - {"coordinator_group_roll_exit_source_idx"}
            )
        )
        legacy_tables = (_GROUP_RUN_BINDING_TABLE,)
        current_tables = tuple(sorted((*legacy_tables, *optional_tables)))
        if tables not in {legacy_tables, current_tables}:
            raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
        schema_objects = tuple(
            tuple(row)
            for row in connection.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
            )
        )
        object_keys = tuple((row[0], row[1]) for row in schema_objects)
        legacy_object_keys = (("table", _GROUP_RUN_BINDING_TABLE),)
        current_object_keys = tuple(
            sorted(
                (
                    ("table", _GROUP_RUN_BINDING_TABLE),
                    *(("table", name) for name in optional_tables),
                    ("index", "coordinator_group_roll_exit_source_idx"),
                )
            )
        )
        current_schema = tables == current_tables
        if object_keys != (current_object_keys if current_schema else legacy_object_keys):
            raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
        if current_schema:
            expected_table_names = {
                **{
                    name: name
                    for name in optional_tables
                },
                "coordinator_group_roll_exit_source_idx": "coordinator_group_roll_exit_actions",
            }
            for object_type, name, table_name, sql in schema_objects:
                if name == _GROUP_RUN_BINDING_TABLE:
                    if object_type != "table" or table_name != _GROUP_RUN_BINDING_TABLE:
                        raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
                    continue
                expected_table_name = expected_table_names.get(name)
                expected_sql = _GROUP_COORDINATOR_SIDE_TABLE_SQL.get(name)
                if (
                    expected_table_name is None
                    or expected_sql is None
                    or table_name != expected_table_name
                    or type(sql) is not str
                    or " ".join(sql.lower().split())
                    != " ".join(expected_sql.lower().split())
                ):
                    raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
        columns = tuple(
            connection.execute(f"PRAGMA table_info({_quote_identifier(_GROUP_RUN_BINDING_TABLE)})")
        )
        expected_columns = (
            ("portfolio_ref", "TEXT", 1, 1),
            ("group_id", "TEXT", 1, 2),
            ("request_fingerprint", "TEXT", 1, 0),
            ("binding_json", "TEXT", 1, 0),
            ("updated_at_ns", "INTEGER", 1, 0),
        )
        actual_columns = tuple(
            (row[1], row[2].upper(), int(row[3]), int(row[5])) for row in columns
        )
        if actual_columns != expected_columns:
            raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
        side_table_columns: dict[str, tuple[tuple[str, str, int, int], ...]] = {}
        for table in optional_tables if current_schema else ():
            table_columns = tuple(
                connection.execute(f"PRAGMA table_info({_quote_identifier(table)})")
            )
            if not table_columns:
                raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
            side_table_columns[table] = tuple(
                (row[1], row[2].upper(), int(row[3]), int(row[5]))
                for row in table_columns
            )
            if connection.execute(
                f"SELECT 1 FROM {_quote_identifier(table)} LIMIT 1"
            ).fetchone() is not None:
                raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)

        schema_document: dict[str, Any] = {
            "sqlite_master": schema_objects,
            _GROUP_RUN_BINDING_TABLE: actual_columns,
        }
        if side_table_columns:
            schema_document["coordinator_side_tables"] = side_table_columns
        schema_payload = _canonical_json_bytes(schema_document)
        if len(schema_payload) > max_bytes:
            raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
        cursor = connection.execute(
            "SELECT portfolio_ref,group_id,request_fingerprint,binding_json,updated_at_ns "
            f"FROM {_quote_identifier(_GROUP_RUN_BINDING_TABLE)} "
            "ORDER BY portfolio_ref,group_id"
        )
        row_payloads: list[bytes] = []
        identity_rows: list[tuple[str, str, str, str, tuple[str, ...]]] = []
        total_rows = 0
        total_bytes = len(schema_payload)
        from .service_execution_group_client import _coordinator_binding_from_json

        while True:
            batch = cursor.fetchmany(min(128, max_rows - total_rows + 1))
            if not batch:
                break
            for row in batch:
                total_rows += 1
                if total_rows > max_rows:
                    raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
                values = tuple(row)
                if (
                    type(values[0]) is not str
                    or not values[0]
                    or type(values[1]) is not str
                    or not values[1]
                    or type(values[2]) is not str
                    or type(values[3]) is not str
                    or type(values[4]) is not int
                    or values[4] <= 0
                ):
                    raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
                _require_sha256(values[2])
                try:
                    payload = json.loads(values[3])
                    if _canonical_json_bytes(payload).decode("utf-8") != values[3]:
                        raise ValueError("noncanonical binding")
                    binding = _coordinator_binding_from_json(values[3])
                except Exception:
                    raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE) from None
                if (
                    binding.request_fingerprint != values[2]
                    or binding.request.portfolio_ref != values[0]
                    or binding.request.group_id != values[1]
                ):
                    raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
                row_payload = _canonical_json_bytes([_sqlite_value(item) for item in values])
                total_bytes += len(row_payload)
                if total_bytes > max_bytes:
                    raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
                row_payloads.append(row_payload)
                identity_rows.append(
                    (
                        values[0],
                        values[1],
                        values[2],
                        binding.origin_run_id,
                        tuple(leg.maximum_intent.intent_id for leg in binding.request.legs),
                    )
                )
        schema_digest = hashlib.sha256(schema_payload).hexdigest()
        state_rows = [
            (_GROUP_RUN_BINDING_TABLE, tuple(sorted(row_payloads))),
            *((table, ()) for table in optional_tables if current_schema),
        ]
        state_digest = _sqlite_state_sha256(
            schema_payload,
            state_rows,
        )
        identity_digest = hashlib.sha256(
            _canonical_json_bytes(sorted(identity_rows))
        ).hexdigest()
        return {
            "source_ref": str(db_path.resolve()),
            "size_bytes": _sqlite_storage_size(db_path, max_bytes=max_bytes),
            "schema_sha256": schema_digest,
            "state_sha256": state_digest,
            "identity_sha256": identity_digest,
            "row_count": total_rows,
            "table_names": (_GROUP_RUN_BINDING_TABLE, *optional_tables) if current_schema else legacy_tables,
        }
    except MigrationBoundaryError:
        raise
    except (sqlite3.Error, OSError, ValueError, TypeError, UnicodeError):
        raise MigrationBoundaryError(CODE_BOUNDARY_UNAVAILABLE) from None
    finally:
        if connection is not None:
            connection.close()


def _sqlite_storage_size(path: str | Path, *, max_bytes: int) -> int:
    """Bound a SQLite database together with its WAL/journal sidecars."""

    db_path = Path(path)
    try:
        main = db_path.stat()
    except OSError:
        raise MigrationBoundaryError(CODE_BOUNDARY_UNAVAILABLE) from None
    if not stat.S_ISREG(main.st_mode) or main.st_size <= 0:
        raise MigrationBoundaryError(CODE_BOUNDARY_UNAVAILABLE)
    total = main.st_size
    if total > max_bytes:
        raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = db_path.with_name(db_path.name + suffix)
        try:
            info = sidecar.stat()
        except FileNotFoundError:
            continue
        except OSError:
            raise MigrationBoundaryError(CODE_BOUNDARY_UNAVAILABLE) from None
        if not stat.S_ISREG(info.st_mode):
            raise MigrationBoundaryError(CODE_BOUNDARY_UNAVAILABLE)
        total += info.st_size
        if total > max_bytes:
            raise MigrationBoundaryError(CODE_BOUNDARY_INCOMPATIBLE)
    return total


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
