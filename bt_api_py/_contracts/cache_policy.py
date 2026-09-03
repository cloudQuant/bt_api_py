"""Cache policy for CACHE_OK query semantics (Task 1.2)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from bt_api_py._contracts.models import Freshness


@dataclass(frozen=True)
class CacheEntry:
    value: object
    freshness: Freshness


class CachePolicy:
    """In-memory cache keyed by operation scope.

    Entries written here are always marked stale — they are fallback snapshots,
    never substitutes for a live query.
    """

    def __init__(self) -> None:
        self._entries: dict[str, CacheEntry] = {}

    def put(self, key: str, value: object, *, stale_reason: str | None = None) -> None:
        self._entries[key] = CacheEntry(
            value=value,
            freshness=Freshness(
                source="cache",
                observed_at=datetime.now(UTC),
                stale=True,
                stale_reason=stale_reason,
            ),
        )

    def get(self, key: str) -> CacheEntry | None:
        return self._entries.get(key)

    def get_within_age(self, key: str, max_age_ms: int) -> CacheEntry | None:
        """Return an entry only while its recorded observation remains usable."""
        entry = self.get(key)
        if entry is None:
            return None
        age_ms = (datetime.now(UTC) - entry.freshness.observed_at).total_seconds() * 1000
        if age_ms > max_age_ms:
            return None
        return entry

    def __contains__(self, key: str) -> bool:
        return key in self._entries
