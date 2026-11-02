"""SDK-neutral account-risk snapshot projection and validation.

The caller provides public projection callbacks; this module neither owns a
framework lifecycle nor establishes the source of account-risk facts.
"""

from __future__ import annotations

from copy import deepcopy
import math
import time
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple



def normalized_positions_are_flat(
    positions, *, invalid_shape, invalid_quantity, known_before_quantity=False,
):
    """Project normalized quantities without claiming source completeness.

    The caller supplies its existing errors and remains responsible for the
    authenticated account, query lifecycle, and completeness assertions.
    """
    quantities = []
    for position in positions:
        if not isinstance(position, Mapping):
            raise invalid_shape()
        if known_before_quantity:
            invalid = position.get("quantity_known") is not True or "quantity" not in position
        else:
            invalid = "quantity" not in position or position.get("quantity_known") is not True
        if invalid:
            raise invalid_shape()
        quantity = Decimal(str(position["quantity"]))
        if not quantity.is_finite():
            raise invalid_quantity()
        quantities.append(quantity)
    return all(quantity == 0 for quantity in quantities)

def position_audit_close_error(
    *, offset, order_size, position_key, dual_side, position_side, is_buy, leg_size, net_size
):
    """Classify an order against caller-owned local positions after an audit block.

    Callbacks read the caller's current order and position state. This decision
    does not establish account provenance or authorize an exchange write.
    """
    offset = str(offset() or "").strip().lower()
    if offset not in {"close", "close_today", "close_yesterday"}:
        return (
            "position_audit_blocked",
            "Opening orders are blocked until a position audit fully matches the local ledger",
        )

    requested = abs(float(order_size() or 0.0))
    if requested <= 0.0:
        return (
            "position_audit_close_not_reducing",
            "Audit-blocked close size must be positive",
        )
    key = position_key()
    if dual_side():
        side = position_side()
        if side not in {"long", "short"}:
            return (
                "position_audit_close_not_reducing",
                "Audit-blocked close orders require an explicit position side",
            )
        if (side == "long" and is_buy()) or (side == "short" and not is_buy()):
            return (
                "position_audit_close_not_reducing",
                "Audit-blocked close order direction would increase the selected leg",
            )
        available = abs(float(leg_size(side, key) or 0.0))
    else:
        current_size = float(net_size(key) or 0.0)
        available = (
            abs(current_size)
            if (is_buy() and current_size < 0.0)
            or (not is_buy() and current_size > 0.0)
            else 0.0
        )

    if requested > available + 1e-12:
        return (
            "position_audit_close_not_reducing",
            "Audit-blocked close size exceeds the locally confirmed position",
        )
    return None


def incomplete_account_risk_projection(snapshot, error_code):
    """Mark an existing neutral risk view incomplete without changing its facts."""
    result = deepcopy(snapshot)
    errors = result.get("evidence_errors")
    if isinstance(errors, (Mapping, list, tuple, set, frozenset)):
        existing = [item for item in errors if isinstance(item, str)]
    elif isinstance(errors, str) and errors:
        existing = [errors]
    else:
        existing = []
    if error_code not in existing:
        existing.append(error_code)
    result["evidence_complete"] = False
    result["evidence_errors"] = existing
    result["error_code"] = error_code
    return result


def _thaw_account_risk_value(value: Any) -> Any:
    """Copy immutable SDK facts into the Store's ordinary JSON projection."""

    if isinstance(value, Mapping):
        return {key: _thaw_account_risk_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_thaw_account_risk_value(item) for item in value]
    return value


def is_expected_prebaseline_account_risk(snapshot: Any) -> bool:
    """Recognize only the deterministic clean missing-baseline latch."""

    if not isinstance(snapshot, Mapping):
        return False
    blocked_reasons = snapshot.get("blocked_reasons")
    evidence_errors = snapshot.get("evidence_errors")
    expected_blocked_reasons = {"account_evidence_incomplete", "baseline_missing"}
    expected_evidence_errors = {
        "account_risk_currency_mismatch",
        "account_risk_not_durable",
        "account_risk_trading_blocked",
        "invalid_baseline_equity",
        "invalid_baseline_equity_by_venue",
        "sdk_blocked_reasons_present",
        "sdk_evidence_incomplete",
    }
    prebaseline_extra_evidence_errors = {
        "invalid_current_equity",
        "current_equity_aggregate_mismatch",
        "sdk_prebaseline_aggregate_unavailable",
    }
    return bool(
        snapshot.get("baseline_equity") is None
        and snapshot.get("baseline_equity_by_venue") is None
        and snapshot.get("loss_limit_breached") is False
        and snapshot.get("loss_breached_at") is None
        and snapshot.get("evidence_complete") is False
        and snapshot.get("durable") is False
        and snapshot.get("trading_blocked") is True
        and snapshot.get("error_code") == "account_risk_evidence_incomplete"
        and type(blocked_reasons) is list
        and len(blocked_reasons) == len(expected_blocked_reasons)
        and set(blocked_reasons) == expected_blocked_reasons
        and type(evidence_errors) is list
        and expected_evidence_errors <= set(evidence_errors)
        and set(evidence_errors) <= (expected_evidence_errors | prebaseline_extra_evidence_errors)
    )


def project_account_risk_snapshot(
    risk_snapshot: Any,
    identity_binding: Any,
    *,
    configured_routes: Sequence[str],
    configured_loss_limit: Any = None,
    owner_pid: int,
    read_started_ns: int,
    read_finished_ns: int,
    unavailable_account_risk_snapshot,
    public_venue_key,
) -> Dict[str, Any]:
    """Validate and project exact SDK risk facts bound to one SDK identity vector."""

    routes = sorted(str(route).strip() for route in configured_routes if str(route).strip())
    try:
        from bt_api_py.facade_protocol import (
            AccountRiskSnapshot,
            ExecutionIdentityVectorBinding,
        )
    except ImportError:
        return unavailable_account_risk_snapshot(
            routes,
            configured_loss_limit=configured_loss_limit,
            error_code="account_risk_snapshot_unavailable",
        )
    if (
        type(risk_snapshot) is not AccountRiskSnapshot
        or type(identity_binding) is not ExecutionIdentityVectorBinding
        or risk_snapshot.identity_binding is not identity_binding
    ):
        return unavailable_account_risk_snapshot(
            routes,
            configured_loss_limit=configured_loss_limit,
            error_code="account_risk_snapshot_identity_mismatch",
        )
    facts = _thaw_account_risk_value(risk_snapshot.facts)
    if not isinstance(facts, dict):
        return unavailable_account_risk_snapshot(
            routes,
            configured_loss_limit=configured_loss_limit,
            error_code="account_risk_snapshot_projection_invalid",
        )
    snapshot = facts
    snapshot["realized_net"] = None
    errors: List[str] = []
    required = {
        "schema_version",
        "baseline_equity",
        "baseline_equity_by_venue",
        "blocked_reasons",
        "clock_domain_id",
        "currency",
        "current_equity",
        "current_equity_by_venue",
        "configured_venues",
        "evidence_errors",
        "generation",
        "fencing_epoch",
        "as_of_monotonic_ns",
        "owner_pid",
        "durable",
        "trading_blocked",
        "evidence_complete",
        "loss_limit_bps",
        "loss_limit_breached",
        "loss_breached_at",
        "loss_amount",
        "loss_limit_amount",
        "loss_bps_observed",
        "peak_loss_bps",
    }
    errors.extend("missing_{}".format(key) for key in sorted(required.difference(snapshot)))
    raw_routes = snapshot.get("configured_venues")
    if isinstance(raw_routes, (list, tuple)) and all(
        isinstance(venue, str) and venue.strip() for venue in raw_routes
    ):
        actual_routes = sorted(venue.strip() for venue in raw_routes)
        if len(actual_routes) != len(set(actual_routes)):
            errors.append("duplicate_configured_venues")
    else:
        actual_routes = []
        errors.append("invalid_configured_venues")
    if actual_routes != routes:
        errors.append("configured_venues_mismatch")
    snapshot["configured_venue_routes"] = actual_routes
    snapshot["configured_venues"] = sorted(
        {public_venue_key(venue) for venue in actual_routes}
    )
    snapshot["identity_binding_sha256"] = identity_binding.identity_binding_sha256
    if tuple(identity_binding.exchange_names) != tuple(routes):
        errors.append("account_risk_identity_binding_routes_mismatch")

    if type(snapshot.get("schema_version")) is not int or snapshot.get("schema_version") != 1:
        errors.append("invalid_schema_version")

    def equity_map(key: str):
        raw = snapshot.get(key)
        if not isinstance(raw, Mapping):
            errors.append("invalid_{}".format(key))
            return None, set()
        if set(raw) != set(routes):
            errors.append("{}_venues_mismatch".format(key))
        total = Decimal(0)
        currencies = set()
        valid = True
        for venue in routes:
            row = raw.get(venue)
            if not isinstance(row, Mapping):
                errors.append("invalid_{}_{}".format(key, venue))
                valid = False
                continue
            currency = str(row.get("currency") or "").strip().upper()
            if not currency:
                errors.append("invalid_{}_{}_currency".format(key, venue))
                valid = False
            else:
                currencies.add(currency)
            try:
                value = Decimal(str(row.get("equity")))
                if not value.is_finite():
                    raise InvalidOperation
                total += value
            except (InvalidOperation, TypeError, ValueError):
                errors.append("invalid_{}_{}_equity".format(key, venue))
                valid = False
        return (total if valid else None), currencies

    baseline_total, baseline_currencies = equity_map("baseline_equity_by_venue")
    current_total, current_currencies = equity_map("current_equity_by_venue")
    aggregate_values = {}
    for key in ("baseline_equity", "current_equity"):
        try:
            value = Decimal(str(snapshot.get(key)))
            if not value.is_finite():
                raise InvalidOperation
            aggregate_values[key] = value
        except (InvalidOperation, TypeError, ValueError):
            errors.append("invalid_{}".format(key))
    if baseline_total is not None and aggregate_values.get("baseline_equity") != baseline_total:
        errors.append("baseline_equity_aggregate_mismatch")
    if current_total is not None and aggregate_values.get("current_equity") != current_total:
        errors.append("current_equity_aggregate_mismatch")

    raw_loss_limit = snapshot.get("loss_limit_bps")
    loss_limit = None
    if configured_loss_limit is None:
        if raw_loss_limit is not None:
            errors.append("unexpected_account_maximum_loss_limit")
    else:
        try:
            expected_limit = Decimal(str(configured_loss_limit))
            loss_limit = Decimal(str(raw_loss_limit))
            if (
                not expected_limit.is_finite()
                or expected_limit <= 0
                or not loss_limit.is_finite()
                or loss_limit <= 0
                or loss_limit != expected_limit
            ):
                raise InvalidOperation
        except (InvalidOperation, TypeError, ValueError):
            errors.append("account_maximum_loss_limit_mismatch")

    loss_limit_breached = snapshot.get("loss_limit_breached")
    if type(loss_limit_breached) is not bool:
        errors.append("invalid_loss_limit_breached")
    loss_breached_at = snapshot.get("loss_breached_at")
    if loss_limit_breached is True:
        if (
            isinstance(loss_breached_at, bool)
            or not isinstance(loss_breached_at, (int, float))
            or not math.isfinite(loss_breached_at)
            or loss_breached_at <= 0
        ):
            errors.append("invalid_loss_breached_at")
    elif loss_breached_at is not None:
        errors.append("unexpected_loss_breached_at")

    loss_values: Dict[str, Any] = {}
    for key in ("loss_amount", "loss_limit_amount", "loss_bps_observed", "peak_loss_bps"):
        raw_value = snapshot.get(key)
        if raw_value is None:
            loss_values[key] = None
            continue
        try:
            value = Decimal(str(raw_value))
            if not value.is_finite() or value < 0 or not isinstance(raw_value, str):
                raise InvalidOperation
        except (InvalidOperation, TypeError, ValueError):
            errors.append("invalid_{}".format(key))
            loss_values[key] = None
        else:
            loss_values[key] = value

    if loss_limit is None:
        if loss_limit_breached is not False or any(
            value is not None for value in loss_values.values()
        ):
            errors.append("unexpected_account_loss_state")
    elif baseline_total is not None and current_total is not None and baseline_total > 0:
        expected_loss = max(baseline_total - current_total, Decimal("0"))
        expected_limit_amount = baseline_total * loss_limit / Decimal("10000")
        expected_loss_bps = expected_loss * Decimal("10000") / baseline_total
        if loss_values["loss_amount"] != expected_loss:
            errors.append("account_loss_amount_mismatch")
        if loss_values["loss_limit_amount"] != expected_limit_amount:
            errors.append("account_loss_limit_amount_mismatch")
        # Match the SDK's unbounded-ratio projection beyond economically
        # meaningful precision without depending on Decimal context defaults.
        bps_quantum = Decimal("0.000000000001")

        def bps_quantized(value: Any):
            if not isinstance(value, Decimal):
                return None
            try:
                return value.quantize(bps_quantum)
            except InvalidOperation:
                return None

        observed_bps = bps_quantized(loss_values["loss_bps_observed"])
        if observed_bps is None or observed_bps != bps_quantized(expected_loss_bps):
            errors.append("account_loss_bps_mismatch")
        peak = bps_quantized(loss_values["peak_loss_bps"])
        if peak is None or peak < bps_quantized(expected_loss_bps):
            errors.append("account_peak_loss_bps_mismatch")
        if loss_limit_breached is False and expected_loss_bps >= loss_limit:
            errors.append("account_loss_latch_missing")

    aggregate_currency = snapshot.get("currency")
    if not isinstance(aggregate_currency, str) or not aggregate_currency.strip():
        errors.append("invalid_currency")
    else:
        aggregate_currency = aggregate_currency.strip().upper()
        if baseline_currencies != {aggregate_currency} or current_currencies != {
            aggregate_currency
        }:
            errors.append("account_risk_currency_mismatch")
        snapshot["currency"] = aggregate_currency
    if snapshot.get("realized_net") is not None:
        try:
            if not Decimal(str(snapshot["realized_net"])).is_finite():
                raise InvalidOperation
        except (InvalidOperation, TypeError, ValueError):
            errors.append("invalid_realized_net")
    for key in ("generation", "fencing_epoch", "as_of_monotonic_ns", "owner_pid"):
        fence_value = snapshot.get(key)
        if type(fence_value) is not int or fence_value <= 0:
            errors.append("invalid_{}".format(key))
    if snapshot.get("generation") != snapshot.get("fencing_epoch"):
        errors.append("account_risk_generation_fence_mismatch")
    snapshot_owner_pid = snapshot.get("owner_pid")
    clock_domain_id = snapshot.get("clock_domain_id")
    if (
        snapshot_owner_pid != owner_pid
        or clock_domain_id != "process:{}:monotonic".format(snapshot_owner_pid)
    ):
        errors.append("account_risk_clock_domain_mismatch")
    as_of_monotonic_ns = snapshot.get("as_of_monotonic_ns")
    if type(as_of_monotonic_ns) is int:
        if as_of_monotonic_ns < read_started_ns:
            errors.append("account_risk_timestamp_precedes_call")
        elif as_of_monotonic_ns > read_finished_ns:
            errors.append("account_risk_timestamp_in_future")
    for key in ("durable", "trading_blocked", "evidence_complete"):
        if not isinstance(snapshot.get(key), bool):
            errors.append("invalid_{}".format(key))
    sdk_evidence_errors = snapshot.get("evidence_errors")
    if not isinstance(sdk_evidence_errors, Mapping):
        errors.append("invalid_sdk_evidence_errors")
    elif sdk_evidence_errors:
        if dict(sdk_evidence_errors) == {
            "account_maximum_loss": "aggregate_equity_not_comparable"
        }:
            errors.append("sdk_prebaseline_aggregate_unavailable")
        else:
            errors.append("sdk_evidence_errors_present")
    blocked_reasons = snapshot.get("blocked_reasons")
    if not isinstance(blocked_reasons, (list, tuple)):
        errors.append("invalid_blocked_reasons")
    elif blocked_reasons:
        errors.append("sdk_blocked_reasons_present")
    if snapshot.get("durable") is not True:
        errors.append("account_risk_not_durable")
    if snapshot.get("trading_blocked") is not False:
        errors.append("account_risk_trading_blocked")
    if snapshot.get("evidence_complete") is not True:
        errors.append("sdk_evidence_incomplete")

    risk_fence = snapshot.get("fencing_epoch")
    if (
        type(identity_binding.fencing_epoch) is not int
        or identity_binding.fencing_epoch <= 0
        or risk_fence != identity_binding.fencing_epoch
        or snapshot.get("generation") != identity_binding.fencing_epoch
    ):
        errors.append("account_risk_fencing_epoch_mismatch")

    if errors or snapshot.get("evidence_complete") is not True:
        snapshot.update(
            durable=False,
            trading_blocked=True,
            evidence_complete=False,
            evidence_errors=sorted(set(errors or ("sdk_evidence_incomplete",))),
            error_code="account_risk_evidence_incomplete",
        )
    return snapshot


def read_account_risk_snapshot(
    api: Any,
    *,
    generation: int,
    configured_routes: Sequence[str],
    configured_loss_limit: Any,
    initialize_baseline: bool,
    identity_expectations: Tuple[Any, ...],
    capture_identity_binding: Callable[[Any, Tuple[Any, ...]], Any],
    validate_identity_binding: Callable[[Any, Any, Tuple[Any, ...]], Any],
    get_snapshot_reader: Callable[..., Any],
    validate_account_risk_snapshot: Callable[[Any, Any, Any], Any],
    project_typed_snapshot: Callable[..., Mapping[str, Any]],
    exception_code: Callable[[Exception, str], str],
    sanitize_exception: Callable[[BaseException], Any],
    observe_error: Callable[[BaseException], Any],
    owner_pid: int,
    monotonic_ns: Callable[[], int] = time.monotonic_ns,
    cache_snapshot: Callable[[Mapping[str, Any], int], Dict[str, Any]],
    unavailable_account_risk_snapshot: Callable[..., Dict[str, Any]],
) -> Dict[str, Any]:
    """Read and cache facts bound to an SDK-issued identity vector.

    The Store supplies exact public route expectations and delegates vector
    capture/currentness to the SDK's aggregate identity-evidence owner. This
    owner retains only its existing refresh and cache lifecycle; raw
    per-route snapshots, assurances, or ledger mappings never cross this
    boundary. Scope-reference continuity is validated within the active
    SDK process; this method does not establish continuity across hot reload
    or process restart.
    """

    callbacks = (
        capture_identity_binding,
        validate_identity_binding,
        get_snapshot_reader,
        validate_account_risk_snapshot,
        project_typed_snapshot,
        exception_code,
        sanitize_exception,
        observe_error,
        monotonic_ns,
    )
    if any(not callable(callback) for callback in callbacks):
        raise TypeError("account-risk read callbacks must be callable")

    routes = sorted(str(route).strip() for route in configured_routes if str(route).strip())

    def unavailable(error_code: str, errors: Optional[Iterable[str]] = None):
        snapshot = unavailable_account_risk_snapshot(
            routes,
            configured_loss_limit=configured_loss_limit,
            error_code=error_code,
            errors=errors,
        )
        return cache_snapshot(snapshot, generation)

    if not routes or len(routes) != len(set(routes)):
        return unavailable("account_risk_identity_unproven")

    try:
        from bt_api_py.facade_protocol import (
            ExecutionIdentityRouteExpectation,
            ExecutionIdentityScopeRef,
            ExecutionIdentityVectorBinding,
            ExecutionIdentityVectorBindingRef,
        )
    except ImportError:
        return unavailable("account_risk_identity_binding_unavailable")

    if (
        type(identity_expectations) is not tuple
        or not identity_expectations
        or any(
            type(item) is not ExecutionIdentityRouteExpectation
            for item in identity_expectations
        )
    ):
        return unavailable("account_risk_identity_expectations_invalid")
    try:
        for expectation in identity_expectations:
            ExecutionIdentityRouteExpectation.__post_init__(expectation)
    except Exception:
        return unavailable("account_risk_identity_expectations_invalid")
    expected_routes = tuple(item.exchange_name for item in identity_expectations)
    if (
        expected_routes != tuple(routes)
        or tuple(sorted(expected_routes)) != expected_routes
        or len(set(expected_routes)) != len(expected_routes)
    ):
        return unavailable("account_risk_identity_expectations_invalid")

    def observe_failure(exc: Exception, fallback: str):
        observe_error(exc)
        sanitize_exception(exc)
        return unavailable(exception_code(exc, fallback))

    def binding_signature(binding: Any):
        if type(binding) is not ExecutionIdentityVectorBinding:
            return None
        try:
            ExecutionIdentityVectorBinding.__post_init__(binding)
        except Exception:
            return None
        digest = binding.identity_binding_sha256
        exchange_names = binding.exchange_names
        scope_refs = binding.identity_scope_refs
        session_generations = binding.session_generations
        fencing_epoch = binding.fencing_epoch
        if (
            type(digest) is not str
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or type(exchange_names) is not tuple
            or exchange_names != tuple(routes)
            or type(scope_refs) is not tuple
            or len(scope_refs) != len(routes)
            or any(type(ref) is not ExecutionIdentityScopeRef for ref in scope_refs)
            or len({id(ref) for ref in scope_refs}) != len(scope_refs)
            or type(session_generations) is not tuple
            or len(session_generations) != len(routes)
            or any(type(value) is not str or not value for value in session_generations)
            or type(fencing_epoch) is not int
            or fencing_epoch <= 0
            or type(binding.binding_ref) is not ExecutionIdentityVectorBindingRef
        ):
            return None
        return digest, exchange_names, scope_refs, session_generations, fencing_epoch

    def same_binding_signature(left: Any, right: Any) -> bool:
        left_signature = binding_signature(left)
        right_signature = binding_signature(right)
        if left_signature is None or right_signature is None:
            return False
        return (
            left_signature[0] == right_signature[0]
            and left_signature[1] == right_signature[1]
            and len(left_signature[2]) == len(right_signature[2])
            and all(
                left_ref is right_ref
                for left_ref, right_ref in zip(left_signature[2], right_signature[2])
            )
            and left_signature[3] == right_signature[3]
            and left_signature[4] == right_signature[4]
        )

    def capture_binding():
        try:
            binding = capture_identity_binding(api, identity_expectations)
        except Exception as exc:
            return None, exc
        if binding_signature(binding) is None:
            return None, None
        return binding, None

    def binding_is_current(binding: Any) -> bool:
        if binding_signature(binding) is None:
            return False
        try:
            valid = validate_identity_binding(api, binding, identity_expectations)
        except Exception as exc:
            observe_error(exc)
            sanitize_exception(exc)
            return False
        return type(valid) is bool and valid is True

    pre_binding, capture_error = capture_binding()
    if capture_error is not None:
        return observe_failure(capture_error, "account_risk_identity_unproven")
    if pre_binding is None:
        return unavailable("account_risk_identity_binding_invalid")
    pre_signature = binding_signature(pre_binding)
    pre_digest = pre_signature[0]
    if not binding_is_current(pre_binding):
        return unavailable("account_risk_identity_binding_not_current")

    read_started_ns = monotonic_ns()
    try:
        risk_snapshot = get_snapshot_reader(
            api,
            pre_binding,
            initialize_baseline=initialize_baseline,
        )
        read_finished_ns = monotonic_ns()
    except Exception as exc:
        return observe_failure(exc, "account_risk_snapshot_failed")

    if getattr(risk_snapshot, "identity_binding", None) is not pre_binding:
        return unavailable("account_risk_snapshot_identity_mismatch")

    post_binding, capture_error = capture_binding()
    if capture_error is not None:
        return observe_failure(capture_error, "account_risk_identity_unproven")
    if post_binding is None:
        return unavailable("account_risk_identity_binding_invalid")
    if not same_binding_signature(pre_binding, post_binding):
        return unavailable("account_risk_identity_binding_changed")
    if not binding_is_current(pre_binding):
        return unavailable("account_risk_identity_binding_not_current")
    if not binding_is_current(post_binding):
        return unavailable("account_risk_identity_binding_changed")

    try:
        snapshot_valid = validate_account_risk_snapshot(api, risk_snapshot, pre_binding)
    except Exception as exc:
        return observe_failure(exc, "account_risk_snapshot_not_current")
    if type(snapshot_valid) is not bool or snapshot_valid is not True:
        return unavailable("account_risk_snapshot_not_current")

    try:
        projected = project_typed_snapshot(
            risk_snapshot,
            pre_binding,
            configured_routes=routes,
            configured_loss_limit=configured_loss_limit,
            owner_pid=owner_pid,
            read_started_ns=read_started_ns,
            read_finished_ns=read_finished_ns,
        )
    except Exception as exc:
        return observe_failure(exc, "account_risk_snapshot_projection_invalid")
    if not isinstance(projected, Mapping):
        return unavailable("account_risk_snapshot_projection_invalid")
    if (
        projected.get("identity_binding_sha256") != pre_digest
        or projected.get("fencing_epoch") != pre_signature[4]
        or projected.get("generation") != pre_signature[4]
    ):
        return unavailable("account_risk_snapshot_identity_mismatch")
    if not binding_is_current(pre_binding):
        return unavailable("account_risk_identity_binding_not_current")
    try:
        snapshot_valid = validate_account_risk_snapshot(api, risk_snapshot, pre_binding)
    except Exception as exc:
        return observe_failure(exc, "account_risk_snapshot_not_current")
    if type(snapshot_valid) is not bool or snapshot_valid is not True:
        return unavailable("account_risk_snapshot_not_current")
    return cache_snapshot(projected, generation)


__all__ = (
    "incomplete_account_risk_projection",
    "is_expected_prebaseline_account_risk",
    "normalized_positions_are_flat",
    "position_audit_close_error",
    "project_account_risk_snapshot",
    "read_account_risk_snapshot",
)
