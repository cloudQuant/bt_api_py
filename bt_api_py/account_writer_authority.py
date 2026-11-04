"""Cross-host account writer authority (iteration 60, G1 cross-host).

:class:`~bt_api_py.account_owner_handoff.AccountOwnerHandoff` keeps the owner,
handoff stages and dispatch leases of one account in a SQLite store and proves
owner liveness with an OS lock. Both are host-local: two hosts with two stores
can each believe they own the account. This module moves the *same* state
machine behind one authority process that every host reaches over the network:

* :class:`WriterAuthorityServer` hosts one :class:`AuthorityHostedHandoff`
  store per account binding and serializes every operation on it. It is the
  only store for its bindings; hosts keep no account-owner state of their own.
* Owner liveness is a **writer lease** with a TTL measured only on the
  authority's monotonic clock. Client clocks never decide who may write.
* A **dispatch lease** (one provider write) is granted by the authority only
  while the caller holds the current writer lease *and* at least
  ``max_dispatch_seconds`` of it remain, so the dispatch fence is evaluated by
  the authority itself, not by a possibly partitioned or paused owner.
* A lease that ends without a clean release (crash, partition, pause) can be
  taken by another holder only after ``ttl + grace``. The new holder starts in
  ``PENDING_REVOCATION`` and is refused every dispatch until provider-side
  session revocation evidence for the previous holder's session is confirmed
  by the deployment verifier. In-flight leases of the previous holder become
  ``UNKNOWN`` (``WRITER_LEASE_EXPIRED_ORPHAN``) and are carried exactly like
  host-local orphans: the same intent can never be submitted again.
* After an authority restart every persisted, unreleased lease is re-armed
  for a full TTL (its remaining time is unknown), so a restart never shortens
  a lease.

Requests and replies are authenticated with HMAC-SHA256 under a per-client
secret, bound to a protocol label and direction, with nonce and timestamp
replay protection (the timestamp is used for replay only; wall-clock skew
beyond the window makes the authority refuse, never grant).

Limits, stated plainly: the authority is one process (no replication); its
loss stops all dispatch (fail closed) and its storage must survive restarts.
The channel is authenticated, not encrypted: run it on a private network or a
TLS tunnel. A provider does not check fencing tokens, so a write already
sent by a paused or partitioned owner can still reach the provider after a
takeover; that window is closed only by verified provider-session revocation
(required before a new holder dispatches) and by reconciling the carried
``UNKNOWN`` writes. Whether a given provider's revocation evidence is real is
a deployment fact this module cannot prove.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import socket
import socketserver
import struct
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from .account_coordination import (
    AccountCoordinationBinding,
    CoordinationError,
    canonical_coordination_path,
)
from .account_owner_handoff import (
    DISPATCH_OUTCOMES,
    AccountOwnerHandoff,
    DispatchLease,
    HandoffError,
    UnresolvedDispatch,
)
from .provider_session_revocation import ProviderSessionRevocationEvidence

__all__ = [
    "AUTHORITY_PROTOCOL",
    "AuthorityHostedHandoff",
    "RemoteAccountOwnerHandoff",
    "WriterAuthorityServer",
    "WriterLeasePolicy",
]

AUTHORITY_PROTOCOL = "bt_api_py.account-writer-authority.v1"
MAX_FRAME_BYTES = 64 * 1024
_REPLAY_WINDOW_NS = 60 * 1_000_000_000
_MAX_NONCES_PER_CLIENT = 8192
_HEADER = struct.Struct(">I")
# Bounded rate difference between two monotonic clocks (see Gateway worker).
_CLOCK_RATE_MARGIN = 1e-3
_LEASE_STATES = ("ACTIVE", "PENDING_REVOCATION")
_RELEASE_NONE, _RELEASE_CLEAN, _RELEASE_UNCLEAN = 0, 1, 2


def _text(value: object, *, limit: int = 256) -> bool:
    return (
        type(value) is str
        and bool(value)
        and value == value.strip()
        and len(value) <= limit
        and value.isprintable()
    )


@dataclass(frozen=True)
class WriterLeasePolicy:
    """Writer-lease timing, enforced on the authority's monotonic clock.

    ``max_dispatch_seconds`` is the longest one provider write may be held;
    a dispatch lease is granted only if that much of the writer lease remains.
    ``grace_seconds`` is the extra wait after an unreleased lease expires
    before another holder may take it; it must cover ``max_dispatch_seconds``.
    """

    ttl_seconds: float = 10.0
    grace_seconds: float = 5.0
    max_dispatch_seconds: float = 3.0

    def __post_init__(self) -> None:
        for value in (self.ttl_seconds, self.grace_seconds, self.max_dispatch_seconds):
            if type(value) not in (int, float) or not 0 < value <= 300:
                raise HandoffError("HANDOFF_WRITER_POLICY_INVALID")
        if not self.max_dispatch_seconds < self.ttl_seconds:
            raise HandoffError("HANDOFF_WRITER_POLICY_INVALID")
        if self.grace_seconds < self.max_dispatch_seconds:
            raise HandoffError("HANDOFF_WRITER_POLICY_INVALID")

    @property
    def ttl_ns(self) -> int:
        return int(self.ttl_seconds * 1_000_000_000)

    @property
    def grace_ns(self) -> int:
        return int(self.grace_seconds * 1_000_000_000)

    @property
    def max_dispatch_ns(self) -> int:
        return int(self.max_dispatch_seconds * 1_000_000_000)


# ---------------------------------------------------------------------------
# Authority-side store
# ---------------------------------------------------------------------------


class AuthorityHostedHandoff(AccountOwnerHandoff):
    """The account-owner state machine as hosted by one writer authority.

    Identical stages, dispatch-lease and UNKNOWN rules as the host-local
    store; only owner liveness differs (writer lease on this process's
    monotonic clock instead of the OS lock). Every lease check and change runs
    in the same serialized transaction domain as the stage transitions.
    """

    def __init__(
        self,
        path: str | Path,
        binding: AccountCoordinationBinding,
        *,
        initial_owner: str,
        policy: WriterLeasePolicy,
        revocation_verifier: object | None = None,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
        wall_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        if type(policy) is not WriterLeasePolicy or not callable(monotonic_ns):
            raise HandoffError("HANDOFF_WRITER_POLICY_INVALID")
        if not callable(wall_ns):
            raise HandoffError("HANDOFF_WRITER_POLICY_INVALID")
        self._policy = policy
        self._mono = monotonic_ns
        self._wall = wall_ns
        # The current lease's holder token and expiry on ``_mono``. Only
        # read or changed while ``_lock`` is held (inside ``_atomic``).
        self._lease_token: str | None = None
        self._lease_expiry_ns: int | None = None
        if type(binding) is not AccountCoordinationBinding:
            raise HandoffError("HANDOFF_BINDING_INVALID")
        super().__init__(
            path,
            binding.account_ref,
            initial_owner=initial_owner,
            binding=binding,
            revocation_verifier=revocation_verifier,
        )
        with self._atomic() as db:
            row = self._lease_row(db)
            if row is not None and row["released"] == _RELEASE_NONE:
                # Remaining time is unknown after a restart: a full TTL.
                self._lease_token = row["holder_token"]
                self._lease_expiry_ns = self._mono() + policy.ttl_ns
                self._write(
                    db,
                    action="WRITER_LEASE_REARMED",
                    detail={"epoch": row["epoch"], "owner": row["owner_id"]},
                )

    @classmethod
    def _authority_hosted(cls) -> bool:
        return True

    @property
    def policy(self) -> WriterLeasePolicy:
        return self._policy

    @property
    def holds_owner_lock(self) -> str | None:
        return None

    def hold_owner_lock(self, owner_id: str, *, provider_session_ref: str) -> None:
        raise HandoffError("HANDOFF_AUTHORITY_REQUIRES_WRITER_LEASE")

    def release_owner_lock(self) -> None:
        return None

    def acquire_dispatch_lease(self, *args: object, **kwargs: object) -> DispatchLease:
        raise HandoffError("HANDOFF_AUTHORITY_REQUIRES_WRITER_LEASE")

    # -- lease row ----------------------------------------------------------

    def _lease_row(self, db) -> dict[str, Any] | None:
        row = db.execute(
            "SELECT owner_id, holder_token, host_id, epoch, provider_session_ref, state,"
            " released, revoke_session_ref, revoke_generation, granted_wall_ns"
            " FROM writer_lease WHERE account_key = ?",
            (self.account_key,),
        ).fetchone()
        if row is None:
            return None
        names = (
            "owner_id",
            "holder_token",
            "host_id",
            "epoch",
            "provider_session_ref",
            "state",
            "released",
            "revoke_session_ref",
            "revoke_generation",
            "granted_wall_ns",
        )
        return dict(zip(names, row))

    def _held_row(self, db, owner_id: object, holder_token: object) -> dict[str, Any]:
        row = self._lease_row(db)
        if (
            row is None
            or type(holder_token) is not str
            or row["holder_token"] != holder_token
            or row["owner_id"] != owner_id
            or row["released"] != _RELEASE_NONE
            or self._lease_token != holder_token
        ):
            raise HandoffError("HANDOFF_WRITER_LEASE_NOT_HELD")
        return row

    def _liveness_refusal(self, db, owner_id: str, holder: object | None) -> str | None:
        try:
            row = self._held_row(db, owner_id, holder)
        except HandoffError as error:
            return error.code
        now = self._mono()
        expiry = self._lease_expiry_ns
        if expiry is None or now >= expiry:
            return "HANDOFF_WRITER_LEASE_EXPIRED"
        if row["state"] != "ACTIVE":
            return "HANDOFF_NO_GO_PREVIOUS_SESSION_NOT_REVOKED"
        if now + self._policy.max_dispatch_ns > expiry:
            return "HANDOFF_WRITER_LEASE_EXPIRING"
        return None

    def _grant(self, row: dict[str, Any], *, renewed: bool) -> dict[str, Any]:
        return {
            "epoch": int(row["epoch"]),
            "state": row["state"],
            "ttl_ns": self._policy.ttl_ns,
            "max_dispatch_ns": self._policy.max_dispatch_ns,
            "renewed": renewed,
        }

    # -- writer lease operations -------------------------------------------

    def writer_lease_status(self) -> dict[str, Any]:
        with self._atomic() as db:
            owner, generation, stage, target = self._row(db)
            row = self._lease_row(db)
            now = self._mono()
            held = (
                row is not None
                and row["released"] == _RELEASE_NONE
                and self._lease_expiry_ns is not None
                and now < self._lease_expiry_ns
            )
            return {
                "owner": owner,
                "generation": generation,
                "stage": stage,
                "target_owner": target,
                "lease_held": held,
                "lease_owner": row["owner_id"] if held else None,
                "lease_host": row["host_id"] if held else None,
                "lease_epoch": int(row["epoch"]) if row is not None else 0,
                "lease_state": row["state"] if held else None,
            }

    def acquire_writer_lease(
        self,
        owner_id: str,
        *,
        holder_token: str,
        host_id: str,
        provider_session_ref: str,
    ) -> dict[str, Any]:
        """Grant (or renew) the writer lease to ``holder_token`` for ``owner_id``."""

        for value in (owner_id, holder_token, host_id):
            if not _text(value):
                raise HandoffError("HANDOFF_IDENTITY_INVALID")
        if not _text(provider_session_ref):
            raise HandoffError("HANDOFF_PROVIDER_SESSION_REQUIRED")
        with self._atomic() as db:
            owner, generation, stage, _target = self._row(db)
            if owner_id != owner:
                raise HandoffError("HANDOFF_OWNER_MISMATCH")
            if stage is not None or generation != 1:
                raise HandoffError("HANDOFF_DISPATCH_FENCED")
            now = self._mono()
            row = self._lease_row(db)
            active = row is not None and row["released"] == _RELEASE_NONE
            if active and row["holder_token"] == holder_token:
                if self._lease_token == holder_token and self._lease_expiry_ns is not None:
                    # Same holder: renew, or resume after its own expiry when
                    # nobody else took the lease meanwhile (no other writer).
                    if provider_session_ref != row["provider_session_ref"]:
                        raise HandoffError("HANDOFF_PROVIDER_SESSION_CHANGED")
                    self._lease_expiry_ns = now + self._policy.ttl_ns
                    return self._grant(row, renewed=True)
            if (
                active
                and self._lease_expiry_ns is not None
                and now < self._lease_expiry_ns + self._policy.grace_ns
            ):
                raise HandoffError("HANDOFF_WRITER_LEASE_HELD")
            orphaned = db.execute(
                "UPDATE dispatch_lease SET state = 'UNKNOWN', reason = 'WRITER_LEASE_EXPIRED_ORPHAN'"
                " WHERE account_key = ? AND state = 'IN_FLIGHT'",
                (self.account_key,),
            ).rowcount
            clean = row is None or (row["released"] == _RELEASE_CLEAN and orphaned == 0)
            state = "ACTIVE" if clean else "PENDING_REVOCATION"
            epoch = 1 if row is None else int(row["epoch"]) + 1
            revoke_ref = None if clean else row["provider_session_ref"]
            revoke_generation = None if clean else generation
            granted_wall = self._wall()
            db.execute(
                "INSERT OR REPLACE INTO writer_lease(account_key, owner_id, holder_token,"
                " host_id, epoch, provider_session_ref, state, released, revoke_session_ref,"
                " revoke_generation, granted_wall_ns) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)",
                (
                    self.account_key,
                    owner_id,
                    holder_token,
                    host_id,
                    epoch,
                    provider_session_ref,
                    state,
                    revoke_ref,
                    revoke_generation,
                    granted_wall,
                ),
            )
            db.execute(
                "INSERT INTO owner_session(account_key, generation, owner_id,"
                " provider_session_ref, at_ns) VALUES (?, ?, ?, ?, ?)",
                (self.account_key, generation, owner_id, provider_session_ref, granted_wall),
            )
            self._lease_token = holder_token
            self._lease_expiry_ns = now + self._policy.ttl_ns
            self._write(
                db,
                action="WRITER_LEASE_GRANTED",
                detail={
                    "owner": owner_id,
                    "host": host_id,
                    "epoch": epoch,
                    "state": state,
                    "orphaned_inflight": orphaned,
                },
            )
            return self._grant(self._lease_row(db), renewed=False)

    def renew_writer_lease(self, owner_id: str, *, holder_token: str) -> dict[str, Any]:
        with self._atomic() as db:
            row = self._held_row(db, owner_id, holder_token)
            now = self._mono()
            if self._lease_expiry_ns is None or now >= self._lease_expiry_ns:
                raise HandoffError("HANDOFF_WRITER_LEASE_EXPIRED")
            self._lease_expiry_ns = now + self._policy.ttl_ns
            return self._grant(row, renewed=True)

    def release_writer_lease(self, owner_id: str, *, holder_token: str) -> dict[str, Any]:
        """Give the lease up; a release with writes still in flight is unclean."""

        with self._atomic() as db:
            row = self._held_row(db, owner_id, holder_token)
            inflight = db.execute(
                "UPDATE dispatch_lease SET state = 'UNKNOWN', reason = 'WRITER_RELEASED_INFLIGHT'"
                " WHERE account_key = ? AND state = 'IN_FLIGHT'",
                (self.account_key,),
            ).rowcount
            released = _RELEASE_UNCLEAN if inflight else _RELEASE_CLEAN
            db.execute(
                "UPDATE writer_lease SET released = ? WHERE account_key = ?",
                (released, self.account_key),
            )
            self._lease_token = None
            self._lease_expiry_ns = None
            self._write(
                db,
                action="WRITER_LEASE_RELEASED",
                detail={"epoch": int(row["epoch"]), "clean": not inflight, "inflight": inflight},
            )
            return {"epoch": int(row["epoch"]), "clean": not inflight}

    def confirm_previous_session_revoked(
        self, owner_id: str, *, holder_token: str, evidence: object
    ) -> dict[str, Any]:
        """Lift ``PENDING_REVOCATION`` after verified previous-session revocation."""

        if type(evidence) is not ProviderSessionRevocationEvidence:
            raise HandoffError("HANDOFF_REVOCATION_EVIDENCE_INVALID")
        with self._atomic() as db:
            row = self._held_row(db, owner_id, holder_token)
            if row["state"] == "ACTIVE":
                return self._grant(row, renewed=False)
            assert self.binding is not None
            if (
                evidence.account_ref != self.binding.account_ref
                or evidence.provider_route != self.binding.provider_route
                or evidence.revoked_owner_generation != row["revoke_generation"]
                or evidence.revoked_session_ref != row["revoke_session_ref"]
            ):
                raise HandoffError("HANDOFF_REVOCATION_NOT_BOUND")
            if evidence.observed_at_ns < row["granted_wall_ns"]:
                raise HandoffError("HANDOFF_REVOCATION_EVIDENCE_STALE")
            if self._revocation_verifier is None:
                raise HandoffError("HANDOFF_NO_GO_REVOCATION_UNVERIFIABLE")
            epoch = row["epoch"]
        try:
            confirmed = self._revocation_verifier.verify_session_revocation(evidence) is True
        except Exception:
            confirmed = False
        if not confirmed:
            raise HandoffError("HANDOFF_NO_GO_REVOCATION_NOT_CONFIRMED")
        with self._atomic() as db:
            row = self._held_row(db, owner_id, holder_token)
            if row["epoch"] != epoch:
                raise HandoffError("HANDOFF_WRITER_LEASE_NOT_HELD")
            db.execute(
                "UPDATE writer_lease SET state = 'ACTIVE' WHERE account_key = ?",
                (self.account_key,),
            )
            self._write(
                db,
                action="WRITER_PREVIOUS_SESSION_REVOKED",
                detail={
                    "epoch": int(epoch),
                    "evidence_kind": evidence.kind,
                    "evidence_ref": evidence.evidence_ref,
                    "revoked_session_ref": evidence.revoked_session_ref,
                },
            )
            return self._grant(self._lease_row(db), renewed=False)

    # -- dispatch leases ----------------------------------------------------

    def may_dispatch_as(self, owner_id: str, generation: int, *, holder_token: str) -> bool:
        with self._atomic() as db:
            return self._dispatch_refusal(db, owner_id, generation, holder_token) is None

    def acquire_dispatch_as(
        self,
        owner_id: str,
        generation: int,
        *,
        holder_token: str,
        request_id: str,
        operation: str,
        intent_id: str,
    ) -> dict[str, Any]:
        """Grant one dispatch lease; a retried ``request_id`` gets the same lease."""

        if operation not in ("submit", "cancel"):
            raise HandoffError("HANDOFF_DISPATCH_OPERATION_INVALID")
        if not _text(intent_id, limit=512):
            raise HandoffError("HANDOFF_DISPATCH_INTENT_INVALID")
        if not _text(request_id) or not _text(holder_token):
            raise HandoffError("HANDOFF_DISPATCH_REQUEST_INVALID")
        refused: str | None = None
        with self._atomic() as db:
            existing = db.execute(
                "SELECT lease_id FROM dispatch_holder WHERE holder_token = ? AND request_id = ?",
                (holder_token, request_id),
            ).fetchone()
            if existing is not None:
                lease_id = int(existing[0])
                row = db.execute(
                    "SELECT owner_id, generation, operation, intent_id, state"
                    " FROM dispatch_lease WHERE lease_id = ? AND account_key = ?",
                    (lease_id, self.account_key),
                ).fetchone()
                if row is None or tuple(row[:4]) != (owner_id, generation, operation, intent_id):
                    raise HandoffError("HANDOFF_DISPATCH_REQUEST_CONFLICT")
                if row[4] != "IN_FLIGHT":
                    raise HandoffError("HANDOFF_DISPATCH_REQUEST_FINISHED")
                refusal = self._dispatch_refusal(db, owner_id, generation, holder_token)
                if refusal is not None:
                    # The caller never received the first grant, so nothing
                    # was sent: record it as such.
                    # (Raised after commit: raising here would roll it back.)
                    db.execute(
                        "UPDATE dispatch_lease SET state = 'NOT_SENT', finished_ns = ?"
                        " WHERE lease_id = ?",
                        (time.time_ns(), lease_id),
                    )
                    refused = refusal
                else:
                    return {"lease_id": lease_id, "max_dispatch_ns": self._policy.max_dispatch_ns}
            else:
                lease_id = self._grant_dispatch(
                    db, owner_id, generation, operation, intent_id, holder_token
                )
                db.execute(
                    "INSERT INTO dispatch_holder(lease_id, holder_token, request_id)"
                    " VALUES (?, ?, ?)",
                    (lease_id, holder_token, request_id),
                )
                return {"lease_id": lease_id, "max_dispatch_ns": self._policy.max_dispatch_ns}
        assert refused is not None
        raise HandoffError(refused)

    def finish_dispatch_as(self, lease_id: int, outcome: str, *, holder_token: str) -> str:
        if outcome not in DISPATCH_OUTCOMES:
            raise HandoffError("HANDOFF_DISPATCH_OUTCOME_INVALID")
        if type(lease_id) is not int:
            raise HandoffError("HANDOFF_DISPATCH_LEASE_UNKNOWN")
        with self._atomic() as db:
            holder = db.execute(
                "SELECT holder_token FROM dispatch_holder WHERE lease_id = ?", (lease_id,)
            ).fetchone()
        if holder is None or holder[0] != holder_token:
            raise HandoffError("HANDOFF_DISPATCH_LEASE_UNKNOWN")
        return self._finish_lease(lease_id, outcome)


# ---------------------------------------------------------------------------
# Wire
# ---------------------------------------------------------------------------


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def _mac(secret: bytes, direction: bytes, body: Mapping[str, Any]) -> str:
    message = AUTHORITY_PROTOCOL.encode("ascii") + b"\n" + direction + b"\n" + _canonical(body)
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def _send_frame(sock: socket.socket, value: Mapping[str, Any]) -> None:
    payload = _canonical(value)
    if len(payload) > MAX_FRAME_BYTES:
        raise HandoffError("HANDOFF_AUTHORITY_FRAME_TOO_LARGE")
    sock.sendall(_HEADER.pack(len(payload)) + payload)


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = sock.recv(size - len(chunks))
        if not chunk:
            raise ConnectionError("closed")
        chunks.extend(chunk)
    return bytes(chunks)


def _recv_frame(sock: socket.socket) -> dict[str, Any]:
    (size,) = _HEADER.unpack(_recv_exact(sock, _HEADER.size))
    if not 0 < size <= MAX_FRAME_BYTES:
        raise ValueError("frame size")
    value = json.loads(_recv_exact(sock, size).decode("ascii"))
    if type(value) is not dict:
        raise ValueError("frame shape")
    return value


_EVIDENCE_FIELDS = frozenset(field.name for field in fields(ProviderSessionRevocationEvidence))


def _evidence_to_wire(evidence: ProviderSessionRevocationEvidence) -> dict[str, Any]:
    return {name: getattr(evidence, name) for name in sorted(_EVIDENCE_FIELDS)}


def _evidence_from_wire(value: object) -> ProviderSessionRevocationEvidence | None:
    if value is None:
        return None
    if type(value) is not dict or set(value) != _EVIDENCE_FIELDS:
        raise HandoffError("HANDOFF_REVOCATION_EVIDENCE_INVALID")
    try:
        return ProviderSessionRevocationEvidence(**value)
    except Exception:
        raise HandoffError("HANDOFF_REVOCATION_EVIDENCE_INVALID") from None


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------


_ARGUMENTS: dict[str, dict[str, type | tuple[type, ...]]] = {
    "open": {"initial_owner": str},
    "status": {},
    "acquire_writer_lease": {
        "owner_id": str,
        "holder_token": str,
        "host_id": str,
        "provider_session_ref": str,
    },
    "renew_writer_lease": {"owner_id": str, "holder_token": str},
    "release_writer_lease": {"owner_id": str, "holder_token": str},
    "confirm_previous_session_revoked": {"owner_id": str, "holder_token": str, "evidence": dict},
    "may_dispatch": {"owner_id": str, "generation": int, "holder_token": str},
    "acquire_dispatch": {
        "owner_id": str,
        "generation": int,
        "holder_token": str,
        "request_id": str,
        "operation": str,
        "intent_id": str,
    },
    "finish_dispatch": {"lease_id": int, "outcome": str, "holder_token": str},
    "unresolved": {},
    "audit": {},
    "begin": {"from_owner": str, "to_owner": str},
    "fence": {
        "to_owner": str,
        "evidence": (dict, type(None)),
        "drain_timeout": (int, float),
    },
    "restore": {"to_owner": str, "ledger_cut": str},
    "sync": {"to_owner": str, "provider_snapshot_ref": str},
    "reconcile": {"to_owner": str, "reconciliation_state": str},
    "ready": {"to_owner": str},
}


class WriterAuthorityServer:
    """One authority process serving account writer leases over TCP.

    ``clients`` maps each client id to its HMAC secret (at least 32 bytes).
    ``allowed_bindings`` optionally restricts a client to binding digests.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        clients: Mapping[str, bytes],
        policy: WriterLeasePolicy | None = None,
        revocation_verifier: object | None = None,
        allowed_bindings: Mapping[str, frozenset[str]] | None = None,
        host: str = "127.0.0.1",
        port: int = 0,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
        wall_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        if not isinstance(clients, Mapping) or not clients:
            raise HandoffError("HANDOFF_AUTHORITY_CLIENTS_INVALID")
        for client_id, secret in clients.items():
            if not _text(client_id) or type(secret) is not bytes or len(secret) < 32:
                raise HandoffError("HANDOFF_AUTHORITY_CLIENTS_INVALID")
        self._clients = dict(clients)
        self._allowed = None if allowed_bindings is None else dict(allowed_bindings)
        self._policy = policy or WriterLeasePolicy()
        self._verifier = revocation_verifier
        self._root = Path(root)
        self._mono = monotonic_ns
        self._wall = wall_ns
        self._engines: dict[str, AuthorityHostedHandoff] = {}
        self._engines_lock = threading.Lock()
        self._nonces: dict[str, OrderedDict[str, int]] = {}
        self._nonce_lock = threading.Lock()
        self._bind = (host, port)
        self._server: socketserver.ThreadingTCPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def address(self) -> tuple[str, int]:
        if self._server is None:
            raise HandoffError("HANDOFF_AUTHORITY_NOT_STARTED")
        host, port = self._server.server_address[:2]
        return str(host), int(port)

    def start(self) -> tuple[str, int]:
        authority = self

        class _Handler(socketserver.BaseRequestHandler):
            def handle(self) -> None:  # pragma: no cover - exercised over sockets
                authority._serve_connection(self.request)

        class _Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self._server = _Server(self._bind, _Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="writer-authority", daemon=True
        )
        self._thread.start()
        return self.address

    def stop(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.shutdown()
            server.server_close()
        with self._engines_lock:
            engines, self._engines = self._engines, {}
        for engine in engines.values():
            engine.close()

    # -- request handling ---------------------------------------------------

    def _serve_connection(self, sock: socket.socket) -> None:
        sock.settimeout(310.0)
        while True:
            try:
                request = _recv_frame(sock)
            except Exception:
                return
            reply = self._handle(request)
            if reply is None:
                return
            try:
                _send_frame(sock, reply)
            except Exception:
                return

    def _handle(self, request: dict[str, Any]) -> dict[str, Any] | None:
        client_id = request.get("client")
        secret = self._clients.get(client_id) if type(client_id) is str else None
        mac = request.get("mac")
        if secret is None or type(mac) is not str:
            return None  # unauthenticated: drop the connection, no reply
        body = {key: value for key, value in request.items() if key != "mac"}
        if not hmac.compare_digest(mac, _mac(secret, b"req", body)):
            return None
        nonce = body.get("nonce")
        reply_body: dict[str, Any] = {"v": 1, "nonce": nonce, "ok": False, "code": None}
        try:
            self._check_replay(client_id, nonce, body.get("ts_ns"))
            if body.get("v") != 1 or set(body) != {
                "v",
                "client",
                "nonce",
                "ts_ns",
                "op",
                "binding",
                "args",
            }:
                raise HandoffError("HANDOFF_AUTHORITY_REQUEST_INVALID")
            reply_body["result"] = self._dispatch(client_id, body)
            reply_body["ok"] = True
        except HandoffError as error:
            reply_body["code"] = error.code
        except Exception:
            reply_body["code"] = "HANDOFF_AUTHORITY_FAILED"
        reply_body.setdefault("result", None)
        return {**reply_body, "mac": _mac(secret, b"rep", reply_body)}

    def _check_replay(self, client_id: str, nonce: object, ts_ns: object) -> None:
        if not _text(nonce, limit=64) or type(ts_ns) is not int:
            raise HandoffError("HANDOFF_AUTHORITY_REQUEST_INVALID")
        now = self._wall()
        if abs(now - ts_ns) > _REPLAY_WINDOW_NS:
            raise HandoffError("HANDOFF_AUTHORITY_REQUEST_STALE")
        with self._nonce_lock:
            seen = self._nonces.setdefault(client_id, OrderedDict())
            # Forget a nonce only once its timestamp can no longer pass the
            # window check; never evict a live one (that would allow replay).
            while seen and next(iter(seen.values())) < now - 2 * _REPLAY_WINDOW_NS:
                seen.popitem(last=False)
            if nonce in seen:
                raise HandoffError("HANDOFF_AUTHORITY_REQUEST_REPLAYED")
            if len(seen) >= _MAX_NONCES_PER_CLIENT:
                raise HandoffError("HANDOFF_AUTHORITY_RATE_LIMITED")
            seen[nonce] = now

    def _binding(self, client_id: str, value: object) -> AccountCoordinationBinding:
        if type(value) is not dict or set(value) != {"account_ref", "provider_route"}:
            raise HandoffError("HANDOFF_BINDING_INVALID")
        try:
            binding = AccountCoordinationBinding(value["account_ref"], value["provider_route"])
        except CoordinationError:
            raise HandoffError("HANDOFF_BINDING_INVALID") from None
        if self._allowed is not None and binding.digest not in self._allowed.get(
            client_id, frozenset()
        ):
            raise HandoffError("HANDOFF_AUTHORITY_BINDING_DENIED")
        return binding

    def _engine(
        self, binding: AccountCoordinationBinding, initial_owner: str | None
    ) -> AuthorityHostedHandoff:
        with self._engines_lock:
            engine = self._engines.get(binding.digest)
            if engine is not None:
                return engine
            path = canonical_coordination_path(self._root, binding)
            if initial_owner is None:
                if not path.exists():
                    raise HandoffError("HANDOFF_AUTHORITY_ACCOUNT_UNKNOWN")
                initial_owner = "unused-existing-store"
            path.parent.mkdir(parents=True, exist_ok=True)
            engine = AuthorityHostedHandoff(
                path,
                binding,
                initial_owner=initial_owner,
                policy=self._policy,
                revocation_verifier=self._verifier,
                monotonic_ns=self._mono,
                wall_ns=self._wall,
            )
            self._engines[binding.digest] = engine
            return engine

    def _dispatch(self, client_id: str, body: dict[str, Any]) -> Any:
        op = body["op"]
        spec = _ARGUMENTS.get(op) if type(op) is str else None
        args = body["args"]
        if spec is None or type(args) is not dict or set(args) != set(spec):
            raise HandoffError("HANDOFF_AUTHORITY_REQUEST_INVALID")
        for name, expected in spec.items():
            value = args[name]
            if type(value) is bool or not isinstance(value, expected):
                raise HandoffError("HANDOFF_AUTHORITY_REQUEST_INVALID")
        binding = self._binding(client_id, body["binding"])
        if op == "open":
            if not _text(args["initial_owner"]):
                raise HandoffError("HANDOFF_IDENTITY_INVALID")
            engine = self._engine(binding, args["initial_owner"])
            return self._status(engine)
        engine = self._engine(binding, None)
        if op == "status":
            return self._status(engine)
        if op == "acquire_writer_lease":
            return engine.acquire_writer_lease(
                args["owner_id"],
                holder_token=args["holder_token"],
                host_id=args["host_id"],
                provider_session_ref=args["provider_session_ref"],
            )
        if op == "renew_writer_lease":
            return engine.renew_writer_lease(args["owner_id"], holder_token=args["holder_token"])
        if op == "release_writer_lease":
            return engine.release_writer_lease(args["owner_id"], holder_token=args["holder_token"])
        if op == "confirm_previous_session_revoked":
            return engine.confirm_previous_session_revoked(
                args["owner_id"],
                holder_token=args["holder_token"],
                evidence=_evidence_from_wire(args["evidence"]),
            )
        if op == "may_dispatch":
            return engine.may_dispatch_as(
                args["owner_id"], args["generation"], holder_token=args["holder_token"]
            )
        if op == "acquire_dispatch":
            return engine.acquire_dispatch_as(
                args["owner_id"],
                args["generation"],
                holder_token=args["holder_token"],
                request_id=args["request_id"],
                operation=args["operation"],
                intent_id=args["intent_id"],
            )
        if op == "finish_dispatch":
            return engine.finish_dispatch_as(
                args["lease_id"], args["outcome"], holder_token=args["holder_token"]
            )
        if op == "unresolved":
            return [list(vars(item).values()) for item in engine.unresolved_dispatches()]
        if op == "audit":
            return [[action, detail] for action, detail in engine.audit()]
        if op == "begin":
            engine.begin(from_owner=args["from_owner"], to_owner=args["to_owner"])
            return None
        if op == "fence":
            engine.fence(
                to_owner=args["to_owner"],
                evidence=_evidence_from_wire(args["evidence"]),
                drain_timeout=float(args["drain_timeout"]),
            )
            return None
        if op == "restore":
            engine.restore(to_owner=args["to_owner"], ledger_cut=args["ledger_cut"])
            return None
        if op == "sync":
            engine.sync(
                to_owner=args["to_owner"], provider_snapshot_ref=args["provider_snapshot_ref"]
            )
            return None
        if op == "reconcile":
            engine.reconcile(
                to_owner=args["to_owner"], reconciliation_state=args["reconciliation_state"]
            )
            return None
        if op == "ready":
            return list(engine.ready(to_owner=args["to_owner"]))
        raise HandoffError("HANDOFF_AUTHORITY_REQUEST_INVALID")  # pragma: no cover

    @staticmethod
    def _status(engine: AuthorityHostedHandoff) -> dict[str, Any]:
        return engine.writer_lease_status()


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class RemoteAccountOwnerHandoff:
    """An owner's handle on the writer authority for one account.

    Exposes the account-owner surface that Brokers and the SDK dispatch path
    use (``owner``, ``stage``, ``holds_owner_lock``, ``may_dispatch``,
    ``acquire_dispatch_lease``, ``unresolved_dispatches`` and the handoff
    stages). ``hold_owner_lock`` takes the cross-host writer lease; a keeper
    thread renews it. The local deadline (send time of the last successful
    grant plus the TTL, reduced by a clock-rate margin) is only an early local
    refusal: the authority itself refuses every dispatch outside the lease.
    Any authority failure refuses the dispatch (nothing is sent).
    """

    def __init__(
        self,
        address: tuple[str, int],
        *,
        client_id: str,
        secret: bytes,
        binding: AccountCoordinationBinding,
        initial_owner: str,
        host_id: str,
        request_timeout: float = 5.0,
        renew_interval: float | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        wall_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        if type(binding) is not AccountCoordinationBinding:
            raise HandoffError("HANDOFF_BINDING_INVALID")
        if not _text(client_id) or type(secret) is not bytes or len(secret) < 32:
            raise HandoffError("HANDOFF_AUTHORITY_CLIENTS_INVALID")
        if not _text(host_id) or not _text(initial_owner):
            raise HandoffError("HANDOFF_IDENTITY_INVALID")
        if type(request_timeout) not in (int, float) or not 0 < request_timeout <= 60:
            raise HandoffError("HANDOFF_AUTHORITY_TIMEOUT_INVALID")
        if renew_interval is not None and (
            type(renew_interval) not in (int, float) or not 0 < renew_interval <= 60
        ):
            raise HandoffError("HANDOFF_AUTHORITY_TIMEOUT_INVALID")
        host, port = address
        self._address = (str(host), int(port))
        self._client_id = client_id
        self._secret = secret
        self.binding = binding
        self.account_key = binding.account_ref
        self._host_id = host_id
        self._timeout = float(request_timeout)
        self._renew_interval = renew_interval
        self._mono = monotonic
        self._wall = wall_ns
        self._holder_token = secrets.token_hex(16)
        self._sock: socket.socket | None = None
        self._io_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._lease_owner: str | None = None
        self._session_ref: str | None = None
        self._deadline: float | None = None
        self._lease_state: str | None = None
        self._ttl_s = 0.0
        self._max_dispatch_s = 0.0
        self._keeper: threading.Thread | None = None
        self._stop = threading.Event()
        self._call("open", initial_owner=initial_owner)

    # -- transport ----------------------------------------------------------

    def _close_socket(self) -> None:
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def _call(self, op: str, **args: Any) -> Any:
        body = {
            "v": 1,
            "client": self._client_id,
            "nonce": uuid.uuid4().hex,
            "ts_ns": int(self._wall()),
            "op": op,
            "binding": {
                "account_ref": self.binding.account_ref,
                "provider_route": self.binding.provider_route,
            },
            "args": args,
        }
        request = {**body, "mac": _mac(self._secret, b"req", body)}
        timeout = self._timeout
        if op == "fence":
            timeout += float(args.get("drain_timeout", 0))
        with self._io_lock:
            try:
                if self._sock is None:
                    self._sock = socket.create_connection(self._address, timeout=timeout)
                self._sock.settimeout(timeout)
                _send_frame(self._sock, request)
                reply = _recv_frame(self._sock)
            except HandoffError:
                self._close_socket()
                raise
            except Exception:
                self._close_socket()
                raise HandoffError("HANDOFF_AUTHORITY_UNAVAILABLE") from None
        mac = reply.get("mac")
        reply_body = {key: value for key, value in reply.items() if key != "mac"}
        if (
            type(mac) is not str
            or not hmac.compare_digest(mac, _mac(self._secret, b"rep", reply_body))
            or reply_body.get("nonce") != body["nonce"]
            or set(reply_body) != {"v", "nonce", "ok", "code", "result"}
        ):
            with self._io_lock:
                self._close_socket()
            raise HandoffError("HANDOFF_AUTHORITY_REPLY_INVALID")
        if reply_body["ok"] is not True:
            code = reply_body.get("code")
            raise HandoffError(code if _text(code) else "HANDOFF_AUTHORITY_FAILED")
        return reply_body["result"]

    # -- state --------------------------------------------------------------

    def _status(self) -> dict[str, Any]:
        result = self._call("status")
        if type(result) is not dict:
            raise HandoffError("HANDOFF_AUTHORITY_REPLY_INVALID")
        return result

    @property
    def owner(self) -> tuple[str, int]:
        status = self._status()
        return status["owner"], int(status["generation"])

    @property
    def stage(self) -> str | None:
        return self._status()["stage"]

    @property
    def writer_lease_state(self) -> str | None:
        with self._state_lock:
            return self._lease_state if self._locally_live() else None

    def _locally_live(self) -> bool:
        return self._deadline is not None and self._mono() < self._deadline

    @property
    def holds_owner_lock(self) -> str | None:
        """The owner whose writer lease this handle holds and has not seen expire."""

        with self._state_lock:
            return self._lease_owner if self._locally_live() else None

    def audit(self) -> list[tuple[str, dict]]:
        return [(action, detail) for action, detail in self._call("audit")]

    # -- writer lease -------------------------------------------------------

    def _apply_grant(self, grant: object, sent_at: float, owner_id: str) -> None:
        if type(grant) is not dict or grant.get("state") not in _LEASE_STATES:
            raise HandoffError("HANDOFF_AUTHORITY_REPLY_INVALID")
        ttl_s = int(grant["ttl_ns"]) / 1_000_000_000
        with self._state_lock:
            self._ttl_s = ttl_s
            self._max_dispatch_s = int(grant["max_dispatch_ns"]) / 1_000_000_000
            self._deadline = sent_at + ttl_s * (1 - _CLOCK_RATE_MARGIN)
            self._lease_owner = owner_id
            self._lease_state = grant["state"]

    def hold_owner_lock(self, owner_id: str, *, provider_session_ref: str) -> None:
        """Take the cross-host writer lease for ``owner_id``."""

        sent_at = self._mono()
        grant = self._call(
            "acquire_writer_lease",
            owner_id=owner_id,
            holder_token=self._holder_token,
            host_id=self._host_id,
            provider_session_ref=provider_session_ref,
        )
        self._session_ref = provider_session_ref
        self._apply_grant(grant, sent_at, owner_id)
        if self._renew_interval is not None and self._keeper is None:
            self._stop.clear()
            self._keeper = threading.Thread(
                target=self._keep, name="writer-lease-keeper", daemon=True
            )
            self._keeper.start()

    def renew_writer_lease(self) -> None:
        owner_id = self._lease_owner
        if owner_id is None:
            raise HandoffError("HANDOFF_WRITER_LEASE_NOT_HELD")
        sent_at = self._mono()
        try:
            grant = self._call(
                "renew_writer_lease", owner_id=owner_id, holder_token=self._holder_token
            )
        except HandoffError as error:
            if error.code != "HANDOFF_WRITER_LEASE_EXPIRED" or self._session_ref is None:
                raise
            # Our own lease expired; resume only if nobody else took it.
            grant = self._call(
                "acquire_writer_lease",
                owner_id=owner_id,
                holder_token=self._holder_token,
                host_id=self._host_id,
                provider_session_ref=self._session_ref,
            )
        self._apply_grant(grant, sent_at, owner_id)

    def _keep(self) -> None:
        assert self._renew_interval is not None
        while not self._stop.wait(self._renew_interval):
            try:
                self.renew_writer_lease()
            except HandoffError as error:
                if error.code in {"HANDOFF_WRITER_LEASE_NOT_HELD", "HANDOFF_WRITER_LEASE_HELD"}:
                    with self._state_lock:
                        self._deadline = None
                        self._lease_owner = None
                    return

    def confirm_previous_session_revoked(
        self, evidence: ProviderSessionRevocationEvidence
    ) -> None:
        if type(evidence) is not ProviderSessionRevocationEvidence:
            raise HandoffError("HANDOFF_REVOCATION_EVIDENCE_INVALID")
        owner_id = self._lease_owner
        if owner_id is None:
            raise HandoffError("HANDOFF_WRITER_LEASE_NOT_HELD")
        sent_at = self._mono()
        grant = self._call(
            "confirm_previous_session_revoked",
            owner_id=owner_id,
            holder_token=self._holder_token,
            evidence=_evidence_to_wire(evidence),
        )
        self._apply_grant(grant, sent_at, owner_id)

    def release_owner_lock(self) -> None:
        self._stop.set()
        keeper, self._keeper = self._keeper, None
        if keeper is not None and keeper is not threading.current_thread():
            keeper.join(timeout=self._timeout + 1)
        owner_id = self._lease_owner
        with self._state_lock:
            self._deadline = None
            self._lease_owner = None
            self._lease_state = None
        if owner_id is not None:
            try:
                self._call(
                    "release_writer_lease", owner_id=owner_id, holder_token=self._holder_token
                )
            except HandoffError:
                pass  # the lease then simply expires on the authority

    # -- dispatch -----------------------------------------------------------

    def may_dispatch(self, owner_id: str, generation: int) -> bool:
        if self.holds_owner_lock != owner_id:
            return False
        try:
            return (
                self._call(
                    "may_dispatch",
                    owner_id=owner_id,
                    generation=generation,
                    holder_token=self._holder_token,
                )
                is True
            )
        except HandoffError:
            return False

    def acquire_dispatch_lease(
        self, owner_id: str, generation: int, *, operation: str, intent_id: str
    ) -> DispatchLease:
        """Authority-granted lease for one provider write; refusal = nothing sent."""

        with self._state_lock:
            live = (
                self._lease_owner == owner_id
                and self._deadline is not None
                and self._mono() + self._max_dispatch_s < self._deadline
            )
        if not live:
            raise HandoffError("HANDOFF_WRITER_LEASE_NOT_HELD")
        request_id = uuid.uuid4().hex
        sent_at = self._mono()
        arguments = {
            "owner_id": owner_id,
            "generation": generation,
            "holder_token": self._holder_token,
            "request_id": request_id,
            "operation": operation,
            "intent_id": intent_id,
        }
        try:
            granted = self._call("acquire_dispatch", **arguments)
        except HandoffError as error:
            if error.code != "HANDOFF_AUTHORITY_UNAVAILABLE":
                raise
            # One retry with the same request id: a lost reply yields the
            # same lease (or records it NOT_SENT if no longer allowed).
            granted = self._call("acquire_dispatch", **arguments)
        if type(granted) is not dict or type(granted.get("lease_id")) is not int:
            raise HandoffError("HANDOFF_AUTHORITY_REPLY_INVALID")
        lease = DispatchLease(self, granted["lease_id"], owner_id, generation, operation, intent_id)
        lease.deadline_monotonic = sent_at + (
            int(granted["max_dispatch_ns"]) / 1_000_000_000
        ) * (1 - _CLOCK_RATE_MARGIN)
        return lease

    def _finish_lease(self, lease_id: int, outcome: str) -> str:
        state = self._call(
            "finish_dispatch",
            lease_id=lease_id,
            outcome=outcome,
            holder_token=self._holder_token,
        )
        if type(state) is not str:
            raise HandoffError("HANDOFF_AUTHORITY_REPLY_INVALID")
        return state

    def unresolved_dispatches(self) -> tuple[UnresolvedDispatch, ...]:
        rows = self._call("unresolved")
        return tuple(UnresolvedDispatch(*row) for row in rows)

    # -- handoff stages (executed by the authority) -------------------------

    def begin(self, *, from_owner: str, to_owner: str) -> None:
        self._call("begin", from_owner=from_owner, to_owner=to_owner)

    def fence(
        self,
        *,
        to_owner: str,
        evidence: ProviderSessionRevocationEvidence | None,
        drain_timeout: float = 5.0,
    ) -> None:
        self._call(
            "fence",
            to_owner=to_owner,
            evidence=None if evidence is None else _evidence_to_wire(evidence),
            drain_timeout=drain_timeout,
        )

    def restore(self, *, to_owner: str, ledger_cut: str) -> None:
        self._call("restore", to_owner=to_owner, ledger_cut=ledger_cut)

    def sync(self, *, to_owner: str, provider_snapshot_ref: str) -> None:
        self._call("sync", to_owner=to_owner, provider_snapshot_ref=provider_snapshot_ref)

    def reconcile(self, *, to_owner: str, reconciliation_state: str) -> None:
        self._call("reconcile", to_owner=to_owner, reconciliation_state=reconciliation_state)

    def ready(self, *, to_owner: str) -> tuple[str, int]:
        owner, generation = self._call("ready", to_owner=to_owner)
        return owner, int(generation)

    def close(self) -> None:
        self.release_owner_lock()
        with self._io_lock:
            self._close_socket()
