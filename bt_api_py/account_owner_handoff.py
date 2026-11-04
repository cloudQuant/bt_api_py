"""Durable account-owner handoff and dispatch leases (direct <-> Gateway).

Iteration 60, step 60.10 (plan §7.6.3, re-check G1). One account must never
have two owners dispatching at once. A handoff walks a persisted state
machine::

    QUIESCE -> FENCE -> RESTORE -> SYNC -> RECONCILE -> READY

* Every transition reads, checks, writes and audits inside one SQLite
  ``BEGIN IMMEDIATE`` transaction, so the check and the update are atomic
  across instances, threads and processes sharing the file.
* **Dispatch lease.** A provider write never runs on a bare "may I?" check.
  The owner takes a :class:`DispatchLease` (one ``IN_FLIGHT`` row bound to
  the account, owner and generation) in the same serialized transaction
  domain as ``begin``; it holds the lease for the whole provider call and
  finishes it with the outcome. ``begin`` (QUIESCE) is the exclusive side:
  once it commits, no lease of the old generation can be granted, and the
  leases granted before it are exactly those with ``lease_id`` at or below
  the recorded high-water mark. ``fence`` then waits (bounded) for those
  leases to finish and records any still running as ``UNKNOWN``.
* **Coordinator identity.** A handoff that may grant leases is bound to an
  :class:`~bt_api_py.account_coordination.AccountCoordinationBinding`
  (account plus provider route). The host registry refuses a second
  coordination database for the same binding, and leases require the host
  owner lock (an OS advisory lock held for the owner's lifetime). An unbound
  handoff is diagnostic only and never grants dispatch.
* **Old session revocation.** ``fence`` requires
  :class:`~bt_api_py.provider_session_revocation.ProviderSessionRevocationEvidence`
  for the exact account, route, old generation and the session the old owner
  registered, observed after QUIESCE, and confirmed by a deployment-supplied
  verifier. Without it the takeover is refused (fail-closed).
* **In-flight UNKNOWN.** Leases that ended ``UNKNOWN`` (or were still running
  at FENCE, or orphaned by a crashed owner) stay unresolved: they are carried
  into ``restore`` and :meth:`AccountOwnerHandoff.unresolved_dispatches`, and
  a new submit lease for the same intent id is refused for every owner.
* RESTORE and SYNC references and the ``BALANCED`` string are still
  caller-supplied and unverified, so ``reconcile`` and ``ready`` never promote
  a new owner. Generations above 1 never dispatch.

This class alone is host-local: the host registry and lock coordinate one
host. Cross-host single-writer uses the same state machine hosted by one
writer authority (:mod:`bt_api_py.account_writer_authority`), where owner
liveness is a TTL writer lease on the authority's clock instead of the OS
lock; see that module for its limits.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .account_coordination import (
    AccountCoordinationBinding,
    CoordinationError,
    HostAccountRegistry,
    HostOwnerLock,
    canonical_coordination_path,
)
from .provider_session_revocation import ProviderSessionRevocationEvidence

__all__ = [
    "DISPATCH_OUTCOMES",
    "HANDOFF_STAGES",
    "AccountOwnerHandoff",
    "DispatchLease",
    "HandoffError",
    "UnresolvedDispatch",
]

HANDOFF_STAGES = ("QUIESCE", "FENCE", "RESTORE", "SYNC", "RECONCILE", "READY")
#: Final lease outcomes. ``UNKNOWN`` requires reconciliation by the next owner.
DISPATCH_OUTCOMES = frozenset({"ACKNOWLEDGED", "REJECTED", "NOT_SENT", "UNKNOWN"})
_OPERATIONS = frozenset({"submit", "cancel"})
_UNRESOLVED = ("IN_FLIGHT", "UNKNOWN")
_MAX_DRAIN_SECONDS = 300.0


class HandoffError(RuntimeError):
    """Finite, value-free handoff failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class UnresolvedDispatch:
    """A provider write whose outcome the next owner must reconcile."""

    intent_id: str
    operation: str
    owner_id: str
    generation: int
    state: str
    reason: str | None
    late_outcome: str | None


class DispatchLease:
    """Shared in-flight token for one provider write.

    Hold it for the whole provider call and :meth:`finish` it with the
    outcome. Leaving the context without finishing records ``UNKNOWN``.
    """

    def __init__(self, handoff, lease_id, owner_id, generation, operation, intent_id):
        self._handoff = handoff
        self.lease_id = lease_id
        self.owner_id = owner_id
        self.generation = generation
        self.operation = operation
        self.intent_id = intent_id
        self.outcome: str | None = None

    def finish(self, outcome: str) -> str:
        """Record the outcome once; returns the persisted state."""

        if outcome not in DISPATCH_OUTCOMES:
            raise HandoffError("HANDOFF_DISPATCH_OUTCOME_INVALID")
        if self.outcome is not None:
            return self.outcome
        state = self._handoff._finish_lease(self.lease_id, outcome)
        self.outcome = state
        return state

    def __enter__(self) -> DispatchLease:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.outcome is None:
            self.finish("UNKNOWN")


class AccountOwnerHandoff:
    """Persisted owner, handoff and dispatch-lease state for one account."""

    def __init__(
        self,
        path: str | Path,
        account_key: str,
        *,
        initial_owner: str,
        binding: AccountCoordinationBinding | None = None,
        revocation_verifier: object | None = None,
        host_registry: HostAccountRegistry | None = None,
    ) -> None:
        for value in (account_key, initial_owner):
            if type(value) is not str or not value:
                raise HandoffError("HANDOFF_IDENTITY_INVALID")
        if revocation_verifier is not None and not callable(
            getattr(revocation_verifier, "verify_session_revocation", None)
        ):
            raise HandoffError("HANDOFF_REVOCATION_VERIFIER_INVALID")
        if self._authority_hosted():
            # The writer authority is the only store for its bindings; owner
            # liveness is its writer lease, not a host registry/OS lock.
            if type(binding) is not AccountCoordinationBinding or host_registry is not None:
                raise HandoffError("HANDOFF_BINDING_INVALID")
            if binding.account_ref != account_key:
                raise HandoffError("HANDOFF_BINDING_ACCOUNT_MISMATCH")
            self._registry = None
            self.binding = binding
            self._owner_lock = None
            self._lock_owner = None
            self._open_store(path, account_key, initial_owner, binding, revocation_verifier)
            return
        if binding is not None:
            if type(binding) is not AccountCoordinationBinding:
                raise HandoffError("HANDOFF_BINDING_INVALID")
            if binding.account_ref != account_key:
                raise HandoffError("HANDOFF_BINDING_ACCOUNT_MISMATCH")
            registry = host_registry or HostAccountRegistry.default()
            if type(registry) is not HostAccountRegistry:
                raise HandoffError("HANDOFF_BINDING_INVALID")
            try:
                registry.register_coordinator(binding, path)
            except CoordinationError as error:
                raise HandoffError(f"HANDOFF_{error.code}") from None
        elif host_registry is not None:
            raise HandoffError("HANDOFF_BINDING_INVALID")
        else:
            registry = None
        self._registry = registry
        self.binding = binding
        self._owner_lock: HostOwnerLock | None = None
        self._lock_owner: str | None = None
        self._open_store(path, account_key, initial_owner, binding, revocation_verifier)

    @classmethod
    def _authority_hosted(cls) -> bool:
        """``True`` only for the store a writer authority hosts."""

        return False

    def _open_store(
        self,
        path: str | Path,
        account_key: str,
        initial_owner: str,
        binding: AccountCoordinationBinding | None,
        revocation_verifier: object | None,
    ) -> None:
        self._db = sqlite3.connect(
            str(path), isolation_level=None, check_same_thread=False, timeout=10.0
        )
        self._lock = threading.Lock()
        self._revocation_verifier = revocation_verifier
        self.account_key = account_key
        self._db.executescript(
            "CREATE TABLE IF NOT EXISTS account_owner ("
            " account_key TEXT PRIMARY KEY, owner_id TEXT NOT NULL,"
            " generation INTEGER NOT NULL CHECK (generation >= 1),"
            " stage TEXT, target_owner TEXT, detail TEXT NOT NULL DEFAULT '{}');"
            "CREATE TABLE IF NOT EXISTS account_owner_audit ("
            " seq INTEGER PRIMARY KEY AUTOINCREMENT, account_key TEXT NOT NULL,"
            " at_ns INTEGER NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL);"
            "CREATE TABLE IF NOT EXISTS account_binding ("
            " account_key TEXT PRIMARY KEY, binding_digest TEXT NOT NULL,"
            " provider_route TEXT NOT NULL);"
            "CREATE TABLE IF NOT EXISTS owner_session ("
            " seq INTEGER PRIMARY KEY AUTOINCREMENT, account_key TEXT NOT NULL,"
            " generation INTEGER NOT NULL, owner_id TEXT NOT NULL,"
            " provider_session_ref TEXT NOT NULL, at_ns INTEGER NOT NULL);"
            "CREATE TABLE IF NOT EXISTS dispatch_lease ("
            " lease_id INTEGER PRIMARY KEY AUTOINCREMENT, account_key TEXT NOT NULL,"
            " generation INTEGER NOT NULL, owner_id TEXT NOT NULL,"
            " operation TEXT NOT NULL, intent_id TEXT NOT NULL, state TEXT NOT NULL,"
            " reason TEXT, late_outcome TEXT, pid INTEGER NOT NULL,"
            " started_ns INTEGER NOT NULL, finished_ns INTEGER);"
            "CREATE INDEX IF NOT EXISTS dispatch_lease_intent"
            " ON dispatch_lease(account_key, intent_id, operation);"
            # Writer-authority mode only (see account_writer_authority).
            "CREATE TABLE IF NOT EXISTS writer_lease ("
            " account_key TEXT PRIMARY KEY, owner_id TEXT NOT NULL,"
            " holder_token TEXT NOT NULL, host_id TEXT NOT NULL,"
            " epoch INTEGER NOT NULL CHECK (epoch >= 1),"
            " provider_session_ref TEXT NOT NULL, state TEXT NOT NULL,"
            " released INTEGER NOT NULL DEFAULT 0,"
            " revoke_session_ref TEXT, revoke_generation INTEGER,"
            " granted_wall_ns INTEGER NOT NULL);"
            "CREATE TABLE IF NOT EXISTS dispatch_holder ("
            " lease_id INTEGER PRIMARY KEY, holder_token TEXT NOT NULL,"
            " request_id TEXT NOT NULL, UNIQUE (holder_token, request_id));"
        )
        try:
            with self._atomic() as db:
                db.execute(
                    "INSERT OR IGNORE INTO account_owner(account_key, owner_id, generation)"
                    " VALUES (?, ?, 1)",
                    (account_key, initial_owner),
                )
                stored = db.execute(
                    "SELECT binding_digest FROM account_binding WHERE account_key = ?",
                    (account_key,),
                ).fetchone()
                if binding is not None:
                    if stored is None:
                        db.execute(
                            "INSERT INTO account_binding VALUES (?, ?, ?)",
                            (account_key, binding.digest, binding.provider_route),
                        )
                    elif stored[0] != binding.digest:
                        raise HandoffError("HANDOFF_COORDINATOR_BINDING_MISMATCH")
        except BaseException:
            self._db.close()
            raise

    @classmethod
    def open_canonical(
        cls,
        coordination_root: str | Path,
        binding: AccountCoordinationBinding,
        *,
        initial_owner: str,
        revocation_verifier: object | None = None,
        host_registry: HostAccountRegistry | None = None,
    ) -> AccountOwnerHandoff:
        """Open the canonical coordination store derived from ``binding``."""

        try:
            path = canonical_coordination_path(coordination_root, binding)
        except CoordinationError as error:
            raise HandoffError(f"HANDOFF_{error.code}") from None
        path.parent.mkdir(parents=True, exist_ok=True)
        return cls(
            path,
            binding.account_ref,
            initial_owner=initial_owner,
            binding=binding,
            revocation_verifier=revocation_verifier,
            host_registry=host_registry,
        )

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    class _Atomic:
        def __init__(self, handoff: AccountOwnerHandoff) -> None:
            self._handoff = handoff

        def __enter__(self) -> sqlite3.Connection:
            self._handoff._lock.acquire()
            try:
                self._handoff._db.execute("BEGIN IMMEDIATE")
            except BaseException:
                self._handoff._lock.release()
                raise
            return self._handoff._db

        def __exit__(self, exc_type, exc, tb) -> None:
            try:
                self._handoff._db.execute("COMMIT" if exc_type is None else "ROLLBACK")
            finally:
                self._handoff._lock.release()

    def _atomic(self) -> AccountOwnerHandoff._Atomic:
        return AccountOwnerHandoff._Atomic(self)

    def _row(self, db: sqlite3.Connection) -> tuple[str, int, str | None, str | None]:
        owner, generation, stage, target = db.execute(
            "SELECT owner_id, generation, stage, target_owner FROM account_owner"
            " WHERE account_key = ?",
            (self.account_key,),
        ).fetchone()
        return owner, int(generation), stage, target

    def _detail(self, db: sqlite3.Connection) -> dict:
        (detail,) = db.execute(
            "SELECT detail FROM account_owner WHERE account_key = ?", (self.account_key,)
        ).fetchone()
        try:
            value = json.loads(detail)
        except ValueError:
            return {}
        return value if isinstance(value, dict) else {}

    def _read(self) -> tuple[str, int, str | None, str | None]:
        with self._atomic() as db:
            return self._row(db)

    @property
    def owner(self) -> tuple[str, int]:
        owner, generation, _stage, _target = self._read()
        return owner, generation

    @property
    def stage(self) -> str | None:
        """Stored diagnostic stage; this is not evidence of completed recovery."""
        return self._read()[2]

    @property
    def holds_owner_lock(self) -> str | None:
        """The owner id whose host lock this instance holds, if any."""

        lock = self._owner_lock
        return self._lock_owner if lock is not None and lock.held else None

    def audit(self) -> list[tuple[str, dict]]:
        with self._atomic() as db:
            rows = db.execute(
                "SELECT action, detail FROM account_owner_audit WHERE account_key = ? ORDER BY seq",
                (self.account_key,),
            ).fetchall()
        return [(action, json.loads(detail)) for action, detail in rows]

    def _liveness_refusal(
        self, db: sqlite3.Connection, owner_id: str, holder: object | None
    ) -> str | None:
        """Host-local liveness: this process holds the OS owner lock."""

        if self._registry is None:
            return "HANDOFF_DISPATCH_UNBOUND"
        if self.holds_owner_lock != owner_id:
            return "HANDOFF_HOST_OWNER_LOCK_NOT_HELD"
        return None

    def _dispatch_refusal(
        self,
        db: sqlite3.Connection,
        owner_id: object,
        generation: object,
        holder: object | None = None,
    ) -> str | None:
        if self.binding is None:
            return "HANDOFF_DISPATCH_UNBOUND"
        if type(owner_id) is not str or type(generation) is not int:
            return "HANDOFF_DISPATCH_FENCED"
        refusal = self._liveness_refusal(db, owner_id, holder)
        if refusal is not None:
            return refusal
        owner, current, stage, _target = self._row(db)
        # Generations above 1 were never promoted through verified recovery.
        if owner_id != owner or not (generation == current == 1) or stage is not None:
            return "HANDOFF_DISPATCH_FENCED"
        return None

    def may_dispatch(self, owner_id: str, generation: int) -> bool:
        """Pre-check only; the provider write itself needs a :class:`DispatchLease`."""

        with self._atomic() as db:
            return self._dispatch_refusal(db, owner_id, generation) is None

    # ------------------------------------------------------------------
    # Host owner lock and dispatch leases
    # ------------------------------------------------------------------

    def hold_owner_lock(self, owner_id: str, *, provider_session_ref: str) -> None:
        """Take this host's exclusive owner lock for the owner's lifetime.

        ``provider_session_ref`` is the opaque provider session the owner
        writes through; revocation evidence must name it. Taking the lock
        proves any earlier lock holder on this host is gone, so its still
        ``IN_FLIGHT`` leases are recorded ``UNKNOWN`` (``OWNER_LOCK_ORPHAN``).
        """

        if self.binding is None or self._registry is None:
            raise HandoffError("HANDOFF_DISPATCH_UNBOUND")
        if type(provider_session_ref) is not str or not provider_session_ref:
            raise HandoffError("HANDOFF_PROVIDER_SESSION_REQUIRED")
        if self._owner_lock is not None and self._owner_lock.held:
            raise HandoffError("HANDOFF_HOST_OWNER_LOCK_ALREADY_HELD")
        try:
            lock = self._registry.acquire_owner_lock(self.binding)
        except CoordinationError as error:
            raise HandoffError(f"HANDOFF_{error.code}") from None
        try:
            with self._atomic() as db:
                owner, generation, _stage, _target = self._row(db)
                if owner_id != owner:
                    raise HandoffError("HANDOFF_OWNER_MISMATCH")
                orphaned = db.execute(
                    "UPDATE dispatch_lease SET state = 'UNKNOWN', reason = 'OWNER_LOCK_ORPHAN'"
                    " WHERE account_key = ? AND state = 'IN_FLIGHT'",
                    (self.account_key,),
                ).rowcount
                db.execute(
                    "INSERT INTO owner_session(account_key, generation, owner_id,"
                    " provider_session_ref, at_ns) VALUES (?, ?, ?, ?, ?)",
                    (self.account_key, generation, owner_id, provider_session_ref, time.time_ns()),
                )
                self._write(
                    db,
                    action="OWNER_LOCK",
                    detail={
                        "owner": owner_id,
                        "generation": generation,
                        "orphaned_inflight": orphaned,
                    },
                )
        except BaseException:
            lock.release()
            raise
        self._owner_lock, self._lock_owner = lock, owner_id

    def release_owner_lock(self) -> None:
        lock, self._owner_lock, self._lock_owner = self._owner_lock, None, None
        if lock is not None:
            lock.release()

    def acquire_dispatch_lease(
        self, owner_id: str, generation: int, *, operation: str, intent_id: str
    ) -> DispatchLease:
        """Atomically check ownership and register one in-flight provider write.

        Raises :class:`HandoffError` when the write must not start; nothing
        has been sent in that case.
        """

        if operation not in _OPERATIONS:
            raise HandoffError("HANDOFF_DISPATCH_OPERATION_INVALID")
        if type(intent_id) is not str or not intent_id:
            raise HandoffError("HANDOFF_DISPATCH_INTENT_INVALID")
        with self._atomic() as db:
            lease_id = self._grant_dispatch(db, owner_id, generation, operation, intent_id, None)
        return DispatchLease(self, lease_id, owner_id, generation, operation, intent_id)

    def _grant_dispatch(
        self,
        db: sqlite3.Connection,
        owner_id: str,
        generation: int,
        operation: str,
        intent_id: str,
        holder: object | None,
    ) -> int:
        """Inside one transaction: check ownership and insert an IN_FLIGHT row."""

        refusal = self._dispatch_refusal(db, owner_id, generation, holder)
        if refusal is not None:
            raise HandoffError(refusal)
        states = {
            state
            for (state,) in db.execute(
                "SELECT state FROM dispatch_lease WHERE account_key = ? AND intent_id = ?"
                " AND operation = ?",
                (self.account_key, intent_id, operation),
            )
        }
        if operation == "submit":
            if states & set(_UNRESOLVED):
                raise HandoffError("HANDOFF_DISPATCH_INTENT_UNRESOLVED")
            if states - {"NOT_SENT"}:
                raise HandoffError("HANDOFF_DISPATCH_INTENT_ALREADY_SENT")
        elif "IN_FLIGHT" in states:
            raise HandoffError("HANDOFF_DISPATCH_INTENT_IN_FLIGHT")
        cursor = db.execute(
            "INSERT INTO dispatch_lease(account_key, generation, owner_id, operation,"
            " intent_id, state, pid, started_ns) VALUES (?, ?, ?, ?, ?, 'IN_FLIGHT', ?, ?)",
            (
                self.account_key,
                generation,
                owner_id,
                operation,
                intent_id,
                os.getpid(),
                time.time_ns(),
            ),
        )
        return int(cursor.lastrowid)

    def _finish_lease(self, lease_id: int, outcome: str) -> str:
        with self._atomic() as db:
            row = db.execute(
                "SELECT state FROM dispatch_lease WHERE lease_id = ? AND account_key = ?",
                (lease_id, self.account_key),
            ).fetchone()
            if row is None:
                raise HandoffError("HANDOFF_DISPATCH_LEASE_UNKNOWN")
            if row[0] != "IN_FLIGHT":
                # Already fenced as UNKNOWN: keep it unresolved, note what the
                # old owner reported, and let the next owner reconcile.
                db.execute(
                    "UPDATE dispatch_lease SET late_outcome = ?, finished_ns = ?"
                    " WHERE lease_id = ?",
                    (outcome, time.time_ns(), lease_id),
                )
                return row[0]
            db.execute(
                "UPDATE dispatch_lease SET state = ?, finished_ns = ? WHERE lease_id = ?",
                (outcome, time.time_ns(), lease_id),
            )
            return outcome

    def unresolved_dispatches(self) -> tuple[UnresolvedDispatch, ...]:
        """Provider writes still ``IN_FLIGHT`` or ``UNKNOWN`` for this account."""

        with self._atomic() as db:
            rows = db.execute(
                "SELECT intent_id, operation, owner_id, generation, state, reason, late_outcome"
                " FROM dispatch_lease WHERE account_key = ? AND state IN (?, ?)"
                " ORDER BY lease_id",
                (self.account_key, *_UNRESOLVED),
            ).fetchall()
        return tuple(UnresolvedDispatch(*row) for row in rows)

    def _inflight_count(self, generation: int) -> int:
        with self._atomic() as db:
            (count,) = db.execute(
                "SELECT COUNT(*) FROM dispatch_lease WHERE account_key = ? AND generation = ?"
                " AND state = 'IN_FLIGHT'",
                (self.account_key, generation),
            ).fetchone()
        return int(count)

    # ------------------------------------------------------------------
    # Transitions (each one atomic: read, check, write, audit)
    # ------------------------------------------------------------------

    def _write(
        self, db: sqlite3.Connection, *, action: str, detail: dict, **fields: object
    ) -> None:
        if fields:
            sets = ", ".join(f"{name} = ?" for name in fields)
            db.execute(
                f"UPDATE account_owner SET {sets} WHERE account_key = ?",  # noqa: S608 - fixed names
                (*fields.values(), self.account_key),
            )
        db.execute(
            "INSERT INTO account_owner_audit(account_key, at_ns, action, detail) VALUES (?, ?, ?, ?)",
            (self.account_key, time.time_ns(), action, json.dumps(detail, sort_keys=True)),
        )

    def _expect(self, db: sqlite3.Connection, required: str, stage: str, target_owner: str):
        """Return the row, or ``None`` if ``stage`` was already completed."""
        row = self._row(db)
        _owner, _generation, current, target = row
        if current is None:
            raise HandoffError("HANDOFF_NOT_STARTED")
        if target != target_owner:
            raise HandoffError("HANDOFF_TARGET_MISMATCH")
        order = HANDOFF_STAGES.index
        if order(current) >= order(stage):
            return None  # resumed after a crash: already done
        if current != required:
            raise HandoffError("HANDOFF_STAGE_OUT_OF_ORDER")
        return row

    def begin(self, *, from_owner: str, to_owner: str) -> None:
        """QUIESCE: the exclusive side of the dispatch lease.

        After this commits no lease of the old generation is granted. The
        leases granted before it are recorded by their high-water mark.
        """
        with self._atomic() as db:
            owner, generation, stage, target = self._row(db)
            if stage is not None:
                if target == to_owner and owner == from_owner:
                    return  # same handoff, resumed after a crash
                raise HandoffError("HANDOFF_ALREADY_IN_PROGRESS")
            if owner != from_owner or to_owner == from_owner:
                raise HandoffError("HANDOFF_OWNER_MISMATCH")
            (high_water,) = db.execute(
                "SELECT COALESCE(MAX(lease_id), 0) FROM dispatch_lease WHERE account_key = ?",
                (self.account_key,),
            ).fetchone()
            (inflight,) = db.execute(
                "SELECT COUNT(*) FROM dispatch_lease WHERE account_key = ? AND generation = ?"
                " AND state = 'IN_FLIGHT'",
                (self.account_key, generation),
            ).fetchone()
            quiesced_at_ns = time.time_ns()
            detail = {
                "from": from_owner,
                "to": to_owner,
                "generation": generation,
                "lease_high_water": int(high_water),
                "inflight_at_quiesce": int(inflight),
                "quiesced_at_ns": quiesced_at_ns,
            }
            db.execute(
                "UPDATE account_owner SET stage = 'QUIESCE', target_owner = ?, detail = ?"
                " WHERE account_key = ?",
                (to_owner, json.dumps(detail, sort_keys=True), self.account_key),
            )
            self._write(db, action="QUIESCE", detail=detail)

    def _check_revocation(
        self, db: sqlite3.Connection, evidence: object, generation: int
    ) -> ProviderSessionRevocationEvidence:
        if self.binding is None:
            raise HandoffError("HANDOFF_COORDINATOR_UNBOUND")
        if evidence is None:
            raise HandoffError("HANDOFF_NO_GO_OLD_SESSION_NOT_REVOKED")
        if type(evidence) is not ProviderSessionRevocationEvidence:
            raise HandoffError("HANDOFF_REVOCATION_EVIDENCE_INVALID")
        session = db.execute(
            "SELECT provider_session_ref FROM owner_session WHERE account_key = ?"
            " AND generation = ? ORDER BY seq DESC LIMIT 1",
            (self.account_key, generation),
        ).fetchone()
        if session is None:
            raise HandoffError("HANDOFF_NO_GO_OLD_SESSION_UNKNOWN")
        if (
            evidence.account_ref != self.binding.account_ref
            or evidence.provider_route != self.binding.provider_route
            or evidence.revoked_owner_generation != generation
            or evidence.revoked_session_ref != session[0]
        ):
            raise HandoffError("HANDOFF_REVOCATION_NOT_BOUND")
        quiesced_at_ns = self._detail(db).get("quiesced_at_ns")
        if type(quiesced_at_ns) is not int or evidence.observed_at_ns < quiesced_at_ns:
            raise HandoffError("HANDOFF_REVOCATION_EVIDENCE_STALE")
        if self._revocation_verifier is None:
            raise HandoffError("HANDOFF_NO_GO_REVOCATION_UNVERIFIABLE")
        return evidence

    def fence(
        self,
        *,
        to_owner: str,
        evidence: ProviderSessionRevocationEvidence | None,
        drain_timeout: float = 5.0,
        poll_interval: float = 0.01,
    ) -> None:
        """FENCE: verified old-session revocation plus drained (or UNKNOWN) leases."""

        if (
            type(drain_timeout) not in (int, float)
            or not 0 <= drain_timeout <= _MAX_DRAIN_SECONDS
            or type(poll_interval) not in (int, float)
            or not 0 < poll_interval <= 1
        ):
            raise HandoffError("HANDOFF_DRAIN_TIMEOUT_INVALID")
        with self._atomic() as db:
            row = self._expect(db, "QUIESCE", "FENCE", to_owner)
            if row is None:
                return
            _owner, generation, _stage, _target = row
            checked = self._check_revocation(db, evidence, generation)
        try:
            confirmed = self._revocation_verifier.verify_session_revocation(checked) is True
        except Exception:
            confirmed = False
        if not confirmed:
            raise HandoffError("HANDOFF_NO_GO_REVOCATION_NOT_CONFIRMED")
        deadline = time.monotonic() + drain_timeout
        while self._inflight_count(generation) and time.monotonic() < deadline:
            time.sleep(poll_interval)
        with self._atomic() as db:
            row = self._expect(db, "QUIESCE", "FENCE", to_owner)
            if row is None:
                return
            if row[1] != generation:
                raise HandoffError("HANDOFF_STAGE_OUT_OF_ORDER")
            pending = [
                intent
                for (intent,) in db.execute(
                    "SELECT intent_id FROM dispatch_lease WHERE account_key = ?"
                    " AND generation = ? AND state = 'IN_FLIGHT' ORDER BY lease_id",
                    (self.account_key, generation),
                )
            ]
            db.execute(
                "UPDATE dispatch_lease SET state = 'UNKNOWN', reason = 'FENCE_DRAIN_TIMEOUT'"
                " WHERE account_key = ? AND generation = ? AND state = 'IN_FLIGHT'",
                (self.account_key, generation),
            )
            self._write(
                db,
                action="FENCE",
                detail={
                    "evidence_kind": checked.kind,
                    "evidence_ref": checked.evidence_ref,
                    "revoked_session_ref": checked.revoked_session_ref,
                    "unknown_inflight": pending,
                },
                stage="FENCE",
            )

    def restore(self, *, to_owner: str, ledger_cut: str) -> None:
        with self._atomic() as db:
            if self._expect(db, "FENCE", "RESTORE", to_owner) is None:
                return
            carried = [
                intent
                for (intent,) in db.execute(
                    "SELECT intent_id FROM dispatch_lease WHERE account_key = ?"
                    " AND state IN (?, ?) ORDER BY lease_id",
                    (self.account_key, *_UNRESOLVED),
                )
            ]
            self._write(
                db,
                action="RESTORE",
                detail={"ledger_cut": ledger_cut, "carried_unknown_intents": carried},
                stage="RESTORE",
            )

    def sync(self, *, to_owner: str, provider_snapshot_ref: str) -> None:
        with self._atomic() as db:
            if self._expect(db, "RESTORE", "SYNC", to_owner) is None:
                return
            self._write(
                db,
                action="SYNC",
                detail={"provider_snapshot_ref": provider_snapshot_ref},
                stage="SYNC",
            )

    def reconcile(self, *, to_owner: str, reconciliation_state: str) -> None:
        with self._atomic() as db:
            if self._expect(db, "SYNC", "RECONCILE", to_owner) is None:
                raise HandoffError("HANDOFF_NO_GO_RECOVERY_UNVERIFIED")
            (unresolved,) = db.execute(
                "SELECT COUNT(*) FROM dispatch_lease WHERE account_key = ? AND state IN (?, ?)",
                (self.account_key, *_UNRESOLVED),
            ).fetchone()
            if unresolved:
                self._write(
                    db,
                    action="RECONCILE_BLOCKED_UNKNOWN_INFLIGHT",
                    detail={"unresolved": int(unresolved)},
                )
                code = "HANDOFF_NO_GO_UNKNOWN_INFLIGHT"
            elif reconciliation_state != "BALANCED":
                self._write(
                    db,
                    action="RECONCILE_BLOCKED",
                    detail={"reconciliation_state": reconciliation_state},
                )
                code = "HANDOFF_RECONCILIATION_NOT_BALANCED"
            else:
                # This string is a request, not a verified account-scoped
                # reconciliation over a trusted ledger and provider snapshot.
                self._write(
                    db,
                    action="RECONCILE_UNVERIFIED",
                    detail={"reconciliation_state": reconciliation_state},
                )
                code = "HANDOFF_NO_GO_RECOVERY_UNVERIFIED"
        # Keep the attempted result for diagnostics, but do not advance.
        raise HandoffError(code)

    def ready(self, *, to_owner: str) -> tuple[str, int]:
        """Refuse promotion until recovery is independently verified."""
        with self._atomic() as db:
            owner, generation, stage, _target = self._row(db)
            if stage is None:
                if owner == to_owner and generation == 1:
                    return owner, generation
                if generation == 1:
                    raise HandoffError("HANDOFF_NOT_STARTED")
            else:
                self._expect(db, "RECONCILE", "READY", to_owner)
            raise HandoffError("HANDOFF_NO_GO_RECOVERY_UNVERIFIED")

    def close(self) -> None:
        self.release_owner_lock()
        self._db.close()
