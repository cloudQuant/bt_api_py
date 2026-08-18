"""Freshness semantics tests (Task 1.2)."""

from __future__ import annotations

from datetime import UTC, datetime

from bt_api_py._contracts.cache_policy import CachePolicy
from bt_api_py._contracts.models import Consistency, Freshness


def test_cache_policy_marks_stale_on_failure_fallback() -> None:
    policy = CachePolicy()
    policy.put("k", {"cash": 1.0}, stale_reason="transport down")

    entry = policy.get("k")
    assert entry is not None
    assert entry.value == {"cash": 1.0}
    assert entry.freshness.stale is True
    assert entry.freshness.source == "cache"
    assert entry.freshness.stale_reason == "transport down"


def test_cache_policy_returns_none_for_missing_key() -> None:
    policy = CachePolicy()
    assert policy.get("missing") is None


def test_freshness_live_is_not_stale() -> None:
    freshness = Freshness(source="live", observed_at=datetime.now(UTC))
    assert freshness.stale is False


def test_consistency_cache_ok_allows_cache() -> None:
    assert Consistency.CACHE_OK.value == "cache_ok"
