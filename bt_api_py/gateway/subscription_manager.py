"""SubscriptionManager — reference-counted symbol subscription tracker.

Maintains per-strategy subscription sets and computes the effective
subscription set (union) that should be forwarded to the adapter.
Supports reconnect-recovery by returning the full active set.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from typing import Any


class SubscriptionManager:
    """Thread-safe reference-counted subscription manager.

    Usage::

        mgr = SubscriptionManager()
        added = mgr.add("strat-1", ["BTCUSDT", "ETHUSDT"])
        # added == {"BTCUSDT", "ETHUSDT"}  (first time)

        added2 = mgr.add("strat-2", ["BTCUSDT", "SOLUSDT"])
        # added2 == {"SOLUSDT"}  (BTCUSDT already subscribed)

        removed = mgr.remove("strat-1", ["BTCUSDT", "ETHUSDT"])
        # removed == {"ETHUSDT"}  (BTCUSDT still held by strat-2)
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # strategy_id -> set of symbols
        self._strategy_symbols: dict[str, set[str]] = defaultdict(set)
        # symbol -> reference count
        self._ref_counts: dict[str, int] = defaultdict(int)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add(self, strategy_id: str, symbols: list[str] | set[str]) -> set[str]:
        """Register *symbols* for *strategy_id*.

        Returns:
            The subset of *symbols* that are **newly** subscribed
            (ref_count went from 0 → 1).
        """
        newly_subscribed: set[str] = set()
        with self._lock:
            for sym in symbols:
                if sym not in self._strategy_symbols[strategy_id]:
                    self._strategy_symbols[strategy_id].add(sym)
                    self._ref_counts[sym] += 1
                    if self._ref_counts[sym] == 1:
                        newly_subscribed.add(sym)
        return newly_subscribed

    def remove(self, strategy_id: str, symbols: list[str] | set[str]) -> set[str]:
        """Unregister *symbols* for *strategy_id*.

        Returns:
            The subset of *symbols* whose ref_count dropped to 0
            (should be unsubscribed from the adapter).
        """
        to_unsubscribe: set[str] = set()
        with self._lock:
            strat_set = self._strategy_symbols.get(strategy_id)
            if strat_set is None:
                return to_unsubscribe
            for sym in symbols:
                if sym in strat_set:
                    strat_set.discard(sym)
                    self._ref_counts[sym] -= 1
                    if self._ref_counts[sym] <= 0:
                        self._ref_counts.pop(sym, None)
                        to_unsubscribe.add(sym)
            if not strat_set:
                del self._strategy_symbols[strategy_id]
        return to_unsubscribe

    def remove_strategy(self, strategy_id: str) -> set[str]:
        """Remove all subscriptions for *strategy_id*.

        Returns:
            Symbols whose ref_count dropped to 0.
        """
        with self._lock:
            strat_set = self._strategy_symbols.pop(strategy_id, set())
            to_unsubscribe: set[str] = set()
            for sym in strat_set:
                self._ref_counts[sym] -= 1
                if self._ref_counts[sym] <= 0:
                    self._ref_counts.pop(sym, None)
                    to_unsubscribe.add(sym)
        return to_unsubscribe

    def get_active_symbols(self) -> set[str]:
        """Return the full set of currently subscribed symbols."""
        with self._lock:
            return set(self._ref_counts.keys())

    def get_strategy_symbols(self, strategy_id: str) -> set[str]:
        """Return the symbols subscribed by *strategy_id*."""
        with self._lock:
            return set(self._strategy_symbols.get(strategy_id, set()))

    def get_strategies(self) -> list[str]:
        """Return all registered strategy IDs."""
        with self._lock:
            return list(self._strategy_symbols.keys())

    def ref_count(self, symbol: str) -> int:
        """Return the current reference count for *symbol*."""
        with self._lock:
            return self._ref_counts.get(symbol, 0)

    @property
    def symbol_count(self) -> int:
        """Number of distinct active symbols."""
        with self._lock:
            return len(self._ref_counts)

    @property
    def strategy_count(self) -> int:
        """Number of registered strategies."""
        with self._lock:
            return len(self._strategy_symbols)

    def snapshot(self) -> dict[str, Any]:
        """Return a serialisable snapshot for health / diagnostics."""
        with self._lock:
            return {
                "strategy_count": len(self._strategy_symbols),
                "symbol_count": len(self._ref_counts),
                "strategies": {sid: sorted(syms) for sid, syms in self._strategy_symbols.items()},
                "ref_counts": dict(self._ref_counts),
            }
