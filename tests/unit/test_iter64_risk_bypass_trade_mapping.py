"""A fill of an order Risk did not place must not stop Risk's observation stream (64.09).

Found by the INTEGRATION_SIM scenario INV64-10: a TRADE return carries no order
quantity, and for a bypass or external order Risk holds no intent. The mapping
refused the event, the pump marked ``observation_unmappable`` and replayed from 0,
met the same event again, and Risk stayed UNVERIFIED for good: every later order
through Risk was refused ``risk_self_trade_unverifiable``. The trade quantity is a
lower bound of the order quantity; the engine keeps the largest one any event of
the order reports. UNIT/FAKE evidence (fakes, no process, no Gateway).
"""

from __future__ import annotations

from decimal import Decimal

from bt_api_base.direct_write_protocol import DirectExecutionEventKind

from bt_api_py.risk_service_gateway import observed_event_from_account_event
from tests.unit.test_iter64_risk_service_gateway import (
    EPOCH,
    RISK,
    Engine,
    Observer,
    account_event,
    page,
    pump,
)


def trade_event(sequence, *, principal="other", key="k", quantity="2", price="10"):
    item = account_event(sequence, principal=principal, key=key)
    observation = item.event.observation
    observation.kind = DirectExecutionEventKind.TRADE
    observation.status = None
    observation.order_quantity = None
    observation.filled_quantity = None
    observation.limit_price = None
    observation.trade_quantity = quantity
    observation.trade_price = price
    observation.trade_key_digest = "t" * 64
    observation.venue_key_digest = "v" * 64
    return item


def test_bypass_trade_maps_with_the_trade_quantity_as_the_order_quantity_bound():
    event = observed_event_from_account_event(
        trade_event(5), epoch=EPOCH, risk_principal=RISK.principal_ref,
        intent_payload=lambda key: None)
    assert event.kind == "trade" and event.origin == "self"
    assert event.quantity == Decimal(2) and event.filled_quantity == Decimal(2)
    assert event.trade_quantity == Decimal(2) and event.trade_id == "t" * 64


def test_risk_own_trade_still_takes_the_order_quantity_from_its_intent():
    payload = {"side": "buy", "offset": "open", "quantity": "3", "price": "10"}
    event = observed_event_from_account_event(
        trade_event(5, principal=RISK.principal_ref, quantity="1"), epoch=EPOCH,
        risk_principal=RISK.principal_ref, intent_payload=lambda key: payload)
    assert event.quantity == Decimal(3) and event.filled_quantity == Decimal(1)


def test_pump_applies_a_bypass_fill_without_a_gap():
    engine = Engine()
    observer = Observer([page([account_event(1), trade_event(2), account_event(3)])])
    value = pump(engine, observer)
    assert value.poll() == 3
    assert [event.sequence for event in engine.applied] == [0, 1, 2]
    assert engine.gaps == ["observation_restart"]  # the restart mark only, no unmappable gap
    assert observer.acks == [3] and value.verified_epoch == EPOCH


def test_order_side_still_required_for_an_order_risk_did_not_place():
    item = trade_event(5)
    item.event.observation.side = None
    try:
        observed_event_from_account_event(item, epoch=EPOCH, risk_principal=RISK.principal_ref,
                                          intent_payload=lambda key: None)
    except Exception as error:  # still refused: no invented side
        assert type(error).__name__ == "RiskFactError"
    else:
        raise AssertionError("an event without a side was mapped")
