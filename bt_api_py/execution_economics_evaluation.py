"""SDK selection and validation for neutral account-economics decisions.

The selected provider owns economic facts and evidence. This workflow does not
grant authorization or convert unavailable evidence into a usable decision.
"""

from __future__ import annotations

from datetime import datetime, timezone


def build_execution_economics_request(rules_decision, quantity_unit):
    """Bind an economics request to the already selected rules decision."""
    from bt_api_py.facade_protocol import ExecutionEconomicsRequest

    return ExecutionEconomicsRequest(
        rule_request=rules_decision.request,
        rule_decision=rules_decision,
        instrument=rules_decision.request.instrument,
        quantity_unit=quantity_unit,
    )


def execution_economics_decision_is_current(decision):
    """Check an accepted economics decision's time bound without authorizing it."""
    if decision.status == "accepted":
        expiry = decision.expires_at_utc
        if expiry is None or expiry <= datetime.now(timezone.utc):
            return False
    return True


def unavailable_execution_economics_decision(request, reason_code):
    from bt_api_py.facade_protocol import ExecutionEconomicsDecision

    return ExecutionEconomicsDecision(
        request=request,
        status="unavailable",
        reason_code=reason_code,
        requirement_kind=None,
        instrument=request.instrument,
        evaluated_quantity=None,
        quantity_unit=None,
        required_amount=None,
        available_amount=None,
        currency=None,
        amount_unit=None,
        fee_included=False,
        source_ref=None,
        account_balance_source_ref=None,
        account_scope_ref=None,
        session_generation=request.rule_request.session_generation,
        observed_at_utc=None,
        expires_at_utc=None,
        lease_ref=None,
    )


def evaluate_execution_economics(api, route, request):
    """Return the selected port and its validated decision for one request."""
    from bt_api_py.facade_protocol import (
        ExecutionEconomicsDecision,
        ExecutionEconomicsFacade,
        ExecutionEconomicsPort,
    )

    if not isinstance(api, ExecutionEconomicsFacade):
        return None, unavailable_execution_economics_decision(
            request, "execution_economics_capability_unavailable"
        )
    try:
        port = api.execution_economics(route)
    except Exception:
        port = None
    if not isinstance(port, ExecutionEconomicsPort):
        return None, unavailable_execution_economics_decision(
            request, "execution_economics_capability_unavailable"
        )
    try:
        decision = port.evaluate_order_economics(request)
    except Exception:
        decision = unavailable_execution_economics_decision(
            request, "execution_economics_provider_unavailable"
        )
    if type(decision) is not ExecutionEconomicsDecision or decision.request != request:
        return None, unavailable_execution_economics_decision(
            request, "execution_economics_projection_invalid"
        )
    try:
        ExecutionEconomicsDecision.__post_init__(decision)
    except Exception:
        return None, unavailable_execution_economics_decision(
            request, "execution_economics_projection_invalid"
        )
    return port, decision


__all__ = (
    "build_execution_economics_request",
    "execution_economics_decision_is_current",
    "evaluate_execution_economics",
    "unavailable_execution_economics_decision",
)
