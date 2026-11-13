"""Pure matching only: no strategy authorization or transaction evidence."""
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal
from enum import StrEnum

import pytest
from bt_api_base.market_protocol import MarketInstrumentKey

from bt_api_py._contracts.models import OrderType
from bt_api_py._execution_route_match import _OrderFacts, _Outcome, _Rule, _RuleSet


@pytest.fixture
def instrument():
    return MarketInstrumentKey('ctp', 'SHFE', 'sim', 'future', 'rb', 'rb2501')


@pytest.fixture
def order(instrument):
    return _OrderFacts(instrument, 'buy', 'open', 'limit', Decimal('5'), 'lots')


def test_first_matching_rule_wins_and_order_is_frozen(order):
    supplied = [_Rule(side='sell'), _Rule(side='buy'), _Rule()]
    rules = _RuleSet(supplied)
    supplied[:] = [_Rule()]
    assert (decision := rules.match(order)).outcome is _Outcome.MATCH
    assert decision.rule_index == 1
    assert _RuleSet([_Rule(), _Rule(side='buy')]).match(order).rule_index == 0
    with pytest.raises(FrozenInstanceError):
        rules.rules[0].side = 'buy'
    with pytest.raises(FrozenInstanceError):
        order.side = 'sell'


@pytest.mark.parametrize('quantity,expected', [
    ('4.999', _Outcome.NO_MATCH), ('5', _Outcome.MATCH), ('5.001', _Outcome.MATCH),
])
def test_inclusive_threshold(order, quantity, expected):
    rules = _RuleSet([_Rule(minimum_quantity=Decimal('5'), quantity_unit='lots')])
    assert rules.match(replace(order, quantity=Decimal(quantity))).outcome is expected


@pytest.mark.parametrize('field,value', [
    ('provider', 'other'), ('venue', 'DCE'), ('environment', 'live'),
    ('product_type', 'option'), ('instrument_id', 'other'), ('native_symbol', 'rb2505'),
])
def test_instrument_identity_checks_every_dimension(order, instrument, field, value):
    different = replace(instrument, **{field: value})
    assert _RuleSet([_Rule(instrument=different)]).match(order).outcome is _Outcome.NO_MATCH
    assert _RuleSet([_Rule(instrument=instrument)]).match(order).outcome is _Outcome.MATCH


@pytest.mark.parametrize('condition', [
    {'side': 'sell'}, {'offset': 'close'}, {'offset': 'close_today'},
    {'offset': 'close_yesterday'},
])
def test_unmatched_dimensions(condition, order):
    assert _RuleSet([_Rule(**condition)]).match(order).outcome is _Outcome.NO_MATCH


def test_non_limit_dynamic_match_is_rejected_not_skipped(order):
    market = replace(order, order_type='market')
    rules = _RuleSet([_Rule(side='buy'), _Rule()])
    decision = rules.match(market)
    assert decision.outcome is _Outcome.REJECT_UNSUPPORTED
    assert decision.rule_index == 0
    assert _RuleSet([_Rule(side='sell')]).match(market).outcome is _Outcome.NO_MATCH
    assert _RuleSet([_Rule(order_type='limit')]).match(market).outcome is _Outcome.NO_MATCH
    assert _RuleSet([_Rule()]).match(replace(order, order_type=OrderType.MARKET)).outcome is _Outcome.REJECT_UNSUPPORTED


class _UnrelatedType(StrEnum):
    LIMIT = 'limit'


@pytest.mark.parametrize('order_type', ['LIMIT', 'Limit', 'stop', 'garbage', _UnrelatedType.LIMIT])
def test_unknown_type_cannot_prove_a_limit_rule_does_not_match(order, order_type):
    candidate = replace(order, order_type=order_type)
    decision = _RuleSet([_Rule(order_type='limit'), _Rule()]).match(candidate)
    assert decision.outcome is _Outcome.INDETERMINATE and decision.rule_index == 0
    assert _RuleSet([_Rule()]).match(candidate).outcome is _Outcome.INDETERMINATE


@pytest.mark.parametrize('order_type', ['limit', OrderType.LIMIT])
def test_declared_limit_values_match(order, order_type):
    assert _RuleSet([_Rule(order_type='limit')]).match(
        replace(order, order_type=order_type)
    ).outcome is _Outcome.MATCH


@pytest.mark.parametrize('order_type', ['market', OrderType.MARKET])
def test_declared_market_values_are_known_nonmatches_and_broad_rejects(order, order_type):
    candidate = replace(order, order_type=order_type)
    assert _RuleSet([_Rule(order_type='limit')]).match(candidate).outcome is _Outcome.NO_MATCH
    decision = _RuleSet([_Rule(order_type='limit'), _Rule()]).match(candidate)
    assert decision.outcome is _Outcome.REJECT_UNSUPPORTED and decision.rule_index == 1


@pytest.mark.parametrize('order_type', ['market', 'stop', '', True, 1])
def test_static_non_limit_type_is_configuration_error(order_type):
    with pytest.raises(ValueError, match='UNSUPPORTED_ROUTE_TYPE'):
        _Rule(order_type=order_type)


def test_unknown_earlier_possible_rule_blocks_later_match(order):
    rules = _RuleSet([_Rule(minimum_quantity=Decimal('5'), quantity_unit='contracts'), _Rule()])
    decision = rules.match(order)
    assert decision.outcome is _Outcome.INDETERMINATE and decision.rule_index == 0


def test_known_mismatch_dominates_unknown_within_same_rule(order):
    rules = _RuleSet([
        _Rule(side='sell', minimum_quantity=Decimal('5'), quantity_unit='contracts'),
        _Rule(side='buy'),
    ])
    decision = rules.match(order)
    assert decision.outcome is _Outcome.MATCH and decision.rule_index == 1
    assert _RuleSet(rules.rules[:1]).match(order).outcome is _Outcome.NO_MATCH


@pytest.mark.parametrize('field,value', [
    ('instrument', None), ('instrument', 'rb'), ('side', None), ('side', 'BUY'),
    ('offset', None), ('offset', 'unknown'), ('order_type', None), ('order_type', ''),
    ('quantity', None), ('quantity', 5), ('quantity', Decimal('NaN')),
    ('quantity', Decimal('Infinity')), ('quantity', Decimal('0')), ('quantity', Decimal('-1')),
    ('quantity_unit', None), ('quantity_unit', 'unknown'),
])
def test_broad_rule_never_matches_incomplete_parent_facts(order, field, value):
    decision = _RuleSet([_Rule()]).match(replace(order, **{field: value}))
    assert decision.outcome is _Outcome.INDETERMINATE and decision.rule_index == 0


def test_condition_unknown_never_skips_to_second_rule(order):
    rules = _RuleSet([_Rule(side='buy'), _Rule()])
    decision = rules.match(replace(order, side=None))
    assert decision.outcome is _Outcome.INDETERMINATE and decision.rule_index == 0


@pytest.mark.parametrize('kwargs', [
    {'side': 'BUY'}, {'side': True}, {'offset': 'x'}, {'instrument': 'rb'},
    {'minimum_quantity': Decimal('0'), 'quantity_unit': 'lots'},
    {'minimum_quantity': Decimal('NaN'), 'quantity_unit': 'lots'},
    {'minimum_quantity': Decimal('Infinity'), 'quantity_unit': 'lots'},
    {'minimum_quantity': 5, 'quantity_unit': 'lots'},
    {'minimum_quantity': Decimal('5')}, {'quantity_unit': 'lots'},
    {'minimum_quantity': Decimal('5'), 'quantity_unit': 'units'},
])
def test_invalid_rule_rejected(kwargs):
    with pytest.raises(ValueError, match='INVALID_ROUTE_RULE'):
        _Rule(**kwargs)


def test_no_rules_and_invalid_containers(order):
    assert _RuleSet([]).match(order).outcome is _Outcome.NO_MATCH
    for invalid in ({_Rule()}, iter([_Rule()]), [_Rule(), None], None):
        with pytest.raises(ValueError, match='INVALID_ROUTE_RULES'):
            _RuleSet(invalid)
    with pytest.raises(ValueError, match='INVALID_ORDER_FACTS'):
        _RuleSet([]).match({})
