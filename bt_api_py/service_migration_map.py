"""Durable persistent legacy-identity mapping for MIG03 / iteration plan L854.

The iteration-10 formal plan row at L854 requires, for the controlled migration
of legacy identities, a conflict-checked persistent mapping with historical
association: "旧 strategy_id、裸 symbol/order_id、进程内 run 与账户配置 |
建立有冲突检查的持久映射及历史关联；无法判定归属保留未分配/待对账，不改名后
重新授信或重新下单".  Increments A (first-write seal gate) and B (two-epoch
drill) prove the negative half only (a rename is refused, unattributable facts
stay in place); the independent flip audit (finding F1) showed the positive
mapping itself had zero implementation and zero assertions.  This module is
that missing positive store.

Five legacy identity kinds map onto five new-identity kinds:

* ``legacy_strategy_id`` -> ``strategy_ref``        (one-to-one: a strategy is
  one strategy);
* ``bare_symbol``        -> ``booked_instrument``  (many-to-one: bare
  spellings fan in as aliases of the booked instrument);
* ``bare_order_id``      -> ``order_ref``          (one-to-one: an order is
  one order);
* ``in_process_run``     -> ``run_ref``            (one-to-one: a process run
  is one run);
* ``account_config``     -> ``account_key``        (many-to-one: configuration
  revisions fan in on the same account key).

Rules enforced by the store (durable, SQLite, standard library only):

* registering the same source with the same target is an idempotent replay
  returning the original record unchanged: provenance (``recorded_at``,
  ``recorded_epoch``, ``note``) of the first recording is retained and never
  overwritten;
* registering the same source with a *different* target refuses
  ``SOURCE_ALREADY_MAPPED`` and mutates nothing (contradictory rename;
  nothing is renamed-then-recredited or renamed-then-reordered);
* binding an *exclusive* target kind (``strategy_ref`` / ``order_ref`` /
  ``run_ref``) that is already held by another source refuses
  ``TARGET_ALREADY_BOUND`` and mutates nothing (ambiguity: two identities must
  never share one ref, so no second order partition and no duplicate
  credit/order target can ever be minted);
* resolving an unknown source never invents an identity: it returns status
  ``UNALLOCATED`` with the 待对账 reconciliation-queue marker, and repeated
  resolution or reopen keeps it unallocated;
* there is no update, delete, rename or reassign API at all: the only write is
  ``register`` and the only transition is absent -> mapped.

The store is fail-closed: an existing file that is not a compatible mapping
database raises ``MigrationMapError(MIGRATION_MAP_UNREADABLE)`` instead of
being silently re-created or repaired.  Errors carry a machine code only (no
identity values), mirroring ``service_migration_gate.MigrationGateError``.

DISCLOSED SCOPE (increment C, new files only): this module is the durable
backing store for the L854 positive mapping; it is not yet wired into a
production caller (host start path or an import step) and it does not itself
hold risk-quota ledgers.  The partition invariant it proves is the identity
one: at most one target per legacy source and at most one source per exclusive
target.
"""

from __future__ import annotations

import sqlite3
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MAPPING_SCHEMA_VERSION = 1
MAPPING_DATABASE_SUFFIX = ".migration-identity-map.sqlite3"

TABLE_IDENTITY_MAP = "migration_identity_map"
TABLE_METADATA = "migration_map_metadata"

SOURCE_LEGACY_STRATEGY_ID = "legacy_strategy_id"
SOURCE_BARE_SYMBOL = "bare_symbol"
SOURCE_BARE_ORDER_ID = "bare_order_id"
SOURCE_IN_PROCESS_RUN = "in_process_run"
SOURCE_ACCOUNT_CONFIG = "account_config"

LEGACY_SOURCE_KINDS = frozenset(
    {
        SOURCE_LEGACY_STRATEGY_ID,
        SOURCE_BARE_SYMBOL,
        SOURCE_BARE_ORDER_ID,
        SOURCE_IN_PROCESS_RUN,
        SOURCE_ACCOUNT_CONFIG,
    }
)

TARGET_STRATEGY_REF = "strategy_ref"
TARGET_BOOKED_INSTRUMENT = "booked_instrument"
TARGET_ORDER_REF = "order_ref"
TARGET_RUN_REF = "run_ref"
TARGET_ACCOUNT_KEY = "account_key"

TARGET_KIND_BY_SOURCE = {
    SOURCE_LEGACY_STRATEGY_ID: TARGET_STRATEGY_REF,
    SOURCE_BARE_SYMBOL: TARGET_BOOKED_INSTRUMENT,
    SOURCE_BARE_ORDER_ID: TARGET_ORDER_REF,
    SOURCE_IN_PROCESS_RUN: TARGET_RUN_REF,
    SOURCE_ACCOUNT_CONFIG: TARGET_ACCOUNT_KEY,
}

TARGET_KINDS = frozenset(TARGET_KIND_BY_SOURCE.values())

# A new identity of these kinds denotes exactly one legacy identity (a strategy
# ref, an order ref or a run ref must never be claimed twice).  Booked
# instruments and account keys are fan-in targets: several bare spellings or
# configuration revisions legitimately resolve onto the same booked value.
EXCLUSIVE_TARGET_KINDS = frozenset({TARGET_STRATEGY_REF, TARGET_ORDER_REF, TARGET_RUN_REF})

RESOLUTION_RESOLVED = "RESOLVED"
RESOLUTION_UNALLOCATED = "UNALLOCATED"

# The plan's untranslatable-attribution bucket (L854 "未分配/待对账"): an
# unresolved identity is retained as UNALLOCATED on the 待对账 queue and is
# never auto-credited, auto-reordered or auto-renamed.
RECONCILIATION_QUEUE = "待对账"

CODE_SOURCE_ALREADY_MAPPED = "SOURCE_ALREADY_MAPPED"
CODE_TARGET_ALREADY_BOUND = "TARGET_ALREADY_BOUND"
CODE_INVALID_MIGRATION_MAP_ARGUMENT = "INVALID_MIGRATION_MAP_ARGUMENT"
CODE_MIGRATION_MAP_UNREADABLE = "MIGRATION_MAP_UNREADABLE"
CODE_MIGRATION_MAP_BACKEND_FAILURE = "MIGRATION_MAP_BACKEND_FAILURE"

_CREATE_IDENTITY_MAP_SQL = f"""
CREATE TABLE {TABLE_IDENTITY_MAP} (
    source_kind TEXT NOT NULL,
    source_value TEXT NOT NULL,
    target_kind TEXT NOT NULL,
    target_value TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    recorded_epoch TEXT,
    note TEXT,
    PRIMARY KEY (source_kind, source_value)
)
"""

_CREATE_TARGET_INDEX_SQL = (
    f"CREATE INDEX migration_identity_map_target "
    f"ON {TABLE_IDENTITY_MAP}(target_kind, target_value)"
)

_CREATE_METADATA_SQL = f"""
CREATE TABLE {TABLE_METADATA} (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
"""

# The column list is inlined literally in every statement (never interpolated)
# so no statement is built from outside values; identity values are always
# bound parameters.
_INSERT_MAPPING_SQL = (
    "INSERT INTO migration_identity_map "
    "(source_kind, source_value, target_kind, target_value, recorded_at, "
    "recorded_epoch, note) VALUES (?, ?, ?, ?, ?, ?, ?)"
)

_SELECT_MAPPING_BY_SOURCE_SQL = (
    "SELECT source_kind, source_value, target_kind, target_value, recorded_at, "
    "recorded_epoch, note FROM migration_identity_map "
    "WHERE source_kind = ? AND source_value = ?"
)

_SELECT_MAPPING_BY_TARGET_SQL = (
    "SELECT source_kind, source_value, target_kind, target_value, recorded_at, "
    "recorded_epoch, note FROM migration_identity_map "
    "WHERE target_kind = ? AND target_value = ? "
    "ORDER BY source_kind, source_value"
)


class MigrationMapError(RuntimeError):
    """Finite, value-free mapping-store error carrying a machine code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class IdentityMapping:
    """One durable legacy-identity -> new-identity association."""

    source_kind: str
    source_value: str
    target_kind: str
    target_value: str
    recorded_at: str
    recorded_epoch: str | None = None
    note: str | None = None


@dataclass(frozen=True, slots=True)
class IdentityResolution:
    """Outcome of resolving one legacy identity against the store.

    ``UNALLOCATED`` is the plan's "未分配/待对账" retention: the identity is
    unknown to the mapping, no new identity was invented for it, and
    ``reconciliation_queue`` names the queue it waits on.
    """

    status: str
    source_kind: str
    source_value: str
    mapping: IdentityMapping | None
    reconciliation_queue: str | None

    @property
    def resolved(self) -> bool:
        return self.status == RESOLUTION_RESOLVED

    @property
    def pending_reconciliation(self) -> bool:
        return self.status == RESOLUTION_UNALLOCATED

    @property
    def target_kind(self) -> str | None:
        return None if self.mapping is None else self.mapping.target_kind

    @property
    def target_value(self) -> str | None:
        return None if self.mapping is None else self.mapping.target_value


def identity_map_path(state_path: str) -> Path:
    """Return the mapping-database location beside one protected state file."""

    return Path(str(state_path) + MAPPING_DATABASE_SUFFIX)


class MigrationIdentityStore:
    """Durable SQLite-backed store for the L854 legacy->new identity mapping."""

    def __init__(self, path: str | Path, *, busy_timeout_ms: int = 5000) -> None:
        if type(busy_timeout_ms) is not int or busy_timeout_ms <= 0:
            raise MigrationMapError(CODE_INVALID_MIGRATION_MAP_ARGUMENT)
        self._path = Path(path)
        self._connection: sqlite3.Connection | None = None
        self._path.parent.mkdir(parents=True, exist_ok=True)
        connection = _connect(self._path, busy_timeout_ms)
        try:
            _ensure_schema(connection)
        except BaseException:
            connection.close()
            raise
        self._connection = connection

    # -- lifecycle ---------------------------------------------------------

    @property
    def path(self) -> Path:
        return self._path

    def close(self) -> None:
        """Close the store; idempotent.  The database file is durable."""

        connection = self._connection
        if connection is None:
            return
        self._connection = None
        connection.close()

    def __enter__(self) -> MigrationIdentityStore:
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    # -- write surface (the only write; no update/delete/rename exists) ----

    def register(
        self,
        source_kind: str,
        source_value: str,
        target_value: str,
        *,
        recorded_epoch: str | None = None,
        note: str | None = None,
        recorded_at: str | None = None,
    ) -> IdentityMapping:
        """Register one legacy identity -> new identity mapping.

        The target kind is fixed by the source kind (one identity category per
        legacy kind).  Replaying the same source+target pair is idempotent and
        returns the original record unchanged; a contradictory target refuses
        ``SOURCE_ALREADY_MAPPED``; an already held exclusive target refuses
        ``TARGET_ALREADY_BOUND``.  Both refusals mutate nothing.
        """

        _require_source_kind(source_kind)
        _require_text(source_value)
        _require_text(target_value)
        if recorded_epoch is not None:
            _require_text(recorded_epoch)
        if note is not None:
            _require_text(note)
        if recorded_at is not None:
            _require_text(recorded_at)
        target_kind = TARGET_KIND_BY_SOURCE[source_kind]
        timestamp = recorded_at if recorded_at is not None else _utc_now_text()
        connection = self._connection_or_raise()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = self._select_source(source_kind, source_value)
            if existing is not None and existing.target_value == target_value:
                connection.execute("COMMIT")
                return existing
            if existing is not None:
                raise MigrationMapError(CODE_SOURCE_ALREADY_MAPPED)
            if target_kind in EXCLUSIVE_TARGET_KINDS:
                bound = self._select_target(target_kind, target_value)
                if bound is not None:
                    raise MigrationMapError(CODE_TARGET_ALREADY_BOUND)
            connection.execute(
                _INSERT_MAPPING_SQL,
                (
                    source_kind,
                    source_value,
                    target_kind,
                    target_value,
                    timestamp,
                    recorded_epoch,
                    note,
                ),
            )
            connection.execute("COMMIT")
        except MigrationMapError:
            _rollback(connection)
            raise
        except sqlite3.Error:
            _rollback(connection)
            raise MigrationMapError(CODE_MIGRATION_MAP_BACKEND_FAILURE) from None
        except BaseException:
            _rollback(connection)
            raise
        return IdentityMapping(
            source_kind=source_kind,
            source_value=source_value,
            target_kind=target_kind,
            target_value=target_value,
            recorded_at=timestamp,
            recorded_epoch=recorded_epoch,
            note=note,
        )

    # -- read surface ------------------------------------------------------

    def resolve(self, source_kind: str, source_value: str) -> IdentityResolution:
        """Resolve one legacy identity without ever mutating the store."""

        _require_source_kind(source_kind)
        _require_text(source_value)
        mapping = self._select_source(source_kind, source_value)
        if mapping is None:
            return IdentityResolution(
                status=RESOLUTION_UNALLOCATED,
                source_kind=source_kind,
                source_value=source_value,
                mapping=None,
                reconciliation_queue=RECONCILIATION_QUEUE,
            )
        return IdentityResolution(
            status=RESOLUTION_RESOLVED,
            source_kind=source_kind,
            source_value=source_value,
            mapping=mapping,
            reconciliation_queue=None,
        )

    def history(self, source_kind: str, source_value: str) -> tuple[IdentityMapping, ...]:
        """Return the retained historical association for one legacy identity.

        The result is empty for an unallocated identity and a one-tuple for a
        mapped one: the mapping is append-only, so the authoritative record is
        also the whole history.  It stays queryable after cutover and reopen.
        """

        _require_source_kind(source_kind)
        _require_text(source_value)
        mapping = self._select_source(source_kind, source_value)
        return () if mapping is None else (mapping,)

    def bindings_for_target(self, target_kind: str, target_value: str) -> tuple[IdentityMapping, ...]:
        """Return every legacy identity bound to one new identity."""

        _require_target_kind(target_kind)
        _require_text(target_value)
        rows = self._fetchall(
            _SELECT_MAPPING_BY_TARGET_SQL,
            (target_kind, target_value),
        )
        return tuple(_mapping_from_row(row) for row in rows)

    def bound_target_count(self, source_kind: str, source_value: str) -> int:
        """Distinct targets bound to one legacy identity (0 or 1 by design).

        The order-partition invariant: one legacy order (or strategy/run) can
        never acquire a second target, so this can never exceed 1.
        """

        _require_source_kind(source_kind)
        _require_text(source_value)
        row = self._fetchone(
            "SELECT COUNT(DISTINCT target_value) FROM migration_identity_map "
            "WHERE source_kind = ? AND source_value = ?",
            (source_kind, source_value),
        )
        return int(row[0])

    def bound_source_count(self, target_kind: str, target_value: str) -> int:
        """Legacy identities bound to one new identity.

        For exclusive target kinds this is at most 1 (the no-duplicate-target
        invariant); fan-in kinds may legitimately exceed 1.
        """

        _require_target_kind(target_kind)
        _require_text(target_value)
        row = self._fetchone(
            "SELECT COUNT(*) FROM migration_identity_map "
            "WHERE target_kind = ? AND target_value = ?",
            (target_kind, target_value),
        )
        return int(row[0])

    def registration_count(self) -> int:
        """Total durable mapping rows (invariant surface for no-mutation proofs)."""

        row = self._fetchone("SELECT COUNT(*) FROM migration_identity_map", ())
        return int(row[0])

    # -- internals ---------------------------------------------------------

    def _connection_or_raise(self) -> sqlite3.Connection:
        connection = self._connection
        if connection is None:
            raise MigrationMapError(CODE_MIGRATION_MAP_BACKEND_FAILURE)
        return connection

    def _select_source(self, source_kind: str, source_value: str) -> IdentityMapping | None:
        row = self._fetchone(
            _SELECT_MAPPING_BY_SOURCE_SQL,
            (source_kind, source_value),
        )
        return None if row is None else _mapping_from_row(row)

    def _select_target(self, target_kind: str, target_value: str) -> IdentityMapping | None:
        row = self._fetchone(
            _SELECT_MAPPING_BY_TARGET_SQL,
            (target_kind, target_value),
        )
        return None if row is None else _mapping_from_row(row)

    def _fetchone(self, sql: str, params: tuple[Any, ...]) -> Any:
        try:
            return self._connection_or_raise().execute(sql, params).fetchone()
        except sqlite3.Error:
            raise MigrationMapError(CODE_MIGRATION_MAP_BACKEND_FAILURE) from None

    def _fetchall(self, sql: str, params: tuple[Any, ...]) -> list[Any]:
        try:
            return self._connection_or_raise().execute(sql, params).fetchall()
        except sqlite3.Error:
            raise MigrationMapError(CODE_MIGRATION_MAP_BACKEND_FAILURE) from None


def _connect(path: Path, busy_timeout_ms: int) -> sqlite3.Connection:
    """Open the durable connection with the SDK store's pragma shape."""

    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            str(path), isolation_level=None, timeout=busy_timeout_ms / 1000.0
        )
        connection.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
    except sqlite3.Error:
        if connection is not None:
            connection.close()
        raise MigrationMapError(CODE_MIGRATION_MAP_UNREADABLE) from None
    return connection


def _ensure_schema(connection: sqlite3.Connection) -> None:
    """Fail closed on anything that is not a fresh or compatible mapping store."""

    try:
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if TABLE_METADATA in tables:
            version_row = connection.execute(
                "SELECT value FROM migration_map_metadata WHERE key = 'schema_version'"
            ).fetchone()
            if (
                version_row is None
                or version_row[0] != str(MAPPING_SCHEMA_VERSION)
                or TABLE_IDENTITY_MAP not in tables
                or user_version != MAPPING_SCHEMA_VERSION
            ):
                raise MigrationMapError(CODE_MIGRATION_MAP_UNREADABLE)
            return
        if tables or user_version != 0:
            raise MigrationMapError(CODE_MIGRATION_MAP_UNREADABLE)
        connection.execute(_CREATE_IDENTITY_MAP_SQL)
        connection.execute(_CREATE_TARGET_INDEX_SQL)
        connection.execute(_CREATE_METADATA_SQL)
        connection.execute(
            "INSERT INTO migration_map_metadata (key, value) "
            "VALUES ('schema_version', ?)",
            (str(MAPPING_SCHEMA_VERSION),),
        )
        connection.execute(f"PRAGMA user_version = {MAPPING_SCHEMA_VERSION}")
    except MigrationMapError:
        raise
    except sqlite3.Error:
        raise MigrationMapError(CODE_MIGRATION_MAP_UNREADABLE) from None


def _rollback(connection: sqlite3.Connection) -> None:
    with suppress(sqlite3.Error):
        connection.execute("ROLLBACK")


def _mapping_from_row(row: Any) -> IdentityMapping:
    return IdentityMapping(
        source_kind=row[0],
        source_value=row[1],
        target_kind=row[2],
        target_value=row[3],
        recorded_at=row[4],
        recorded_epoch=row[5],
        note=row[6],
    )


def _require_text(value: Any) -> None:
    if type(value) is not str or not value:
        raise MigrationMapError(CODE_INVALID_MIGRATION_MAP_ARGUMENT)


def _require_source_kind(value: Any) -> None:
    if type(value) is not str or value not in LEGACY_SOURCE_KINDS:
        raise MigrationMapError(CODE_INVALID_MIGRATION_MAP_ARGUMENT)


def _require_target_kind(value: Any) -> None:
    if type(value) is not str or value not in TARGET_KINDS:
        raise MigrationMapError(CODE_INVALID_MIGRATION_MAP_ARGUMENT)


def _utc_now_text() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
