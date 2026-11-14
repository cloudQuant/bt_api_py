"""Structural observation mapping only; no source or ACK authority is implied."""

from types import SimpleNamespace

import pytest

from bt_api_base.direct_write_protocol import DirectExecutionEventKind, DirectOrderStatus
from bt_api_base.gateway_execution_stream_protocol import (
    GatewayExecutionObservation,
    GatewayExecutionStreamEvent,
    GatewayTradeAttribution,
)
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_py.gateway_account_observer_client import GatewayAccountObservedEvent
from bt_api_py.risk_service_gateway import observed_event_from_account_event
from bt_api_risk.pretrade.facts import GatewayObservationIdentity, RiskFactError


def event_item(*, sequence=1, account="account-a", route="CTP___FUTURE", principal="risk-a"):
    instrument = MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb", "rb")
    observation = GatewayExecutionObservation(
        account_scope=account, route=route, instrument=instrument, trading_day="20261108",
        kind=DirectExecutionEventKind.ORDER, status=DirectOrderStatus.ACCEPTED,
        side="buy", offset="open", order_quantity="2", filled_quantity="0",
    )
    event = GatewayExecutionStreamEvent(
        sequence=sequence, request_key="request-a", reservation_id="reservation-a",
        command_digest="a" * 64, command_principal=principal, fact_digest="b" * 64,
        observation=observation,
    )
    return GatewayAccountObservedEvent("strategy-a", event)


def map_event(item):
    return observed_event_from_account_event(
        item, epoch="epoch-a", risk_principal="risk-a", intent_payload=lambda _: None,
    )


def test_real_gateway_event_preserves_every_identity_field_and_zero_based_sequence():
    result = map_event(event_item())
    assert result.sequence == 0
    assert result.gateway_identity == GatewayObservationIdentity(
        "account-a", "CTP___FUTURE", "risk-a", "a" * 64, "b" * 64,
    )
    assert result.origin == "risk:risk-a"


def test_different_account_route_and_principal_are_preserved_not_authorized():
    result = map_event(event_item(sequence=2, account="account-b", route="other-route", principal="other"))
    assert result.sequence == 1
    assert result.gateway_identity == GatewayObservationIdentity(
        "account-b", "other-route", "other", "a" * 64, "b" * 64,
    )
    assert result.origin == "self"


@pytest.mark.parametrize("field,value", [
    ("account_scope", "account-b"), ("route", "other-route"),
    ("command_principal", "other"), ("command_digest", "c" * 64),
    ("fact_digest", "d" * 64),
])
def test_each_original_identity_field_is_preserved_independently(field, value):
    item = event_item()
    if field in ("account_scope", "route"):
        object.__setattr__(item.event.observation, field, value)
    else:
        object.__setattr__(item.event, field, value)
    assert getattr(map_event(item).gateway_identity, field) == value


@pytest.mark.parametrize("field", ["account_scope", "route", "command_principal", "command_digest", "fact_digest"])
def test_missing_identity_field_rejected_with_finite_error(field):
    item = event_item()
    target = item.event.observation if field in ("account_scope", "route") else item.event
    object.__delattr__(target, field)
    with pytest.raises(RiskFactError, match="^RISK_OBSERVATION_INVALID$"):
        map_event(item)


@pytest.mark.parametrize("field,value", [
    ("account_scope", ""), ("route", " " + "r"), ("command_principal", "\ud800"),
    ("command_digest", "a" * 63), ("command_digest", "A" * 64),
    ("fact_digest", "g" * 64), ("fact_digest", None),
])
def test_malformed_identity_field_rejected(field, value):
    item = event_item()
    target = item.event.observation if field in ("account_scope", "route") else item.event
    object.__setattr__(target, field, value)
    with pytest.raises(RiskFactError, match="^RISK_OBSERVATION_INVALID$"):
        map_event(item)


@pytest.mark.parametrize("sequence", [0, -1, True, 2**63])
def test_invalid_gateway_sequence_is_not_coerced(sequence):
    item = event_item()
    object.__setattr__(item.event, "sequence", sequence)
    with pytest.raises(RiskFactError, match="^RISK_OBSERVATION_INVALID$"):
        map_event(item)


def test_maximum_gateway_sequence_maps_without_overflow():
    assert map_event(event_item(sequence=2**63 - 1)).sequence == 2**63 - 2


def test_callback_mutation_is_rejected_before_projection():
    item = event_item()

    def changing_payload(_):
        object.__setattr__(item.event, "sequence", 2)
        object.__setattr__(item.event, "command_principal", "other")
        object.__setattr__(item.event.observation, "side", "sell")
        object.__setattr__(item.event.observation, "trading_day", "20261109")
        return None

    with pytest.raises(RiskFactError, match="^RISK_OBSERVATION_INVALID$"):
        observed_event_from_account_event(
            item, epoch="epoch-a", risk_principal="risk-a", intent_payload=changing_payload,
        )


def test_callback_side_mutation_is_not_applied_or_acknowledged():
    from bt_api_py.risk_service_gateway import ObservationPump, RiskFactsCache
    from tests.unit.test_iter64_risk_service_gateway import Engine, Observer, page

    item = event_item()

    def changing_payload(_):
        object.__setattr__(item.event.observation, "side", "sell")
        return None

    engine, observer = Engine(), Observer([page([item])], offered=1)
    value = ObservationPump(
        engine, observer, RiskFactsCache({}), risk_principal="risk-a",
        intent_payload=changing_payload, timeout_seconds=1,
    )
    assert value.poll() == 0
    assert observer.acks == [] and engine.applied == []
    assert engine.gaps[-1] == "observation_unmappable"


def test_payload_mapping_side_mutation_is_not_applied_or_acknowledged():
    from bt_api_py.risk_service_gateway import ObservationPump, RiskFactsCache
    from tests.unit.test_iter64_risk_service_gateway import Engine, Observer, page

    item = event_item()

    class MutatingPayload(dict):
        def get(self, key, default=None):
            object.__setattr__(item.event.observation, "side", "sell")
            return super().get(key, default)

    engine, observer = Engine(), Observer([page([item])], offered=1)
    value = ObservationPump(
        engine, observer, RiskFactsCache({}), risk_principal="risk-a",
        intent_payload=lambda _: MutatingPayload(), timeout_seconds=1,
    )
    assert value.poll() == 0
    assert observer.acks == [] and engine.applied == []
    assert engine.gaps[-1] == "observation_unmappable"


def attributed_trade():
    instrument = MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb", "rb")
    attribution = GatewayTradeAttribution(
        account_scope="account-a", strategy_scope="strategy-a", fact_digest="b" * 64,
        reservation_id="reservation-a", command_digest="a" * 64,
        anchor_fact_digest="c" * 64, anchor_sequence=1, venue_key_digest="d" * 64,
        original_binding_key_digests=("e" * 64,),
    )
    observation = GatewayExecutionObservation(
        account_scope="account-a", route="CTP___FUTURE", instrument=instrument,
        trading_day="20261108", kind=DirectExecutionEventKind.TRADE, side="buy",
        offset="open", trade_quantity="1", trade_price="10", trade_key_digest="f" * 64,
        native_identity_digest="a" * 64, identity_key_digests=("d" * 64,),
        venue_key_digest="d" * 64, schema_version=3, trade_attribution=attribution,
    )
    event = GatewayExecutionStreamEvent(
        sequence=2, request_key="request-a", reservation_id="reservation-a",
        command_digest="a" * 64, command_principal="risk-a", fact_digest="b" * 64,
        observation=observation,
    )
    return GatewayAccountObservedEvent("strategy-a", event)


@pytest.mark.parametrize("broken", ["duck_wrapper", "duck_event", "wrong_command", "wrong_strategy"])
def test_duck_types_and_inconsistent_attribution_are_not_applied_or_acknowledged(broken):
    from tests.unit.test_iter64_risk_service_gateway import Engine, Observer, page, pump

    item = attributed_trade()
    if broken == "duck_wrapper":
        item = SimpleNamespace(strategy_scope=item.strategy_scope, event=item.event)
    elif broken == "duck_event":
        fields = {name: getattr(item.event, name) for name in item.event.__dataclass_fields__}
        fields["command_digest"] = "f" * 64
        object.__setattr__(item, "event", SimpleNamespace(**fields))
    elif broken == "wrong_command":
        object.__setattr__(item.event, "command_digest", "f" * 64)
    else:
        object.__setattr__(item, "strategy_scope", "other-strategy")
    engine, observer = Engine(), Observer([page([item])], offered=2)
    value = pump(engine, observer)
    assert value.poll() == 0
    assert observer.acks == [] and engine.applied == []
    assert engine.gaps[-1] == "observation_unmappable"


def test_missing_identity_marks_observation_gap_without_ack():
    from tests.unit.test_iter64_risk_service_gateway import Engine, Observer, page, pump

    item = event_item()
    object.__setattr__(item.event, "fact_digest", None)
    engine = Engine()
    observer = Observer([page([item])], offered=1)
    value = pump(engine, observer)
    assert value.poll() == 0
    assert observer.acks == []
    assert engine.applied == []
    assert engine.gaps[-1] == "observation_unmappable"


@pytest.mark.parametrize("field,value", [
    ("order_quantity", "bad"), ("order_quantity", "0"), ("order_quantity", "1.0"),
    ("filled_quantity", "bad"), ("filled_quantity", "-1"),
    ("remaining_quantity", "bad"), ("limit_price", "bad"),
    ("trade_quantity", "bad"), ("trade_price", "NaN"),
])
def test_corrupted_explicit_source_numbers_never_become_missing(field, value):
    item = event_item()
    object.__setattr__(item.event.observation, field, value)
    with pytest.raises(RiskFactError, match="^RISK_OBSERVATION_INVALID$"):
        map_event(item)


@pytest.mark.parametrize("field,value", [
    ("request_key", None), ("reservation_id", None), ("request_key", ""),
    ("reservation_id", True),
])
def test_corrupted_required_command_identity_is_rejected(field, value):
    item = event_item()
    object.__setattr__(item.event, field, value)
    with pytest.raises(RiskFactError, match="^RISK_OBSERVATION_INVALID$"):
        map_event(item)


@pytest.mark.parametrize("field,value", [
    ("trading_day", None), ("trading_day", "20260230"), ("instrument", None),
    ("kind", "order"), ("status", "accepted"), ("side", ""), ("offset", "bad"),
])
def test_corrupted_required_or_explicit_fact_fields_are_rejected(field, value):
    item = event_item()
    object.__setattr__(item.event.observation, field, value)
    with pytest.raises(RiskFactError, match="^RISK_OBSERVATION_INVALID$"):
        map_event(item)


def test_absent_optional_values_remain_absent_and_do_not_fabricate_a_price():
    item = event_item()
    object.__setattr__(item.event.observation, "filled_quantity", None)
    result = map_event(item)
    assert result.filled_quantity == 0 and result.limit_price is None


def test_bad_explicit_payload_price_is_not_replaced_with_missing_price():
    with pytest.raises(RiskFactError, match="^RISK_OBSERVATION_INVALID$"):
        observed_event_from_account_event(
            event_item(), epoch="epoch-a", risk_principal="risk-a",
            intent_payload=lambda _: {"price": "bad"},
        )


def test_base_observation_invariants_are_rechecked_after_construction():
    item = event_item()
    # A venue key must also be present in the observation's identity keys.
    object.__setattr__(item.event.observation, "venue_key_digest", "c" * 64)
    called = []
    with pytest.raises(RiskFactError, match="^RISK_OBSERVATION_INVALID$"):
        observed_event_from_account_event(
            item, epoch="epoch-a", risk_principal="risk-a",
            intent_payload=lambda key: called.append(key),
        )
    assert called == []


@pytest.mark.parametrize("field,value", [("filled_quantity", "bad"), ("limit_price", "bad")])
def test_corrupt_numeric_event_is_not_applied_or_acknowledged(field, value):
    from tests.unit.test_iter64_risk_service_gateway import Engine, Observer, page, pump

    item = event_item()
    object.__setattr__(item.event.observation, field, value)
    engine, observer = Engine(), Observer([page([item])], offered=1)
    value = pump(engine, observer)
    assert value.poll() == 0
    assert observer.acks == [] and engine.applied == []
    assert engine.gaps[-1] == "observation_unmappable"


def test_missing_request_key_is_not_applied_or_acknowledged():
    from tests.unit.test_iter64_risk_service_gateway import Engine, Observer, page, pump

    item = event_item()
    object.__setattr__(item.event, "request_key", None)
    engine, observer = Engine(), Observer([page([item])], offered=1)
    value = pump(engine, observer)
    assert value.poll() == 0
    assert observer.acks == [] and engine.applied == []
    assert engine.gaps[-1] == "observation_unmappable"
