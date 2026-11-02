"""Direct-mode strategy account books on the shared neutral contract.

Iteration 60, step 60.07. Without a Gateway/service host, a direct process
still owns one execution store per account and reconciles it with
``ExecutionAccountBookService``. This module turns that local reconciliation
into the same ``StrategyAccountBookSnapshot`` the service path returns:

* the owner projection is produced by the service module's own
  ``_owner_projection`` and serialized by its ``_reply``;
* the result is validated by Base ``parse_strategy_account_book_reply``.

So direct and Gateway readers share one projection and one parser rather than
two account-book implementations. A local reconciliation that is not
complete fails closed exactly like a remote one
(``STRATEGY_BOOK_COVERAGE_INCOMPLETE``). Cash and value stay unavailable
unless the caller supplies the persisted financial projection for the same
cut; nothing is inferred from the account balance (plan INV-10).
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from typing import Any

from bt_api_base.account_book_protocol import (
    AccountBookProjectionError,
    StrategyAccountBookSnapshot,
    parse_strategy_account_book_reply,
)
from bt_api_execution import AccountBookReconciliation, AllocationOwnerKind

from .service_execution_account_books import (
    _financial_unavailable,
    _owner_projection,
    _reply,
)

__all__ = [
    "DirectStrategyAccountBookReader",
    "strategy_account_book_snapshot",
]

FinancialProvider = Callable[[AccountBookReconciliation, str], Mapping[str, Any]]


def strategy_account_book_snapshot(
    reconciliation: AccountBookReconciliation,
    *,
    strategy_ref: str,
    account_ref: str,
    financial: Mapping[str, Any] | None = None,
) -> StrategyAccountBookSnapshot:
    """Project one strategy's book from a local reconciliation."""

    if type(reconciliation) is not AccountBookReconciliation:
        raise AccountBookProjectionError("INVALID_REPLY")
    result = dict(_owner_projection(reconciliation, (AllocationOwnerKind.STRATEGY, strategy_ref)))
    result["financial"] = (
        dict(financial)
        if financial is not None
        else _financial_unavailable("STRATEGY_FINANCIAL_UNAVAILABLE")
    )
    return parse_strategy_account_book_reply(
        _reply(True, "OK", result), strategy_ref=strategy_ref, account_ref=account_ref
    )


class DirectStrategyAccountBookReader:
    """Read one strategy's book from a direct-mode account coordinator.

    ``reconcile`` returns the account's current ``AccountBookReconciliation``
    (the coordinator supplies the provider snapshots and allocation
    baseline). ``financial`` optionally returns the persisted financial
    projection for that same reconciliation and strategy.
    """

    def __init__(
        self,
        reconcile: Callable[[], AccountBookReconciliation],
        *,
        strategy_ref: str,
        account_ref: str,
        financial: FinancialProvider | None = None,
    ) -> None:
        if not callable(reconcile) or (financial is not None and not callable(financial)):
            raise AccountBookProjectionError("INVALID_SCOPE")
        for value in (strategy_ref, account_ref):
            if type(value) is not str or not value or value != value.strip():
                raise AccountBookProjectionError("INVALID_SCOPE")
        self._reconcile = reconcile
        self._financial = financial
        self.strategy_ref = strategy_ref
        self.account_ref = account_ref
        self._lock = threading.Lock()

    def snapshot(self) -> StrategyAccountBookSnapshot:
        with self._lock:
            reconciliation = self._reconcile()
            financial = (
                self._financial(reconciliation, self.strategy_ref)
                if self._financial is not None
                else None
            )
            return strategy_account_book_snapshot(
                reconciliation,
                strategy_ref=self.strategy_ref,
                account_ref=self.account_ref,
                financial=financial,
            )
