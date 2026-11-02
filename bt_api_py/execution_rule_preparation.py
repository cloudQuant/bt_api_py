"""SDK workflow for preparing and validating neutral execution rules.

The selected provider remains the source of rules and currentness.
This module grants no write authority.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation


def build_execution_rule_request(payload, instrument, session, position_mode, *, error_type):
    """Project detached order fields into an SDK rules request.

    The caller resolves the configured instrument and its own session fence.
    """
    from bt_api_py.facade_protocol import ExecutionRuleRequest

    def decimal_text(value, field_name, *, positive=False):
        if value is None:
            return None
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            raise error_type(f"{field_name} is not a finite decimal") from None
        if not parsed.is_finite() or (positive and parsed <= 0):
            raise error_type(f"{field_name} is not a valid positive decimal")
        return format(parsed, "f")

    limit_price = payload.get("limit_price")
    if limit_price is None and payload.get("order_type") == "limit":
        limit_price = payload.get("price")
    return ExecutionRuleRequest(
        instrument=instrument,
        session_generation=session.generation,
        side=str(payload.get("side") or ""),
        quantity=decimal_text(payload.get("size"), "order quantity", positive=True),
        order_type=str(payload.get("order_type") or ""),
        time_in_force=(
            str(payload["time_in_force"]).upper()
            if payload.get("time_in_force") not in (None, "")
            else None
        ),
        limit_price=decimal_text(limit_price, "limit price", positive=True),
        position_mode=position_mode,
        position_side=payload.get("position_side"),
        offset=payload.get("offset"),
        net_position_size=None,
        long_position_size=None,
        short_position_size=None,
        long_close_today_size=None,
        long_close_yesterday_size=None,
        short_close_today_size=None,
        short_close_yesterday_size=None,
        hedge_class=payload.get("hedge_class"),
    )


def prepare_execution_rules(api, route, instrument, session, hedge_class, *,
                            sanitize_exception, missing_dependency_error_type):
    """Warm the exact evaluator-owned rules cache before reading position evidence."""
    try:
        from bt_api_py.facade_protocol import (
            ExecutionRulesFacade,
            ExecutionRulesPort,
            ExecutionRulesPreparationPort,
            ExecutionRulesPreparationUnavailable,
            InstrumentExecutionFacts,
            InstrumentExecutionRulesSnapshot,
        )
    except ImportError as exc:
        raise missing_dependency_error_type(
            "SDK execution-rules preparation contracts are required"
        ) from exc

    if not isinstance(api, ExecutionRulesFacade):
        return None, None, "execution_rules_capability_unavailable"
    if type(hedge_class) is not str or hedge_class not in {
        "speculation",
        "arbitrage",
        "hedge",
    }:
        return None, None, "execution_rules_hedge_class_unavailable"
    try:
        rules_port = api.execution_rules(route)
    except Exception as exc:
        sanitize_exception(exc)
        return None, None, "execution_rules_capability_unavailable"
    if not isinstance(rules_port, ExecutionRulesPort):
        return None, None, "execution_rules_capability_unavailable"
    if not isinstance(rules_port, ExecutionRulesPreparationPort):
        return rules_port, None, "execution_rules_preparation_unavailable"
    try:
        rules_snapshot = rules_port.prepare_rules(
            instrument,
            hedge_class=hedge_class,
        )
    except ExecutionRulesPreparationUnavailable:
        return rules_port, None, "execution_rules_preparation_unavailable"
    except Exception as exc:
        sanitize_exception(exc)
        return rules_port, None, "execution_rules_preparation_unavailable"
    if type(rules_snapshot) is not InstrumentExecutionRulesSnapshot:
        return rules_port, None, "execution_rules_preparation_invalid"
    try:
        InstrumentExecutionRulesSnapshot.__post_init__(rules_snapshot)
        facts = rules_snapshot.facts
        if type(facts) is not InstrumentExecutionFacts:
            return rules_port, None, "execution_rules_preparation_invalid"
        InstrumentExecutionFacts.__post_init__(facts)
    except Exception:
        return rules_port, None, "execution_rules_preparation_invalid"
    if (
        facts.instrument is not instrument
        or facts.session_generation != session.generation
        or facts.hedge_class != hedge_class
        or facts.status != "available"
    ):
        return rules_port, None, "execution_rules_preparation_scope_mismatch"
    if not validate_prepared_execution_rules(rules_port, rules_snapshot):
        return rules_port, None, "execution_rules_preparation_not_current"
    return rules_port, rules_snapshot, None


def validate_prepared_execution_rules(rules_port, rules_snapshot):
    try:
        from bt_api_py.facade_protocol import (
            ExecutionRulesPreparationPort,
            InstrumentExecutionRulesSnapshot,
        )

        if (
            not isinstance(rules_port, ExecutionRulesPreparationPort)
            or type(rules_snapshot) is not InstrumentExecutionRulesSnapshot
        ):
            return False
        valid = rules_port.validate_prepared_rules(
            rules_snapshot,
            now_utc=datetime.now(timezone.utc),
        )
        return type(valid) is bool and valid is True
    except Exception:
        return False


def evaluate_prepared_execution_rules(api, rules_port, rules_snapshot, request):
    """Evaluate the exact prepared SDK rule request or return unavailable."""
    from bt_api_py.facade_protocol import (
        ExecutionRuleDecision,
        ExecutionRulesFacade,
        ExecutionRulesPort,
        ExecutionRulesPreparationPort,
    )

    if (
        not isinstance(api, ExecutionRulesFacade)
        or not isinstance(rules_port, ExecutionRulesPort)
        or not isinstance(rules_port, ExecutionRulesPreparationPort)
    ):
        return unavailable_execution_rule_decision(request, "execution_rules_capability_unavailable")
    if rules_snapshot is None or not validate_prepared_execution_rules(rules_port, rules_snapshot):
        return unavailable_execution_rule_decision(
            request, "execution_rules_preparation_expired_before_evaluation"
        )
    try:
        decision = rules_port.evaluate_order_rules(request)
    except Exception:
        decision = unavailable_execution_rule_decision(request, "execution_rules_provider_unavailable")
    if type(decision) is not ExecutionRuleDecision or decision.request is not request:
        return unavailable_execution_rule_decision(request, "execution_rules_projection_invalid")
    try:
        ExecutionRuleDecision.__post_init__(decision)
    except Exception:
        return unavailable_execution_rule_decision(request, "execution_rules_projection_invalid")
    return decision


def evaluate_rules_with_position_evidence(
    api, route, session, request, quantity_unit, rules_port, rules_snapshot,
    preparation_error, *, session_generation_matches, sanitize_exception,
    missing_dependency_error_type,
):
    """Bind neutral position evidence to prepared rules before evaluation.

    The caller owns the session-generation fence; the selected SDK ports
    remain responsible for the evidence and rules currentness checks.
    """
    if preparation_error is not None:
        position_port = None
        position_evidence = None
        position_error = preparation_error
    elif not session_generation_matches(api, route, session.generation):
        position_port = None
        position_evidence = None
        position_error = "account_session_generation_changed"
    elif (
        type(quantity_unit) is not str
        or not quantity_unit
        or quantity_unit != quantity_unit.strip()
    ):
        position_port = None
        position_evidence = None
        position_error = "position_evidence_quantity_unit_unavailable"
    else:
        try:
            from bt_api_py.order_position_evidence_read import read_order_position_evidence
        except ImportError as exc:
            raise missing_dependency_error_type(
                "SDK order-position evidence contracts are required"
            ) from exc

        position_port, position_evidence, position_error = read_order_position_evidence(
            api, route, request.instrument, session, quantity_unit,
            sanitize_exception=sanitize_exception,
            missing_dependency_error_type=missing_dependency_error_type,
        )
    if position_evidence is not None:
        if not session_generation_matches(api, route, session.generation):
            position_evidence = None
            position_error = "account_session_generation_changed"
        elif not validate_prepared_execution_rules(rules_port, rules_snapshot):
            position_evidence = None
            position_error = "execution_rules_preparation_expired_after_position_read"
    if position_evidence is not None:
        quantities = position_evidence.quantities
        request = replace(
            request,
            net_position_size=quantities.net_position_size,
            long_position_size=quantities.long_position_size,
            short_position_size=quantities.short_position_size,
            long_close_today_size=quantities.long_close_today_size,
            long_close_yesterday_size=quantities.long_close_yesterday_size,
            short_close_today_size=quantities.short_close_today_size,
            short_close_yesterday_size=quantities.short_close_yesterday_size,
            position_evidence_ref=position_evidence.evidence_ref,
            position_evidence_generation=position_evidence.session_generation,
            position_observed_at_utc=position_evidence.observed_at_utc,
            position_expires_at_utc=position_evidence.expires_at_utc,
        )
    if position_error is not None:
        decision = unavailable_execution_rule_decision(request, position_error)
    else:
        decision = evaluate_prepared_execution_rules(api, rules_port, rules_snapshot, request)
    return request, decision, position_port, position_evidence


def verify_execution_rule_evidence(
    api, route, decision, session_generation, *, rules_port, rules_snapshot,
    position_evidence, position_port, position_quantity_unit,
):
    """Validate SDK rule and position evidence for an already bound decision.

    The caller remains responsible for its own order and session identity fence.
    """
    from bt_api_py.facade_protocol import (
        ExecutionRulesFacade,
        ExecutionRulesPort,
        ExecutionRulesPreparationPort,
        InstrumentExecutionFacts,
        InstrumentExecutionRulesSnapshot,
        OrderPositionEvidence,
        OrderPositionEvidencePort,
        OrderPositionQuantities,
    )

    if rules_snapshot is None:
        if decision.status == "accepted" or decision.facts is not None:
            return False
    else:
        if (
            not isinstance(api, ExecutionRulesFacade)
            or not isinstance(rules_port, ExecutionRulesPort)
            or not isinstance(rules_port, ExecutionRulesPreparationPort)
            or api.execution_rules(route) is not rules_port
            or type(rules_snapshot) is not InstrumentExecutionRulesSnapshot
        ):
            return False
        InstrumentExecutionRulesSnapshot.__post_init__(rules_snapshot)
        prepared_facts = rules_snapshot.facts
        if type(prepared_facts) is not InstrumentExecutionFacts:
            return False
        InstrumentExecutionFacts.__post_init__(prepared_facts)
        if (
            prepared_facts.instrument is not decision.request.instrument
            or prepared_facts.session_generation != session_generation
            or prepared_facts.hedge_class != decision.request.hedge_class
            or prepared_facts.status != "available"
            or not validate_prepared_execution_rules(
                rules_port,
                rules_snapshot,
            )
        ):
            return False
        if decision.facts is not None:
            if type(decision.facts) is not InstrumentExecutionFacts:
                return False
            if decision.facts is not prepared_facts:
                return False
    request = decision.request
    position_request_fields = (
        request.net_position_size,
        request.long_position_size,
        request.short_position_size,
        request.long_close_today_size,
        request.long_close_yesterday_size,
        request.short_close_today_size,
        request.short_close_yesterday_size,
        request.position_evidence_ref,
        request.position_evidence_generation,
        request.position_observed_at_utc,
        request.position_expires_at_utc,
    )
    if position_evidence is None:
        if any(value is not None for value in position_request_fields):
            return False
    else:
        if (
            type(position_evidence) is not OrderPositionEvidence
            or not isinstance(position_port, OrderPositionEvidencePort)
        ):
            return False
        OrderPositionEvidence.__post_init__(position_evidence)
        quantities = position_evidence.quantities
        if type(quantities) is not OrderPositionQuantities:
            return False
        OrderPositionQuantities.__post_init__(quantities)
        if (
            quantities.instrument is not request.instrument
            or position_evidence.session_generation != session_generation
            or position_quantity_unit != quantities.qty_unit
            or request.net_position_size != quantities.net_position_size
            or request.long_position_size != quantities.long_position_size
            or request.short_position_size != quantities.short_position_size
            or request.long_close_today_size != quantities.long_close_today_size
            or request.long_close_yesterday_size
            != quantities.long_close_yesterday_size
            or request.short_close_today_size != quantities.short_close_today_size
            or request.short_close_yesterday_size
            != quantities.short_close_yesterday_size
            or request.position_evidence_ref != position_evidence.evidence_ref
            or request.position_evidence_generation
            != position_evidence.session_generation
            or request.position_observed_at_utc != position_evidence.observed_at_utc
            or request.position_expires_at_utc != position_evidence.expires_at_utc
        ):
            return False
        position_verified = position_port.validate(
            position_evidence, now=datetime.now(timezone.utc)
        )
        if type(position_verified) is not bool or position_verified is not True:
            return False
    facts = decision.facts
    if facts is not None:
        expiry = facts.expires_at_utc
        if expiry is None or expiry <= datetime.now(timezone.utc):
            return False
    elif decision.status == "accepted":
        return False
    return True


def unavailable_execution_rule_decision(request, reason_code):
    from bt_api_py.facade_protocol import ExecutionRuleDecision

    return ExecutionRuleDecision(
        request=request,
        status="unavailable",
        reason_code=reason_code,
        facts=None,
        normalized_position_side=None,
        normalized_offset=None,
        normalized_time_in_force=None,
        commission_estimate=None,
        commission_currency=None,
        commission_charge_unit=None,
        position_evidence_validated=False,
    )


__all__ = (
    "build_execution_rule_request",
    "prepare_execution_rules",
    "validate_prepared_execution_rules",
    "evaluate_prepared_execution_rules",
    "evaluate_rules_with_position_evidence",
    "verify_execution_rule_evidence",
    "unavailable_execution_rule_decision",
)
