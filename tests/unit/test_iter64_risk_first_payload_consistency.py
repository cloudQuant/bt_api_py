"""Synthetic value checks only; these do not exercise a Gateway admission path."""

import ast
import copy
import socket
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from threading import Event, Thread

import pytest
from bt_api_base.gateway_delegation_protocol import GatewayDelegatedCommand
from bt_api_base.gateway_direct_protocol import gateway_payload_digest
from bt_api_base.gateway_risk_first_reserve_v2 import GatewayRiskFirstReserveRequestV2
from bt_api_base.market_protocol import MarketInstrumentKey

import bt_api_py.risk_first_payload_consistency as consistency
from bt_api_py._contracts.models import OrderRequest, OrderType, Side
from bt_api_py.risk_first_payload_consistency import (
    RiskFirstPayloadConsistencyError,
    validate_risk_first_payload_consistency,
)


def _payload():
    return OrderRequest(
        symbol="rb2701", side=Side.BUY, order_type=OrderType.LIMIT,
        quantity=Decimal("1"), price=Decimal("10"), account_id="acct",
        client_order_id="client", idempotency_key="key", quantity_unit="lots",
        exchange_id="SHFE", offset="open",
    ).to_dict()


def _request(payload):
    command = GatewayDelegatedCommand(
        account_scope="acct", strategy_scope="strategy", execution_domain_ref="domain",
        actor_principal="risk", actor_ref="actor", actor_kind="risk", run_id="run",
        request_key="key", payload_digest=gateway_payload_digest(payload),
        instrument=MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb2701", "rb2701"),
        grant_revision="grant",
    )
    return GatewayRiskFirstReserveRequestV2(
        command=command, risk_service_id="service", risk_instance_id="instance",
        strategy_principal="principal", risk_registration_id="registration",
        risk_service_epoch="epoch", risk_permit_id="permit", risk_view_digest="a" * 64,
        attempt_nonce_digest="b" * 64, requested_ttl_ms=1000,
    )


def _reject(request, payload):
    with pytest.raises(RiskFirstPayloadConsistencyError) as exc:
        validate_risk_first_payload_consistency(request, payload)
    assert str(exc.value) == "RISK_FIRST_PAYLOAD_INCONSISTENT"
    assert exc.value.__context__ is None and exc.value.__cause__ is None


def test_real_dto_and_order_payload_match_without_mutation_or_network(monkeypatch):
    payload = _payload()
    request = _request(payload)
    before_payload, before_request = copy.deepcopy(payload), request.to_mapping()
    monkeypatch.setattr(socket, "socket", lambda *args, **kwargs: pytest.fail("network access"))
    checked = validate_risk_first_payload_consistency(request, payload)
    assert dict(checked) == before_payload
    assert payload == before_payload and request.to_mapping() == before_request
    with pytest.raises(TypeError):
        checked["quantity"] = "2"
    payload["quantity"] = "2"
    assert checked["quantity"] == "1"


def test_interleaved_mutation_is_rejected_and_decoder_sees_checked_snapshot(monkeypatch):
    payload = _payload()
    request = _request(payload)
    started, changed = Event(), Event()
    original_decode = consistency.decode_gateway_order_request

    def interleaved_decode(snapshot, account):
        started.set()
        assert changed.wait(5)
        assert snapshot["quantity"] == "1"
        return original_decode(snapshot, account)

    def mutate_original():
        if started.wait(5):
            payload["quantity"] = "2"
            changed.set()

    monkeypatch.setattr(consistency, "decode_gateway_order_request", interleaved_decode)
    worker = Thread(target=mutate_original)
    worker.start()
    try:
        _reject(request, payload)
    finally:
        worker.join(timeout=6)
    assert not worker.is_alive() and changed.is_set()


def test_decoder_or_request_mutation_during_validation_is_rejected(monkeypatch):
    payload = _payload()
    request = _request(payload)
    original_decode = consistency.decode_gateway_order_request

    def mutate_decoded_input(value, account):
        result = original_decode(value, account)
        value["quantity"] = "2"
        return result

    monkeypatch.setattr(consistency, "decode_gateway_order_request", mutate_decoded_input)
    _reject(request, payload)

    def mutate_request(value, account):
        result = original_decode(value, account)
        object.__setattr__(request.command, "request_key", "other")
        return result

    monkeypatch.setattr(consistency, "decode_gateway_order_request", mutate_request)
    _reject(request, payload)


@pytest.mark.parametrize("field,value", [
    ("idempotency_key", "other"), ("symbol", "other"), ("exchange_id", "DCE"),
    ("exchange_id", None), ("account_id", "other"),
])
def test_rejects_mismatch_even_if_digest_was_recomputed(field, value):
    payload = _payload()
    payload[field] = value
    _reject(_request(payload), payload)


def test_rejects_payload_digest_mismatch_and_does_not_echo_values():
    payload = _payload()
    request = _request(payload)
    payload["client_order_id"] = "private-changed-value"
    _reject(request, payload)


@pytest.mark.parametrize("alias,value", [
    ("request_key", "other"), ("request_key", "key"), ("instrument", "rb2701"),
    ("venue", "DCE"), ("exchange", "SHFE"), ("account", "other"),
    ("account_scope", "acct"), ("symbol_id", "rb2701"),
])
def test_rejects_aliases_even_if_digest_includes_them(alias, value):
    payload = _payload()
    payload[alias] = value
    _reject(_request(payload), payload)


@pytest.mark.parametrize("field,value", [
    ("client_order_id", ""), ("client_order_id", "   "), ("time_in_force", ""),
    ("position_id", "\n"), ("symbol", " rb2701"), ("quantity", "0"),
    ("quantity", "NaN"), ("price", "0"), ("side", "BUY"),
    ("quantity", "1e9999999999"), ("price", "1e9999999999"),
    ("reduce_only", 1), ("exchange_id", ""),
])
def test_rejects_invalid_order_values_even_with_matching_digest(field, value):
    payload = _payload()
    payload[field] = value
    _reject(_request(payload), payload)


def test_rejects_missing_extra_noncanonical_and_wrong_inputs():
    payload = _payload()
    request = _request(payload)
    missing = dict(payload)
    del missing["time_in_force"]
    for bad in (missing, {**payload, "extra": "x"}, {**payload, "quantity": "1e0"}, {}, [], None):
        _reject(request, bad)
    for bad in (None, {}, request.to_mapping()):
        _reject(bad, payload)


def test_rejects_missing_and_extra_fields_even_with_matching_digest():
    missing = _payload()
    del missing["time_in_force"]
    extra = {**_payload(), "unexpected": "private-value"}
    _reject(_request(missing), missing)
    _reject(_request(extra), extra)


def test_revalidates_dto_if_immutable_object_was_tampered_with():
    payload = _payload()
    request = _request(payload)
    object.__setattr__(request.command, "actor_kind", "execution")
    _reject(request, payload)


def test_unmapped_instrument_claims_are_not_inferred_from_the_payload():
    payload = _payload()
    request = _request(payload)
    instrument = replace(request.command.instrument, provider="other", environment="other",
                         product_type="other", instrument_id="other")
    altered = replace(request, command=replace(request.command, instrument=instrument))
    # Success cannot establish any of those source or identity claims.
    assert dict(validate_risk_first_payload_consistency(altered, payload)) == payload


def test_validator_is_not_wired_into_existing_gateway_or_risk_entrypoints():
    root = Path(__file__).resolve().parents[2]
    entrypoints = [root / "bt_api_py" / name for name in (
        "gateway_api.py", "gateway_delegate_risk_host.py", "risk_service_host.py",
        "risk_service_component.py", "gateway_risk_v2_direct_origin.py",
    )]
    entrypoints.append(root / "bt_api/bt_api_gateway/src/bt_api_gateway/direct_delegation.py")
    for path in entrypoints:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        assert not any(isinstance(node, (ast.Import, ast.ImportFrom)) and
                       "risk_first_payload_consistency" in ast.unparse(node) for node in ast.walk(tree))
