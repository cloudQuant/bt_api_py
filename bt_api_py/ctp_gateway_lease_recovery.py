"""CTP lease-expiry DRAIN recovery remains unavailable without a provider cut.

The local return sequence, empty buffer and maintenance continuity do not bind
the full-day queries to the callback stream at a common venue cut. Until such a
source is established, neither new evidence nor historical evidence can release
a lease cause. Binding and the journal's historical state remain untouched.
"""

from __future__ import annotations

import json
import math
import threading
import time  # noqa: F401 - retained for legacy test fixture monkeypatching
from contextlib import contextmanager
from typing import Any

from bt_api_py.gateway_lease_recovery import LeaseRecoveryBinding, LeaseRecoveryEvidence

_MAX_OPEN_FACTS = 1024


def _is_day(value: object) -> bool:
    return type(value) is str and len(value) == 8 and value.isascii() and value.isdigit()


class CtpGatewayLeaseRecoverySource:
    """Owner-thread proof source; unbound (before owner start) it proves nothing."""

    def __init__(self, *, account_scope: str, strategy_symbols: dict[str, frozenset[str]],
                 validity_seconds: float = 1.0) -> None:
        if (type(account_scope) is not str or not account_scope
                or type(strategy_symbols) is not dict or not strategy_symbols
                or any(type(name) is not str or not name or type(symbols) is not frozenset or not symbols
                       or any(type(symbol) is not str or not symbol for symbol in symbols)
                       for name, symbols in strategy_symbols.items())
                or type(validity_seconds) not in (int, float) or not math.isfinite(validity_seconds)
                or not 0 < validity_seconds <= 5):
            raise ValueError("INVALID_CTP_LEASE_RECOVERY_SOURCE")
        self._account = account_scope
        self._symbols = dict(strategy_symbols)
        self._validity = float(validity_seconds)
        self._bound: tuple[Any, Any, Any, Any, threading.Thread] | None = None

    def bind(self, *, session: Any, provider: Any, returns: Any, journal: Any) -> None:
        """Called by the profile on the owner thread once the session resources exist."""
        self._bound = (session, provider, returns, journal, threading.current_thread())

    def unbind(self) -> None:
        self._bound = None

    def _cut(self) -> tuple[int, int, str] | None:
        bound = self._bound
        if bound is None or threading.current_thread() is not bound[4]:
            return None
        session, provider, returns = bound[0], bound[1], bound[2]
        if returns.ledger_continuous() is not True:
            return None
        status = session.return_buffer.status()
        generation, day = provider.session_generation(), provider.trading_day()
        if (status.gap or status.record_count or type(status.last_sequence) is not int
                or type(generation) is not int or not _is_day(day)):
            return None
        return generation, status.last_sequence, day

    def _strategy_returns_resolved(self, strategy: str, day: str) -> bool:
        symbols = self._symbols.get(strategy)
        if not symbols or self._bound is None:
            return False
        journal = self._bound[3]
        with journal._transaction() as db:
            rows = db.execute(
                "SELECT fact_json FROM direct_execution_facts WHERE account=? "
                "AND disposition IN ('UNATTRIBUTED','CONFLICT') LIMIT ?",
                (self._account, _MAX_OPEN_FACTS + 1)).fetchall()
        if len(rows) > _MAX_OPEN_FACTS:
            return False
        for row in rows:
            fact = json.loads(row[0])
            instrument = fact.get("instrument") if type(fact) is dict else None
            if type(instrument) is not dict or not _is_day(fact.get("trading_day")):
                return False
            if fact["trading_day"] == day and instrument.get("native_symbol") in symbols:
                return False
        return True

    @contextmanager
    def hold_verified(self, binding: LeaseRecoveryBinding):
        # Local observations cannot establish the common provider/stream cut.
        yield None

    def recheck(self, evidence: LeaseRecoveryEvidence) -> bool:
        # Previously issued buffer-based evidence has the same missing cut.
        return False


__all__ = ("CtpGatewayLeaseRecoverySource",)
