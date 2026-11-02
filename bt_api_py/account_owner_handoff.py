"""Durable account-owner handoff (direct <-> Gateway, host <-> host).

Iteration 60, step 60.10 (plan §7.6.3). One account must never have two
owners dispatching at once. A handoff walks a persisted state machine::

    QUIESCE -> FENCE -> RESTORE -> SYNC -> RECONCILE -> READY

* Every transition is committed to SQLite with an append-only audit row, so a
  process that crashes mid-handoff resumes at the recorded stage; repeating a
  completed stage with the same arguments is a no-op.
* ``fence`` requires an :class:`OwnerRevocationProof` for the exact account
  and old owner generation: either the old owner's provider session was
  revoked, or a dispatch fence enforced on both owners is in place. A storage
  lease alone is not accepted (plan INV-08). Without proof the handoff is
  NO-GO and stays at ``QUIESCE``.
* From ``QUIESCE`` until ``READY`` nobody may dispatch. At ``READY`` the owner
  changes and the owner generation increases by one; the old generation can
  never dispatch again.
* ``reconcile`` accepts only a ``BALANCED`` reconciliation; anything else
  blocks the handoff for manual review.

The proof's content is supplied by the deployment (for example a provider
logout receipt); this module checks that it is bound to the right account and
generation, it cannot verify the provider fact itself.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
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


class AccountOwnerHandoff:
    """Persisted owner and handoff state for one account."""

    def __init__(self, path: str | Path, account_key: str, *, initial_owner: str) -> None:
        for value in (account_key, initial_owner):
            if type(value) is not str or not value:
                raise HandoffError("HANDOFF_IDENTITY_INVALID")
        self._db = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
        self._lock = threading.Lock()
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
        self._db.execute(
            "INSERT OR IGNORE INTO account_owner(account_key, owner_id, generation)"
            " VALUES (?, ?, 1)",
            (account_key, initial_owner),
        )

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def _row(self) -> tuple[str, int, str | None, str | None, dict]:
        owner, generation, stage, target, detail = self._db.execute(
            "SELECT owner_id, generation, stage, target_owner, detail FROM account_owner"
            " WHERE account_key = ?",
            (self.account_key,),
        ).fetchone()
        return owner, int(generation), stage, target, json.loads(detail)

    @property
    def owner(self) -> tuple[str, int]:
        owner, generation, _stage, _target, _detail = self._row()
        return owner, generation

    @property
    def stage(self) -> str | None:
        """Current handoff stage, or ``None`` when no handoff is in progress."""
        return self._row()[2]

    def audit(self) -> list[tuple[str, dict]]:
        rows = self._db.execute(
            "SELECT action, detail FROM account_owner_audit WHERE account_key = ? ORDER BY seq",
            (self.account_key,),
        ).fetchall()
        return [(action, json.loads(detail)) for action, detail in rows]

    def may_dispatch(self, owner_id: str, generation: int) -> bool:
        """Dispatch gate: only the current owner and generation, never mid-handoff.

        QUIESCE already stops new commands, so no owner dispatches until READY.
        """
        owner, current, stage, _target, _detail = self._row()
        if owner_id != owner or generation != current:
            return False
        return stage is None

    # ------------------------------------------------------------------
    # Transitions
    # ------------------------------------------------------------------

    def _commit(self, *, action: str, detail: dict, **fields: object) -> None:
        sets = ", ".join(f"{name} = ?" for name in fields)
        self._db.execute("BEGIN IMMEDIATE")
        try:
            if fields:
                self._db.execute(
                    f"UPDATE account_owner SET {sets} WHERE account_key = ?",  # noqa: S608 - fixed column names
                    (*fields.values(), self.account_key),
                )
            self._db.execute(
                "INSERT INTO account_owner_audit(account_key, at_ns, action, detail)"
                " VALUES (?, ?, ?, ?)",
                (self.account_key, time.time_ns(), action, json.dumps(detail, sort_keys=True)),
            )
            self._db.execute("COMMIT")
        except BaseException:
            self._db.execute("ROLLBACK")
            raise

    def _expect(self, required: str, stage: str, target_owner: str) -> bool:
        """Return True if ``stage`` was already completed for this handoff."""
        _owner, _generation, current, target, _detail = self._row()
        if current is None:
            raise HandoffError("HANDOFF_NOT_STARTED")
        if target != target_owner:
            raise HandoffError("HANDOFF_TARGET_MISMATCH")
        order = HANDOFF_STAGES.index
        if order(current) >= order(stage):
            return True  # resumed after a crash: already done
        if current != required:
            raise HandoffError("HANDOFF_STAGE_OUT_OF_ORDER")
        return False

    def begin(self, *, from_owner: str, to_owner: str) -> None:
        """QUIESCE: the caller has stopped new commands for this account."""
        with self._lock:
            owner, generation, stage, target, _detail = self._row()
            if stage is not None:
                if target == to_owner and owner == from_owner:
                    return  # same handoff, resumed after a crash
                raise HandoffError("HANDOFF_ALREADY_IN_PROGRESS")
            if owner != from_owner or to_owner == from_owner:
                raise HandoffError("HANDOFF_OWNER_MISMATCH")
            self._commit(
                action="QUIESCE",
                detail={"from": from_owner, "to": to_owner, "generation": generation},
                stage="QUIESCE",
                target_owner=to_owner,
            )

    def fence(self, *, to_owner: str, proof: OwnerRevocationProof | None) -> None:
        with self._lock:
            if self._expect("QUIESCE", "FENCE", to_owner):
                return
            _owner, generation, _stage, _target, _detail = self._row()
            if proof is None:
                raise HandoffError("HANDOFF_NO_GO_OLD_OWNER_NOT_PROVEN_REVOKED")
            if type(proof) is not OwnerRevocationProof or (
                proof.account_key != self.account_key or proof.old_owner_generation != generation
            ):
                raise HandoffError("HANDOFF_PROOF_NOT_BOUND")
            self._commit(
                action="FENCE",
                detail={"proof_kind": proof.kind, "evidence_ref": proof.evidence_ref},
                stage="FENCE",
            )

    def restore(self, *, to_owner: str, ledger_cut: str) -> None:
        with self._lock:
            if self._expect("FENCE", "RESTORE", to_owner):
                return
            self._commit(action="RESTORE", detail={"ledger_cut": ledger_cut}, stage="RESTORE")

    def sync(self, *, to_owner: str, provider_snapshot_ref: str) -> None:
        with self._lock:
            if self._expect("RESTORE", "SYNC", to_owner):
                return
            self._commit(
                action="SYNC", detail={"provider_snapshot_ref": provider_snapshot_ref}, stage="SYNC"
            )

    def reconcile(self, *, to_owner: str, reconciliation_state: str) -> None:
        with self._lock:
            if self._expect("SYNC", "RECONCILE", to_owner):
                return
            if reconciliation_state != "BALANCED":
                self._commit(
                    action="RECONCILE_BLOCKED",
                    detail={"reconciliation_state": reconciliation_state},
                )
                raise HandoffError("HANDOFF_RECONCILIATION_NOT_BALANCED")
            self._commit(
                action="RECONCILE",
                detail={"reconciliation_state": reconciliation_state},
                stage="RECONCILE",
            )

    def ready(self, *, to_owner: str) -> tuple[str, int]:
        """READY: the new owner takes over under the next generation."""
        with self._lock:
            owner, generation, stage, target, _detail = self._row()
            if stage is None and owner == to_owner:
                return owner, generation  # already completed
            if self._expect("RECONCILE", "READY", to_owner):
                return self.owner
            self._commit(
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
