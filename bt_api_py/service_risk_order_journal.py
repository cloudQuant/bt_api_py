"""Durable, provider-observation journal for the risk-only order owner.

This is a small dispatch/idempotency journal, not an order-management system.
Provider APIs remain the authority for order and fill facts.  The journal keeps
the one allowed dispatch attempt, the normalized observations and a durable
reference to the Risk reservation used to resolve that attempt.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import MappingProxyType
from typing import Any

from bt_api_risk import (
    AccountScope,
    DispatchClaimBinding,
    DispatchEvidenceClass,
    DispatchRecoveryAttestation,
    DispatchResolutionReceipt,
    DispatchResolutionReceiptStatus,
    DispatchTrackedOrderProof,
    VerifiedDispatchRecoveryResolution,
    VerifiedDispatchResolution,
)

_SCHEMA_VERSION = 1
_SHA256_LENGTH = 64
_WIRE_SCOPE_KEYS = frozenset(
    {
        "account_ref",
        "account_refs",
        "execution_domain_ref",
        "group_id",
        "leg_id",
        "manual_book_ref",
        "portfolio_ref",
        "run_id",
        "strategy_ref",
    }
)
_PROCESS_LOCK_GUARD = threading.Lock()
_PROCESS_LOCKS: dict[str, threading.RLock] = {}
_THREAD_FENCES = threading.local()


class RiskOrderJournalError(RuntimeError):
    """Finite, payload-free journal error."""

    def __init__(self, code: str) -> None:
        if type(code) is not str or not code or len(code) > 96:
            raise ValueError("invalid journal error code")
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class RiskOrderWriterFence:
    """One current, account-wide process fence held by the caller."""

    scope: AccountScope
    owner_id: str
    fencing_token: int
    fence_sha256: str

    def __post_init__(self) -> None:
        if type(self.scope) is not AccountScope:
            raise ValueError("invalid risk-order writer scope")
        if type(self.owner_id) is not str or not self.owner_id:
            raise ValueError("invalid risk-order writer owner")
        if type(self.fencing_token) is not int or self.fencing_token <= 0:
            raise ValueError("invalid risk-order fencing token")
        _require_sha256(self.fence_sha256, "writer fence digest")


@dataclass(frozen=True, slots=True)
class RiskOrderCommand:
    """Immutable command identity plus its latest append-only journal event."""

    scope: AccountScope
    principal_ref: str
    intent_id: str
    risk_intent_id: str
    payload_sha256: str
    canonical_payload: Mapping[str, Any]
    wire_scope: Mapping[str, Any]
    exchange_name: str
    provider_account_id: str
    client_order_id: str
    permit_id: str | None
    order_request: Mapping[str, Any]
    order_request_sha256: str
    created_revision: int
    created_record_sha256: str
    created_at_ns: int
    updated_at_ns: int
    state: str
    latest_revision: int
    latest_record_sha256: str
    latest_event: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class RiskOrderJournalEvent:
    scope: AccountScope
    principal_ref: str
    intent_id: str
    revision: int
    event_type: str
    payload: Mapping[str, Any]
    previous_account_sha256: str
    previous_command_sha256: str
    record_sha256: str
    writer_owner_id: str
    writer_fencing_token: int
    writer_fence_sha256: str


@dataclass(frozen=True, slots=True)
class DispatchProofRecoverySource:
    """Verified immutable source rows needed to recover a proof-ready order.

    The original ``PROOF_READY`` event and its exposure reference are never
    rewritten under a later writer fence. Recovery appends a separate event
    that binds those original rows to the current fence.
    """

    command: RiskOrderCommand
    claim: DispatchClaimBinding
    proof: DispatchTrackedOrderProof
    proof_ready_event: RiskOrderJournalEvent
    order_observation_event: RiskOrderJournalEvent
    fill_observation_event: RiskOrderJournalEvent
    exposure_reference: Mapping[str, Any]
    exposure_reference_id: str
    exposure_reference_sha256: str
    recovery_events: tuple[RiskOrderJournalEvent, ...]
    recovery_attestations: tuple[DispatchRecoveryAttestation, ...]


def canonical_json(value: Any) -> str:
    """Encode the deliberately scalar journal DTOs without lossy defaults."""

    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError):
        raise RiskOrderJournalError("JOURNAL_PAYLOAD_INVALID") from None


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="strict")).hexdigest()


def _require_sha256(value: object, label: str) -> str:
    if (
        type(value) is not str
        or len(value) != _SHA256_LENGTH
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise ValueError("invalid " + label)
    return value


def _same_wire_scope_for_idempotency(
    persisted: Mapping[str, Any], requested: Mapping[str, Any]
) -> bool:
    """Permit a new trusted strategy run without changing business identity."""

    if set(persisted) != _WIRE_SCOPE_KEYS or set(requested) != _WIRE_SCOPE_KEYS:
        return False
    if any(persisted.get(key) != requested.get(key) for key in _WIRE_SCOPE_KEYS if key != "run_id"):
        return False
    persisted_run = persisted.get("run_id")
    requested_run = requested.get("run_id")
    if persisted_run == requested_run:
        return True
    return (
        type(persisted_run) is str
        and bool(persisted_run)
        and type(requested_run) is str
        and bool(requested_run)
        and type(persisted.get("strategy_ref")) is str
        and bool(persisted.get("strategy_ref"))
    )


def _scope_key(scope: AccountScope) -> str:
    if type(scope) is not AccountScope:
        raise RiskOrderJournalError("INVALID_ACCOUNT_SCOPE")
    return scope.key


def _thread_fences() -> dict[str, RiskOrderWriterFence]:
    value = getattr(_THREAD_FENCES, "by_scope", None)
    if value is None:
        value = {}
        _THREAD_FENCES.by_scope = value
    return value


class RiskOrderJournal:
    """Append-only scoped intent/evidence journal with a durable writer fence.

    The advisory lock does not expire while a provider call is in flight.  A
    monotonically increasing SQLite fencing token changes on every new owner.
    Every append rechecks that token; dispatch evidence is accepted by the Risk
    resolver only while the same account lock remains held.
    """

    def __init__(self, path: str | os.PathLike[str], *, writer_id: str) -> None:
        if type(writer_id) is not str or not writer_id or writer_id != writer_id.strip():
            raise RiskOrderJournalError("INVALID_WRITER_ID")
        if len(writer_id) > 96:
            raise RiskOrderJournalError("INVALID_WRITER_ID")
        self._path = Path(path).expanduser().resolve()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._writer_id = writer_id
        self._instance_id = uuid.uuid4().hex
        self._owner_id = f"{writer_id}:{os.getpid()}:{self._instance_id}"
        self._initialize()

    @property
    def path(self) -> Path:
        """Return the resolved journal path for trusted service assembly."""

        return self._path

    @contextmanager
    def writer_fence(self, scope: AccountScope) -> Iterator[RiskOrderWriterFence]:
        """Hold one account-wide, non-expiring OS lock and issue its generation."""

        key = _scope_key(scope)
        active = _thread_fences().get(key)
        if active is not None:
            self._assert_current(active)
            yield active
            return

        lock_path = self._lock_path(scope)
        lock_key = str(lock_path).casefold()
        with _PROCESS_LOCK_GUARD:
            process_lock = _PROCESS_LOCKS.setdefault(lock_key, threading.RLock())
        process_lock.acquire()
        owner_file = None
        try:
            owner_file = self._open_and_lock(lock_path)
            token = self._next_fencing_token(scope)
            material = canonical_json(
                {
                    "account_scope_key": scope.key,
                    "fencing_token": token,
                    "owner_id": self._owner_id,
                    "schema": "bt-api-risk-order-writer-fence-v1",
                }
            )
            fence = RiskOrderWriterFence(
                scope=scope,
                owner_id=self._owner_id,
                fencing_token=token,
                fence_sha256=sha256_text(material),
            )
            _thread_fences()[key] = fence
            self._assert_current(fence)
            yield fence
        finally:
            _thread_fences().pop(key, None)
            if owner_file is not None:
                try:
                    self._unlock_and_close(owner_file)
                finally:
                    process_lock.release()
            else:
                process_lock.release()

    def prepare_or_get(
        self,
        fence: RiskOrderWriterFence,
        *,
        principal_ref: str,
        intent_id: str,
        risk_intent_id: str,
        payload_sha256: str,
        canonical_payload: Mapping[str, Any],
        wire_scope: Mapping[str, Any],
        exchange_name: str,
        provider_account_id: str,
        client_order_id: str,
        order_request: Mapping[str, Any],
        order_request_sha256: str,
    ) -> tuple[RiskOrderCommand, bool]:
        """Persist an idempotency key before Risk or provider side effects."""

        self._assert_current(fence)
        _require_sha256(payload_sha256, "payload digest")
        _require_sha256(order_request_sha256, "provider request digest")
        values = (
            principal_ref,
            intent_id,
            risk_intent_id,
            exchange_name,
            provider_account_id,
            client_order_id,
        )
        if any(type(value) is not str or not value or value != value.strip() for value in values):
            raise RiskOrderJournalError("INVALID_COMMAND_IDENTITY")
        canonical_payload_json = canonical_json(dict(canonical_payload))
        wire_scope_json = canonical_json(dict(wire_scope))
        order_request_json = canonical_json(dict(order_request))
        scope_key = fence.scope.key
        with self._transaction() as connection:
            self._assert_current_in(connection, fence)
            row = connection.execute(
                "SELECT * FROM risk_order_commands WHERE scope_key=? AND principal_ref=? AND intent_id=?",
                (scope_key, principal_ref, intent_id),
            ).fetchone()
            if row is not None:
                command = self._command_from_row(connection, row, fence.scope)
                if (
                    command.payload_sha256 != payload_sha256
                    or command.risk_intent_id != risk_intent_id
                    or command.client_order_id != client_order_id
                    or command.order_request_sha256 != order_request_sha256
                    or not _same_wire_scope_for_idempotency(command.wire_scope, wire_scope)
                ):
                    raise RiskOrderJournalError("INTENT_CONFLICT")
                return command, False

            revision, previous_account, previous_command = self._next_event_identity(
                connection, scope_key, principal_ref, intent_id
            )
            created_at_ns = time.time_ns()
            body = {
                "canonical_payload": json.loads(canonical_payload_json),
                "client_order_id": client_order_id,
                "created_at_ns": created_at_ns,
                "exchange_name": exchange_name,
                "order_request": json.loads(order_request_json),
                "order_request_sha256": order_request_sha256,
                "provider_account_id": provider_account_id,
                "risk_intent_id": risk_intent_id,
                "wire_scope": json.loads(wire_scope_json),
            }
            event = self._event_from_body(
                fence,
                principal_ref,
                intent_id,
                revision,
                "PREPARED",
                {**body, "updated_at_ns": created_at_ns},
                previous_account,
                previous_command,
            )
            connection.execute(
                """INSERT INTO risk_order_commands (
                    scope_key, principal_ref, intent_id, risk_intent_id,
                    payload_sha256, canonical_payload_json, wire_scope_json,
                    exchange_name, provider_account_id, client_order_id,
                    order_request_json, order_request_sha256,
                    created_revision, created_record_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    scope_key,
                    principal_ref,
                    intent_id,
                    risk_intent_id,
                    payload_sha256,
                    canonical_payload_json,
                    wire_scope_json,
                    exchange_name,
                    provider_account_id,
                    client_order_id,
                    order_request_json,
                    order_request_sha256,
                    revision,
                    event.record_sha256,
                ),
            )
            self._insert_event(connection, fence, event)
            row = connection.execute(
                "SELECT * FROM risk_order_commands WHERE scope_key=? AND principal_ref=? AND intent_id=?",
                (scope_key, principal_ref, intent_id),
            ).fetchone()
            assert row is not None
            return self._command_from_row(connection, row, fence.scope), True

    def append_event(
        self,
        fence: RiskOrderWriterFence,
        *,
        principal_ref: str,
        intent_id: str,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> RiskOrderJournalEvent:
        """Append one immutable event after validating the current owner token."""

        self._assert_current(fence)
        if type(event_type) is not str or event_type not in {
            "RESERVED",
            "CLAIMED",
            "DISPATCHING",
            "ORDER_OBSERVATION",
            "FILL_OBSERVATION",
            "PROOF_READY",
            "ACKED_TRACKED",
            "UNKNOWN",
            "ADMISSION_DENIED",
            "NO_DISPATCH",
            "PROVIDER_QUERY",
        }:
            raise RiskOrderJournalError("INVALID_EVENT_TYPE")
        if not isinstance(payload, Mapping):
            raise RiskOrderJournalError("INVALID_EVENT_PAYLOAD")
        payload_json = canonical_json({**dict(payload), "updated_at_ns": time.time_ns()})
        with self._transaction() as connection:
            self._assert_current_in(connection, fence)
            self._require_command(connection, fence.scope.key, principal_ref, intent_id)
            current = connection.execute(
                "SELECT event_type FROM risk_order_events WHERE scope_key=? AND principal_ref=? AND intent_id=? ORDER BY revision DESC LIMIT 1",
                (fence.scope.key, principal_ref, intent_id),
            ).fetchone()
            if current is None:
                raise RiskOrderJournalError("COMMAND_EVENT_MISSING")
            self._validate_transition(str(current["event_type"]), event_type)
            revision, previous_account, previous_command = self._next_event_identity(
                connection, fence.scope.key, principal_ref, intent_id
            )
            event = self._event_from_body(
                fence,
                principal_ref,
                intent_id,
                revision,
                event_type,
                json.loads(payload_json),
                previous_account,
                previous_command,
            )
            self._insert_event(connection, fence, event)
            return event

    def store_exposure_reference(
        self,
        fence: RiskOrderWriterFence,
        *,
        principal_ref: str,
        intent_id: str,
        claim: DispatchClaimBinding,
        evidence_class: DispatchEvidenceClass,
        permit_id: str,
        provider_order_id: str,
        accepted_request_sha256: str,
        reconciliation_evidence_sha256: str,
        observation_revision: int,
        observation_record_sha256: str,
    ) -> tuple[str, str, Mapping[str, Any]]:
        """Store a durable per-order Risk exposure reference, not an exposure ledger."""

        self._assert_current(fence)
        if type(claim) is not DispatchClaimBinding:
            raise RiskOrderJournalError("DISPATCH_CLAIM_REQUIRED")
        if type(evidence_class) is not DispatchEvidenceClass:
            raise RiskOrderJournalError("DISPATCH_EVIDENCE_CLASS_REQUIRED")
        for value, name in (
            (permit_id, "permit id"),
            (provider_order_id, "provider order id"),
        ):
            if type(value) is not str or not value or value != value.strip():
                raise RiskOrderJournalError("INVALID_" + name.upper().replace(" ", "_"))
        _require_sha256(accepted_request_sha256, "accepted request digest")
        _require_sha256(reconciliation_evidence_sha256, "reconciliation digest")
        _require_sha256(observation_record_sha256, "observation record digest")
        if type(observation_revision) is not int or observation_revision <= 0:
            raise RiskOrderJournalError("INVALID_OBSERVATION_REVISION")
        command = self.get_command(fence.scope, principal_ref, intent_id)
        if (
            command is None
            or command.state != "FILL_OBSERVATION"
            or command.risk_intent_id != claim.intent_id
            or claim.scope != fence.scope
            or claim.permit_id != permit_id
        ):
            raise RiskOrderJournalError("COMPLETE_OBSERVATION_REQUIRED")
        reservation_id = (
            "risk-order-exposure:"
            + sha256_text(
                canonical_json(
                    {
                        "intent_id": command.risk_intent_id,
                        "permit_id": permit_id,
                        "provider_order_id": provider_order_id,
                        "scope_key": fence.scope.key,
                    }
                )
            )[:32]
        )
        with self._connection() as connection:
            order_row = connection.execute(
                "SELECT revision, record_sha256 FROM risk_order_events "
                "WHERE scope_key=? AND principal_ref=? AND intent_id=? "
                "AND event_type='ORDER_OBSERVATION' AND revision<? ORDER BY revision DESC LIMIT 1",
                (fence.scope.key, principal_ref, intent_id, command.latest_revision),
            ).fetchone()
            fill_row = connection.execute(
                "SELECT revision, record_sha256 FROM risk_order_events "
                "WHERE scope_key=? AND principal_ref=? AND intent_id=? AND revision=? "
                "AND event_type='FILL_OBSERVATION'",
                (fence.scope.key, principal_ref, intent_id, command.latest_revision),
            ).fetchone()
        if order_row is None or fill_row is None:
            raise RiskOrderJournalError("COMPLETE_OBSERVATION_REQUIRED")
        reference = {
            "accepted_request_sha256": accepted_request_sha256,
            "claim": _claim_payload(claim),
            "claim_digest": claim.claim_digest,
            "dispatch_attempt_count": 1,
            "evidence_class": evidence_class.value,
            "filled_quantity": 0,
            "intent_id": command.risk_intent_id,
            "intent_hash": claim.intent_hash,
            "order_observation_record_sha256": str(order_row["record_sha256"]),
            "order_observation_revision": int(order_row["revision"]),
            "observation_record_sha256": observation_record_sha256,
            "observation_revision": observation_revision,
            "permit_id": permit_id,
            "provider_order_id": provider_order_id,
            "reconciliation_evidence_sha256": reconciliation_evidence_sha256,
            "schema": "bt-api-risk-order-exposure-reference-v1",
            "scope_key": fence.scope.key,
            "trade_count": 0,
            "fill_observation_record_sha256": str(fill_row["record_sha256"]),
            "fill_observation_revision": int(fill_row["revision"]),
            "writer_fence_sha256": fence.fence_sha256,
        }
        reference_json = canonical_json(reference)
        reference_sha256 = sha256_text(reference_json)
        with self._transaction() as connection:
            self._assert_current_in(connection, fence)
            self._require_command(connection, fence.scope.key, principal_ref, intent_id)
            prior = connection.execute(
                "SELECT * FROM risk_order_exposure_references WHERE scope_key=? AND principal_ref=? AND intent_id=?",
                (fence.scope.key, principal_ref, intent_id),
            ).fetchone()
            if prior is not None:
                if prior["reference_sha256"] != reference_sha256:
                    raise RiskOrderJournalError("EXPOSURE_REFERENCE_CONFLICT")
                return reservation_id, reference_sha256, reference
            revision, previous_account, previous_command = self._next_event_identity(
                connection, fence.scope.key, principal_ref, intent_id
            )
            event = self._event_from_body(
                fence,
                principal_ref,
                intent_id,
                revision,
                "PROOF_READY",
                {
                    "accepted_request_sha256": accepted_request_sha256,
                    "cause_id": claim.cause_id,
                    "claim": _claim_payload(claim),
                    "claim_digest": claim.claim_digest,
                    "dispatch_attempt_count": 1,
                    "evidence_class": evidence_class.value,
                    "exposure_reference": reference,
                    "exposure_reference_id": reservation_id,
                    "exposure_reference_sha256": reference_sha256,
                    "filled_quantity": 0,
                    "intent_hash": claim.intent_hash,
                    "intent_id": claim.intent_id,
                    "permit_id": permit_id,
                    "provider_order_id": provider_order_id,
                    "reconciliation_evidence_sha256": reconciliation_evidence_sha256,
                    "observation_record_sha256": observation_record_sha256,
                    "observation_revision": observation_revision,
                    "risk_intent_id": command.risk_intent_id,
                    "trade_count": 0,
                    "writer_fence_sha256": fence.fence_sha256,
                    "updated_at_ns": time.time_ns(),
                },
                previous_account,
                previous_command,
            )
            connection.execute(
                """INSERT INTO risk_order_exposure_references (
                    scope_key, principal_ref, intent_id, reservation_id,
                    reference_sha256, reference_json, created_revision, record_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    fence.scope.key,
                    principal_ref,
                    intent_id,
                    reservation_id,
                    reference_sha256,
                    reference_json,
                    revision,
                    event.record_sha256,
                ),
            )
            self._insert_event(connection, fence, event)
        return reservation_id, reference_sha256, reference

    def get_command(
        self, scope: AccountScope, principal_ref: str, intent_id: str
    ) -> RiskOrderCommand | None:
        """Read one exact principal/account command; never accepts provider IDs."""

        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM risk_order_commands WHERE scope_key=? AND principal_ref=? AND intent_id=?",
                (scope.key, principal_ref, intent_id),
            ).fetchone()
            return None if row is None else self._command_from_row(connection, row, scope)

    def has_proof_ready_history(
        self,
        fence: RiskOrderWriterFence,
        *,
        principal_ref: str,
        intent_id: str,
    ) -> bool:
        """Fail closed from provider reads when an immutable proof may exist."""

        self._assert_current(fence)
        with self._connection() as connection:
            self._assert_current_in(connection, fence)
            row = connection.execute(
                "SELECT 1 FROM risk_order_events WHERE scope_key=? AND principal_ref=? "
                "AND intent_id=? AND event_type='PROOF_READY' LIMIT 1",
                (fence.scope.key, principal_ref, intent_id),
            ).fetchone()
            return row is not None

    def has_resolved_ack_history(
        self,
        fence: RiskOrderWriterFence,
        *,
        principal_ref: str,
        intent_id: str,
        receipt: DispatchResolutionReceipt,
    ) -> bool:
        """Verify an ACK event against Risk's exact resolved proof receipt.

        This check is separate from proof recovery: provider-query events after
        ACK are valid read history, but they must never be admitted into the
        immutable proof-recovery loader.
        """

        self._assert_current(fence)
        if (
            type(receipt) is not DispatchResolutionReceipt
            or receipt.status is not DispatchResolutionReceiptStatus.ACKED_TRACKED_RESOLVED
            or receipt.dispatch_latch_active is not False
            or type(receipt.claim) is not DispatchClaimBinding
            or type(receipt.proof) is not DispatchTrackedOrderProof
            or receipt.proof_sha256 != receipt.proof.fingerprint
        ):
            return False
        claim = receipt.claim
        proof = receipt.proof
        if (
            type(principal_ref) is not str
            or not principal_ref
            or type(intent_id) is not str
            or not intent_id
            or claim.scope != fence.scope
            or proof.scope != fence.scope
            or proof.permit_id != claim.permit_id
            or proof.intent_id != claim.intent_id
            or proof.intent_hash != claim.intent_hash
            or proof.cause_id != claim.cause_id
            or proof.claim_digest != claim.claim_digest
            or proof.dispatch_attempt_count != 1
            or proof.filled_quantity != 0
            or proof.trade_count != 0
        ):
            return False

        with self._connection() as connection:
            self._assert_current_in(connection, fence)
            command_row = connection.execute(
                "SELECT * FROM risk_order_commands WHERE scope_key=? AND principal_ref=? AND intent_id=?",
                (fence.scope.key, principal_ref, intent_id),
            ).fetchone()
            if command_row is None:
                return False
            command_events_rows = connection.execute(
                "SELECT * FROM risk_order_events WHERE scope_key=? AND principal_ref=? AND intent_id=? ORDER BY revision",
                (fence.scope.key, principal_ref, intent_id),
            ).fetchall()
            if not command_events_rows:
                return False
            head = connection.execute(
                "SELECT revision FROM risk_order_events WHERE scope_key=? ORDER BY revision DESC LIMIT 1",
                (fence.scope.key,),
            ).fetchone()
            if head is None or not self._verify_event_chains(
                connection,
                command_row,
                fence.scope,
                int(head["revision"]),
                target_revision=proof.journal_revision,
            ):
                return False

            command = self._command_from_row(connection, command_row, fence.scope)
            if (
                command.state not in {"ACKED_TRACKED", "PROVIDER_QUERY", "UNKNOWN"}
                or command.risk_intent_id != claim.intent_id
                or command.permit_id != claim.permit_id
                or command.order_request_sha256 != proof.accepted_request_sha256
            ):
                return False
            events = tuple(self._event_from_row(row, fence.scope) for row in command_events_rows)
            by_type: dict[str, list[RiskOrderJournalEvent]] = {}
            for event in events:
                by_type.setdefault(event.event_type, []).append(event)
            reserved = by_type.get("RESERVED", [])
            claimed = by_type.get("CLAIMED", [])
            dispatching = by_type.get("DISPATCHING", [])
            proof_ready = by_type.get("PROOF_READY", [])
            acknowledgements = by_type.get("ACKED_TRACKED", [])
            if (
                len(reserved) != 1
                or len(claimed) != 1
                or len(dispatching) != 1
                or len(proof_ready) != 1
                or len(acknowledgements) != 1
            ):
                return False

            reserve_payload = dict(reserved[0].payload)
            claim_payload = dict(claimed[0].payload)
            dispatch_payload = dict(dispatching[0].payload)
            proof_event = proof_ready[0]
            proof_payload = dict(proof_event.payload)
            ack_event = acknowledgements[0]
            ack_payload = dict(ack_event.payload)
            if (
                reserved[0].revision >= claimed[0].revision
                or claimed[0].revision >= dispatching[0].revision
                or dispatching[0].revision >= proof_event.revision
                or proof_event.revision >= ack_event.revision
                or proof_event.revision != proof.journal_revision
                or proof_event.record_sha256 != proof.journal_record_sha256
                or proof_event.writer_fence_sha256 != proof.writer_fence_sha256
                or _fence_digest(
                    fence.scope,
                    proof_event.writer_owner_id,
                    proof_event.writer_fencing_token,
                )
                != proof_event.writer_fence_sha256
                or ack_event.writer_fencing_token < proof_event.writer_fencing_token
                or _fence_digest(
                    fence.scope, ack_event.writer_owner_id, ack_event.writer_fencing_token
                )
                != ack_event.writer_fence_sha256
                or set(reserve_payload) != {"permit_id", "risk_intent_id", "updated_at_ns"}
                or reserve_payload.get("permit_id") != claim.permit_id
                or reserve_payload.get("risk_intent_id") != claim.intent_id
                or type(reserve_payload.get("updated_at_ns")) is not int
                or reserve_payload.get("updated_at_ns") <= 0
                or set(claim_payload) != {"claim", "permit_id", "risk_intent_id", "updated_at_ns"}
                or claim_payload.get("claim") != _claim_payload(claim)
                or claim_payload.get("permit_id") != claim.permit_id
                or claim_payload.get("risk_intent_id") != claim.intent_id
                or type(claim_payload.get("updated_at_ns")) is not int
                or claim_payload.get("updated_at_ns") <= 0
                or set(dispatch_payload)
                != {"dispatch_attempt_count", "permit_id", "risk_intent_id", "updated_at_ns"}
                or dispatch_payload.get("dispatch_attempt_count") != 1
                or dispatch_payload.get("permit_id") != claim.permit_id
                or dispatch_payload.get("risk_intent_id") != claim.intent_id
                or type(dispatch_payload.get("updated_at_ns")) is not int
                or dispatch_payload.get("updated_at_ns") <= 0
            ):
                return False

            expected_proof_fields = {
                "accepted_request_sha256",
                "cause_id",
                "claim",
                "claim_digest",
                "dispatch_attempt_count",
                "evidence_class",
                "exposure_reference",
                "exposure_reference_id",
                "exposure_reference_sha256",
                "filled_quantity",
                "intent_hash",
                "intent_id",
                "observation_record_sha256",
                "observation_revision",
                "permit_id",
                "provider_order_id",
                "reconciliation_evidence_sha256",
                "risk_intent_id",
                "trade_count",
                "updated_at_ns",
                "writer_fence_sha256",
            }
            if (
                set(proof_payload) != expected_proof_fields
                or proof_payload.get("claim") != _claim_payload(claim)
                or proof_payload.get("claim_digest") != claim.claim_digest
                or proof_payload.get("cause_id") != proof.cause_id
                or proof_payload.get("intent_hash") != proof.intent_hash
                or proof_payload.get("intent_id") != proof.intent_id
                or proof_payload.get("risk_intent_id") != command.risk_intent_id
                or proof_payload.get("permit_id") != proof.permit_id
                or proof_payload.get("dispatch_attempt_count") != 1
                or proof_payload.get("evidence_class") != proof.evidence_class.value
                or proof_payload.get("accepted_request_sha256") != proof.accepted_request_sha256
                or proof_payload.get("provider_order_id") != proof.provider_order_id
                or proof_payload.get("exposure_reference_id") != proof.exposure_reservation_id
                or proof_payload.get("exposure_reference_sha256")
                != proof.exposure_reservation_sha256
                or proof_payload.get("reconciliation_evidence_sha256")
                != proof.reconciliation_evidence_sha256
                or proof_payload.get("filled_quantity") != 0
                or proof_payload.get("trade_count") != 0
                or proof_payload.get("writer_fence_sha256") != proof.writer_fence_sha256
                or type(proof_payload.get("updated_at_ns")) is not int
                or proof_payload.get("updated_at_ns") <= 0
            ):
                return False

            exposure = connection.execute(
                "SELECT * FROM risk_order_exposure_references WHERE scope_key=? AND principal_ref=? AND intent_id=?",
                (fence.scope.key, principal_ref, intent_id),
            ).fetchone()
            if exposure is None:
                return False
            reference_json = str(exposure["reference_json"])
            if sha256_text(reference_json) != proof.exposure_reservation_sha256:
                return False
            try:
                reference = json.loads(reference_json)
            except (TypeError, ValueError):
                return False
            expected_reference_fields = {
                "accepted_request_sha256",
                "claim",
                "claim_digest",
                "dispatch_attempt_count",
                "evidence_class",
                "fill_observation_record_sha256",
                "fill_observation_revision",
                "filled_quantity",
                "intent_hash",
                "intent_id",
                "observation_record_sha256",
                "observation_revision",
                "order_observation_record_sha256",
                "order_observation_revision",
                "permit_id",
                "provider_order_id",
                "reconciliation_evidence_sha256",
                "schema",
                "scope_key",
                "trade_count",
                "writer_fence_sha256",
            }
            if (
                type(reference) is not dict
                or set(reference) != expected_reference_fields
                or str(exposure["reservation_id"]) != proof.exposure_reservation_id
                or str(exposure["reference_sha256"]) != proof.exposure_reservation_sha256
                or int(exposure["created_revision"]) != proof.journal_revision
                or str(exposure["record_sha256"]) != proof.journal_record_sha256
                or proof_payload.get("exposure_reference") != reference
                or reference.get("schema") != "bt-api-risk-order-exposure-reference-v1"
                or reference.get("scope_key") != fence.scope.key
                or reference.get("intent_id") != proof.intent_id
                or reference.get("intent_hash") != proof.intent_hash
                or reference.get("permit_id") != proof.permit_id
                or reference.get("claim_digest") != proof.claim_digest
                or reference.get("claim") != _claim_payload(claim)
                or reference.get("accepted_request_sha256") != proof.accepted_request_sha256
                or reference.get("provider_order_id") != proof.provider_order_id
                or reference.get("reconciliation_evidence_sha256")
                != proof.reconciliation_evidence_sha256
                or reference.get("writer_fence_sha256") != proof.writer_fence_sha256
                or reference.get("dispatch_attempt_count") != 1
                or reference.get("filled_quantity") != 0
                or reference.get("trade_count") != 0
            ):
                return False
            if reference.get("observation_revision") != proof_payload.get(
                "observation_revision"
            ) or reference.get("observation_record_sha256") != proof_payload.get(
                "observation_record_sha256"
            ):
                return False

            order_revision = reference.get("order_observation_revision")
            fill_revision = reference.get("fill_observation_revision")
            if (
                type(order_revision) is not int
                or order_revision <= 0
                or type(fill_revision) is not int
                or fill_revision <= order_revision
                or fill_revision >= proof_event.revision
            ):
                return False
            order_row = connection.execute(
                "SELECT event_type, record_sha256 FROM risk_order_events "
                "WHERE scope_key=? AND principal_ref=? AND intent_id=? AND revision=?",
                (fence.scope.key, principal_ref, intent_id, order_revision),
            ).fetchone()
            fill_row = connection.execute(
                "SELECT event_type, record_sha256 FROM risk_order_events "
                "WHERE scope_key=? AND principal_ref=? AND intent_id=? AND revision=?",
                (fence.scope.key, principal_ref, intent_id, fill_revision),
            ).fetchone()
            if (
                order_row is None
                or fill_row is None
                or order_row["event_type"] != "ORDER_OBSERVATION"
                or fill_row["event_type"] != "FILL_OBSERVATION"
                or order_row["record_sha256"] != reference.get("order_observation_record_sha256")
                or fill_row["record_sha256"] != reference.get("fill_observation_record_sha256")
                or fill_revision != proof_payload.get("observation_revision")
                or fill_row["record_sha256"] != proof_payload.get("observation_record_sha256")
            ):
                return False

            common_ack_fields = {
                "dispatch_attempt_count",
                "exposure_reference_id",
                "exposure_reference_sha256",
                "filled_quantity",
                "permit_id",
                "provider_order_id",
                "public_state",
                "reconciliation_evidence_sha256",
                "risk_intent_id",
                "trade_count",
                "updated_at_ns",
            }
            recovered_ack_fields = common_ack_fields | {
                "claim_digest",
                "proof_ready_record_sha256",
                "proof_ready_revision",
                "proof_sha256",
                "recovery_attestation_sha256",
            }
            if set(ack_payload) not in (common_ack_fields, recovered_ack_fields):
                return False
            if (
                ack_payload.get("dispatch_attempt_count") != 1
                or ack_payload.get("exposure_reference_id") != proof.exposure_reservation_id
                or ack_payload.get("exposure_reference_sha256") != proof.exposure_reservation_sha256
                or ack_payload.get("filled_quantity") != 0
                or ack_payload.get("permit_id") != proof.permit_id
                or ack_payload.get("provider_order_id") != proof.provider_order_id
                or ack_payload.get("public_state") != "ACKED_TRACKED"
                or ack_payload.get("reconciliation_evidence_sha256")
                != proof.reconciliation_evidence_sha256
                or ack_payload.get("risk_intent_id") != proof.intent_id
                or ack_payload.get("trade_count") != 0
            ):
                return False
            if set(ack_payload) == recovered_ack_fields and (
                ack_payload.get("claim_digest") != claim.claim_digest
                or ack_payload.get("proof_ready_record_sha256") != proof.journal_record_sha256
                or ack_payload.get("proof_ready_revision") != proof.journal_revision
                or ack_payload.get("proof_sha256") != proof.fingerprint
                or ack_payload.get("recovery_attestation_sha256")
                != (
                    None
                    if receipt.recovery_attestation is None
                    else receipt.recovery_attestation.fingerprint
                )
            ):
                return False
            if (
                type(ack_payload.get("updated_at_ns")) is not int
                or ack_payload["updated_at_ns"] <= 0
            ):
                return False

            proof_index = events.index(proof_event)
            ack_index = events.index(ack_event)
            if ack_index <= proof_index:
                return False
            for event in events[ack_index + 1 :]:
                payload = dict(event.payload)
                if event.event_type == "UNKNOWN":
                    if (
                        payload.get("permit_id") != proof.permit_id
                        or payload.get("dispatch_attempt_count") != 1
                        or payload.get("public_state") != "UNKNOWN"
                        or type(payload.get("reason_code")) is not str
                    ):
                        return False
                elif event.event_type == "PROVIDER_QUERY":
                    if (
                        payload.get("permit_id") is not None
                        or payload.get("dispatch_attempt_count") != 1
                        or payload.get("public_state") not in {"ACKED_TRACKED", "UNKNOWN"}
                        or payload.get("provider_order_id") != proof.provider_order_id
                        or payload.get("client_order_id") != command.client_order_id
                        or payload.get("account_id") != command.provider_account_id
                        or payload.get("complete") is not True
                        or payload.get("source_alignment_valid") is not True
                        or payload.get("fill_source_alignment_valid") is not True
                        or type(payload.get("updated_at_ns")) is not int
                        or payload.get("updated_at_ns") <= 0
                    ):
                        return False
                    query_digest = payload.get("provider_observation_sha256")
                    digest_payload = dict(payload)
                    digest_payload.pop("provider_observation_sha256", None)
                    digest_payload.pop("dispatch_attempt_count", None)
                    digest_payload.pop("updated_at_ns", None)
                    if query_digest != sha256_text(canonical_json(digest_payload)):
                        return False
                    if payload.get("public_state") == "ACKED_TRACKED":
                        record = payload.get("record")
                        try:
                            fill_is_zero = Decimal(str(payload.get("filled_quantity"))) == 0
                            record_fill_is_zero = (
                                type(record) is dict and Decimal(str(record.get("filled"))) == 0
                            )
                        except (InvalidOperation, ValueError):
                            return False
                        if (
                            payload.get("record_count") != 0
                            or payload.get("trade_count") != 0
                            or payload.get("records") != []
                            or not fill_is_zero
                            or type(record) is not dict
                            or record.get("order_id") != proof.provider_order_id
                            or record.get("client_order_id") != command.client_order_id
                            or record.get("account_id") != command.provider_account_id
                            or record.get("status") != "accepted"
                            or not record_fill_is_zero
                        ):
                            return False
                else:
                    return False
            latest = events[-1]
            if command.state == "PROVIDER_QUERY":
                return latest.event_type == "PROVIDER_QUERY" and latest.payload.get(
                    "public_state"
                ) in {"ACKED_TRACKED", "UNKNOWN"}
            if command.state == "UNKNOWN":
                return latest.event_type == "UNKNOWN"
            return command.state == "ACKED_TRACKED" and latest.event_type == "ACKED_TRACKED"

    def load_dispatch_proof_for_recovery(
        self,
        fence: RiskOrderWriterFence,
        *,
        principal_ref: str,
        intent_id: str,
        claim: DispatchClaimBinding,
    ) -> DispatchProofRecoverySource | None:
        """Verify immutable proof, observations, and exposure source rows.

        The read is scoped by the authenticated principal and logical intent.
        It never accepts a provider/client order ID as lookup authority.
        """

        self._assert_current(fence)
        if type(claim) is not DispatchClaimBinding or claim.scope != fence.scope:
            raise RiskOrderJournalError("RECOVERY_CLAIM_SCOPE_INVALID")
        with self._connection() as connection:
            self._assert_current_in(connection, fence)
            return self._load_dispatch_proof_for_recovery_in(
                connection,
                fence,
                principal_ref=principal_ref,
                intent_id=intent_id,
                claim=claim,
            )

    def append_proof_recovery_attestation(
        self,
        fence: RiskOrderWriterFence,
        source: DispatchProofRecoverySource,
    ) -> DispatchRecoveryAttestation:
        """Append a current-fence attestation without rebinding original rows."""

        self._assert_current(fence)
        if type(source) is not DispatchProofRecoverySource:
            raise RiskOrderJournalError("RECOVERY_SOURCE_REQUIRED")
        if source.proof.writer_fence_sha256 == fence.fence_sha256:
            raise RiskOrderJournalError("RECOVERY_NEW_FENCE_REQUIRED")
        with self._transaction() as connection:
            self._assert_current_in(connection, fence)
            current = self._load_dispatch_proof_for_recovery_in(
                connection,
                fence,
                principal_ref=source.command.principal_ref,
                intent_id=source.command.intent_id,
                claim=source.claim,
            )
            if current is None or not _same_recovery_source_snapshot(source, current):
                raise RiskOrderJournalError("RECOVERY_SOURCE_CHANGED")
            revision, previous_account, previous_command = self._next_event_identity(
                connection,
                fence.scope.key,
                source.command.principal_ref,
                source.command.intent_id,
            )
            recovery_event_id = sha256_text(
                canonical_json(
                    [
                        fence.scope.key,
                        source.command.principal_ref,
                        source.command.intent_id,
                        revision,
                    ]
                )
            )
            payload = _recovery_event_payload(
                source,
                fence,
                recovery_event_id=recovery_event_id,
                recovery_event_revision=revision,
            )
            payload["updated_at_ns"] = time.time_ns()
            self._validate_transition(source.command.state, "PROOF_RECOVERY_ATTESTED")
            event = self._event_from_body(
                fence,
                source.command.principal_ref,
                source.command.intent_id,
                revision,
                "PROOF_RECOVERY_ATTESTED",
                payload,
                previous_account,
                previous_command,
            )
            self._insert_event(connection, fence, event)
            return _recovery_attestation_from_event(source, event)

    @contextmanager
    def recovered_dispatch_resolution_guard(
        self,
        proof: DispatchTrackedOrderProof,
        attestation: DispatchRecoveryAttestation,
        *,
        claim: DispatchClaimBinding,
    ) -> Iterator[VerifiedDispatchRecoveryResolution | None]:
        """Hold the live account fence while Risk consumes recovered proof."""

        if (
            type(proof) is not DispatchTrackedOrderProof
            or type(attestation) is not DispatchRecoveryAttestation
            or type(claim) is not DispatchClaimBinding
        ):
            yield None
            return
        fence = _thread_fences().get(proof.scope.key)
        if fence is None or fence.scope != proof.scope or claim.scope != proof.scope:
            yield None
            return
        verified = self._attest_recovered_dispatch_resolution(proof, attestation, claim, fence)
        self._assert_current(fence)
        try:
            yield verified
        finally:
            self._assert_current(fence)

    def append_recovered_ack(
        self,
        fence: RiskOrderWriterFence,
        *,
        source: DispatchProofRecoverySource,
        receipt: DispatchResolutionReceipt,
    ) -> RiskOrderCommand:
        """Record ACKED_TRACKED only from the exact durable Risk receipt."""

        self._assert_current(fence)
        if (
            type(source) is not DispatchProofRecoverySource
            or type(receipt) is not DispatchResolutionReceipt
            or receipt.status is not DispatchResolutionReceiptStatus.ACKED_TRACKED_RESOLVED
            or receipt.proof is None
            or receipt.proof_sha256 != source.proof.fingerprint
            or receipt.proof != source.proof
            or receipt.claim != source.claim
            or receipt.dispatch_latch_active is not False
        ):
            raise RiskOrderJournalError("RECOVERY_RECEIPT_MISMATCH")
        if receipt.recovery_attestation is not None and receipt.recovery_attestation not in (
            source.recovery_attestations
        ):
            raise RiskOrderJournalError("RECOVERY_RECEIPT_ATTESTATION_MISMATCH")
        with self._transaction() as connection:
            self._assert_current_in(connection, fence)
            current = self._load_dispatch_proof_for_recovery_in(
                connection,
                fence,
                principal_ref=source.command.principal_ref,
                intent_id=source.command.intent_id,
                claim=source.claim,
            )
            if current is None or not _same_recovery_source_snapshot(source, current):
                raise RiskOrderJournalError("RECOVERY_SOURCE_CHANGED")
            if current.command.state == "UNKNOWN":
                # Only this proof-specific method may finish UNKNOWN. The
                # loader above has verified the unique immutable PROOF_READY
                # chain and the exact settled exposure reference, and the
                # receipt above proves Risk already resolved that same proof.
                # Keep the generic transition map closed so an UNKNOWN event
                # alone can never authorize ACKED_TRACKED.
                latest = current.command.latest_event
                if (
                    latest.get("permit_id") != current.proof.permit_id
                    or latest.get("dispatch_attempt_count") != 1
                    or latest.get("public_state") != "UNKNOWN"
                ):
                    raise RiskOrderJournalError("RECOVERY_UNKNOWN_EVENT_INVALID")
            else:
                self._validate_transition(current.command.state, "ACKED_TRACKED")
            proof = current.proof
            revision, previous_account, previous_command = self._next_event_identity(
                connection,
                fence.scope.key,
                current.command.principal_ref,
                current.command.intent_id,
            )
            event = self._event_from_body(
                fence,
                current.command.principal_ref,
                current.command.intent_id,
                revision,
                "ACKED_TRACKED",
                {
                    "claim_digest": current.claim.claim_digest,
                    "dispatch_attempt_count": 1,
                    "exposure_reference_id": proof.exposure_reservation_id,
                    "exposure_reference_sha256": proof.exposure_reservation_sha256,
                    "filled_quantity": 0,
                    "permit_id": proof.permit_id,
                    "proof_ready_record_sha256": proof.journal_record_sha256,
                    "proof_ready_revision": proof.journal_revision,
                    "proof_sha256": proof.fingerprint,
                    "provider_order_id": proof.provider_order_id,
                    "public_state": "ACKED_TRACKED",
                    "recovery_attestation_sha256": (
                        None
                        if receipt.recovery_attestation is None
                        else receipt.recovery_attestation.fingerprint
                    ),
                    "reconciliation_evidence_sha256": proof.reconciliation_evidence_sha256,
                    "risk_intent_id": proof.intent_id,
                    "trade_count": 0,
                    "updated_at_ns": time.time_ns(),
                },
                previous_account,
                previous_command,
            )
            self._insert_event(connection, fence, event)
            row = self._require_command(
                connection,
                fence.scope.key,
                current.command.principal_ref,
                current.command.intent_id,
            )
            return self._command_from_row(connection, row, fence.scope)

    def _load_dispatch_proof_for_recovery_in(
        self,
        connection: sqlite3.Connection,
        fence: RiskOrderWriterFence,
        *,
        principal_ref: str,
        intent_id: str,
        claim: DispatchClaimBinding,
    ) -> DispatchProofRecoverySource | None:
        return _load_dispatch_proof_for_recovery_in(
            self,
            connection,
            fence,
            principal_ref=principal_ref,
            intent_id=intent_id,
            claim=claim,
        )

    def _attest_recovered_dispatch_resolution(
        self,
        proof: DispatchTrackedOrderProof,
        attestation: DispatchRecoveryAttestation,
        claim: DispatchClaimBinding,
        fence: RiskOrderWriterFence,
    ) -> VerifiedDispatchRecoveryResolution | None:
        try:
            with self._connection() as connection:
                self._assert_current_in(connection, fence)
                command_row = connection.execute(
                    "SELECT * FROM risk_order_commands WHERE scope_key=? AND risk_intent_id=?",
                    (proof.scope.key, proof.intent_id),
                ).fetchone()
                if command_row is None:
                    return None
                source = self._load_dispatch_proof_for_recovery_in(
                    connection,
                    fence,
                    principal_ref=str(command_row["principal_ref"]),
                    intent_id=str(command_row["intent_id"]),
                    claim=claim,
                )
            if source is None or source.proof != proof or source.claim != claim:
                return None
            if not source.recovery_attestations or source.recovery_attestations[-1] != attestation:
                return None
            recovery_event = source.recovery_events[-1]
            if (
                recovery_event.event_type != "PROOF_RECOVERY_ATTESTED"
                or recovery_event.revision != source.command.latest_revision
                or recovery_event.record_sha256 != attestation.recovery_event_sha256
                or recovery_event.writer_owner_id != fence.owner_id
                or recovery_event.writer_fencing_token != fence.fencing_token
                or recovery_event.writer_fence_sha256 != fence.fence_sha256
                or fence.fencing_token <= source.proof_ready_event.writer_fencing_token
            ):
                return None
            return VerifiedDispatchRecoveryResolution(
                scope=proof.scope,
                permit_id=proof.permit_id,
                intent_id=proof.intent_id,
                intent_hash=proof.intent_hash,
                cause_id=proof.cause_id,
                claim_digest=claim.claim_digest,
                proof_sha256=proof.fingerprint,
                attestation_sha256=attestation.fingerprint,
                recovery_event_id=attestation.recovery_event_id,
                recovery_event_revision=attestation.recovery_event_revision,
                recovery_event_sha256=attestation.recovery_event_sha256,
                original_writer_owner_id=attestation.original_writer_owner_id,
                original_writer_fencing_token=attestation.original_writer_fencing_token,
                original_writer_fence_sha256=attestation.original_writer_fence_sha256,
                current_writer_owner_id=fence.owner_id,
                current_writer_fencing_token=fence.fencing_token,
                current_writer_fence_sha256=fence.fence_sha256,
            )
        except (RiskOrderJournalError, sqlite3.Error, KeyError, TypeError, ValueError):
            return None

    def commands_for_scope(self, scope: AccountScope) -> tuple[RiskOrderCommand, ...]:
        """Return immutable command snapshots for startup recovery."""

        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM risk_order_commands WHERE scope_key=? ORDER BY created_revision",
                (scope.key,),
            ).fetchall()
            return tuple(self._command_from_row(connection, row, scope) for row in rows)

    def append_resolution_observations(
        self,
        fence: RiskOrderWriterFence,
        *,
        principal_ref: str,
        intent_id: str,
        order_observation: Mapping[str, Any],
        fill_observation: Mapping[str, Any],
    ) -> tuple[RiskOrderJournalEvent, RiskOrderJournalEvent, str]:
        """Persist the exact normalized order/fill observations before proof."""

        command = self.get_command(fence.scope, principal_ref, intent_id)
        if command is None:
            raise RiskOrderJournalError("COMMAND_NOT_FOUND")
        order_payload = _bounded_observation(order_observation)
        fill_payload = _bounded_observation(fill_observation)
        order_event = self.append_event(
            fence,
            principal_ref=principal_ref,
            intent_id=intent_id,
            event_type="ORDER_OBSERVATION",
            payload=order_payload,
        )
        fill_event = self.append_event(
            fence,
            principal_ref=principal_ref,
            intent_id=intent_id,
            event_type="FILL_OBSERVATION",
            payload=fill_payload,
        )
        evidence_sha256 = sha256_text(
            canonical_json(
                {
                    "fill_event_sha256": fill_event.record_sha256,
                    "fill_revision": fill_event.revision,
                    "order_event_sha256": order_event.record_sha256,
                    "order_revision": order_event.revision,
                    "provider_contract_sha256": order_payload.get("provider_contract_sha256"),
                    "request_sha256": command.order_request_sha256,
                    "schema": "bt-api-risk-order-reconciliation-evidence-v1",
                }
            )
        )
        return order_event, fill_event, evidence_sha256

    @contextmanager
    def dispatch_resolution_guard(
        self,
        proof: DispatchTrackedOrderProof,
        *,
        claim: DispatchClaimBinding,
    ) -> Iterator[VerifiedDispatchResolution | None]:
        """Reread the exact proof chain while the dispatch owner fence is held."""

        if type(proof) is not DispatchTrackedOrderProof or type(claim) is not DispatchClaimBinding:
            yield None
            return
        fence = _thread_fences().get(proof.scope.key)
        if fence is None or fence.scope != proof.scope:
            yield None
            return
        attestation = self._attest_dispatch_resolution(proof, claim, fence)
        self._assert_current(fence)
        try:
            yield attestation
        finally:
            self._assert_current(fence)

    def _attest_dispatch_resolution(
        self,
        proof: DispatchTrackedOrderProof,
        claim: DispatchClaimBinding,
        fence: RiskOrderWriterFence,
    ) -> VerifiedDispatchResolution | None:
        try:
            with self._connection() as connection:
                self._assert_current_in(connection, fence)
                command = connection.execute(
                    "SELECT * FROM risk_order_commands WHERE scope_key=? AND risk_intent_id=?",
                    (proof.scope.key, proof.intent_id),
                ).fetchone()
                if command is None:
                    return None
                row = connection.execute(
                    "SELECT * FROM risk_order_events WHERE scope_key=? AND principal_ref=? AND intent_id=? AND revision=?",
                    (
                        proof.scope.key,
                        command["principal_ref"],
                        command["intent_id"],
                        proof.journal_revision,
                    ),
                ).fetchone()
                if row is None:
                    return None
                event = self._event_from_row(row, proof.scope)
                if not self._verify_event_chains(
                    connection, command, proof.scope, proof.journal_revision
                ):
                    return None
                payload = event.payload
                if (
                    event.event_type != "PROOF_READY"
                    or event.record_sha256 != proof.journal_record_sha256
                    or event.writer_fence_sha256 != fence.fence_sha256
                    or event.writer_owner_id != fence.owner_id
                    or event.writer_fencing_token != fence.fencing_token
                    or payload.get("permit_id") != proof.permit_id
                    or payload.get("risk_intent_id") != proof.intent_id
                    or payload.get("intent_hash") != proof.intent_hash
                    or payload.get("cause_id") != proof.cause_id
                    or payload.get("claim_digest") != proof.claim_digest
                    or payload.get("dispatch_attempt_count") != 1
                    or payload.get("evidence_class") != proof.evidence_class.value
                    or payload.get("accepted_request_sha256") != proof.accepted_request_sha256
                    or payload.get("provider_order_id") != proof.provider_order_id
                    or payload.get("exposure_reference_id") != proof.exposure_reservation_id
                    or payload.get("exposure_reference_sha256") != proof.exposure_reservation_sha256
                    or payload.get("reconciliation_evidence_sha256")
                    != proof.reconciliation_evidence_sha256
                    or payload.get("filled_quantity") != 0
                    or payload.get("trade_count") != 0
                    or payload.get("claim") != _claim_payload(claim)
                    or event.scope != claim.scope
                    or command["risk_intent_id"] != claim.intent_id
                    or command["payload_sha256"] == ""
                    or command["order_request_sha256"] != proof.accepted_request_sha256
                ):
                    return None
                exposure = connection.execute(
                    "SELECT * FROM risk_order_exposure_references WHERE scope_key=? AND principal_ref=? AND intent_id=?",
                    (proof.scope.key, command["principal_ref"], command["intent_id"]),
                ).fetchone()
                if (
                    exposure is None
                    or exposure["reservation_id"] != proof.exposure_reservation_id
                    or exposure["reference_sha256"] != proof.exposure_reservation_sha256
                    or sha256_text(exposure["reference_json"]) != proof.exposure_reservation_sha256
                ):
                    return None
                reference = json.loads(exposure["reference_json"])
                if (
                    reference.get("schema") != "bt-api-risk-order-exposure-reference-v1"
                    or reference.get("scope_key") != proof.scope.key
                    or reference.get("intent_id") != proof.intent_id
                    or reference.get("intent_hash") != proof.intent_hash
                    or reference.get("permit_id") != proof.permit_id
                    or reference.get("claim_digest") != proof.claim_digest
                    or reference.get("accepted_request_sha256") != proof.accepted_request_sha256
                    or reference.get("provider_order_id") != proof.provider_order_id
                    or reference.get("observation_record_sha256")
                    != payload.get("observation_record_sha256")
                    or reference.get("observation_revision") != payload.get("observation_revision")
                    or reference.get("reconciliation_evidence_sha256")
                    != proof.reconciliation_evidence_sha256
                    or reference.get("writer_fence_sha256") != proof.writer_fence_sha256
                    or reference.get("dispatch_attempt_count") != 1
                    or reference.get("filled_quantity") != 0
                    or reference.get("trade_count") != 0
                    or reference.get("claim") != _claim_payload(claim)
                ):
                    return None
                for event_type, revision_key, digest_key in (
                    (
                        "ORDER_OBSERVATION",
                        "order_observation_revision",
                        "order_observation_record_sha256",
                    ),
                    (
                        "FILL_OBSERVATION",
                        "fill_observation_revision",
                        "fill_observation_record_sha256",
                    ),
                ):
                    observation_row = connection.execute(
                        "SELECT event_type, record_sha256 FROM risk_order_events "
                        "WHERE scope_key=? AND principal_ref=? AND intent_id=? AND revision=?",
                        (
                            proof.scope.key,
                            command["principal_ref"],
                            command["intent_id"],
                            reference.get(revision_key),
                        ),
                    ).fetchone()
                    if (
                        observation_row is None
                        or observation_row["event_type"] != event_type
                        or observation_row["record_sha256"] != reference.get(digest_key)
                    ):
                        return None
                if (
                    _fence_digest(fence.scope, fence.owner_id, fence.fencing_token)
                    != proof.writer_fence_sha256
                ):
                    return None
                return VerifiedDispatchResolution(
                    scope=proof.scope,
                    permit_id=proof.permit_id,
                    intent_id=proof.intent_id,
                    intent_hash=proof.intent_hash,
                    claim_digest=proof.claim_digest,
                    proof_sha256=proof.fingerprint,
                    journal_revision=proof.journal_revision,
                    journal_record_sha256=proof.journal_record_sha256,
                    writer_fence_sha256=proof.writer_fence_sha256,
                )
        except (RiskOrderJournalError, sqlite3.Error, KeyError, TypeError, ValueError):
            return None

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS risk_order_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS risk_order_fences (
                    scope_key TEXT PRIMARY KEY,
                    fencing_token INTEGER NOT NULL CHECK(fencing_token > 0)
                );
                CREATE TABLE IF NOT EXISTS risk_order_commands (
                    scope_key TEXT NOT NULL,
                    principal_ref TEXT NOT NULL,
                    intent_id TEXT NOT NULL,
                    risk_intent_id TEXT NOT NULL UNIQUE,
                    payload_sha256 TEXT NOT NULL,
                    canonical_payload_json TEXT NOT NULL,
                    wire_scope_json TEXT NOT NULL,
                    exchange_name TEXT NOT NULL,
                    provider_account_id TEXT NOT NULL,
                    client_order_id TEXT NOT NULL,
                    order_request_json TEXT NOT NULL,
                    order_request_sha256 TEXT NOT NULL,
                    created_revision INTEGER NOT NULL,
                    created_record_sha256 TEXT NOT NULL,
                    PRIMARY KEY(scope_key, principal_ref, intent_id),
                    UNIQUE(scope_key, client_order_id)
                );
                CREATE TABLE IF NOT EXISTS risk_order_events (
                    scope_key TEXT NOT NULL,
                    principal_ref TEXT NOT NULL,
                    intent_id TEXT NOT NULL,
                    revision INTEGER NOT NULL CHECK(revision > 0),
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    previous_account_sha256 TEXT NOT NULL,
                    previous_command_sha256 TEXT NOT NULL,
                    record_sha256 TEXT NOT NULL,
                    writer_owner_id TEXT NOT NULL,
                    writer_fencing_token INTEGER NOT NULL,
                    writer_fence_sha256 TEXT NOT NULL,
                    PRIMARY KEY(scope_key, revision),
                    UNIQUE(scope_key, principal_ref, intent_id, revision),
                    FOREIGN KEY(scope_key, principal_ref, intent_id)
                      REFERENCES risk_order_commands(scope_key, principal_ref, intent_id)
                );
                CREATE TABLE IF NOT EXISTS risk_order_exposure_references (
                    scope_key TEXT NOT NULL,
                    principal_ref TEXT NOT NULL,
                    intent_id TEXT NOT NULL,
                    reservation_id TEXT NOT NULL,
                    reference_sha256 TEXT NOT NULL,
                    reference_json TEXT NOT NULL,
                    created_revision INTEGER NOT NULL,
                    record_sha256 TEXT NOT NULL,
                    PRIMARY KEY(scope_key, principal_ref, intent_id),
                    UNIQUE(scope_key, reservation_id),
                    FOREIGN KEY(scope_key, principal_ref, intent_id)
                      REFERENCES risk_order_commands(scope_key, principal_ref, intent_id)
                );
                CREATE TRIGGER IF NOT EXISTS risk_order_commands_no_update
                BEFORE UPDATE ON risk_order_commands BEGIN
                    SELECT RAISE(ABORT, 'risk_order_commands are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS risk_order_commands_no_delete
                BEFORE DELETE ON risk_order_commands BEGIN
                    SELECT RAISE(ABORT, 'risk_order_commands are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS risk_order_events_no_update
                BEFORE UPDATE ON risk_order_events BEGIN
                    SELECT RAISE(ABORT, 'risk_order_events are append only');
                END;
                CREATE TRIGGER IF NOT EXISTS risk_order_events_no_delete
                BEFORE DELETE ON risk_order_events BEGIN
                    SELECT RAISE(ABORT, 'risk_order_events are append only');
                END;
                CREATE TRIGGER IF NOT EXISTS risk_order_exposure_refs_no_update
                BEFORE UPDATE ON risk_order_exposure_references BEGIN
                    SELECT RAISE(ABORT, 'risk-order exposure references are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS risk_order_exposure_refs_no_delete
                BEFORE DELETE ON risk_order_exposure_references BEGIN
                    SELECT RAISE(ABORT, 'risk-order exposure references are immutable');
                END;
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO risk_order_meta(key, value) VALUES('schema_version', ?)",
                (str(_SCHEMA_VERSION),),
            )
            version = connection.execute(
                "SELECT value FROM risk_order_meta WHERE key='schema_version'"
            ).fetchone()
            if version is None or version[0] != str(_SCHEMA_VERSION):
                raise RiskOrderJournalError("JOURNAL_SCHEMA_MISMATCH")

    def _connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=30.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def _lock_path(self, scope: AccountScope) -> Path:
        digest = sha256_text(scope.key)
        return self._path.with_name(self._path.name + "." + digest + ".writer.lock")

    @staticmethod
    def _open_and_lock(path: Path):
        owner_file = open(path, "a+b")  # noqa: SIM115 -- held for the full writer-fence lifetime.
        owner_file.seek(0, os.SEEK_END)
        if owner_file.tell() == 0:
            owner_file.write(b"\0")
            owner_file.flush()
        owner_file.seek(0)
        if os.name == "nt":
            import msvcrt

            while True:
                try:
                    msvcrt.locking(owner_file.fileno(), msvcrt.LK_NBLCK, 1)
                    return owner_file
                except OSError:
                    time.sleep(0.01)
        else:
            import fcntl

            while True:
                try:
                    fcntl.flock(owner_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return owner_file
                except BlockingIOError:
                    time.sleep(0.01)

    @staticmethod
    def _unlock_and_close(owner_file) -> None:
        try:
            if os.name == "nt":
                import msvcrt

                owner_file.seek(0)
                msvcrt.locking(owner_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(owner_file.fileno(), fcntl.LOCK_UN)
        finally:
            owner_file.close()

    def _next_fencing_token(self, scope: AccountScope) -> int:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT fencing_token FROM risk_order_fences WHERE scope_key=?",
                (scope.key,),
            ).fetchone()
            token = 1 if row is None else int(row[0]) + 1
            connection.execute(
                "INSERT INTO risk_order_fences(scope_key, fencing_token) VALUES(?, ?) "
                "ON CONFLICT(scope_key) DO UPDATE SET fencing_token=excluded.fencing_token",
                (scope.key, token),
            )
            return token

    def _assert_current(self, fence: RiskOrderWriterFence) -> None:
        if type(fence) is not RiskOrderWriterFence:
            raise RiskOrderJournalError("WRITER_FENCE_REQUIRED")
        active = _thread_fences().get(fence.scope.key)
        if active is not fence:
            raise RiskOrderJournalError("WRITER_FENCE_NOT_HELD")
        with self._connection() as connection:
            self._assert_current_in(connection, fence)

    @staticmethod
    def _assert_current_in(connection: sqlite3.Connection, fence: RiskOrderWriterFence) -> None:
        row = connection.execute(
            "SELECT fencing_token FROM risk_order_fences WHERE scope_key=?",
            (fence.scope.key,),
        ).fetchone()
        if row is None or int(row[0]) != fence.fencing_token:
            raise RiskOrderJournalError("WRITER_FENCE_STALE")

    @staticmethod
    def _next_event_identity(
        connection: sqlite3.Connection,
        scope_key: str,
        principal_ref: str,
        intent_id: str,
    ) -> tuple[int, str, str]:
        account = connection.execute(
            "SELECT revision, record_sha256 FROM risk_order_events WHERE scope_key=? ORDER BY revision DESC LIMIT 1",
            (scope_key,),
        ).fetchone()
        command = connection.execute(
            "SELECT record_sha256 FROM risk_order_events WHERE scope_key=? AND principal_ref=? AND intent_id=? ORDER BY revision DESC LIMIT 1",
            (scope_key, principal_ref, intent_id),
        ).fetchone()
        return (
            1 if account is None else int(account["revision"]) + 1,
            "0" * _SHA256_LENGTH if account is None else str(account["record_sha256"]),
            "0" * _SHA256_LENGTH if command is None else str(command["record_sha256"]),
        )

    @staticmethod
    def _event_from_body(
        fence: RiskOrderWriterFence,
        principal_ref: str,
        intent_id: str,
        revision: int,
        event_type: str,
        payload: Mapping[str, Any],
        previous_account: str,
        previous_command: str,
    ) -> RiskOrderJournalEvent:
        payload_json = canonical_json(dict(payload))
        record_material = canonical_json(
            {
                "event_type": event_type,
                "intent_id": intent_id,
                "payload": json.loads(payload_json),
                "previous_account_sha256": previous_account,
                "previous_command_sha256": previous_command,
                "principal_ref": principal_ref,
                "revision": revision,
                "scope_key": fence.scope.key,
                "writer_fence_sha256": fence.fence_sha256,
                "writer_fencing_token": fence.fencing_token,
                "writer_owner_id": fence.owner_id,
            }
        )
        return RiskOrderJournalEvent(
            scope=fence.scope,
            principal_ref=principal_ref,
            intent_id=intent_id,
            revision=revision,
            event_type=event_type,
            payload=json.loads(payload_json),
            previous_account_sha256=previous_account,
            previous_command_sha256=previous_command,
            record_sha256=sha256_text(record_material),
            writer_owner_id=fence.owner_id,
            writer_fencing_token=fence.fencing_token,
            writer_fence_sha256=fence.fence_sha256,
        )

    @staticmethod
    def _insert_event(
        connection: sqlite3.Connection,
        fence: RiskOrderWriterFence,
        event: RiskOrderJournalEvent,
    ) -> None:
        connection.execute(
            """INSERT INTO risk_order_events (
                scope_key, principal_ref, intent_id, revision, event_type,
                payload_json, previous_account_sha256, previous_command_sha256,
                record_sha256, writer_owner_id, writer_fencing_token, writer_fence_sha256
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                fence.scope.key,
                event.principal_ref,
                event.intent_id,
                event.revision,
                event.event_type,
                canonical_json(dict(event.payload)),
                event.previous_account_sha256,
                event.previous_command_sha256,
                event.record_sha256,
                event.writer_owner_id,
                event.writer_fencing_token,
                event.writer_fence_sha256,
            ),
        )

    def _command_from_row(
        self, connection: sqlite3.Connection, row: sqlite3.Row, scope: AccountScope
    ) -> RiskOrderCommand:
        events = connection.execute(
            "SELECT * FROM risk_order_events WHERE scope_key=? AND principal_ref=? AND intent_id=? ORDER BY revision",
            (row["scope_key"], row["principal_ref"], row["intent_id"]),
        ).fetchall()
        if not events:
            raise RiskOrderJournalError("COMMAND_EVENT_MISSING")
        event = self._event_from_row(events[-1], scope)
        initial_payload = json.loads(events[0]["payload_json"])
        permit_id = next(
            (
                payload.get("permit_id")
                for payload in (json.loads(item["payload_json"]) for item in reversed(events))
                if type(payload.get("permit_id")) is str
            ),
            None,
        )
        return RiskOrderCommand(
            scope=scope,
            principal_ref=str(row["principal_ref"]),
            intent_id=str(row["intent_id"]),
            risk_intent_id=str(row["risk_intent_id"]),
            payload_sha256=str(row["payload_sha256"]),
            canonical_payload=json.loads(row["canonical_payload_json"]),
            wire_scope=json.loads(row["wire_scope_json"]),
            exchange_name=str(row["exchange_name"]),
            provider_account_id=str(row["provider_account_id"]),
            client_order_id=str(row["client_order_id"]),
            permit_id=permit_id,
            order_request=json.loads(row["order_request_json"]),
            order_request_sha256=str(row["order_request_sha256"]),
            created_revision=int(row["created_revision"]),
            created_record_sha256=str(row["created_record_sha256"]),
            created_at_ns=int(initial_payload.get("created_at_ns", 0)),
            updated_at_ns=int(event.payload.get("updated_at_ns", 0)),
            state=event.event_type,
            latest_revision=event.revision,
            latest_record_sha256=event.record_sha256,
            latest_event=event.payload,
        )

    def _event_from_row(self, row: sqlite3.Row, scope: AccountScope) -> RiskOrderJournalEvent:
        return RiskOrderJournalEvent(
            scope=scope,
            principal_ref=str(row["principal_ref"]),
            intent_id=str(row["intent_id"]),
            revision=int(row["revision"]),
            event_type=str(row["event_type"]),
            payload=json.loads(row["payload_json"]),
            previous_account_sha256=str(row["previous_account_sha256"]),
            previous_command_sha256=str(row["previous_command_sha256"]),
            record_sha256=str(row["record_sha256"]),
            writer_owner_id=str(row["writer_owner_id"]),
            writer_fencing_token=int(row["writer_fencing_token"]),
            writer_fence_sha256=str(row["writer_fence_sha256"]),
        )

    @staticmethod
    def _require_command(
        connection: sqlite3.Connection, scope_key: str, principal_ref: str, intent_id: str
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM risk_order_commands WHERE scope_key=? AND principal_ref=? AND intent_id=?",
            (scope_key, principal_ref, intent_id),
        ).fetchone()
        if row is None:
            raise RiskOrderJournalError("COMMAND_NOT_FOUND")
        return row

    @staticmethod
    def _validate_transition(current: str, following: str) -> None:
        allowed = {
            "PREPARED": {"RESERVED", "ADMISSION_DENIED", "UNKNOWN"},
            "RESERVED": {"CLAIMED", "NO_DISPATCH", "UNKNOWN"},
            "CLAIMED": {"DISPATCHING", "UNKNOWN"},
            "DISPATCHING": {"ORDER_OBSERVATION", "UNKNOWN"},
            "ORDER_OBSERVATION": {"FILL_OBSERVATION", "UNKNOWN"},
            "FILL_OBSERVATION": {"PROOF_READY", "UNKNOWN", "PROVIDER_QUERY"},
            "PROOF_READY": {"ACKED_TRACKED", "PROOF_RECOVERY_ATTESTED", "UNKNOWN"},
            "PROOF_RECOVERY_ATTESTED": {
                "ACKED_TRACKED",
                "PROOF_RECOVERY_ATTESTED",
                "UNKNOWN",
            },
            "ACKED_TRACKED": {"PROVIDER_QUERY", "UNKNOWN"},
            "PROVIDER_QUERY": {"PROVIDER_QUERY", "UNKNOWN"},
            "UNKNOWN": {
                "ORDER_OBSERVATION",
                "PROOF_RECOVERY_ATTESTED",
                "UNKNOWN",
            },
            "ADMISSION_DENIED": set(),
            "NO_DISPATCH": set(),
        }
        if following not in allowed.get(current, set()):
            raise RiskOrderJournalError("JOURNAL_STATE_CONFLICT")

    @staticmethod
    def _verify_event_chains(
        connection: sqlite3.Connection,
        command_row: sqlite3.Row,
        scope: AccountScope,
        through_revision: int,
        *,
        target_revision: int | None = None,
    ) -> bool:
        if target_revision is None:
            target_revision = through_revision
        events = connection.execute(
            "SELECT * FROM risk_order_events WHERE scope_key=? AND revision<=? ORDER BY revision",
            (command_row["scope_key"], through_revision),
        ).fetchall()
        account_hash = "0" * _SHA256_LENGTH
        command_hashes: dict[tuple[str, str], str] = {}
        expected_revision = 1
        found_target = False
        for row in events:
            revision = int(row["revision"])
            event_key = (str(row["principal_ref"]), str(row["intent_id"]))
            previous_command = command_hashes.get(event_key, "0" * _SHA256_LENGTH)
            if (
                revision != expected_revision
                or row["previous_account_sha256"] != account_hash
                or row["previous_command_sha256"] != previous_command
            ):
                return False
            event = RiskOrderJournalEvent(
                scope=scope,
                principal_ref=event_key[0],
                intent_id=event_key[1],
                revision=revision,
                event_type=str(row["event_type"]),
                payload=json.loads(row["payload_json"]),
                previous_account_sha256=str(row["previous_account_sha256"]),
                previous_command_sha256=str(row["previous_command_sha256"]),
                record_sha256=str(row["record_sha256"]),
                writer_owner_id=str(row["writer_owner_id"]),
                writer_fencing_token=int(row["writer_fencing_token"]),
                writer_fence_sha256=str(row["writer_fence_sha256"]),
            )
            expected = RiskOrderJournal._event_from_body(
                RiskOrderWriterFence(
                    scope,
                    event.writer_owner_id,
                    event.writer_fencing_token,
                    event.writer_fence_sha256,
                ),
                event.principal_ref,
                event.intent_id,
                event.revision,
                event.event_type,
                event.payload,
                event.previous_account_sha256,
                event.previous_command_sha256,
            )
            if expected.record_sha256 != event.record_sha256:
                return False
            account_hash = event.record_sha256
            command_hashes[event_key] = event.record_sha256
            if event.revision == target_revision and event_key == (
                command_row["principal_ref"],
                command_row["intent_id"],
            ):
                found_target = True
            expected_revision += 1
        return found_target and expected_revision == through_revision + 1


def _claim_payload(claim: DispatchClaimBinding) -> dict[str, Any]:
    return {
        "cause_id": claim.cause_id,
        "claim_digest": claim.claim_digest,
        "claimed_at": claim.claimed_at,
        "intent_hash": claim.intent_hash,
        "intent_id": claim.intent_id,
        "permit_id": claim.permit_id,
        "scope_key": claim.scope.key,
    }


def _load_dispatch_proof_for_recovery_in(
    journal: RiskOrderJournal,
    connection: sqlite3.Connection,
    fence: RiskOrderWriterFence,
    *,
    principal_ref: str,
    intent_id: str,
    claim: DispatchClaimBinding,
) -> DispatchProofRecoverySource | None:
    if (
        type(principal_ref) is not str
        or not principal_ref
        or type(intent_id) is not str
        or not intent_id
        or type(claim) is not DispatchClaimBinding
        or claim.scope != fence.scope
    ):
        raise RiskOrderJournalError("RECOVERY_IDENTITY_INVALID")
    command_row = connection.execute(
        "SELECT * FROM risk_order_commands WHERE scope_key=? AND principal_ref=? AND intent_id=?",
        (fence.scope.key, principal_ref, intent_id),
    ).fetchone()
    if command_row is None:
        return None
    command_event_rows = connection.execute(
        "SELECT * FROM risk_order_events WHERE scope_key=? AND principal_ref=? AND intent_id=? "
        "ORDER BY revision",
        (fence.scope.key, principal_ref, intent_id),
    ).fetchall()
    if not command_event_rows:
        raise RiskOrderJournalError("RECOVERY_EVENT_CHAIN_INVALID")
    command_events = tuple(journal._event_from_row(row, fence.scope) for row in command_event_rows)
    proof_events = tuple(event for event in command_events if event.event_type == "PROOF_READY")
    if not proof_events:
        return None
    if len(proof_events) != 1:
        raise RiskOrderJournalError("RECOVERY_PROOF_READY_AMBIGUOUS")
    proof_ready_event = proof_events[0]
    head = connection.execute(
        "SELECT revision FROM risk_order_events WHERE scope_key=? ORDER BY revision DESC LIMIT 1",
        (fence.scope.key,),
    ).fetchone()
    if head is None or not journal._verify_event_chains(
        connection,
        command_row,
        fence.scope,
        int(head["revision"]),
        target_revision=proof_ready_event.revision,
    ):
        raise RiskOrderJournalError("RECOVERY_EVENT_CHAIN_INVALID")
    command = journal._command_from_row(connection, command_row, fence.scope)
    if command.state not in {"PROOF_READY", "PROOF_RECOVERY_ATTESTED", "UNKNOWN"}:
        raise RiskOrderJournalError("RECOVERY_COMMAND_STATE_INVALID")
    if command.risk_intent_id != claim.intent_id or command.permit_id != claim.permit_id:
        raise RiskOrderJournalError("RECOVERY_CLAIM_COMMAND_MISMATCH")
    if command.created_revision != command_events[0].revision or (
        command.created_record_sha256 != command_events[0].record_sha256
    ):
        raise RiskOrderJournalError("RECOVERY_COMMAND_CREATION_MISMATCH")

    initial = command_events[0]
    initial_payload = dict(initial.payload)
    if (
        initial.event_type != "PREPARED"
        or initial.revision != command.created_revision
        or initial.record_sha256 != command.created_record_sha256
        or initial_payload.get("risk_intent_id") != command.risk_intent_id
        or initial_payload.get("canonical_payload") != dict(command.canonical_payload)
        or initial_payload.get("wire_scope") != dict(command.wire_scope)
        or initial_payload.get("exchange_name") != command.exchange_name
        or initial_payload.get("provider_account_id") != command.provider_account_id
        or initial_payload.get("client_order_id") != command.client_order_id
        or initial_payload.get("order_request") != dict(command.order_request)
        or initial_payload.get("order_request_sha256") != command.order_request_sha256
        or sha256_text(canonical_json(dict(command.canonical_payload))) != command.payload_sha256
        or sha256_text(
            canonical_json(
                {
                    "exchange_name": command.exchange_name,
                    "operation": "BtApi.make_order(normalized=True)",
                    "request": dict(command.order_request),
                }
            )
        )
        != command.order_request_sha256
    ):
        raise RiskOrderJournalError("RECOVERY_COMMAND_CREATION_MISMATCH")

    by_type: dict[str, list[RiskOrderJournalEvent]] = {}
    for event in command_events:
        by_type.setdefault(event.event_type, []).append(event)
    reserved = by_type.get("RESERVED", [])
    claimed = by_type.get("CLAIMED", [])
    dispatching = by_type.get("DISPATCHING", [])
    if len(reserved) != 1 or len(claimed) != 1 or len(dispatching) != 1:
        raise RiskOrderJournalError("RECOVERY_DISPATCH_HISTORY_INVALID")
    claimed_event = claimed[0]
    dispatch_event = dispatching[0]
    if (
        reserved[0].revision >= claimed_event.revision
        or claimed_event.revision >= dispatch_event.revision
        or dispatch_event.revision >= proof_ready_event.revision
        or reserved[0].payload.get("permit_id") != claim.permit_id
        or reserved[0].payload.get("risk_intent_id") != claim.intent_id
        or claimed_event.payload.get("permit_id") != claim.permit_id
        or claimed_event.payload.get("risk_intent_id") != claim.intent_id
        or claimed_event.payload.get("claim") != _claim_payload(claim)
        or dispatch_event.payload.get("permit_id") != claim.permit_id
        or dispatch_event.payload.get("risk_intent_id") != claim.intent_id
        or dispatch_event.payload.get("dispatch_attempt_count") != 1
    ):
        raise RiskOrderJournalError("RECOVERY_DISPATCH_HISTORY_INVALID")

    proof_payload = dict(proof_ready_event.payload)
    expected_proof_keys = {
        "accepted_request_sha256",
        "cause_id",
        "claim",
        "claim_digest",
        "dispatch_attempt_count",
        "evidence_class",
        "exposure_reference",
        "exposure_reference_id",
        "exposure_reference_sha256",
        "filled_quantity",
        "intent_hash",
        "intent_id",
        "observation_record_sha256",
        "observation_revision",
        "permit_id",
        "provider_order_id",
        "reconciliation_evidence_sha256",
        "risk_intent_id",
        "trade_count",
        "updated_at_ns",
        "writer_fence_sha256",
    }
    if set(proof_payload) != expected_proof_keys:
        raise RiskOrderJournalError("RECOVERY_PROOF_FIELDS_INVALID")
    if (
        proof_payload.get("claim") != _claim_payload(claim)
        or proof_payload.get("claim_digest") != claim.claim_digest
        or proof_payload.get("intent_hash") != claim.intent_hash
        or proof_payload.get("cause_id") != claim.cause_id
        or proof_payload.get("intent_id") != claim.intent_id
        or proof_payload.get("risk_intent_id") != command.risk_intent_id
        or proof_payload.get("permit_id") != claim.permit_id
        or proof_payload.get("dispatch_attempt_count") != 1
        or proof_payload.get("accepted_request_sha256") != command.order_request_sha256
        or proof_payload.get("filled_quantity") != 0
        or proof_payload.get("trade_count") != 0
        or proof_payload.get("writer_fence_sha256") != proof_ready_event.writer_fence_sha256
        or proof_ready_event.writer_fencing_token >= fence.fencing_token
        or proof_ready_event.writer_fence_sha256 == fence.fence_sha256
        or _fence_digest(
            fence.scope,
            proof_ready_event.writer_owner_id,
            proof_ready_event.writer_fencing_token,
        )
        != proof_ready_event.writer_fence_sha256
    ):
        raise RiskOrderJournalError("RECOVERY_PROOF_BINDING_INVALID")
    try:
        evidence_class = DispatchEvidenceClass(proof_payload["evidence_class"])
        proof = DispatchTrackedOrderProof(
            scope=fence.scope,
            permit_id=str(proof_payload["permit_id"]),
            intent_id=str(proof_payload["risk_intent_id"]),
            intent_hash=str(proof_payload["intent_hash"]),
            cause_id=str(proof_payload["cause_id"]),
            claim_digest=str(proof_payload["claim_digest"]),
            evidence_class=evidence_class,
            dispatch_attempt_count=proof_payload["dispatch_attempt_count"],
            journal_revision=proof_ready_event.revision,
            journal_record_sha256=proof_ready_event.record_sha256,
            reconciliation_evidence_sha256=str(proof_payload["reconciliation_evidence_sha256"]),
            writer_fence_sha256=proof_ready_event.writer_fence_sha256,
            provider_order_id=str(proof_payload["provider_order_id"]),
            accepted_request_sha256=str(proof_payload["accepted_request_sha256"]),
            exposure_reservation_id=str(proof_payload["exposure_reference_id"]),
            exposure_reservation_sha256=str(proof_payload["exposure_reference_sha256"]),
            filled_quantity=proof_payload["filled_quantity"],
            trade_count=proof_payload["trade_count"],
        )
    except (KeyError, TypeError, ValueError):
        raise RiskOrderJournalError("RECOVERY_PROOF_BINDING_INVALID") from None
    if proof.scope != claim.scope or proof.intent_id != claim.intent_id:
        raise RiskOrderJournalError("RECOVERY_PROOF_CLAIM_MISMATCH")

    exposure_row = connection.execute(
        "SELECT * FROM risk_order_exposure_references WHERE scope_key=? AND principal_ref=? AND intent_id=?",
        (fence.scope.key, principal_ref, intent_id),
    ).fetchone()
    if exposure_row is None:
        raise RiskOrderJournalError("RECOVERY_EXPOSURE_REFERENCE_MISSING")
    try:
        reference = json.loads(exposure_row["reference_json"])
    except (TypeError, ValueError):
        raise RiskOrderJournalError("RECOVERY_EXPOSURE_REFERENCE_INVALID") from None
    if type(reference) is not dict:
        raise RiskOrderJournalError("RECOVERY_EXPOSURE_REFERENCE_INVALID")
    reference_sha = sha256_text(str(exposure_row["reference_json"]))
    expected_exposure_id = (
        "risk-order-exposure:"
        + sha256_text(
            canonical_json(
                {
                    "intent_id": command.risk_intent_id,
                    "permit_id": claim.permit_id,
                    "provider_order_id": proof.provider_order_id,
                    "scope_key": fence.scope.key,
                }
            )
        )[:32]
    )
    if (
        str(exposure_row["reservation_id"]) != proof.exposure_reservation_id
        or proof.exposure_reservation_id != expected_exposure_id
        or str(exposure_row["reference_sha256"]) != reference_sha
        or reference_sha != proof.exposure_reservation_sha256
        or int(exposure_row["created_revision"]) != proof_ready_event.revision
        or str(exposure_row["record_sha256"]) != proof_ready_event.record_sha256
        or proof_payload.get("exposure_reference") != reference
        or reference.get("schema") != "bt-api-risk-order-exposure-reference-v1"
        or reference.get("scope_key") != fence.scope.key
        or reference.get("intent_id") != proof.intent_id
        or reference.get("intent_hash") != proof.intent_hash
        or reference.get("permit_id") != proof.permit_id
        or reference.get("claim_digest") != proof.claim_digest
        or reference.get("claim") != _claim_payload(claim)
        or reference.get("accepted_request_sha256") != proof.accepted_request_sha256
        or reference.get("provider_order_id") != proof.provider_order_id
        or reference.get("reconciliation_evidence_sha256") != proof.reconciliation_evidence_sha256
        or reference.get("writer_fence_sha256") != proof.writer_fence_sha256
        or reference.get("dispatch_attempt_count") != 1
        or reference.get("filled_quantity") != 0
        or reference.get("trade_count") != 0
        or reference.get("observation_revision") != proof_payload.get("observation_revision")
        or reference.get("observation_record_sha256")
        != proof_payload.get("observation_record_sha256")
    ):
        raise RiskOrderJournalError("RECOVERY_EXPOSURE_REFERENCE_INVALID")
    if (
        type(reference.get("order_observation_revision")) is not int
        or type(reference.get("fill_observation_revision")) is not int
    ):
        raise RiskOrderJournalError("RECOVERY_OBSERVATION_LINK_INVALID")
    order_row = connection.execute(
        "SELECT * FROM risk_order_events WHERE scope_key=? AND principal_ref=? AND intent_id=? "
        "AND revision=?",
        (
            fence.scope.key,
            principal_ref,
            intent_id,
            reference["order_observation_revision"],
        ),
    ).fetchone()
    fill_row = connection.execute(
        "SELECT * FROM risk_order_events WHERE scope_key=? AND principal_ref=? AND intent_id=? "
        "AND revision=?",
        (
            fence.scope.key,
            principal_ref,
            intent_id,
            reference["fill_observation_revision"],
        ),
    ).fetchone()
    if (
        order_row is None
        or fill_row is None
        or str(order_row["event_type"]) != "ORDER_OBSERVATION"
        or str(fill_row["event_type"]) != "FILL_OBSERVATION"
        or int(order_row["revision"]) >= int(fill_row["revision"])
        or int(fill_row["revision"]) >= proof_ready_event.revision
        or str(order_row["record_sha256"]) != reference.get("order_observation_record_sha256")
        or str(fill_row["record_sha256"]) != reference.get("fill_observation_record_sha256")
        or int(fill_row["revision"]) != proof_payload.get("observation_revision")
        or str(fill_row["record_sha256"]) != proof_payload.get("observation_record_sha256")
    ):
        raise RiskOrderJournalError("RECOVERY_OBSERVATION_LINK_INVALID")
    order_event = journal._event_from_row(order_row, fence.scope)
    fill_event = journal._event_from_row(fill_row, fence.scope)
    reconciliation_sha = sha256_text(
        canonical_json(
            {
                "fill_event_sha256": fill_event.record_sha256,
                "fill_revision": fill_event.revision,
                "order_event_sha256": order_event.record_sha256,
                "order_revision": order_event.revision,
                "provider_contract_sha256": order_event.payload.get("provider_contract_sha256"),
                "request_sha256": command.order_request_sha256,
                "schema": "bt-api-risk-order-reconciliation-evidence-v1",
            }
        )
    )
    if (
        reconciliation_sha != proof.reconciliation_evidence_sha256
        or reference.get("order_observation_record_sha256") != order_event.record_sha256
        or reference.get("fill_observation_record_sha256") != fill_event.record_sha256
        or reference.get("observation_revision") != fill_event.revision
        or reference.get("observation_record_sha256") != fill_event.record_sha256
    ):
        raise RiskOrderJournalError("RECOVERY_RECONCILIATION_BINDING_INVALID")

    recovery_events: list[RiskOrderJournalEvent] = []
    proof_index = command_events.index(proof_ready_event)
    post_proof_events = command_events[proof_index + 1 :]
    for event in post_proof_events:
        if event.event_type == "PROOF_RECOVERY_ATTESTED":
            recovery_events.append(event)
        elif event.event_type == "UNKNOWN":
            if (
                event.payload.get("permit_id") != claim.permit_id
                or event.payload.get("dispatch_attempt_count") != 1
                or event.payload.get("public_state") != "UNKNOWN"
            ):
                raise RiskOrderJournalError("RECOVERY_UNKNOWN_EVENT_INVALID")
        else:
            # In particular, a provider query after PROOF_READY invalidates
            # automatic proof recovery. Only the immutable pre-query evidence
            # may be used to clear the latch.
            raise RiskOrderJournalError("RECOVERY_POST_PROOF_EVENT_UNSUPPORTED")
    if command.state == "PROOF_RECOVERY_ATTESTED" and (
        not recovery_events or recovery_events[-1].revision != command.latest_revision
    ):
        raise RiskOrderJournalError("RECOVERY_EVENT_MISSING")
    source = DispatchProofRecoverySource(
        command=command,
        claim=claim,
        proof=proof,
        proof_ready_event=proof_ready_event,
        order_observation_event=order_event,
        fill_observation_event=fill_event,
        exposure_reference=MappingProxyType(reference),
        exposure_reference_id=proof.exposure_reservation_id,
        exposure_reference_sha256=proof.exposure_reservation_sha256,
        recovery_events=tuple(recovery_events),
        recovery_attestations=(),
    )
    recovery_attestations = [
        _recovery_attestation_from_event(source, event) for event in recovery_events
    ]
    return DispatchProofRecoverySource(
        command=source.command,
        claim=source.claim,
        proof=source.proof,
        proof_ready_event=source.proof_ready_event,
        order_observation_event=source.order_observation_event,
        fill_observation_event=source.fill_observation_event,
        exposure_reference=source.exposure_reference,
        exposure_reference_id=source.exposure_reference_id,
        exposure_reference_sha256=source.exposure_reference_sha256,
        recovery_events=source.recovery_events,
        recovery_attestations=tuple(recovery_attestations),
    )


def _recovery_event_payload(
    source: DispatchProofRecoverySource,
    fence: RiskOrderWriterFence,
    *,
    recovery_event_id: str,
    recovery_event_revision: int,
) -> dict[str, Any]:
    proof = source.proof
    original = source.proof_ready_event
    return {
        "accepted_request_sha256": proof.accepted_request_sha256,
        "cause_id": proof.cause_id,
        "claim_digest": proof.claim_digest,
        "client_intent_id": source.command.intent_id,
        "current_writer_fence_sha256": fence.fence_sha256,
        "current_writer_fencing_token": fence.fencing_token,
        "current_writer_owner_id": fence.owner_id,
        "exposure_reservation_id": proof.exposure_reservation_id,
        "exposure_reservation_sha256": proof.exposure_reservation_sha256,
        "fill_observation_record_sha256": source.fill_observation_event.record_sha256,
        "fill_observation_revision": source.fill_observation_event.revision,
        "intent_hash": proof.intent_hash,
        "permit_id": proof.permit_id,
        "principal_ref": source.command.principal_ref,
        "proof_ready_record_sha256": original.record_sha256,
        "proof_ready_revision": original.revision,
        "proof_sha256": proof.fingerprint,
        "provider_order_id": proof.provider_order_id,
        "reconciliation_evidence_sha256": proof.reconciliation_evidence_sha256,
        "recovery_event_id": recovery_event_id,
        "recovery_event_revision": recovery_event_revision,
        "risk_intent_id": proof.intent_id,
        "schema": "bt-api-risk-order-recovery-attestation-v1",
        "scope_key": proof.scope.key,
        "order_observation_record_sha256": source.order_observation_event.record_sha256,
        "order_observation_revision": source.order_observation_event.revision,
        "original_writer_fence_sha256": original.writer_fence_sha256,
        "original_writer_fencing_token": original.writer_fencing_token,
        "original_writer_owner_id": original.writer_owner_id,
    }


def _recovery_attestation_from_event(
    source: DispatchProofRecoverySource,
    event: RiskOrderJournalEvent,
) -> DispatchRecoveryAttestation:
    payload = dict(event.payload)
    expected_keys = {
        "accepted_request_sha256",
        "cause_id",
        "claim_digest",
        "client_intent_id",
        "current_writer_fence_sha256",
        "current_writer_fencing_token",
        "current_writer_owner_id",
        "exposure_reservation_id",
        "exposure_reservation_sha256",
        "fill_observation_record_sha256",
        "fill_observation_revision",
        "intent_hash",
        "order_observation_record_sha256",
        "order_observation_revision",
        "original_writer_fence_sha256",
        "original_writer_fencing_token",
        "original_writer_owner_id",
        "permit_id",
        "principal_ref",
        "proof_ready_record_sha256",
        "proof_ready_revision",
        "proof_sha256",
        "provider_order_id",
        "reconciliation_evidence_sha256",
        "recovery_event_id",
        "recovery_event_revision",
        "risk_intent_id",
        "schema",
        "scope_key",
        "updated_at_ns",
    }
    expected_event_id = sha256_text(
        canonical_json(
            [
                source.proof.scope.key,
                source.command.principal_ref,
                source.command.intent_id,
                event.revision,
            ]
        )
    )
    proof = source.proof
    original = source.proof_ready_event
    if (
        event.event_type != "PROOF_RECOVERY_ATTESTED"
        or set(payload) != expected_keys
        or payload.get("schema") != "bt-api-risk-order-recovery-attestation-v1"
        or payload.get("scope_key") != proof.scope.key
        or payload.get("principal_ref") != source.command.principal_ref
        or payload.get("client_intent_id") != source.command.intent_id
        or payload.get("risk_intent_id") != proof.intent_id
        or payload.get("permit_id") != proof.permit_id
        or payload.get("intent_hash") != proof.intent_hash
        or payload.get("cause_id") != proof.cause_id
        or payload.get("claim_digest") != proof.claim_digest
        or payload.get("proof_sha256") != proof.fingerprint
        or payload.get("proof_ready_revision") != original.revision
        or payload.get("proof_ready_record_sha256") != original.record_sha256
        or payload.get("original_writer_owner_id") != original.writer_owner_id
        or payload.get("original_writer_fencing_token") != original.writer_fencing_token
        or payload.get("original_writer_fence_sha256") != original.writer_fence_sha256
        or payload.get("order_observation_revision") != source.order_observation_event.revision
        or payload.get("order_observation_record_sha256")
        != source.order_observation_event.record_sha256
        or payload.get("fill_observation_revision") != source.fill_observation_event.revision
        or payload.get("fill_observation_record_sha256")
        != source.fill_observation_event.record_sha256
        or payload.get("reconciliation_evidence_sha256") != proof.reconciliation_evidence_sha256
        or payload.get("accepted_request_sha256") != proof.accepted_request_sha256
        or payload.get("provider_order_id") != proof.provider_order_id
        or payload.get("exposure_reservation_id") != proof.exposure_reservation_id
        or payload.get("exposure_reservation_sha256") != proof.exposure_reservation_sha256
        or payload.get("recovery_event_id") != expected_event_id
        or payload.get("recovery_event_revision") != event.revision
        or payload.get("current_writer_owner_id") != event.writer_owner_id
        or payload.get("current_writer_fencing_token") != event.writer_fencing_token
        or payload.get("current_writer_fence_sha256") != event.writer_fence_sha256
        or _fence_digest(proof.scope, event.writer_owner_id, event.writer_fencing_token)
        != event.writer_fence_sha256
        or type(payload.get("updated_at_ns")) is not int
        or payload.get("updated_at_ns") <= 0
        or event.writer_fencing_token <= original.writer_fencing_token
        or event.writer_fence_sha256 == original.writer_fence_sha256
    ):
        raise RiskOrderJournalError("RECOVERY_ATTESTATION_EVENT_INVALID")
    try:
        return DispatchRecoveryAttestation(
            scope=proof.scope,
            permit_id=proof.permit_id,
            intent_id=proof.intent_id,
            intent_hash=proof.intent_hash,
            cause_id=proof.cause_id,
            claim_digest=proof.claim_digest,
            proof_sha256=proof.fingerprint,
            proof_ready_revision=original.revision,
            proof_ready_record_sha256=original.record_sha256,
            original_writer_owner_id=original.writer_owner_id,
            original_writer_fencing_token=original.writer_fencing_token,
            original_writer_fence_sha256=original.writer_fence_sha256,
            order_observation_revision=source.order_observation_event.revision,
            order_observation_record_sha256=source.order_observation_event.record_sha256,
            fill_observation_revision=source.fill_observation_event.revision,
            fill_observation_record_sha256=source.fill_observation_event.record_sha256,
            reconciliation_evidence_sha256=proof.reconciliation_evidence_sha256,
            accepted_request_sha256=proof.accepted_request_sha256,
            provider_order_id=proof.provider_order_id,
            exposure_reservation_id=proof.exposure_reservation_id,
            exposure_reservation_sha256=proof.exposure_reservation_sha256,
            recovery_event_id=expected_event_id,
            recovery_event_revision=event.revision,
            recovery_event_sha256=event.record_sha256,
            current_writer_owner_id=event.writer_owner_id,
            current_writer_fencing_token=event.writer_fencing_token,
            current_writer_fence_sha256=event.writer_fence_sha256,
        )
    except (TypeError, ValueError):
        raise RiskOrderJournalError("RECOVERY_ATTESTATION_EVENT_INVALID") from None


def _same_recovery_source_snapshot(
    left: DispatchProofRecoverySource, right: DispatchProofRecoverySource
) -> bool:
    return (
        left.command.scope == right.command.scope
        and left.command.principal_ref == right.command.principal_ref
        and left.command.intent_id == right.command.intent_id
        and left.command.state == right.command.state
        and left.command.latest_revision == right.command.latest_revision
        and left.command.latest_record_sha256 == right.command.latest_record_sha256
        and left.claim == right.claim
        and left.proof == right.proof
        and left.proof_ready_event == right.proof_ready_event
        and left.order_observation_event == right.order_observation_event
        and left.fill_observation_event == right.fill_observation_event
        and dict(left.exposure_reference) == dict(right.exposure_reference)
        and left.exposure_reference_id == right.exposure_reference_id
        and left.exposure_reference_sha256 == right.exposure_reference_sha256
        and left.recovery_events == right.recovery_events
        and left.recovery_attestations == right.recovery_attestations
    )


def _fence_digest(scope: AccountScope, owner_id: str, fencing_token: int) -> str:
    return sha256_text(
        canonical_json(
            {
                "account_scope_key": scope.key,
                "fencing_token": fencing_token,
                "owner_id": owner_id,
                "schema": "bt-api-risk-order-writer-fence-v1",
            }
        )
    )


def _bounded_observation(value: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {
        "account_revision",
        "acknowledgement",
        "collection_provenance",
        "provider_contract_sha256",
        "operation",
        "normalizer_schema",
        "normalization_provenance",
        "source_fields",
        "source_values",
        "envelope_fields",
        "envelope_values",
        "source_sha256",
        "response_fields",
        "response",
        "record",
        "records",
        "record_count",
        "order_observation_sha256",
        "fill_observation_sha256",
        "complete",
        "schema",
        "schema_version",
        "source_alignment_valid",
        "source_row_count",
        "watermark",
        "provider_order_id",
        "client_order_id",
        "query_client_order_id",
        "account_id",
        "instrument",
        "side",
        "status",
        "quantity",
        "filled_quantity",
        "trade_count",
        "provider_observation_sha256",
        "public_state",
        "reason_code",
    }
    if not isinstance(value, Mapping) or set(value) - allowed:
        raise RiskOrderJournalError("OBSERVATION_FIELDS_INVALID")
    projected = dict(value)
    if len(canonical_json(projected).encode("utf-8")) > 256 * 1024:
        raise RiskOrderJournalError("OBSERVATION_TOO_LARGE")
    return projected


__all__ = [
    "DispatchProofRecoverySource",
    "RiskOrderCommand",
    "RiskOrderJournal",
    "RiskOrderJournalError",
    "RiskOrderJournalEvent",
    "RiskOrderWriterFence",
    "canonical_json",
    "sha256_text",
]
