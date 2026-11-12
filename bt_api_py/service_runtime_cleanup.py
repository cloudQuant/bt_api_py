"""Shared, conservative inspection of a stopped SDK client runtime."""

from __future__ import annotations

from typing import Any


def runtime_cleanup_confirmed(snapshot: Any) -> bool:
    """Allow replacement only after local and remote cleanup is established."""
    return (
        getattr(snapshot, "state", None) in ("closed", "failed")
        and getattr(snapshot, "thread_alive", None) is False
        and getattr(snapshot, "error_code", "UNPROVEN") in
            (None, "MAINTENANCE_FAILED", "START_FAILED", "START_TIMEOUT")
        and type(getattr(snapshot, "unregister_uncertainties", None)) is int
        and snapshot.unregister_uncertainties == 0
        and type(getattr(snapshot, "owner_cleanup_uncertainties", None)) is int
        and snapshot.owner_cleanup_uncertainties == 0
    )
