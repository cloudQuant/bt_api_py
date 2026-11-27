"""Account-wide read-only recent execution/trade page for an account operator (Iter 74.02).

One read-only operation on ``service.account_read``:
  - ``account.executions.recent {limit, before_sequence?}``

Reads directly from ``GatewayDirectExecutionLedger.read_recent_events()`` on the
owner thread without creating or mutating any consumer cursor or ACK state.
Projected rows are redacted public summaries carrying no raw broker credentials
or file paths.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from bt_api_base.direct_write_protocol import DirectExecutionEventKind
from bt_api_base.service_protocol import WireRole
from bt_api_gateway.direct_command_journal import DirectCommandJournalError
from bt_api_gateway.direct_execution_journal import GatewayDirectExecutionLedger

from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayOwnerServiceCallError
from bt_api_py.service_registry import (
    ServiceCallContext,
    ServiceDispatchAdmission,
    ServiceResultTooLargeError,
)

ACCOUNT_EXECUTION_HISTORY_OPERATIONS = frozenset({"account.executions.recent"})


def _ref(value: object) -> bool:
    return (
        type(value) is str
        and 0 < len(value) <= 256
        and value == value.strip()
        and value.isprintable()
    )


def _reject(code: str) -> dict[str, str]:
    return {"status": "REQUEST_REJECTED", "code": code}


def _kind_label(kind: Any) -> str:
    if kind is DirectExecutionEventKind.TRADE:
        return "trade"
    if kind is DirectExecutionEventKind.ORDER:
        return "order"
    if kind is DirectExecutionEventKind.ORDER_ERROR:
        return "order_error"
    if kind is DirectExecutionEventKind.CANCEL_ERROR:
        return "cancel_error"
    return str(getattr(kind, "value", kind))


def _project_execution_event(event: Any) -> dict[str, Any]:
    fact = event.fact
    inst = fact.instrument
    kind = _kind_label(fact.kind)
    is_trade = kind == "trade"
    quantity = fact.trade_quantity if is_trade else (fact.order_quantity or fact.filled_quantity)
    price = fact.trade_price if is_trade else fact.limit_price
    trade_ref = ":".join(fact.trade_key) if fact.trade_key else None
    status_val = getattr(fact.status, "value", fact.status)
    return {
        "sequence": int(event.sequence),
        "strategy": event.strategy_scope,
        "kind": kind,
        "venue": inst.venue,
        "instrument_id": inst.instrument_id,
        "side": fact.side,
        "offset": fact.offset,
        "status": status_val,
        "quantity": quantity,
        "filled_quantity": fact.filled_quantity,
        "remaining_quantity": fact.remaining_quantity,
        "price": price,
        "trading_day": fact.trading_day,
        "trade_time": fact.trade_time,
        "reservation_id": event.reservation_id,
        "order_ref": fact.venue_key,
        "trade_ref": trade_ref,
        "error_code": fact.error_code,
    }


class GatewayAccountRecentExecutions:
    """Owner-thread read-only projection of recent account execution & trade events."""

    def __init__(
        self,
        owner: GatewayDirectOwner,
        journal: Any,
        ledger: GatewayDirectExecutionLedger,
        *,
        account_scope: str,
        principals: frozenset[str],
    ) -> None:
        if (
            type(owner) is not GatewayDirectOwner
            or not owner.is_bound_to(journal)
            or type(ledger) is not GatewayDirectExecutionLedger
            or not ledger.is_bound_to(journal)
            or journal._schema_version < 9
            or journal.account_scope != account_scope
            or type(principals) is not frozenset
            or any(not _ref(item) for item in principals)
        ):
            raise ValueError("INVALID_ACCOUNT_RECENT_EXECUTIONS")
        self.owner = owner
        self.journal = journal
        self.ledger = ledger
        self.account_scope = account_scope
        self.principals = principals

    def handle(
        self,
        operation: str,
        context: ServiceCallContext,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        if (
            operation not in ACCOUNT_EXECUTION_HISTORY_OPERATIONS
            or type(context) is not ServiceCallContext
            or not isinstance(arguments, Mapping)
            or context.principal_ref not in self.principals
            or context.identity.role is not WireRole.APPLICATION_CLIENT
            or context.identity.principal_ref != context.principal_ref
            or context.identity.delegate_ref is not None
            or context.scope.account_ref != self.account_scope
            or context.scope.account_refs
            or context.scope.strategy_ref is not None
            or context.scope.run_id is not None
            or context.scope.portfolio_ref is not None
            or context.scope.manual_book_ref is not None
            or context.scope.group_id is not None
            or context.scope.leg_id is not None
        ):
            return _reject("SCOPE_DENIED")
        fields = set(arguments)
        if not ({"limit"} <= fields <= {"limit", "before_sequence"}):
            return _reject("INVALID_REQUEST")
        limit = arguments["limit"]
        before_sequence = arguments.get("before_sequence")
        if type(limit) is not int or not 1 <= limit <= 100:
            return _reject("INVALID_REQUEST")
        if before_sequence is not None and (
            type(before_sequence) is not int or not 1 <= before_sequence < 2**63
        ):
            return _reject("INVALID_REQUEST")
        admission = context.dispatch_admission
        if (
            type(admission) is not ServiceDispatchAdmission
            or not admission.matches_context(context)
            or not admission.matches_operation(operation)
            or not admission.matches(arguments)
        ):
            return _reject("ADMISSION_DENIED")

        def read() -> dict[str, Any]:
            with admission.scope() as recheck:
                events, has_more = self.ledger.read_recent_events(
                    limit=limit, before_sequence=before_sequence
                )
                recheck()
            projected = [_project_execution_event(ev) for ev in events]
            next_before = projected[-1]["sequence"] if (has_more and projected) else None
            response = {
                "status": "OBSERVED",
                "source_provenance": {
                    "schema_version": 1,
                    "account_scope": self.account_scope,
                    "journal_id": self.journal.journal_id,
                    "owner_generation": self.journal.generation,
                },
                "has_more": bool(has_more),
                "next_before_sequence": next_before,
                "executions": projected,
            }
            if callable(context.result_fits) and not context.result_fits(response):
                raise ServiceResultTooLargeError()
            return response

        try:
            return self.owner.submit_service_call(read).result(timeout_seconds=1.0)
        except DirectCommandJournalError as error:
            if error.code in {"INVALID_INPUT"}:
                return _reject("INVALID_REQUEST")
            raise
        except GatewayOwnerServiceCallError:
            return {"status": "UNKNOWN"}
        finally:
            admission.close()


__all__ = [
    "ACCOUNT_EXECUTION_HISTORY_OPERATIONS",
    "GatewayAccountRecentExecutions",
]
