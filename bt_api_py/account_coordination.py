"""Account-bound coordination location and host owner lock (iteration 60, G1).

A coordination database path by itself says nothing about which provider
account it governs: two processes given two different files could each
believe they own the same account. This module binds coordination to the
account identity instead:

* :class:`AccountCoordinationBinding` names the account by its trusted
  binding (``account_ref`` plus the provider route that authenticated it).
  Its digest selects a canonical per-account coordination location.
* :class:`HostAccountRegistry` keeps one host-wide registration per binding.
  The first coordination database registered for a binding is the only one
  accepted on this host; a second database for the same account is refused.
* The registry also provides the host owner lock: an OS advisory lock
  (``fcntl.flock`` on POSIX, ``msvcrt.locking`` on Windows) held by the owner
  process for its lifetime. Two processes on one host cannot both hold it,
  and the OS releases it when the holder dies.

Limits (kept NO-GO, see 60.10 delivery notes): the registry is host-local and
per OS user. Two hosts, or two OS users with different registry roots, are not
coordinated by this module. Cross-host single-writer needs provider-side
proof (session revocation or exchange-side session limits).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "AccountCoordinationBinding",
    "CoordinationError",
    "HostAccountRegistry",
    "HostOwnerLock",
    "canonical_coordination_path",
]

_DIGEST_SCHEMA = "bt_api_py.account_coordination.v1"


class CoordinationError(RuntimeError):
    """Finite, value-free coordination failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _text(value: object) -> bool:
    return type(value) is str and bool(value) and value == value.strip()


@dataclass(frozen=True)
class AccountCoordinationBinding:
    """Trusted account identity that a coordination store is bound to.

    ``account_ref`` is the configured account reference and ``provider_route``
    the provider route identity (for example a provider name plus front or
    broker id) that authenticated it. Both come from deployment configuration
    that was verified at login; this class does not authenticate them.
    """

    account_ref: str
    provider_route: str

    def __post_init__(self) -> None:
        if not _text(self.account_ref) or not _text(self.provider_route):
            raise CoordinationError("COORDINATION_BINDING_INVALID")

    @property
    def digest(self) -> str:
        payload = json.dumps(
            [_DIGEST_SCHEMA, self.account_ref, self.provider_route],
            separators=(",", ":"),
            ensure_ascii=True,
        )
        return hashlib.sha256(payload.encode("ascii")).hexdigest()


def canonical_coordination_path(root: str | Path, binding: AccountCoordinationBinding) -> Path:
    """Return the canonical coordination database for ``binding`` under ``root``."""

    if type(binding) is not AccountCoordinationBinding:
        raise CoordinationError("COORDINATION_BINDING_INVALID")
    return Path(root) / "accounts" / binding.digest[:32] / "owner.sqlite3"


def _try_lock(handle) -> bool:
    if os.name == "nt":  # pragma: no cover - exercised on Windows only
        import msvcrt

        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(handle) -> None:
    if os.name == "nt":  # pragma: no cover - exercised on Windows only
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class HostOwnerLock:
    """An acquired host owner lock; released by :meth:`release` or process exit."""

    def __init__(self, handle, digest: str) -> None:
        self._handle = handle
        self.digest = digest

    @property
    def held(self) -> bool:
        return self._handle is not None

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            _unlock(handle)
        finally:
            handle.close()


class HostAccountRegistry:
    """Host-wide registration of coordination stores and owner locks."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    @classmethod
    def default(cls) -> HostAccountRegistry:
        """Per-user host registry. Deployments must not override it per process."""

        return cls(Path.home() / ".bt_api_py" / "account-owner-host")

    def _ensure_root(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root

    def register_coordinator(self, binding: AccountCoordinationBinding, path: str | Path) -> Path:
        """Accept ``path`` only if it is (or becomes) this host's store for ``binding``.

        Registration is created atomically (hard link of a complete temporary
        file); a concurrent or later registration with a different database
        path is refused with ``COORDINATION_STORE_CONFLICT``.
        """

        if type(binding) is not AccountCoordinationBinding:
            raise CoordinationError("COORDINATION_BINDING_INVALID")
        resolved = str(Path(path).resolve())
        record = json.dumps({"binding": binding.digest, "path": resolved}, sort_keys=True).encode(
            "utf-8"
        )
        target = self._ensure_root() / f"{binding.digest}.coordinator"
        if not target.exists():
            temporary = self.root / f".{binding.digest}.{uuid.uuid4().hex}.tmp"
            try:
                with open(temporary, "xb") as handle:
                    handle.write(record)
                    handle.flush()
                    os.fsync(handle.fileno())
                with contextlib.suppress(FileExistsError):
                    os.link(temporary, target)
            finally:
                with contextlib.suppress(FileNotFoundError):
                    temporary.unlink()
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise CoordinationError("COORDINATION_REGISTRY_UNREADABLE") from None
        if existing.get("binding") != binding.digest:
            raise CoordinationError("COORDINATION_REGISTRY_UNREADABLE")
        if existing.get("path") != resolved:
            raise CoordinationError("COORDINATION_STORE_CONFLICT")
        return Path(resolved)

    def acquire_owner_lock(self, binding: AccountCoordinationBinding) -> HostOwnerLock:
        """Take the exclusive host owner lock without waiting."""

        if type(binding) is not AccountCoordinationBinding:
            raise CoordinationError("COORDINATION_BINDING_INVALID")
        path = self._ensure_root() / f"{binding.digest}.owner.lock"
        handle = open(path, "a+b")  # noqa: SIM115 - held for the owner lifetime
        try:
            if not _try_lock(handle):
                raise CoordinationError("HOST_OWNER_LOCK_HELD")
        except BaseException:
            handle.close()
            raise
        return HostOwnerLock(handle, binding.digest)
