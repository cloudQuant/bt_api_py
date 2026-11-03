"""Iteration 60.08 (G5): SDK order requests <-> typed Base commands.

Every SDK submit/cancel request field round-trips through the typed wire
command (encoded and decoded as real MessagePack) with an equal semantic
fingerprint; unrepresentable fields and provider-private bindings are refused
before sending; typed results map to the SDK result semantics.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from bt_api_base.account_protocol import AccountKey
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.wire_codec_v1 import (
    StreamPosition,
    command_fingerprint,
    decode_message,
    decode_typed_part,
    encode_message,
    encode_typed_part,
)
from bt_api_py.service_command_wire import (
    ServiceCommandWireError,
    payload_from_typed_cancel,
    payload_from_typed_submit,
    require_accepted_result,
    typed_cancel_from_payload,
    typed_result_from_cancel_record,
    typed_result_from_order_record,
    typed_submit_from_payload,
)
from bt_api_py.service_execution import (
    CancelRecordView,
    OrderRecordView,
    ServiceExecutionClient,
    ServiceExecutionError,
    ServiceExecutionUnknownOutcomeError,
)

ACCOUNT = AccountKey(
    provider="ctp",
    venue="simnow",
    environment="simnow",
    account_id="synthetic-account",
)
INSTRUMENT = MarketInstrumentKey(
    provider="ctp",
    venue="SHFE",
    environment="simnow",
    product_type="future",
    instrument_id="rb2601",
    native_symbol="rb2601",
)
SCOPE = {
    "account_key": ACCOUNT,
    "instrument": INSTRUMENT,
    "route": "route-ctp-a",
    "strategy_ref": "strategy-a",
    "run_id": "run-a",
    "request_id": "request-1",
}
STREAM = StreamPosition("commands", "epoch-1", 1)


def _submit_payload(**changes):
    values = {
        "intent_id": "intent-1",
        "signal_id": "signal-1",
        "instrument": "rb2601",
        "side": "SELL",
        "position_effect": "CLOSE_YESTERDAY",
        "order_type": "LIMIT",
        "quantity": "3.50",
        "price": "3501.0",
        "time_in_force": "GTC",
        "reduce_only": True,
        "metadata_version": "meta-9",
        "deadline_ns": 1_762_131_600_123_456_789,
        "tags": {"desk": "a", "alpha": "momentum"},
        "position_side": "LONG",
        "position_bucket": "YESTERDAY",
    }
    values.update(changes)
    return dict(ServiceExecutionClient.build_submit_payload(**values))


def _cancel_payload(**changes):
    values = {
        "cancel_id": "cancel-1",
        "target_intent_id": "intent-1",
        "provider_order_id": "provider-order-7",
        "metadata_version": "meta-9",
        "tags": {"reason": "timeout"},
    }
    values.update(changes)
    return dict(ServiceExecutionClient.build_cancel_payload(**values))


def _typed_submit(payload=None):
    payload = _submit_payload() if payload is None else payload
    return typed_submit_from_payload(
        payload,
        quantity_unit="lots",
        # A market order has no limit price, hence no price currency.
        price_currency="CNY" if payload.get("order_type") == "LIMIT" else None,
        **SCOPE,
    )


def _sdk_fingerprint(payload):
    return command_fingerprint(payload)


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"order_type": "MARKET", "price": None},
        {"position_effect": "OPEN", "reduce_only": False, "position_side": "NET",
         "position_bucket": "ALL"},
        {"position_effect": "CLOSE_TODAY", "position_bucket": "TODAY", "side": "BUY",
         "position_side": "SHORT"},
        {"deadline_ns": None, "tags": None, "time_in_force": "IOC"},
    ],
)
def test_every_sdk_submit_field_round_trips_through_the_wire(changes):
    payload = _submit_payload(**changes)
    command = _typed_submit(payload)
    decoded = decode_message(encode_message("strategy_submit", command, stream=STREAM)).payload
    assert decoded.semantic_fingerprint == command.semantic_fingerprint
    back = payload_from_typed_submit(decoded)
    assert set(back) == set(payload)
    for name, value in payload.items():
        if name in {"quantity", "price"} and value is not None:
            assert Decimal(back[name]) == Decimal(value)
        else:
            assert back[name] == value, name
    assert _sdk_fingerprint(back) == _sdk_fingerprint(payload)
    # The typed part (RPC form) yields the same command directly.
    part = encode_typed_part("strategy_submit", command)
    assert decode_typed_part(part, kind="strategy_submit") == decoded


def test_ns_deadline_is_exact_and_the_intent_is_the_idempotency_key():
    command = _typed_submit()
    assert command.valid_until_utc_ns == 1_762_131_600_123_456_789
    assert command.idempotency_key == command.client_order_id == "intent-1"
    assert command.position_bucket == "yesterday" and command.position_side == "long"


@pytest.mark.parametrize(
    "extra, code",
    [
        ({"front_id": 1}, "COMMAND_PRIVATE_BINDING_UNREPRESENTABLE"),
        ({"session_id": 2}, "COMMAND_PRIVATE_BINDING_UNREPRESENTABLE"),
        ({"order_ref": "000001"}, "COMMAND_PRIVATE_BINDING_UNREPRESENTABLE"),
        ({"ctp_exchange_binding": "x"}, "COMMAND_PRIVATE_BINDING_UNREPRESENTABLE"),
        ({"iceberg_visible": "1"}, "COMMAND_FIELD_UNREPRESENTABLE"),
        ({"stop_price": "1"}, "COMMAND_FIELD_UNREPRESENTABLE"),
    ],
)
def test_unrepresentable_request_fields_are_refused_not_dropped(extra, code):
    with pytest.raises(ServiceCommandWireError) as raised:
        _typed_submit({**_submit_payload(), **extra})
    assert raised.value.code == code


def test_private_bindings_cannot_hide_in_tags_and_bad_requests_keep_sdk_codes():
    for key in ("order_ref", "FRONT_ID", "ctp.session"):
        with pytest.raises(ServiceCommandWireError, match="COMMAND_PRIVATE_BINDING"):
            _typed_submit({**_submit_payload(), "tags": {key: "1"}})
    with pytest.raises(ServiceCommandWireError, match="INVALID_DEADLINE"):
        _typed_submit({**_submit_payload(), "deadline_ns": 1.5})
    with pytest.raises(ServiceCommandWireError, match="INVALID_QUANTITY"):
        _typed_submit({**_submit_payload(), "quantity": "NaN"})
    with pytest.raises(ServiceCommandWireError, match="COMMAND_INSTRUMENT_MISMATCH"):
        _typed_submit({**_submit_payload(), "instrument": "rb2605"})
    with pytest.raises(ServiceCommandWireError, match="COMMAND_UNREPRESENTABLE"):
        # A tag value the typed contract cannot express (non-printable).
        _typed_submit({**_submit_payload(), "tags": {"note": "a\x01b"}})


def test_typed_commands_the_sdk_request_cannot_express_are_refused():
    command = _typed_submit()
    for changes in (
        {"signal_id": None},
        {"metadata_version": None},
        {"position_effect": None, "reduce_only": False, "position_bucket": None},
        {"client_order_id": "other-client-id"},
    ):
        with pytest.raises(ServiceCommandWireError, match="COMMAND_UNREPRESENTABLE"):
            payload_from_typed_submit(replace(command, **changes))


def test_cancel_round_trip_and_refusals():
    payload = _cancel_payload()
    command = typed_cancel_from_payload(payload, **{**SCOPE})
    decoded = decode_message(encode_message("strategy_cancel", command, stream=STREAM)).payload
    assert payload_from_typed_cancel(decoded) == payload
    assert decoded.semantic_fingerprint == command.semantic_fingerprint
    with pytest.raises(ServiceCommandWireError, match="COMMAND_PRIVATE_BINDING"):
        typed_cancel_from_payload({**payload, "order_sys_id": "x"}, **SCOPE)
    with pytest.raises(ServiceCommandWireError, match="COMMAND_UNREPRESENTABLE"):
        payload_from_typed_cancel(replace(command, provider_order_ref=None))


def _order_record(state, **changes):
    values = dict(
        intent_id="intent-1",
        scope_key="scope",
        state=state,
        provider_order_id="provider-order-7",
        filled_quantity=Decimal("0"),
        average_price=None,
        cumulative_commission=None,
        permit_reference=None,
        dispatch_attempts=1,
        unknown_reason="TIMEOUT" if state == "UNKNOWN" else None,
        review_required=False,
        is_terminal=state in {"FILLED", "CANCELLED", "REJECTED", "BLOCKED"},
        created_at_ns=1,
        updated_at_ns=2,
    )
    values.update(changes)
    return OrderRecordView(**values)


@pytest.mark.parametrize(
    "state, outcome",
    [
        ("ACKED", "accepted"),
        ("PARTIALLY_FILLED", "accepted"),
        ("FILLED", "accepted"),
        ("UNKNOWN", "unknown"),
        ("DISPATCHING", "unknown"),
        ("PENDING_DISPATCH", "unknown"),
        ("REJECTED", "rejected"),
        ("BLOCKED", "rejected"),
    ],
)
def test_order_records_map_to_typed_results_and_back_to_sdk_semantics(state, outcome):
    command = _typed_submit()
    result = typed_result_from_order_record(
        _order_record(state), command, request_id="request-1",
        observed_at_utc_ns=1_762_131_600_000_000_001, source_precision_ns=1_000,
    )
    decoded = decode_message(encode_message("strategy_result", result, stream=STREAM)).payload
    assert decoded == result and decoded.outcome == outcome
    assert decoded.command_fingerprint == command.semantic_fingerprint
    assert decoded.observed_at_utc_ns == 1_762_131_600_000_000_001
    if outcome == "accepted":
        assert require_accepted_result(decoded, command) is decoded
    elif outcome == "unknown":
        with pytest.raises(ServiceExecutionUnknownOutcomeError) as raised:
            require_accepted_result(decoded, command)
        assert raised.value.identifier == "intent-1"
    else:
        with pytest.raises(ServiceExecutionError) as raised:
            require_accepted_result(decoded, command)
        assert not isinstance(raised.value, ServiceExecutionUnknownOutcomeError)
        assert raised.value.code == state


def test_results_cannot_be_attached_to_other_command_content():
    command = _typed_submit()
    result = typed_result_from_order_record(_order_record("ACKED"), command, request_id="r")
    other = _typed_submit(_submit_payload(quantity="4"))  # same intent id, other content
    with pytest.raises(ServiceCommandWireError, match="COMMAND_RESULT_BINDING_MISMATCH"):
        require_accepted_result(result, other)
    with pytest.raises(ServiceCommandWireError, match="COMMAND_RESULT_BINDING_MISMATCH"):
        typed_result_from_order_record(
            _order_record("ACKED", intent_id="intent-2"), command, request_id="r"
        )
    cancel = typed_cancel_from_payload(_cancel_payload(), **SCOPE)
    with pytest.raises(ServiceCommandWireError, match="COMMAND_RESULT_BINDING_MISMATCH"):
        require_accepted_result(result, cancel)


def test_cancel_records_map_to_typed_results():
    cancel = typed_cancel_from_payload(_cancel_payload(), **SCOPE)
    record = CancelRecordView(
        cancel_id="cancel-1",
        scope_key="scope",
        target_intent_id="intent-1",
        provider_order_id="provider-order-7",
        state="CANCELLED",
        permit_reference=None,
        dispatch_attempts=1,
        unknown_reason=None,
        review_required=False,
        is_terminal=True,
        created_at_ns=1,
        updated_at_ns=2,
    )
    result = typed_result_from_cancel_record(record, cancel, request_id="request-2")
    assert result.operation == "cancel" and result.outcome == "accepted"
    assert require_accepted_result(result, cancel) is result
