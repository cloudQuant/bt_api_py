"""Durable account-owner handoff (direct <-> Gateway, host <-> host).

Iteration 60, step 60.10 (plan §7.6.3, acceptance report P0-1). One account
must never have two owners dispatching at once. A handoff walks a persisted
state machine::

    QUIESCE -> FENCE -> RESTORE -> SYNC -> RECONCILE -> READY

* Every transition reads, checks, writes and audits inside one SQLite
  ``BEGIN IMMEDIATE`` transaction, so the check and the update are atomic
  across instances, threads and processes sharing the file (compare-and-set
  on the persisted stage, target and generation). Two concurrent ``begin``
  calls can never both succeed.
* A process that crashes mid-handoff resumes at the recorded stage;
  repeating a completed stage for the same handoff is a no-op.
* ``fence`` requires an :class:`OwnerRevocationProof` for the exact account
  and old generation *and* a deployment-supplied ``proof_verifier`` that
  checks it against a trusted source (for example the provider's active
  session list). Without a verifier, or when it does not confirm, the
  handoff is NO-GO and stays at ``QUIESCE``. A storage lease alone is never
  accepted (plan INV-08).
* From ``QUIESCE`` until ``READY`` nobody may dispatch. At ``READY`` the owner
  changes and the generation increases by one; the old generation can never
  dispatch again. :meth:`may_dispatch` is the dispatch-exit check: callers
  (for example ``BtApiBroker.bind_account_owner``) must call it immediately
  before every provider write.
* ``reconcile`` accepts only ``BALANCED``; anything else blocks for review.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "HANDOFF_STAGES",
    "AccountOwnerHandoff",
    "HandoffError",
    "OwnerRevocationProof",
]

HANDOFF_STAGES = ("QUIESCE", "FENCE", "RESTORE", "SYNC", "RECONCILE", "READY")
_PROOF_KINDS = frozenset({"PROVIDER_SESSION_REVOKED", "DISPATCH_FENCE_ENFORCED"})


class HandoffError(RuntimeError):
    """Finite, value-free handoff failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class OwnerRevocationProof:
    """Deployment evidence that the old owner can no longer dispatch."""

    account_key: str
    old_owner_generation: int
    kind: str
    evidence_ref: str

    def __post_init__(self) -> None:
        if self.kind not in _PROOF_KINDS:
            raise HandoffError("HANDOFF_PROOF_KIND_INVALID")
        if type(self.evidence_ref) is not str or not self.evidence_ref:
            raise HandoffError("HANDOFF_PROOF_EVIDENCE_REQUIRED")
        if type(self.old_owner_generation) is not int or self.old_owner_generation < 1:
            raise HandoffError("HANDOFF_PROOF_GENERATION_INVALID")


ProofVerifier = Callable[[OwnerRevocationProof, str], bool]


class AccountOwnerHandoff:
    """Persisted owner and handoff state for one account."""

    def __init__(
        self,
        path: str | Path,
        account_key: str,
        *,
        initial_owner: str,
        proof_verifier: ProofVerifier | None = None,
    ) -> None:
        for value in (account_key, initial_owner):
            if type(value) is not str or not value:
                raise HandoffError("HANDOFF_IDENTITY_INVALID")
        if proof_verifier is not None and not callable(proof_verifier):
            raise HandoffError("HANDOFF_PROOF_VERIFIER_INVALID")
        self._db = sqlite3.connect(
            str(path), isolation_level=None, check_same_thread=False, timeout=10.0
        )
        self._lock = threading.Lock()
        self._proof_verifier = proof_verifier
        self.account_key = account_key
        self._db.executescript(
            "CREATE TABLE IF NOT EXISTS account_owner ("
            " account_key TEXT PRIMARY KEY, owner_id TEXT NOT NULL,"
            " generation INTEGER NOT NULL CHECK (generation >= 1),"
            " stage TEXT, target_owner TEXT, detail TEXT NOT NULL DEFAULT '{}');"
            "CREATE TABLE IF NOT EXISTS account_owner_audit ("
            " seq INTEGER PRIMARY KEY AUTOINCREMENT, account_key TEXT NOT NULL,"
            " at_ns INTEGER NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL);"
        )
        with self._atomic() as db:
            db.execute(
                "INSERT OR IGNORE INTO account_owner(account_key, owner_id, generation)"
                " VALUES (?, ?, 1)",
                (account_key, initial_owner),
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

    def _read(self) -> tuple[str, int, str | None, str | None]:
        with self._atomic() as db:
            return self._row(db)

    @property
    def owner(self) -> tuple[str, int]:
        owner, generation, _stage, _target = self._read()
        return owner, generation

    @property
    def stage(self) -> str | None:
        """Current handoff stage, or ``None`` when no handoff is in progress."""
        return self._read()[2]

    def audit(self) -> list[tuple[str, dict]]:
        with self._atomic() as db:
            rows = db.execute(
                "SELECT action, detail FROM account_owner_audit WHERE account_key = ? ORDER BY seq",
                (self.account_key,),
            ).fetchall()
        return [(action, json.loads(detail)) for action, detail in rows]

    def may_dispatch(self, owner_id: str, generation: int) -> bool:
        """Dispatch-exit check: only the current owner and generation, never mid-handoff."""
        owner, current, stage, _target = self._read()
        return owner_id == owner and generation == current and stage is None

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
        """QUIESCE: the caller has stopped new commands for this account."""
        with self._atomic() as db:
            owner, generation, stage, target = self._row(db)
            if stage is not None:
                if target == to_owner and owner == from_owner:
                    return  # same handoff, resumed after a crash
                raise HandoffError("HANDOFF_ALREADY_IN_PROGRESS")
            if owner != from_owner or to_owner == from_owner:
                raise HandoffError("HANDOFF_OWNER_MISMATCH")
            self._write(
                db,
                action="QUIESCE",
                detail={"from": from_owner, "to": to_owner, "generation": generation},
                stage="QUIESCE",
                target_owner=to_owner,
            )

    def fence(self, *, to_owner: str, proof: OwnerRevocationProof | None) -> None:
        with self._atomic() as db:
            row = self._expect(db, "QUIESCE", "FENCE", to_owner)
            if row is None:
                return
            owner, generation, _stage, _target = row
            if proof is None:
                raise HandoffError("HANDOFF_NO_GO_OLD_OWNER_NOT_PROVEN_REVOKED")
            if type(proof) is not OwnerRevocationProof or (
                proof.account_key != self.account_key or proof.old_owner_generation != generation
            ):
                raise HandoffError("HANDOFF_PROOF_NOT_BOUND")
            if self._proof_verifier is None:
                raise HandoffError("HANDOFF_NO_GO_PROOF_UNVERIFIABLE")
            try:
                confirmed = self._proof_verifier(proof, owner) is True
            except Exception:
                confirmed = False
            if not confirmed:
                raise HandoffError("HANDOFF_NO_GO_PROOF_NOT_CONFIRMED")
            self._write(
                db,
                action="FENCE",
                detail={"proof_kind": proof.kind, "evidence_ref": proof.evidence_ref},
                stage="FENCE",
            )

    def restore(self, *, to_owner: str, ledger_cut: str) -> None:
        with self._atomic() as db:
            if self._expect(db, "FENCE", "RESTORE", to_owner) is None:
                return
            self._write(db, action="RESTORE", detail={"ledger_cut": ledger_cut}, stage="RESTORE")

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
                return
            if reconciliation_state != "BALANCED":
                self._write(
                    db,
                    action="RECONCILE_BLOCKED",
                    detail={"reconciliation_state": reconciliation_state},
                )
                blocked = True
            else:
                self._write(
                    db,
                    action="RECONCILE",
                    detail={"reconciliation_state": reconciliation_state},
                    stage="RECONCILE",
                )
                blocked = False
        if blocked:  # the audit row is committed before the error is raised
            raise HandoffError("HANDOFF_RECONCILIATION_NOT_BALANCED")

    def ready(self, *, to_owner: str) -> tuple[str, int]:
        """READY: the new owner takes over under the next generation."""
        with self._atomic() as db:
            owner, generation, stage, _target = self._row(db)
            if stage is None and owner == to_owner:
                return owner, generation  # already completed
            if self._expect(db, "RECONCILE", "READY", to_owner) is None:
                owner, generation, _stage, _target = self._row(db)
                return owner, generation
            self._write(
                db,
                action="READY",
                detail={"owner": to_owner, "generation": generation + 1},
                owner_id=to_owner,
                generation=generation + 1,
                stage=None,
                target_owner=None,
            )
            return to_owner, generation + 1

    def close(self) -> None:
        self._db.close()
