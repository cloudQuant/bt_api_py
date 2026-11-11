"""CTP recovery proof for a lease-expiry DRAIN cause (D63-33, I63-E128).

``GatewayLeaseRecovery`` releases such a cause only with evidence from a source
that trusted host code installed. This module is that source for the CTP
trading profile. It runs on the account owner thread and performs no provider
or network I/O.

``GatewayLeaseRecovery`` itself has already checked, in the journal, that the
strategy holds a new registration, that it acknowledged every execution event
recorded for it and that none of its commands or cancels is still unresolved.
The source adds what only the provider side can tell:

* Completeness: the return maintenance of the current native session is
  continuous (``CtpGatewayReturnMaintenance.ledger_continuous``): both full-day
  queries of this session completed and no gap, stale source or failed batch
  was seen since. Every order and trade of the account arrives on that stream.
* One common cut: the private return buffer holds no waiting record. Its last
  sequence, the native session generation and the trading day name the cut.
* Nothing of this strategy can be missing from its event stream: no return of
  the current trading day on an instrument the strategy may trade is still
  unattributed or in conflict. This is deliberately coarse: a manual trade on
  one of those instruments also keeps the cause until it is resolved (this
  profile installs no external classifier that could tell the two apart).

The owner thread is the only dispatcher and the only return ingestor, so the
cut cannot move while the caller holds the context on that thread; a callback
that arrives meanwhile waits in the buffer and makes ``recheck`` fail. Anything
that cannot be shown yields no evidence: the cause then stays.
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
import time
from contextlib import contextmanager
from dataclasses import astuple
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
        evidence = None
        try:
            cut = self._cut() if type(binding) is LeaseRecoveryBinding else None
            if (cut is not None and binding.account == self._account
                    and self._strategy_returns_resolved(binding.strategy, cut[2])):
                provider_cut = f"ctp-return-buffer:{cut[0]}:{cut[1]}:{cut[2]}"
                evidence = LeaseRecoveryEvidence(
                    binding,
                    hashlib.sha256(json.dumps([list(astuple(binding)), provider_cut],
                                              separators=(",", ":")).encode()).hexdigest(),
                    provider_cut, time.monotonic() + self._validity)
        except Exception:
            evidence = None
        yield evidence

    def recheck(self, evidence: LeaseRecoveryEvidence) -> bool:
        try:
            cut = self._cut()
            return (cut is not None
                    and evidence.provider_cut == f"ctp-return-buffer:{cut[0]}:{cut[1]}:{cut[2]}"
                    and time.monotonic() < evidence.valid_until_monotonic)
        except Exception:
            # Includes an object that is not this source's evidence.
            return False


__all__ = ("CtpGatewayLeaseRecoverySource",)
