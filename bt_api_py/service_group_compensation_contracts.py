"""Typed contracts for the internal P5B group-compensation Risk bridge.

This module contains wire values only.  It grants no service capability and
does not submit or query an order.  The Risk owner must independently rebuild
the source facts from its injected durable source before accepting an
authorization.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from enum import StrEnum
from typing import Any, Protocol

from bt_api_execution import ExecutionState, GroupChildOrderStatus, Side, payload_sha256

from .service_registry import ActiveRegistrationContext

_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DECIMAL = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
_ACTION_REF_SCHEMA = "group-compensation-action-ref-v1"
_RESOLVED_ACTION_REF_SCHEMA = "group-compensation-resolved-action-ref-v3"
_SOURCE_FACTS_SCHEMA = "group-compensation-source-facts-v3"
_AUTHORIZATION_SCHEMA = "group-compensation-close-auth-v4"
_CLOSURE_FACTS_SCHEMA = "group-compensation-closure-facts-v2"
_SOURCE_LEG_FACTS_SCHEMA = "group-compensation-source-leg-facts-v1"
_CLIENT_ORDER_ID_SCHEMA = "group-compensation-client-order-id-v1"
_BUDGET_POLICY_SCHEMA = "group-compensation-budget-policy-v1"
LIMIT_CLOSE_NOTIONAL_V1 = "LIMIT_CLOSE_NOTIONAL_V1"
_MAX_DECIMAL_TEXT = 512
_MAX_DECIMAL_DIGITS = 256
_MAX_DECIMAL_EXPONENT = 256
_RISK_INTENT_ID = re.compile(r"^risk-order:[0-9a-f]{64}$")


class GroupCompensationActionPhase(StrEnum):
    """Monotonic durable phase of one compensation action."""

    UNSTARTED = "UNSTARTED"
    PLAN_RECORDED = "PLAN_RECORDED"
    SUBMIT_STARTED = "SUBMIT_STARTED"
    STATUS_OBSERVED = "STATUS_OBSERVED"
    RESOLVED = "RESOLVED"


def _reference(value: object, name: str) -> str:
    if type(value) is not str or _REFERENCE.fullmatch(value) is None:
        raise ValueError(f"invalid {name}")
    return value


def _digest(value: object, name: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise ValueError(f"invalid {name}")
    return value


def _risk_intent_id(value: object) -> str:
    if type(value) is not str or _RISK_INTENT_ID.fullmatch(value) is None:
        raise ValueError("invalid deterministic Risk intent ID")
    return value


def _decimal(value: object, name: str, *, positive: bool = False) -> Decimal:
    if type(value) is not Decimal or not value.is_finite() or (positive and value <= 0):
        raise ValueError(f"invalid {name}")
    _validate_decimal_bounds(value, name)
    return value


def _validate_decimal_bounds(value: Decimal, name: str) -> None:
    parts = value.as_tuple()
    if (
        len(parts.digits) > _MAX_DECIMAL_DIGITS
        or type(parts.exponent) is not int
        or abs(parts.exponent) > _MAX_DECIMAL_EXPONENT
    ):
        raise ValueError(f"out-of-range {name}")


def _fraction(value: Decimal, name: str) -> Fraction:
    return Fraction(_decimal(value, name))


def _fraction_to_decimal_contract(value: Fraction) -> Decimal:
    """Convert only finite base-ten rationals without Decimal context rounding."""

    if type(value) is not Fraction:
        raise ValueError("invalid exact rational")
    denominator = value.denominator
    twos = fives = 0
    while denominator % 2 == 0:
        denominator //= 2
        twos += 1
    while denominator % 5 == 0:
        denominator //= 5
        fives += 1
    scale = max(twos, fives)
    if denominator != 1 or scale > _MAX_DECIMAL_EXPONENT or len(str(abs(value.numerator))) > _MAX_DECIMAL_DIGITS + scale:
        raise ValueError("rational is outside exact decimal contract bounds")
    unscaled = value.numerator * (2 ** (scale - twos)) * (5 ** (scale - fives))
    sign = "-" if unscaled < 0 else ""
    digits = str(abs(unscaled))
    if scale:
        digits = digits.rjust(scale + 1, "0")
        text = sign + digits[:-scale] + "." + digits[-scale:]
    else:
        text = sign + digits
    result = Decimal(text)
    if Fraction(result) != value:
        raise ValueError("rational did not round-trip exactly")
    return result


def _decimal_payload(value: object, name: str, *, positive: bool = False) -> Decimal:
    if (
        type(value) is not str
        or len(value) > _MAX_DECIMAL_TEXT
        or _DECIMAL.fullmatch(value) is None
    ):
        raise ValueError(f"invalid {name}")
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError):
        raise ValueError(f"invalid {name}") from None
    if not parsed.is_finite() or (positive and parsed <= 0):
        raise ValueError(f"invalid {name}")
    _validate_decimal_bounds(parsed, name)
    if format(parsed, "f") != value:
        raise ValueError(f"invalid {name}")
    return parsed


def _decimal_text(value: Decimal) -> str:
    return format(_decimal(value, "decimal value"), "f")


def _vector(value: object) -> tuple[tuple[str, Decimal], ...]:
    if type(value) is not tuple or len(value) != 3:
        raise ValueError("three ordered residual coordinates are required")
    result: list[tuple[str, Decimal]] = []
    for item in value:
        if type(item) is not tuple or len(item) != 2:
            raise ValueError("invalid residual coordinate")
        leg_id = _reference(item[0], "residual leg id")
        amount = _decimal(item[1], "residual base amount")
        result.append((leg_id, amount))
    if len({leg_id for leg_id, _ in result}) != 3:
        raise ValueError("duplicate residual coordinate")
    return tuple(result)


def _vector_payload(value: tuple[tuple[str, Decimal], ...]) -> list[list[str]]:
    return [[leg_id, _decimal_text(amount)] for leg_id, amount in value]


def _validate_prior_resolved_actions(
    value: object,
    *,
    leg_ids: tuple[str, ...],
    selected_leg_id: str,
    selected_side: Side,
    selected_instrument: str,
    selected_metadata_version: str,
    selected_base_units_per_order_unit: Decimal,
    quote_currency: str,
    budget_policy_fingerprint: str,
) -> tuple[GroupCompensationResolvedActionRef, ...]:
    if (
        type(value) is not tuple
        or len(value) > 9
        or any(type(item) is not GroupCompensationResolvedActionRef for item in value)
        or len({item.action_id for item in value}) != len(value)
        or any(item.source_leg_id not in leg_ids for item in value)
        or any(item.quote_currency != quote_currency for item in value)
        or any(item.budget_policy_fingerprint != budget_policy_fingerprint for item in value)
    ):
        raise ValueError("invalid or unbounded prior compensation receipts")
    for item in value:
        if _fraction(item.close_reference_notional, "prior close notional") != (
            _fraction(item.close_quantity, "prior close quantity")
            * _fraction(item.close_limit_price, "prior close limit price")
            * _fraction(item.source_base_units_per_order_unit, "prior close conversion")
        ):
            raise ValueError("prior compensation notional does not match its accepted limit")
        if item.source_leg_id == selected_leg_id and (
            item.instrument != selected_instrument
            or item.metadata_version != selected_metadata_version
            or item.source_base_units_per_order_unit != selected_base_units_per_order_unit
            or item.closing_side is selected_side
        ):
            raise ValueError("prior close does not match the selected source leg")
    return value


@dataclass(frozen=True, slots=True)
class GroupCompensationActionRef:
    """Minimal prepare selector; it is not an authorization."""

    group_id: str
    source_leg_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "group_id", _reference(self.group_id, "group_id"))
        object.__setattr__(self, "source_leg_id", _reference(self.source_leg_id, "source_leg_id"))

    def to_payload(self) -> dict[str, str]:
        return {
            "schema": _ACTION_REF_SCHEMA,
            "group_id": self.group_id,
            "source_leg_id": self.source_leg_id,
        }

    @classmethod
    def from_payload(cls, value: object) -> GroupCompensationActionRef:
        if not isinstance(value, Mapping) or set(value) != {"schema", "group_id", "source_leg_id"}:
            raise ValueError("invalid compensation action reference")
        if value["schema"] != _ACTION_REF_SCHEMA:
            raise ValueError("invalid compensation action reference schema")
        return cls(group_id=value["group_id"], source_leg_id=value["source_leg_id"])


@dataclass(frozen=True, slots=True)
class GroupCompensationResolvedActionRef:
    """Bounded reference to an earlier Risk-verified terminal CLOSE action."""

    action_id: str
    source_leg_id: str
    intent_id: str
    risk_intent_id: str
    client_order_id: str
    permit_id: str
    authorization_digest: str
    receipt_sha256: str
    closing_side: Side
    close_quantity: Decimal
    close_quantity_unit: str
    source_base_units_per_order_unit: Decimal
    instrument: str
    metadata_version: str
    close_limit_price: Decimal
    close_reference_notional: Decimal
    quote_currency: str
    budget_policy_fingerprint: str

    def __post_init__(self) -> None:
        for name in (
            "action_id",
            "source_leg_id",
            "intent_id",
            "client_order_id",
            "permit_id",
            "close_quantity_unit",
            "instrument",
            "metadata_version",
            "quote_currency",
        ):
            object.__setattr__(self, name, _reference(getattr(self, name), name))
        object.__setattr__(self, "risk_intent_id", _risk_intent_id(self.risk_intent_id))
        for name in ("authorization_digest", "receipt_sha256", "budget_policy_fingerprint"):
            _digest(getattr(self, name), name)
        try:
            object.__setattr__(self, "closing_side", Side(self.closing_side))
        except (TypeError, ValueError) as error:
            raise ValueError("invalid resolved compensation side") from error
        object.__setattr__(
            self, "close_quantity", _decimal(self.close_quantity, "resolved close quantity", positive=True)
        )
        object.__setattr__(
            self,
            "source_base_units_per_order_unit",
            _decimal(self.source_base_units_per_order_unit, "resolved close base-unit ratio", positive=True),
        )
        object.__setattr__(
            self,
            "close_reference_notional",
            _decimal(self.close_reference_notional, "resolved close reference notional", positive=True),
        )
        object.__setattr__(
            self,
            "close_limit_price",
            _decimal(self.close_limit_price, "resolved close limit price", positive=True),
        )
        if _fraction(self.close_reference_notional, "resolved close notional") != (
            _fraction(self.close_quantity, "resolved close quantity")
            * _fraction(self.close_limit_price, "resolved close limit price")
            * _fraction(self.source_base_units_per_order_unit, "resolved close conversion")
        ):
            raise ValueError("resolved close notional does not match its accepted limit")
        if self.intent_id != derive_group_compensation_intent_id(self.action_id):
            raise ValueError("resolved compensation intent ID is not action-bound")
        if self.client_order_id != derive_group_compensation_client_order_id(self.action_id):
            raise ValueError("resolved compensation client order ID is not action-bound")

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": _RESOLVED_ACTION_REF_SCHEMA,
            "action_id": self.action_id,
            "source_leg_id": self.source_leg_id,
            "intent_id": self.intent_id,
            "risk_intent_id": self.risk_intent_id,
            "client_order_id": self.client_order_id,
            "permit_id": self.permit_id,
            "authorization_digest": self.authorization_digest,
            "receipt_sha256": self.receipt_sha256,
            "closing_side": self.closing_side.value,
            "close_quantity": _decimal_text(self.close_quantity),
            "close_quantity_unit": self.close_quantity_unit,
            "source_base_units_per_order_unit": _decimal_text(
                self.source_base_units_per_order_unit
            ),
            "instrument": self.instrument,
            "metadata_version": self.metadata_version,
            "close_limit_price": _decimal_text(self.close_limit_price),
            "close_reference_notional": _decimal_text(self.close_reference_notional),
            "quote_currency": self.quote_currency,
            "budget_policy_fingerprint": self.budget_policy_fingerprint,
        }

    @classmethod
    def from_payload(cls, value: object) -> GroupCompensationResolvedActionRef:
        expected = {
            "schema",
            "action_id",
            "source_leg_id",
            "intent_id",
            "risk_intent_id",
            "client_order_id",
            "permit_id",
            "authorization_digest",
            "receipt_sha256",
            "closing_side",
            "close_quantity",
            "close_quantity_unit",
            "source_base_units_per_order_unit",
            "instrument",
            "metadata_version",
            "close_limit_price",
            "close_reference_notional",
            "quote_currency",
            "budget_policy_fingerprint",
        }
        if (
            not isinstance(value, Mapping)
            or set(value) != expected
            or value["schema"] != _RESOLVED_ACTION_REF_SCHEMA
        ):
            raise ValueError("invalid resolved compensation action reference")
        return cls(
            action_id=value["action_id"],
            source_leg_id=value["source_leg_id"],
            intent_id=value["intent_id"],
            risk_intent_id=value["risk_intent_id"],
            client_order_id=value["client_order_id"],
            permit_id=value["permit_id"],
            authorization_digest=value["authorization_digest"],
            receipt_sha256=value["receipt_sha256"],
            closing_side=value["closing_side"],
            close_quantity=_decimal_payload(value["close_quantity"], "resolved close quantity", positive=True),
            close_quantity_unit=value["close_quantity_unit"],
            source_base_units_per_order_unit=_decimal_payload(
                value["source_base_units_per_order_unit"], "resolved close base-unit ratio", positive=True
            ),
            instrument=value["instrument"],
            metadata_version=value["metadata_version"],
            close_limit_price=_decimal_payload(value["close_limit_price"], "resolved close limit price", positive=True),
            close_reference_notional=_decimal_payload(
                value["close_reference_notional"], "resolved close reference notional", positive=True
            ),
            quote_currency=value["quote_currency"],
            budget_policy_fingerprint=value["budget_policy_fingerprint"],
        )


@dataclass(frozen=True, slots=True)
class GroupCompensationSourceFacts:
    """Hash-verifiable group-owned facts reconstructed by the injected source.

    ``phase`` comes from the durable group event stream.  Budget fields are
    from the accepted compensation-cap policy and its durable remaining
    reservation ledger; they are never inferred from an opening quote.
    """

    phase: GroupCompensationActionPhase
    action_id: str
    ordinal: int
    group_id: str
    origin_run_id: str
    group_fingerprint: str
    execution_scope_key: str
    account_ref: str
    strategy_id: str
    source_leg_id: str
    source_intent_id: str
    source_request_fingerprint: str
    source_terminal_status: GroupChildOrderStatus
    source_requested_quantity: Decimal
    source_claim_digest: str
    source_risk_resolution_digest: str
    source_event_digest: str
    source_side: Side
    instrument: str
    metadata_version: str
    source_price: Decimal
    source_base_units_per_order_unit: Decimal
    source_quantity_unit: str
    source_quote_currency: str
    source_uncompensated_position_base_units: Decimal
    source_chain_deficit_vector_base_units: tuple[tuple[str, Decimal], ...]
    source_filled_position_exposure_vector_base_units: tuple[tuple[str, Decimal], ...]
    prior_resolved_actions: tuple[GroupCompensationResolvedActionRef, ...]
    compensation_budget_cap_amount: Decimal
    action_remaining_budget_amount: Decimal
    action_remaining_budget_currency: str
    action_remaining_budget_metric: str
    action_remaining_budget_policy_id: str
    action_remaining_budget_policy_version: int
    action_remaining_budget_policy_fingerprint: str

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "phase", GroupCompensationActionPhase(self.phase))
            object.__setattr__(self, "source_side", Side(self.source_side))
        except (TypeError, ValueError) as error:
            raise ValueError("invalid compensation source facts enum") from error
        for name in (
            "action_id",
            "group_id",
            "origin_run_id",
            "account_ref",
            "strategy_id",
            "source_leg_id",
            "source_intent_id",
            "instrument",
            "metadata_version",
            "source_quantity_unit",
            "source_quote_currency",
            "action_remaining_budget_currency",
            "action_remaining_budget_metric",
            "action_remaining_budget_policy_id",
        ):
            object.__setattr__(self, name, _reference(getattr(self, name), name))
        for name in (
            "group_fingerprint",
            "source_request_fingerprint",
            "source_claim_digest",
            "source_risk_resolution_digest",
            "source_event_digest",
            "action_remaining_budget_policy_fingerprint",
        ):
            _digest(getattr(self, name), name)
        if type(self.execution_scope_key) is not str or not self.execution_scope_key:
            raise ValueError("invalid execution scope key")
        if type(self.ordinal) is not int or not 1 <= self.ordinal <= 1_000_000:
            raise ValueError("invalid compensation ordinal")
        if type(self.source_terminal_status) is not GroupChildOrderStatus:
            raise ValueError("invalid source terminal status")
        if (
            self.source_terminal_status.state not in {
                ExecutionState.FILLED,
                ExecutionState.CANCELLED,
                ExecutionState.REJECTED,
            }
            or self.source_terminal_status.filled_quantity <= 0
            or _fraction(
                _decimal(self.source_requested_quantity, "source requested quantity", positive=True),
                "source requested quantity",
            ) <= 0
            or _fraction(
                _decimal(self.source_terminal_status.filled_quantity, "source filled quantity"),
                "source filled quantity",
            ) > _fraction(self.source_requested_quantity, "source requested quantity")
            or self.source_terminal_status.intent_id != self.source_intent_id
        ):
            raise ValueError("source order must have exact terminal fills within its request")
        object.__setattr__(self, "source_price", _decimal(self.source_price, "source price", positive=True))
        object.__setattr__(
            self,
            "source_base_units_per_order_unit",
            _decimal(self.source_base_units_per_order_unit, "source base-unit ratio", positive=True),
        )
        object.__setattr__(
            self,
            "compensation_budget_cap_amount",
            _decimal(self.compensation_budget_cap_amount, "compensation budget cap", positive=True),
        )
        object.__setattr__(
            self,
            "source_uncompensated_position_base_units",
            _decimal(self.source_uncompensated_position_base_units, "source uncompensated position"),
        )
        chain_deficit = _vector(self.source_chain_deficit_vector_base_units)
        filled_exposure = _vector(self.source_filled_position_exposure_vector_base_units)
        prior_actions = _validate_prior_resolved_actions(
            self.prior_resolved_actions,
            leg_ids=tuple(leg_id for leg_id, _ in filled_exposure),
            selected_leg_id=self.source_leg_id,
            selected_side=self.source_side,
            selected_instrument=self.instrument,
            selected_metadata_version=self.metadata_version,
            selected_base_units_per_order_unit=self.source_base_units_per_order_unit,
            quote_currency=self.source_quote_currency,
            budget_policy_fingerprint=self.action_remaining_budget_policy_fingerprint,
        )
        object.__setattr__(self, "prior_resolved_actions", prior_actions)
        object.__setattr__(self, "source_chain_deficit_vector_base_units", chain_deficit)
        object.__setattr__(self, "source_filled_position_exposure_vector_base_units", filled_exposure)
        filled_exposure_by_leg = dict(filled_exposure)
        selected_source_sign = 1 if self.source_side is Side.BUY else -1
        selected_close_delta = sum(
            (
                _fraction(item.close_quantity, "prior close quantity")
                * _fraction(item.source_base_units_per_order_unit, "prior close conversion")
                * (1 if item.closing_side is Side.BUY else -1)
                for item in self.prior_resolved_actions
                if item.source_leg_id == self.source_leg_id
            ),
            Fraction(0),
        )
        selected_source_position = (
            selected_source_sign
            * _fraction(self.source_terminal_status.filled_quantity, "source filled quantity")
            * _fraction(self.source_base_units_per_order_unit, "source base-unit conversion")
            + selected_close_delta
        )
        if (
            tuple(leg_id for leg_id, _ in chain_deficit)
            != tuple(leg_id for leg_id, _ in filled_exposure)
            or self.source_leg_id not in filled_exposure_by_leg
            or _fraction(filled_exposure_by_leg[self.source_leg_id], "selected exposure")
            != _fraction(self.source_uncompensated_position_base_units, "selected uncompensated exposure")
            or _fraction(filled_exposure_by_leg[self.source_leg_id], "selected exposure") != selected_source_position
            or self.source_uncompensated_position_base_units == 0
            or (self.source_side is Side.BUY and self.source_uncompensated_position_base_units < 0)
            or (self.source_side is Side.SELL and self.source_uncompensated_position_base_units > 0)
            or self.action_id
            != derive_group_compensation_action_id(
                group_id=self.group_id,
                group_fingerprint=self.group_fingerprint,
                source_leg_id=self.source_leg_id,
                source_event_digest=self.source_event_digest,
                ordinal=self.ordinal,
            )
        ):
            raise ValueError("source residual is absent or disagrees with its vector")
        object.__setattr__(
            self,
            "action_remaining_budget_amount",
            _decimal(self.action_remaining_budget_amount, "remaining compensation budget", positive=True),
        )
        if (
            self.action_remaining_budget_currency != self.source_quote_currency
            or self.action_remaining_budget_metric != LIMIT_CLOSE_NOTIONAL_V1
            or self.action_remaining_budget_amount > self.compensation_budget_cap_amount
            or _fraction(self.action_remaining_budget_amount, "remaining compensation budget")
            != _fraction(self.compensation_budget_cap_amount, "compensation budget cap")
            - sum(
                (_fraction(item.close_reference_notional, "prior close notional") for item in prior_actions),
                Fraction(0),
            )
            or self.action_remaining_budget_policy_fingerprint
            != group_compensation_budget_policy_fingerprint(
                policy_id=self.action_remaining_budget_policy_id,
                policy_version=self.action_remaining_budget_policy_version,
                cap_amount=self.compensation_budget_cap_amount,
                currency=self.source_quote_currency,
                metric=self.action_remaining_budget_metric,
            )
        ):
            raise ValueError("compensation budget policy does not match the source instrument")
        if (
            type(self.action_remaining_budget_policy_version) is not int
            or self.action_remaining_budget_policy_version <= 0
        ):
            raise ValueError("invalid compensation budget policy version")

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": _SOURCE_FACTS_SCHEMA,
            "phase": self.phase.value,
            "action_id": self.action_id,
            "ordinal": self.ordinal,
            "group_id": self.group_id,
            "origin_run_id": self.origin_run_id,
            "group_fingerprint": self.group_fingerprint,
            "execution_scope_key": self.execution_scope_key,
            "account_ref": self.account_ref,
            "strategy_id": self.strategy_id,
            "source_leg_id": self.source_leg_id,
            "source_intent_id": self.source_intent_id,
            "source_request_fingerprint": self.source_request_fingerprint,
            "source_terminal_status": self.source_terminal_status.to_payload(),
            "source_requested_quantity": _decimal_text(self.source_requested_quantity),
            "source_claim_digest": self.source_claim_digest,
            "source_risk_resolution_digest": self.source_risk_resolution_digest,
            "source_event_digest": self.source_event_digest,
            "source_side": self.source_side.value,
            "instrument": self.instrument,
            "metadata_version": self.metadata_version,
            "source_price": _decimal_text(self.source_price),
            "source_base_units_per_order_unit": _decimal_text(self.source_base_units_per_order_unit),
            "source_quantity_unit": self.source_quantity_unit,
            "source_quote_currency": self.source_quote_currency,
            "source_uncompensated_position_base_units": _decimal_text(self.source_uncompensated_position_base_units),
            "source_chain_deficit_vector_base_units": _vector_payload(self.source_chain_deficit_vector_base_units),
            "source_filled_position_exposure_vector_base_units": _vector_payload(
                self.source_filled_position_exposure_vector_base_units
            ),
            "prior_resolved_actions": [item.to_payload() for item in self.prior_resolved_actions],
            "compensation_budget_cap_amount": _decimal_text(self.compensation_budget_cap_amount),
            "action_remaining_budget_amount": _decimal_text(self.action_remaining_budget_amount),
            "action_remaining_budget_currency": self.action_remaining_budget_currency,
            "action_remaining_budget_metric": self.action_remaining_budget_metric,
            "action_remaining_budget_policy_id": self.action_remaining_budget_policy_id,
            "action_remaining_budget_policy_version": self.action_remaining_budget_policy_version,
            "action_remaining_budget_policy_fingerprint": self.action_remaining_budget_policy_fingerprint,
        }

    @property
    def digest(self) -> str:
        # Lifecycle phase is mutable evidence, not part of the immutable
        # source authorization. Durable phase transitions are independently
        # reconstructed from the group event chain.
        payload = self.to_payload()
        payload.pop("phase")
        return payload_sha256(payload)

    @classmethod
    def from_payload(cls, value: object) -> GroupCompensationSourceFacts:
        expected = {
            "schema", "phase", "action_id", "ordinal", "group_id", "origin_run_id",
            "group_fingerprint", "execution_scope_key", "account_ref", "strategy_id",
            "source_leg_id", "source_intent_id", "source_request_fingerprint",
            "source_terminal_status", "source_requested_quantity", "source_claim_digest", "source_risk_resolution_digest",
            "source_event_digest", "source_side", "instrument", "metadata_version",
            "source_price", "source_base_units_per_order_unit", "source_quantity_unit", "source_quote_currency",
            "source_uncompensated_position_base_units", "source_chain_deficit_vector_base_units",
            "source_filled_position_exposure_vector_base_units", "prior_resolved_actions",
            "compensation_budget_cap_amount", "action_remaining_budget_amount", "action_remaining_budget_currency",
            "action_remaining_budget_metric", "action_remaining_budget_policy_id",
            "action_remaining_budget_policy_version", "action_remaining_budget_policy_fingerprint",
        }
        if not isinstance(value, Mapping) or set(value) != expected or value["schema"] != _SOURCE_FACTS_SCHEMA:
            raise ValueError("invalid compensation source facts payload")
        vectors: dict[str, tuple[tuple[str, Decimal], ...]] = {}
        for field in (
            "source_chain_deficit_vector_base_units",
            "source_filled_position_exposure_vector_base_units",
        ):
            raw_vector = value[field]
            if type(raw_vector) is not list or len(raw_vector) != 3:
                raise ValueError(f"invalid {field} payload")
            vector = tuple(
                (_reference(item[0], "residual leg id"), _decimal_payload(item[1], "base amount"))
                for item in raw_vector
                if type(item) is list and len(item) == 2
            )
            if len(vector) != 3:
                raise ValueError(f"invalid {field} payload")
            vectors[field] = vector
        raw_prior = value["prior_resolved_actions"]
        if type(raw_prior) is not list or len(raw_prior) > 9:
            raise ValueError("invalid or unbounded prior compensation receipts")
        prior_resolved_actions = tuple(
            GroupCompensationResolvedActionRef.from_payload(item) for item in raw_prior
        )
        return cls(
            phase=value["phase"],
            action_id=value["action_id"],
            ordinal=value["ordinal"],
            group_id=value["group_id"],
            origin_run_id=value["origin_run_id"],
            group_fingerprint=value["group_fingerprint"],
            execution_scope_key=value["execution_scope_key"],
            account_ref=value["account_ref"],
            strategy_id=value["strategy_id"],
            source_leg_id=value["source_leg_id"],
            source_intent_id=value["source_intent_id"],
            source_request_fingerprint=value["source_request_fingerprint"],
            source_terminal_status=GroupChildOrderStatus.from_payload(value["source_terminal_status"]),
            source_requested_quantity=_decimal_payload(value["source_requested_quantity"], "source requested quantity", positive=True),
            source_claim_digest=value["source_claim_digest"],
            source_risk_resolution_digest=value["source_risk_resolution_digest"],
            source_event_digest=value["source_event_digest"],
            source_side=value["source_side"],
            instrument=value["instrument"],
            metadata_version=value["metadata_version"],
            source_price=_decimal_payload(value["source_price"], "source price", positive=True),
            source_base_units_per_order_unit=_decimal_payload(value["source_base_units_per_order_unit"], "source base-unit ratio", positive=True),
            source_quantity_unit=value["source_quantity_unit"],
            source_quote_currency=value["source_quote_currency"],
            source_uncompensated_position_base_units=_decimal_payload(
                value["source_uncompensated_position_base_units"], "source uncompensated position"
            ),
            source_chain_deficit_vector_base_units=vectors["source_chain_deficit_vector_base_units"],
            source_filled_position_exposure_vector_base_units=(
                vectors["source_filled_position_exposure_vector_base_units"]
            ),
            prior_resolved_actions=prior_resolved_actions,
            compensation_budget_cap_amount=_decimal_payload(value["compensation_budget_cap_amount"], "compensation budget cap", positive=True),
            action_remaining_budget_amount=_decimal_payload(value["action_remaining_budget_amount"], "remaining compensation budget", positive=True),
            action_remaining_budget_currency=value["action_remaining_budget_currency"],
            action_remaining_budget_metric=value["action_remaining_budget_metric"],
            action_remaining_budget_policy_id=value["action_remaining_budget_policy_id"],
            action_remaining_budget_policy_version=value["action_remaining_budget_policy_version"],
            action_remaining_budget_policy_fingerprint=value["action_remaining_budget_policy_fingerprint"],
        )


def derive_group_compensation_action_id(
    *, group_id: str, group_fingerprint: str, source_leg_id: str, source_event_digest: str, ordinal: int
) -> str:
    """Derive one immutable action ID from accepted group and source evidence."""

    facts = {
        "group_id": _reference(group_id, "group_id"),
        "group_fingerprint": _digest(group_fingerprint, "group fingerprint"),
        "source_leg_id": _reference(source_leg_id, "source leg id"),
        "source_event_digest": _digest(source_event_digest, "source event digest"),
        "ordinal": ordinal,
    }
    if type(ordinal) is not int or not 1 <= ordinal <= 1_000_000:
        raise ValueError("invalid compensation ordinal")
    return "gca-" + payload_sha256(facts)[:48]


def derive_group_compensation_intent_id(action_id: str) -> str:
    return "gclose-" + _reference(action_id, "action id")


def derive_group_compensation_client_order_id(action_id: str) -> str:
    """Derive the singleton CLOSE client ID from its durable action ID."""

    checked = _reference(action_id, "action id")
    return "gco-" + payload_sha256(
        {"schema": _CLIENT_ORDER_ID_SCHEMA, "action_id": checked}
    )[:48]


def group_compensation_budget_policy_fingerprint(
    *, policy_id: str, policy_version: int, cap_amount: Decimal, currency: str, metric: str
) -> str:
    """Digest the trusted explicit cap and its fixed reference-notional metric."""

    checked_id = _reference(policy_id, "compensation budget policy id")
    checked_currency = _reference(currency, "compensation budget currency")
    if type(policy_version) is not int or policy_version <= 0:
        raise ValueError("invalid compensation budget policy version")
    checked_cap = _decimal(cap_amount, "compensation budget cap", positive=True)
    if metric != LIMIT_CLOSE_NOTIONAL_V1:
        raise ValueError("unsupported compensation budget metric")
    return payload_sha256(
        {
            "schema": _BUDGET_POLICY_SCHEMA,
            "policy_id": checked_id,
            "policy_version": policy_version,
            "cap_amount": _decimal_text(checked_cap),
            "currency": checked_currency,
            "metric": metric,
        }
    )


@dataclass(frozen=True, slots=True)
class GroupCompensationCloseAuthorization:
    """Risk-owner signed-by-reconstruction request for one singleton CLOSE."""

    action_id: str
    ordinal: int
    group_id: str
    origin_run_id: str
    group_fingerprint: str
    execution_scope_key: str
    account_ref: str
    strategy_id: str
    source_leg_id: str
    source_intent_id: str
    source_request_fingerprint: str
    source_terminal_status: GroupChildOrderStatus
    source_requested_quantity: Decimal
    source_claim_digest: str
    source_risk_resolution_digest: str
    source_event_digest: str
    source_side: Side
    instrument: str
    metadata_version: str
    source_price: Decimal
    source_base_units_per_order_unit: Decimal
    source_quantity_unit: str
    source_quote_currency: str
    closing_side: Side
    close_quantity: Decimal
    close_quantity_unit: str
    close_reference_notional: Decimal
    source_uncompensated_position_base_units: Decimal
    source_chain_deficit_vector_base_units: tuple[tuple[str, Decimal], ...]
    source_filled_position_exposure_vector_base_units: tuple[tuple[str, Decimal], ...]
    prior_resolved_actions: tuple[GroupCompensationResolvedActionRef, ...]
    compensation_budget_cap_amount: Decimal
    action_remaining_budget_amount: Decimal
    action_remaining_budget_currency: str
    action_remaining_budget_metric: str
    action_remaining_budget_policy_id: str
    action_remaining_budget_policy_version: int
    action_remaining_budget_policy_fingerprint: str
    close_position_version: str
    close_allocation_available_quantity: Decimal
    close_allocation_quantity_unit: str
    intent_id: str
    risk_intent_id: str
    client_order_id: str
    source_facts_digest: str
    authorization_digest: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "action_id", _reference(self.action_id, "action_id"))
        object.__setattr__(self, "intent_id", _reference(self.intent_id, "intent_id"))
        object.__setattr__(self, "risk_intent_id", _risk_intent_id(self.risk_intent_id))
        object.__setattr__(self, "client_order_id", _reference(self.client_order_id, "client_order_id"))
        if (
            self.intent_id != derive_group_compensation_intent_id(self.action_id)
            or self.client_order_id != derive_group_compensation_client_order_id(self.action_id)
        ):
            raise ValueError("compensation order IDs are not derived from its action ID")
        if type(self.ordinal) is not int or not 1 <= self.ordinal <= 1_000_000:
            raise ValueError("invalid compensation ordinal")
        for name in (
            "group_id", "origin_run_id", "account_ref", "strategy_id", "source_leg_id",
            "source_intent_id", "instrument", "metadata_version", "source_quantity_unit",
            "source_quote_currency", "close_quantity_unit", "action_remaining_budget_currency",
            "action_remaining_budget_metric", "action_remaining_budget_policy_id",
            "close_position_version", "close_allocation_quantity_unit",
        ):
            object.__setattr__(self, name, _reference(getattr(self, name), name))
        for name in (
            "group_fingerprint", "source_request_fingerprint", "source_claim_digest",
            "source_risk_resolution_digest", "source_event_digest",
            "action_remaining_budget_policy_fingerprint", "source_facts_digest",
        ):
            _digest(getattr(self, name), name)
        if type(self.execution_scope_key) is not str or not self.execution_scope_key:
            raise ValueError("invalid execution scope key")
        if type(self.source_terminal_status) is not GroupChildOrderStatus:
            raise ValueError("invalid source terminal status")
        if (
            self.source_terminal_status.intent_id != self.source_intent_id
            or self.source_terminal_status.state not in {
                ExecutionState.FILLED, ExecutionState.CANCELLED, ExecutionState.REJECTED
            }
            or self.source_terminal_status.filled_quantity <= 0
            or _fraction(
                _decimal(self.source_requested_quantity, "source requested quantity", positive=True),
                "source requested quantity",
            ) <= 0
            or _fraction(
                _decimal(self.source_terminal_status.filled_quantity, "source filled quantity"),
                "source filled quantity",
            ) > _fraction(self.source_requested_quantity, "source requested quantity")
        ):
            raise ValueError("source child is not terminal with fills within its request")
        for name, enum_type in (("source_side", Side), ("closing_side", Side)):
            try:
                object.__setattr__(self, name, enum_type(getattr(self, name)))
            except (TypeError, ValueError) as error:
                raise ValueError("invalid compensation side") from error
        if self.closing_side is self.source_side:
            raise ValueError("compensation side must reverse the source leg")
        for name in (
            "source_price",
            "source_base_units_per_order_unit",
            "close_quantity",
            "close_reference_notional",
            "compensation_budget_cap_amount",
            "action_remaining_budget_amount",
            "close_allocation_available_quantity",
        ):
            object.__setattr__(self, name, _decimal(getattr(self, name), name, positive=True))
        uncompensated = _decimal(
            self.source_uncompensated_position_base_units,
            "source uncompensated position",
        )
        object.__setattr__(
            self, "source_uncompensated_position_base_units", uncompensated
        )
        chain_deficit = _vector(self.source_chain_deficit_vector_base_units)
        filled_exposure = _vector(self.source_filled_position_exposure_vector_base_units)
        prior_actions = _validate_prior_resolved_actions(
            self.prior_resolved_actions,
            leg_ids=tuple(leg_id for leg_id, _ in filled_exposure),
            selected_leg_id=self.source_leg_id,
            selected_side=self.source_side,
            selected_instrument=self.instrument,
            selected_metadata_version=self.metadata_version,
            selected_base_units_per_order_unit=self.source_base_units_per_order_unit,
            quote_currency=self.source_quote_currency,
            budget_policy_fingerprint=self.action_remaining_budget_policy_fingerprint,
        )
        object.__setattr__(self, "prior_resolved_actions", prior_actions)
        object.__setattr__(self, "source_chain_deficit_vector_base_units", chain_deficit)
        object.__setattr__(self, "source_filled_position_exposure_vector_base_units", filled_exposure)
        coordinates = dict(filled_exposure)
        selected_source_sign = 1 if self.source_side is Side.BUY else -1
        selected_close_delta = sum(
            (
                _fraction(item.close_quantity, "prior close quantity")
                * _fraction(item.source_base_units_per_order_unit, "prior close conversion")
                * (1 if item.closing_side is Side.BUY else -1)
                for item in self.prior_resolved_actions
                if item.source_leg_id == self.source_leg_id
            ),
            Fraction(0),
        )
        selected_source_position = (
            selected_source_sign
            * _fraction(self.source_terminal_status.filled_quantity, "source filled quantity")
            * _fraction(self.source_base_units_per_order_unit, "source base-unit conversion")
            + selected_close_delta
        )
        if (
            tuple(leg_id for leg_id, _ in chain_deficit)
            != tuple(leg_id for leg_id, _ in filled_exposure)
            or self.source_leg_id not in coordinates
            or _fraction(coordinates[self.source_leg_id], "selected exposure")
            != _fraction(uncompensated, "selected uncompensated exposure")
            or _fraction(coordinates[self.source_leg_id], "selected exposure") != selected_source_position
            or _fraction(uncompensated, "selected uncompensated exposure") == 0
            or (self.source_side is Side.BUY and _fraction(uncompensated, "selected uncompensated exposure") < 0)
            or (self.source_side is Side.SELL and _fraction(uncompensated, "selected uncompensated exposure") > 0)
            or self.action_id
            != derive_group_compensation_action_id(
                group_id=self.group_id,
                group_fingerprint=self.group_fingerprint,
                source_leg_id=self.source_leg_id,
                source_event_digest=self.source_event_digest,
                ordinal=self.ordinal,
            )
            or _fraction(self.close_quantity, "close quantity")
            * _fraction(self.source_base_units_per_order_unit, "source base-unit conversion")
            > abs(_fraction(uncompensated, "selected uncompensated exposure"))
            or _fraction(self.close_quantity, "close quantity")
            > _fraction(self.close_allocation_available_quantity, "close allocation available quantity")
            or self.close_quantity_unit != self.source_quantity_unit
            or self.close_quantity_unit != self.close_allocation_quantity_unit
            or self.action_remaining_budget_currency != self.source_quote_currency
            or self.action_remaining_budget_metric != LIMIT_CLOSE_NOTIONAL_V1
            or self.action_remaining_budget_amount > self.compensation_budget_cap_amount
            or _fraction(self.action_remaining_budget_amount, "remaining compensation budget")
            != _fraction(self.compensation_budget_cap_amount, "compensation budget cap")
            - sum(
                (_fraction(item.close_reference_notional, "prior close notional") for item in prior_actions),
                Fraction(0),
            )
            or _fraction(self.close_reference_notional, "close reference notional")
            != _fraction(self.close_quantity, "close quantity")
            * _fraction(self.source_price, "source price")
            * _fraction(self.source_base_units_per_order_unit, "source base-unit conversion")
            or _fraction(self.close_reference_notional, "close reference notional")
            > _fraction(self.action_remaining_budget_amount, "remaining compensation budget")
            or self.action_remaining_budget_policy_fingerprint
            != group_compensation_budget_policy_fingerprint(
                policy_id=self.action_remaining_budget_policy_id,
                policy_version=self.action_remaining_budget_policy_version,
                cap_amount=self.compensation_budget_cap_amount,
                currency=self.source_quote_currency,
                metric=self.action_remaining_budget_metric,
            )
        ):
            raise ValueError("compensation quantity or reference notional exceeds authority")
        if (
            type(self.action_remaining_budget_policy_version) is not int
            or self.action_remaining_budget_policy_version <= 0
        ):
            raise ValueError("invalid compensation budget policy version")
        if self.authorization_digest:
            _digest(self.authorization_digest, "authorization digest")
            if self.authorization_digest != payload_sha256(self._payload_without_digest()):
                raise ValueError("compensation authorization digest mismatch")
        else:
            object.__setattr__(self, "authorization_digest", payload_sha256(self._payload_without_digest()))

    def _payload_without_digest(self) -> dict[str, Any]:
        return {
            "schema": _AUTHORIZATION_SCHEMA,
            "action_id": self.action_id,
            "ordinal": self.ordinal,
            "group_id": self.group_id,
            "origin_run_id": self.origin_run_id,
            "group_fingerprint": self.group_fingerprint,
            "execution_scope_key": self.execution_scope_key,
            "account_ref": self.account_ref,
            "strategy_id": self.strategy_id,
            "source_leg_id": self.source_leg_id,
            "source_intent_id": self.source_intent_id,
            "source_request_fingerprint": self.source_request_fingerprint,
            "source_terminal_status": self.source_terminal_status.to_payload(),
            "source_requested_quantity": _decimal_text(self.source_requested_quantity),
            "source_claim_digest": self.source_claim_digest,
            "source_risk_resolution_digest": self.source_risk_resolution_digest,
            "source_event_digest": self.source_event_digest,
            "source_side": self.source_side.value,
            "instrument": self.instrument,
            "metadata_version": self.metadata_version,
            "source_price": _decimal_text(self.source_price),
            "source_base_units_per_order_unit": _decimal_text(self.source_base_units_per_order_unit),
            "source_quantity_unit": self.source_quantity_unit,
            "source_quote_currency": self.source_quote_currency,
            "closing_side": self.closing_side.value,
            "close_quantity": _decimal_text(self.close_quantity),
            "close_quantity_unit": self.close_quantity_unit,
            "close_reference_notional": _decimal_text(self.close_reference_notional),
            "source_uncompensated_position_base_units": _decimal_text(
                self.source_uncompensated_position_base_units
            ),
            "source_chain_deficit_vector_base_units": _vector_payload(
                self.source_chain_deficit_vector_base_units
            ),
            "source_filled_position_exposure_vector_base_units": _vector_payload(
                self.source_filled_position_exposure_vector_base_units
            ),
            "prior_resolved_actions": [item.to_payload() for item in self.prior_resolved_actions],
            "compensation_budget_cap_amount": _decimal_text(self.compensation_budget_cap_amount),
            "action_remaining_budget_amount": _decimal_text(self.action_remaining_budget_amount),
            "action_remaining_budget_currency": self.action_remaining_budget_currency,
            "action_remaining_budget_metric": self.action_remaining_budget_metric,
            "action_remaining_budget_policy_id": self.action_remaining_budget_policy_id,
            "action_remaining_budget_policy_version": self.action_remaining_budget_policy_version,
            "action_remaining_budget_policy_fingerprint": self.action_remaining_budget_policy_fingerprint,
            "close_position_version": self.close_position_version,
            "close_allocation_available_quantity": _decimal_text(self.close_allocation_available_quantity),
            "close_allocation_quantity_unit": self.close_allocation_quantity_unit,
            "intent_id": self.intent_id,
            "risk_intent_id": self.risk_intent_id,
            "client_order_id": self.client_order_id,
            "source_facts_digest": self.source_facts_digest,
        }

    def to_payload(self) -> dict[str, Any]:
        payload = self._payload_without_digest()
        payload["authorization_digest"] = self.authorization_digest
        return payload

    @property
    def digest(self) -> str:
        return self.authorization_digest

    @classmethod
    def from_payload(cls, value: object) -> GroupCompensationCloseAuthorization:
        if not isinstance(value, Mapping):
            raise ValueError("invalid compensation authorization payload")
        expected = set(cls.__dataclass_fields__) | {"schema"}
        expected.remove("authorization_digest")
        expected.update({"authorization_digest", "schema"})
        if set(value) != expected or value["schema"] != _AUTHORIZATION_SCHEMA:
            raise ValueError("invalid compensation authorization payload fields")
        vectors: dict[str, tuple[tuple[str, Decimal], ...]] = {}
        for field in (
            "source_chain_deficit_vector_base_units",
            "source_filled_position_exposure_vector_base_units",
        ):
            vector_raw = value[field]
            if type(vector_raw) not in (list, tuple) or len(vector_raw) != 3:
                raise ValueError(f"invalid {field} payload")
            vector: list[tuple[str, Decimal]] = []
            for item in vector_raw:
                if type(item) not in (list, tuple) or len(item) != 2:
                    raise ValueError(f"invalid {field} coordinate payload")
                vector.append((_reference(item[0], "residual leg id"), _decimal_payload(item[1], "base amount")))
            vectors[field] = tuple(vector)
        raw_prior = value["prior_resolved_actions"]
        if type(raw_prior) not in (list, tuple) or len(raw_prior) > 9:
            raise ValueError("invalid or unbounded prior compensation receipts")
        prior_resolved_actions = tuple(
            GroupCompensationResolvedActionRef.from_payload(item) for item in raw_prior
        )
        return cls(
            action_id=value["action_id"],
            ordinal=value["ordinal"],
            group_id=value["group_id"],
            origin_run_id=value["origin_run_id"],
            group_fingerprint=value["group_fingerprint"],
            execution_scope_key=value["execution_scope_key"],
            account_ref=value["account_ref"],
            strategy_id=value["strategy_id"],
            source_leg_id=value["source_leg_id"],
            source_intent_id=value["source_intent_id"],
            source_request_fingerprint=value["source_request_fingerprint"],
            source_terminal_status=GroupChildOrderStatus.from_payload(value["source_terminal_status"]),
            source_requested_quantity=_decimal_payload(value["source_requested_quantity"], "source requested quantity", positive=True),
            source_claim_digest=value["source_claim_digest"],
            source_risk_resolution_digest=value["source_risk_resolution_digest"],
            source_event_digest=value["source_event_digest"],
            source_side=value["source_side"],
            instrument=value["instrument"],
            metadata_version=value["metadata_version"],
            source_price=_decimal_payload(value["source_price"], "source price", positive=True),
            source_base_units_per_order_unit=_decimal_payload(value["source_base_units_per_order_unit"], "source base-unit ratio", positive=True),
            source_quantity_unit=value["source_quantity_unit"],
            source_quote_currency=value["source_quote_currency"],
            closing_side=value["closing_side"],
            close_quantity=_decimal_payload(value["close_quantity"], "close quantity", positive=True),
            close_quantity_unit=value["close_quantity_unit"],
            close_reference_notional=_decimal_payload(value["close_reference_notional"], "close reference notional", positive=True),
            source_uncompensated_position_base_units=_decimal_payload(
                value["source_uncompensated_position_base_units"], "source uncompensated position"
            ),
            source_chain_deficit_vector_base_units=vectors["source_chain_deficit_vector_base_units"],
            source_filled_position_exposure_vector_base_units=(
                vectors["source_filled_position_exposure_vector_base_units"]
            ),
            prior_resolved_actions=prior_resolved_actions,
            compensation_budget_cap_amount=_decimal_payload(value["compensation_budget_cap_amount"], "compensation budget cap", positive=True),
            action_remaining_budget_amount=_decimal_payload(value["action_remaining_budget_amount"], "remaining budget", positive=True),
            action_remaining_budget_currency=value["action_remaining_budget_currency"],
            action_remaining_budget_metric=value["action_remaining_budget_metric"],
            action_remaining_budget_policy_id=value["action_remaining_budget_policy_id"],
            action_remaining_budget_policy_version=value["action_remaining_budget_policy_version"],
            action_remaining_budget_policy_fingerprint=value["action_remaining_budget_policy_fingerprint"],
            close_position_version=value["close_position_version"],
            close_allocation_available_quantity=_decimal_payload(value["close_allocation_available_quantity"], "close allocation available", positive=True),
            close_allocation_quantity_unit=value["close_allocation_quantity_unit"],
            intent_id=value["intent_id"],
            risk_intent_id=value["risk_intent_id"],
            client_order_id=value["client_order_id"],
            source_facts_digest=value["source_facts_digest"],
            authorization_digest=value["authorization_digest"],
        )


@dataclass(frozen=True, slots=True)
class GroupCompensationSourceLegFacts:
    """Accepted request and terminal source facts for one ordered group leg."""

    leg_id: str
    source_request_fingerprint: str
    source_intent_id: str | None
    source_terminal_status: GroupChildOrderStatus | None
    source_claim_digest: str | None
    source_risk_resolution_digest: str | None
    source_event_digest: str | None
    side: Side
    requested_quantity: Decimal
    filled_quantity: Decimal
    instrument: str
    metadata_version: str
    source_price: Decimal | None
    base_units_per_order_unit: Decimal
    quantity_unit: str
    quote_currency: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "leg_id", _reference(self.leg_id, "source leg id"))
        object.__setattr__(self, "source_request_fingerprint", _digest(
            self.source_request_fingerprint, "source request fingerprint"
        ))
        try:
            object.__setattr__(self, "side", Side(self.side))
        except (TypeError, ValueError) as error:
            raise ValueError("invalid source leg side") from error
        for name in ("instrument", "metadata_version", "quantity_unit", "quote_currency"):
            object.__setattr__(self, name, _reference(getattr(self, name), name))
        requested = _decimal(self.requested_quantity, "source requested quantity")
        filled = _decimal(self.filled_quantity, "source filled quantity")
        conversion = _decimal(self.base_units_per_order_unit, "source base-unit conversion", positive=True)
        if filled < 0 or requested < 0 or _fraction(filled, "source filled quantity") > _fraction(
            requested, "source requested quantity"
        ):
            raise ValueError("source fills exceed the accepted request")
        if self.source_price is not None:
            object.__setattr__(self, "source_price", _decimal(self.source_price, "source limit price", positive=True))
        for name in ("source_claim_digest", "source_risk_resolution_digest", "source_event_digest"):
            value = getattr(self, name)
            if value is not None:
                _digest(value, name)
        if self.source_intent_id is None:
            if (
                self.source_terminal_status is not None
                or self.source_claim_digest is not None
                or self.source_risk_resolution_digest is not None
                or self.source_event_digest is not None
                or self.source_price is not None
                or requested != 0
                or filled != 0
            ):
                raise ValueError("unsubmitted source leg must have exact empty evidence")
            return
        object.__setattr__(self, "source_intent_id", _reference(self.source_intent_id, "source intent id"))
        status = self.source_terminal_status
        if (
            type(status) is not GroupChildOrderStatus
            or status.intent_id != self.source_intent_id
            or status.state not in {ExecutionState.FILLED, ExecutionState.CANCELLED, ExecutionState.REJECTED}
            or status.filled_quantity != filled
            or requested <= 0
            or self.source_claim_digest is None
            or self.source_risk_resolution_digest is None
            or (filled > 0 and self.source_event_digest is None)
            or (filled > 0 and self.source_price is None)
        ):
            raise ValueError("submitted source leg lacks exact terminal proof")
        # Keep the conversion in the canonical DTO even for zero-fill legs;
        # it is used to independently recompute the signed position vector.
        object.__setattr__(self, "base_units_per_order_unit", conversion)

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": _SOURCE_LEG_FACTS_SCHEMA,
            "leg_id": self.leg_id,
            "source_request_fingerprint": self.source_request_fingerprint,
            "source_intent_id": self.source_intent_id,
            "source_terminal_status": None if self.source_terminal_status is None else self.source_terminal_status.to_payload(),
            "source_claim_digest": self.source_claim_digest,
            "source_risk_resolution_digest": self.source_risk_resolution_digest,
            "source_event_digest": self.source_event_digest,
            "side": self.side.value,
            "requested_quantity": _decimal_text(self.requested_quantity),
            "filled_quantity": _decimal_text(self.filled_quantity),
            "instrument": self.instrument,
            "metadata_version": self.metadata_version,
            "source_price": None if self.source_price is None else _decimal_text(self.source_price),
            "base_units_per_order_unit": _decimal_text(self.base_units_per_order_unit),
            "quantity_unit": self.quantity_unit,
            "quote_currency": self.quote_currency,
        }

    @classmethod
    def from_payload(cls, value: object) -> GroupCompensationSourceLegFacts:
        expected = {
            "schema", "leg_id", "source_request_fingerprint", "source_intent_id", "source_terminal_status",
            "source_claim_digest", "source_risk_resolution_digest", "source_event_digest", "side",
            "requested_quantity", "filled_quantity", "instrument", "metadata_version", "source_price",
            "base_units_per_order_unit", "quantity_unit", "quote_currency",
        }
        if not isinstance(value, Mapping) or set(value) != expected or value["schema"] != _SOURCE_LEG_FACTS_SCHEMA:
            raise ValueError("invalid compensation source leg facts payload")
        raw_status = value["source_terminal_status"]
        if raw_status is not None and not isinstance(raw_status, Mapping):
            raise ValueError("invalid compensation source terminal status payload")
        return cls(
            leg_id=value["leg_id"],
            source_request_fingerprint=value["source_request_fingerprint"],
            source_intent_id=value["source_intent_id"],
            source_terminal_status=None if raw_status is None else GroupChildOrderStatus.from_payload(raw_status),
            source_claim_digest=value["source_claim_digest"],
            source_risk_resolution_digest=value["source_risk_resolution_digest"],
            source_event_digest=value["source_event_digest"],
            side=value["side"],
            requested_quantity=_decimal_payload(value["requested_quantity"], "source requested quantity"),
            filled_quantity=_decimal_payload(value["filled_quantity"], "source filled quantity"),
            instrument=value["instrument"],
            metadata_version=value["metadata_version"],
            source_price=None if value["source_price"] is None else _decimal_payload(value["source_price"], "source price", positive=True),
            base_units_per_order_unit=_decimal_payload(value["base_units_per_order_unit"], "source base-unit conversion", positive=True),
            quantity_unit=value["quantity_unit"],
            quote_currency=value["quote_currency"],
        )


@dataclass(frozen=True, slots=True)
class GroupCompensationClosureFacts:
    """Canonical source projection for Risk-verified terminal group closure.

    This DTO is only a cross-check.  The Risk bridge must reread every CLOSE
    command and typed terminal receipt in its own durable store before it
    accepts this projection or returns a canonical closure receipt.
    """

    group_id: str
    origin_run_id: str
    group_fingerprint: str
    execution_scope_key: str
    account_ref: str
    strategy_id: str
    group_state: str
    group_revision: int
    source_legs: tuple[GroupCompensationSourceLegFacts, ...]
    chain_deficit_vector_base_units: tuple[tuple[str, Decimal], ...]
    signed_uncompensated_filled_exposure_vector_base_units: tuple[tuple[str, Decimal], ...]
    resolved_actions: tuple[GroupCompensationResolvedActionRef, ...]
    budget_policy_id: str
    budget_policy_version: int
    budget_policy_fingerprint: str
    budget_metric: str
    budget_currency: str
    budget_cap_amount: Decimal
    budget_consumed_amount: Decimal
    budget_remaining_amount: Decimal
    pending_budget_obligation_amount: Decimal
    pending_action_count: int
    unknown_order_count: int
    unresolved_group_obligation_count: int
    active_dispatch_latch_count: int
    account_reservation_phase: str
    account_reservation_sha256: str
    group_budget_reservation_state: str
    group_budget_reservation_sha256: str
    group_event_chain_sha256: str

    def __post_init__(self) -> None:
        for name in ("group_id", "origin_run_id", "account_ref", "strategy_id", "budget_policy_id", "budget_metric", "budget_currency"):
            object.__setattr__(self, name, _reference(getattr(self, name), name))
        for name in ("group_fingerprint", "budget_policy_fingerprint", "account_reservation_sha256", "group_budget_reservation_sha256", "group_event_chain_sha256"):
            _digest(getattr(self, name), name)
        if type(self.execution_scope_key) is not str or not self.execution_scope_key:
            raise ValueError("invalid compensation closure execution scope")
        if type(self.group_state) is not str or self.group_state not in {
            "UNWINDING",
            "RECOVERY_REQUIRED",
        }:
            raise ValueError("compensation closure requires a recoverable group state")
        if type(self.group_revision) is not int or self.group_revision <= 0:
            raise ValueError("invalid compensation closure revision")
        if type(self.source_legs) is not tuple or len(self.source_legs) != 3 or any(
            type(item) is not GroupCompensationSourceLegFacts for item in self.source_legs
        ):
            raise ValueError("exactly three ordered source legs are required")
        leg_ids = tuple(item.leg_id for item in self.source_legs)
        if len(set(leg_ids)) != 3:
            raise ValueError("duplicate compensation source leg")
        chain = _vector(self.chain_deficit_vector_base_units)
        exposure = _vector(self.signed_uncompensated_filled_exposure_vector_base_units)
        if tuple(item[0] for item in chain) != leg_ids or tuple(item[0] for item in exposure) != leg_ids:
            raise ValueError("compensation vectors must follow accepted leg order")
        if any(amount != 0 for _, amount in exposure):
            raise ValueError("compensation closure requires zero signed filled exposure")
        if type(self.resolved_actions) is not tuple or not 1 <= len(self.resolved_actions) <= 9 or any(
            type(item) is not GroupCompensationResolvedActionRef for item in self.resolved_actions
        ) or len({item.action_id for item in self.resolved_actions}) != len(self.resolved_actions):
            raise ValueError("invalid or unbounded resolved compensation actions")
        by_leg = {item.leg_id: item for item in self.source_legs}
        net = {leg_id: Fraction(0) for leg_id in leg_ids}
        for leg in self.source_legs:
            if leg.source_terminal_status is not None:
                sign = 1 if leg.side is Side.BUY else -1
                net[leg.leg_id] += sign * _fraction(leg.filled_quantity, "source fill") * _fraction(
                    leg.base_units_per_order_unit, "source base-unit conversion"
                )
        for action in self.resolved_actions:
            leg = by_leg.get(action.source_leg_id)
            if (
                leg is None
                or leg.source_terminal_status is None
                or leg.filled_quantity <= 0
                or action.instrument != leg.instrument
                or action.metadata_version != leg.metadata_version
                or action.close_quantity_unit != leg.quantity_unit
                or action.source_base_units_per_order_unit != leg.base_units_per_order_unit
                or action.quote_currency != leg.quote_currency
                or action.closing_side is leg.side
                or _fraction(action.close_reference_notional, "close reference notional")
                != _fraction(action.close_quantity, "close quantity")
                * _fraction(action.close_limit_price, "close limit price")
                * _fraction(action.source_base_units_per_order_unit, "close conversion")
            ):
                raise ValueError("resolved CLOSE action does not match its accepted source leg")
            close_sign = 1 if action.closing_side is Side.BUY else -1
            net[action.source_leg_id] += close_sign * _fraction(action.close_quantity, "close quantity") * _fraction(
                action.source_base_units_per_order_unit, "close conversion"
            )
        if tuple((leg_id, _fraction_to_decimal_contract(value)) for leg_id, value in net.items()) != exposure:
            raise ValueError("signed exposure does not match source fills and CLOSE receipts")
        try:
            policy_version = self.budget_policy_version
            object.__setattr__(self, "budget_metric", _reference(self.budget_metric, "budget metric"))
        except (TypeError, ValueError) as error:
            raise ValueError("invalid compensation budget facts") from error
        if type(policy_version) is not int or policy_version <= 0:
            raise ValueError("invalid compensation budget policy version")
        cap = _fraction(_decimal(self.budget_cap_amount, "budget cap", positive=True), "budget cap")
        consumed = _fraction(_decimal(self.budget_consumed_amount, "budget consumed"), "budget consumed")
        remaining = _fraction(_decimal(self.budget_remaining_amount, "budget remaining"), "budget remaining")
        pending = _fraction(_decimal(self.pending_budget_obligation_amount, "pending budget obligation"), "pending budget obligation")
        if (
            consumed < 0
            or remaining < 0
            or pending != 0
            or consumed != sum((_fraction(item.close_reference_notional, "close notional") for item in self.resolved_actions), Fraction(0))
            or consumed + remaining != cap
            or self.budget_metric != LIMIT_CLOSE_NOTIONAL_V1
            or self.budget_currency != self.resolved_actions[0].quote_currency
            or any(
                leg.filled_quantity > 0 and leg.quote_currency != self.budget_currency
                for leg in self.source_legs
            )
            or any(item.quote_currency != self.budget_currency for item in self.resolved_actions)
            or self.budget_policy_fingerprint != group_compensation_budget_policy_fingerprint(
                policy_id=self.budget_policy_id,
                policy_version=policy_version,
                cap_amount=self.budget_cap_amount,
                currency=self.budget_currency,
                metric=self.budget_metric,
            )
        ):
            raise ValueError("compensation budget closure facts do not reconcile")
        for name in ("pending_action_count", "unknown_order_count", "unresolved_group_obligation_count", "active_dispatch_latch_count"):
            value = getattr(self, name)
            if type(value) is not int or value < 0 or value != 0:
                raise ValueError(f"{name} must be exactly zero")
        if self.account_reservation_phase != "RELEASED" or self.group_budget_reservation_state != "HELD":
            raise ValueError("compensation closure reservation phases are not settled")
        object.__setattr__(self, "chain_deficit_vector_base_units", chain)
        object.__setattr__(self, "signed_uncompensated_filled_exposure_vector_base_units", exposure)

    def _payload_without_digest(self) -> dict[str, Any]:
        return {
            "schema": _CLOSURE_FACTS_SCHEMA,
            "group_id": self.group_id,
            "origin_run_id": self.origin_run_id,
            "group_fingerprint": self.group_fingerprint,
            "execution_scope_key": self.execution_scope_key,
            "account_ref": self.account_ref,
            "strategy_id": self.strategy_id,
            "group_state": self.group_state,
            "group_revision": self.group_revision,
            "source_legs": [item.to_payload() for item in self.source_legs],
            "chain_deficit_vector_base_units": _vector_payload(self.chain_deficit_vector_base_units),
            "signed_uncompensated_filled_exposure_vector_base_units": _vector_payload(self.signed_uncompensated_filled_exposure_vector_base_units),
            "resolved_actions": [item.to_payload() for item in self.resolved_actions],
            "budget_policy_id": self.budget_policy_id,
            "budget_policy_version": self.budget_policy_version,
            "budget_policy_fingerprint": self.budget_policy_fingerprint,
            "budget_metric": self.budget_metric,
            "budget_currency": self.budget_currency,
            "budget_cap_amount": _decimal_text(self.budget_cap_amount),
            "budget_consumed_amount": _decimal_text(self.budget_consumed_amount),
            "budget_remaining_amount": _decimal_text(self.budget_remaining_amount),
            "pending_budget_obligation_amount": _decimal_text(self.pending_budget_obligation_amount),
            "pending_action_count": self.pending_action_count,
            "unknown_order_count": self.unknown_order_count,
            "unresolved_group_obligation_count": self.unresolved_group_obligation_count,
            "active_dispatch_latch_count": self.active_dispatch_latch_count,
            "account_reservation_phase": self.account_reservation_phase,
            "account_reservation_sha256": self.account_reservation_sha256,
            "group_budget_reservation_state": self.group_budget_reservation_state,
            "group_budget_reservation_sha256": self.group_budget_reservation_sha256,
            "group_event_chain_sha256": self.group_event_chain_sha256,
        }

    @property
    def digest(self) -> str:
        return payload_sha256(self._payload_without_digest())

    def to_payload(self) -> dict[str, Any]:
        payload = self._payload_without_digest()
        payload["closure_digest"] = self.digest
        return payload

    @classmethod
    def from_payload(cls, value: object) -> GroupCompensationClosureFacts:
        if not isinstance(value, Mapping) or set(value) != set(cls.__dataclass_fields__) | {"schema", "closure_digest"}:
            raise ValueError("invalid compensation closure facts payload")
        if value["schema"] != _CLOSURE_FACTS_SCHEMA:
            raise ValueError("invalid compensation closure facts schema")
        raw_legs = value["source_legs"]
        raw_actions = value["resolved_actions"]
        if type(raw_legs) is not list or len(raw_legs) != 3 or type(raw_actions) is not list or len(raw_actions) > 9:
            raise ValueError("invalid compensation closure leg/action lists")
        kwargs = {name: value[name] for name in cls.__dataclass_fields__}
        kwargs["source_legs"] = tuple(GroupCompensationSourceLegFacts.from_payload(item) for item in raw_legs)
        for field in ("chain_deficit_vector_base_units", "signed_uncompensated_filled_exposure_vector_base_units"):
            raw = value[field]
            if type(raw) is not list or len(raw) != 3 or any(type(item) is not list or len(item) != 2 for item in raw):
                raise ValueError("invalid compensation closure vector")
            kwargs[field] = tuple((_reference(item[0], "closure leg id"), _decimal_payload(item[1], "closure vector amount")) for item in raw)
        kwargs["resolved_actions"] = tuple(GroupCompensationResolvedActionRef.from_payload(item) for item in raw_actions)
        for name in ("budget_cap_amount", "budget_consumed_amount", "budget_remaining_amount", "pending_budget_obligation_amount"):
            kwargs[name] = _decimal_payload(value[name], name.replace("_", " "))
        result = cls(**kwargs)
        if value["closure_digest"] != result.digest:
            raise ValueError("compensation closure facts digest mismatch")
        return result


class GroupCompensationAuthorizationSource(Protocol):
    """Required typed, read-only provenance port injected into Risk owner builds.

    Its concrete implementation must retain and identity-check the exact
    accepted-group binding store, origin-run binding store, group store,
    execution store, and ``GroupProofSource`` used by the owner composition.
    It must not expose generic callbacks or close borrowed resources.
    """

    @property
    def ready(self) -> bool:
        """Return exact bool based on the retained backing-store composition."""

    def resolve_action(
        self,
        registration: ActiveRegistrationContext,
        action_ref: GroupCompensationActionRef,
        *,
        operation: str,
    ) -> GroupCompensationSourceFacts:
        """Reconstruct source facts for the next or already durable action."""

    def assert_authorization(
        self,
        registration: ActiveRegistrationContext,
        authorization: GroupCompensationCloseAuthorization,
        *,
        operation: str,
    ) -> GroupCompensationSourceFacts:
        """Reconstruct the exact action facts and durable phase for this auth."""

    def resolve_close(
        self,
        registration: ActiveRegistrationContext,
        group_id: str,
        *,
        operation: str,
    ) -> GroupCompensationClosureFacts:
        """Rebuild all durable group and action facts before terminal closure."""


def group_compensation_authorization_digest(value: Mapping[str, Any]) -> str:
    """Compute the canonical digest of one full auth payload, verifying schema."""

    authorization = GroupCompensationCloseAuthorization.from_payload(value)
    return authorization.digest


__all__ = [
    "GroupCompensationActionPhase",
    "GroupCompensationActionRef",
    "GroupCompensationAuthorizationSource",
    "GroupCompensationCloseAuthorization",
    "GroupCompensationClosureFacts",
    "GroupCompensationResolvedActionRef",
    "GroupCompensationSourceLegFacts",
    "GroupCompensationSourceFacts",
    "LIMIT_CLOSE_NOTIONAL_V1",
    "derive_group_compensation_action_id",
    "derive_group_compensation_client_order_id",
    "derive_group_compensation_intent_id",
    "group_compensation_budget_policy_fingerprint",
    "group_compensation_authorization_digest",
]
