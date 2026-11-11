"""Block one contract while a return on it belongs to nobody (D63-59).

A trade or an order return that the Gateway cannot attribute to one of its own
commands may still be a strategy's: the strategies' ledgers for that contract
are then not known to be complete. Until that is settled the contract is frozen
for every strategy: new opens and closes on it are refused, other contracts go
on, and cancellation stays possible.

The block is an ordinary control cause with the contract as its scope, so it is
stored in the journal, survives a restart, shows up in the operator's listing
and is released with the operator's ordinary resume, which records who did it.

This watcher runs on the account owner thread and does two things:

* when a contract has unresolved returns that no cause of this watcher covers
  yet, it applies a FREEZE cause for the contract and logs a warning;
* when a contract with such a cause has no unresolved return any more (the
  journal attributed them after all), it releases the cause itself.

A cause that an operator released is not applied again for the same returns:
its id names exactly the set of returns it was applied for. A new unresolved
return on the contract is a new set and blocks the contract again.

After an operator's release the watcher hands the released trades to the
profile's ``record_released`` callback, which records them as external trades:
the operator has vouched that they are not a strategy's. From then on the
strategies' own positions and close capacity on the contract are available
again and the account comparison counts those trades as external holdings.

Limits:

* A return in conflict (the same trade reported with different content) cannot
  be recorded as external; the strategies' ledgers for its contract stay
  unavailable after a release until it is investigated.
* It sees what the journal holds. A return the Gateway never received is not
  detected here; the account comparison is the check for that.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

_LOG = logging.getLogger(__name__)

ORIGIN_PREFIX = "system:unresolved_return:"
RELEASE_PREFIX = "operator-release:"
_AUTOMATIC_RELEASE = "resolved:"
_MAX_UNRESOLVED = 4096
_MAX_CAUSES = 4096


def unresolved_return_origin(account_scope: str) -> str:
    """The origin of this watcher's causes for one account."""
    return ORIGIN_PREFIX + hashlib.sha256(account_scope.encode("utf-8")).hexdigest()


def operator_release_ref(cause) -> str:
    """Names one operator release of one contract cause (cause ids contain no ``|``)."""
    return RELEASE_PREFIX + cause["cause_id"] + "|" + cause["resume_id"]


def released_by_operator(db, account: str, fact: Any, evidence: Any) -> bool:
    """Whether an external-trade record rests on an operator's release of the trade's contract.

    True only when the record names a contract cause of this watcher that an
    operator released (not one the watcher lifted itself) and the cause is for
    the contract of this very return.
    """
    reference = getattr(evidence, "exclusion_ref", None)
    if type(reference) is not str or not reference.startswith(RELEASE_PREFIX) or "|" not in reference:
        return False
    cause_id, resume_id = reference[len(RELEASE_PREFIX):].rsplit("|", 1)
    row = db.execute("SELECT scope_value, resume_id, origin FROM direct_control_causes "
                     "WHERE account=? AND cause_id=?", (account, cause_id)).fetchone()
    # A stored resume id is never empty and exists only on a released cause (the journal keeps
    # both), and only this watcher's contract causes carry its origin: released, a contract cause
    # and the right contract all follow from the three comparisons below.
    return (row is not None
            and row[1] == resume_id and not resume_id.startswith(_AUTOMATIC_RELEASE)
            and row[2] == unresolved_return_origin(account)
            and row[0] == fact.instrument.venue + ":" + fact.instrument.native_symbol
            and evidence.account_scope == account and evidence.fact_digest == fact.fact_digest())


def contract_has_undecided_orders(db, account: str, scope_value: str) -> bool:
    """Whether a command on the contract may still be at the venue with an unknown outcome.

    An unattributed return on a contract may be the return of exactly such a
    command, so an operator's release is refused while one exists.
    """
    venue, symbol = scope_value.split(":", 1)
    rows = db.execute("SELECT payload_json FROM direct_journal_commands WHERE account=? "
                      "AND status IN ('DISPATCHING','UNKNOWN')", (account,)).fetchall()
    for row in rows:
        try:
            payload = json.loads(row[0])
        except (ValueError, TypeError):
            return True
        if payload.get("symbol") == symbol and payload.get("exchange_id") in (None, "", venue):
            return True
    return False


class GatewayInstrumentBlock:
    """Owner-thread watcher of unresolved returns; see the module docstring."""

    def __init__(self, *, journal: Any, control: Any, interval_seconds: float = 1.0,
                 clock: Callable[[], float] = time.monotonic,
                 record_released: Callable[[dict, list], object] | None = None) -> None:
        from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
        from bt_api_gateway.direct_control import GatewayDirectControl

        if (type(journal) is not GatewayDirectCommandJournal or type(control) is not GatewayDirectControl
                or control._journal is not journal or not callable(clock)
                or (record_released is not None and not callable(record_released))
                or type(interval_seconds) not in (int, float) or not 0 < interval_seconds <= 60):
            raise ValueError("INVALID_GATEWAY_INSTRUMENT_BLOCK")
        self._journal, self._control, self._clock = journal, control, clock
        self._interval = float(interval_seconds)
        self._origin = unresolved_return_origin(journal.account_scope)
        self._thread = threading.current_thread()
        self._next = 0.0
        self._failed: str | None = None
        # Causes an operator released while their returns are still unresolved: not applied again.
        self._released: set[str] = set()
        # Called once per released cause with the cause and the digests it was applied for.
        self._record_released = record_released
        self._recorded: set[str] = set()

    @property
    def origin(self) -> str:
        return self._origin

    def _unresolved(self) -> dict[str, list[str]]:
        """Contract scope value -> digests of its unresolved returns."""
        from bt_api_gateway.direct_control import instrument_scope_value

        account = self._journal.account_scope
        with self._journal._transaction() as db:
            rows = db.execute(
                "SELECT fact_digest, fact_json FROM direct_execution_facts WHERE account=? "
                "AND disposition IN ('UNATTRIBUTED','CONFLICT') ORDER BY fact_digest LIMIT ?",
                (account, _MAX_UNRESOLVED + 1)).fetchall()
        if len(rows) > _MAX_UNRESOLVED:
            raise OverflowError("TOO_MANY_UNRESOLVED_RETURNS")
        found: dict[str, list[str]] = {}
        for digest, raw in rows:
            instrument = json.loads(raw)["instrument"]
            scope = instrument_scope_value(instrument["venue"], instrument["native_symbol"])
            found.setdefault(scope, []).append(digest)
        return found

    def _cause_id(self, scope: str, digests: list[str]) -> str:
        return "unresolved-return:" + scope + ":" + hashlib.sha256(
            json.dumps(sorted(digests), separators=(",", ":")).encode("ascii")).hexdigest()[:16]

    def maintain(self) -> None:
        """One pass, at most every ``interval_seconds``. Never raises."""
        if threading.current_thread() is not self._thread:
            return
        now = self._clock()
        if now < self._next:
            return
        self._next = now + self._interval
        try:
            self._pass()
            if self._failed is not None:
                self._failed = None
                _LOG.info("Unresolved returns are being watched again")
        except Exception as error:
            # Nothing was decided in this pass; what is already blocked stays blocked.
            code = getattr(error, "code", None) or str(error) or type(error).__name__
            if code != self._failed:
                self._failed = code
                _LOG.error("Unresolved returns could not be checked (contracts are not being blocked "
                           "for new unresolved returns): %s", code)

    def _pass(self) -> None:
        unresolved = self._unresolved()
        active = {row["cause_id"]: row for row in self._control.active_system_causes(self._origin)}
        if len(active) > _MAX_CAUSES:
            raise OverflowError("TOO_MANY_INSTRUMENT_CAUSES")
        for scope, digests in sorted(unresolved.items()):
            cause_id = self._cause_id(scope, digests)
            if cause_id in active or cause_id in self._recorded:
                continue
            if cause_id in self._released and self._record_released is None:
                continue
            cause = self._control.apply(cause_id, "FREEZE", scope_kind="instrument", scope_value=scope,
                                        origin=self._origin)
            if cause["active"] == 1 and cause["version"] == 1:
                _LOG.warning(
                    "Contract %s is blocked for every strategy: %d return(s) on it belong to no known "
                    "order (cause %s). New opens and closes on it are refused until an operator "
                    "releases the cause; cancellation is still possible.", scope, len(digests), cause_id)
            elif cause["active"] == 0:
                self._released.add(cause_id)
                if (self._record_released is not None and cause_id not in self._recorded
                        and not str(cause["resume_id"]).startswith(_AUTOMATIC_RELEASE)):
                    # The operator vouched for exactly these returns: let the profile record the
                    # trades among them as external, so the strategies' ledgers are whole again.
                    try:
                        recorded = self._record_released(dict(cause), list(digests))
                        _LOG.info("Contract %s: %s released trade(s) recorded as external (cause %s)",
                                  scope, recorded, cause_id)
                    except Exception as error:
                        # Not retried on every pass; a restart tries once more.
                        _LOG.warning(
                            "Contract %s: its released trades could not be recorded as external (%s); the "
                            "strategies' positions on it stay unavailable (cause %s)", scope,
                            getattr(error, "code", None) or type(error).__name__, cause_id)
                    self._recorded.add(cause_id)
        for cause_id, row in sorted(active.items()):
            if row["scope_value"] in unresolved:
                continue
            # Every return on the contract has been attributed since: nothing is unknown any more.
            self._control.resume(cause_id, expected_version=row["version"], operation_id="resolved:" + cause_id)
            _LOG.info("Contract %s is no longer blocked: its returns were attributed (cause %s)",
                      row["scope_value"], cause_id)


__all__ = (
    "GatewayInstrumentBlock",
    "ORIGIN_PREFIX",
    "RELEASE_PREFIX",
    "contract_has_undecided_orders",
    "operator_release_ref",
    "released_by_operator",
    "unresolved_return_origin",
)
