"""Service hosts write and serve the account's strategy ledger (iteration 60, R2-E).

An execution ``account_owner`` component configured with the
``strategy_ledger`` option owns one :class:`SqliteStrategyLedger` for its one
account. This module is the adapter between that ledger and the managed
execution journal (``bt_api_execution``); the ledger itself stays free of any
``bt_api_execution`` import (user decision).

Write path, in commit order (each step is its own durable transaction):

1. ``admit``: the ledger reserves the order (budget, close capacity, flip and
   strategy-cash checks of ``SqliteStrategyLedger.reserve``) *before* the
   journal sees it. A refusal returns the ledger's finite code and nothing is
   journaled or dispatched.
2. The managed order entry records/admits/claims the intent in the journal,
   calls the provider, and records the provider outcome (journal commit).
3. ``sync``: journal outbox events after the ledger's journal cursor are
   applied to the ledger with ``apply_journal_event``; the ledger effects and
   the journal sequence commit in one ledger transaction (exactly once).

Crash windows and recovery (``recover`` runs before the host serves):

* after 1, before 2: the ledger holds a ``RESERVED`` intent the journal has
  never seen. The journal records every intent before any provider call, so
  such an intent was provably never dispatched and is finished ``REJECTED``.
* after the provider call, before the outcome commit: the journal recovers the
  dispatch as ``UNKNOWN`` (``recover_interrupted_dispatches``); the ledger
  projects ``UNKNOWN`` and keeps the reservation counting until a provider
  observation resolves it.
* after 2, before 3 (or during 3): ``sync`` resumes after the last applied
  sequence. The ledger never runs ahead of the journal.

Fills, fees and terminal states are projected only from the journal's events
(``provider_observation``/``reconciled_observation``: cumulative quantity,
average price, cumulative fee with its currency; ``provider_commission_evidence``:
fee-only corrections; ``cancelled_by_cancel_intent``; rejection/block events),
so the ledger and the journal cannot disagree on which fills exist. A journal
event that cannot be projected (an intent the ledger never admitted, a fee
without a currency, an unknown event type, a replaced journal generation)
blocks the projection: admissions and strategy-book reads then refuse with
``STRATEGY_LEDGER_PROJECTION_BLOCKED`` instead of serving a book that might
be wrong.

Iteration 60 R3 additions:

* A pre-journal refusal (admission above) is answered with the
  ``NOT_JOURNALED`` refusal disposition when the journal provably has no
  record of the key (:meth:`ServiceStrategyLedgerBinding.journal_lacks`); the
  client maps exactly that reply to a definitive rejection.
* A late fee after a terminal order is journaled by ``bt_api_execution`` as
  ``provider_commission_evidence`` and projected like any fee evidence (the
  intent keeps its final state; cumulative per currency, regression refused).
* The Risk account owner uses the same binding over its ``RiskOrderJournal``
  (:meth:`ServiceStrategyLedgerBinding.for_risk_journal`): the cursor is the
  account scope's event revision; fills and fees come from the complete fill
  rows of ``get_deals`` observations, terminal states from the published
  public state, ``ADMISSION_DENIED``/``NO_DISPATCH`` release the reservation.

Reads: ``read_strategy_ledger`` returns the ledger and the subscription-fed
account position evidence (``StreamedAccountPositions`` /
``GatewayAccountPositionStream`` / ``GatewayReconciliationPositionSource``;
their ``report`` does no I/O), after a ``sync``. No provider read happens per
query.
"""

from __future__ import annotations

import dataclasses
import threading
from collections.abc import Callable, Mapping
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import MappingProxyType
from typing import Any

from bt_api_base.service_protocol import WireScope

from bt_api_py.strategy_ledger import (
    InstrumentTerms,
    LedgerIntent,
    SqliteStrategyLedger,
    StrategyBudget,
    StrategyCapital,
    StrategyLedgerError,
)

__all__ = [
    "STRATEGY_LEDGER_OPTION",
    "STRATEGY_LEDGER_POSITIONS_RESOURCE",
    "ServiceStrategyLedgerBinding",
    "ServiceStrategyLedgerError",
    "StrategyLedgerAccountBookSource",
    "parse_strategy_ledger_option",
]

STRATEGY_LEDGER_OPTION = "strategy_ledger"
STRATEGY_LEDGER_POSITIONS_RESOURCE = "strategy_ledger_positions"
_OPTION_FIELDS = frozenset(
    {"path", "instruments", "budgets", "capital", "reconciliation_max_age_seconds"}
)
_PAGE = 500
# Journal event types that change nothing in the strategy ledger.
_NO_LEDGER_EFFECT = frozenset(
    {
        "intent_recorded",
        "intent_admitted",
        "dispatch_claimed",
        "manual_review_required",
        "pending_admission_reference_reauthorized",
    }
)
_REJECTION_EVENTS = frozenset({"intent_rejected", "intent_blocked", "dispatch_blocked"})
_OBSERVATION_EVENTS = frozenset(
    {"provider_observation", "reconciled_observation", "cancelled_by_cancel_intent"}
)
_FINAL_BY_JOURNAL_STATE = {
    "FILLED": "FILLED",
    "CANCELLED": "CANCELLED",
    "REJECTED": "REJECTED",
    "BLOCKED": "REJECTED",
}


class ServiceStrategyLedgerError(ValueError):
    """Finite, value-free strategy-ledger host failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


# ---------------------------------------------------------------------------
# Configuration (one parser for service_config and the component validator)
# ---------------------------------------------------------------------------


def _text(value: object, code: str = "STRATEGY_LEDGER_OPTION_INVALID") -> str:
    if type(value) is not str or not value or value != value.strip() or len(value) > 256:
        raise ServiceStrategyLedgerError(code)
    return value


def _decimal_text(value: object, *, positive: bool = True) -> str:
    # Strings only: a float would silently change a budget or a multiplier.
    if type(value) is not str:
        raise ServiceStrategyLedgerError("STRATEGY_LEDGER_OPTION_INVALID")
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError):
        raise ServiceStrategyLedgerError("STRATEGY_LEDGER_OPTION_INVALID") from None
    if not parsed.is_finite() or (positive and parsed <= 0):
        raise ServiceStrategyLedgerError("STRATEGY_LEDGER_OPTION_INVALID")
    return str(parsed)


def _table(raw: object, fields: tuple[str, ...]) -> dict[str, dict[str, str]]:
    if not isinstance(raw, Mapping):
        raise ServiceStrategyLedgerError("STRATEGY_LEDGER_OPTION_INVALID")
    result: dict[str, dict[str, str]] = {}
    for name, row in raw.items():
        if not isinstance(row, Mapping) or set(row) - set(fields):
            raise ServiceStrategyLedgerError("STRATEGY_LEDGER_OPTION_INVALID")
        result[_text(name)] = {key: row[key] for key in fields if key in row}
    return result


def parse_strategy_ledger_option(raw: object) -> Mapping[str, Any]:
    """Validate the ``strategy_ledger`` option and return its plain normal form.

    ``path`` is required and absolute (no default location). Instruments,
    budgets and capital are the ledger's trusted policy; every number is a
    decimal string. Unknown fields are refused.
    """

    if not isinstance(raw, Mapping) or "path" not in raw or set(raw) - _OPTION_FIELDS:
        raise ServiceStrategyLedgerError("STRATEGY_LEDGER_OPTION_INVALID")
    path = _text(raw["path"])
    if not Path(path).is_absolute():
        raise ServiceStrategyLedgerError("STRATEGY_LEDGER_PATH_NOT_ABSOLUTE")
    result: dict[str, Any] = {"path": path}
    instruments = {}
    for name, row in _table(
        raw.get("instruments", {}), ("multiplier", "quote_currency", "product_model")
    ).items():
        if "multiplier" not in row or "quote_currency" not in row:
            raise ServiceStrategyLedgerError("STRATEGY_LEDGER_OPTION_INVALID")
        instruments[name] = {
            "multiplier": _decimal_text(row["multiplier"]),
            "quote_currency": _text(row["quote_currency"]),
            "product_model": _text(row.get("product_model", "spot")),
        }
    budgets = {}
    for name, row in _table(raw.get("budgets", {}), ("limit", "currency")).items():
        if set(row) != {"limit", "currency"}:
            raise ServiceStrategyLedgerError("STRATEGY_LEDGER_OPTION_INVALID")
        budgets[name] = {"limit": _decimal_text(row["limit"]), "currency": _text(row["currency"])}
    capital = {}
    for name, row in _table(raw.get("capital", {}), ("amount", "currency")).items():
        if set(row) != {"amount", "currency"}:
            raise ServiceStrategyLedgerError("STRATEGY_LEDGER_OPTION_INVALID")
        capital[name] = {"amount": _decimal_text(row["amount"]), "currency": _text(row["currency"])}
    result["instruments"] = instruments
    result["budgets"] = budgets
    result["capital"] = capital
    if "reconciliation_max_age_seconds" in raw:
        age = raw["reconciliation_max_age_seconds"]
        if type(age) is not int or not 0 < age <= 86_400:
            raise ServiceStrategyLedgerError("STRATEGY_LEDGER_OPTION_INVALID")
        result["reconciliation_max_age_seconds"] = age
    # Construct the typed policy once so an invalid row fails at config time.
    _policy(result)
    return MappingProxyType(result)


def _policy(option: Mapping[str, Any]) -> dict[str, Any]:
    try:
        return {
            "instruments": {
                name: InstrumentTerms(
                    Decimal(row["multiplier"]), row["quote_currency"], row["product_model"]
                )
                for name, row in option["instruments"].items()
            },
            "budgets": {
                name: StrategyBudget(Decimal(row["limit"]), row["currency"])
                for name, row in option["budgets"].items()
            },
            "capital": {
                name: StrategyCapital(Decimal(row["amount"]), row["currency"])
                for name, row in option["capital"].items()
            },
        }
    except StrategyLedgerError:
        raise ServiceStrategyLedgerError("STRATEGY_LEDGER_OPTION_INVALID") from None


def _subscription_report(resource: object) -> Callable[[], Any]:
    """Accept only subscription-maintained position states (``report`` does no I/O)."""

    from bt_api_py.account_position_stream import (
        GatewayAccountPositionStream,
        GatewayReconciliationPositionSource,
        StreamedAccountPositions,
    )

    if type(resource) not in (
        StreamedAccountPositions,
        GatewayAccountPositionStream,
        GatewayReconciliationPositionSource,
    ):
        raise ServiceStrategyLedgerError("STRATEGY_LEDGER_POSITIONS_INVALID")
    return resource.report  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Host binding
# ---------------------------------------------------------------------------


def _resolve(targets: Mapping[WireScope, Any], requested: WireScope) -> Any | None:
    exact = targets.get(requested)
    if exact is not None:
        return exact
    if requested.run_id is None or requested.strategy_ref is None:
        return None
    # A run-independent strategy template (same rule as the order entry).
    return targets.get(dataclasses.replace(requested, run_id=None))


def _fill_from_payload(payload: Mapping[str, Any], *, fee: bool) -> dict[str, Any]:
    quantity = payload.get("filled_quantity")
    if type(quantity) is not str:
        raise ServiceStrategyLedgerError("LEDGER_JOURNAL_EVENT_INVALID")
    result: dict[str, Any] = {
        "cumulative_quantity": quantity,
        "average_price": payload.get("average_price"),
    }
    commission = payload.get("cumulative_commission")
    if fee and commission is not None:
        currency = payload.get("cumulative_commission_currency")
        if type(currency) is not str or not currency:
            # A fee in an unknown currency cannot be booked or ignored.
            raise ServiceStrategyLedgerError("LEDGER_FEE_CURRENCY_UNKNOWN")
        result["fee"] = commission
        result["fee_currency"] = currency
    return result


class _ExecutionJournalPort:
    """The Execution account owner's journal (``bt_api_execution`` store)."""

    def __init__(self, store: Any, targets: Mapping[WireScope, Any]) -> None:
        from bt_api_execution import SqliteExecutionStore

        if type(store) is not SqliteExecutionStore:
            raise ServiceStrategyLedgerError("STRATEGY_LEDGER_STORE_INVALID")
        accounts = {scope.account_ref for scope in targets.values()}
        if len(accounts) != 1:
            raise ServiceStrategyLedgerError("STRATEGY_LEDGER_TARGETS_INVALID")
        (self.account_ref,) = accounts
        strategies: dict[str, str] = {}
        for wire_scope, execution_scope in targets.items():
            if wire_scope.strategy_ref is None:
                continue  # manual books are not strategy-ledger owners
            if strategies.setdefault(execution_scope.key, wire_scope.strategy_ref) != (
                wire_scope.strategy_ref
            ):
                raise ServiceStrategyLedgerError("STRATEGY_LEDGER_TARGETS_INVALID")
        if not strategies:
            raise ServiceStrategyLedgerError("STRATEGY_LEDGER_TARGETS_INVALID")
        generation = store.journal_source_identity().get("generation")
        if type(generation) is not str or not generation:
            raise ServiceStrategyLedgerError("STRATEGY_LEDGER_JOURNAL_UNAVAILABLE")
        self._source = "execution-journal:" + generation
        self._store = store
        self._targets = MappingProxyType(dict(targets))
        self._strategies = MappingProxyType(strategies)
        self._strategy_scopes = tuple(
            {scope.key: scope for scope in targets.values() if scope.key in strategies}.values()
        )
        self.account_keys = frozenset(scope.account_key for scope in targets.values())

    def source(self) -> str:
        return self._source

    def strategy_for(self, wire_scope: WireScope) -> str | None:
        execution_scope = _resolve(self._targets, wire_scope)
        if execution_scope is None:
            return None
        return self._strategies.get(execution_scope.key)

    def has(self, intent_id: str) -> bool:
        return any(
            self._store.get(intent_id, scope=scope) is not None for scope in self._strategy_scopes
        )

    def read(self, cursor: int, limit: int) -> list[tuple[int, dict[str, Any] | None]]:
        events = self._store.read_outbox(after_sequence=cursor, limit=limit)
        return [(event.sequence, self._plan(event)) for event in events]

    def _plan(self, event: Any) -> dict[str, Any] | None:
        if event.scope_key not in self._strategies:
            return None
        kind = event.event_type
        if kind.startswith("cancel_") or kind in _NO_LEDGER_EFFECT:
            # A cancellation's own lifecycle; its effect on the order arrives
            # as ``cancelled_by_cancel_intent``.
            return None
        intent_id = event.intent_id
        state = str(getattr(event.state, "value", event.state))
        if kind == "dispatch_unknown":
            return {"intent_id": intent_id, "step": "UNKNOWN"}
        if kind in _REJECTION_EVENTS:
            return {"intent_id": intent_id, "step": "FINISH", "final_state": "REJECTED"}
        if kind == "provider_commission_evidence":
            # Fee-only evidence, including a late fee after the terminal state
            # (iteration 60 R3): the ledger applies the cumulative fee per
            # currency; a terminal intent keeps its final state.
            return {
                "intent_id": intent_id,
                "step": "EXECUTION",
                **_fill_from_payload(event.payload, fee=True),
            }
        if kind not in _OBSERVATION_EVENTS:
            raise ServiceStrategyLedgerError("LEDGER_JOURNAL_EVENT_UNSUPPORTED")
        # A cancellation record carries the order's fee without its currency;
        # fees are applied from the provider observations that carry both.
        fill = _fill_from_payload(event.payload, fee=kind != "cancelled_by_cancel_intent")
        if state == "ACKED":
            return {"intent_id": intent_id, "step": "SUBMITTED"}
        if state == "UNKNOWN":
            return {"intent_id": intent_id, "step": "UNKNOWN"}
        if state == "PARTIALLY_FILLED":
            return {"intent_id": intent_id, "step": "EXECUTION", **fill}
        final = _FINAL_BY_JOURNAL_STATE.get(state)
        if final is None:
            raise ServiceStrategyLedgerError("LEDGER_JOURNAL_EVENT_UNSUPPORTED")
        if Decimal(fill["cumulative_quantity"]) == 0:
            fill = {}
        return {"intent_id": intent_id, "step": "FINISH", "final_state": final, **fill}


# Risk journal event types (bt_api_risk ``RiskOrderJournal``) with no ledger
# effect: preparation, Risk reservation/claim and proof bookkeeping.
_RISK_NO_EFFECT = frozenset(
    {"PREPARED", "RESERVED", "CLAIMED", "DISPATCHING", "PROOF_READY", "PROOF_RECOVERY_ATTESTED"}
)
_RISK_REJECTED = frozenset({"ADMISSION_DENIED", "NO_DISPATCH"})
_RISK_PROGRESS = frozenset(
    {"ORDER_OBSERVATION", "FILL_OBSERVATION", "ACKED_TRACKED", "ACKED_TERMINAL",
     "PROVIDER_QUERY", "EXPOSURE_APPLIED"}
)
_RISK_FINAL_PUBLIC = {
    "FILLED": "FILLED",
    "CANCELLED": "CANCELLED",
    "REJECTED": "REJECTED",
    "EXPIRED": "EXPIRED",
}


def _risk_fill(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    """Cumulative fill of one Risk observation from its complete fill rows.

    Only events that carry the provider's fill history (``records`` of a
    ``get_deals`` observation: size, price, fee, fee currency) define fills;
    ``None`` when the event has no fill history. Fees: one currency per
    intent; a fee without its currency or mixed currencies cannot be booked.
    """

    if payload.get("operation") != "get_deals" or not isinstance(payload.get("records"), list):
        return None
    total = Decimal("0")
    notional = Decimal("0")
    fee_total: Decimal | None = None
    currencies: set[str] = set()
    for row in payload["records"]:
        if not isinstance(row, Mapping):
            raise ServiceStrategyLedgerError("LEDGER_JOURNAL_EVENT_INVALID")
        try:
            size = Decimal(str(row["size"]))
            price = Decimal(str(row["price"]))
        except (KeyError, InvalidOperation, ValueError):
            raise ServiceStrategyLedgerError("LEDGER_JOURNAL_EVENT_INVALID") from None
        if not size.is_finite() or not price.is_finite() or size <= 0 or price <= 0:
            raise ServiceStrategyLedgerError("LEDGER_JOURNAL_EVENT_INVALID")
        total += size
        notional += size * price
        fee = row.get("fee")
        if fee is not None:
            currency = row.get("fee_currency")
            if type(currency) is not str or not currency:
                raise ServiceStrategyLedgerError("LEDGER_FEE_CURRENCY_UNKNOWN")
            try:
                amount = Decimal(str(fee))
            except (InvalidOperation, ValueError):
                raise ServiceStrategyLedgerError("LEDGER_JOURNAL_EVENT_INVALID") from None
            if not amount.is_finite() or amount < 0:
                raise ServiceStrategyLedgerError("LEDGER_JOURNAL_EVENT_INVALID")
            currencies.add(currency)
            fee_total = amount if fee_total is None else fee_total + amount
    if len(currencies) > 1:
        raise ServiceStrategyLedgerError("LEDGER_FEE_CURRENCY_MIXED")
    if total == 0:
        return {"cumulative_quantity": "0"}
    result: dict[str, Any] = {
        "cumulative_quantity": str(total),
        "average_price": str(notional / total),
    }
    if fee_total is not None:
        result["fee"] = str(fee_total)
        result["fee_currency"] = next(iter(currencies))
    return result


class _RiskJournalPort:
    """The Risk account owner's journal (iteration 60 R3).

    One account scope; its events carry one per-account revision sequence,
    which is the ledger cursor. The journal identity is the record hash of the
    scope's first event, so a replaced journal file is detected.
    """

    def __init__(self, journal: Any, targets: Mapping[WireScope, Any]) -> None:
        from bt_api_py.service_risk_order_journal import RiskOrderJournal

        if type(journal) is not RiskOrderJournal:
            raise ServiceStrategyLedgerError("STRATEGY_LEDGER_JOURNAL_INVALID")
        scopes = {getattr(target, "account_scope", None) for target in targets.values()}
        if len(scopes) != 1 or None in scopes:
            raise ServiceStrategyLedgerError("STRATEGY_LEDGER_TARGETS_INVALID")
        (self._scope,) = scopes
        accounts = {wire_scope.account_ref for wire_scope in targets}
        if len(accounts) != 1:
            raise ServiceStrategyLedgerError("STRATEGY_LEDGER_TARGETS_INVALID")
        (self.account_ref,) = accounts
        strategies: dict[WireScope, str] = {}
        for wire_scope, target in targets.items():
            if wire_scope.strategy_ref is None or wire_scope.manual_book_ref is not None:
                continue
            if target.strategy_id != wire_scope.strategy_ref:
                raise ServiceStrategyLedgerError("STRATEGY_LEDGER_TARGETS_INVALID")
            strategies[wire_scope] = wire_scope.strategy_ref
        if not strategies:
            raise ServiceStrategyLedgerError("STRATEGY_LEDGER_TARGETS_INVALID")
        self._journal = journal
        self._strategies = MappingProxyType(strategies)
        self._strategy_refs = frozenset(strategies.values())
        self._intent_strategy: dict[tuple[str, str], str | None] = {}
        self._source: str | None = None
        self.account_keys = frozenset({self._scope.key})

    def source(self) -> str | None:
        if self._source is None:
            first = self._journal.strategy_ledger_journal_identity(self._scope)
            if first is None:
                return None
            self._source = "risk-journal:" + first
        return self._source

    def strategy_for(self, wire_scope: WireScope) -> str | None:
        return _resolve(self._strategies, wire_scope)

    def has(self, intent_id: str) -> bool:
        return self._journal.has_intent_id(self._scope, intent_id)

    def read(self, cursor: int, limit: int) -> list[tuple[int, dict[str, Any] | None]]:
        identity = self._journal.strategy_ledger_journal_identity(self._scope)
        if identity is None or "risk-journal:" + identity != self._source:
            raise ServiceStrategyLedgerError("LEDGER_JOURNAL_SOURCE_CHANGED")
        events = self._journal.strategy_ledger_events_after(self._scope, cursor, limit)
        return [(event.revision, self._plan(event)) for event in events]

    def _strategy_of(self, event: Any) -> str | None:
        key = (event.principal_ref, event.intent_id)
        if key not in self._intent_strategy:
            command = self._journal.get_command(self._scope, event.principal_ref, event.intent_id)
            wire = None if command is None else command.wire_scope
            strategy = wire.get("strategy_ref") if isinstance(wire, Mapping) else None
            manual = wire.get("manual_book_ref") if isinstance(wire, Mapping) else None
            self._intent_strategy[key] = (
                strategy if strategy in self._strategy_refs and manual is None else None
            )
        return self._intent_strategy[key]

    def _plan(self, event: Any) -> dict[str, Any] | None:
        kind = event.event_type
        if kind in _RISK_NO_EFFECT or self._strategy_of(event) is None:
            return None
        intent_id = event.intent_id
        if kind in _RISK_REJECTED:
            return {"intent_id": intent_id, "step": "FINISH", "final_state": "REJECTED"}
        if kind == "UNKNOWN":
            return {"intent_id": intent_id, "step": "UNKNOWN"}
        if kind not in _RISK_PROGRESS:
            raise ServiceStrategyLedgerError("LEDGER_JOURNAL_EVENT_UNSUPPORTED")
        payload = event.payload
        fill = _risk_fill(payload) or {}
        if fill and Decimal(fill["cumulative_quantity"]) == 0:
            fill = {}
        public = payload.get("public_state")
        final = _RISK_FINAL_PUBLIC.get(public) if type(public) is str else None
        if kind == "ACKED_TERMINAL" and final is None:
            # Resolved terminal without fills: the reservation is released.
            final = "CANCELLED"
        if final is not None:
            return {"intent_id": intent_id, "step": "FINISH", "final_state": final, **fill}
        if public == "UNKNOWN":
            return {"intent_id": intent_id, "step": "UNKNOWN", **fill}
        if fill:
            return {"intent_id": intent_id, "step": "EXECUTION", **fill}
        if kind == "ORDER_OBSERVATION":
            # The first provider observation of the dispatched order.
            return {"intent_id": intent_id, "step": "SUBMITTED"}
        return None


class ServiceStrategyLedgerBinding:
    """One account's strategy ledger, bound to the account owner's journal.

    The journal is either the Execution account owner's ``SqliteExecutionStore``
    (``store=``, targets ``WireScope -> ExecutionScope``) or, through
    :meth:`for_risk_journal`, the Risk account owner's ``RiskOrderJournal``
    (targets ``WireScope -> RiskOrderTarget``, iteration 60 R3). Both use the
    same admission, projection, recovery and read rules below.
    """

    def __init__(
        self,
        option: Mapping[str, Any],
        *,
        store: Any = None,
        targets: Mapping[WireScope, Any],
        account_positions: object,
        clock_ns: Callable[[], int] | None = None,
        _port: Any = None,
    ) -> None:
        if not isinstance(targets, Mapping) or not targets:
            raise ServiceStrategyLedgerError("STRATEGY_LEDGER_TARGETS_INVALID")
        port = _port if _port is not None else _ExecutionJournalPort(store, targets)
        option = parse_strategy_ledger_option(option)
        report = _subscription_report(account_positions)
        account_ref = port.account_ref
        if getattr(account_positions, "account_ref", account_ref) != account_ref:
            raise ServiceStrategyLedgerError("STRATEGY_LEDGER_POSITIONS_ACCOUNT_MISMATCH")
        kwargs: dict[str, Any] = dict(_policy(option))
        if "reconciliation_max_age_seconds" in option:
            kwargs["reconciliation_max_age_ns"] = (
                option["reconciliation_max_age_seconds"] * 1_000_000_000
            )
        if clock_ns is not None:
            kwargs["clock_ns"] = clock_ns
        try:
            self.ledger = SqliteStrategyLedger(option["path"], account_ref=account_ref, **kwargs)
        except StrategyLedgerError as error:
            raise ServiceStrategyLedgerError(error.code) from None
        self.account_ref = account_ref
        self._port = port
        self._targets = MappingProxyType(dict(targets))
        self._account_positions = report
        self._lock = threading.RLock()
        self._blocked: str | None = None
        self._observation_sink: Callable[[WireScope, Any], Any] | None = None
        self._closed = False

    @classmethod
    def for_risk_journal(
        cls,
        option: Mapping[str, Any],
        *,
        journal: Any,
        targets: Mapping[WireScope, Any],
        account_positions: object,
        clock_ns: Callable[[], int] | None = None,
    ) -> ServiceStrategyLedgerBinding:
        """The Risk account owner's ledger: same rules, Risk journal events."""

        if not isinstance(targets, Mapping) or not targets:
            raise ServiceStrategyLedgerError("STRATEGY_LEDGER_TARGETS_INVALID")
        return cls(
            option,
            targets=targets,
            account_positions=account_positions,
            clock_ns=clock_ns,
            _port=_RiskJournalPort(journal, targets),
        )

    def strategy_for(self, wire_scope: WireScope) -> str | None:
        """The strategy whose ledger admits orders of ``wire_scope`` (``None``: not one)."""

        return self._port.strategy_for(wire_scope)

    @property
    def journal_source(self) -> str | None:
        """The journal identity the ledger cursor belongs to (``None`` before any event)."""

        return self._port.source()

    # -- state ---------------------------------------------------------------

    @property
    def blocked(self) -> str | None:
        """The finite reason the projection stopped, or ``None``."""

        return self._blocked

    def attach_observation_sink(self, sink: Callable[[WireScope, Any], Any]) -> None:
        """Called once by the account-owner service that records observations."""

        if not callable(sink) or self._observation_sink is not None:
            raise ServiceStrategyLedgerError("STRATEGY_LEDGER_SINK_INVALID")
        self._observation_sink = sink

    def record_provider_observation(self, wire_scope: WireScope, observation: Any) -> Any:
        """Host entry for later provider evidence (fills, fees, terminal states).

        The observation is committed to the execution journal first, then
        projected into the ledger; it is never applied to the ledger directly.
        """

        if self._observation_sink is None:
            raise ServiceStrategyLedgerError("STRATEGY_LEDGER_SINK_UNAVAILABLE")
        return self._observation_sink(wire_scope, observation)

    # -- projection ----------------------------------------------------------

    def sync(self) -> int:
        """Apply journal events after the ledger cursor; return how many applied."""

        with self._lock:
            if self._blocked is not None:
                raise ServiceStrategyLedgerError("STRATEGY_LEDGER_PROJECTION_BLOCKED")
            applied = 0
            try:
                source = self._port.source()
                if source is None:
                    return 0  # the journal has no event yet
                cursor = self.ledger.journal_cursor(source)
                while True:
                    events = self._port.read(cursor, _PAGE)
                    if not events:
                        return applied
                    for sequence, plan in events:
                        if plan is not None and self.ledger.apply_journal_event(
                            source, sequence, **plan
                        ):
                            applied += 1
                        cursor = sequence
                    # Events of other scopes still move the durable cursor.
                    if self.ledger.journal_cursor(source) < cursor:
                        self.ledger.apply_journal_event(source, cursor)
                    if len(events) < _PAGE:
                        return applied
            except ServiceStrategyLedgerError as error:
                self._blocked = error.code
                raise ServiceStrategyLedgerError("STRATEGY_LEDGER_PROJECTION_BLOCKED") from None
            except StrategyLedgerError as error:
                self._blocked = error.code
                raise ServiceStrategyLedgerError("STRATEGY_LEDGER_PROJECTION_BLOCKED") from None

    def recover(self) -> None:
        """Before serving: catch up, then release reservations the journal never saw."""

        with self._lock:
            self.sync()
            for intent_id in self.ledger.reserved_intents():
                if not self._port.has(intent_id):
                    self.ledger.finish(intent_id, "REJECTED")

    def journal_lacks(self, wire_scope: WireScope, intent_id: str) -> bool:
        """Whether the journal provably holds no record of ``intent_id`` (R3).

        Used to state a pre-journal refusal as ``NOT_JOURNALED``. Any doubt
        (unserved scope, read failure) answers ``False``.
        """

        if type(wire_scope) is not WireScope or type(intent_id) is not str or not intent_id:
            return False
        try:
            if self._port.strategy_for(wire_scope) is None:
                return False
            return not self._port.has(intent_id)
        except Exception:
            return False

    # -- admission -----------------------------------------------------------

    def admit(self, wire_scope: WireScope, intent: Any) -> str | None:
        """Reserve ``intent`` in the ledger; return a refusal code or ``None``.

        Called before the journal sees the order. A refusal means nothing may
        be journaled or dispatched for it.
        """

        strategy_ref = self._port.strategy_for(wire_scope)
        if strategy_ref is None:
            return None  # not a strategy scope: the order entry decides alone
        with self._lock:
            try:
                self.sync()
            except ServiceStrategyLedgerError as error:
                return error.code
            try:
                record = self._port.has(intent.intent_id) or None
                try:
                    existing: str | None = self.ledger.intent_state(intent.intent_id)
                except StrategyLedgerError as error:
                    if error.code != "INTENT_NOT_FOUND":
                        raise
                    existing = None
                if existing is not None:
                    if record is not None:
                        return None  # the journal replays or refuses this key
                    if existing != "RESERVED":
                        # Finished here and never journaled: a new dispatch
                        # under this key would bypass the ledger's admission.
                        return "LEDGER_INTENT_FINISHED"
                elif record is not None:
                    return "LEDGER_INTENT_UNKNOWN"
                effect = self.ledger.reserve(
                    LedgerIntent(
                        intent.intent_id,
                        strategy_ref,
                        intent.instrument,
                        str(getattr(intent.side, "value", intent.side)),
                        intent.quantity,
                        intent.price,
                    )
                )
                declared = str(getattr(intent.position_effect, "value", intent.position_effect))
                if (effect == "OPEN") != (declared == "OPEN"):
                    if existing is None:
                        self.ledger.finish(intent.intent_id, "REJECTED")
                    return "LEDGER_POSITION_EFFECT_MISMATCH"
            except StrategyLedgerError as error:
                return error.code
            except Exception:
                return "STRATEGY_LEDGER_UNAVAILABLE"
            return None

    def settle(self, wire_scope: WireScope | None, intent_id: str | None) -> None:
        """After the order entry returned: release an unjournaled reservation, project."""

        strategy_ref = None if wire_scope is None else self._port.strategy_for(wire_scope)
        with self._lock:
            if intent_id and strategy_ref is not None:
                try:
                    unjournaled = not self._port.has(intent_id)
                    if unjournaled and self.ledger.intent_state(intent_id) == "RESERVED":
                        # The order entry records every intent before a
                        # provider call: no record proves no dispatch.
                        self.ledger.finish(intent_id, "REJECTED")
                except Exception:
                    pass  # unprovable: the reservation keeps counting (fail closed)
            try:
                self.sync()
            except ServiceStrategyLedgerError:
                pass  # recorded in ``blocked``; reads and admissions refuse

    # -- reads ---------------------------------------------------------------

    def read_strategy_ledger(self, *, account_key: str) -> Any:
        from bt_api_py.service_execution_account_books import StrategyLedgerBookSourceV1

        if account_key not in self._port.account_keys:
            raise ServiceStrategyLedgerError("STRATEGY_LEDGER_ACCOUNT_MISMATCH")
        self.sync()
        return StrategyLedgerBookSourceV1(self.ledger, self._account_positions)

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                self.ledger.close()


class StrategyLedgerAccountBookSource:
    """Account-book resource: the host's optional source plus the strategy ledger.

    Strategy views are served from the ledger; account and manual-book views
    use the host's source when it has one and are refused otherwise.
    """

    def __init__(self, binding: ServiceStrategyLedgerBinding, host_source: object | None) -> None:
        if type(binding) is not ServiceStrategyLedgerBinding:
            raise ServiceStrategyLedgerError("STRATEGY_LEDGER_BINDING_INVALID")
        self._binding = binding
        self._host = host_source

    def authorize_account_view(self, **kwargs: Any) -> bool:
        authorize = getattr(self._host, "authorize_account_view", None)
        return bool(callable(authorize) and authorize(**kwargs) is True)

    def authorize_manual_book_view(self, **kwargs: Any) -> bool:
        authorize = getattr(self._host, "authorize_manual_book_view", None)
        return bool(callable(authorize) and authorize(**kwargs) is True)

    def read_inputs(self, **kwargs: Any) -> Any:
        read = getattr(self._host, "read_inputs", None)
        if not callable(read):
            raise ServiceStrategyLedgerError("ACCOUNT_BOOK_INPUTS_UNAVAILABLE")
        return read(**kwargs)

    def read_strategy_ledger(self, *, account_key: str) -> Any:
        return self._binding.read_strategy_ledger(account_key=account_key)

    def __getattr__(self, name: str) -> Any:
        # Optional host readers (financial directory, recovery directory).
        if name.startswith("_") or self._host is None:
            raise AttributeError(name)
        return getattr(self._host, name)
