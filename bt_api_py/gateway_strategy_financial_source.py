"""Explicit owner-local source for verified strategy financial snapshots.

Only trusted deployment code may construct this port.  Configuration and wire
requests cannot supply its callbacks or turn unverified numbers into facts.
"""
from __future__ import annotations

import os
import threading
from contextlib import contextmanager

from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.session_guard import LocalAccountSessionGuard
from bt_api_gateway.strategy_financial_ledger import StrategyFinancialSnapshot


class GatewayStrategyFinancialSourceError(RuntimeError):
    """Finite source lifecycle failure without disclosing source details."""


class GatewayStrategyFinancialSource:
    """Install and maintain a trusted local same-cut source on the owner thread.

    ``collect`` returns a bounded tuple of verified snapshots. ``source_fence``
    holds the independent source cut across a journal transaction and yields a
    checker which must return literal True only while *all* required financial
    inputs, coverage, identity, and cut remain current.  An empty collection
    never creates a snapshot.  The source is unavailable after maintenance
    failure until a subsequent successful maintenance pass.
    """

    __slots__ = ("_journal", "_guard", "_source_fence", "_collect", "_cleanup",
                 "_pid", "_thread", "_started", "_healthy", "_closing", "_closed")

    def __init__(self, *, journal, guard, source_fence, collect, close):
        if (type(journal) is not GatewayDirectCommandJournal
                or type(guard) is not LocalAccountSessionGuard
                or not all(callable(item) for item in (source_fence, collect, close))):
            raise GatewayStrategyFinancialSourceError("STRATEGY_FINANCIAL_SOURCE_INVALID")
        self._journal, self._guard = journal, guard
        self._source_fence, self._collect, self._cleanup = source_fence, collect, close
        self._pid, self._thread = os.getpid(), threading.current_thread()
        self._started = self._healthy = self._closing = self._closed = False

    def _assert_owner(self):
        if self._closed or self._pid != os.getpid() or self._thread is not threading.current_thread():
            raise GatewayStrategyFinancialSourceError("STRATEGY_FINANCIAL_SOURCE_UNAVAILABLE")

    def bound_to(self, journal, guard):
        self._assert_owner()
        return self._journal is journal and self._guard is guard

    @contextmanager
    def _fence(self, account, strategy, currency):
        self._assert_owner()
        if not self._healthy:
            raise GatewayStrategyFinancialSourceError("STRATEGY_FINANCIAL_SOURCE_UNAVAILABLE")
        with self._source_fence(account, strategy, currency) as verify:
            if not callable(verify):
                raise GatewayStrategyFinancialSourceError("STRATEGY_FINANCIAL_SOURCE_UNAVAILABLE")

            def checked(db, snapshot):
                self._assert_owner()
                return self._healthy and verify(db, snapshot) is True

            yield checked

    def start(self):
        self._assert_owner()
        if self._started or self._closing:
            raise GatewayStrategyFinancialSourceError("STRATEGY_FINANCIAL_SOURCE_ALREADY_STARTED")
        self._journal.install_strategy_financial_port(guard=self._guard, source_fence=self._fence)
        self._started = True
        return self.maintain()

    def maintain(self):
        self._assert_owner()
        if not self._started or self._closing:
            raise GatewayStrategyFinancialSourceError("STRATEGY_FINANCIAL_SOURCE_UNAVAILABLE")
        self._healthy = False
        try:
            snapshots = self._collect()
            if (type(snapshots) is not tuple or len(snapshots) > 256
                    or any(type(item) is not StrategyFinancialSnapshot for item in snapshots)):
                raise ValueError
            self._healthy = True
            for snapshot in snapshots:
                self._journal.append_strategy_financial(snapshot, guard=self._guard)
            return True
        except Exception:
            self._healthy = False
            return False

    def invalidate(self):
        """Fail closed during a connection transition until maintenance succeeds."""
        self._assert_owner()
        self._healthy = False

    def close(self):
        self._assert_owner()
        self._healthy = False
        self._closing = True
        self._cleanup()
        self._closed = True
