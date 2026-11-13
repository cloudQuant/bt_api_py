"""Local SDK/Base differential checks; no provider or authorization evidence."""

from dataclasses import replace
from decimal import Decimal, localcontext
from itertools import product

from bt_api_base.gateway_direct_protocol import gateway_payload_digest
from bt_api_base.gateway_risk_order_values import (
    GatewayRiskOrderValuesError,
    validate_gateway_risk_order_values,
)
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.risk_service_protocol import RiskOrderView

from bt_api_py._contracts.models import OrderRequest, OrderType, Side
from bt_api_py.gateway_order_contract import (
    GatewayDirectCompositionError,
    decode_gateway_order_request,
)

ACCOUNT = "fixture-account"
INSTRUMENT = MarketInstrumentKey("ctp", "SHFE", "fixture", "future", "rb", "rb")


def make_pair(side, order_type, offset, position_side, unit):
    request = OrderRequest(
        symbol="rb", side=side, order_type=order_type, quantity=Decimal("2.50"),
        account_id=ACCOUNT, client_order_id="client", idempotency_key="request",
        price=Decimal("3500.00") if order_type is OrderType.LIMIT else None,
        time_in_force="DAY", quantity_unit=unit, offset=offset, position_side=position_side,
        exchange_id="SHFE",
    )
    view = RiskOrderView(
        account_scope=ACCOUNT, route="fixture-route", strategy_ref="fixture-strategy",
        origin="strategy", request_key="request", instrument=INSTRUMENT,
        side=side.value.upper(), offset=None if offset is None else offset.upper(),
        position_side="NET" if position_side is None else position_side.upper(),
        quantity=Decimal("2.50"), quantity_unit=unit, order_type=order_type.value.upper(),
        limit_price=request.price, time_in_force="DAY",
    )
    return request.to_dict(), view


def accepted_by_base(payload, view):
    # Always recalculate after mutation; the digest itself provides no authority.
    digest = gateway_payload_digest(payload)
    try:
        validate_gateway_risk_order_values(payload, view, digest)
    except GatewayRiskOrderValuesError:
        return False
    return True


def accepted_by_sdk(payload):
    try:
        decode_gateway_order_request(payload, ACCOUNT)
    except GatewayDirectCompositionError:
        return False
    return True


def test_sdk_orders_and_invalid_mutations_obey_one_way_implication():
    combinations = product(
        (Side.BUY, Side.SELL), (OrderType.LIMIT, OrderType.MARKET),
        (None, "open", "close", "close_today", "close_yesterday"),
        (None, "long", "short"), ("base", "contracts", "lots", "native"),
    )
    # Every value is JSON-encodable so that mutation produces a fresh digest.
    mutations = (
        ("symbol", ""), ("account_id", "different"), ("client_order_id", ""),
        ("side", "BUY"), ("side", None), ("order_type", "stop"),
        ("quantity", "2e0"), ("quantity", "02.50"), ("quantity", "0"),
        ("quantity", 2), ("price", "NaN"), ("quantity_unit", "shares"),
        ("offset", "OPEN"), ("position_mode", "hedged"), ("hedge_flag", "4"),
        ("hedge_class", "unknown"), ("reduce_only", 0), ("instrument", {}),
        ("ctp_order_identity", {}), ("managed_intent_id", "id"),
        ("execution_cycle_id", "cycle"), ("extra_field", "x"),
    )
    valid_count = invalid_count = 0
    for args in combinations:
        payload, view = make_pair(*args)
        assert len(payload) == 25
        assert accepted_by_sdk(payload)
        assert accepted_by_base(payload, view)
        valid_count += 1
        for field, value in mutations:
            changed = dict(payload, **{field: value})
            sdk_ok = accepted_by_sdk(changed)
            base_ok = accepted_by_base(changed, view)
            assert not sdk_ok, (args, field, value)
            assert not base_ok or sdk_ok, (args, field, value)
            invalid_count += 1
        missing = dict(payload)
        del missing["hedge_class"]
        assert not accepted_by_sdk(missing)
        assert not accepted_by_base(missing, view)
        invalid_count += 1
    assert valid_count == 240
    assert invalid_count == 5520


def test_sdk_valid_payload_may_fail_matching_view_or_venue():
    payload, view = make_pair(Side.BUY, OrderType.LIMIT, "open", None, "lots")
    no_venue = dict(payload, exchange_id=None)
    assert accepted_by_sdk(no_venue)
    assert not accepted_by_base(no_venue, view)
    different_qty = dict(payload, quantity="3")
    assert accepted_by_sdk(different_qty)
    assert not accepted_by_base(different_qty, view)


def test_sdk_valid_decimal_may_fail_risk_encoding_guard():
    payload, view = make_pair(Side.BUY, OrderType.LIMIT, "open", None, "lots")
    value = Decimal("12345678901234567890123456789")
    changed = dict(payload, quantity=str(value))
    view = replace(view, quantity=value)
    assert accepted_by_sdk(changed)
    with localcontext() as ctx:
        ctx.prec = 28
        assert not accepted_by_base(changed, view)
