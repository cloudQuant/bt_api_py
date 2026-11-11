"""Account-wide reads of every strategy's funds and book for an account operator (G64-06).

Two read-only operations on ``service.account_read``, granted only through the bundle's
``account_observation.operator_reads`` to an application client of the ``controls`` section
that may act on the whole account (an operator or the Risk service's control principal):

* ``account.strategy_financial {strategy?, currency?}``: the D63-44 projection of each
  declared strategy (or the one named), with exactly the fields the strategy's own
  ``direct.strategy_financial`` returns. The projection holds no start-of-day value.
* ``account.strategy_book {after?, limit}``: a page of each declared strategy's theoretical
  position per declared instrument (``read_strategy_position``) and of the ``external`` book
  per instrument (``read_external_net``).

Each item says whether it was read (``OBSERVED``) or not (``UNAVAILABLE``); ``complete`` is
true only when every item of the reply was read. Nothing here writes, and no value supplied
by the caller is taken as a fact. The Registry admission is held around every journal read.
"""
from __future__ import annotations

import json
from collections.abc import Mapping

from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireRole
from bt_api_gateway.direct_command_journal import DirectCommandJournalError

from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayOwnerServiceCallError
from bt_api_py.service_registry import ServiceCallContext, ServiceDispatchAdmission

OPERATIONS = frozenset({"account.strategy_financial", "account.strategy_book"})
_FINANCIAL_FIELDS = ("model", "revision", "cut_ref", "owner_generation")
_BUCKETS = ("long_today", "long_yesterday", "short_today", "short_yesterday")


def _ref(value: object) -> bool:
    return (type(value) is str and 0 < len(value) <= 256 and value == value.strip()
            and value.isprintable())


def _reject(code: str) -> dict[str, str]:
    return {"status": "REQUEST_REJECTED", "code": code}


class GatewayAccountStrategyReads:
    """Owner-thread projection of the account's strategy funds and books; see the module."""

    def __init__(self, owner, journal, *, account_scope, principals, strategies, instruments,
                 resolve_binding=None):
        if (type(owner) is not GatewayDirectOwner or not owner.is_bound_to(journal)
                or journal.account_scope != account_scope or journal._schema_version < 12
                or type(principals) is not frozenset or any(not _ref(item) for item in principals)
                or type(strategies) is not frozenset or not strategies
                or any(not _ref(item) for item in strategies)
                or type(instruments) is not frozenset or not instruments
                or any(type(item) is not MarketInstrumentKey for item in instruments)
                or (resolve_binding is not None and not callable(resolve_binding))):
            raise ValueError("INVALID_ACCOUNT_STRATEGY_READS")
        self.owner, self.journal, self.account_scope = owner, journal, account_scope
        self.principals, self.strategies, self.instruments = principals, strategies, instruments
        self._resolve_binding = resolve_binding

    def handle(self, operation, context, arguments):
        if (operation not in OPERATIONS or type(context) is not ServiceCallContext
                or not isinstance(arguments, Mapping)
                or context.principal_ref not in self.principals
                or context.identity.role is not WireRole.APPLICATION_CLIENT
                or context.identity.principal_ref != context.principal_ref
                or context.identity.delegate_ref is not None
                or context.scope.account_ref != self.account_scope or context.scope.account_refs
                or context.scope.strategy_ref is not None or context.scope.run_id is not None
                or context.scope.portfolio_ref is not None or context.scope.manual_book_ref is not None
                or context.scope.group_id is not None or context.scope.leg_id is not None):
            return _reject("SCOPE_DENIED")
        admission = context.dispatch_admission
        if (type(admission) is not ServiceDispatchAdmission or not admission.matches_context(context)
                or not admission.matches_operation(operation) or not admission.matches(arguments)):
            return _reject("ADMISSION_DENIED")
        try:
            if operation == "account.strategy_financial":
                return self._financial(context, dict(arguments), admission)
            return self._book(context, dict(arguments), admission)
        finally:
            admission.close()

    def _fits(self, context, response):
        return context.result_fits is None or (callable(context.result_fits)
                                               and context.result_fits(response) is True)

    # -- account.strategy_financial ------------------------------------------------------------

    def _financial(self, context, arguments, admission):
        if (not set(arguments) <= {"strategy", "currency"}
                or any(not _ref(value) for value in arguments.values())):
            return _reject("INVALID_REQUEST")
        if "strategy" in arguments and arguments["strategy"] not in self.strategies:
            return _reject("SCOPE_DENIED")
        journal = self.journal
        if journal._schema_version < 14 or journal._strategy_financial_port is None:
            return _reject("STRATEGY_FINANCIAL_UNAVAILABLE")
        strategies = sorted({arguments["strategy"]} if "strategy" in arguments else self.strategies)

        def read():
            if "currency" in arguments:
                keys = [(strategy, arguments["currency"]) for strategy in strategies]
            else:
                # Every currency the journal holds a projection in, for the strategies asked.
                with admission.scope() as recheck, journal._transaction() as db:
                    found = {(row[0], row[1]) for row in db.execute(
                        "SELECT DISTINCT strategy,currency FROM strategy_financial_snapshots WHERE account=?",
                        (journal.account_scope,)) if row[0] in strategies}
                    recheck()
                keys = sorted(found | {(strategy, None) for strategy in strategies
                                       if not any(item[0] == strategy for item in found)})
            items = []
            for strategy, currency in keys:
                item = {"strategy": strategy, "currency": currency, "status": "UNAVAILABLE"}
                if currency is not None:
                    try:
                        snapshot = journal.read_strategy_financial(strategy, currency,
                                                                   admission_fence=admission.scope)
                        if (snapshot.account != journal.account_scope or snapshot.strategy != strategy
                                or snapshot.currency != currency or snapshot.journal_id != journal.journal_id
                                or snapshot.owner_generation != journal.generation):
                            raise DirectCommandJournalError("STRATEGY_FINANCIAL_UNAVAILABLE")
                    except DirectCommandJournalError as error:
                        if error.code != "STRATEGY_FINANCIAL_UNAVAILABLE":
                            raise
                    else:
                        item.update({name: getattr(snapshot, name) for name in _FINANCIAL_FIELDS})
                        item.update(status="OBSERVED", snapshot_digest=snapshot.digest,
                                    value=format(snapshot.value, "f"), cash=format(snapshot.cash, "f"))
                items.append(item)
            return {"status": "OBSERVED", "schema_version": 1, "account_scope": journal.account_scope,
                    "owner_generation": journal.generation,
                    "complete": all(item["status"] == "OBSERVED" for item in items), "strategies": items}
        try:
            response = self.owner.submit_service_call(read).result(timeout_seconds=1.0)
        except GatewayOwnerServiceCallError:
            return _reject("STRATEGY_FINANCIAL_UNAVAILABLE")
        return response if self._fits(context, response) else _reject("REPLY_TOO_LARGE")

    # -- account.strategy_book -----------------------------------------------------------------

    def _rows(self):
        rows = []
        for instrument in self.instruments:
            for book, strategy in [("external", None)] + [("strategy", item) for item in self.strategies]:
                cursor = json.dumps([book, strategy or "", instrument.venue, instrument.native_symbol],
                                    separators=(",", ":"))
                rows.append((cursor, book, strategy, instrument))
        return sorted(rows, key=lambda row: row[0])

    def _unit(self, instrument):
        """The quantity unit the installed resolver binds to the instrument."""
        from bt_api_gateway.strategy_position_ledger import StrategyInstrumentBinding

        try:
            binding = self._resolve_binding({"symbol": instrument.native_symbol, "exchange_id": instrument.venue})
        except Exception as error:
            # The installed resolver does not bind it: no position is known for it.
            raise DirectCommandJournalError("STRATEGY_LEDGER_UNAVAILABLE") from error
        if type(binding) is not StrategyInstrumentBinding or binding.instrument != instrument:
            raise DirectCommandJournalError("STRATEGY_LEDGER_UNAVAILABLE")
        return binding.quantity_unit

    def _book(self, context, arguments, admission):
        after = arguments.get("after", "")
        if (not {"limit"} <= set(arguments) <= {"limit", "after"} or type(arguments["limit"]) is not int
                or not 1 <= arguments["limit"] <= 256 or type(after) is not str or len(after) > 1024):
            return _reject("INVALID_REQUEST")
        journal = self.journal
        if journal._schema_version < 11 or journal._strategy_ledger_ports is None or self._resolve_binding is None:
            return _reject("STRATEGY_LEDGER_UNAVAILABLE")
        candidates = [row for row in self._rows() if row[0] > after]
        page, has_more = candidates[:arguments["limit"]], len(candidates) > arguments["limit"]

        def read():
            items = []
            with admission.scope() as recheck:
                for cursor, book, strategy, instrument in page:
                    item = {"cursor": cursor, "book": book, "instrument": instrument.to_dict(),
                            "status": "UNAVAILABLE"}
                    if book == "strategy":
                        item["strategy"] = strategy
                    try:
                        if book == "external":
                            day, cut, quantity = journal.read_external_net(instrument)
                            item.update(status="OBSERVED", net=format(quantity, "f"), trading_day=day, cut=cut)
                        else:
                            payload = {"symbol": instrument.native_symbol, "exchange_id": instrument.venue,
                                       "quantity_unit": self._unit(instrument)}
                            position = journal.read_strategy_position(strategy, payload,
                                                                      expected_instrument=instrument)
                            item.update({name: format(getattr(position, name), "f") for name in _BUCKETS})
                            item.update(status="OBSERVED", trading_day=position.trading_day,
                                        projected_buckets=position.projected_buckets,
                                        long=format(position.long_today + position.long_yesterday, "f"),
                                        short=format(position.short_today + position.short_yesterday, "f"),
                                        net=format(position.net, "f"))
                    except DirectCommandJournalError as error:
                        if error.code != "STRATEGY_LEDGER_UNAVAILABLE":
                            raise
                    items.append(item)
                recheck()
            return {"status": "OBSERVED", "schema_version": 1, "account_scope": journal.account_scope,
                    "owner_generation": journal.generation,
                    "complete": all(item["status"] == "OBSERVED" for item in items),
                    "rows": items, "has_more": has_more, "next_after": page[-1][0] if page else None}
        try:
            response = self.owner.submit_service_call(read).result(timeout_seconds=1.0)
        except GatewayOwnerServiceCallError:
            return _reject("STRATEGY_LEDGER_UNAVAILABLE")
        return response if self._fits(context, response) else _reject("REPLY_TOO_LARGE")


__all__ = ("OPERATIONS", "GatewayAccountStrategyReads")
