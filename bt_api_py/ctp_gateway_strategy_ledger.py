"""Owner-local strategy ledger ports and baseline for the CTP Gateway profile.

The Gateway journal keeps a per-strategy position ledger that it replays from
strongly attributed trades. It only serves that ledger once trusted owner code
has installed four local ports and a baseline exists (I63-E111). This module is
that owner code for the CTP trading profile. Everything here runs on the
account owner thread; nothing is reachable from an RPC peer.

Ports (all nonblocking, no provider or network I/O):

* ``resolve_binding``: the profile's configured instruments identify the full
  instrument of an order payload; the CTP provider declares whether the venue
  needs an explicit today/yesterday close.
* ``current_trading_day``: the trading day of the logged-in TD session.
* ``verify_day_transition``: both days are well formed and
  ``old < new <= current session day``. Fact days come from venue returns that
  the session already bound to its own trading day when it accepted them.
* ``verify_current_continuity``: the request is for the session's trading day,
  the return source has completed both full-day queries of the current native
  session without a gap, stale source or failed batch
  (``CtpGatewayReturnMaintenance.ledger_continuous``), and no command of an
  earlier trading day on the instrument can still trade unseen
  (``prior_day_commands_settled``).

Baseline (owner thread, outside the journal transaction, may query CTP):

* A journal without history takes the empty baseline once one complete account
  read shows no position and no open order.
* A journal that already has history takes the replayed baseline: the journal's
  own settled history must reproduce the complete account read exactly.
* A journal without history on an account that already holds positions takes
  the external baseline, and only when the operator declared it in the
  protected resource bundle (``ExternalBaselineDeclaration``): the declared
  holdings must equal the complete account read exactly. They are booked to
  external, never to a strategy, and no strategy can close them (D63-06).
* Anything else (undeclared holdings, holdings next to existing history, open
  orders, unresolved commands) leaves the ledger without a baseline. The
  profile then refuses new reservations; nothing is assigned from the account
  snapshot.

One account read is a comparable cut when the private return buffer holds no
waiting record and shows the same last sequence, without a gap, before and
after the read under one native session and trading day: every order or trade
of the account, including manual ones, arrives on that stream. The same facts
are rechecked inside the journal transaction just before commit.
"""

from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_gateway.direct_command_journal import DirectCommandJournalError
from bt_api_gateway.strategy_position_ledger import (
    ExternalBaselinePosition,
    StrategyBaselineEvidence,
    StrategyInstrumentBinding,
    prior_day_commands_settled,
)

_LOG = logging.getLogger(__name__)
_QUANTITY_UNIT = "contracts"
_SOURCE_REF = "ctp.td.account-query"


class CtpGatewayStrategyLedgerError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _is_day(value: object) -> bool:
    return type(value) is str and len(value) == 8 and value.isdigit()


def _ordered(positions) -> tuple[ExternalBaselinePosition, ...]:
    return tuple(sorted(positions, key=lambda item: (item.instrument.venue, item.instrument.native_symbol,
                                                     item.direction)))


@dataclass(frozen=True, slots=True)
class ExternalBaselineDeclaration:
    """The operator's statement of holdings that predate the Gateway journal.

    It authorizes nothing by itself: the baseline is written only when these
    rows equal the provider's complete account read. ``operation_id`` is the
    operator's stable command id; a later, different declaration never
    replaces an existing baseline.
    """

    operator_ref: str
    operation_id: str
    positions: tuple[ExternalBaselinePosition, ...]

    def __post_init__(self) -> None:
        if (any(type(value) is not str or not value or value != value.strip() or not value.isprintable()
                or len(value) > 256 for value in (self.operator_ref, self.operation_id))
                or type(self.positions) is not tuple or not 1 <= len(self.positions) <= 4096
                or any(type(item) is not ExternalBaselinePosition for item in self.positions)
                or len({(item.instrument, item.direction) for item in self.positions}) != len(self.positions)):
            raise CtpGatewayStrategyLedgerError("INVALID_EXTERNAL_BASELINE")
        object.__setattr__(self, "positions", _ordered(self.positions))


class CtpGatewayStrategyLedger:
    """Install the ledger ports and establish the ledger baseline for one owner."""

    def __init__(
        self,
        *,
        journal: Any,
        guard: Any,
        session: Any,
        provider: Any,
        returns: Any,
        instruments: tuple[MarketInstrumentKey, ...],
        account_scope: str,
        route: str,
        service_generation: str,
        split_close_required: Any,
        external_baseline: ExternalBaselineDeclaration | None = None,
        snapshot_timeout_seconds: float = 10.0,
        snapshot_validity_seconds: float = 5.0,
        retry_seconds: float = 30.0,
    ) -> None:
        if (type(instruments) is not tuple or not instruments
                or any(type(item) is not MarketInstrumentKey for item in instruments)
                or not callable(split_close_required)
                or any(type(value) not in (int, float) or not 0 < value <= 300
                       for value in (snapshot_timeout_seconds, snapshot_validity_seconds, retry_seconds))):
            raise CtpGatewayStrategyLedgerError("INVALID_STRATEGY_LEDGER_SETTINGS")
        self._journal, self._guard = journal, guard
        self._session, self._provider, self._returns = session, provider, returns
        self._account_scope, self._route = account_scope, route
        self._service_generation = service_generation
        self._snapshot_timeout = float(snapshot_timeout_seconds)
        self._snapshot_validity_ns = int(snapshot_validity_seconds * 1_000_000_000)
        self._retry_seconds = float(retry_seconds)
        self._owner_thread = threading.current_thread()
        bindings: dict[tuple[str, str], StrategyInstrumentBinding] = {}
        by_symbol: dict[str, list[StrategyInstrumentBinding]] = {}
        for instrument in instruments:
            try:
                split = split_close_required(instrument.venue)
            except Exception:
                # The provider has not established the venue's close rule: the
                # instrument stays unresolvable instead of being guessed.
                _LOG.warning("Gateway strategy ledger cannot serve venue %s: close rule not established",
                             instrument.venue)
                continue
            if type(split) is not bool:
                raise CtpGatewayStrategyLedgerError("INVALID_STRATEGY_LEDGER_SETTINGS")
            binding = StrategyInstrumentBinding(instrument, route, _QUANTITY_UNIT, split)
            if (instrument.venue, instrument.native_symbol) in bindings:
                raise CtpGatewayStrategyLedgerError("INVALID_STRATEGY_LEDGER_SETTINGS")
            bindings[(instrument.venue, instrument.native_symbol)] = binding
            by_symbol.setdefault(instrument.native_symbol, []).append(binding)
        self._bindings, self._by_symbol = bindings, by_symbol
        if external_baseline is not None and (
                type(external_baseline) is not ExternalBaselineDeclaration
                or any(bindings.get((item.instrument.venue, item.instrument.native_symbol)) is None
                       or bindings[(item.instrument.venue, item.instrument.native_symbol)].instrument
                       != item.instrument for item in external_baseline.positions)):
            raise CtpGatewayStrategyLedgerError("INVALID_EXTERNAL_BASELINE")
        self._external_baseline = external_baseline
        self._settled_prior: set[tuple[str, str, str]] = set()
        self._installed = False
        self._ready = False
        self._state = "PENDING"
        self._next_attempt = 0.0
        self._pending_evidence: StrategyBaselineEvidence | None = None
        self._pending_cut: tuple[int, int, str] | None = None
        self._context = ""

    # ------------------------------------------------------------------
    # ports
    # ------------------------------------------------------------------
    def resolve_binding(self, payload: object) -> StrategyInstrumentBinding:
        if type(payload) is not dict:
            raise CtpGatewayStrategyLedgerError("INSTRUMENT_UNRESOLVED")
        symbol, venue = payload.get("symbol"), payload.get("exchange_id")
        if type(symbol) is not str or (venue is not None and type(venue) is not str):
            raise CtpGatewayStrategyLedgerError("INSTRUMENT_UNRESOLVED")
        if venue is not None:
            binding = self._bindings.get((venue, symbol))
            if binding is None:
                raise CtpGatewayStrategyLedgerError("INSTRUMENT_UNRESOLVED")
            return binding
        candidates = self._by_symbol.get(symbol, ())
        if len(candidates) != 1:
            raise CtpGatewayStrategyLedgerError("INSTRUMENT_UNRESOLVED")
        return candidates[0]

    def current_trading_day(self) -> str:
        day = self._provider.trading_day()
        if not _is_day(day):
            raise CtpGatewayStrategyLedgerError("TRADING_DAY_UNAVAILABLE")
        return day

    def verify_day_transition(self, old_day: object, new_day: object) -> bool:
        try:
            return (_is_day(old_day) and _is_day(new_day)
                    and old_day < new_day <= self.current_trading_day())
        except Exception:
            return False

    def verify_current_continuity(self, db, account, binding, trading_day, baseline) -> bool:
        try:
            if (threading.current_thread() is not self._owner_thread
                    or account != self._account_scope
                    or self._bindings.get((binding.instrument.venue, binding.instrument.native_symbol)) != binding
                    or type(baseline) is not StrategyBaselineEvidence
                    or baseline.account_scope != account or baseline.route != self._route
                    or baseline.journal_id != self._journal.journal_id
                    or trading_day != self.current_trading_day()
                    or self._returns.ledger_continuous() is not True):
                return False
            key = (trading_day, binding.instrument.venue, binding.instrument.native_symbol)
            if key in self._settled_prior:
                return True
            if prior_day_commands_settled(db, account, binding, trading_day) is not True:
                return False
            # Earlier days never gain commands; only an unsettled one can heal.
            self._settled_prior = {item for item in self._settled_prior if item[0] == trading_day}
            self._settled_prior.add(key)
            return True
        except Exception:
            return False

    def install(self) -> None:
        """Bind the ports to the journal; a second call is a no-op."""
        if self._installed:
            return
        self._journal.install_strategy_ledger_ports(
            guard=self._guard, resolve_binding=self.resolve_binding,
            current_trading_day=self.current_trading_day,
            verify_day_transition=self.verify_day_transition,
            verify_current_continuity=self.verify_current_continuity)
        self._installed = True

    # ------------------------------------------------------------------
    # baseline
    # ------------------------------------------------------------------
    @property
    def ready(self) -> bool:
        """Ports installed and a baseline present; a cached flag, no I/O."""
        return self._ready

    @property
    def state(self) -> str:
        return self._state

    @contextmanager
    def reservation_fence(self, context, order):
        """Refuse every new reservation until the ledger can account for it."""
        if not self._ready:
            raise DirectCommandJournalError("STRATEGY_LEDGER_UNAVAILABLE")
        with self._returns.reservation_fence(context, order):
            yield

    def maintain(self) -> None:
        """Owner-thread tick: establish the baseline once the returns are complete.

        Never raises: an attempt that cannot be proved leaves the ledger
        unavailable, names the reason once and retries later.
        """
        if self._ready or threading.current_thread() is not self._owner_thread:
            return
        now = time.monotonic()
        if now < self._next_attempt:
            return
        try:
            state = self._attempt()
        except DirectCommandJournalError as error:
            state = "REFUSED:" + error.code
        except Exception as error:
            code = getattr(error, "code", None)
            state = "FAILED:" + (code if type(code) is str and code.isupper() else type(error).__name__)
        finally:
            self._pending_evidence = self._pending_cut = None
        if state in ("PRESENT", "EMPTY_INITIALIZED", "REPLAY_INITIALIZED", "EXTERNAL_INITIALIZED"):
            self._ready = True
            _LOG.info("Gateway strategy ledger baseline ready (%s)", state)
        else:
            # Returns still catching up are retried on the next tick.
            self._next_attempt = now + (0.0 if state == "WAITING_RETURNS" else self._retry_seconds)
            if state != self._state and state != "WAITING_RETURNS":
                # Days and counts only: enough to tell an environment or day
                # mismatch from a real difference, never account values.
                _LOG.warning("Gateway strategy ledger has no baseline: %s%s", state, self._context)
        self._state = state

    def _attempt(self) -> str:
        self.install()
        self._context = ""
        current = self._journal.read_strategy_ledger_state(guard=self._guard)
        declared = self._external_baseline
        if current["baseline"] is not None:
            if declared is not None:
                stored = StrategyBaselineEvidence.from_mapping(current["baseline"])
                if (stored.operation_id != declared.operation_id or stored.operator_ref != declared.operator_ref
                        or _ordered(stored.external_positions) != declared.positions):
                    # Same id with other content, or another declaration: never rebuilt.
                    return "EXTERNAL_BASELINE_CONFLICT"
            return "PRESENT"
        if declared is not None and current["history_present"] is True:
            return "EXTERNAL_BASELINE_NEEDS_EMPTY_JOURNAL"
        if self._returns.ledger_continuous() is not True:
            return "WAITING_RETURNS"
        buffer = self._session.return_buffer
        before = buffer.status()
        if before.gap or before.record_count:
            return "WAITING_RETURNS"
        generation, day = self._provider.session_generation(), self.current_trading_day()
        if type(generation) is not int or not 0 < generation < 2**63:
            return "SESSION_UNAVAILABLE"
        observation = self._session.read_account(
            generation=self._service_generation, timeout=self._snapshot_timeout)
        after = buffer.status()
        if (after.gap or after.record_count or after.last_sequence != before.last_sequence
                or self._provider.session_generation() != generation
                or self.current_trading_day() != day
                or self._returns.ledger_continuous() is not True):
            return "CUT_NOT_COMPARABLE"
        if (observation.positions_complete is not True or observation.open_orders_complete is not True
                or observation.open_orders is None):
            return "SNAPSHOT_INCOMPLETE"
        if observation.open_orders:
            return "OPEN_ORDERS_PRESENT"
        positions = self._positions(observation.positions)
        if positions is None:
            return "POSITION_NOT_ATTRIBUTABLE"
        observed_at = time.time_ns()
        history = current["history_present"] is True
        external = {}
        if declared is not None:
            if positions != declared.positions:
                return "EXTERNAL_BASELINE_MISMATCH"
            external = {"external_positions": positions, "operator_ref": declared.operator_ref,
                        "operation_id": declared.operation_id}
        elif not history and positions:
            # Holdings that predate the journal need the operator's external baseline.
            return "EXTERNAL_BASELINE_REQUIRED"
        earliest = current["earliest_trade_day"]
        self._context = " (session day %s, history from %s, %d position row(s), %s)" % (
            day, earliest if _is_day(earliest) else "-", len(positions),
            "external declared" if external else "journal has history" if history else "empty journal")
        evidence = StrategyBaselineEvidence(
            account_scope=self._account_scope, journal_id=self._journal.journal_id, trading_day=day,
            route=self._route, owner_generation=self._journal.generation, session_generation=generation,
            source_ref=_SOURCE_REF, position_snapshot_ref=observation.observation_id + ":positions",
            open_orders_snapshot_ref=observation.observation_id + ":open-orders",
            cut_ref=f"ctp-return-buffer:{generation}:{before.last_sequence}",
            coverage_refs=("open_orders", "positions"), observed_at_ns=observed_at,
            valid_until_ns=observed_at + self._snapshot_validity_ns,
            positions_complete=True, open_orders_complete=True,
            positions_empty=not positions, open_orders_empty=True,
            **({"schema_version": 2, "history_start_trading_day": min(day, earliest) if _is_day(earliest) else day,
                "account_positions": positions} if history else external),
        )
        self._pending_evidence = evidence
        self._pending_cut = (generation, before.last_sequence, day)
        if external:
            self._journal.initialize_external_strategy_ledger(
                evidence, guard=self._guard, baseline_fence=self._fence,
                verify_authorized_snapshot=self._verify_current)
            return "EXTERNAL_INITIALIZED"
        if history:
            self._journal.initialize_replayed_strategy_ledger(
                evidence, guard=self._guard, baseline_fence=self._fence,
                verify_current_snapshot=self._verify_current)
            return "REPLAY_INITIALIZED"
        self._journal.initialize_empty_strategy_ledger(
            evidence, guard=self._guard, baseline_fence=self._fence,
            verify_current_empty=self._verify_current)
        return "EMPTY_INITIALIZED"

    def _positions(self, rows) -> tuple[ExternalBaselinePosition, ...] | None:
        """Project complete provider rows; ``None`` when a row cannot be attributed."""
        result = []
        for row in rows:
            binding = self._bindings.get((row.instrument.venue, row.instrument.native_symbol))
            if (binding is None or row.side not in ("long", "short")
                    or row.today_quantity is None or row.yesterday_quantity is None):
                return None
            if int(row.today_quantity) == 0 and int(row.yesterday_quantity) == 0:
                continue
            result.append(ExternalBaselinePosition(
                binding.instrument, row.side, str(int(row.today_quantity)), str(int(row.yesterday_quantity))))
        return _ordered(result)

    @contextmanager
    def _fence(self):
        # The owner thread is the only dispatcher and the only return ingestor:
        # holding it for the transaction excludes every system write.
        if threading.current_thread() is not self._owner_thread:
            raise CtpGatewayStrategyLedgerError("OWNER_REQUIRED")
        yield

    def _verify_current(self, evidence) -> bool:
        try:
            if (evidence is not self._pending_evidence or self._pending_cut is None
                    or threading.current_thread() is not self._owner_thread):
                return False
            generation, sequence, day = self._pending_cut
            status = self._session.return_buffer.status()
            return (not status.gap and not status.record_count and status.last_sequence == sequence
                    and self._provider.session_generation() == generation
                    and self.current_trading_day() == day
                    and time.time_ns() <= evidence.valid_until_ns)
        except Exception:
            return False


__all__ = ("CtpGatewayStrategyLedger", "CtpGatewayStrategyLedgerError", "ExternalBaselineDeclaration")
