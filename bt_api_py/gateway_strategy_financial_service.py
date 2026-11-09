"""Owner-thread service projection of a trusted strategy financial snapshot.

Registration/operation authorization is provided by ServiceRegistry. The
journal independently verifies its account guard and installed source fence;
client-supplied currency never establishes a snapshot or source authority.
"""
from __future__ import annotations

from collections.abc import Mapping

from bt_api_base.service_protocol import WireRole
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.strategy_financial_ledger import StrategyFinancialSnapshot
from bt_api_py.gateway_direct_owner import GatewayDirectOwner
from bt_api_py.service_registry import ServiceCallContext, ServiceDispatchAdmission


def _ref(value: object) -> bool:
    return (type(value) is str and 0 < len(value) <= 256
            and value == value.strip() and value.isprintable())


def _reject(code: str) -> dict[str, str]:
    return {"status": "REQUEST_REJECTED", "code": code}


def handle_strategy_financial(*, journal, owner, context, arguments, grant):
    """Read only an authenticated strategy's single-currency snapshot.

    The caller owns and closes the validated admission after this helper
    returns. An owner timeout is unavailable, not a cached financial result.
    """
    from bt_api_py.gateway_direct_service import GatewayDirectGrant

    if (type(journal) is not GatewayDirectCommandJournal
            or type(owner) is not GatewayDirectOwner
            or type(context) is not ServiceCallContext
            or type(grant) is not GatewayDirectGrant or not isinstance(arguments, Mapping)):
        return _reject("STRATEGY_FINANCIAL_UNAVAILABLE")
    if set(arguments) != {"currency"} or not _ref(arguments.get("currency")):
        return _reject("INVALID_REQUEST")
    identity, scope = context.identity, context.scope
    admission = context.dispatch_admission
    if (identity.role is not WireRole.STRATEGY_CLIENT
            or identity.principal_ref != context.principal_ref
            or identity.actor_ref != grant.strategy_ref or identity.delegate_ref is not None
            or scope.account_ref != journal.account_scope or scope.strategy_ref != grant.strategy_ref
            or not scope.run_id or scope.account_refs or scope.manual_book_ref is not None
            or scope.portfolio_ref is not None or scope.group_id is not None or scope.leg_id is not None
            or type(admission) is not ServiceDispatchAdmission):
        return _reject("SCOPE_DENIED")
    try:
        if (not admission.matches_context(context)
                or not admission.matches_operation("direct.strategy_financial")
                or not admission.matches(arguments)
                or journal._schema_version < 14 or not owner.is_bound_to(journal)):
            return _reject("STRATEGY_FINANCIAL_UNAVAILABLE")
        currency = arguments["currency"]

        def read():
            snapshot = journal.read_strategy_financial(
                grant.strategy_ref, currency, admission_fence=admission.scope
            )
            if (type(snapshot) is not StrategyFinancialSnapshot
                    or snapshot.account != journal.account_scope
                    or snapshot.journal_id != journal.journal_id
                    or snapshot.strategy != grant.strategy_ref
                    or snapshot.currency != currency
                    or snapshot.owner_generation != journal.generation):
                raise ValueError("STRATEGY_FINANCIAL_UNAVAILABLE")
            return {
                "status": "OBSERVED", "schema_version": 1, "currency": currency,
                "model": snapshot.model, "revision": snapshot.revision,
                "cut_ref": snapshot.cut_ref, "owner_generation": snapshot.owner_generation,
                "snapshot_digest": snapshot.digest,
                "value": format(snapshot.value, "f"), "cash": format(snapshot.cash, "f"),
            }

        response = owner.submit_service_call(read).result(timeout_seconds=1.0)
        if context.result_fits is not None:
            if not callable(context.result_fits) or context.result_fits(response) is not True:
                return _reject("STRATEGY_FINANCIAL_UNAVAILABLE")
        return response
    except Exception:
        return _reject("STRATEGY_FINANCIAL_UNAVAILABLE")
