"""Private, unconnected Iteration 65 route matching candidate.

These are local values, not the pending Base wire/configuration schema. Matching
neither authorizes a strategy nor submits an order. Facts supplied here are
untrusted; callers must not treat MATCH as admission to Execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from bt_api_base.market_protocol import MarketInstrumentKey

from bt_api_py._contracts.models import OrderType

_SIDES = frozenset({'buy', 'sell'})
_OFFSETS = frozenset({'open', 'close', 'close_today', 'close_yesterday'})
_UNITS = frozenset({'base', 'contracts', 'lots', 'native'})
_PASS_V2_ORDER_TYPES = frozenset({'limit', 'market', 'stop', 'stop_limit', 'conditional'})


def _positive_decimal(value: object) -> bool:
    return type(value) is Decimal and value.is_finite() and value > 0


def _known_order_type(value: object, *, include_passthrough: bool = False) -> str | None:
    # Only the SDK's declared enum or its exact wire values are recognized.
    # Provider-native spellings and arbitrary strings are not proof of mismatch.
    if type(value) is OrderType:
        return value.value
    if type(value) is str:
        if include_passthrough and value in _PASS_V2_ORDER_TYPES:
            return value
        if value in {'limit', 'market'}:
            return value
    return None


@dataclass(frozen=True)
class _Rule:
    """One conjunction of exact conditions and an inclusive minimum quantity.

    ``None`` means no condition, not a wildcard over invalid order facts. The
    shape is intentionally private until the Base route schema is frozen.
    ``allowed_order_types`` is set only for PASS v2 rules (D62S-05 Scheme A2)
    which may route ``limit``, ``market``, ``stop``, ``stop_limit``, and
    ``conditional`` orders while keeping SLICE/TWAP/PASS-v1 strictly limit-only.
    """

    instrument: MarketInstrumentKey | None = None
    side: str | None = None
    offset: str | None = None
    order_type: str | None = None
    minimum_quantity: Decimal | None = None
    quantity_unit: str | None = None
    allowed_order_types: frozenset[str] | None = None

    def __post_init__(self) -> None:
        if self.instrument is not None and type(self.instrument) is not MarketInstrumentKey:
            raise ValueError('INVALID_ROUTE_RULE')
        if self.side is not None and (type(self.side) is not str or self.side not in _SIDES):
            raise ValueError('INVALID_ROUTE_RULE')
        if self.offset is not None and (type(self.offset) is not str or self.offset not in _OFFSETS):
            raise ValueError('INVALID_ROUTE_RULE')
        if self.allowed_order_types is not None:
            if (type(self.allowed_order_types) is not frozenset
                    or not self.allowed_order_types
                    or any(type(t) is not str or t not in _PASS_V2_ORDER_TYPES
                           for t in self.allowed_order_types)):
                raise ValueError('UNSUPPORTED_ROUTE_TYPE')
            if self.order_type is not None and self.order_type not in self.allowed_order_types:
                raise ValueError('UNSUPPORTED_ROUTE_TYPE')
        elif self.order_type is not None and (type(self.order_type) is not str or self.order_type != 'limit'):
            # An explicit non-limit type would route an unsupported parent on v1/SLICE/TWAP rules.
            raise ValueError('UNSUPPORTED_ROUTE_TYPE')
        if self.minimum_quantity is None:
            if self.quantity_unit is not None:
                raise ValueError('INVALID_ROUTE_RULE')
        elif (not _positive_decimal(self.minimum_quantity) or type(self.quantity_unit) is not str
              or self.quantity_unit not in _UNITS):
            raise ValueError('INVALID_ROUTE_RULE')


@dataclass(frozen=True)
class _OrderFacts:
    """Untrusted order claims; missing or invalid values remain unknown."""

    instrument: object = None
    side: object = None
    offset: object = None
    order_type: object = None
    quantity: object = None
    quantity_unit: object = None


class _Outcome(StrEnum):
    MATCH = 'MATCH'
    NO_MATCH = 'NO_MATCH'
    REJECT_UNSUPPORTED = 'REJECT_UNSUPPORTED'
    INDETERMINATE = 'INDETERMINATE'


@dataclass(frozen=True)
class _Decision:
    outcome: _Outcome
    # Zero-based priority for MATCH/rejection/unknown; no rule for NO_MATCH.
    rule_index: int | None = None


@dataclass(frozen=True, init=False)
class _RuleSet:
    """Immutable snapshot of an explicitly ordered list of rules."""

    rules: tuple[_Rule, ...]

    def __init__(self, rules: list[_Rule] | tuple[_Rule, ...]) -> None:
        if type(rules) not in (list, tuple) or any(type(rule) is not _Rule for rule in rules):
            raise ValueError('INVALID_ROUTE_RULES')
        object.__setattr__(self, 'rules', tuple(rules))

    def match(self, order: _OrderFacts) -> _Decision:
        if type(order) is not _OrderFacts:
            raise ValueError('INVALID_ORDER_FACTS')
        v1_order_type = _known_order_type(order.order_type, include_passthrough=False)
        for index, rule in enumerate(self.rules):
            is_pass_v2 = rule.allowed_order_types is not None
            order_type = (
                _known_order_type(order.order_type, include_passthrough=True)
                if is_pass_v2
                else v1_order_type
            )
            if is_pass_v2:
                order_type_match: bool | None = (
                    None if order_type is None else (order_type in rule.allowed_order_types)
                )
            else:
                order_type_match = _equals(rule.order_type, order_type, order_type is not None)
            # A known mismatch defeats unknowns in the same conjunction. An
            # otherwise possible earlier rule blocks later rules and direct.
            parts = (
                _equals(rule.instrument, order.instrument, type(order.instrument) is MarketInstrumentKey),
                _equals(rule.side, order.side, type(order.side) is str and order.side in _SIDES),
                _equals(rule.offset, order.offset, type(order.offset) is str and order.offset in _OFFSETS),
                order_type_match,
                _minimum(rule, order),
            )
            if False in parts:
                continue
            if None in parts:
                return _Decision(_Outcome.INDETERMINATE, index)
            # Execution's parent contract requires all of these facts
            # even when this particular rule does not filter on them.
            if (type(order.instrument) is not MarketInstrumentKey
                    or type(order.side) is not str or order.side not in _SIDES
                    or type(order.offset) is not str or order.offset not in _OFFSETS
                    or order_type is None
                    or not _positive_decimal(order.quantity)
                    or type(order.quantity_unit) is not str or order.quantity_unit not in _UNITS):
                return _Decision(_Outcome.INDETERMINATE, index)
            if not is_pass_v2 and order_type != 'limit':
                return _Decision(_Outcome.REJECT_UNSUPPORTED, index)
            return _Decision(_Outcome.MATCH, index)
        return _Decision(_Outcome.NO_MATCH)


def _equals(expected: object, actual: object, valid: bool) -> bool | None:
    if expected is None:
        return True
    if not valid:
        return None
    return expected == actual


def _minimum(rule: _Rule, order: _OrderFacts) -> bool | None:
    if rule.minimum_quantity is None:
        return True
    if (not _positive_decimal(order.quantity) or type(order.quantity_unit) is not str
            or order.quantity_unit not in _UNITS or order.quantity_unit != rule.quantity_unit):
        return None
    return order.quantity >= rule.minimum_quantity
