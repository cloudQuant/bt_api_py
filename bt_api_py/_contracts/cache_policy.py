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

    def __contains__(self, key: str) -> bool:
        return key in self._entries
