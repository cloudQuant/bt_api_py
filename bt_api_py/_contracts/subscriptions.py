"""Subscription handle contract (Task 1.3)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SubscriptionHandle:
    """Opaque handle for an accepted upstream market-data subscription."""

    id: str
    exchange_name: str
    symbols: list[str]
    topics: list[str]
    account_id: str | None = None
