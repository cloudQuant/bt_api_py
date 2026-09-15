"""Pure CTP path-budget calculations and opaque reservation capabilities.

The module deliberately owns no files, account connections, or journal.  The
execution session is the only owner of a reservation's durable state.  This
split lets tests exercise the arithmetic with synthetic evidence while keeping
the authority to reserve or dispatch behind the session's existing writer
lease.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any

BUDGET_SCHEMA_VERSION = "ctp-execution-budget-v1"
BUDGET_MONEY_UNIT = "CNY"
BUDGET_MAX_CNY = Decimal("10000")
BUDGET_ORDINARY_MAX_CNY = Decimal("8000")
BUDGET_RECOVERY_HEADROOM_CNY = Decimal("2000")
_BUDGET_CAPABILITY_SEAL = object()
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID = re.compile(r"^[\w][\w.:-]{0,255}$", re.ASCII)
_SOURCE_VALUES = frozenset({"sdk_runtime", "deployment_manifest", "synthetic_test"})
_STATE_KINDS = frozenset(
    {"prefix", "partial", "unknown", "cancel", "late_fill", "recovery", "final"}
)
_CONTEXT_FIELDS = (
    "account_fingerprint",
    "trading_day",
    "connection_generation",
    "environment_profile",
    "candidate_id",
    "strategy_id",
    "strategy_identity_sha256",
    "execution_cycle_id",
    "scope_version",
    "authorized_instruments",
    "primary_instrument",
)
_COST_FIELDS = (
    "future_gross_margin",
    "seller_option_gross_margin",
    "paid_long_premium",
    "fees_financing",
    "stress_cash_loss",
    "unresolved_reserve",
)
_REQUIRED_STATE_KINDS = frozenset(
    {"prefix", "partial", "unknown", "cancel", "late_fill", "recovery"}
)


class CtpBudgetError(ValueError):
    """A deterministic pure-budget validation failure."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        self.code = code
        super().__init__(detail or code)


def _decimal(value: Any, *, field: str, nonnegative: bool = True) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise CtpBudgetError("budget_invalid_number", field)
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, (int, float, str)):
        try:
            result = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            raise CtpBudgetError("budget_invalid_number", field) from None
    else:
        raise CtpBudgetError("budget_invalid_number", field)
    if not result.is_finite() or (nonnegative and result < 0):
        raise CtpBudgetError("budget_invalid_number", field)
    return result


def _signed_decimal(value: Any, *, field: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise CtpBudgetError("budget_invalid_pnl", field)
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise CtpBudgetError("budget_invalid_pnl", field) from None
    if not result.is_finite():
        raise CtpBudgetError("budget_invalid_pnl", field)
    return result


def _string(value: Any, *, field: str, pattern: re.Pattern[str] | None = None) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise CtpBudgetError("budget_invalid_context", field)
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise CtpBudgetError("budget_invalid_context", field)
    if pattern is not None and pattern.fullmatch(value) is None:
        raise CtpBudgetError("budget_invalid_context", field)
    return value


def _hash(value: Any, *, field: str) -> str:
    return _string(value, field=field, pattern=_HEX64)


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8", "strict")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise CtpBudgetError("budget_invalid_evidence", "non-canonical evidence") from exc


def _instrument(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {"exchange_id", "instrument_id"}:
        raise CtpBudgetError("budget_invalid_scope", "instrument")
    exchange_id = _string(value.get("exchange_id"), field="exchange_id")
    instrument_id = _string(value.get("instrument_id"), field="instrument_id")
    return {"exchange_id": exchange_id, "instrument_id": instrument_id}


def _instrument_list(value: Any) -> tuple[dict[str, str], ...]:
    if not isinstance(value, list) or not 2 <= len(value) <= 3:
        raise CtpBudgetError("budget_invalid_scope", "authorized_instruments")
    result = tuple(_instrument(item) for item in value)
    keys = [(item["exchange_id"], item["instrument_id"]) for item in result]
    if len(set(keys)) != len(keys):
        raise CtpBudgetError("budget_duplicate_instrument", "authorized_instruments")
    return result


def _normalize_context(evidence: Mapping[str, Any]) -> dict[str, Any]:
    context = evidence.get("context")
    if context is not None:
        if not isinstance(context, dict):
            raise CtpBudgetError("budget_invalid_context", "context")
        merged = dict(evidence)
        for field, value in context.items():
            if field in merged and merged[field] != value:
                raise CtpBudgetError("budget_context_mismatch", field)
            merged[field] = value
        evidence = merged
    result: dict[str, Any] = {}
    result["account_fingerprint"] = _string(
        evidence.get("account_fingerprint"), field="account_fingerprint"
    )
    result["trading_day"] = _string(
        evidence.get("trading_day"), field="trading_day", pattern=re.compile(r"^[0-9]{8}$")
    )
    generation = evidence.get("connection_generation")
    if isinstance(generation, bool) or type(generation) is not int or generation <= 0:
        raise CtpBudgetError("budget_invalid_context", "connection_generation")
    result["connection_generation"] = generation
    result["environment_profile"] = _string(
        evidence.get("environment_profile"), field="environment_profile", pattern=_SAFE_ID
    )
    for field in ("candidate_id", "strategy_id", "execution_cycle_id"):
        result[field] = _string(evidence.get(field), field=field, pattern=_SAFE_ID)
    strategy_identity = _hash(
        evidence.get("strategy_identity_sha256"), field="strategy_identity_sha256"
    )
    result["strategy_identity_sha256"] = strategy_identity
    result["scope_version"] = _string(evidence.get("scope_version"), field="scope_version")
    result["authorized_instruments"] = _instrument_list(evidence.get("authorized_instruments"))
    primary = _instrument(evidence.get("primary_instrument"))
    if primary not in result["authorized_instruments"]:
        raise CtpBudgetError("budget_primary_scope_mismatch", "primary_instrument")
    result["primary_instrument"] = primary
    return result


def _context_matches(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return all(left.get(field) == right.get(field) for field in _CONTEXT_FIELDS)


def _state_context(state: Mapping[str, Any], context: Mapping[str, Any]) -> bool:
    nested = state.get("context")
    if nested is not None:
        if not isinstance(nested, dict):
            return False
        state = {**state, **nested}
    for field in _CONTEXT_FIELDS:
        if field not in state:
            return False
        left = state.get(field)
        right = context.get(field)
        # ``_normalize_context`` deliberately canonicalizes the authorized
        # scope to a tuple of immutable instrument mappings.  State rows are
        # JSON-shaped lists/dicts, so compare the same semantic value rather
        # than making a valid JSON row fail merely because of container type.
        if field == "authorized_instruments":
            try:
                left = tuple(_instrument(item) for item in left)
            except (CtpBudgetError, TypeError):
                return False
        if left != right:
            return False
    return True


def _costs(state: Mapping[str, Any]) -> tuple[dict[str, Decimal], Decimal]:
    raw = state.get("costs")
    if not isinstance(raw, Mapping):
        raw = state
    result: dict[str, Decimal] = {}
    for field in _COST_FIELDS:
        if field not in raw:
            raise CtpBudgetError("budget_cost_incomplete", field)
        result[field] = _decimal(raw[field], field=field)
    return result, sum(result.values(), Decimal(0))


def _parse_pnl_minimum(evidence: Mapping[str, Any]) -> tuple[Decimal, bool, tuple[str, ...]]:
    values = evidence.get("historical_cumulative_pnl_cny")
    reasons: list[str] = []
    if values is None:
        explicit = evidence.get("historical_min_pnl_cny")
        if explicit is None:
            return Decimal(0), False, ()
        return _signed_decimal(explicit, field="historical_min_pnl_cny"), False, ()
    if (
        not isinstance(values, Sequence)
        or isinstance(values, (str, bytes, bytearray))
        or not values
    ):
        raise CtpBudgetError("budget_invalid_pnl", "historical_cumulative_pnl_cny")
    parsed: list[Decimal] = []
    for value in values:
        if value is None:
            reasons.append("budget_valuation_unknown")
            continue
        parsed.append(_signed_decimal(value, field="historical_cumulative_pnl_cny"))
    if not parsed:
        return Decimal(0), True, tuple(dict.fromkeys(reasons))
    return min(Decimal(0), min(parsed)), bool(reasons), tuple(dict.fromkeys(reasons))


def _budget_from_pnl(min_pnl: Decimal) -> Decimal:
    return min(BUDGET_MAX_CNY, BUDGET_MAX_CNY + min_pnl)


def _time(value: Any, *, field: str) -> datetime:
    text = _string(value, field=field)
    if not text.endswith("Z"):
        raise CtpBudgetError("budget_invalid_time", field)
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError:
        raise CtpBudgetError("budget_invalid_time", field) from None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise CtpBudgetError("budget_invalid_time", field)
    return parsed.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class CtpBudgetEvaluation:
    """Pure result of evaluating all supplied reachable states."""

    accepted: bool
    mode: str
    candidate_budget_cny: Decimal
    ordinary_cap_cny: Decimal
    ordinary_peak_cny: Decimal
    recovery_increment_cny: Decimal
    full_state_peak_cny: Decimal
    available_required_cny: Decimal
    fresh_available_cny: Decimal | None
    state_costs_cny: tuple[tuple[str, Decimal], ...]
    reasons: tuple[str, ...]
    coverage_complete: bool
    write_eligible: bool
    source: str
    context: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": BUDGET_SCHEMA_VERSION,
            "accepted": self.accepted,
            "mode": self.mode,
            "candidate_budget_cny": format(self.candidate_budget_cny, "f"),
            "ordinary_cap_cny": format(self.ordinary_cap_cny, "f"),
            "ordinary_peak_cny": format(self.ordinary_peak_cny, "f"),
            "recovery_increment_cny": format(self.recovery_increment_cny, "f"),
            "full_state_peak_cny": format(self.full_state_peak_cny, "f"),
            "available_required_cny": format(self.available_required_cny, "f"),
            "fresh_available_cny": (
                None if self.fresh_available_cny is None else format(self.fresh_available_cny, "f")
            ),
            "state_costs_cny": {key: format(value, "f") for key, value in self.state_costs_cny},
            "reasons": list(self.reasons),
            "coverage_complete": self.coverage_complete,
            "write_eligible": self.write_eligible,
            "source": self.source,
            "context": _thaw(self.context),
        }


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def evaluate_ctp_budget(
    evidence: Mapping[str, Any],
    *,
    mode: str = "ordinary",
    now: datetime | None = None,
) -> CtpBudgetEvaluation:
    """Evaluate a complete, provider-neutral CTP budget evidence package.

    ``reachable_states`` are alternatives in one execution plan.  Their costs
    are therefore reduced with ``max`` rather than summed.  A caller supplied
    final basket or ``complete=True`` cannot establish coverage by itself.
    """

    if not isinstance(evidence, Mapping) or type(evidence) is not dict:
        raise CtpBudgetError("budget_invalid_evidence")
    if mode not in {"ordinary", "recovery"}:
        raise CtpBudgetError("budget_invalid_mode")
    context = _normalize_context(evidence)
    source = evidence.get("source")
    if source not in _SOURCE_VALUES:
        raise CtpBudgetError("budget_invalid_source")
    _string(evidence.get("source_version"), field="source_version", pattern=_SAFE_ID)
    if evidence.get("money_unit") != BUDGET_MONEY_UNIT:
        raise CtpBudgetError("budget_money_unit_required")
    states = evidence.get("reachable_states")
    reasons: list[str] = []
    state_costs: list[tuple[str, Decimal]] = []
    ordinary_values: list[Decimal] = []
    recovery_values: list[Decimal] = []
    seen_ids: set[str] = set()
    seen_kinds: set[str] = set()
    if not isinstance(states, list) or not states:
        reasons.append("budget_path_incomplete")
        states = []
    for state in states:
        if not isinstance(state, Mapping) or type(state) is not dict:
            reasons.append("budget_invalid_state")
            continue
        try:
            state_id = _string(state.get("state_id"), field="state_id", pattern=_SAFE_ID)
        except CtpBudgetError as exc:
            reasons.append(exc.code)
            continue
        if state_id in seen_ids:
            reasons.append("budget_duplicate_state")
            continue
        seen_ids.add(state_id)
        kind = state.get("state_kind", state.get("kind"))
        if kind not in _STATE_KINDS:
            reasons.append("budget_invalid_state_kind")
            continue
        seen_kinds.add(kind)
        if not _state_context(state, context):
            reasons.append("budget_state_context_mismatch")
        try:
            cost_map, total = _costs(state)
        except CtpBudgetError as exc:
            reasons.append(exc.code)
            continue
        state_costs.append((state_id, total))
        if kind == "recovery":
            if state.get("non_overlapping") is not True:
                reasons.append("budget_recovery_nonoverlap_unproven")
            increment_raw = state.get("recovery_increment_cny")
            if increment_raw is None:
                increment_raw = state.get("increment_cny")
            if increment_raw is None:
                increment_raw = sum(
                    cost_map[field]
                    for field in ("unresolved_reserve", "fees_financing", "stress_cash_loss")
                )
            try:
                increment = _decimal(increment_raw, field="recovery_increment_cny")
            except CtpBudgetError as exc:
                reasons.append(exc.code)
                increment = Decimal(0)
            recovery_values.append(increment)
        else:
            ordinary_values.append(total)

    required_kinds = evidence.get("required_state_kinds", _REQUIRED_STATE_KINDS)
    if not isinstance(required_kinds, (list, tuple, set, frozenset)):
        reasons.append("budget_path_incomplete")
    else:
        expected = set(required_kinds)
        if not expected.issubset(_STATE_KINDS) or not expected.issubset(seen_kinds):
            reasons.append("budget_path_incomplete")
    # A complete state set must not be declared only by a caller flag.  Require
    # explicit state rows and context on every row before a reservation can be
    # considered write eligible.
    coverage_complete = bool(states) and "budget_path_incomplete" not in reasons
    if evidence.get("complete") is True and not coverage_complete:
        reasons.append("budget_path_incomplete")

    try:
        min_pnl, pnl_unknown, pnl_reasons = _parse_pnl_minimum(evidence)
    except CtpBudgetError as exc:
        reasons.append(exc.code)
        min_pnl, pnl_unknown, pnl_reasons = Decimal(0), True, ()
    reasons.extend(pnl_reasons)
    candidate_budget = _budget_from_pnl(min_pnl)
    ordinary_cap = max(
        Decimal(0), min(BUDGET_ORDINARY_MAX_CNY, candidate_budget - BUDGET_RECOVERY_HEADROOM_CNY)
    )
    ordinary_peak = max(ordinary_values, default=Decimal(0))
    recovery_increment = max(recovery_values, default=Decimal(0))
    full_state = ordinary_peak + recovery_increment
    if mode == "ordinary" and ordinary_peak > ordinary_cap:
        reasons.append("budget_ordinary_cap_exceeded")
    if mode == "recovery":
        if ordinary_peak > ordinary_cap:
            reasons.append("budget_ordinary_cap_exceeded")
        if not recovery_values:
            reasons.append("budget_recovery_state_missing")
        if full_state > candidate_budget:
            reasons.append("budget_total_exceeded")
        if recovery_increment <= 0:
            reasons.append("budget_recovery_not_reducing")

    unabsorbed = _decimal(
        evidence.get("remaining_unabsorbed_new_obligation_cny", 0),
        field="remaining_unabsorbed_new_obligation_cny",
    )
    recovery_headroom = _decimal(
        evidence.get("unallocated_recovery_headroom_cny", 0),
        field="unallocated_recovery_headroom_cny",
    )
    available_required = unabsorbed + recovery_headroom
    fresh_available_raw = evidence.get("fresh_available_cny")
    fresh_available = (
        None
        if fresh_available_raw is None
        else _decimal(fresh_available_raw, field="fresh_available_cny")
    )
    if fresh_available is not None and fresh_available < available_required:
        reasons.append("budget_available_insufficient")
    if pnl_unknown:
        reasons.append("budget_valuation_unknown")
    expiry = evidence.get("expires_at")
    if expiry is not None:
        try:
            expires_at = _time(expiry, field="expires_at")
            current = now.astimezone(UTC) if now is not None and now.tzinfo else datetime.now(UTC)
            if current >= expires_at:
                reasons.append("budget_expired")
        except CtpBudgetError as exc:
            reasons.append(exc.code)

    # Synthetic evidence can prove arithmetic and journal state, but it cannot
    # authorize a native account write.  The session may only upgrade this
    # boundary after its own trusted collector has supplied live facts.
    write_eligible = source in {"sdk_runtime", "deployment_manifest"} and not pnl_unknown
    accepted = not reasons and coverage_complete
    return CtpBudgetEvaluation(
        accepted=accepted,
        mode=mode,
        candidate_budget_cny=candidate_budget,
        ordinary_cap_cny=ordinary_cap,
        ordinary_peak_cny=ordinary_peak,
        recovery_increment_cny=recovery_increment,
        full_state_peak_cny=full_state,
        available_required_cny=available_required,
        fresh_available_cny=fresh_available,
        state_costs_cny=tuple(state_costs),
        reasons=tuple(dict.fromkeys(reasons)),
        coverage_complete=coverage_complete,
        write_eligible=write_eligible,
        source=source,
        context=MappingProxyType(_thaw(context)),
    )


def evaluate_ctp_budget_numbers(
    *,
    ordinary_peak_cny: Any,
    recovery_increment_cny: Any = 0,
    historical_cumulative_pnl_cny: Sequence[Any] = (0,),
    mode: str = "ordinary",
    fresh_available_cny: Any | None = None,
    remaining_unabsorbed_new_obligation_cny: Any = 0,
    unallocated_recovery_headroom_cny: Any = 0,
) -> CtpBudgetEvaluation:
    """Evaluate hand-fixed numeric values without granting a reservation.

    This helper is intentionally a pure numeric oracle adapter.  It is useful
    for independent expected-value tests; the durable session API requires the
    richer per-state evidence package above.
    """

    ordinary = _decimal(ordinary_peak_cny, field="ordinary_peak_cny")
    recovery = _decimal(recovery_increment_cny, field="recovery_increment_cny")
    context = {
        "account_fingerprint": "synthetic-numeric-account",
        "trading_day": "20260911",
        "connection_generation": 1,
        "environment_profile": "synthetic_test",
        "candidate_id": "synthetic-numeric-candidate",
        "strategy_id": "synthetic-numeric-strategy",
        "strategy_identity_sha256": "0" * 64,
        "execution_cycle_id": "synthetic-numeric-cycle",
        "scope_version": "synthetic-numeric-v1",
        "authorized_instruments": [
            {"exchange_id": "SYNTH", "instrument_id": "LEG1"},
            {"exchange_id": "SYNTH", "instrument_id": "LEG2"},
        ],
        "primary_instrument": {"exchange_id": "SYNTH", "instrument_id": "LEG1"},
    }
    min_pnl = min(
        (
            _signed_decimal(item, field="historical_cumulative_pnl_cny")
            for item in historical_cumulative_pnl_cny
            if item is not None
        ),
        default=Decimal(0),
    )
    candidate_budget = _budget_from_pnl(min(Decimal(0), min_pnl))
    ordinary_cap = max(
        Decimal(0), min(BUDGET_ORDINARY_MAX_CNY, candidate_budget - BUDGET_RECOVERY_HEADROOM_CNY)
    )
    required = _decimal(
        remaining_unabsorbed_new_obligation_cny, field="remaining_unabsorbed_new_obligation_cny"
    ) + _decimal(unallocated_recovery_headroom_cny, field="unallocated_recovery_headroom_cny")
    available = (
        None
        if fresh_available_cny is None
        else _decimal(fresh_available_cny, field="fresh_available_cny")
    )
    reasons: list[str] = []
    if mode not in {"ordinary", "recovery"}:
        raise CtpBudgetError("budget_invalid_mode")
    if mode == "ordinary" and ordinary > ordinary_cap:
        reasons.append("budget_ordinary_cap_exceeded")
    if mode == "recovery":
        if ordinary > ordinary_cap:
            reasons.append("budget_ordinary_cap_exceeded")
        if ordinary + recovery > candidate_budget:
            reasons.append("budget_total_exceeded")
        if recovery <= 0:
            reasons.append("budget_recovery_not_reducing")
    if available is not None and available < required:
        reasons.append("budget_available_insufficient")
    return CtpBudgetEvaluation(
        accepted=not reasons,
        mode=mode,
        candidate_budget_cny=candidate_budget,
        ordinary_cap_cny=ordinary_cap,
        ordinary_peak_cny=ordinary,
        recovery_increment_cny=recovery,
        full_state_peak_cny=ordinary + recovery,
        available_required_cny=required,
        fresh_available_cny=available,
        state_costs_cny=(("numeric", ordinary + recovery),),
        reasons=tuple(reasons),
        coverage_complete=False,
        write_eligible=False,
        source="synthetic_test",
        context=MappingProxyType(context),
    )


class CtpBudgetReservation:
    """Opaque, session-owned reservation returned after durable commit."""

    __slots__ = (
        "_seal",
        "_owner",
        "_session_token",
        "_state",
    )

    def __init__(
        self,
        *,
        _seal: object,
        _owner: object,
        _session_token: object,
        state: Mapping[str, Any],
    ) -> None:
        if _seal is not _BUDGET_CAPABILITY_SEAL:
            raise TypeError("session-owned budget reservation required")
        self._seal = _seal
        self._owner = _owner
        self._session_token = _session_token
        self._state = MappingProxyType(dict(state))

    @property
    def reservation_id(self) -> str:
        return self._state["reservation_id"]

    @property
    def mode(self) -> str:
        return self._state["mode"]

    @property
    def amount_cny(self) -> Decimal:
        return self._state["amount_cny"]

    @property
    def candidate_id(self) -> str:
        return self._state["candidate_id"]

    @property
    def execution_cycle_id(self) -> str:
        return self._state["execution_cycle_id"]

    @property
    def synthetic(self) -> bool:
        return bool(self._state.get("synthetic"))

    def audit(self) -> dict[str, Any]:
        return _thaw(self._state)


def _new_budget_reservation(
    *, owner: object, session_token: object, state: Mapping[str, Any]
) -> CtpBudgetReservation:
    return CtpBudgetReservation(
        _seal=_BUDGET_CAPABILITY_SEAL,
        _owner=owner,
        _session_token=session_token,
        state=state,
    )


def _is_budget_reservation(value: Any, *, owner: object | None = None) -> bool:
    return bool(
        type(value) is CtpBudgetReservation
        and value._seal is _BUDGET_CAPABILITY_SEAL
        and (owner is None or value._owner is owner)
    )


def budget_evidence_digest(evidence: Mapping[str, Any]) -> str:
    """Digest the exact provider-neutral package for durable binding."""

    return hashlib.sha256(_canonical_json(evidence)).hexdigest()


__all__ = [
    "BUDGET_MONEY_UNIT",
    "BUDGET_SCHEMA_VERSION",
    "CtpBudgetError",
    "CtpBudgetEvaluation",
    "CtpBudgetReservation",
    "budget_evidence_digest",
    "evaluate_ctp_budget",
    "evaluate_ctp_budget_numbers",
]
