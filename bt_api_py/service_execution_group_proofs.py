"""Durable provider-source proofs for the Execution group-order boundary.

This module is deliberately separate from both Execution's order ledger and
RiskOrderEntry's single-order journal.  It records the one actual group
provider attempt and the normalized provider order/fill facts obtained by the
same call-local dispatcher.  It never retries provider writes and never turns
an ExecutionRecord aggregate into a source revision, fill row, or fee fact.

The service composition creates ``GroupProofSource`` before constructing the
dedicated group ``DurableRiskGate`` (the gate receives this object as its
``execution_journal_authority``), then creates the Risk and account-scoped
group adapters and finally binds them in ``GroupProofRuntimeBundle``.  The
normal ServiceHost adapter is the only caller of ``bind_dispatch``.
"""

from __future__ import annotations

import contextvars
import json
import math
import re
import sqlite3
import threading
import time
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from types import MappingProxyType
from typing import Any

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution import (
    AccountScopedGroupAdmissionAdapter,
    ExecutionRecord,
    ExecutionScope,
    ExecutionState,
    GroupChildOrderStatus,
    GroupDispatchResolutionReceipt,
    GroupLegAuthorization,
    GroupLegClaimReceipt,
    GroupLegOrderReceipt,
    GroupLegPermitReference,
    GroupPermitBinding,
    OrderIntent,
    OrderType,
    PositionEffect,
    ProviderObservation,
    SqliteExecutionStore,
    TimeInForce,
    WriterLease,
    risk_account_scope_fingerprint,
    writer_lease_fingerprint,
)
from bt_api_risk import (
    INSTRUMENT_METADATA_DIGEST_TAG,
    AccountScope,
    DispatchClaimBinding,
    DispatchEvidenceClass,
    DispatchExposureResolutionProof,
    DispatchExposureResolutionReceipt,
    DispatchExposureResolutionReceiptStatus,
    DispatchTerminalProof,
    DispatchTerminalResolutionReceipt,
    DispatchTerminalResolutionReceiptStatus,
    DispatchTerminalState,
    DurableRiskAdmissionAdapter,
    ExposureAccountingMode,
    InstrumentRiskMetadata,
    OrderExposureObservationProof,
    OrderExposureState,
    OrderFillFact,
    RiskAdmissionPolicyBinding,
    RiskGroupLegClaimReceipt,
    RiskIntent,
    VerifiedDispatchExposureResolution,
    VerifiedDispatchResolution,
    fill_ids_sha256,
)

from ._contracts.models import OrderRequest, QueryOrderRequest
from ._contracts.models import OrderType as BtOrderType
from ._contracts.models import Side as BtSide
from ._normalization import NormalizationProvenance, NormalizedRecord, NormalizedRecords
from .bt_api import BtApi
from .service_execution_group_authority import ExecutionGroupAuthorityGuard, GroupDispatchHold
from .service_group_reservations import GroupReservationTarget
from .service_registry import ServiceCallContext
from .service_risk_order_entry import RiskBtApiProviderContract, RiskOrderTarget
from .service_risk_order_journal import (
    RiskOrderJournal,
    RiskOrderWriterFence,
    canonical_json,
    sha256_text,
)

_SCHEMA_VERSION = 2
_ZERO_SHA256 = "0" * 64
_MAX_EVENTS_PER_ACCOUNT = 100_000
_MAX_GROUP_EVENTS_PER_INTENT = 256
_MAX_EVENT_BYTES = 1_000_000
_MAX_FILL_ROWS = 10_000
_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_NORMALIZER_V2 = "bt-api-order-source-provenance-v2"
_ORDER_ALIASES = {
    "order_id": ("order_id", "external_order_id", "ordId", "orderId", "order_sys_id", "OrderSysID"),
    "client_order_id": ("client_order_id", "clOrdId", "clientOrderId", "order_ref", "OrderRef"),
    "status": ("status", "order_status", "state"),
    "filled": (
        "filled",
        "filled_quantity",
        "executed_qty",
        "accFillSz",
        "executedQty",
        "VolumeTraded",
    ),
    "quantity": ("quantity", "size", "order_size", "sz", "origQty", "VolumeTotalOriginal"),
    "account_id": ("account_id", "AccountID", "InvestorID"),
    "instrument": (
        "symbol",
        "symbol_name",
        "instrument",
        "instrument_id",
        "data_name",
        "instId",
        "InstrumentID",
        "trade_symbol",
    ),
    "side": ("side", "order_side", "trade_side", "S"),
    "price": ("price", "limit_price", "order_price", "Price"),
}
_FILL_ALIASES = {
    "trade_id": ("trade_id", "fill_id", "deal_id", "tradeId", "id", "TradeID"),
    "order_id": ("order_id", "external_order_id", "orderId", "ordId", "OrderSysID"),
    "client_order_id": ("client_order_id", "clOrdId", "OrderRef"),
    "account_id": ("account_id", "AccountID", "InvestorID"),
    "instrument": (
        "symbol",
        "symbol_name",
        "instrument",
        "instrument_id",
        "data_name",
        "instId",
        "InstrumentID",
        "trade_symbol",
    ),
    "side": ("side", "order_side", "trade_side", "S"),
    "quantity": ("quantity", "size", "trade_volume", "qty", "fillSz", "volume", "Volume"),
    "price": ("trade_price", "price", "fillPx", "Price"),
}
_ALIAS_MAP_SHA256 = sha256_text(
    canonical_json(
        {
            "fills": {key: list(value) for key, value in _FILL_ALIASES.items()},
            "orders": {key: list(value) for key, value in _ORDER_ALIASES.items()},
            "schema": "bt-api-execution-group-source-aliases-v1",
        }
    )
)
_ACTIVE_SESSION: contextvars.ContextVar[Any] = contextvars.ContextVar(
    "bt_api_execution_group_proof_session", default=None
)


class GroupProofSourceError(RuntimeError):
    """Finite, payload-free refusal from the group evidence source."""

    def __init__(self, code: str) -> None:
        if type(code) is not str or _REFERENCE.fullmatch(code) is None:
            raise ValueError("invalid group proof source code")
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class GroupProofJournalEvent:
    """One account-global and command-local hash-chained source event."""

    account_scope_key: str
    intent_id: str
    group_id: str
    leg_id: str
    revision: int
    event_type: str
    payload: Mapping[str, Any]
    previous_account_sha256: str
    previous_intent_sha256: str
    record_sha256: str
    writer_owner_id: str
    writer_fencing_token: int
    writer_fence_sha256: str


def _require_sha256(value: object, code: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise GroupProofSourceError(code)
    return value


def _wire_payload(scope: WireScope) -> dict[str, Any]:
    if type(scope) is not WireScope:
        raise GroupProofSourceError("GROUP_WIRE_SCOPE_INVALID")
    return {
        "account_ref": scope.account_ref,
        "account_refs": list(scope.account_refs),
        "execution_domain_ref": scope.execution_domain_ref,
        "group_id": scope.group_id,
        "leg_id": scope.leg_id,
        "manual_book_ref": scope.manual_book_ref,
        "portfolio_ref": scope.portfolio_ref,
        "run_id": scope.run_id,
        "strategy_ref": scope.strategy_ref,
    }


def _identity_payload(identity: WireIdentity) -> dict[str, str | None]:
    if type(identity) is not WireIdentity:
        raise GroupProofSourceError("GROUP_IDENTITY_INVALID")
    return {
        "actor_ref": identity.actor_ref,
        "delegate_ref": identity.delegate_ref,
        "principal_ref": identity.principal_ref,
        "role": identity.role.value,
    }


def _scope_key(scope: WireScope) -> WireScope:
    """Remove only the group envelope; run_id remains a separately matched field."""

    if type(scope) is not WireScope:
        raise GroupProofSourceError("GROUP_WIRE_SCOPE_INVALID")
    try:
        return WireScope(
            execution_domain_ref=scope.execution_domain_ref,
            account_ref=scope.account_ref,
            account_refs=scope.account_refs,
            strategy_ref=scope.strategy_ref,
            run_id=scope.run_id,
            manual_book_ref=scope.manual_book_ref,
            portfolio_ref=scope.portfolio_ref,
        )
    except (TypeError, ValueError):
        raise GroupProofSourceError("GROUP_WIRE_SCOPE_INVALID") from None


def _matches_run_wildcard(template: WireScope, requested: WireScope) -> bool:
    """Mirror the account owner's exact template rule: only run_id may vary."""

    if type(template) is not WireScope or type(requested) is not WireScope:
        return False
    if template.run_id is not None or requested.run_id is None:
        return False
    if template.strategy_ref is None or requested.strategy_ref != template.strategy_ref:
        return False
    fields = (
        "execution_domain_ref",
        "account_ref",
        "account_refs",
        "strategy_ref",
        "manual_book_ref",
        "portfolio_ref",
        "group_id",
        "leg_id",
    )
    return all(getattr(template, field) == getattr(requested, field) for field in fields)


def _json_scalar(value: object) -> object:
    if value is None or type(value) in (str, int, bool):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise GroupProofSourceError("GROUP_SOURCE_FACT_INVALID")
        # BtApi's public normalizer intentionally represents order/fill
        # numerics as floats. Preserve the shortest decimal that round-trips
        # to that exact finite float, then persist it in the same canonical
        # fixed-point string form used for Decimal values. This keeps hashes
        # stable across JSON encode/decode without admitting NaN/Infinity or
        # widening raw provider-fact parsing.
        try:
            normalized = Decimal(repr(value))
        except (InvalidOperation, ValueError):
            raise GroupProofSourceError("GROUP_SOURCE_FACT_INVALID") from None
        if not normalized.is_finite():
            raise GroupProofSourceError("GROUP_SOURCE_FACT_INVALID")
        return format(normalized, "f")
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise GroupProofSourceError("GROUP_SOURCE_FACT_INVALID")
        return format(value, "f")
    if isinstance(value, Mapping):
        return {str(key): _json_scalar(item) for key, item in value.items()}
    if type(value) in (tuple, list):
        return [_json_scalar(item) for item in value]
    raise GroupProofSourceError("GROUP_SOURCE_FACT_INVALID")


def _decimal(
    value: object, code: str, *, positive: bool = False, nonnegative: bool = False
) -> Decimal:
    if type(value) not in (str, int, Decimal):
        raise GroupProofSourceError(code)
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError):
        raise GroupProofSourceError(code) from None
    if not number.is_finite() or (positive and number <= 0) or (nonnegative and number < 0):
        raise GroupProofSourceError(code)
    return number


def _normalized_float_decimal(
    value: object, code: str, *, positive: bool = False, nonnegative: bool = False
) -> Decimal:
    """Read a built-in normalized SDK float without broadening raw facts.

    Order/fill normalizer fields use built-in floats. Their shortest
    round-tripping decimal is the exact numeric value exposed by that
    normalized record. Raw provenance continues through ``_decimal`` and
    therefore cannot start accepting binary floats as provider facts.
    """

    if type(value) is not float or not math.isfinite(value):
        raise GroupProofSourceError(code)
    try:
        number = Decimal(repr(value))
    except (InvalidOperation, ValueError):
        raise GroupProofSourceError(code) from None
    if not number.is_finite() or (positive and number <= 0) or (nonnegative and number < 0):
        raise GroupProofSourceError(code)
    return number


def _normalized_matches_source_projection(
    normalized: Decimal,
    source: Decimal,
    code: str,
) -> bool:
    try:
        normalized_float = float(normalized)
        source_float = float(source)
    except (OverflowError, ValueError):
        raise GroupProofSourceError(code) from None
    if (
        not math.isfinite(normalized_float)
        or not math.isfinite(source_float)
        or (normalized != 0 and normalized_float == 0.0)
        or (source != 0 and source_float == 0.0)
    ):
        raise GroupProofSourceError(code)
    return normalized_float == source_float


def _normalized_float_matches_source(
    value: object,
    source: Decimal,
    code: str,
    *,
    positive: bool = False,
    nonnegative: bool = False,
) -> bool:
    """Verify a normalized float against the exact source decimal's projection.

    ``number()`` converts source values to binary64. Raw provenance can retain
    an input float as its 17-digit decimal spelling, while the normalized
    record is serialized using the shortest round-tripping spelling. Compare
    both through the same finite binary64 conversion, but keep all policy and
    accounting arithmetic on the exact source ``Decimal`` values.
    """

    normalized = _normalized_float_decimal(value, code, positive=positive, nonnegative=nonnegative)
    return _normalized_matches_source_projection(normalized, source, code)


def _persisted_normalized_matches_source(
    value: object,
    source: Decimal,
    code: str,
    *,
    positive: bool = False,
    nonnegative: bool = False,
) -> bool:
    """Verify the canonical text projection written by ``_json_scalar``."""

    if type(value) is not str:
        raise GroupProofSourceError(code)
    try:
        normalized = Decimal(value)
    except (InvalidOperation, ValueError):
        raise GroupProofSourceError(code) from None
    if (
        not normalized.is_finite()
        or format(normalized, "f") != value
        or (positive and normalized <= 0)
        or (nonnegative and normalized < 0)
    ):
        raise GroupProofSourceError(code)
    try:
        normalized_float = float(normalized)
    except (OverflowError, ValueError):
        raise GroupProofSourceError(code) from None
    if not math.isfinite(normalized_float):
        raise GroupProofSourceError(code)
    # The persisted spelling must be exactly what the finite normalized
    # binary64 value produces, not a higher precision value that merely rounds
    # to the same float.
    try:
        canonical = format(Decimal(repr(normalized_float)), "f")
    except (InvalidOperation, ValueError):
        raise GroupProofSourceError(code) from None
    if canonical != value:
        raise GroupProofSourceError(code)
    return _normalized_matches_source_projection(normalized, source, code)


def _field(values: Mapping[str, Any], fields: list[str], aliases: tuple[str, ...]) -> Any:
    for name in aliases:
        if name in fields and name in values:
            return values[name]
    return None


def _provenance(value: object, operation: str) -> dict[str, Any]:
    if type(value) is not NormalizationProvenance:
        raise GroupProofSourceError("GROUP_SOURCE_PROVENANCE_MISSING")
    projected = value.to_dict()
    if (
        projected.get("schema") != _NORMALIZER_V2
        or projected.get("operation") != operation
        or type(projected.get("source_fields")) is not list
        or projected["source_fields"] != sorted(set(projected["source_fields"]))
        or type(projected.get("envelope_fields")) is not list
        or projected["envelope_fields"] != sorted(set(projected["envelope_fields"]))
        or type(projected.get("source_values")) is not dict
        or type(projected.get("envelope_values")) is not dict
        or set(projected["source_values"]) - set(projected["source_fields"])
        or set(projected["envelope_values"]) - set(projected["envelope_fields"])
    ):
        raise GroupProofSourceError("GROUP_SOURCE_PROVENANCE_INVALID")
    for name in ("source_fields", "envelope_fields"):
        if any(type(item) is not str for item in projected[name]):
            raise GroupProofSourceError("GROUP_SOURCE_PROVENANCE_INVALID")
    for values in (projected["source_values"], projected["envelope_values"]):
        if any(
            type(key) is not str or type(item) not in (str, int, bool)
            for key, item in values.items()
        ):
            raise GroupProofSourceError("GROUP_SOURCE_PROVENANCE_INVALID")
    expected = sha256_text(
        canonical_json(
            {
                "envelope_values": projected["envelope_values"],
                "operation": operation,
                "schema": projected["schema"],
                "source_fields": projected["source_fields"],
                "source_values": projected["source_values"],
            }
        )
    )
    if projected.get("source_sha256") != expected:
        raise GroupProofSourceError("GROUP_SOURCE_PROVENANCE_INVALID")
    return projected


def _envelope(
    provenance: Mapping[str, Any],
    *,
    operation: str,
    schema: str,
    client_order_id: str,
    account_id: str,
) -> tuple[object, str]:
    values = provenance["envelope_values"]
    response_schema = (
        values.get("schema_version") or values.get("response_schema") or values.get("schema")
    )
    complete = values.get("complete")
    if complete is None:
        complete = values.get(
            "complete_order_snapshot" if operation == "query_order" else "complete_fill_history"
        )
    if complete is None:
        complete = values.get(
            "snapshot_complete" if operation == "query_order" else "history_complete"
        )
    response_client = (
        values.get("client_order_id")
        or values.get("query_client_order_id")
        or values.get("queried_client_order_id")
        or values.get("response_client_order_id")
    )
    response_account = values.get("account_id") or values.get("response_account_id")
    watermark_raw = (
        values.get("watermark") or values.get("response_watermark")
        if operation == "query_order"
        else values.get("fill_watermark")
    )
    if (
        response_schema != schema
        or complete is not True
        or response_client != client_order_id
        or response_account != account_id
        or (
            operation == "query_order"
            and (
                type(watermark_raw) not in (str, int)
                or watermark_raw == ""
                or type(watermark_raw) is int
                and watermark_raw <= 0
            )
        )
        or (operation == "get_deals" and (type(watermark_raw) is not int or watermark_raw <= 0))
    ):
        raise GroupProofSourceError("GROUP_SOURCE_ENVELOPE_INVALID")
    return watermark_raw, response_schema


def _same_order_request(intent: OrderIntent, request: OrderRequest) -> bool:
    return bool(
        request.symbol == intent.instrument
        and request.side.value.upper() == intent.side.value.upper()
        and request.order_type.value.upper() == "LIMIT"
        and request.quantity == intent.quantity
        and request.price == intent.price
        and request.time_in_force == "IOC"
        and request.reduce_only is False
        and request.account_id
        and request.client_order_id
        and request.idempotency_key == request.client_order_id
    )


class GroupProofJournal:
    """SQLite append-only group attempt/source journal with dual hash chains."""

    EVENT_TYPES = frozenset(
        {
            "ATTEMPT_STARTED",
            "ATTEMPT_UNKNOWN",
            "SOURCE_OBSERVED",
            "SOURCE_PROGRESS",
            "SOURCE_RECONCILED",
            "PROOF_READY",
            "RISK_RESOLVED",
        }
    )

    def __init__(self, path: str | Path, *, writer_id: str, busy_timeout_ms: int = 5_000) -> None:
        if type(writer_id) is not str or _REFERENCE.fullmatch(writer_id) is None:
            raise GroupProofSourceError("GROUP_JOURNAL_WRITER_INVALID")
        if type(busy_timeout_ms) is not int or busy_timeout_ms <= 0:
            raise GroupProofSourceError("GROUP_JOURNAL_TIMEOUT_INVALID")
        self._path = Path(path).expanduser().resolve()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._writer_id = writer_id
        # Appends are mediated by the source, which owns the actual Risk
        # account fence.  A fence DTO alone is not authority: it is a public
        # immutable value and can otherwise be copied or constructed.
        self.__append_capability = object()
        self._lock = threading.RLock()
        self._closed = False
        try:
            self._connection = sqlite3.connect(
                str(self._path),
                timeout=busy_timeout_ms / 1000,
                isolation_level=None,
                check_same_thread=False,
            )
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys=ON")
            self._connection.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA synchronous=FULL")
            self._initialize()
        except (OSError, sqlite3.Error, ValueError):
            connection = getattr(self, "_connection", None)
            if connection is not None:
                connection.close()
            raise GroupProofSourceError("GROUP_JOURNAL_UNAVAILABLE") from None

    @property
    def path(self) -> Path:
        return self._path

    @property
    def incarnation_id(self) -> str:
        with self._lock:
            row = self._connection.execute(
                "SELECT value FROM group_proof_meta WHERE key='incarnation_id'"
            ).fetchone()
        if row is None or type(row["value"]) is not str:
            raise GroupProofSourceError("GROUP_JOURNAL_CORRUPT")
        return row["value"]

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._connection.close()
            self._closed = True

    def _ensure_open(self) -> None:
        if self._closed:
            raise GroupProofSourceError("GROUP_JOURNAL_CLOSED")

    def _initialize(self) -> None:
        with self._lock:
            connection = self._connection
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS group_proof_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)"
                )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS group_proof_events("
                    "revision INTEGER PRIMARY KEY,account_scope_key TEXT NOT NULL,intent_id TEXT NOT NULL,"
                    "group_id TEXT NOT NULL,leg_id TEXT NOT NULL,event_type TEXT NOT NULL,payload_json TEXT NOT NULL,"
                    "previous_account_sha256 TEXT NOT NULL,previous_intent_sha256 TEXT NOT NULL,"
                    "record_sha256 TEXT NOT NULL,writer_owner_id TEXT NOT NULL,writer_fencing_token INTEGER NOT NULL,"
                    "writer_fence_sha256 TEXT NOT NULL)"
                )
                row = connection.execute(
                    "SELECT value FROM group_proof_meta WHERE key='schema_version'"
                ).fetchone()
                if row is None:
                    connection.execute(
                        "INSERT INTO group_proof_meta VALUES('schema_version',?)",
                        (str(_SCHEMA_VERSION),),
                    )
                    connection.execute(
                        "INSERT INTO group_proof_meta VALUES('incarnation_id',?)",
                        (uuid.uuid4().hex,),
                    )
                    connection.execute("INSERT INTO group_proof_meta VALUES('head_revision','0')")
                    connection.execute(
                        "INSERT INTO group_proof_meta VALUES('head_sha256',?)", (_ZERO_SHA256,)
                    )
                elif row["value"] == "1":
                    # Version 1 allowed one row per event type and intent. Open
                    # orders can receive several source-bound progress reads
                    # before a terminal proof exists, so v2 removes only that
                    # uniqueness restriction while preserving every existing
                    # event byte, revision and hash-chain link.
                    connection.execute(
                        "ALTER TABLE group_proof_events RENAME TO group_proof_events_v1"
                    )
                    connection.execute(
                        "CREATE TABLE group_proof_events("
                        "revision INTEGER PRIMARY KEY,account_scope_key TEXT NOT NULL,intent_id TEXT NOT NULL,"
                        "group_id TEXT NOT NULL,leg_id TEXT NOT NULL,event_type TEXT NOT NULL,payload_json TEXT NOT NULL,"
                        "previous_account_sha256 TEXT NOT NULL,previous_intent_sha256 TEXT NOT NULL,"
                        "record_sha256 TEXT NOT NULL,writer_owner_id TEXT NOT NULL,writer_fencing_token INTEGER NOT NULL,"
                        "writer_fence_sha256 TEXT NOT NULL)"
                    )
                    connection.execute(
                        "INSERT INTO group_proof_events SELECT * FROM group_proof_events_v1"
                    )
                    connection.execute("DROP TABLE group_proof_events_v1")
                    connection.execute(
                        "UPDATE group_proof_meta SET value=? WHERE key='schema_version'",
                        (str(_SCHEMA_VERSION),),
                    )
                elif row["value"] != str(_SCHEMA_VERSION):
                    raise GroupProofSourceError("GROUP_JOURNAL_SCHEMA_UNSUPPORTED")
                if connection.execute("SELECT COUNT(*) FROM group_proof_meta").fetchone()[0] != 4:
                    raise GroupProofSourceError("GROUP_JOURNAL_CORRUPT")
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise

    @staticmethod
    def _event_hash(material: Mapping[str, Any]) -> str:
        return sha256_text(canonical_json(dict(material)))

    def _verify_global_chain(self, connection: sqlite3.Connection) -> tuple[int, str]:
        rows = connection.execute("SELECT * FROM group_proof_events ORDER BY revision").fetchall()
        if len(rows) > _MAX_EVENTS_PER_ACCOUNT * 100:
            raise GroupProofSourceError("GROUP_JOURNAL_LIMIT_EXCEEDED")
        previous_account = _ZERO_SHA256
        previous_by_command: dict[tuple[str, str], str] = {}
        for expected_revision, row in enumerate(rows, 1):
            if row["revision"] != expected_revision:
                raise GroupProofSourceError("GROUP_JOURNAL_CHAIN_INVALID")
            try:
                payload = json.loads(row["payload_json"])
            except (TypeError, json.JSONDecodeError):
                raise GroupProofSourceError("GROUP_JOURNAL_CHAIN_INVALID") from None
            if type(payload) is not dict or canonical_json(payload) != row["payload_json"]:
                raise GroupProofSourceError("GROUP_JOURNAL_CHAIN_INVALID")
            command_key = (row["account_scope_key"], row["intent_id"])
            previous_intent = previous_by_command.get(command_key, _ZERO_SHA256)
            material = {
                "account_scope_key": row["account_scope_key"],
                "event_type": row["event_type"],
                "group_id": row["group_id"],
                "intent_id": row["intent_id"],
                "leg_id": row["leg_id"],
                "payload": payload,
                "previous_account_sha256": previous_account,
                "previous_intent_sha256": previous_intent,
                "revision": row["revision"],
                "writer_fence_sha256": row["writer_fence_sha256"],
                "writer_fencing_token": row["writer_fencing_token"],
                "writer_owner_id": row["writer_owner_id"],
            }
            if (
                row["previous_account_sha256"] != previous_account
                or row["previous_intent_sha256"] != previous_intent
                or row["record_sha256"] != self._event_hash(material)
                or row["event_type"] not in self.EVENT_TYPES
                or type(row["writer_owner_id"]) is not str
                or not row["writer_owner_id"]
                or type(row["writer_fencing_token"]) is not int
                or row["writer_fencing_token"] <= 0
                or type(row["writer_fence_sha256"]) is not str
                or _SHA256.fullmatch(row["writer_fence_sha256"]) is None
            ):
                raise GroupProofSourceError("GROUP_JOURNAL_CHAIN_INVALID")
            previous_account = row["record_sha256"]
            previous_by_command[command_key] = row["record_sha256"]
        meta_revision = int(
            connection.execute(
                "SELECT value FROM group_proof_meta WHERE key='head_revision'"
            ).fetchone()[0]
        )
        meta_hash = connection.execute(
            "SELECT value FROM group_proof_meta WHERE key='head_sha256'"
        ).fetchone()[0]
        if meta_revision != len(rows) or meta_hash != previous_account:
            raise GroupProofSourceError("GROUP_JOURNAL_HEAD_INVALID")
        return meta_revision, meta_hash

    def _append(
        self,
        capability: object,
        fence: RiskOrderWriterFence,
        *,
        intent_id: str,
        group_id: str,
        leg_id: str,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> GroupProofJournalEvent:
        if capability is not self.__append_capability:
            raise GroupProofSourceError("GROUP_JOURNAL_APPEND_FORBIDDEN")
        if type(fence) is not RiskOrderWriterFence:
            raise GroupProofSourceError("GROUP_RISK_FENCE_REQUIRED")
        if any(
            type(value) is not str or _REFERENCE.fullmatch(value) is None
            for value in (intent_id, group_id, leg_id)
        ):
            raise GroupProofSourceError("GROUP_JOURNAL_ID_INVALID")
        if event_type not in self.EVENT_TYPES or not isinstance(payload, Mapping):
            raise GroupProofSourceError("GROUP_JOURNAL_EVENT_INVALID")
        body = _json_scalar(dict(payload))
        payload_json = canonical_json(body)
        if len(payload_json.encode("utf-8")) > _MAX_EVENT_BYTES:
            raise GroupProofSourceError("GROUP_JOURNAL_EVENT_TOO_LARGE")
        account_scope_key = fence.scope.key
        with self._lock:
            self._ensure_open()
            connection = self._connection
            try:
                connection.execute("BEGIN IMMEDIATE")
                current_revision, current_hash = self._verify_global_chain(connection)
                prior_row = connection.execute(
                    "SELECT record_sha256 FROM group_proof_events WHERE account_scope_key=? AND intent_id=? ORDER BY revision DESC LIMIT 1",
                    (account_scope_key, intent_id),
                ).fetchone()
                prior_intent = _ZERO_SHA256 if prior_row is None else prior_row["record_sha256"]
                revision = current_revision + 1
                if revision > 2**63 - 1:
                    raise GroupProofSourceError("GROUP_JOURNAL_REVISION_EXHAUSTED")
                material = {
                    "account_scope_key": account_scope_key,
                    "event_type": event_type,
                    "group_id": group_id,
                    "intent_id": intent_id,
                    "leg_id": leg_id,
                    "payload": body,
                    "previous_account_sha256": current_hash,
                    "previous_intent_sha256": prior_intent,
                    "revision": revision,
                    "writer_fence_sha256": fence.fence_sha256,
                    "writer_fencing_token": fence.fencing_token,
                    "writer_owner_id": fence.owner_id,
                }
                record_sha = self._event_hash(material)
                connection.execute(
                    "INSERT INTO group_proof_events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        revision,
                        account_scope_key,
                        intent_id,
                        group_id,
                        leg_id,
                        event_type,
                        payload_json,
                        current_hash,
                        prior_intent,
                        record_sha,
                        fence.owner_id,
                        fence.fencing_token,
                        fence.fence_sha256,
                    ),
                )
                connection.execute(
                    "UPDATE group_proof_meta SET value=? WHERE key='head_revision'",
                    (str(revision),),
                )
                connection.execute(
                    "UPDATE group_proof_meta SET value=? WHERE key='head_sha256'", (record_sha,)
                )
                connection.execute("COMMIT")
            except BaseException as error:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                if isinstance(error, GroupProofSourceError):
                    raise
                if isinstance(error, sqlite3.IntegrityError):
                    raise GroupProofSourceError("GROUP_JOURNAL_DUPLICATE_EVENT") from None
                raise GroupProofSourceError("GROUP_JOURNAL_APPEND_FAILED") from None
        return GroupProofJournalEvent(
            account_scope_key,
            intent_id,
            group_id,
            leg_id,
            revision,
            event_type,
            MappingProxyType(dict(body)),
            current_hash,
            prior_intent,
            record_sha,
            fence.owner_id,
            fence.fencing_token,
            fence.fence_sha256,
        )

    def _source_capability(self) -> object:
        """Return the identity token only to the trusted source constructor."""

        return self.__append_capability

    def events(self, account_scope_key: str, intent_id: str) -> tuple[GroupProofJournalEvent, ...]:
        if (
            type(account_scope_key) is not str
            or not account_scope_key
            or type(intent_id) is not str
        ):
            raise GroupProofSourceError("GROUP_JOURNAL_READ_INVALID")
        with self._lock:
            self._ensure_open()
            connection = self._connection
            try:
                connection.execute("PRAGMA query_only=ON")
                connection.execute("BEGIN")
                self._verify_global_chain(connection)
                rows = connection.execute(
                    "SELECT * FROM group_proof_events WHERE account_scope_key=? AND intent_id=? ORDER BY revision",
                    (account_scope_key, intent_id),
                ).fetchall()
                connection.commit()
            except BaseException as error:
                if connection.in_transaction:
                    connection.rollback()
                if isinstance(error, GroupProofSourceError):
                    raise
                raise GroupProofSourceError("GROUP_JOURNAL_READ_FAILED") from None
            finally:
                # This connection is also the append writer.  Do not leave
                # SQLite's connection-level query-only flag set after a read.
                connection.execute("PRAGMA query_only=OFF")
        result: list[GroupProofJournalEvent] = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            result.append(
                GroupProofJournalEvent(
                    row["account_scope_key"],
                    row["intent_id"],
                    row["group_id"],
                    row["leg_id"],
                    row["revision"],
                    row["event_type"],
                    MappingProxyType(payload),
                    row["previous_account_sha256"],
                    row["previous_intent_sha256"],
                    row["record_sha256"],
                    row["writer_owner_id"],
                    row["writer_fencing_token"],
                    row["writer_fence_sha256"],
                )
            )
        if any(event.event_type not in self.EVENT_TYPES for event in result):
            raise GroupProofSourceError("GROUP_JOURNAL_EVENT_INVALID")
        if len({event.event_type for event in result}) != len(result):
            raise GroupProofSourceError("GROUP_JOURNAL_DUPLICATE_EVENT")
        return tuple(result)


@dataclass(frozen=True, slots=True)
class GroupRiskPolicyBinding:
    """Policy identity read from the actual durable Risk vector receipt."""

    scope_key: str
    group_id: str
    group_fingerprint: str
    policy_id: str
    policy_fingerprint: str
    exposure_accounting_mode: ExposureAccountingMode
    deadline_ns: int

    def __post_init__(self) -> None:
        if type(self.scope_key) is not str or not self.scope_key:
            raise GroupProofSourceError("GROUP_RISK_POLICY_BINDING_INVALID")
        if type(self.group_id) is not str or _REFERENCE.fullmatch(self.group_id) is None:
            raise GroupProofSourceError("GROUP_RISK_POLICY_BINDING_INVALID")
        _require_sha256(self.group_fingerprint, "GROUP_RISK_POLICY_BINDING_INVALID")
        if type(self.policy_id) is not str or not self.policy_id:
            raise GroupProofSourceError("GROUP_RISK_POLICY_BINDING_INVALID")
        _require_sha256(self.policy_fingerprint, "GROUP_RISK_POLICY_BINDING_INVALID")
        if self.exposure_accounting_mode is not ExposureAccountingMode.RESERVATIONS_ONLY:
            raise GroupProofSourceError("GROUP_RISK_POLICY_BINDING_INVALID")
        if type(self.deadline_ns) is not int or self.deadline_ns <= 0:
            raise GroupProofSourceError("GROUP_RISK_POLICY_BINDING_INVALID")


@dataclass(frozen=True, slots=True)
class GroupProofTarget:
    """Trusted local resources for one exact account-owned group scope."""

    execution_scope: ExecutionScope
    risk_target: RiskOrderTarget
    provider_contract: RiskBtApiProviderContract
    bt_api: BtApi
    execution_store: SqliteExecutionStore
    risk_order_journal: RiskOrderJournal
    evidence_class: DispatchEvidenceClass
    max_observation_age_ns: int = 60_000_000_000

    def __post_init__(self) -> None:
        if (
            type(self.execution_scope) is not ExecutionScope
            or type(self.risk_target) is not RiskOrderTarget
            or type(self.provider_contract) is not RiskBtApiProviderContract
            or type(self.bt_api) is not BtApi
            or type(self.execution_store) is not SqliteExecutionStore
            or type(self.risk_order_journal) is not RiskOrderJournal
            or type(self.evidence_class) is not DispatchEvidenceClass
            or type(self.max_observation_age_ns) is not int
            or self.max_observation_age_ns <= 0
        ):
            raise GroupProofSourceError("GROUP_PROOF_TARGET_INVALID")
        contract = self.provider_contract
        if self.risk_target.strategy_id != self.execution_scope.strategy_id:
            raise GroupProofSourceError("GROUP_PROOF_TARGET_SCOPE_INVALID")
        if (
            not contract.exposure_ready
            or contract.supported_order_types != ("LIMIT",)
            or contract.supported_position_effects != ("OPEN",)
            or "IOC" not in contract.supported_time_in_force
            or contract.exchange_name != self.risk_target.exchange_name
            or self.risk_target.account_scope.provider.casefold()
            != contract.exchange_name.partition("___")[0].casefold()
        ):
            raise GroupProofSourceError("GROUP_PROVIDER_CONTRACT_UNAVAILABLE")

    @property
    def account_scope(self) -> AccountScope:
        return self.risk_target.account_scope


class GroupProofRuntimeBundle:
    """Closed, group-only composition shared by the reservation and owner roles.

    The bundle contains distinct dedicated and account-scoped admission
    adapters.  It does not expose or merge either adapter's internal gate.
    """

    def __init__(
        self,
        risk_admission: DurableRiskAdmissionAdapter,
        account_scoped_admission: AccountScopedGroupAdmissionAdapter,
        reservation_targets: Mapping[WireScope, GroupReservationTarget],
        proof_targets: Mapping[WireScope, GroupProofTarget],
        proof_source: GroupProofSource,
    ) -> None:
        if type(risk_admission) is not DurableRiskAdmissionAdapter:
            raise GroupProofSourceError("GROUP_RUNTIME_BUNDLE_INVALID")
        try:
            risk_policy_binding = risk_admission.policy_binding
        except Exception:
            raise GroupProofSourceError("GROUP_RISK_POLICY_BINDING_UNAVAILABLE") from None
        if type(risk_policy_binding) is not RiskAdmissionPolicyBinding:
            raise GroupProofSourceError("GROUP_RISK_POLICY_BINDING_INVALID")
        if (
            risk_policy_binding.exposure_accounting_mode
            is not ExposureAccountingMode.RESERVATIONS_ONLY
        ):
            # The group proof journal revision is not the account cursor used
            # by LIVE_GROSS baselines. Until both bind to one trusted,
            # generation-qualified cursor, do not compose this path under that
            # policy even though each individual proof is source-backed.
            raise GroupProofSourceError("GROUP_LIVE_GROSS_CURSOR_UNAVAILABLE")
        if (
            type(account_scoped_admission) is not AccountScopedGroupAdmissionAdapter
            or type(proof_source) is not GroupProofSource
            or type(reservation_targets) is not MappingProxyType
            or type(proof_targets) is not MappingProxyType
            or not reservation_targets
            or set(reservation_targets) != set(proof_targets)
        ):
            raise GroupProofSourceError("GROUP_RUNTIME_BUNDLE_INVALID")
        try:
            # Matching account-scope fingerprints are not enough: two
            # independent DurableRiskAdmissionAdapters can target the same
            # account and policy while holding different durable gate state.
            # The Execution-owned public adapter check proves this exact
            # composition identity without exposing its wrapped gate.
            account_scoped_admission.require_admission_gate(risk_admission)
        except Exception:
            raise GroupProofSourceError("GROUP_RISK_ADMISSION_GATE_IDENTITY_MISMATCH") from None
        for wire_scope in reservation_targets:
            reservation_target = reservation_targets[wire_scope]
            proof_target = proof_targets[wire_scope]
            if (
                type(wire_scope) is not WireScope
                or wire_scope.group_id is not None
                or wire_scope.leg_id is not None
                or type(reservation_target) is not GroupReservationTarget
                or reservation_target.admission is not risk_admission
                or type(proof_target) is not GroupProofTarget
                or proof_target.account_scope != reservation_target.account_scope
                or proof_target.execution_scope.account_ref != wire_scope.account_ref
                or proof_target.execution_scope.strategy_id != wire_scope.strategy_ref
                or account_scoped_admission.risk_scope_fingerprint
                != risk_account_scope_fingerprint(reservation_target.account_scope.key)
                or proof_source.target_for_template(wire_scope) is not proof_target
            ):
                raise GroupProofSourceError("GROUP_RUNTIME_BUNDLE_SCOPE_INVALID")
        self.risk_admission = risk_admission
        self.risk_policy_binding = risk_policy_binding
        self.account_scoped_admission = account_scoped_admission
        self.reservation_targets = reservation_targets
        self.proof_targets = proof_targets
        self.proof_source = proof_source
        self._target_map = proof_targets
        proof_source._bind_runtime_bundle(self)

    def reservation_target_for(self, wire_scope: WireScope) -> GroupReservationTarget:
        target = self.reservation_targets.get(_scope_key(wire_scope))
        if target is None:
            raise GroupProofSourceError("GROUP_SCOPE_DENIED")
        return target

    def proof_target_for(self, wire_scope: WireScope) -> GroupProofTarget:
        template, target = self.proof_source._target_for_wire_scope(wire_scope)
        if self.proof_targets.get(template) is not target:
            raise GroupProofSourceError("GROUP_SCOPE_DENIED")
        return target

    def execution_scope_for(self, wire_scope: WireScope) -> ExecutionScope:
        return self.proof_target_for(wire_scope).execution_scope

    def require_runtime_bundle(
        self,
        *,
        account_scoped_admission: AccountScopedGroupAdmissionAdapter,
        risk_admission: DurableRiskAdmissionAdapter,
    ) -> None:
        if (
            account_scoped_admission is not self.account_scoped_admission
            or risk_admission is not self.risk_admission
            or self.proof_source.runtime_bundle is not self
        ):
            raise GroupProofSourceError("GROUP_RUNTIME_BUNDLE_IDENTITY_MISMATCH")
        self._assert_reservations_only_policy()

    def _assert_reservations_only_policy(self) -> None:
        try:
            current = self.risk_admission.policy_binding
        except Exception:
            raise GroupProofSourceError("GROUP_RISK_POLICY_BINDING_UNAVAILABLE") from None
        if (
            type(current) is not RiskAdmissionPolicyBinding
            or current != self.risk_policy_binding
            or current.exposure_accounting_mode is not ExposureAccountingMode.RESERVATIONS_ONLY
        ):
            raise GroupProofSourceError("GROUP_RISK_POLICY_BINDING_CHANGED")

    def policy_binding_for(self, wire_scope: WireScope, group_id: str) -> GroupRiskPolicyBinding:
        self._assert_reservations_only_policy()
        reservation_target = self.reservation_target_for(wire_scope)
        try:
            receipt = self.risk_admission.read_group_vector_snapshot(
                reservation_target.account_scope, group_id
            )
        except Exception:
            raise GroupProofSourceError("GROUP_RISK_POLICY_UNAVAILABLE") from None
        if (
            receipt.scope != reservation_target.account_scope
            or receipt.group_id != group_id
            or receipt.policy_fingerprint != self.risk_policy_binding.policy_fingerprint
        ):
            raise GroupProofSourceError("GROUP_RISK_POLICY_UNAVAILABLE")
        return GroupRiskPolicyBinding(
            scope_key=receipt.scope.key,
            group_id=receipt.group_id,
            group_fingerprint=receipt.group_fingerprint,
            policy_id=self.risk_policy_binding.policy_id,
            policy_fingerprint=self.risk_policy_binding.policy_fingerprint,
            exposure_accounting_mode=self.risk_policy_binding.exposure_accounting_mode,
            deadline_ns=receipt.deadline_ns,
        )


class GroupProofSource:
    """Produce and verify one-call, source-backed group terminal receipts."""

    def __init__(
        self,
        journal: GroupProofJournal,
        targets: Mapping[WireScope, GroupProofTarget],
        *,
        clock_ns=time.time_ns,
    ) -> None:
        if (
            type(journal) is not GroupProofJournal
            or not isinstance(targets, Mapping)
            or not targets
        ):
            raise GroupProofSourceError("GROUP_PROOF_SOURCE_INVALID")
        if not callable(clock_ns):
            raise GroupProofSourceError("GROUP_CLOCK_INVALID")
        copied: dict[WireScope, GroupProofTarget] = {}
        for wire_scope, target in targets.items():
            if (
                type(wire_scope) is not WireScope
                or wire_scope.group_id is not None
                or wire_scope.leg_id is not None
                or type(target) is not GroupProofTarget
            ):
                raise GroupProofSourceError("GROUP_PROOF_TARGETS_INVALID")
            _scope_key(wire_scope)
            copied[wire_scope] = target
        self._journal = journal
        self._targets = MappingProxyType(copied)
        self._capability = journal._source_capability()
        self._clock_ns = clock_ns
        self._runtime_bundle: GroupProofRuntimeBundle | None = None
        self._bundle_lock = threading.RLock()

    @property
    def targets(self) -> Mapping[WireScope, GroupProofTarget]:
        return self._targets

    @property
    def runtime_bundle(self) -> GroupProofRuntimeBundle | None:
        return self._runtime_bundle

    def target_for_template(self, wire_scope: WireScope) -> GroupProofTarget | None:
        return self._targets.get(wire_scope)

    def _bind_runtime_bundle(self, bundle: GroupProofRuntimeBundle) -> None:
        if type(bundle) is not GroupProofRuntimeBundle:
            raise GroupProofSourceError("GROUP_RUNTIME_BUNDLE_INVALID")
        with self._bundle_lock:
            if self._runtime_bundle is not None and self._runtime_bundle is not bundle:
                raise GroupProofSourceError("GROUP_RUNTIME_BUNDLE_ALREADY_BOUND")
            if dict(bundle.proof_targets) != dict(self._targets):
                raise GroupProofSourceError("GROUP_RUNTIME_BUNDLE_TARGET_MISMATCH")
            self._runtime_bundle = bundle

    def _target_for_wire_scope(self, wire_scope: WireScope) -> tuple[WireScope, GroupProofTarget]:
        if (
            type(wire_scope) is not WireScope
            or type(wire_scope.group_id) is not str
            or _REFERENCE.fullmatch(wire_scope.group_id) is None
            or type(wire_scope.leg_id) is not str
            or _REFERENCE.fullmatch(wire_scope.leg_id) is None
        ):
            raise GroupProofSourceError("GROUP_WIRE_SCOPE_INVALID")
        stable = _scope_key(wire_scope)
        matches = [
            (template, target)
            for template, target in self._targets.items()
            if stable == template or _matches_run_wildcard(template, stable)
        ]
        if len(matches) != 1:
            raise GroupProofSourceError(
                "GROUP_SCOPE_AMBIGUOUS" if matches else "GROUP_SCOPE_DENIED"
            )
        return matches[0]

    def _validate_call(
        self,
        *,
        wire_scope: WireScope,
        principal_ref: str,
        identity: WireIdentity,
        service_context: ServiceCallContext,
    ) -> tuple[GroupProofTarget, WireScope]:
        template, target = self._target_for_wire_scope(wire_scope)
        if (
            type(service_context) is not ServiceCallContext
            or type(identity) is not WireIdentity
            or identity.role is not WireRole.SERVICE
            or identity != service_context.identity
            or type(principal_ref) is not str
            or principal_ref != service_context.principal_ref
            or identity.principal_ref != principal_ref
            or replace(wire_scope, group_id=None, leg_id=None) != service_context.scope
            or service_context.scope not in self._targets
            and not _matches_run_wildcard(template, service_context.scope)
            or service_context.registration_id == ""
        ):
            raise GroupProofSourceError("GROUP_CALL_AUTHORITY_INVALID")
        return target, template

    def _map_intent(
        self,
        *,
        target: GroupProofTarget,
        wire_scope: WireScope,
        intent: OrderIntent,
        permit: GroupLegPermitReference,
    ) -> tuple[object, GroupPermitBinding]:
        if (
            type(intent) is not OrderIntent
            or type(permit) is not GroupLegPermitReference
            or intent.scope != target.execution_scope
            or wire_scope.account_ref != permit.account_ref
            or wire_scope.strategy_ref != permit.strategy_id
            or wire_scope.portfolio_ref != permit.portfolio_ref
            or wire_scope.group_id != permit.group_id
            or wire_scope.leg_id != permit.leg_id
            or intent.order_type is not OrderType.LIMIT
            or intent.time_in_force is not TimeInForce.IOC
            or intent.position_effect is not PositionEffect.OPEN
            or intent.reduce_only is not False
            or intent.metadata_version == "unknown"
            or intent.price is None
        ):
            raise GroupProofSourceError("GROUP_ORDER_CAPABILITY_UNSUPPORTED")
        try:
            permit.validate_order(intent)
            if intent.intent_id != permit.intent_id:
                raise ValueError
            mapped = self._runtime_bundle.risk_admission.map_group_intent(
                intent, scope=target.account_scope
            )
            if type(mapped) is not RiskIntent:
                raise TypeError
            binding = permit.bind_actual_child(mapped.fingerprint)
            binding.validate_order(intent)
        except Exception:
            raise GroupProofSourceError("GROUP_PERMIT_BINDING_INVALID") from None
        snapshot = self._read_group_snapshot(target, permit.group_id)
        leg = self._reservation_leg(snapshot, permit.leg_id)
        if (
            snapshot.group_fingerprint != permit.group_fingerprint
            or snapshot.policy_fingerprint != permit.policy_fingerprint
            or snapshot.deadline_ns != permit.deadline_ns
            or leg.permit_id != permit.permit_id
            or leg.intent_id != permit.intent_id
            or leg.reserved_intent_hash != permit.reserved_intent_hash
            or leg.exposure_profile_fingerprint != permit.exposure_profile_fingerprint
            or permit.risk_scope_fingerprint
            != risk_account_scope_fingerprint(target.account_scope.key)
            or binding.actual_child_intent_hash != mapped.fingerprint
        ):
            raise GroupProofSourceError("GROUP_RESERVATION_BINDING_MISMATCH")
        return mapped, binding

    def _read_group_snapshot(self, target: GroupProofTarget, group_id: str):
        bundle = self._runtime_bundle
        if bundle is None:
            raise GroupProofSourceError("GROUP_RUNTIME_BUNDLE_UNBOUND")
        bundle._assert_reservations_only_policy()
        try:
            receipt = bundle.risk_admission.read_group_vector_snapshot(
                target.account_scope, group_id
            )
        except Exception:
            raise GroupProofSourceError("GROUP_RISK_RESERVATION_UNAVAILABLE") from None
        if (
            receipt.scope != target.account_scope
            or receipt.group_id != group_id
            or receipt.policy_fingerprint != bundle.risk_policy_binding.policy_fingerprint
        ):
            raise GroupProofSourceError("GROUP_RISK_RESERVATION_MISMATCH")
        return receipt

    @staticmethod
    def _reservation_leg(snapshot: object, leg_id: str):
        matches = [leg for leg in getattr(snapshot, "legs", ()) if leg.leg_id == leg_id]
        if len(matches) != 1:
            raise GroupProofSourceError("GROUP_RISK_LEG_MISSING")
        return matches[0]

    def bind_permit(
        self,
        *,
        permit: GroupLegPermitReference,
        intent: OrderIntent,
        principal_ref: str,
        identity: WireIdentity,
        wire_scope: WireScope,
        service_context: ServiceCallContext,
    ) -> GroupPermitBinding:
        target, _template = self._validate_call(
            wire_scope=wire_scope,
            principal_ref=principal_ref,
            identity=identity,
            service_context=service_context,
        )
        _mapped, binding = self._map_intent(
            target=target, wire_scope=wire_scope, intent=intent, permit=permit
        )
        return binding

    def bind_dispatch(
        self,
        *,
        binding: GroupPermitBinding,
        intent: OrderIntent,
        permit: GroupLegPermitReference,
        principal_ref: str,
        identity: WireIdentity,
        wire_scope: WireScope,
        service_context: ServiceCallContext,
        active_authority: ExecutionGroupAuthorityGuard,
    ) -> GroupProofDispatchSession:
        target, _template = self._validate_call(
            wire_scope=wire_scope,
            principal_ref=principal_ref,
            identity=identity,
            service_context=service_context,
        )
        if type(active_authority) is not ExecutionGroupAuthorityGuard:
            raise GroupProofSourceError("GROUP_ACTIVE_AUTHORITY_REQUIRED")
        _mapped, expected = self._map_intent(
            target=target, wire_scope=wire_scope, intent=intent, permit=permit
        )
        if type(binding) is not GroupPermitBinding or binding != expected:
            raise GroupProofSourceError("GROUP_PERMIT_BINDING_MISMATCH")
        return GroupProofDispatchSession(
            source=self,
            target=target,
            binding=binding,
            intent=intent,
            permit=permit,
            mapped_intent=_mapped,
            principal_ref=principal_ref,
            identity=identity,
            wire_scope=wire_scope,
            service_context=service_context,
            active_authority=active_authority,
        )

    def _append_event(
        self,
        *,
        target: GroupProofTarget,
        fence: RiskOrderWriterFence,
        intent: OrderIntent,
        permit: GroupLegPermitReference,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> GroupProofJournalEvent:
        return self._journal._append(
            self._capability,
            fence,
            intent_id=intent.intent_id,
            group_id=permit.group_id,
            leg_id=permit.leg_id,
            event_type=event_type,
            payload=payload,
        )

    def _events(
        self, target: GroupProofTarget, intent_id: str
    ) -> tuple[GroupProofJournalEvent, ...]:
        events = self._journal.events(target.account_scope.key, intent_id)
        if len(events) > _MAX_GROUP_EVENTS_PER_INTENT or any(
            event.account_scope_key != target.account_scope.key or event.intent_id != intent_id
            for event in events
        ):
            raise GroupProofSourceError("GROUP_SOURCE_EVENT_CHAIN_INVALID")
        return events

    def _query_claim(
        self, target: GroupProofTarget, permit: GroupLegPermitReference
    ) -> RiskGroupLegClaimReceipt | None:
        bundle = self._runtime_bundle
        if bundle is None:
            raise GroupProofSourceError("GROUP_RUNTIME_BUNDLE_UNBOUND")
        bundle._assert_reservations_only_policy()
        try:
            claim = bundle.risk_admission.query_group_leg_claim(
                target.account_scope, permit.group_id, permit.leg_id
            )
        except Exception:
            raise GroupProofSourceError("GROUP_RISK_CLAIM_UNAVAILABLE") from None
        if claim is not None and type(claim) is not RiskGroupLegClaimReceipt:
            raise GroupProofSourceError("GROUP_RISK_CLAIM_INVALID")
        return claim

    def _claim_projection(
        self,
        target: GroupProofTarget,
        intent: OrderIntent,
        permit: GroupLegPermitReference,
        mapped: RiskIntent,
        claim: RiskGroupLegClaimReceipt,
        *,
        require_unresolved: bool,
    ) -> GroupLegClaimReceipt:
        if (
            type(claim) is not RiskGroupLegClaimReceipt
            or claim.scope != target.account_scope
            or claim.group_id != permit.group_id
            or claim.leg_id != permit.leg_id
            or claim.group_fingerprint != permit.group_fingerprint
            or claim.policy_fingerprint != permit.policy_fingerprint
            or claim.deadline_ns != permit.deadline_ns
            or claim.permit.permit_id != permit.permit_id
            or claim.permit.intent_id != permit.intent_id
            or claim.reserved_intent_hash != permit.reserved_intent_hash
            or claim.actual_intent_hash != mapped.fingerprint
            or claim.exposure_profile_fingerprint != permit.exposure_profile_fingerprint
            or claim.claim.intent_id != intent.intent_id
            or claim.claim.permit_id != permit.permit_id
            or claim.claim.intent_hash != permit.reserved_intent_hash
            or claim.claim.scope != target.account_scope
            or claim.claim.cause_id != "dispatch-inflight:" + intent.intent_id
            or claim.claim.claim_digest == _ZERO_SHA256
            or claim.actual_quantity != mapped.quantity
            or claim.actual_notional != mapped.notional
            or require_unresolved
            and claim.resolved
        ):
            raise GroupProofSourceError("GROUP_RISK_CLAIM_MISMATCH")
        try:
            return GroupLegClaimReceipt(
                permit=permit,
                actual_child_intent_hash=mapped.fingerprint,
                claim_digest=claim.claim.claim_digest,
                actual_quantity=claim.actual_quantity,
                actual_notional=claim.actual_notional,
            )
        except Exception:
            raise GroupProofSourceError("GROUP_RISK_CLAIM_INVALID") from None

    def _reservation_is_claimable(
        self, target: GroupProofTarget, permit: GroupLegPermitReference
    ) -> None:
        snapshot = self._read_group_snapshot(target, permit.group_id)
        leg = self._reservation_leg(snapshot, permit.leg_id)
        now = self._clock_ns()
        if (
            type(now) is not int
            or snapshot.phase.value != "ACTIVE"
            or snapshot.deadline_ns != permit.deadline_ns
            or now >= permit.deadline_ns
            or leg.state.value != "ACTIVE"
            or leg.claimed
            or leg.resolved
            or leg.expires_at_ns <= now
            or leg.permit_id != permit.permit_id
            or leg.intent_id != permit.intent_id
            or leg.reserved_intent_hash != permit.reserved_intent_hash
            or leg.exposure_profile_fingerprint != permit.exposure_profile_fingerprint
        ):
            raise GroupProofSourceError("GROUP_RESERVATION_NOT_CLAIMABLE")

    def _risk_receipt(
        self,
        target: GroupProofTarget,
        permit: GroupLegPermitReference,
        *,
        positive: bool,
    ) -> DispatchExposureResolutionReceipt | DispatchTerminalResolutionReceipt:
        bundle = self._runtime_bundle
        if bundle is None:
            raise GroupProofSourceError("GROUP_RUNTIME_BUNDLE_UNBOUND")
        bundle._assert_reservations_only_policy()
        try:
            if positive:
                receipt = bundle.risk_admission.dispatch_exposure_resolution_receipt(
                    permit.permit_id
                )
                if (
                    type(receipt) is not DispatchExposureResolutionReceipt
                    or receipt.status
                    is not DispatchExposureResolutionReceiptStatus.TERMINAL_EXPOSURE_RESOLVED
                ):
                    raise GroupProofSourceError("GROUP_RISK_RESOLUTION_UNRESOLVED")
            else:
                receipt = bundle.risk_admission.dispatch_terminal_resolution_receipt(
                    permit.permit_id
                )
                if (
                    type(receipt) is not DispatchTerminalResolutionReceipt
                    or receipt.status
                    is not DispatchTerminalResolutionReceiptStatus.NO_FILL_TERMINAL_RESOLVED
                ):
                    raise GroupProofSourceError("GROUP_RISK_RESOLUTION_UNRESOLVED")
        except GroupProofSourceError:
            raise
        except Exception:
            raise GroupProofSourceError("GROUP_RISK_RESOLUTION_UNAVAILABLE") from None
        return receipt

    def _resolution_projection(
        self,
        *,
        target: GroupProofTarget,
        permit: GroupLegPermitReference,
        intent: OrderIntent,
        claim_projection: GroupLegClaimReceipt,
        risk_receipt: DispatchExposureResolutionReceipt | DispatchTerminalResolutionReceipt,
        status: GroupChildOrderStatus,
        source_event: GroupProofJournalEvent,
    ) -> GroupDispatchResolutionReceipt:
        if (
            risk_receipt.claim.scope != target.account_scope
            or risk_receipt.claim.permit_id != permit.permit_id
            or risk_receipt.claim.intent_id != intent.intent_id
        ):
            raise GroupProofSourceError("GROUP_RISK_RECEIPT_MISMATCH")
        if type(risk_receipt) is DispatchExposureResolutionReceipt:
            try:
                final_state = ExecutionState(risk_receipt.final_state.value)
            except (TypeError, ValueError):
                raise GroupProofSourceError("GROUP_RISK_RECEIPT_MISMATCH") from None
            expected_filled = (
                intent.quantity
                if final_state is ExecutionState.FILLED
                else status.filled_quantity
            )
            if (
                risk_receipt.status
                is not DispatchExposureResolutionReceiptStatus.TERMINAL_EXPOSURE_RESOLVED
                or risk_receipt.dispatch_latch_active
                or risk_receipt.claim.claim_digest != claim_projection.claim_digest
                or final_state not in {ExecutionState.FILLED, ExecutionState.CANCELLED}
                or status.state is not final_state
                or risk_receipt.requested_quantity != intent.quantity
                or risk_receipt.filled_quantity != expected_filled
                or risk_receipt.trade_count != status.trade_count
                or risk_receipt.order_revision != status.order_revision
                or risk_receipt.journal_revision != source_event.revision
            ):
                raise GroupProofSourceError("GROUP_RISK_RECEIPT_MISMATCH")
            resolution_kind = "TERMINAL_EXPOSURE_RESOLVED"
            proof_sha256 = risk_receipt.proof_sha256
            exposure_proof_sha256 = risk_receipt.exposure_proof_sha256
            requested_quantity = risk_receipt.requested_quantity
            filled_quantity = risk_receipt.filled_quantity
            trade_count = risk_receipt.trade_count
            order_revision = risk_receipt.order_revision
            journal_revision = risk_receipt.journal_revision
            exposure_reservation_id = risk_receipt.exposure_reservation_id
            exposure_reservation_sha256 = risk_receipt.exposure_reservation_sha256
            has_other_active_freezes = risk_receipt.has_other_active_freezes
        else:
            proof = risk_receipt.proof
            expected_terminal_state = (
                DispatchTerminalState.CANCELED_NO_FILL
                if status.state is ExecutionState.CANCELLED
                else DispatchTerminalState.REJECTED_NO_FILL
            )
            expected_kind = _source_proof_kind(source_event.payload)
            if (
                risk_receipt.status
                is not DispatchTerminalResolutionReceiptStatus.NO_FILL_TERMINAL_RESOLVED
                or risk_receipt.dispatch_latch_active
                or risk_receipt.group_claim is None
                or not risk_receipt.group_claim.resolved
                or risk_receipt.claim.claim_digest != claim_projection.claim_digest
                or proof is None
                or proof.terminal_state is not expected_terminal_state
                or proof.dispatch_attempt_count != 1
                or proof.fee_record_count != 0
                or proof.fee_history_complete is not True
                or proof.filled_quantity != 0
                or proof.trade_count != 0
                or proof.journal_revision != source_event.revision
                or status.state not in {ExecutionState.REJECTED, ExecutionState.CANCELLED}
                or expected_kind
                != (
                    "CANCELED_NO_FILL"
                    if status.state is ExecutionState.CANCELLED
                    else "REJECTED_NO_FILL"
                )
                or status.filled_quantity != 0
                or status.trade_count != 0
            ):
                raise GroupProofSourceError("GROUP_RISK_RECEIPT_MISMATCH")
            resolution_kind = "TERMINAL_NO_FILL_RESOLVED"
            proof_sha256 = risk_receipt.proof_sha256
            exposure_proof_sha256 = None
            final_state = status.state
            requested_quantity = intent.quantity
            filled_quantity = Decimal("0")
            trade_count = 0
            # The coordinator compares this field against the child status
            # whenever the status carries one, and the frozen source validator
            # requires the status to echo the provider order revision even for
            # a zero-fill rejection, so the receipt must echo it as well.
            order_revision = status.order_revision
            journal_revision = proof.journal_revision
            exposure_reservation_id = None
            exposure_reservation_sha256 = None
            has_other_active_freezes = risk_receipt.has_other_active_freezes
        try:
            return GroupDispatchResolutionReceipt(
                claim=claim_projection,
                resolution_kind=resolution_kind,
                proof_sha256=proof_sha256,
                final_state=final_state,
                requested_quantity=requested_quantity,
                filled_quantity=filled_quantity,
                trade_count=trade_count,
                order_revision=order_revision,
                journal_revision=journal_revision,
                exposure_proof_sha256=exposure_proof_sha256,
                exposure_reservation_id=exposure_reservation_id,
                exposure_reservation_sha256=exposure_reservation_sha256,
                dispatch_latch_active=False,
                has_other_active_freezes=has_other_active_freezes,
            )
        except Exception:
            raise GroupProofSourceError("GROUP_RESOLUTION_PROJECTION_INVALID") from None

    def _read_receipt_locked(
        self,
        *,
        target: GroupProofTarget,
        binding: GroupPermitBinding,
        intent: OrderIntent,
        permit: GroupLegPermitReference,
        mapped: RiskIntent,
        principal_ref: str | None = None,
        identity: WireIdentity | None = None,
        wire_scope: WireScope | None = None,
        service_context: ServiceCallContext | None = None,
        active_authority: ExecutionGroupAuthorityGuard | None = None,
    ) -> GroupLegOrderReceipt | None:
        events = self._events(target, intent.intent_id)
        if not events:
            record = target.execution_store.get(intent.intent_id, scope=target.execution_scope)
            claim = self._query_claim(target, permit)
            if record is None and claim is None:
                return None
            if record is not None and (
                record.group_binding != binding or record.payload_sha256 != intent.fingerprint
            ):
                raise GroupProofSourceError("GROUP_EXECUTION_RECORD_MISMATCH")
            projected_claim = (
                None
                if claim is None
                else self._claim_projection(
                    target, intent, permit, mapped, claim, require_unresolved=False
                )
            )
            return GroupLegOrderReceipt(
                status=GroupChildOrderStatus(
                    intent_id=intent.intent_id,
                    state=ExecutionState.UNKNOWN,
                    filled_quantity=Decimal("0") if record is None else record.filled_quantity,
                    provider_order_id=None if record is None else record.provider_order_id,
                ),
                claim=projected_claim,
                resolution=None,
            )
        if (
            events[0].event_type != "ATTEMPT_STARTED"
            or events[0].group_id != permit.group_id
            or events[0].leg_id != permit.leg_id
            or any(
                event.group_id != permit.group_id
                or event.leg_id != permit.leg_id
                or event.account_scope_key != target.account_scope.key
                for event in events
            )
        ):
            raise GroupProofSourceError("GROUP_SOURCE_EVENT_CHAIN_INVALID")
        try:
            _validate_recovery_event_order(events)
        except GroupProofSourceError:
            raise GroupProofSourceError("GROUP_SOURCE_EVENT_CHAIN_INVALID")
        if (
            events[0].payload.get("binding") != binding.to_payload()
            or events[0].payload.get("intent_fingerprint") != intent.fingerprint
            or events[0].payload.get("intent_id") != intent.intent_id
            or events[0].payload.get("account_scope_key") != target.account_scope.key
            or events[0].payload.get("execution_scope_key") != target.execution_scope.key
        ):
            raise GroupProofSourceError("GROUP_SOURCE_BINDING_MISMATCH")
        claim = self._query_claim(target, permit)
        claim_projection = (
            None
            if claim is None
            else self._claim_projection(
                target, intent, permit, mapped, claim, require_unresolved=False
            )
        )
        record = target.execution_store.get(intent.intent_id, scope=target.execution_scope)
        if (
            record is None
            or record.dispatch_attempts != 1
            or record.group_binding != binding
            or record.payload_sha256 != intent.fingerprint
        ):
            raise GroupProofSourceError("GROUP_EXECUTION_RECORD_MISMATCH")
        unknown_event = next(
            (event for event in events if event.event_type == "ATTEMPT_UNKNOWN"), None
        )
        if claim_projection is None:
            return _unknown_group_receipt(intent, record, None)
        if unknown_event is not None and (
            unknown_event.payload.get("claim_digest") != claim_projection.claim_digest
        ):
            raise GroupProofSourceError("GROUP_UNKNOWN_EVENT_BINDING_MISMATCH")

        latest_source = _latest_source_event(events)
        if latest_source is None:
            return _unknown_group_receipt(intent, record, claim_projection)
        if latest_source.payload.get("status") is None:
            # Pre-v2 open-source rows did not persist a typed status. Keep them
            # query-only UNKNOWN until a fresh exact-source reconciliation
            # replaces that projection with a typed, hash-chained snapshot.
            return _unknown_group_receipt(intent, record, claim_projection)
        try:
            latest_status = GroupChildOrderStatus.from_payload(
                latest_source.payload.get("status")
            )
            latest_state = ExecutionState(latest_source.payload.get("state"))
        except Exception:
            raise GroupProofSourceError("GROUP_SOURCE_STATUS_INVALID") from None
        if latest_status.state is not latest_state:
            raise GroupProofSourceError("GROUP_SOURCE_STATUS_INVALID")
        if latest_state not in {
            ExecutionState.FILLED,
            ExecutionState.CANCELLED,
            ExecutionState.REJECTED,
        }:
            if any(event.event_type in {"PROOF_READY", "RISK_RESOLVED"} for event in events):
                raise GroupProofSourceError("GROUP_SOURCE_EVENT_CHAIN_INVALID")
            # An open order may be used as a cancellation target only while
            # the exact original dispatch claim remains active.  The public
            # accepted-reservation snapshot is immutable and can still say
            # ``claimed=False`` after dispatch, so bind this check to the live
            # Risk claim read above instead.
            claim_projection = self._claim_projection(
                target, intent, permit, mapped, claim, require_unresolved=True
            )
            session = _ACTIVE_SESSION.get()
            if (
                type(session) is not GroupProofDispatchSession
                or session.source is not self
                or session.intent.intent_id != intent.intent_id
            ):
                if (
                    type(principal_ref) is not str
                    or type(identity) is not WireIdentity
                    or type(wire_scope) is not WireScope
                    or type(service_context) is not ServiceCallContext
                    or type(active_authority) is not ExecutionGroupAuthorityGuard
                ):
                    raise GroupProofSourceError("GROUP_RECEIPT_CONTEXT_REQUIRED")
                session = GroupProofDispatchSession(
                    source=self,
                    target=target,
                    binding=binding,
                    intent=intent,
                    permit=permit,
                    mapped_intent=mapped,
                    principal_ref=principal_ref,
                    identity=identity,
                    wire_scope=wire_scope,
                    service_context=service_context,
                    active_authority=active_authority,
                )
                session._claim_receipt = claim
                session._claim_projection = claim_projection
                session._attempt_started = True
                session._attempt_unknown = unknown_event is not None
            elif session._claim_projection != claim_projection:
                raise GroupProofSourceError("GROUP_SESSION_CONTEXT_MISMATCH")
            session._source_event = latest_source
            session._status = latest_status
            session._validate_source_event(latest_source)
            if not _execution_record_matches_group_status(record, latest_status):
                return _unknown_group_receipt(intent, record, claim_projection)
            return GroupLegOrderReceipt(
                status=latest_status,
                claim=claim_projection,
                resolution=None,
            )
        source_event = _terminal_source_event(events)
        if source_event is None:
            return _unknown_group_receipt(intent, record, claim_projection)
        proof_events = [event for event in events if event.event_type == "PROOF_READY"]
        resolved_events = [event for event in events if event.event_type == "RISK_RESOLVED"]
        if len(proof_events) > 1 or len(resolved_events) > 1:
            raise GroupProofSourceError("GROUP_SOURCE_EVENT_CHAIN_INVALID")
        if not proof_events or not resolved_events:
            return _unknown_group_receipt(intent, record, claim_projection)

        # Readback rebuilds source validation and the typed proof from the
        # source-bound request, immutable event payloads and the event's own
        # persisted Risk fence. Current writer epochs may differ after restart.
        session = _ACTIVE_SESSION.get()
        if (
            type(session) is not GroupProofDispatchSession
            or session.source is not self
            or session.intent.intent_id != intent.intent_id
        ):
            if (
                type(principal_ref) is not str
                or type(identity) is not WireIdentity
                or type(wire_scope) is not WireScope
                or type(service_context) is not ServiceCallContext
                or type(active_authority) is not ExecutionGroupAuthorityGuard
            ):
                raise GroupProofSourceError("GROUP_RECEIPT_CONTEXT_REQUIRED")
            session = GroupProofDispatchSession(
                source=self,
                target=target,
                binding=binding,
                intent=intent,
                permit=permit,
                mapped_intent=mapped,
                principal_ref=principal_ref,
                identity=identity,
                wire_scope=wire_scope,
                service_context=service_context,
                active_authority=active_authority,
            )
            session._claim_receipt = claim
            session._claim_projection = claim_projection
            session._attempt_started = True
            session._attempt_unknown = unknown_event is not None
        elif session._claim_projection != claim_projection:
            raise GroupProofSourceError("GROUP_SESSION_CONTEXT_MISMATCH")

        try:
            status = GroupChildOrderStatus.from_payload(source_event.payload.get("status"))
            state = ExecutionState(source_event.payload.get("state"))
        except Exception:
            raise GroupProofSourceError("GROUP_SOURCE_STATUS_INVALID") from None
        if status.state is not state:
            raise GroupProofSourceError("GROUP_SOURCE_STATUS_INVALID")
        session._source_event = source_event
        session._status = status
        session._validate_source_event(source_event)
        if not _execution_record_matches_group_status(record, status):
            return GroupLegOrderReceipt(
                status=GroupChildOrderStatus(
                    intent_id=intent.intent_id,
                    state=ExecutionState.UNKNOWN,
                    filled_quantity=record.filled_quantity,
                    provider_order_id=record.provider_order_id,
                ),
                claim=claim_projection,
                resolution=None,
            )
        proof_event = proof_events[0]
        proof_payload = proof_event.payload
        if (
            proof_event.revision <= source_event.revision
            or proof_payload.get("source_revision") != source_event.revision
            or proof_payload.get("source_record_sha256") != source_event.record_sha256
            or proof_payload.get("claim_digest") != claim.claim.claim_digest
            or proof_payload.get("contract_sha256") != target.provider_contract.fingerprint
            or proof_payload.get("proof_kind")
            != _source_proof_kind(source_event.payload)
        ):
            raise GroupProofSourceError("GROUP_PROOF_BINDING_MISMATCH")
        proof: DispatchExposureResolutionProof | DispatchTerminalProof | None
        try:
            proof = session._proof_from_ready_event(source_event, proof_event)
        except GroupProofSourceError as error:
            # Older completed exposure receipts predate persisted observation
            # times. They remain readable only when the hash-verified terminal
            # Risk receipt independently binds the immutable PROOF_READY hash;
            # an unresolved legacy proof cannot be reconstructed or released.
            if (
                error.code != "GROUP_PROOF_RECOVERY_FACTS_MISSING"
                or state not in {ExecutionState.FILLED, ExecutionState.CANCELLED}
                or type(proof_payload.get("proof_sha256")) is not str
            ):
                raise GroupProofSourceError("GROUP_PROOF_BINDING_MISMATCH") from None
            proof = None
        except Exception:
            raise GroupProofSourceError("GROUP_PROOF_BINDING_MISMATCH") from None
        try:
            risk_receipt = self._risk_receipt(
                target,
                permit,
                positive=(
                    state is ExecutionState.FILLED
                    or (
                        state is ExecutionState.CANCELLED
                        and status.filled_quantity > 0
                    )
                ),
            )
        except GroupProofSourceError as error:
            if error.code in {
                "GROUP_RISK_RESOLUTION_UNRESOLVED",
                "GROUP_RISK_RESOLUTION_UNAVAILABLE",
            }:
                return GroupLegOrderReceipt(
                    status=GroupChildOrderStatus(
                        intent_id=intent.intent_id,
                        state=ExecutionState.UNKNOWN,
                        filled_quantity=status.filled_quantity,
                        provider_order_id=status.provider_order_id,
                    ),
                    claim=claim_projection,
                    resolution=None,
                )
            raise
        proof_sha256 = proof_payload.get("proof_sha256")
        if (
            (proof is not None and proof_sha256 != proof.fingerprint)
            or proof_sha256 != risk_receipt.proof_sha256
        ):
            raise GroupProofSourceError("GROUP_RISK_PROOF_RECEIPT_MISMATCH")
        resolution = self._resolution_projection(
            target=target,
            permit=permit,
            intent=intent,
            claim_projection=claim_projection,
            risk_receipt=risk_receipt,
            status=status,
            source_event=source_event,
        )
        resolved_event = resolved_events[0]
        expected_resolved_payload = {
            "proof_ready_revision": proof_event.revision,
            "proof_ready_record_sha256": proof_event.record_sha256,
            "proof_sha256": proof_sha256,
            "resolution": resolution.to_payload(),
            "source_revision": source_event.revision,
            "source_record_sha256": source_event.record_sha256,
        }
        if (
            resolved_event.revision <= proof_event.revision
            or dict(resolved_event.payload) != expected_resolved_payload
        ):
            raise GroupProofSourceError("GROUP_RESOLVED_EVENT_MISMATCH")
        return GroupLegOrderReceipt(status=status, claim=claim_projection, resolution=resolution)

    def read_receipt(
        self,
        *,
        binding: GroupPermitBinding,
        intent: OrderIntent,
        permit: GroupLegPermitReference,
        principal_ref: str,
        identity: WireIdentity,
        wire_scope: WireScope,
        service_context: ServiceCallContext,
        active_authority: ExecutionGroupAuthorityGuard,
    ) -> GroupLegOrderReceipt | None:
        target, _template = self._validate_call(
            wire_scope=wire_scope,
            principal_ref=principal_ref,
            identity=identity,
            service_context=service_context,
        )
        if type(active_authority) is not ExecutionGroupAuthorityGuard:
            raise GroupProofSourceError("GROUP_ACTIVE_AUTHORITY_REQUIRED")
        mapped, expected = self._map_intent(
            target=target, wire_scope=wire_scope, intent=intent, permit=permit
        )
        if type(binding) is not GroupPermitBinding or binding != expected:
            raise GroupProofSourceError("GROUP_PERMIT_BINDING_MISMATCH")
        current = _ACTIVE_SESSION.get()
        if current is not None:
            if current.source is not self or current.intent.intent_id != intent.intent_id:
                raise GroupProofSourceError("GROUP_SESSION_CONTEXT_MISMATCH")
            return self._read_receipt_locked(
                target=target,
                binding=binding,
                intent=intent,
                permit=permit,
                mapped=mapped,
                principal_ref=principal_ref,
                identity=identity,
                wire_scope=wire_scope,
                service_context=service_context,
                active_authority=active_authority,
            )
        try:
            with target.risk_order_journal.writer_fence(target.account_scope):
                return self._read_receipt_locked(
                    target=target,
                    binding=binding,
                    intent=intent,
                    permit=permit,
                    mapped=mapped,
                    principal_ref=principal_ref,
                    identity=identity,
                    wire_scope=wire_scope,
                    service_context=service_context,
                    active_authority=active_authority,
                )
        except GroupProofSourceError:
            raise
        except Exception:
            raise GroupProofSourceError("GROUP_RECEIPT_READ_UNAVAILABLE") from None

    def resolve_receipt(self, **kwargs: Any) -> GroupLegOrderReceipt | None:
        """Read an already-proven resolution; this method never advances state."""

        return self.read_receipt(**kwargs)

    def verify_authority_reconciliation(
        self,
        hold: GroupDispatchHold,
        proof: object,
        *,
        active_authority: ExecutionGroupAuthorityGuard,
        context: ServiceCallContext,
        execution_scope: ExecutionScope,
    ) -> bool:
        """Verify one exact terminal receipt before the authority releases its hold.

        The method accepts no new authorization data.  It reconstructs the
        permit and binding from the hash-verified original ATTEMPT_STARTED
        event, then checks the current Registry context, persisted hold, exact
        Execution row, complete source/proof chain and terminal Risk receipt.
        """

        if (
            type(hold) is not GroupDispatchHold
            or type(proof) is not GroupLegOrderReceipt
            or proof.resolution is None
            or type(context) is not ServiceCallContext
            or type(execution_scope) is not ExecutionScope
            or type(active_authority) is not ExecutionGroupAuthorityGuard
            or context.registration_id != hold.registration_id
            or context.principal_ref != hold.principal_ref
            or context.identity.role is not WireRole.SERVICE
            or hold.execution_scope_key != execution_scope.key
            or hold.account_ref != execution_scope.account_ref
            or hold.strategy_id != execution_scope.strategy_id
        ):
            return False
        matches = [
            target
            for target in self._targets.values()
            if target.execution_scope == execution_scope
            and risk_account_scope_fingerprint(target.account_scope.key)
            == hold.risk_scope_fingerprint
        ]
        if len(matches) != 1:
            return False
        target = matches[0]
        try:
            events = self._events(target, hold.intent_id)
            if not events or events[0].event_type != "ATTEMPT_STARTED":
                return False
            started = events[0]
            wire_scope = replace(
                context.scope, group_id=started.group_id, leg_id=started.leg_id
            )
            principal_ref = context.principal_ref
            identity = context.identity
            if (
                started.payload.get("intent_id") != hold.intent_id
                or started.payload.get("principal_ref") != principal_ref
                or started.payload.get("identity") != _identity_payload(identity)
                or started.payload.get("wire_scope") != _wire_payload(wire_scope)
                or started.payload.get("execution_scope_key") != execution_scope.key
                or started.payload.get("account_scope_key") != target.account_scope.key
            ):
                return False
            intent = target.execution_store.get_intent(
                hold.intent_id, scope=execution_scope
            )
            if type(intent) is not OrderIntent:
                return False
            permit = GroupLegPermitReference.from_payload(started.payload.get("permit"))
            binding = GroupPermitBinding.from_payload(started.payload.get("binding"))
            mapped, expected_binding = self._map_intent(
                target=target, wire_scope=wire_scope, intent=intent, permit=permit
            )
            if binding != expected_binding:
                return False
            authorization = self._validate_recovery_attempt(
                target=target,
                started=started,
                binding=binding,
                intent=intent,
                permit=permit,
                principal_ref=principal_ref,
                identity=identity,
                wire_scope=wire_scope,
            )
            self._validate_recovery_hold(
                hold=hold,
                authorization=authorization,
                started=started,
                target=target,
                binding=binding,
                intent=intent,
                permit=permit,
                principal_ref=principal_ref,
            )
            expected_receipt = self.read_receipt(
                binding=binding,
                intent=intent,
                permit=permit,
                principal_ref=principal_ref,
                identity=identity,
                wire_scope=wire_scope,
                service_context=context,
                active_authority=active_authority,
            )
            record = target.execution_store.get(hold.intent_id, scope=execution_scope)
            return (
                type(expected_receipt) is GroupLegOrderReceipt
                and expected_receipt == proof
                and expected_receipt.resolution is not None
                and expected_receipt.status.state
                in {ExecutionState.FILLED, ExecutionState.CANCELLED, ExecutionState.REJECTED}
                and type(record) is ExecutionRecord
                and record.dispatch_attempts == 1
                and record.group_binding == binding
                and record.payload_sha256 == intent.fingerprint
                and _execution_record_matches_group_status(record, expected_receipt.status)
            )
        except Exception:
            return False

    def reconcile_unknown(
        self,
        *,
        binding: GroupPermitBinding,
        intent: OrderIntent,
        permit: GroupLegPermitReference,
        principal_ref: str,
        identity: WireIdentity,
        wire_scope: WireScope,
        service_context: ServiceCallContext,
        active_authority: ExecutionGroupAuthorityGuard,
        acquire_writer_lease: Any,
    ) -> GroupLegOrderReceipt:
        """Read-only reconcile an exact accepted attempt and finish its proof chain.

        This method never calls ``make_order``.  It reads at most the original
        provider client order id and its complete fill history, then records a
        terminal Execution observation and Risk proof only after the source
        facts pass the same normalizer/provenance checks used by submit.
        """

        if (
            type(active_authority) is not ExecutionGroupAuthorityGuard
            or not callable(acquire_writer_lease)
        ):
            raise GroupProofSourceError("GROUP_RECOVERY_AUTHORITY_INVALID")
        target, _template = self._validate_call(
            wire_scope=wire_scope,
            principal_ref=principal_ref,
            identity=identity,
            service_context=service_context,
        )
        mapped, expected_binding = self._map_intent(
            target=target, wire_scope=wire_scope, intent=intent, permit=permit
        )
        if type(binding) is not GroupPermitBinding or binding != expected_binding:
            raise GroupProofSourceError("GROUP_PERMIT_BINDING_MISMATCH")

        events = self._events(target, intent.intent_id)
        current_record = target.execution_store.get(intent.intent_id, scope=target.execution_scope)
        existing = self.read_receipt(
            binding=binding,
            intent=intent,
            permit=permit,
            principal_ref=principal_ref,
            identity=identity,
            wire_scope=wire_scope,
            service_context=service_context,
            active_authority=active_authority,
        )
        if not events or events[0].event_type != "ATTEMPT_STARTED":
            if existing is not None:
                return existing
            raise GroupProofSourceError("GROUP_ATTEMPT_SOURCE_MISSING")

        started = events[0]
        attempt = self._validate_recovery_attempt(
            target=target,
            started=started,
            binding=binding,
            intent=intent,
            permit=permit,
            principal_ref=principal_ref,
            identity=identity,
            wire_scope=wire_scope,
        )
        hold_id = attempt.authorization_attempt_id
        hold_state = active_authority.reconciliation_hold_state(hold_id)
        if hold_state == "active":
            if existing is not None:
                return existing
            raise GroupProofSourceError("GROUP_DISPATCH_HOLD_ACTIVE")
        if hold_state == "absent":
            if existing is not None:
                return existing
            raise GroupProofSourceError("GROUP_DISPATCH_HOLD_MISSING")
        if hold_state == "reconciled":
            if existing is None or existing.resolution is None:
                raise GroupProofSourceError("GROUP_RECONCILIATION_TOMBSTONE_UNPROVEN")
            active_authority.reconcile_hold(
                hold_id,
                existing,
                context=service_context,
                execution_scope=target.execution_scope,
            )
            return existing
        if hold_state != "pending":
            raise GroupProofSourceError("GROUP_DISPATCH_HOLD_STATE_INVALID")

        hold = active_authority.read_reconciliation_hold(
            hold_id, context=service_context, execution_scope=target.execution_scope
        )
        self._validate_recovery_hold(
            hold=hold,
            authorization=attempt,
            started=started,
            target=target,
            binding=binding,
            intent=intent,
            permit=permit,
            principal_ref=principal_ref,
        )
        if existing is not None and existing.resolution is not None:
            active_authority.reconcile_hold(
                hold_id,
                existing,
                context=service_context,
                execution_scope=target.execution_scope,
            )
            return existing

        session = GroupProofDispatchSession(
            source=self,
            target=target,
            binding=binding,
            intent=intent,
            permit=permit,
            mapped_intent=mapped,
            principal_ref=principal_ref,
            identity=identity,
            wire_scope=wire_scope,
            service_context=service_context,
            active_authority=active_authority,
        )
        if (
            started.payload.get("accepted_request_sha256") != session._accepted_request_sha256
            or started.payload.get("provider_request") != session._request.to_dict()
            or started.payload.get("client_order_id") != session._request.client_order_id
            or started.payload.get("account_scope_key") != target.account_scope.key
            or started.payload.get("execution_scope_key") != target.execution_scope.key
        ):
            raise GroupProofSourceError("GROUP_ATTEMPT_REQUEST_BINDING_MISMATCH")

        session._recovery_hold = hold
        session._attempt_started = True
        session._attempt_unknown = True
        session._claim_receipt = self._query_claim(target, permit)
        if type(session._claim_receipt) is not RiskGroupLegClaimReceipt:
            raise GroupProofSourceError("GROUP_RISK_CLAIM_MISSING")
        session._claim_projection = self._claim_projection(
            target,
            intent,
            permit,
            mapped,
            session._claim_receipt,
            require_unresolved=False,
        )
        try:
            session._risk_fence_context = target.risk_order_journal.writer_fence(
                target.account_scope
            )
            session._risk_fence = session._risk_fence_context.__enter__()
            if type(session._risk_fence) is not RiskOrderWriterFence:
                raise GroupProofSourceError("GROUP_RISK_FENCE_UNAVAILABLE")
            session._context_token = _ACTIVE_SESSION.set(session)
            session._assert_current()
            events = self._events(target, intent.intent_id)
            unknown_event = next(
                (event for event in events if event.event_type == "ATTEMPT_UNKNOWN"), None
            )
            source_event = _terminal_source_event(events)
            if unknown_event is None:
                prior_source_event = next(
                    (
                        event
                        for event in reversed(events)
                        if event.event_type in {"SOURCE_OBSERVED", "SOURCE_RECONCILED"}
                    ),
                    None,
                )
                unknown_event = self._append_event(
                    target=target,
                    fence=session._risk_fence,
                    intent=intent,
                    permit=permit,
                    event_type="ATTEMPT_UNKNOWN",
                    payload={
                        "claim_digest": session._claim_projection.claim_digest,
                        "reason_code": "GROUP_OWNER_RECOVERY_REQUIRED",
                        "source_revision": None
                        if prior_source_event is None
                        else prior_source_event.revision,
                        "source_record_sha256": None
                        if prior_source_event is None
                        else prior_source_event.record_sha256,
                    },
                )
                events = self._events(target, intent.intent_id)
                source_event = _terminal_source_event(events)

            proof_already_ready = any(
                event.event_type == "PROOF_READY" for event in events
            )
            refresh_source = source_event is None or (
                source_event.event_type == "SOURCE_OBSERVED" and not proof_already_ready
            )
            if source_event is not None:
                session._validate_source_event(source_event)
                try:
                    status = GroupChildOrderStatus.from_payload(
                        source_event.payload.get("status")
                    )
                    state = ExecutionState(source_event.payload.get("state"))
                except Exception:
                    raise GroupProofSourceError("GROUP_SOURCE_STATUS_INVALID") from None
                if status.state is not state:
                    raise GroupProofSourceError("GROUP_SOURCE_STATUS_INVALID")

            if refresh_source:
                order_request = QueryOrderRequest(
                    symbol=session._request.symbol,
                    account_id=session._request.account_id,
                    client_order_id=session._request.client_order_id,
                )
                try:
                    order = target.bt_api.query_order(
                        target.provider_contract.exchange_name,
                        order_request,
                        normalized=True,
                    )
                    fills = target.bt_api.get_deals(
                        target.provider_contract.exchange_name,
                        session._request.symbol,
                        normalized=True,
                        client_order_id=session._request.client_order_id,
                        account_id=session._request.account_id,
                    )
                    facts = session._validate_provider_source(
                        order,
                        fills,
                        source_observed_at_ns=self._clock_ns(),
                    )
                except Exception:
                    return self._read_recovery_receipt(session)
                state = facts["state"]
                if state in {
                    ExecutionState.ACKED,
                    ExecutionState.PARTIALLY_FILLED,
                    ExecutionState.UNKNOWN,
                }:
                    status = session._status_from_facts(facts, state)
                    session._facts = facts
                    session._status = status
                    source_payload = session._source_event_payload(
                        facts, state=state, status=status
                    )
                    latest_source = _latest_source_event(events)
                    if latest_source is not None:
                        session._validate_source_event(latest_source)
                    append_progress = _source_progress_is_new(
                        latest_source, source_payload
                    )
                    current_record = target.execution_store.get(
                        intent.intent_id, scope=target.execution_scope
                    )
                    if type(current_record) is not ExecutionRecord or (
                        current_record.dispatch_attempts != 1
                        or current_record.group_binding != binding
                        or current_record.payload_sha256 != intent.fingerprint
                    ):
                        raise GroupProofSourceError("GROUP_EXECUTION_RECORD_MISMATCH")
                    if not _execution_record_matches_group_status(current_record, status):
                        lease = acquire_writer_lease()
                        if (
                            type(lease) is not WriterLease
                            or lease.scope_key != target.execution_scope.account_key
                        ):
                            raise GroupProofSourceError(
                                "GROUP_EXECUTION_WRITER_LEASE_UNAVAILABLE"
                            )
                        session._writer_lease = lease
                        session._assert_current()
                        try:
                            current_record = target.execution_store.record_observation(
                                session._provider_observation(status),
                                scope=target.execution_scope,
                                source="provider",
                                writer_lease=lease,
                            )
                        except Exception:
                            raise GroupProofSourceError(
                                "GROUP_EXECUTION_RECONCILIATION_UNCERTAIN"
                            ) from None
                        if not _execution_record_matches_group_status(current_record, status):
                            raise GroupProofSourceError(
                                "GROUP_EXECUTION_OBSERVATION_MISMATCH"
                            )
                    session._assert_current()
                    if append_progress:
                        session._source_event = self._append_event(
                            target=target,
                            fence=session._risk_fence,
                            intent=intent,
                            permit=permit,
                            event_type="SOURCE_PROGRESS",
                            payload=source_payload,
                        )
                    else:
                        session._source_event = latest_source
                    return self._read_recovery_receipt(session)
                if state not in {
                    ExecutionState.FILLED,
                    ExecutionState.REJECTED,
                    ExecutionState.CANCELLED,
                }:
                    return self._read_recovery_receipt(session)
                status = session._status_from_facts(facts, state)
                session._facts = facts
                source_payload = session._source_event_payload(
                    facts,
                    state=state,
                    status=status,
                    include_observation_window=True,
                )
                # A normal SOURCE_OBSERVED terminal fact may be old by the
                # time its interrupted caller is recovered. Preserve that
                # hash-chained event and append a fresh, exact-ID read below;
                # never renew its proof interval using the current clock.
                source_event = None
            else:
                facts = None
                source_payload = None

            if status.state not in {
                ExecutionState.FILLED,
                ExecutionState.REJECTED,
                ExecutionState.CANCELLED,
            }:
                return self._read_recovery_receipt(session)
            session._status = status
            current_record = target.execution_store.get(
                intent.intent_id, scope=target.execution_scope
            )
            if type(current_record) is not ExecutionRecord or (
                current_record.dispatch_attempts != 1
                or current_record.group_binding != binding
                or current_record.payload_sha256 != intent.fingerprint
            ):
                raise GroupProofSourceError("GROUP_EXECUTION_RECORD_MISMATCH")
            if not _execution_record_matches_group_status(current_record, status):
                # ExecutionStore writer leases are account-keyed; the group
                # hold separately pins the full strategy-specific scope.
                lease = acquire_writer_lease()
                if (
                    type(lease) is not WriterLease
                    or lease.scope_key != target.execution_scope.account_key
                ):
                    raise GroupProofSourceError("GROUP_EXECUTION_WRITER_LEASE_UNAVAILABLE")
                session._writer_lease = lease
                session._assert_current()
                try:
                    current_record = target.execution_store.record_observation(
                        session._provider_observation(status),
                        scope=target.execution_scope,
                        source="provider",
                        writer_lease=lease,
                    )
                except Exception:
                    raise GroupProofSourceError("GROUP_EXECUTION_RECONCILIATION_UNCERTAIN") from None
                if not _execution_record_matches_group_status(current_record, status):
                    raise GroupProofSourceError("GROUP_EXECUTION_OBSERVATION_MISMATCH")
            elif session._writer_lease is None:
                # Proof finalization is fenced by the current owner lease even
                # when the terminal Execution row survived the prior crash.
                # ExecutionStore keys that lease by account, not strategy.
                lease = acquire_writer_lease()
                if (
                    type(lease) is not WriterLease
                    or lease.scope_key != target.execution_scope.account_key
                ):
                    raise GroupProofSourceError("GROUP_EXECUTION_WRITER_LEASE_UNAVAILABLE")
                session._writer_lease = lease
            session._assert_current()

            if source_event is None:
                source_event = self._append_event(
                    target=target,
                    fence=session._risk_fence,
                    intent=intent,
                    permit=permit,
                    event_type="SOURCE_RECONCILED",
                    payload=source_payload,
                )
                events = self._events(target, intent.intent_id)
            session._source_event = source_event
            proof_event = next(
                (event for event in events if event.event_type == "PROOF_READY"), None
            )
            proof: DispatchExposureResolutionProof | DispatchTerminalProof | None
            if proof_event is None:
                try:
                    proof, proof_payload = session._build_terminal_proof_for_recovery(
                        source_event
                    )
                except GroupProofSourceError:
                    # The provider terminal snapshot or its observation window
                    # is insufficient for a current proof. Report UNKNOWN and
                    # preserve the original hold; never refresh source time
                    # from a later clock read.
                    return _unknown_group_receipt(
                        intent, current_record, session._claim_projection
                    )
                proof_event = self._append_event(
                    target=target,
                    fence=session._risk_fence,
                    intent=intent,
                    permit=permit,
                    event_type="PROOF_READY",
                    payload=proof_payload,
                )
                events = self._events(target, intent.intent_id)
            else:
                try:
                    proof = session._proof_from_ready_event(source_event, proof_event)
                except GroupProofSourceError as error:
                    proof_payload = proof_event.payload
                    if (
                        error.code != "GROUP_PROOF_RECOVERY_FACTS_MISSING"
                        or status.state
                        not in {ExecutionState.FILLED, ExecutionState.CANCELLED}
                        or type(proof_payload.get("proof_sha256")) is not str
                    ):
                        raise
                    # An older PROOF_READY record may not have retained the
                    # observation interval needed to reconstruct its typed
                    # proof. It is safe to finish only an already-committed
                    # Risk transition whose durable receipt binds this exact
                    # hash; never synthesize a replacement proof.
                    proof = None
            session._assert_current()

            positive_exposure = type(proof) is DispatchExposureResolutionProof or (
                proof is None
                and (
                    status.state is ExecutionState.FILLED
                    or (
                        status.state is ExecutionState.CANCELLED
                        and status.filled_quantity > 0
                    )
                )
            )
            try:
                risk_receipt = self._existing_risk_terminal_receipt(
                    target, permit, positive=positive_exposure
                )
            except GroupProofSourceError:
                # Before the first Risk resolution, the reservation may
                # still be ACTIVE even though its exact dispatch claim,
                # terminal Execution row, source event, and typed proof are
                # now durably established. Handle that state below using
                # Risk's recovery-only settlement API.
                risk_receipt = None
            if risk_receipt is None:
                if proof is None:
                    return self._read_recovery_receipt(session)
                source_valid_until_ns = (
                    proof.observation.valid_until_ns
                    if type(proof) is DispatchExposureResolutionProof
                    else source_event.payload.get("source_valid_until_ns")
                )
                reconciliation_now_ns = self._clock_ns()
                if (
                    type(source_valid_until_ns) is not int
                    or type(reconciliation_now_ns) is not int
                    or reconciliation_now_ns >= source_valid_until_ns
                ):
                    # A durable proof hash alone cannot renew an expired or
                    # legacy source snapshot. Only a Risk transaction already
                    # committed against this exact proof may finish its later
                    # journal/tombstone steps.
                    return _unknown_group_receipt(
                        intent, current_record, session._claim_projection
                    )
                try:
                    # This is deliberately after the fresh source proof and
                    # before the receipt/resolution transaction. The Risk
                    # operation accepts only a claimed ACTIVE/SETTLED permit
                    # and never clears the original dispatch latch.
                    self._runtime_bundle.risk_admission.ensure_settled(
                        permit.permit_id
                    )
                except Exception:
                    # A prior owner may have committed the terminal Risk
                    # transaction already. The exact typed receipt read below
                    # decides whether that outcome can continue; otherwise
                    # this recovery remains UNKNOWN with its hold retained.
                    pass
                try:
                    risk_receipt = self._existing_risk_terminal_receipt(
                        target, permit, positive=positive_exposure
                    )
                except GroupProofSourceError:
                    return self._read_recovery_receipt(session)
            if risk_receipt is None:
                if proof is None:
                    return self._read_recovery_receipt(session)
                try:
                    if type(proof) is DispatchExposureResolutionProof:
                        self._runtime_bundle.risk_admission.resolve_dispatch_exposure(proof)
                    else:
                        self._runtime_bundle.risk_admission.resolve_dispatch_freeze(proof)
                except Exception:
                    # The transaction may have committed before its response
                    # was lost; only an exact subsequent receipt can settle it.
                    pass
                try:
                    risk_receipt = self._existing_risk_terminal_receipt(
                        target, permit, positive=positive_exposure
                    )
                except GroupProofSourceError:
                    return self._read_recovery_receipt(session)
            proof_sha256 = (
                proof.fingerprint if proof is not None else proof_event.payload.get("proof_sha256")
            )
            if risk_receipt is None or getattr(risk_receipt, "proof_sha256", None) != proof_sha256:
                return self._read_recovery_receipt(session)
            resolution = self._resolution_projection(
                target=target,
                permit=permit,
                intent=intent,
                claim_projection=session._claim_projection,
                risk_receipt=risk_receipt,
                status=status,
                source_event=source_event,
            )
            resolved_event = next(
                (event for event in events if event.event_type == "RISK_RESOLVED"), None
            )
            expected_resolved_payload = {
                "proof_ready_revision": proof_event.revision,
                "proof_ready_record_sha256": proof_event.record_sha256,
                "proof_sha256": proof_sha256,
                "resolution": resolution.to_payload(),
                "source_revision": source_event.revision,
                "source_record_sha256": source_event.record_sha256,
            }
            if resolved_event is None:
                self._append_event(
                    target=target,
                    fence=session._risk_fence,
                    intent=intent,
                    permit=permit,
                    event_type="RISK_RESOLVED",
                    payload=expected_resolved_payload,
                )
            elif dict(resolved_event.payload) != expected_resolved_payload:
                raise GroupProofSourceError("GROUP_RESOLVED_EVENT_MISMATCH")
            session._assert_current()
            receipt = self._read_receipt_locked(
                target=target,
                binding=binding,
                intent=intent,
                permit=permit,
                mapped=mapped,
            )
            if type(receipt) is not GroupLegOrderReceipt or receipt.resolution != resolution:
                raise GroupProofSourceError("GROUP_TERMINAL_RECEIPT_UNPROVEN")
            active_authority.reconcile_hold(
                hold_id,
                receipt,
                context=service_context,
                execution_scope=target.execution_scope,
            )
            return receipt
        finally:
            if session._context_token is not None:
                _ACTIVE_SESSION.reset(session._context_token)
                session._context_token = None
            if session._risk_fence_context is not None:
                with suppress(Exception):
                    session._risk_fence_context.__exit__(None, None, None)
                session._risk_fence_context = None

    def _read_recovery_receipt(
        self, session: GroupProofDispatchSession
    ) -> GroupLegOrderReceipt:
        receipt = self._read_receipt_locked(
            target=session.target,
            binding=session.binding,
            intent=session.intent,
            permit=session.permit,
            mapped=session.mapped_intent,
        )
        if type(receipt) is not GroupLegOrderReceipt:
            raise GroupProofSourceError("GROUP_RECEIPT_READ_UNAVAILABLE")
        return receipt

    def _existing_risk_terminal_receipt(
        self,
        target: GroupProofTarget,
        permit: GroupLegPermitReference,
        *,
        positive: bool,
    ) -> DispatchExposureResolutionReceipt | DispatchTerminalResolutionReceipt | None:
        bundle = self._runtime_bundle
        if bundle is None:
            raise GroupProofSourceError("GROUP_RUNTIME_BUNDLE_UNBOUND")
        bundle._assert_reservations_only_policy()
        try:
            if positive:
                receipt = bundle.risk_admission.dispatch_exposure_resolution_receipt(
                    permit.permit_id
                )
                if type(receipt) is not DispatchExposureResolutionReceipt:
                    raise TypeError
                if receipt.status is DispatchExposureResolutionReceiptStatus.UNRESOLVED_ACTIVE:
                    return None
                if (
                    receipt.status
                    is not DispatchExposureResolutionReceiptStatus.TERMINAL_EXPOSURE_RESOLVED
                ):
                    raise TypeError
                return receipt
            receipt = bundle.risk_admission.dispatch_terminal_resolution_receipt(
                permit.permit_id
            )
            if type(receipt) is not DispatchTerminalResolutionReceipt:
                raise TypeError
            if receipt.status is DispatchTerminalResolutionReceiptStatus.UNRESOLVED_ACTIVE:
                return None
            if (
                receipt.status
                is not DispatchTerminalResolutionReceiptStatus.NO_FILL_TERMINAL_RESOLVED
            ):
                raise TypeError
            return receipt
        except GroupProofSourceError:
            raise
        except Exception:
            raise GroupProofSourceError("GROUP_RISK_RESOLUTION_UNAVAILABLE") from None

    @staticmethod
    def _validate_recovery_attempt(
        *,
        target: GroupProofTarget,
        started: GroupProofJournalEvent,
        binding: GroupPermitBinding,
        intent: OrderIntent,
        permit: GroupLegPermitReference,
        principal_ref: str,
        identity: WireIdentity,
        wire_scope: WireScope,
    ) -> GroupLegAuthorization:
        payload = started.payload
        try:
            persisted_binding = GroupPermitBinding.from_payload(payload.get("binding"))
            persisted_permit = GroupLegPermitReference.from_payload(payload.get("permit"))
            authorization = GroupLegAuthorization.from_payload(
                payload.get("authorization")
            )
        except Exception:
            raise GroupProofSourceError("GROUP_ATTEMPT_AUTHORITY_INVALID") from None
        if (
            started.event_type != "ATTEMPT_STARTED"
            or started.account_scope_key != target.account_scope.key
            or started.intent_id != intent.intent_id
            or started.group_id != permit.group_id
            or started.leg_id != permit.leg_id
            or payload.get("binding") != binding.to_payload()
            or persisted_binding != binding
            or persisted_permit != permit
            or payload.get("intent_id") != intent.intent_id
            or payload.get("intent_fingerprint") != intent.fingerprint
            or payload.get("principal_ref") != principal_ref
            or payload.get("identity") != _identity_payload(identity)
            or payload.get("wire_scope") != _wire_payload(wire_scope)
            or payload.get("account_scope_key") != target.account_scope.key
            or payload.get("execution_scope_key") != target.execution_scope.key
            or payload.get("risk_fence")
            != {
                "fence_sha256": started.writer_fence_sha256,
                "fencing_token": started.writer_fencing_token,
                "owner_id": started.writer_owner_id,
                "scope_key": target.account_scope.key,
            }
            or payload.get("authorization_fingerprint") != authorization.fingerprint
            or authorization.authorization_attempt_id == ""
            or authorization.principal_ref != principal_ref
            or authorization.portfolio_ref != permit.portfolio_ref
            or authorization.strategy_id != permit.strategy_id
            or authorization.account_ref != permit.account_ref
            or authorization.group_id != permit.group_id
            or authorization.leg_id != permit.leg_id
            or authorization.group_fingerprint != permit.group_fingerprint
            or authorization.binding_fingerprint != binding.fingerprint
            or authorization.deadline_ns != permit.deadline_ns
            or authorization.risk_scope_fingerprint != permit.risk_scope_fingerprint
        ):
            raise GroupProofSourceError("GROUP_ATTEMPT_AUTHORITY_MISMATCH")
        authorization.validate_binding(binding)
        return authorization

    @staticmethod
    def _validate_recovery_hold(
        *,
        hold: GroupDispatchHold,
        authorization: GroupLegAuthorization,
        started: GroupProofJournalEvent,
        target: GroupProofTarget,
        binding: GroupPermitBinding,
        intent: OrderIntent,
        permit: GroupLegPermitReference,
        principal_ref: str,
    ) -> None:
        writer_lease = started.payload.get("writer_lease")
        if type(writer_lease) is not dict:
            raise GroupProofSourceError("GROUP_ATTEMPT_LEASE_INVALID")
        try:
            persisted_lease = WriterLease(
                scope_key=writer_lease["scope_key"],
                owner_id=writer_lease["owner_id"],
                fencing_token=writer_lease["fencing_token"],
                expires_at_ns=writer_lease["expires_at_ns"],
            )
        except Exception:
            raise GroupProofSourceError("GROUP_ATTEMPT_LEASE_INVALID") from None
        try:
            authorization.validate_writer_lease(persisted_lease)
        except Exception:
            raise GroupProofSourceError("GROUP_ATTEMPT_LEASE_INVALID") from None
        if (
            type(hold) is not GroupDispatchHold
            or hold.hold_id != authorization.authorization_attempt_id
            or hold.principal_ref != principal_ref
            or hold.portfolio_ref != permit.portfolio_ref
            or hold.account_ref != permit.account_ref
            or hold.strategy_id != permit.strategy_id
            or hold.execution_scope_key != target.execution_scope.key
            or hold.intent_id != intent.intent_id
            or hold.intent_fingerprint != intent.fingerprint
            or hold.binding_fingerprint != binding.fingerprint
            or hold.risk_scope_fingerprint != permit.risk_scope_fingerprint
            or hold.execution_writer_lease_fingerprint
            != started.payload.get("writer_lease_sha256")
            or writer_lease_fingerprint(persisted_lease)
            != started.payload.get("writer_lease_sha256")
            # ExecutionStore's writer fence is account-wide; the hold above
            # independently binds the strategy-specific execution scope.
            or persisted_lease.scope_key != target.execution_scope.account_key
            or persisted_lease.fencing_token != authorization.account_writer_epoch
            or hold.account_writer_epoch != writer_lease.get("fencing_token")
            or authorization.account_writer_epoch != hold.account_writer_epoch
            or authorization.execution_writer_lease_fingerprint
            != hold.execution_writer_lease_fingerprint
            or authorization.coordinator_epoch != hold.coordinator_generation
        ):
            raise GroupProofSourceError("GROUP_DISPATCH_HOLD_BINDING_MISMATCH")

    @contextmanager
    def dispatch_exposure_resolution_guard(
        self,
        proof: DispatchExposureResolutionProof,
        *,
        claim: DispatchClaimBinding,
    ) -> Iterator[VerifiedDispatchExposureResolution | None]:
        """Verify a same-call positive-fill terminal proof under both owner fences."""

        session = _ACTIVE_SESSION.get()
        if (
            type(proof) is not DispatchExposureResolutionProof
            or type(claim) is not DispatchClaimBinding
            or type(session) is not GroupProofDispatchSession
            or session.source is not self
            or session._risk_fence is None
        ):
            raise GroupProofSourceError("GROUP_PROOF_GUARD_UNAVAILABLE")
        session._assert_current()
        session._assert_claim(claim)
        events = self._events(session.target, session.intent.intent_id)
        source_event = _terminal_source_event(events)
        if source_event is None:
            raise GroupProofSourceError("GROUP_SOURCE_EVENT_CHAIN_INVALID")
        proof_event = _one_event(events, "PROOF_READY")
        proof_fence = _proof_fence_from_event(
            proof_event, source_event, session.target.account_scope
        )
        expected_kind = _source_proof_kind(source_event.payload)
        if (
            proof_event.revision <= source_event.revision
            or proof_event.payload.get("source_revision") != source_event.revision
            or proof_event.payload.get("source_record_sha256") != source_event.record_sha256
            or proof_event.payload.get("proof_sha256") != proof.fingerprint
            or expected_kind not in {"FILLED", "CANCELLED_PARTIAL"}
            or proof_event.payload.get("proof_kind") != expected_kind
            or proof_event.payload.get("claim_digest") != claim.claim_digest
            or proof.claim != claim
        ):
            raise GroupProofSourceError("GROUP_PROOF_GUARD_MISMATCH")
        expected = session._build_exposure_proof(
            source_event,
            observed_at_ns=proof.observation.observed_at_ns,
            valid_until_ns=proof.observation.valid_until_ns,
            proof_fence=proof_fence,
        )
        if expected != proof:
            raise GroupProofSourceError("GROUP_PROOF_SOURCE_MISMATCH")
        observation = proof.observation
        try:
            verified = VerifiedDispatchExposureResolution(
                scope=claim.scope,
                permit_id=claim.permit_id,
                intent_id=claim.intent_id,
                intent_hash=claim.intent_hash,
                cause_id=claim.cause_id,
                claim_digest=claim.claim_digest,
                proof_sha256=proof.fingerprint,
                exposure_proof_sha256=observation.fingerprint,
                provider_order_id=observation.provider_order_id,
                accepted_request_sha256=observation.accepted_request_sha256,
                exposure_reservation_id=observation.exposure_reservation_id,
                exposure_reservation_sha256=observation.exposure_reservation_sha256,
                final_state=observation.state,
                requested_quantity=observation.requested_quantity,
                filled_quantity=observation.cumulative_filled_quantity,
                trade_count=observation.fill_trade_count,
                order_revision=observation.order_revision,
                journal_revision=observation.journal_revision,
                journal_record_sha256=observation.journal_record_sha256,
                writer_owner_id=observation.writer_owner_id,
                writer_fencing_token=observation.writer_fencing_token,
                writer_fence_sha256=observation.writer_fence_sha256,
            )
        except Exception:
            raise GroupProofSourceError("GROUP_PROOF_GUARD_MISMATCH") from None
        yield verified
        session._assert_current()

    @contextmanager
    def dispatch_resolution_guard(
        self,
        proof: DispatchTerminalProof,
        *,
        claim: DispatchClaimBinding,
    ) -> Iterator[VerifiedDispatchResolution | None]:
        """Verify a source-backed zero-fill REJECTED proof under both fences."""

        session = _ACTIVE_SESSION.get()
        if (
            type(proof) is not DispatchTerminalProof
            or type(claim) is not DispatchClaimBinding
            or type(session) is not GroupProofDispatchSession
            or session.source is not self
            or session._risk_fence is None
        ):
            raise GroupProofSourceError("GROUP_PROOF_GUARD_UNAVAILABLE")
        session._assert_current()
        session._assert_claim(claim)
        events = self._events(session.target, session.intent.intent_id)
        source_event = _terminal_source_event(events)
        if source_event is None:
            raise GroupProofSourceError("GROUP_SOURCE_EVENT_CHAIN_INVALID")
        proof_event = _one_event(events, "PROOF_READY")
        proof_fence = _proof_fence_from_event(
            proof_event, source_event, session.target.account_scope
        )
        if (
            proof_event.revision <= source_event.revision
            or proof_event.payload.get("source_revision") != source_event.revision
            or proof_event.payload.get("source_record_sha256") != source_event.record_sha256
            or proof_event.payload.get("proof_sha256") != proof.fingerprint
            or proof_event.payload.get("proof_kind")
            != _source_proof_kind(source_event.payload)
            or proof_event.payload.get("proof_kind")
            not in {"REJECTED_NO_FILL", "CANCELED_NO_FILL"}
            or proof.terminal_state
            is not (
                DispatchTerminalState.CANCELED_NO_FILL
                if source_event.payload.get("state") == ExecutionState.CANCELLED.value
                else DispatchTerminalState.REJECTED_NO_FILL
            )
            or proof_event.payload.get("claim_digest") != claim.claim_digest
            or proof.intent_id != claim.intent_id
            or proof.permit_id != claim.permit_id
            or proof.intent_hash != claim.intent_hash
            or proof.claim_digest != claim.claim_digest
            or proof.journal_revision != source_event.revision
            or proof.journal_record_sha256 != source_event.record_sha256
            or proof.writer_fence_sha256 != proof_fence.fence_sha256
        ):
            raise GroupProofSourceError("GROUP_PROOF_GUARD_MISMATCH")
        session._validate_source_event(source_event)
        if session._build_terminal_proof(source_event, proof_fence=proof_fence) != proof:
            raise GroupProofSourceError("GROUP_PROOF_SOURCE_MISMATCH")
        try:
            verified = VerifiedDispatchResolution(
                scope=claim.scope,
                permit_id=claim.permit_id,
                intent_id=claim.intent_id,
                intent_hash=claim.intent_hash,
                claim_digest=claim.claim_digest,
                proof_sha256=proof.fingerprint,
                journal_revision=proof.journal_revision,
                journal_record_sha256=proof.journal_record_sha256,
                writer_fence_sha256=proof.writer_fence_sha256,
            )
        except Exception:
            raise GroupProofSourceError("GROUP_PROOF_GUARD_MISMATCH") from None
        yield verified
        session._assert_current()


def _one_event(
    events: tuple[GroupProofJournalEvent, ...], event_type: str
) -> GroupProofJournalEvent:
    matches = [item for item in events if item.event_type == event_type]
    if len(matches) != 1:
        raise GroupProofSourceError("GROUP_SOURCE_EVENT_CHAIN_INVALID")
    return matches[0]


def _validate_recovery_event_order(
    events: tuple[GroupProofJournalEvent, ...],
) -> None:
    """Accept only normal or monotonic owner-recovery event orderings."""

    if not events or events[0].event_type != "ATTEMPT_STARTED":
        raise GroupProofSourceError("GROUP_SOURCE_EVENT_CHAIN_INVALID")
    by_type: dict[str, GroupProofJournalEvent] = {}
    positions: dict[str, int] = {}
    progress: list[tuple[int, GroupProofJournalEvent]] = []
    for index, event in enumerate(events):
        if event.event_type not in GroupProofJournal.EVENT_TYPES:
            raise GroupProofSourceError("GROUP_SOURCE_EVENT_CHAIN_INVALID")
        if event.event_type == "SOURCE_PROGRESS":
            progress.append((index, event))
            continue
        if event.event_type in by_type:
            raise GroupProofSourceError("GROUP_SOURCE_EVENT_CHAIN_INVALID")
        by_type[event.event_type] = event
        positions[event.event_type] = index

    observed = by_type.get("SOURCE_OBSERVED")
    reconciled = by_type.get("SOURCE_RECONCILED")
    unknown = by_type.get("ATTEMPT_UNKNOWN")
    proof = by_type.get("PROOF_READY")
    resolved = by_type.get("RISK_RESOLVED")

    if observed is not None and positions["SOURCE_OBSERVED"] <= 0:
        raise GroupProofSourceError("GROUP_SOURCE_EVENT_CHAIN_INVALID")
    if unknown is not None and positions["ATTEMPT_UNKNOWN"] <= 0:
        raise GroupProofSourceError("GROUP_SOURCE_EVENT_CHAIN_INVALID")
    if observed is not None and unknown is not None:
        if positions["SOURCE_OBSERVED"] >= positions["ATTEMPT_UNKNOWN"]:
            raise GroupProofSourceError("GROUP_SOURCE_EVENT_CHAIN_INVALID")
    if reconciled is not None:
        if (
            unknown is None
            or positions["ATTEMPT_UNKNOWN"] >= positions["SOURCE_RECONCILED"]
            or observed is not None
            and positions["SOURCE_OBSERVED"] >= positions["ATTEMPT_UNKNOWN"]
        ):
            raise GroupProofSourceError("GROUP_SOURCE_EVENT_CHAIN_INVALID")
    if progress:
        if unknown is None or any(index <= positions["ATTEMPT_UNKNOWN"] for index, _ in progress):
            raise GroupProofSourceError("GROUP_SOURCE_EVENT_CHAIN_INVALID")
        if reconciled is not None and any(
            index >= positions["SOURCE_RECONCILED"] for index, _ in progress
        ):
            raise GroupProofSourceError("GROUP_SOURCE_EVENT_CHAIN_INVALID")
        if any(
            event.event_type == "PROOF_READY"
            and index < positions["ATTEMPT_UNKNOWN"]
            for index, event in enumerate(events)
        ):
            raise GroupProofSourceError("GROUP_SOURCE_EVENT_CHAIN_INVALID")

    source_events = [
        event
        for event in events
        if event.event_type in {"SOURCE_OBSERVED", "SOURCE_PROGRESS", "SOURCE_RECONCILED"}
    ]
    prior_source_payload: Mapping[str, Any] | None = None
    for source_event in source_events:
        payload = source_event.payload
        if prior_source_payload is not None:
            try:
                prior_order_revision = prior_source_payload.get("order_revision")
                prior_fill_revision = prior_source_payload.get("fill_watermark_revision")
                order_revision = payload.get("order_revision")
                fill_revision = payload.get("fill_watermark_revision")
                prior_filled = Decimal(str(prior_source_payload.get("filled_quantity")))
                filled = Decimal(str(payload.get("filled_quantity")))
                if (
                    type(prior_order_revision) is not int
                    or type(prior_fill_revision) is not int
                    or type(order_revision) is not int
                    or type(fill_revision) is not int
                    or order_revision < prior_order_revision
                    or fill_revision < prior_fill_revision
                    or filled < prior_filled
                    or (
                        order_revision == prior_order_revision
                        and fill_revision == prior_fill_revision
                        and payload.get("observation_sha256")
                        != prior_source_payload.get("observation_sha256")
                    )
                ):
                    raise GroupProofSourceError("GROUP_SOURCE_EVENT_CHAIN_INVALID")
            except (TypeError, ValueError, ArithmeticError):
                raise GroupProofSourceError("GROUP_SOURCE_EVENT_CHAIN_INVALID") from None
        prior_source_payload = payload

    terminal_source = _terminal_source_event(events)
    if proof is not None and (
        terminal_source is None
        or positions["PROOF_READY"]
        <= positions[terminal_source.event_type]
    ):
        raise GroupProofSourceError("GROUP_SOURCE_EVENT_CHAIN_INVALID")
    if resolved is not None and (
        proof is None or positions["RISK_RESOLVED"] <= positions["PROOF_READY"]
    ):
        raise GroupProofSourceError("GROUP_SOURCE_EVENT_CHAIN_INVALID")
    if unknown is not None:
        previous_source = next(
            (
                event
                for event in reversed(events[: positions["ATTEMPT_UNKNOWN"]])
                if event.event_type
                in {"SOURCE_OBSERVED", "SOURCE_PROGRESS", "SOURCE_RECONCILED"}
            ),
            None,
        )
        expected_revision = None if previous_source is None else previous_source.revision
        expected_sha256 = (
            None if previous_source is None else previous_source.record_sha256
        )
        if (
            unknown.payload.get("source_revision") != expected_revision
            or unknown.payload.get("source_record_sha256") != expected_sha256
            or type(unknown.payload.get("reason_code")) is not str
            or _REFERENCE.fullmatch(unknown.payload["reason_code"]) is None
        ):
            raise GroupProofSourceError("GROUP_SOURCE_EVENT_CHAIN_INVALID")


def _unknown_group_receipt(
    intent: OrderIntent,
    record: ExecutionRecord | None,
    claim: GroupLegClaimReceipt | None,
) -> GroupLegOrderReceipt:
    return GroupLegOrderReceipt(
        status=GroupChildOrderStatus(
            intent_id=intent.intent_id,
            state=ExecutionState.UNKNOWN,
            filled_quantity=Decimal("0") if record is None else record.filled_quantity,
            provider_order_id=None if record is None else record.provider_order_id,
        ),
        claim=claim,
        resolution=None,
    )


def _terminal_source_event(
    events: tuple[GroupProofJournalEvent, ...],
) -> GroupProofJournalEvent | None:
    event = _latest_source_event(events)
    if event is None or event.payload.get("state") not in {
        ExecutionState.FILLED.value,
        ExecutionState.CANCELLED.value,
        ExecutionState.REJECTED.value,
    }:
        return None
    return event


def _latest_source_event(
    events: tuple[GroupProofJournalEvent, ...],
) -> GroupProofJournalEvent | None:
    return next(
        (
            event
            for event in reversed(events)
            if event.event_type
            in {"SOURCE_OBSERVED", "SOURCE_PROGRESS", "SOURCE_RECONCILED"}
        ),
        None,
    )


def _source_progress_is_new(
    previous: GroupProofJournalEvent | None,
    payload: Mapping[str, Any],
) -> bool:
    """Validate a source snapshot's monotonicity and report whether it changed."""

    if previous is None:
        return True
    old = previous.payload
    try:
        old_order = old.get("order_revision")
        old_fill = old.get("fill_watermark_revision")
        new_order = payload.get("order_revision")
        new_fill = payload.get("fill_watermark_revision")
        old_filled = Decimal(str(old.get("filled_quantity")))
        new_filled = Decimal(str(payload.get("filled_quantity")))
    except (TypeError, ValueError, ArithmeticError):
        raise GroupProofSourceError("GROUP_SOURCE_PROGRESS_INVALID") from None
    if (
        type(old_order) is not int
        or type(old_fill) is not int
        or type(new_order) is not int
        or type(new_fill) is not int
        or new_order < old_order
        or new_fill < old_fill
        or new_filled < old_filled
    ):
        raise GroupProofSourceError("GROUP_SOURCE_PROGRESS_REGRESSION")
    same_revision = new_order == old_order and new_fill == old_fill
    # SQLite JSON readback represents Decimal quantities as their canonical
    # strings, while a fresh provider projection still carries Decimal.
    # Compare the parsed values above so an identical source snapshot remains
    # idempotent across that persistence boundary.
    same_facts = (
        payload.get("observation_sha256") == old.get("observation_sha256")
        and payload.get("state") == old.get("state")
        and new_filled == old_filled
        and payload.get("trade_count") == old.get("trade_count")
        and payload.get("fill_history_sha256") == old.get("fill_history_sha256")
    )
    if same_revision and not same_facts:
        raise GroupProofSourceError("GROUP_SOURCE_REVISION_CONFLICT")
    return not (same_revision and same_facts)


def _execution_record_matches_group_status(
    record: ExecutionRecord, status: GroupChildOrderStatus
) -> bool:
    return (
        type(record) is ExecutionRecord
        and type(status) is GroupChildOrderStatus
        and record.state is status.state
        and record.provider_order_id == status.provider_order_id
        and record.filled_quantity == status.filled_quantity
        and record.average_price == status.average_price
        and record.cumulative_commission == status.cumulative_commission
    )


def _proof_fence_payload(fence: RiskOrderWriterFence) -> dict[str, Any]:
    if type(fence) is not RiskOrderWriterFence:
        raise GroupProofSourceError("GROUP_RISK_FENCE_UNAVAILABLE")
    return {
        "fence_sha256": fence.fence_sha256,
        "fencing_token": fence.fencing_token,
        "owner_id": fence.owner_id,
        "scope_key": fence.scope.key,
    }


def _proof_fence_from_event(
    proof_event: GroupProofJournalEvent,
    source_event: GroupProofJournalEvent,
    scope: AccountScope,
) -> RiskOrderWriterFence:
    payload = proof_event.payload.get("proof_writer_fence")
    if payload is None:
        # Backward compatibility for an already-written normal proof: its
        # PROOF_READY event used the same fence as SOURCE_OBSERVED.
        payload = source_event.payload.get("writer_fence")
    if type(payload) is not dict or set(payload) != {
        "fence_sha256",
        "fencing_token",
        "owner_id",
        "scope_key",
    }:
        raise GroupProofSourceError("GROUP_PROOF_FENCE_INVALID")
    try:
        fence = RiskOrderWriterFence(
            scope=scope,
            owner_id=payload["owner_id"],
            fencing_token=payload["fencing_token"],
            fence_sha256=payload["fence_sha256"],
        )
    except Exception:
        raise GroupProofSourceError("GROUP_PROOF_FENCE_INVALID") from None
    event_fence = {
        "fence_sha256": proof_event.writer_fence_sha256,
        "fencing_token": proof_event.writer_fencing_token,
        "owner_id": proof_event.writer_owner_id,
        "scope_key": scope.key,
    }
    if _proof_fence_payload(fence) != event_fence:
        raise GroupProofSourceError("GROUP_PROOF_FENCE_INVALID")
    return fence


def _source_proof_kind(source_payload: object) -> str | None:
    """Map exact persisted terminal facts to the matching Risk proof kind."""

    if not isinstance(source_payload, Mapping):
        return None
    state = source_payload.get("state")
    filled_raw = source_payload.get("filled_quantity")
    quantity_raw = source_payload.get("quantity")
    trade_count = source_payload.get("trade_count")
    rows = source_payload.get("fill_records")
    if (
        type(filled_raw) is not str
        or type(quantity_raw) is not str
        or type(trade_count) is not int
        or type(rows) is not list
    ):
        return None
    try:
        filled = Decimal(filled_raw)
        quantity = Decimal(quantity_raw)
    except Exception:
        return None
    if not filled.is_finite() or not quantity.is_finite() or quantity <= 0:
        return None
    if state == ExecutionState.FILLED.value and filled == quantity and rows:
        return "FILLED"
    if state == ExecutionState.CANCELLED.value:
        if filled == 0 and trade_count == 0 and not rows:
            return "CANCELED_NO_FILL"
        if 0 < filled < quantity and trade_count == len(rows) and rows:
            return "CANCELLED_PARTIAL"
        return None
    if (
        state == ExecutionState.REJECTED.value
        and filled == 0
        and trade_count == 0
        and not rows
    ):
        return "REJECTED_NO_FILL"
    return None


class GroupProofDispatchSession:
    """One call-local provider attempt and its source-backed terminal proof.

    Construction is intentionally lock-free. The Risk account fence is only
    acquired in ``after_group_claim`` after Execution has acquired/asserted its
    writer lease and obtained its exact one-shot group claim.
    """

    def __init__(
        self,
        *,
        source: GroupProofSource,
        target: GroupProofTarget,
        binding: GroupPermitBinding,
        intent: OrderIntent,
        permit: GroupLegPermitReference,
        mapped_intent: RiskIntent,
        principal_ref: str,
        identity: WireIdentity,
        wire_scope: WireScope,
        service_context: ServiceCallContext,
        active_authority: ExecutionGroupAuthorityGuard,
    ) -> None:
        if (
            type(source) is not GroupProofSource
            or type(target) is not GroupProofTarget
            or type(binding) is not GroupPermitBinding
            or type(intent) is not OrderIntent
            or type(permit) is not GroupLegPermitReference
            or type(mapped_intent) is not RiskIntent
            or type(identity) is not WireIdentity
            or type(wire_scope) is not WireScope
            or type(service_context) is not ServiceCallContext
            or type(active_authority) is not ExecutionGroupAuthorityGuard
        ):
            raise GroupProofSourceError("GROUP_SESSION_INPUT_INVALID")
        self.source = source
        self.target = target
        self.binding = binding
        self.intent = intent
        self.permit = permit
        self.mapped_intent = mapped_intent
        self.principal_ref = principal_ref
        self.identity = identity
        self.wire_scope = wire_scope
        self.service_context = service_context
        self.active_authority = active_authority
        self._risk_fence_context = None
        self._risk_fence: RiskOrderWriterFence | None = None
        self._context_token = None
        self._authorization: GroupLegAuthorization | None = None
        self._writer_lease: WriterLease | None = None
        self._claim_receipt: RiskGroupLegClaimReceipt | None = None
        self._claim_projection: GroupLegClaimReceipt | None = None
        self._attempt_started = False
        self._attempt_unknown = False
        self._finished = False
        self._aborted = False
        self._dispatch_started = False
        self._source_event: GroupProofJournalEvent | None = None
        self._status: GroupChildOrderStatus | None = None
        self._acknowledgement: dict[str, Any] | None = None
        self._facts: dict[str, Any] | None = None
        # The Execution package releases its durable group-authority hold on
        # successful return from submit_group_leg.  Capture the exact receipt
        # and durable record while after_group_result still runs under that
        # hold; finish() may then project only this already-verified result.
        self._terminal_receipt: GroupLegOrderReceipt | None = None
        self._terminal_record: ExecutionRecord | None = None
        self._terminal_binding: GroupPermitBinding | None = None
        self._terminal_authorization: GroupLegAuthorization | None = None
        self._terminal_writer_lease: WriterLease | None = None
        self._terminal_claim_receipt: object | None = None
        self._recovery_hold: GroupDispatchHold | None = None
        self._request, self._accepted_request_sha256 = self._build_request()
        self.provider_dispatcher = self

    def _build_request(self) -> tuple[OrderRequest, str]:
        metadata = self.target.risk_target.registry.get(self.intent.instrument)
        now = self.source._clock_ns()
        if (
            type(metadata) is not InstrumentRiskMetadata
            or type(now) is not int
            or metadata.metadata_version != self.intent.metadata_version
            or self.intent.tags.get(INSTRUMENT_METADATA_DIGEST_TAG) != metadata.digest
            or metadata.quantity_unit not in {"base", "contracts", "lots", "native"}
            or metadata.valuation_unit is None
            or self.target.provider_contract.quote_currency(self.intent.instrument)
            != metadata.valuation_unit
            or metadata.as_of_ns > now
            or metadata.expires_at_ns <= now
            or now - metadata.as_of_ns > self.target.max_observation_age_ns
        ):
            raise GroupProofSourceError("GROUP_TRUSTED_INSTRUMENT_FACTS_UNAVAILABLE")
        business_scope = _wire_payload(replace(self.wire_scope, group_id=None, leg_id=None))
        business_scope.pop("run_id", None)
        client_material = {
            "account_scope": self.target.account_scope.key,
            "business_scope": business_scope,
            "group_id": self.permit.group_id,
            "intent_fingerprint": self.intent.fingerprint,
            "intent_id": self.intent.intent_id,
            "leg_id": self.permit.leg_id,
            "permit_id": self.permit.permit_id,
            "principal_ref": self.principal_ref,
            "provider": self.target.provider_contract.exchange_name,
        }
        client_order_id = "grp-" + sha256_text(canonical_json(client_material))[:40]
        try:
            request = OrderRequest(
                symbol=self.intent.instrument,
                side=BtSide(self.intent.side.value.lower()),
                order_type=BtOrderType.LIMIT,
                quantity=self.intent.quantity,
                account_id=self.target.risk_target.provider_account_id,
                client_order_id=client_order_id,
                price=self.intent.price,
                time_in_force="IOC",
                reduce_only=False,
                idempotency_key=client_order_id,
                quantity_unit=metadata.quantity_unit,
                offset="open",
            )
        except Exception:
            raise GroupProofSourceError("GROUP_PROVIDER_REQUEST_INVALID") from None
        if not _same_order_request(self.intent, request):
            raise GroupProofSourceError("GROUP_PROVIDER_REQUEST_MISMATCH")
        digest = sha256_text(
            canonical_json(
                {
                    "exchange_name": self.target.provider_contract.exchange_name,
                    "operation": "BtApi.make_order(normalized=True)",
                    "request": request.to_dict(),
                }
            )
        )
        return request, digest

    def _assert_current(self) -> None:
        if self._risk_fence is None:
            raise GroupProofSourceError("GROUP_SESSION_NOT_CLAIMED")
        bundle = self.source.runtime_bundle
        if bundle is None:
            raise GroupProofSourceError("GROUP_RUNTIME_BUNDLE_UNBOUND")
        bundle._assert_reservations_only_policy()
        try:
            if self._recovery_hold is not None:
                current_hold = self.active_authority.read_reconciliation_hold(
                    self._recovery_hold.hold_id,
                    context=self.service_context,
                    execution_scope=self.target.execution_scope,
                )
                if current_hold != self._recovery_hold:
                    raise GroupProofSourceError("GROUP_DISPATCH_HOLD_CHANGED")
                if self._writer_lease is not None:
                    self.target.execution_store.assert_writer_lease(
                        self.target.execution_scope, self._writer_lease
                    )
            else:
                if self._writer_lease is None or self._authorization is None:
                    raise GroupProofSourceError("GROUP_SESSION_NOT_CLAIMED")
                self.target.execution_store.assert_writer_lease(
                    self.target.execution_scope, self._writer_lease
                )
                self.active_authority.assert_current_group_leg_authorization(
                    principal_ref=self.principal_ref,
                    identity=self.identity,
                    wire_scope=self.wire_scope,
                    execution_scope=self.target.execution_scope,
                    intent=self.intent,
                    binding=self.binding,
                    authorization=self._authorization,
                    writer_lease=self._writer_lease,
                )
            with self.target.risk_order_journal.writer_fence(
                self.target.account_scope
            ) as current_fence:
                if current_fence != self._risk_fence:
                    raise GroupProofSourceError("GROUP_RISK_FENCE_CHANGED")
        except GroupProofSourceError:
            raise
        except Exception:
            raise GroupProofSourceError("GROUP_OWNER_FENCE_UNAVAILABLE") from None

    def _assert_claim(self, claim: DispatchClaimBinding) -> None:
        if type(claim) is not DispatchClaimBinding or self._claim_receipt is None:
            raise GroupProofSourceError("GROUP_RISK_CLAIM_INVALID")
        actual = self._claim_receipt.claim
        if claim != actual or claim.claim_digest != actual.claim_digest:
            raise GroupProofSourceError("GROUP_RISK_CLAIM_MISMATCH")

    def after_group_claim(
        self,
        intent: OrderIntent,
        binding: GroupPermitBinding,
        authorization: GroupLegAuthorization,
        writer_lease: WriterLease,
        validated_claim: object,
    ) -> None:
        if (
            self._attempt_started
            or self._aborted
            or intent is not self.intent
            or binding != self.binding
            or type(authorization) is not GroupLegAuthorization
            or type(writer_lease) is not WriterLease
            or type(validated_claim) is not RiskGroupLegClaimReceipt
            or validated_claim.claim.permit_id != self.permit.permit_id
        ):
            raise GroupProofSourceError("GROUP_ATTEMPT_ALREADY_STARTED")
        self._authorization = authorization
        self._writer_lease = writer_lease
        self._claim_receipt = validated_claim
        self._claim_projection = self.source._claim_projection(
            self.target,
            self.intent,
            self.permit,
            self.mapped_intent,
            validated_claim,
            require_unresolved=True,
        )
        try:
            self._risk_fence_context = self.target.risk_order_journal.writer_fence(
                self.target.account_scope
            )
            self._risk_fence = self._risk_fence_context.__enter__()
            if type(self._risk_fence) is not RiskOrderWriterFence:
                raise GroupProofSourceError("GROUP_RISK_FENCE_UNAVAILABLE")
            if _ACTIVE_SESSION.get() is not None:
                raise GroupProofSourceError("GROUP_SESSION_NESTED")
            self._context_token = _ACTIVE_SESSION.set(self)
            self._assert_current()
            queried_claim = self.source._query_claim(self.target, self.permit)
            if queried_claim != validated_claim:
                raise GroupProofSourceError("GROUP_RISK_CLAIM_CHANGED")
            auth_payload = authorization.to_payload()
            lease_payload = {
                "expires_at_ns": writer_lease.expires_at_ns,
                "fencing_token": writer_lease.fencing_token,
                "owner_id": writer_lease.owner_id,
                "scope_key": writer_lease.scope_key,
            }
            payload = {
                "accepted_request_sha256": self._accepted_request_sha256,
                "account_scope_key": self.target.account_scope.key,
                "acknowledgement": None,
                "authorization": auth_payload,
                "authorization_fingerprint": authorization.fingerprint,
                "binding": self.binding.to_payload(),
                "client_order_id": self._request.client_order_id,
                "contract_sha256": self.target.provider_contract.fingerprint,
                "execution_scope_key": self.target.execution_scope.key,
                "group_id": self.permit.group_id,
                "identity": _identity_payload(self.identity),
                "intent_fingerprint": self.intent.fingerprint,
                "intent_id": self.intent.intent_id,
                "leg_id": self.permit.leg_id,
                "mapped_intent_fingerprint": self.mapped_intent.fingerprint,
                "permit": self.permit.to_payload(),
                "principal_ref": self.principal_ref,
                "provider_request": self._request.to_dict(),
                "risk_claim": self._claim_projection.to_payload(),
                "risk_fence": {
                    "fence_sha256": self._risk_fence.fence_sha256,
                    "fencing_token": self._risk_fence.fencing_token,
                    "owner_id": self._risk_fence.owner_id,
                    "scope_key": self._risk_fence.scope.key,
                },
                "wire_scope": _wire_payload(self.wire_scope),
                "writer_lease": lease_payload,
                "writer_lease_sha256": writer_lease_fingerprint(writer_lease),
            }
            self.source._append_event(
                target=self.target,
                fence=self._risk_fence,
                intent=self.intent,
                permit=self.permit,
                event_type="ATTEMPT_STARTED",
                payload=payload,
            )
            self._attempt_started = True
            self._assert_current()
        except Exception as error:
            if self._attempt_started:
                self._mark_unknown("GROUP_ATTEMPT_SETUP_UNCERTAIN")
            if isinstance(error, GroupProofSourceError):
                raise
            raise GroupProofSourceError("GROUP_ATTEMPT_SETUP_UNCERTAIN") from None

    def submit(self, intent: OrderIntent) -> ProviderObservation:
        """Make the sole write, then query the exact order and full fill history."""

        if (
            not self._attempt_started
            or self._dispatch_started
            or self._aborted
            or intent is not self.intent
        ):
            raise GroupProofSourceError("GROUP_PROVIDER_DISPATCH_INVALID")
        self._dispatch_started = True
        self._assert_current()
        ack = None
        try:
            ack = self.target.bt_api.make_order(
                self.target.provider_contract.exchange_name,
                self._request,
                normalized=True,
            )
            self._acknowledgement = _safe_ack(ack)
            self._assert_current()
            query = QueryOrderRequest(
                symbol=self._request.symbol,
                account_id=self._request.account_id,
                client_order_id=self._request.client_order_id,
            )
            order = self.target.bt_api.query_order(
                self.target.provider_contract.exchange_name,
                query,
                normalized=True,
            )
            fills = self.target.bt_api.get_deals(
                self.target.provider_contract.exchange_name,
                self._request.symbol,
                normalized=True,
                client_order_id=self._request.client_order_id,
                account_id=self._request.account_id,
            )
            facts = self._validate_provider_source(
                order,
                fills,
                source_observed_at_ns=self.source._clock_ns(),
            )
            self._facts = facts
            state = facts["state"]
            if state in {ExecutionState.ACKED, ExecutionState.PARTIALLY_FILLED}:
                status = self._status_from_facts(facts, state)
                self._status = status
                self._source_event = self._record_source(
                    facts, state=state, status=status
                )
                return self._provider_observation(status)
            if state is ExecutionState.UNKNOWN:
                status = self._status_from_facts(facts, state)
                self._status = status
                self._source_event = self._record_source(
                    facts, state=state, status=status
                )
                self._mark_unknown("GROUP_PROVIDER_STATE_UNKNOWN")
                return self._provider_observation(status)
            # A provider CANCELLED terminal fact is either a zero-fill cancel
            # or an IOC remainder cancel after a strictly positive partial
            # fill. Full-fill CANCELLED is contradictory and remains UNKNOWN.
            if state not in {
                ExecutionState.FILLED,
                ExecutionState.REJECTED,
                ExecutionState.CANCELLED,
            }:
                self._record_source(facts, state=state)
                self._mark_unknown("GROUP_ORDER_NOT_TERMINAL")
                raise GroupProofSourceError("GROUP_ORDER_OUTCOME_UNKNOWN")
            status = self._status_from_facts(facts, state)
            self._status = status
            if state is ExecutionState.CANCELLED and facts["filled_quantity"] > 0:
                # The execution store does not allow DISPATCHING -> CANCELLED
                # directly, but it does allow DISPATCHING -> PARTIALLY_FILLED
                # -> CANCELLED. Persist the exact evidenced partial fill first,
                # then return the terminal observation for the facade commit.
                partial = self._provider_observation(
                    self._status_from_facts(facts, ExecutionState.PARTIALLY_FILLED)
                )
                try:
                    self.target.execution_store.record_observation(
                        partial,
                        scope=self.target.execution_scope,
                        source="provider",
                        writer_lease=self._writer_lease,
                    )
                except Exception:
                    self._mark_unknown("GROUP_PARTIAL_EVIDENCE_COMMIT_UNCERTAIN")
                    raise GroupProofSourceError(
                        "GROUP_PARTIAL_EVIDENCE_COMMIT_UNCERTAIN"
                    ) from None
                self._assert_current()
            elif state is ExecutionState.CANCELLED:
                # DISPATCHING cannot move directly to CANCELLED. For a
                # source-verified zero-fill cancellation, persist its exact
                # zero-fill ACKED predecessor before the terminal observation.
                acknowledged = self._provider_observation(
                    self._status_from_facts(facts, ExecutionState.ACKED)
                )
                try:
                    self.target.execution_store.record_observation(
                        acknowledged,
                        scope=self.target.execution_scope,
                        source="provider",
                        writer_lease=self._writer_lease,
                    )
                except Exception:
                    self._mark_unknown("GROUP_ACKNOWLEDGEMENT_COMMIT_UNCERTAIN")
                    raise GroupProofSourceError(
                        "GROUP_ACKNOWLEDGEMENT_COMMIT_UNCERTAIN"
                    ) from None
                self._assert_current()
            self._source_event = self._record_source(facts, state=state, status=status)
            return self._provider_observation(status)
        except GroupProofSourceError as error:
            if error.code not in {"GROUP_ORDER_OUTCOME_UNKNOWN"}:
                self._mark_unknown(error.code)
            raise
        except Exception:
            self._mark_unknown("GROUP_PROVIDER_OBSERVATION_UNCERTAIN")
            raise GroupProofSourceError("GROUP_PROVIDER_OBSERVATION_UNCERTAIN") from None

    def _validate_provider_source(
        self,
        order: object,
        fills: object,
        *,
        source_observed_at_ns: int | None = None,
    ) -> dict[str, Any]:
        contract = self.target.provider_contract
        request = self._request
        if (
            type(order) is not NormalizedRecord
            or type(fills) is not NormalizedRecords
            or order.source_alignment_valid is not True
            or type(order.source_row_count) is not int
            or order.source_row_count != 1
            or fills.source_alignment_valid is not True
            or type(fills.row_provenance) is not tuple
            or len(fills.row_provenance) != len(fills)
            or order.get("execution_unknown") is not False
        ):
            raise GroupProofSourceError("GROUP_SOURCE_CARDINALITY_INVALID")
        order_prov = _provenance(order.normalization_provenance, "query_order")
        order_collection = _provenance(order.collection_provenance, "query_order")
        fill_collection = _provenance(fills.normalization_provenance, "get_deals")
        order_watermark, _ = _envelope(
            order_collection,
            operation="query_order",
            schema=contract.order_schema,
            client_order_id=request.client_order_id,
            account_id=request.account_id,
        )
        row_watermark, _ = _envelope(
            order_prov,
            operation="query_order",
            schema=contract.order_schema,
            client_order_id=request.client_order_id,
            account_id=request.account_id,
        )
        fill_watermark, _ = _envelope(
            fill_collection,
            operation="get_deals",
            schema=contract.fill_schema,
            client_order_id=request.client_order_id,
            account_id=request.account_id,
        )
        if order_watermark != row_watermark or type(fill_watermark) is not int:
            raise GroupProofSourceError("GROUP_SOURCE_WATERMARK_MISMATCH")
        order_values = order_prov["source_values"]
        order_fields = order_prov["source_fields"]
        order_id = _field(order_values, order_fields, _ORDER_ALIASES["order_id"])
        client_id = _field(order_values, order_fields, _ORDER_ALIASES["client_order_id"])
        account_id = _field(order_values, order_fields, _ORDER_ALIASES["account_id"])
        instrument = _field(order_values, order_fields, _ORDER_ALIASES["instrument"])
        side = _field(order_values, order_fields, _ORDER_ALIASES["side"])
        raw_status = _field(order_values, order_fields, _ORDER_ALIASES["status"])
        raw_quantity = _field(order_values, order_fields, _ORDER_ALIASES["quantity"])
        raw_filled = _field(order_values, order_fields, _ORDER_ALIASES["filled"])
        raw_price = _field(order_values, order_fields, _ORDER_ALIASES["price"])
        revision = order_values.get(contract.order_revision_field)
        if (
            type(order_id) is not str
            or not order_id
            or type(client_id) is not str
            or client_id != request.client_order_id
            or type(account_id) is not str
            or account_id != request.account_id
            or type(instrument) is not str
            or instrument != request.symbol
            or type(side) is not str
            or side.upper() != request.side.value.upper()
            or type(revision) is not int
            or revision <= 0
            or type(raw_status) is not str
            or type(order.get("order_id")) is not str
            or order.get("order_id") != order_id
            or order.get("client_order_id") != client_id
            or order.get("account_id") != account_id
            or order.get("symbol") != instrument
            or str(order.get("side", "")).upper() != side.upper()
        ):
            raise GroupProofSourceError("GROUP_SOURCE_ORDER_IDENTITY_INVALID")
        raw_status_key = raw_status.lower().replace("-", "_")
        status_map = dict(contract.provider_status_mapping)
        normalized_status = status_map.get(raw_status_key)
        if normalized_status is None or str(order.get("status")) != normalized_status:
            raise GroupProofSourceError("GROUP_SOURCE_STATUS_INVALID")
        quantity = _decimal(raw_quantity, "GROUP_SOURCE_QUANTITY_INVALID", positive=True)
        filled_quantity = _decimal(raw_filled, "GROUP_SOURCE_FILL_INVALID", nonnegative=True)
        source_price = _decimal(raw_price, "GROUP_SOURCE_PRICE_INVALID", positive=True)
        normalized_quantity_matches = _normalized_float_matches_source(
            order.get("size"),
            quantity,
            "GROUP_NORMALIZED_QUANTITY_INVALID",
            positive=True,
        )
        normalized_filled_matches = _normalized_float_matches_source(
            order.get("filled"),
            filled_quantity,
            "GROUP_NORMALIZED_FILL_INVALID",
            nonnegative=True,
        )
        if (
            quantity != request.quantity
            or source_price != request.price
            or not normalized_quantity_matches
            or not normalized_filled_matches
            or revision > fill_watermark
        ):
            raise GroupProofSourceError("GROUP_SOURCE_ORDER_REQUEST_MISMATCH")
        fill_envelopes = [_provenance(item, "get_deals") for item in fills.row_provenance]
        if any(
            item["envelope_values"] != fill_collection["envelope_values"] for item in fill_envelopes
        ):
            raise GroupProofSourceError("GROUP_SOURCE_FILL_ENVELOPE_MISMATCH")
        fill_records: list[dict[str, Any]] = []
        fill_facts: list[OrderFillFact] = []
        fee_currencies: set[str] = set()
        for normalized, raw_prov in zip(fills, fill_envelopes, strict=True):
            values = raw_prov["source_values"]
            fields = raw_prov["source_fields"]
            trade_id = _field(values, fields, _FILL_ALIASES["trade_id"])
            fill_order_id = _field(values, fields, _FILL_ALIASES["order_id"])
            fill_client_id = _field(values, fields, _FILL_ALIASES["client_order_id"])
            fill_account_id = _field(values, fields, _FILL_ALIASES["account_id"])
            fill_instrument = _field(values, fields, _FILL_ALIASES["instrument"])
            fill_side = _field(values, fields, _FILL_ALIASES["side"])
            fill_quantity_raw = _field(values, fields, _FILL_ALIASES["quantity"])
            fill_price_raw = _field(values, fields, _FILL_ALIASES["price"])
            fill_revision = values.get(contract.fill_revision_field)
            fee_raw = values.get(contract.fee_amount_field)
            fee_currency_raw = values.get(contract.fee_currency_field)
            if (
                type(trade_id) is not str
                or not trade_id
                or type(fill_revision) is not int
                or not 0 < fill_revision <= fill_watermark
                or str(fill_order_id) != order_id
                or str(fill_client_id) != request.client_order_id
                or str(fill_account_id) != request.account_id
                or str(fill_instrument) != request.symbol
                or str(fill_side).upper() != request.side.value.upper()
                or type(fee_currency_raw) is not str
                or not fee_currency_raw
            ):
                raise GroupProofSourceError("GROUP_SOURCE_FILL_IDENTITY_INVALID")
            fill_quantity = _decimal(fill_quantity_raw, "GROUP_SOURCE_FILL_INVALID", positive=True)
            fill_price = _decimal(fill_price_raw, "GROUP_SOURCE_FILL_PRICE_INVALID", positive=True)
            fee_amount = _decimal(fee_raw, "GROUP_SOURCE_FEE_INVALID")
            normalized_size_matches = _normalized_float_matches_source(
                normalized.get("size"),
                fill_quantity,
                "GROUP_NORMALIZED_FILL_INVALID",
                positive=True,
            )
            normalized_price_matches = _normalized_float_matches_source(
                normalized.get("price"),
                fill_price,
                "GROUP_NORMALIZED_FILL_PRICE_INVALID",
                positive=True,
            )
            normalized_fee_matches = _normalized_float_matches_source(
                normalized.get("fee"), fee_amount, "GROUP_NORMALIZED_FEE_INVALID"
            )
            fee_currency = fee_currency_raw.upper()
            if (
                not normalized_size_matches
                or not normalized_price_matches
                or not normalized_fee_matches
                or str(normalized.get("trade_id")) != trade_id
                or str(normalized.get("order_id")) != order_id
                or str(normalized.get("client_order_id")) != request.client_order_id
                or str(normalized.get("account_id")) != request.account_id
                or str(normalized.get("symbol")) != request.symbol
                or str(normalized.get("side", "")).upper() != request.side.value.upper()
                or str(normalized.get("fee_currency", "")).upper() != fee_currency
            ):
                raise GroupProofSourceError("GROUP_SOURCE_FILL_NORMALIZATION_MISMATCH")
            row_digest = sha256_text(
                canonical_json(
                    {
                        "schema": "bt-api-provider-source-row-v1",
                        "operation": "get_deals",
                        "source_fields": raw_prov["source_fields"],
                        "source_values": raw_prov["source_values"],
                    }
                )
            )
            fee_currencies.add(fee_currency)
            fill_facts.append(
                OrderFillFact(
                    trade_id=trade_id,
                    quantity=fill_quantity,
                    price=fill_price,
                    side=request.side.value.upper(),
                    position_effect="OPEN",
                    fee_amount=fee_amount,
                    fee_currency=fee_currency,
                    source_record_sha256=row_digest,
                    source_revision=fill_revision,
                )
            )
            fill_records.append(
                {
                    "normalization_provenance": raw_prov,
                    "record": _normalized_fill(normalized),
                    "source_record_sha256": row_digest,
                    "source_revision": fill_revision,
                }
            )
        if (
            sum((item.quantity for item in fill_facts), Decimal(0)) != filled_quantity
            or len({item.trade_id for item in fill_facts}) != len(fill_facts)
            or len(fee_currencies) > 1
        ):
            raise GroupProofSourceError("GROUP_SOURCE_FILL_TOTAL_MISMATCH")
        state_by_source = {
            "accepted": ExecutionState.ACKED,
            "submitted": ExecutionState.ACKED,
            "partial": ExecutionState.PARTIALLY_FILLED,
            "completed": ExecutionState.FILLED,
            "canceled": ExecutionState.CANCELLED,
            "expired": ExecutionState.UNKNOWN,
            "rejected": ExecutionState.REJECTED,
            "unknown": ExecutionState.UNKNOWN,
        }
        state = state_by_source.get(normalized_status)
        if state is None:
            raise GroupProofSourceError("GROUP_SOURCE_STATUS_UNSUPPORTED")
        if state is ExecutionState.FILLED and (
            filled_quantity != quantity or not fill_facts
        ):
            raise GroupProofSourceError("GROUP_SOURCE_FILL_TOTAL_MISMATCH")
        if state is ExecutionState.CANCELLED and not (
            filled_quantity == 0 and not fill_facts
            or Decimal("0") < filled_quantity < quantity and bool(fill_facts)
        ):
            # The provider contract treats CANCELLED as a cancelled remainder;
            # a full fill carrying a cancel status is contradictory and stays
            # UNKNOWN rather than being silently reclassified as FILLED.
            raise GroupProofSourceError("GROUP_SOURCE_CANCELLED_FILL_CONFLICT")
        if state is ExecutionState.REJECTED and (filled_quantity != 0 or fill_facts):
            raise GroupProofSourceError("GROUP_SOURCE_REJECTED_WITH_FILL")
        if state is ExecutionState.ACKED and filled_quantity != 0:
            raise GroupProofSourceError("GROUP_SOURCE_ACK_FILL_MISMATCH")
        now = (
            self.source._clock_ns()
            if source_observed_at_ns is None
            else source_observed_at_ns
        )
        metadata = self.target.risk_target.registry.get(self.intent.instrument)
        quote = contract.quote_currency(self.intent.instrument)
        if (
            type(now) is not int
            or type(metadata) is not InstrumentRiskMetadata
            or metadata.metadata_version != self.intent.metadata_version
            or self.intent.tags.get(INSTRUMENT_METADATA_DIGEST_TAG) != metadata.digest
            or metadata.valuation_unit is None
            or quote != metadata.valuation_unit
            or metadata.as_of_ns > now
            or metadata.expires_at_ns <= now
            or now - metadata.as_of_ns > self.target.max_observation_age_ns
        ):
            raise GroupProofSourceError("GROUP_TRUSTED_INSTRUMENT_FACTS_STALE")
        source_valid_until_ns = min(
            metadata.expires_at_ns, now + self.target.max_observation_age_ns
        )
        obs_material = {
            "accepted_request_sha256": self._accepted_request_sha256,
            "contract_sha256": contract.fingerprint,
            "order_collection_provenance": order_collection,
            "order_provenance": order_prov,
            "normalized_order": _normalized_order(order),
        }
        fill_material = {
            "fill_collection_provenance": fill_collection,
            "fill_rows": fill_records,
        }
        return {
            "acknowledgement": self._acknowledgement,
            "accepted_request_sha256": self._accepted_request_sha256,
            "account_id": request.account_id,
            "client_order_id": request.client_order_id,
            "contract_sha256": contract.fingerprint,
            "evidence_class": self.target.evidence_class.value,
            "fill_source_alignment_valid": fills.source_alignment_valid,
            "fill_source_row_count": len(fills),
            "fill_collection_provenance": fill_collection,
            "fill_history_sha256": sha256_text(canonical_json(fill_material)),
            "fill_records": fill_records,
            "fill_watermark_revision": fill_watermark,
            "filled_quantity": filled_quantity,
            "fee_currency": next(iter(fee_currencies), None),
            "fee_total": sum((item.fee_amount for item in fill_facts), Decimal(0)),
            "fill_facts": tuple(sorted(fill_facts, key=lambda item: item.trade_id)),
            "intent_fingerprint": self.intent.fingerprint,
            "metadata_digest": metadata.digest,
            "metadata_version": metadata.metadata_version,
            "metadata_multiplier": metadata.contract_multiplier,
            "metadata_as_of_ns": metadata.as_of_ns,
            "metadata_expires_at_ns": metadata.expires_at_ns,
            "source_observed_at_ns": now,
            "source_valid_until_ns": source_valid_until_ns,
            "notional_unit": metadata.valuation_unit,
            "normalized_order": _normalized_order(order),
            "order_source_alignment_valid": order.source_alignment_valid,
            "order_source_row_count": order.source_row_count,
            "normalized_status": normalized_status,
            "observation_sha256": sha256_text(canonical_json(obs_material)),
            "order_id": order_id,
            "order_provenance": order_prov,
            "order_collection_provenance": order_collection,
            "order_revision": revision,
            "order_state": state.value,
            "quantity": quantity,
            "quote_currency": quote,
            "request": request.to_dict(),
            "source_price": source_price,
            "state": state,
            "status_mapping_key": raw_status_key,
            "trade_count": len(fill_facts),
        }

    def _status_from_facts(self, facts: Mapping[str, Any], state: ExecutionState):
        average_price = None
        if state in {ExecutionState.FILLED, ExecutionState.PARTIALLY_FILLED} or (
            state is ExecutionState.CANCELLED and facts["filled_quantity"] > 0
        ):
            total_qty = sum((item.quantity for item in facts["fill_facts"]), Decimal(0))
            if total_qty <= 0:
                raise GroupProofSourceError("GROUP_SOURCE_FILL_TOTAL_MISMATCH")
            with localcontext() as context:
                context.prec = 50
                average_price = (
                    sum(
                        (item.quantity * item.price for item in facts["fill_facts"]),
                        Decimal(0),
                    )
                    / total_qty
                )
        # The frozen GroupChildOrderStatus/ProviderObservation contracts require
        # ``^[a-z][a-z0-9_]{0,127}$`` reason codes (authority.py enforces the
        # same shape on the outbox payload), so the rejected observation must
        # use the lowercase snake form.
        reason = "provider_rejected" if state is ExecutionState.REJECTED else None
        return GroupChildOrderStatus(
            intent_id=self.intent.intent_id,
            state=state,
            filled_quantity=facts["filled_quantity"],
            provider_order_id=facts["order_id"],
            average_price=average_price,
            cumulative_commission=facts["fee_total"] if facts["trade_count"] else None,
            cumulative_commission_currency=facts["fee_currency"] if facts["trade_count"] else None,
            reason_code=reason,
            order_revision=facts["order_revision"],
            trade_count=facts["trade_count"],
        )

    def _provider_observation(self, status: GroupChildOrderStatus) -> ProviderObservation:
        return ProviderObservation(
            intent_id=status.intent_id,
            state=status.state,
            provider_order_id=status.provider_order_id,
            filled_quantity=status.filled_quantity,
            average_price=status.average_price,
            reason_code=status.reason_code,
            cumulative_commission=status.cumulative_commission,
            cumulative_commission_currency=status.cumulative_commission_currency,
        )

    def _record_source(
        self,
        facts: Mapping[str, Any],
        *,
        state: ExecutionState,
        status: GroupChildOrderStatus | None = None,
    ) -> GroupProofJournalEvent:
        self._assert_current()
        if self._source_event is not None:
            raise GroupProofSourceError("GROUP_SOURCE_ALREADY_RECORDED")
        payload = self._source_event_payload(facts, state=state, status=status)
        event = self.source._append_event(
            target=self.target,
            fence=self._risk_fence,
            intent=self.intent,
            permit=self.permit,
            event_type="SOURCE_OBSERVED",
            payload=payload,
        )
        self._source_event = event
        return event

    def _source_event_payload(
        self,
        facts: Mapping[str, Any],
        *,
        state: ExecutionState,
        status: GroupChildOrderStatus | None = None,
        include_observation_window: bool = False,
    ) -> dict[str, Any]:
        """Project the validated normalized/provider facts into one source event."""

        if self._risk_fence is None or self._claim_projection is None:
            raise GroupProofSourceError("GROUP_RISK_FENCE_REQUIRED")
        payload = {
            "acknowledgement": facts["acknowledgement"],
            "accepted_request_sha256": facts["accepted_request_sha256"],
            "account_scope_key": self.target.account_scope.key,
            "account_id": facts["account_id"],
            "client_order_id": facts["client_order_id"],
            "claim_digest": self._claim_projection.claim_digest,
            "contract_sha256": facts["contract_sha256"],
            "evidence_class": facts["evidence_class"],
            "fill_source_alignment_valid": facts["fill_source_alignment_valid"],
            "fill_source_row_count": facts["fill_source_row_count"],
            "fill_collection_provenance": facts["fill_collection_provenance"],
            "fill_history_sha256": facts["fill_history_sha256"],
            "fill_records": facts["fill_records"],
            "fill_watermark_revision": facts["fill_watermark_revision"],
            "filled_quantity": facts["filled_quantity"],
            "fee_currency": facts["fee_currency"],
            "fee_total": facts["fee_total"],
            "intent_fingerprint": self.intent.fingerprint,
            "metadata_as_of_ns": facts["metadata_as_of_ns"],
            "metadata_digest": facts["metadata_digest"],
            "metadata_expires_at_ns": facts["metadata_expires_at_ns"],
            "metadata_multiplier": facts["metadata_multiplier"],
            "metadata_version": facts["metadata_version"],
            "normalized_order": facts["normalized_order"],
            "order_source_alignment_valid": facts["order_source_alignment_valid"],
            "order_source_row_count": facts["order_source_row_count"],
            "normalized_status": facts["normalized_status"],
            "notional_unit": facts["notional_unit"],
            "observation_sha256": facts["observation_sha256"],
            "order_collection_provenance": facts["order_collection_provenance"],
            "order_id": facts["order_id"],
            "order_provenance": facts["order_provenance"],
            "order_revision": facts["order_revision"],
            "order_state": facts["order_state"],
            "quantity": facts["quantity"],
            "quote_currency": facts["quote_currency"],
            "request": facts["request"],
            "source_price": facts["source_price"],
            "state": state.value,
            "status": None if status is None else status.to_payload(),
            "status_mapping_key": facts["status_mapping_key"],
            "trade_count": facts["trade_count"],
            "wire_scope": _wire_payload(self.wire_scope),
            "writer_fence": {
                "fence_sha256": self._risk_fence.fence_sha256,
                "fencing_token": self._risk_fence.fencing_token,
                "owner_id": self._risk_fence.owner_id,
                "scope_key": self._risk_fence.scope.key,
            },
        }
        if include_observation_window:
            payload["source_observed_at_ns"] = facts["source_observed_at_ns"]
            payload["source_valid_until_ns"] = facts["source_valid_until_ns"]
        return payload

    def _mark_unknown(self, code: str) -> None:
        if self._attempt_unknown or not self._attempt_started or self._risk_fence is None:
            return
        try:
            self.source._append_event(
                target=self.target,
                fence=self._risk_fence,
                intent=self.intent,
                permit=self.permit,
                event_type="ATTEMPT_UNKNOWN",
                payload={
                    "claim_digest": None
                    if self._claim_projection is None
                    else self._claim_projection.claim_digest,
                    "reason_code": code if _REFERENCE.fullmatch(code) else "GROUP_OUTCOME_UNKNOWN",
                    "source_revision": None
                    if self._source_event is None
                    else self._source_event.revision,
                    "source_record_sha256": None
                    if self._source_event is None
                    else self._source_event.record_sha256,
                },
            )
            self._attempt_unknown = True
        except Exception:
            # The first ATTEMPT_STARTED row plus Execution's UNKNOWN record
            # remain durable; failure to append diagnostics cannot authorize a
            # retry or release either owner hold.
            self._attempt_unknown = True

    def _validate_source_event(self, source_event: GroupProofJournalEvent) -> None:
        payload = source_event.payload
        source_fence = {
            "fence_sha256": source_event.writer_fence_sha256,
            "fencing_token": source_event.writer_fencing_token,
            "owner_id": source_event.writer_owner_id,
            "scope_key": self.target.account_scope.key,
        }
        if (
            source_event.event_type
            not in {"SOURCE_OBSERVED", "SOURCE_PROGRESS", "SOURCE_RECONCILED"}
            or
            payload.get("claim_digest") != self._claim_projection.claim_digest
            or payload.get("intent_fingerprint") != self.intent.fingerprint
            or payload.get("accepted_request_sha256") != self._accepted_request_sha256
            or payload.get("contract_sha256") != self.target.provider_contract.fingerprint
            or payload.get("client_order_id") != self._request.client_order_id
            or payload.get("account_id") != self._request.account_id
            or payload.get("request") != self._request.to_dict()
            or payload.get("wire_scope") != _wire_payload(self.wire_scope)
            or payload.get("writer_fence") != source_fence
            or payload.get("evidence_class") != self.target.evidence_class.value
            or payload.get("account_scope_key") != self.target.account_scope.key
            or payload.get("wire_scope") != _wire_payload(self.wire_scope)
        ):
            raise GroupProofSourceError("GROUP_SOURCE_EVENT_BINDING_MISMATCH")
        if (
            payload.get("state")
            not in {
                ExecutionState.ACKED.value,
                ExecutionState.PARTIALLY_FILLED.value,
                ExecutionState.UNKNOWN.value,
                ExecutionState.FILLED.value,
                ExecutionState.CANCELLED.value,
                ExecutionState.REJECTED.value,
            }
            or payload.get("request") != self._request.to_dict()
            or type(payload.get("normalized_order")) is not dict
            or payload.get("normalized_order")
            != _normalized_order(payload.get("normalized_order", {}))
            or payload.get("order_source_alignment_valid") is not True
            or type(payload.get("order_source_row_count")) is not int
            or payload.get("order_source_row_count") != 1
            or payload.get("fill_source_alignment_valid") is not True
            or type(payload.get("fill_source_row_count")) is not int
        ):
            raise GroupProofSourceError("GROUP_SOURCE_STATE_UNSUPPORTED")
        order_prov = _provenance_payload(payload.get("order_provenance"), "query_order")
        order_collection = _provenance_payload(
            payload.get("order_collection_provenance"), "query_order"
        )
        fill_collection = _provenance_payload(
            payload.get("fill_collection_provenance"), "get_deals"
        )
        contract = self.target.provider_contract
        request = self._request
        order_watermark, _ = _envelope(
            order_collection,
            operation="query_order",
            schema=contract.order_schema,
            client_order_id=request.client_order_id,
            account_id=request.account_id,
        )
        row_watermark, _ = _envelope(
            order_prov,
            operation="query_order",
            schema=contract.order_schema,
            client_order_id=request.client_order_id,
            account_id=request.account_id,
        )
        fill_watermark, _ = _envelope(
            fill_collection,
            operation="get_deals",
            schema=contract.fill_schema,
            client_order_id=request.client_order_id,
            account_id=request.account_id,
        )
        order_values = order_prov["source_values"]
        order_fields = order_prov["source_fields"]
        order_id = _field(order_values, order_fields, _ORDER_ALIASES["order_id"])
        client_id = _field(order_values, order_fields, _ORDER_ALIASES["client_order_id"])
        account_id = _field(order_values, order_fields, _ORDER_ALIASES["account_id"])
        instrument = _field(order_values, order_fields, _ORDER_ALIASES["instrument"])
        side = _field(order_values, order_fields, _ORDER_ALIASES["side"])
        raw_status = _field(order_values, order_fields, _ORDER_ALIASES["status"])
        raw_quantity = _field(order_values, order_fields, _ORDER_ALIASES["quantity"])
        raw_filled = _field(order_values, order_fields, _ORDER_ALIASES["filled"])
        raw_price = _field(order_values, order_fields, _ORDER_ALIASES["price"])
        revision = order_values.get(contract.order_revision_field)
        normalized_order = payload["normalized_order"]
        normalized_status = dict(contract.provider_status_mapping).get(
            str(raw_status).lower().replace("-", "_")
        )
        try:
            quantity = _decimal(raw_quantity, "GROUP_SOURCE_QUANTITY_INVALID", positive=True)
            filled_quantity = _decimal(raw_filled, "GROUP_SOURCE_FILL_INVALID", nonnegative=True)
            source_price = _decimal(raw_price, "GROUP_SOURCE_PRICE_INVALID", positive=True)
        except GroupProofSourceError:
            raise
        if (
            order_watermark != row_watermark
            or type(fill_watermark) is not int
            or type(revision) is not int
            or revision <= 0
            or revision > fill_watermark
            or type(order_id) is not str
            or not order_id
            or type(client_id) is not str
            or client_id != request.client_order_id
            or type(account_id) is not str
            or account_id != request.account_id
            or type(instrument) is not str
            or instrument != request.symbol
            or type(side) is not str
            or side.upper() != request.side.value.upper()
            or quantity != self.intent.quantity
            or source_price != self.intent.price
            or filled_quantity
            != _decimal(
                payload.get("filled_quantity"), "GROUP_SOURCE_FILL_INVALID", nonnegative=True
            )
            or revision != payload.get("order_revision")
            or fill_watermark != payload.get("fill_watermark_revision")
            or normalized_status != payload.get("normalized_status")
            or normalized_status != normalized_order.get("status")
            or normalized_order.get("order_id") != order_id
            or normalized_order.get("client_order_id") != client_id
            or normalized_order.get("account_id") != account_id
            or normalized_order.get("symbol") != instrument
            or str(normalized_order.get("side", "")).upper() != side.upper()
            or not _persisted_normalized_matches_source(
                normalized_order.get("size"),
                quantity,
                "GROUP_NORMALIZED_QUANTITY_INVALID",
                positive=True,
            )
            or not _persisted_normalized_matches_source(
                normalized_order.get("filled"),
                filled_quantity,
                "GROUP_NORMALIZED_FILL_INVALID",
                nonnegative=True,
            )
            or normalized_order.get("execution_unknown") is not False
            or payload.get("status_mapping_key") != str(raw_status).lower().replace("-", "_")
            or payload.get("order_state") != payload.get("state")
            or payload.get("quantity") != format(quantity, "f")
            or payload.get("source_price") != format(source_price, "f")
        ):
            raise GroupProofSourceError("GROUP_SOURCE_ORDER_FACTS_MISMATCH")
        state_by_source = {
            "accepted": ExecutionState.ACKED,
            "submitted": ExecutionState.ACKED,
            "partial": ExecutionState.PARTIALLY_FILLED,
            "completed": ExecutionState.FILLED,
            "canceled": ExecutionState.CANCELLED,
            "expired": ExecutionState.UNKNOWN,
            "rejected": ExecutionState.REJECTED,
            "unknown": ExecutionState.UNKNOWN,
        }
        if state_by_source.get(normalized_status) is not ExecutionState(payload["state"]):
            raise GroupProofSourceError("GROUP_SOURCE_STATUS_INVALID")
        rows = payload.get("fill_records")
        if (
            type(rows) is not list
            or len(rows) != payload.get("trade_count")
            or len(rows) != payload.get("fill_source_row_count")
            or len(rows) > _MAX_FILL_ROWS
        ):
            raise GroupProofSourceError("GROUP_SOURCE_FILL_HISTORY_INVALID")
        fill_values_material = []
        fill_total = Decimal(0)
        fill_gross = Decimal(0)
        fee_total = Decimal(0)
        fee_currencies: set[str] = set()
        trade_ids: set[str] = set()
        for row in rows:
            if not isinstance(row, Mapping):
                raise GroupProofSourceError("GROUP_SOURCE_FILL_HISTORY_INVALID")
            fill_prov = _provenance_payload(row.get("normalization_provenance"), "get_deals")
            if fill_prov["envelope_values"] != fill_collection["envelope_values"]:
                raise GroupProofSourceError("GROUP_SOURCE_FILL_ENVELOPE_MISMATCH")
            values = fill_prov["source_values"]
            fields = fill_prov["source_fields"]
            trade_id = _field(values, fields, _FILL_ALIASES["trade_id"])
            fill_order_id = _field(values, fields, _FILL_ALIASES["order_id"])
            fill_client_id = _field(values, fields, _FILL_ALIASES["client_order_id"])
            fill_account_id = _field(values, fields, _FILL_ALIASES["account_id"])
            fill_instrument = _field(values, fields, _FILL_ALIASES["instrument"])
            fill_side = _field(values, fields, _FILL_ALIASES["side"])
            quantity_raw = _field(values, fields, _FILL_ALIASES["quantity"])
            price_raw = _field(values, fields, _FILL_ALIASES["price"])
            fill_revision = values.get(contract.fill_revision_field)
            fee_raw = values.get(contract.fee_amount_field)
            fee_currency_raw = values.get(contract.fee_currency_field)
            if (
                type(trade_id) is not str
                or not trade_id
                or trade_id in trade_ids
                or type(fill_revision) is not int
                or not 0 < fill_revision <= fill_watermark
                or str(fill_order_id) != order_id
                or str(fill_client_id) != request.client_order_id
                or str(fill_account_id) != request.account_id
                or str(fill_instrument) != request.symbol
                or str(fill_side).upper() != request.side.value.upper()
                or type(fee_currency_raw) is not str
                or not fee_currency_raw
            ):
                raise GroupProofSourceError("GROUP_SOURCE_FILL_IDENTITY_INVALID")
            fill_quantity = _decimal(quantity_raw, "GROUP_SOURCE_FILL_INVALID", positive=True)
            fill_price = _decimal(price_raw, "GROUP_SOURCE_FILL_PRICE_INVALID", positive=True)
            fee = _decimal(fee_raw, "GROUP_SOURCE_FEE_INVALID")
            normalized = row.get("record")
            if (
                type(normalized) is not dict
                or normalized != _normalized_fill(normalized)
                or not _persisted_normalized_matches_source(
                    normalized.get("size"),
                    fill_quantity,
                    "GROUP_NORMALIZED_FILL_INVALID",
                    positive=True,
                )
                or not _persisted_normalized_matches_source(
                    normalized.get("price"),
                    fill_price,
                    "GROUP_NORMALIZED_FILL_PRICE_INVALID",
                    positive=True,
                )
                or not _persisted_normalized_matches_source(
                    normalized.get("fee"), fee, "GROUP_NORMALIZED_FEE_INVALID"
                )
                or normalized.get("trade_id") != trade_id
                or normalized.get("order_id") != order_id
                or normalized.get("client_order_id") != request.client_order_id
                or normalized.get("account_id") != request.account_id
                or normalized.get("symbol") != request.symbol
                or str(normalized.get("side", "")).upper() != request.side.value.upper()
                or str(normalized.get("fee_currency", "")).upper() != fee_currency_raw.upper()
            ):
                raise GroupProofSourceError("GROUP_SOURCE_FILL_NORMALIZATION_MISMATCH")
            row_digest = sha256_text(
                canonical_json(
                    {
                        "schema": "bt-api-provider-source-row-v1",
                        "operation": "get_deals",
                        "source_fields": fill_prov["source_fields"],
                        "source_values": fill_prov["source_values"],
                    }
                )
            )
            if (
                row.get("source_record_sha256") != row_digest
                or row.get("source_revision") != fill_revision
            ):
                raise GroupProofSourceError("GROUP_SOURCE_FILL_PROVENANCE_MISMATCH")
            trade_ids.add(trade_id)
            fill_total += fill_quantity
            with localcontext() as decimal_context:
                decimal_context.prec = 50
                fill_gross += fill_quantity * fill_price
            fee_total += fee
            fee_currencies.add(fee_currency_raw.upper())
            fill_values_material.append(row)
        if (
            fill_total != filled_quantity
            or len(fee_currencies) > 1
            or payload.get("fee_total") != format(fee_total, "f")
            or payload.get("fee_currency") != next(iter(fee_currencies), None)
        ):
            raise GroupProofSourceError("GROUP_SOURCE_FILL_TOTAL_MISMATCH")
        state = ExecutionState(payload["state"])
        if state is ExecutionState.FILLED:
            if filled_quantity != quantity or not rows:
                raise GroupProofSourceError("GROUP_SOURCE_FILL_TOTAL_MISMATCH")
        elif state is ExecutionState.CANCELLED:
            if not (
                filled_quantity == 0 and not rows
                or Decimal("0") < filled_quantity < quantity and bool(rows)
            ):
                raise GroupProofSourceError("GROUP_SOURCE_CANCELLED_FILL_CONFLICT")
        elif state is ExecutionState.REJECTED:
            if filled_quantity != 0 or rows:
                raise GroupProofSourceError("GROUP_SOURCE_REJECTED_WITH_FILL")
        elif state is ExecutionState.ACKED:
            if filled_quantity != 0 or rows:
                raise GroupProofSourceError("GROUP_SOURCE_ACK_FILL_MISMATCH")
        elif state is ExecutionState.PARTIALLY_FILLED:
            if not (Decimal("0") < filled_quantity < quantity and rows):
                raise GroupProofSourceError("GROUP_SOURCE_PARTIAL_FILL_MISMATCH")
        elif state is ExecutionState.UNKNOWN:
            if not (Decimal("0") <= filled_quantity <= quantity) or bool(rows) != (
                filled_quantity > 0
            ):
                raise GroupProofSourceError("GROUP_SOURCE_UNKNOWN_FILL_MISMATCH")
        else:
            raise GroupProofSourceError("GROUP_SOURCE_REJECTED_WITH_FILL")
        metadata = self.target.risk_target.registry.get(self.intent.instrument)
        quote_currency = contract.quote_currency(self.intent.instrument)
        if (
            type(metadata) is not InstrumentRiskMetadata
            or metadata.digest != payload.get("metadata_digest")
            or metadata.metadata_version != payload.get("metadata_version")
            or metadata.contract_multiplier
            != _decimal(payload.get("metadata_multiplier"), "GROUP_METADATA_INVALID", positive=True)
            or metadata.valuation_unit != payload.get("notional_unit")
            or metadata.as_of_ns != payload.get("metadata_as_of_ns")
            or metadata.expires_at_ns != payload.get("metadata_expires_at_ns")
            or quote_currency != metadata.valuation_unit
            or payload.get("quote_currency") != quote_currency
        ):
            raise GroupProofSourceError("GROUP_METADATA_CHANGED")
        source_observed_at_ns = payload.get("source_observed_at_ns")
        source_valid_until_ns = payload.get("source_valid_until_ns")
        if ("source_observed_at_ns" in payload) != ("source_valid_until_ns" in payload):
            raise GroupProofSourceError("GROUP_SOURCE_OBSERVATION_TIME_INVALID")
        if "source_observed_at_ns" in payload and (
            type(source_observed_at_ns) is not int
            or type(source_valid_until_ns) is not int
            or source_observed_at_ns < metadata.as_of_ns
            or source_valid_until_ns <= source_observed_at_ns
            or source_valid_until_ns > metadata.expires_at_ns
            or source_valid_until_ns - source_observed_at_ns
            > self.target.max_observation_age_ns
        ):
            raise GroupProofSourceError("GROUP_SOURCE_OBSERVATION_TIME_INVALID")
        expected_average = None
        if rows:
            with localcontext() as decimal_context:
                decimal_context.prec = 50
                # Derive the average from the exact source-backed fill facts
                # parsed above. The normalized SDK rows intentionally use
                # finite binary64 projections and can round a provider decimal
                # such as 0.100000000000000001 to 0.1.
                expected_average = fill_gross / fill_total
        try:
            status = GroupChildOrderStatus.from_payload(payload.get("status"))
        except Exception:
            raise GroupProofSourceError("GROUP_SOURCE_STATUS_INVALID") from None
        if (
            status.intent_id != self.intent.intent_id
            or status.state is not ExecutionState(payload["state"])
            or status.provider_order_id != order_id
            or status.filled_quantity != filled_quantity
            or status.average_price != expected_average
            or status.cumulative_commission != (fee_total if rows else None)
            or status.cumulative_commission_currency
            != (next(iter(fee_currencies), None) if rows else None)
            or status.order_revision != revision
            or status.trade_count != len(rows)
        ):
            raise GroupProofSourceError("GROUP_SOURCE_STATUS_INVALID")
        obs_material = {
            "accepted_request_sha256": self._accepted_request_sha256,
            "contract_sha256": contract.fingerprint,
            "order_collection_provenance": order_collection,
            "order_provenance": order_prov,
            "normalized_order": normalized_order,
        }
        fill_material = {
            "fill_collection_provenance": fill_collection,
            "fill_rows": fill_values_material,
        }
        if sha256_text(canonical_json(obs_material)) != payload.get(
            "observation_sha256"
        ) or sha256_text(canonical_json(fill_material)) != payload.get("fill_history_sha256"):
            raise GroupProofSourceError("GROUP_SOURCE_OBSERVATION_DIGEST_MISMATCH")

    def _build_exposure_proof(
        self,
        source_event: GroupProofJournalEvent,
        *,
        observed_at_ns: int | None = None,
        valid_until_ns: int | None = None,
        proof_fence: RiskOrderWriterFence | None = None,
    ) -> DispatchExposureResolutionProof:
        self._validate_source_event(source_event)
        payload = source_event.payload
        if payload.get("state") not in {
            ExecutionState.FILLED.value,
            ExecutionState.CANCELLED.value,
        }:
            raise GroupProofSourceError("GROUP_POSITIVE_PROOF_NOT_FILLED")
        fence = self._risk_fence if proof_fence is None else proof_fence
        if type(fence) is not RiskOrderWriterFence or fence.scope != self.target.account_scope:
            raise GroupProofSourceError("GROUP_RISK_FENCE_UNAVAILABLE")
        fills: list[OrderFillFact] = []
        for row in payload["fill_records"]:
            provenance = row["normalization_provenance"]
            values = provenance["source_values"]
            fields = provenance["source_fields"]
            quantity = _decimal(
                _field(values, fields, _FILL_ALIASES["quantity"]),
                "GROUP_FILL_PROOF_INVALID",
                positive=True,
            )
            price = _decimal(
                _field(values, fields, _FILL_ALIASES["price"]),
                "GROUP_FILL_PROOF_INVALID",
                positive=True,
            )
            fee = _decimal(
                values.get(self.target.provider_contract.fee_amount_field),
                "GROUP_FILL_PROOF_INVALID",
            )
            fee_currency = values.get(self.target.provider_contract.fee_currency_field)
            trade_id = _field(values, fields, _FILL_ALIASES["trade_id"])
            side = _field(values, fields, _FILL_ALIASES["side"])
            fill_revision = values.get(self.target.provider_contract.fill_revision_field)
            if (
                type(fee_currency) is not str
                or type(trade_id) is not str
                or type(fill_revision) is not int
            ):
                raise GroupProofSourceError("GROUP_FILL_PROOF_INVALID")
            fills.append(
                OrderFillFact(
                    trade_id=trade_id,
                    quantity=quantity,
                    price=price,
                    side=str(side).upper(),
                    position_effect="OPEN",
                    fee_amount=fee,
                    fee_currency=fee_currency.upper(),
                    source_record_sha256=row["source_record_sha256"],
                    source_revision=fill_revision,
                )
            )
        fill_tuple = tuple(sorted(fills, key=lambda item: item.trade_id))
        fill_qty = sum((item.quantity for item in fill_tuple), Decimal(0))
        expected_filled = (
            self.intent.quantity
            if payload["state"] == ExecutionState.FILLED.value
            else _decimal(payload.get("filled_quantity"), "GROUP_SOURCE_FILL_INVALID", nonnegative=True)
        )
        if fill_qty != expected_filled or len(fill_tuple) != payload.get("trade_count"):
            raise GroupProofSourceError("GROUP_SOURCE_FILL_TOTAL_MISMATCH")
        claim = self._claim_receipt.claim
        metadata = self.target.risk_target.registry.get(self.intent.instrument)
        if (
            type(metadata) is not InstrumentRiskMetadata
            or metadata.digest != payload.get("metadata_digest")
            or metadata.metadata_version != payload.get("metadata_version")
            or metadata.contract_multiplier
            != _decimal(payload.get("metadata_multiplier"), "GROUP_METADATA_INVALID", positive=True)
            or metadata.valuation_unit != payload.get("notional_unit")
            or metadata.as_of_ns != payload.get("metadata_as_of_ns")
            or metadata.expires_at_ns != payload.get("metadata_expires_at_ns")
        ):
            raise GroupProofSourceError("GROUP_METADATA_CHANGED")
        now = self.source._clock_ns()
        if type(now) is not int:
            raise GroupProofSourceError("GROUP_METADATA_STALE")
        if observed_at_ns is None and valid_until_ns is None:
            observed = payload.get("source_observed_at_ns")
            valid_until = payload.get("source_valid_until_ns")
            if type(observed) is not int or type(valid_until) is not int:
                raise GroupProofSourceError("GROUP_PROOF_RECOVERY_FACTS_MISSING")
        elif type(observed_at_ns) is int and type(valid_until_ns) is int:
            observed = observed_at_ns
            valid_until = valid_until_ns
        else:
            raise GroupProofSourceError("GROUP_PROOF_RECOVERY_FACTS_MISSING")
        if (
            type(observed) is not int
            or type(valid_until) is not int
            or observed < metadata.as_of_ns
            or observed > now
            or valid_until <= now
            or valid_until > metadata.expires_at_ns
            or valid_until - observed > self.target.max_observation_age_ns
        ):
            raise GroupProofSourceError("GROUP_METADATA_STALE")
        gross = sum(
            (item.quantity * item.price * metadata.contract_multiplier for item in fill_tuple),
            Decimal(0),
        )
        reservation_id = "group-exposure-v1:" + sha256_text(
            canonical_json(
                {
                    "claim_digest": claim.claim_digest,
                    "intent_fingerprint": self.intent.fingerprint,
                    "permit_id": self.permit.permit_id,
                    "provider_order_id": payload["order_id"],
                }
            )
        )
        reservation_sha = sha256_text(
            canonical_json(
                {
                    "accepted_request_sha256": self._accepted_request_sha256,
                    "claim_digest": claim.claim_digest,
                    "fill_history_sha256": payload["fill_history_sha256"],
                    "intent_fingerprint": self.intent.fingerprint,
                    "metadata_digest": metadata.digest,
                    "order_id": payload["order_id"],
                    "order_revision": payload["order_revision"],
                    "source_record_sha256": source_event.record_sha256,
                }
            )
        )
        valuation_facts = sha256_text(
            canonical_json(
                {
                    "fill_fingerprints": [item.fingerprint for item in fill_tuple],
                    "instrument_metadata_sha256": metadata.digest,
                    "instrument_multiplier": format(metadata.contract_multiplier, "f"),
                    "quote_currency": payload["quote_currency"],
                    "valuation_unit": payload["notional_unit"],
                }
            )
        )
        try:
            observation = OrderExposureObservationProof(
                scope=claim.scope,
                permit_id=claim.permit_id,
                intent_id=claim.intent_id,
                intent_hash=claim.intent_hash,
                dispatch_claim_digest=claim.claim_digest,
                dispatch_proof_sha256=None,
                provider_order_id=payload["order_id"],
                accepted_request_sha256=self._accepted_request_sha256,
                exposure_reservation_id=reservation_id,
                exposure_reservation_sha256=reservation_sha,
                order_revision=payload["order_revision"],
                journal_revision=source_event.revision,
                journal_record_sha256=source_event.record_sha256,
                observation_sha256=payload["observation_sha256"],
                fill_history_sha256=payload["fill_history_sha256"],
                source_alias_map_sha256=_ALIAS_MAP_SHA256,
                state=(
                    OrderExposureState.CANCELLED
                    if payload["state"] == ExecutionState.CANCELLED.value
                    else OrderExposureState.FILLED
                ),
                requested_quantity=self.intent.quantity,
                cumulative_filled_quantity=fill_qty,
                leaves_quantity=Decimal("0"),
                filled_gross_notional=gross,
                leaves_gross_notional=Decimal("0"),
                leaves_valuation_price=None,
                notional_unit=metadata.valuation_unit,
                complete_order_snapshot=True,
                complete_fill_history=True,
                fill_watermark_revision=payload["fill_watermark_revision"],
                source_contract_ref=self.target.provider_contract.profile_id,
                source_contract_version=self.target.provider_contract.fingerprint,
                provider_profile_version=self.target.provider_contract.profile_id,
                normalizer_schema_version=self.target.provider_contract.normalizer_schema,
                order_side=self.intent.side.value.upper(),
                position_effect="OPEN",
                fills=fill_tuple,
                fill_trade_count=len(fill_tuple),
                fill_ids_sha256=fill_ids_sha256(fill_tuple),
                instrument_id=metadata.instrument,
                instrument_version=metadata.metadata_version,
                instrument_multiplier=metadata.contract_multiplier,
                quote_currency=payload["quote_currency"],
                valuation_unit=metadata.valuation_unit,
                instrument_metadata_sha256=metadata.digest,
                instrument_metadata_observed_at_ns=metadata.as_of_ns,
                instrument_metadata_valid_until_ns=metadata.expires_at_ns,
                valuation_facts_sha256=valuation_facts,
                valuation_observed_at_ns=metadata.as_of_ns,
                valuation_valid_until_ns=metadata.expires_at_ns,
                fx_pair=None,
                fx_rate=None,
                fx_observed_at_ns=None,
                fx_valid_until_ns=None,
                observed_at_ns=observed,
                valid_until_ns=valid_until,
                writer_owner_id=fence.owner_id,
                writer_fencing_token=fence.fencing_token,
                writer_fence_sha256=fence.fence_sha256,
            )
            proof = DispatchExposureResolutionProof(
                claim=claim,
                evidence_class=self.target.evidence_class,
                dispatch_attempt_count=1,
                reconciliation_evidence_sha256=sha256_text(
                    canonical_json(
                        {
                            "accepted_request_sha256": self._accepted_request_sha256,
                            "claim_digest": claim.claim_digest,
                            "fill_history_sha256": payload["fill_history_sha256"],
                            "observation_sha256": payload["observation_sha256"],
                            "source_record_sha256": source_event.record_sha256,
                        }
                    )
                ),
                observation=observation,
            )
        except GroupProofSourceError:
            raise
        except Exception:
            raise GroupProofSourceError("GROUP_POSITIVE_PROOF_INVALID") from None
        return proof

    def _build_terminal_proof(
        self,
        source_event: GroupProofJournalEvent,
        *,
        proof_fence: RiskOrderWriterFence | None = None,
    ) -> DispatchTerminalProof:
        self._validate_source_event(source_event)
        payload = source_event.payload
        fence = self._risk_fence if proof_fence is None else proof_fence
        if type(fence) is not RiskOrderWriterFence or fence.scope != self.target.account_scope:
            raise GroupProofSourceError("GROUP_RISK_FENCE_UNAVAILABLE")
        state = payload.get("state")
        terminal_state = (
            DispatchTerminalState.CANCELED_NO_FILL
            if state == ExecutionState.CANCELLED.value
            else DispatchTerminalState.REJECTED_NO_FILL
        )
        if (
            state not in {ExecutionState.REJECTED.value, ExecutionState.CANCELLED.value}
            or payload.get("filled_quantity") != "0"
            or payload.get("trade_count") != 0
            or payload.get("fill_records") != []
        ):
            raise GroupProofSourceError("GROUP_NO_FILL_PROOF_INVALID")
        claim = self._claim_receipt.claim
        reconciliation = sha256_text(
            canonical_json(
                {
                    "accepted_request_sha256": self._accepted_request_sha256,
                    "claim_digest": claim.claim_digest,
                    "fill_history_sha256": payload["fill_history_sha256"],
                    "observation_sha256": payload["observation_sha256"],
                    "source_record_sha256": source_event.record_sha256,
                }
            )
        )
        try:
            return DispatchTerminalProof(
                scope=claim.scope,
                permit_id=claim.permit_id,
                intent_id=claim.intent_id,
                intent_hash=claim.intent_hash,
                cause_id=claim.cause_id,
                claim_digest=claim.claim_digest,
                evidence_class=self.target.evidence_class,
                terminal_state=terminal_state,
                dispatch_attempt_count=1,
                journal_revision=source_event.revision,
                journal_record_sha256=source_event.record_sha256,
                reconciliation_evidence_sha256=reconciliation,
                writer_fence_sha256=fence.fence_sha256,
                fee_record_count=0,
                fee_history_complete=True,
                filled_quantity=0,
                trade_count=0,
            )
        except Exception:
            raise GroupProofSourceError("GROUP_NO_FILL_PROOF_INVALID") from None

    def _build_terminal_proof_for_recovery(
        self, source_event: GroupProofJournalEvent
    ) -> tuple[DispatchExposureResolutionProof | DispatchTerminalProof, dict[str, Any]]:
        if self._risk_fence is None or self._claim_projection is None:
            raise GroupProofSourceError("GROUP_RISK_FENCE_REQUIRED")
        state = source_event.payload.get("state")
        payload: dict[str, Any] = {
            "claim_digest": self._claim_projection.claim_digest,
            "contract_sha256": self.target.provider_contract.fingerprint,
            "proof_kind": _source_proof_kind(source_event.payload),
            "source_record_sha256": source_event.record_sha256,
            "source_revision": source_event.revision,
            "proof_writer_fence": _proof_fence_payload(self._risk_fence),
        }
        if state == ExecutionState.FILLED.value or (
            state == ExecutionState.CANCELLED.value
            and _decimal(
                source_event.payload.get("filled_quantity"),
                "GROUP_SOURCE_STATE_UNSUPPORTED",
            ) > 0
        ):
            proof = self._build_exposure_proof(source_event, proof_fence=self._risk_fence)
            payload["proof_sha256"] = proof.fingerprint
            payload["proof_observed_at_ns"] = proof.observation.observed_at_ns
            payload["proof_valid_until_ns"] = proof.observation.valid_until_ns
        elif state in {ExecutionState.REJECTED.value, ExecutionState.CANCELLED.value}:
            proof = self._build_terminal_proof(source_event, proof_fence=self._risk_fence)
            payload["proof_sha256"] = proof.fingerprint
        else:
            raise GroupProofSourceError("GROUP_SOURCE_STATE_UNSUPPORTED")
        return proof, payload

    def _proof_from_ready_event(
        self,
        source_event: GroupProofJournalEvent,
        proof_event: GroupProofJournalEvent,
    ) -> DispatchExposureResolutionProof | DispatchTerminalProof:
        if proof_event.event_type != "PROOF_READY":
            raise GroupProofSourceError("GROUP_PROOF_EVENT_INVALID")
        payload = proof_event.payload
        if (
            payload.get("source_revision") != source_event.revision
            or payload.get("source_record_sha256") != source_event.record_sha256
            or payload.get("proof_kind") != _source_proof_kind(source_event.payload)
            or payload.get("claim_digest")
            != (None if self._claim_projection is None else self._claim_projection.claim_digest)
            or payload.get("contract_sha256") != self.target.provider_contract.fingerprint
        ):
            raise GroupProofSourceError("GROUP_PROOF_BINDING_MISMATCH")
        proof_fence = _proof_fence_from_event(
            proof_event, source_event, self.target.account_scope
        )
        state = source_event.payload.get("state")
        proof_kind = _source_proof_kind(source_event.payload)
        if proof_kind in {"FILLED", "CANCELLED_PARTIAL"}:
            observed = payload.get("proof_observed_at_ns")
            valid_until = payload.get("proof_valid_until_ns")
            if type(observed) is not int or type(valid_until) is not int:
                raise GroupProofSourceError("GROUP_PROOF_RECOVERY_FACTS_MISSING")
            source_payload = source_event.payload
            if (
                ("source_observed_at_ns" in source_payload)
                != ("source_valid_until_ns" in source_payload)
                or (
                    "source_observed_at_ns" in source_payload
                    and (
                        type(source_payload.get("source_observed_at_ns")) is not int
                        or type(source_payload.get("source_valid_until_ns")) is not int
                        or observed != source_payload.get("source_observed_at_ns")
                        or valid_until != source_payload.get("source_valid_until_ns")
                    )
                )
            ):
                raise GroupProofSourceError("GROUP_PROOF_SOURCE_MISMATCH")
            proof = self._build_exposure_proof(
                source_event,
                observed_at_ns=observed,
                valid_until_ns=valid_until,
                proof_fence=proof_fence,
            )
        elif proof_kind in {"REJECTED_NO_FILL", "CANCELED_NO_FILL"}:
            proof = self._build_terminal_proof(source_event, proof_fence=proof_fence)
        else:
            raise GroupProofSourceError("GROUP_SOURCE_STATE_UNSUPPORTED")
        if payload.get("proof_sha256") != proof.fingerprint:
            raise GroupProofSourceError("GROUP_PROOF_SOURCE_MISMATCH")
        return proof

    def after_group_result(
        self,
        intent: OrderIntent,
        binding: GroupPermitBinding,
        authorization: GroupLegAuthorization,
        writer_lease: WriterLease,
        validated_claim: object,
        record: ExecutionRecord,
    ) -> None:
        self._check_callback_identity(intent, binding, authorization, writer_lease, validated_claim)
        if record.state in {ExecutionState.ACKED, ExecutionState.PARTIALLY_FILLED}:
            if (
                not self._attempt_started
                or self._source_event is None
                or self._status is None
                or record.dispatch_attempts != 1
                or record.state is not self._status.state
                or record.provider_order_id != self._status.provider_order_id
                or record.filled_quantity != self._status.filled_quantity
                or record.average_price != self._status.average_price
                or record.cumulative_commission != self._status.cumulative_commission
            ):
                self._mark_unknown("GROUP_OPEN_ORDER_RESULT_MISMATCH")
                raise GroupProofSourceError("GROUP_OPEN_ORDER_RESULT_UNPROVEN")
            self._validate_source_event(self._source_event)
            self._assert_current()
            try:
                # Settlement records the accepted source-backed dispatch while
                # deliberately retaining the original Risk dispatch latch.
                self.source._runtime_bundle.risk_admission.ensure_settled(
                    self.permit.permit_id
                )
            except Exception:
                raise GroupProofSourceError("GROUP_OPEN_ORDER_RISK_HOLD_UNPROVEN") from None
            self._assert_current()
            # The Execution record and source facts are now durable, but the
            # original submit claim remains unresolved until complete terminal
            # order and fill history produce the existing typed proof.
            raise GroupProofSourceError("GROUP_ORDER_STILL_OPEN")
        if (
            not self._attempt_started
            or self._source_event is None
            or type(record) is not ExecutionRecord
            or record.state
            not in {
                ExecutionState.FILLED,
                ExecutionState.CANCELLED,
                ExecutionState.REJECTED,
            }
            or record.dispatch_attempts != 1
            or self._status is None
            or record.state is not self._status.state
            or record.provider_order_id != self._status.provider_order_id
            or record.filled_quantity != self._status.filled_quantity
            or record.average_price != self._status.average_price
            or record.cumulative_commission != self._status.cumulative_commission
        ):
            self._mark_unknown("GROUP_EXECUTION_RESULT_MISMATCH")
            raise GroupProofSourceError("GROUP_EXECUTION_RESULT_UNPROVEN")
        self._assert_current()
        claim = self._claim_receipt.claim
        positive_exposure = record.state is ExecutionState.FILLED or (
            record.state is ExecutionState.CANCELLED and record.filled_quantity > 0
        )
        if positive_exposure:
            if type(self._facts) is not dict:
                raise GroupProofSourceError("GROUP_SOURCE_FACTS_UNAVAILABLE")
            proof = self._build_exposure_proof(
                self._source_event,
                observed_at_ns=self._facts.get("source_observed_at_ns"),
                valid_until_ns=self._facts.get("source_valid_until_ns"),
            )
            kind = _source_proof_kind(self._source_event.payload)
            proof_sha256 = proof.fingerprint
            proof_payload = {
                "claim_digest": claim.claim_digest,
                "contract_sha256": self.target.provider_contract.fingerprint,
                "proof_kind": kind,
                "proof_sha256": proof_sha256,
                "proof_writer_fence": _proof_fence_payload(self._risk_fence),
                "proof_observed_at_ns": proof.observation.observed_at_ns,
                "proof_valid_until_ns": proof.observation.valid_until_ns,
                "source_record_sha256": self._source_event.record_sha256,
                "source_revision": self._source_event.revision,
            }
        else:
            proof = self._build_terminal_proof(self._source_event)
            kind = _source_proof_kind(self._source_event.payload)
            proof_sha256 = proof.fingerprint
            proof_payload = {
                "claim_digest": claim.claim_digest,
                "contract_sha256": self.target.provider_contract.fingerprint,
                "proof_kind": kind,
                "proof_sha256": proof_sha256,
                "proof_writer_fence": _proof_fence_payload(self._risk_fence),
                "source_record_sha256": self._source_event.record_sha256,
                "source_revision": self._source_event.revision,
            }
        proof_event = self.source._append_event(
            target=self.target,
            fence=self._risk_fence,
            intent=self.intent,
            permit=self.permit,
            event_type="PROOF_READY",
            payload=proof_payload,
        )
        self._assert_current()
        try:
            # The terminal Execution result and its hash-chained source proof
            # are durable before Risk consumes the claimed reservation. This
            # idempotent transition keeps the dispatch latch active; the typed
            # proof below is still required to resolve it.
            self.source._runtime_bundle.risk_admission.ensure_settled(
                self.permit.permit_id
            )
            if type(proof) is DispatchExposureResolutionProof:
                self.source._runtime_bundle.risk_admission.resolve_dispatch_exposure(proof)
            else:
                self.source._runtime_bundle.risk_admission.resolve_dispatch_freeze(proof)
            receipt = self.source._risk_receipt(
                self.target,
                self.permit,
                positive=type(proof) is DispatchExposureResolutionProof,
            )
            self._assert_current()
            resolution = self.source._resolution_projection(
                target=self.target,
                permit=self.permit,
                intent=self.intent,
                claim_projection=self._claim_projection,
                risk_receipt=receipt,
                status=self._status,
                source_event=self._source_event,
            )
            self.source._append_event(
                target=self.target,
                fence=self._risk_fence,
                intent=self.intent,
                permit=self.permit,
                event_type="RISK_RESOLVED",
                payload={
                    "proof_ready_revision": proof_event.revision,
                    "proof_ready_record_sha256": proof_event.record_sha256,
                    "proof_sha256": proof_sha256,
                    "resolution": resolution.to_payload(),
                    "source_revision": self._source_event.revision,
                    "source_record_sha256": self._source_event.record_sha256,
                },
            )
            self._assert_current()
            # Validate the same durable projection that a later read-only
            # query will expose before the package's authorization context
            # exits and releases its hold.  finish() runs after that release,
            # so it must not try to re-enter the active-hold guard.
            receipt = self.source._read_receipt_locked(
                target=self.target,
                binding=self.binding,
                intent=self.intent,
                permit=self.permit,
                mapped=self.mapped_intent,
            )
            current_record = self.target.execution_store.get(
                self.intent.intent_id, scope=self.target.execution_scope
            )
            if (
                type(receipt) is not GroupLegOrderReceipt
                or receipt.resolution is None
                or receipt.status != self._status
                or current_record != record
            ):
                raise GroupProofSourceError("GROUP_TERMINAL_RECEIPT_UNPROVEN")
            self._assert_current()
            self._terminal_receipt = receipt
            self._terminal_record = current_record
            self._terminal_binding = binding
            self._terminal_authorization = authorization
            self._terminal_writer_lease = writer_lease
            self._terminal_claim_receipt = validated_claim
        except Exception:
            # The exact provider result remains in Execution's durable store;
            # if Risk may have committed, its exact receipt is authoritative
            # on readback. The coordinator hold is retained by callback error.
            raise GroupProofSourceError("GROUP_RISK_RESOLUTION_UNPROVEN") from None

    def _check_callback_identity(
        self,
        intent: OrderIntent,
        binding: GroupPermitBinding,
        authorization: GroupLegAuthorization,
        writer_lease: WriterLease,
        validated_claim: object,
    ) -> None:
        if (
            intent is not self.intent
            or binding != self.binding
            or authorization != self._authorization
            or writer_lease != self._writer_lease
            or validated_claim != self._claim_receipt
        ):
            raise GroupProofSourceError("GROUP_CALLBACK_BINDING_MISMATCH")
        self._assert_current()

    def finish(self, *, record: ExecutionRecord) -> GroupLegOrderReceipt:
        if (
            self._finished
            or self._aborted
            or type(record) is not ExecutionRecord
            or self._terminal_receipt is None
            or self._terminal_record is None
            or self._terminal_binding is None
            or self._terminal_authorization is None
            or self._terminal_writer_lease is None
            or self._terminal_claim_receipt is None
            or self._status is None
            or self._claim_receipt is None
            or self._authorization is None
            or self._writer_lease is None
            or self._risk_fence is None
        ):
            raise GroupProofSourceError("GROUP_SESSION_FINISH_INVALID")
        receipt = self._terminal_receipt
        current_record = self.target.execution_store.get(
            self.intent.intent_id, scope=self.target.execution_scope
        )
        if (
            type(receipt) is not GroupLegOrderReceipt
            or receipt.resolution is None
            or receipt.status.state
            not in {
                ExecutionState.FILLED,
                ExecutionState.CANCELLED,
                ExecutionState.REJECTED,
            }
            or receipt.status.state is not record.state
            or receipt.status.intent_id != self.intent.intent_id
            or receipt.status.filled_quantity != record.filled_quantity
            or receipt.status.provider_order_id != record.provider_order_id
            or receipt.status.average_price != record.average_price
            or receipt.status.cumulative_commission != record.cumulative_commission
            or record.dispatch_attempts != 1
            or record != self._terminal_record
            or current_record != self._terminal_record
            or self._terminal_binding != self.binding
            or self._terminal_authorization != self._authorization
            or self._terminal_writer_lease != self._writer_lease
            or self._terminal_claim_receipt != self._claim_receipt
            or receipt.claim != self._claim_projection
            or receipt.resolution.claim != receipt.claim
            or receipt.resolution.final_state is not record.state
            or receipt.resolution.filled_quantity != record.filled_quantity
            or receipt.resolution.trade_count != self._status.trade_count
            or receipt.status != self._status
            or not self._attempt_started
            or self._attempt_unknown
        ):
            raise GroupProofSourceError("GROUP_TERMINAL_RECEIPT_UNPROVEN")
        # Check that the call-local Risk fence used while constructing and
        # validating the receipt has not changed. The coordinator hold is
        # expected to be gone after normal package return.
        try:
            with self.target.risk_order_journal.writer_fence(
                self.target.account_scope
            ) as current_fence:
                if current_fence != self._risk_fence:
                    raise GroupProofSourceError("GROUP_RISK_FENCE_CHANGED")
        except GroupProofSourceError:
            raise
        except Exception:
            raise GroupProofSourceError("GROUP_OWNER_FENCE_UNAVAILABLE") from None
        self._finished = True
        self._release_risk_fence()
        return receipt

    def abort(self) -> None:
        if self._aborted or self._finished:
            return
        self._aborted = True
        if self._attempt_started and not self._attempt_unknown and self._source_event is None:
            self._mark_unknown("GROUP_SESSION_ABORTED")
        self._release_risk_fence()

    def _release_risk_fence(self) -> None:
        token = self._context_token
        self._context_token = None
        if token is not None:
            with suppress(Exception):
                _ACTIVE_SESSION.reset(token)
        context = self._risk_fence_context
        self._risk_fence_context = None
        self._risk_fence = None
        if context is not None:
            with suppress(Exception):
                context.__exit__(None, None, None)


def _normalized_order(value: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "account_id",
        "client_order_id",
        "order_id",
        "symbol",
        "side",
        "status",
        "filled",
        "size",
        "execution_unknown",
        "terminal_confirmed",
        "definite_reject",
    )
    return {key: _json_scalar(value.get(key)) for key in keys}


def _normalized_fill(value: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "account_id",
        "client_order_id",
        "order_id",
        "symbol",
        "side",
        "trade_id",
        "size",
        "price",
        "fee",
        "fee_currency",
    )
    return {key: _json_scalar(value.get(key)) for key in keys}


def _safe_ack(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    for key in ("filled", "size"):
        number = value.get(key)
        if number is not None and (type(number) is not float or not math.isfinite(number)):
            raise GroupProofSourceError("GROUP_SOURCE_FACT_INVALID")
    provenance = getattr(value, "normalization_provenance", None)
    if type(provenance) is NormalizationProvenance:
        provenance_payload = provenance.to_dict()
    else:
        provenance_payload = None
    return {
        "normalization_provenance": provenance_payload,
        "record": _normalized_order(value),
    }


def _provenance_payload(value: object, operation: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise GroupProofSourceError("GROUP_SOURCE_PROVENANCE_INVALID")
    required = {
        "schema",
        "operation",
        "source_fields",
        "source_values",
        "envelope_fields",
        "envelope_values",
        "source_sha256",
    }
    if (
        set(value) != required
        or value.get("operation") != operation
        or value.get("schema") != _NORMALIZER_V2
    ):
        raise GroupProofSourceError("GROUP_SOURCE_PROVENANCE_INVALID")
    if (
        type(value["source_fields"]) is not list
        or value["source_fields"] != sorted(set(value["source_fields"]))
        or type(value["envelope_fields"]) is not list
        or value["envelope_fields"] != sorted(set(value["envelope_fields"]))
        or type(value["source_values"]) is not dict
        or type(value["envelope_values"]) is not dict
        or set(value["source_values"]) - set(value["source_fields"])
        or set(value["envelope_values"]) - set(value["envelope_fields"])
    ):
        raise GroupProofSourceError("GROUP_SOURCE_PROVENANCE_INVALID")
    if any(type(name) is not str for name in value["source_fields"] + value["envelope_fields"]):
        raise GroupProofSourceError("GROUP_SOURCE_PROVENANCE_INVALID")
    for projection in (value["source_values"], value["envelope_values"]):
        if any(
            type(key) is not str or type(item) not in (str, int, bool)
            for key, item in projection.items()
        ):
            raise GroupProofSourceError("GROUP_SOURCE_PROVENANCE_INVALID")
    expected = sha256_text(
        canonical_json(
            {
                "envelope_values": value["envelope_values"],
                "operation": operation,
                "schema": _NORMALIZER_V2,
                "source_fields": value["source_fields"],
                "source_values": value["source_values"],
            }
        )
    )
    if value["source_sha256"] != expected:
        raise GroupProofSourceError("GROUP_SOURCE_PROVENANCE_INVALID")
    return value
